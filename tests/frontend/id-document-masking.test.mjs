import test from 'node:test';
import assert from 'node:assert/strict';

import {
  ID_DOCUMENT_TYPES,
  buildMaskedBlob,
  clampMaskRatio,
  defaultMaskRatio,
  maskRatioToPixels,
} from '../../src/features/model/idDocumentMasking.js';

// 이 파일이 지키는 것: 신분증 사진의 주민등록번호는 서버가 마스킹 여부를 검증할 수
// 없으므로 클라이언트가 전송 전에 실제로 픽셀을 덮어써야 한다(buildMaskedBlob). 원본
// File/Image 는 이 함수의 인자로만 쓰이고 반환값(blob)에는 등장하지 않는다.

test('신분증 네 종류가 서버 계약(rrc|dl|passport|arc)과 정확히 같다', () => {
  assert.deepEqual(ID_DOCUMENT_TYPES.map((d) => d.value), ['rrc', 'dl', 'passport', 'arc']);
  for (const doc of ID_DOCUMENT_TYPES) {
    assert.equal(typeof doc.label, 'string');
    assert.ok(doc.label.length > 0, `${doc.value} 라벨이 비어 있다`);
  }
});

test('종류마다 기본 마스킹 비율이 있고 0~1 범위를 벗어나지 않는다', () => {
  for (const doc of ID_DOCUMENT_TYPES) {
    const ratio = defaultMaskRatio(doc.value);
    for (const key of ['xr', 'yr', 'wr', 'hr']) {
      assert.ok(ratio[key] >= 0 && ratio[key] <= 1, `${doc.value}.${key} 가 비율(0~1)을 벗어났다`);
    }
    assert.ok(ratio.xr + ratio.wr <= 1.0001, `${doc.value} 마스크가 가로 폭을 넘어간다`);
    assert.ok(ratio.yr + ratio.hr <= 1.0001, `${doc.value} 마스크가 세로 높이를 넘어간다`);
  }
});

test('모르는 종류는 주민등록증 기본값으로 안전하게 떨어진다', () => {
  assert.deepEqual(defaultMaskRatio('unknown-type'), defaultMaskRatio('rrc'));
  assert.deepEqual(defaultMaskRatio(null), defaultMaskRatio('rrc'));
});

test('비율 박스를 실제 픽셀 좌표로 반올림해 바꾼다', () => {
  assert.deepEqual(
    maskRatioToPixels({ xr: 0.1, yr: 0.2, wr: 0.5, hr: 0.25 }, 1000, 800),
    { x: 100, y: 160, w: 500, h: 200 },
  );
});

test('드래그·리사이즈 결과가 이미지 밖으로 나가거나 뒤집히지 않게 자른다', () => {
  // 왼쪽 위로 너무 끌면 0에서 멈춘다.
  assert.deepEqual(
    clampMaskRatio({ xr: -0.5, yr: -0.5, wr: 0.3, hr: 0.2 }),
    { xr: 0, yr: 0, wr: 0.3, hr: 0.2 },
  );
  // 오른쪽 아래로 너무 끌면 (1 - 너비/높이)에서 멈춘다.
  assert.deepEqual(
    clampMaskRatio({ xr: 0.9, yr: 0.95, wr: 0.3, hr: 0.2 }),
    { xr: 0.7, yr: 0.8, wr: 0.3, hr: 0.2 },
  );
  // 리사이즈로 음수/0 이 되면 최소 크기(0.02)로 유지한다(사각형이 사라지지 않게).
  const shrunk = clampMaskRatio({ xr: 0.1, yr: 0.1, wr: -0.5, hr: 0 });
  assert.equal(shrunk.wr, 0.02);
  assert.equal(shrunk.hr, 0.02);
});

// ── buildMaskedBlob: 마스킹의 핵심 계약 ─────────────────────────────
// 캔버스 스텁은 tests/frontend/image-transcode.test.mjs 와 같은 패턴(진짜 DOM 없이
// getContext/drawImage/fillRect/toBlob 을 흉내만 낸다).
function fakeCanvas() {
  const calls = { drawImage: [], fillRect: [], fillStyle: null };
  return {
    width: 0,
    height: 0,
    calls,
    getContext: () => ({
      drawImage: (...args) => calls.drawImage.push(args),
      set fillStyle(value) { calls.fillStyle = value; },
      get fillStyle() { return calls.fillStyle; },
      fillRect: (...args) => calls.fillRect.push(args),
    }),
    toBlob(resolve, type, quality) {
      // 실제 브라우저 캔버스라면 이 시점의 픽셀(=마스킹 이후)만 인코딩한다. 스텁은 그
      // 시점의 호출 기록을 blob 안에 실어 테스트가 "무엇을 그린 뒤 뽑았는지" 확인하게 한다.
      resolve({
        __fakeBlob: true,
        type,
        quality,
        drawImageCalls: calls.drawImage.length,
        fillRectCalls: calls.fillRect.length,
        fillStyle: calls.fillStyle,
      });
    },
  };
}

test('원본을 캔버스에 그린 뒤 마스킹 사각형으로 덮어쓰고, 그 결과만 blob 으로 뽑는다', async () => {
  const canvas = fakeCanvas();
  canvas.width = 400;
  canvas.height = 300;
  const image = { __originalImage: true };
  const mask = { x: 10, y: 20, w: 100, h: 40 };

  const blob = await buildMaskedBlob({ canvas, image, mask });

  assert.equal(canvas.calls.drawImage.length, 1, 'drawImage 는 정확히 한 번만 호출돼야 한다');
  assert.deepEqual(canvas.calls.drawImage[0], [image, 0, 0, 400, 300]);
  assert.equal(canvas.calls.fillRect.length, 1, 'fillRect(마스킹) 가 한 번은 반드시 일어나야 한다');
  assert.deepEqual(canvas.calls.fillRect[0], [10, 20, 100, 40]);
  assert.equal(canvas.calls.fillStyle, '#111');
  // blob 자체는 원본 image 객체가 전혀 아니다 — 캔버스에서 새로 뽑아낸 결과물이다.
  assert.notEqual(blob, image);
  assert.equal(blob.__fakeBlob, true);
  assert.equal(blob.type, 'image/jpeg');
  // fillRect(마스킹)가 blob 을 뽑기 전에 이미 반영돼 있었다는 증거.
  assert.equal(blob.fillRectCalls, 1);
});
