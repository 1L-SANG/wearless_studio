import test from 'node:test';
import assert from 'node:assert/strict';

import {
  ID_DOCUMENT_TYPES,
  buildMaskedBlob,
  burnGuideMask,
  clampMaskRatio,
  containContentRect,
  defaultMaskRatio,
  elementRatioToImagePixels,
  maskRatioToPixels,
} from '../../src/features/model/idDocumentMasking.js';
import { rrnRectInFrame } from '../../src/features/model/idCardGeometry.js';

// 이 파일이 지키는 것: 신분증 사진의 주민등록번호는 서버가 마스킹 여부를 검증할 수
// 없으므로 클라이언트가 전송 전에 실제로 픽셀을 덮어써야 한다(buildMaskedBlob). 원본
// File/Image 는 이 함수의 인자로만 쓰이고 반환값(blob)에는 등장하지 않는다.

// 최종리뷰 I12: v1 은 주민등록증만 받는다. 마스크가 사각형 하나뿐이라 면허번호·여권번호·
// 외국인등록번호는 가려지지 않은 채 최대 7일 저장되는데, 셋 다 고유식별정보(개인정보보호법
// §24)이고 처리방침 §5 는 "수집하지 않는다"고 적혀 있다. 넓히려면 다중 마스크 영역이 먼저다.
test('v1 은 신분증 종류가 주민등록증 하나뿐이다(서버 ID_DOCUMENT_TYPES 와 같은 집합)', () => {
  assert.deepEqual(ID_DOCUMENT_TYPES.map((d) => d.value), ['rrc']);
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
// sequence 는 drawImage/fillRect 호출을 "한 줄"에 순서대로 적재한다(따로 세는 배열 두 개가
// 아니다). 리뷰에서 지적된 구멍: drawImage.length===1 && fillRect.length===1 는 두 호출이
// 있었다는 것만 보장하고 **순서**는 보장하지 못한다 — fillRect 를 먼저 부르고 drawImage 로
// 원본을 그 위에 덧그리면(마스킹이 원본에 덮여 사라짐, 주민등록번호가 그대로 살아남음)
// 두 카운트 다 여전히 1 로 통과해 버린다. 아래 fillStyle 도 같은 sequence 에 적재해
// "#111 로 칠하기 전에 값이 바뀌지 않았는가"까지 순서로 확인한다.
function fakeCanvas() {
  const sequence = [];
  let fillStyle = null;
  return {
    width: 0,
    height: 0,
    sequence,
    getContext: () => ({
      drawImage: (...args) => sequence.push({ op: 'drawImage', args }),
      set fillStyle(value) { fillStyle = value; sequence.push({ op: 'fillStyle', value }); },
      get fillStyle() { return fillStyle; },
      fillRect: (...args) => sequence.push({ op: 'fillRect', args }),
    }),
    toBlob(resolve, type, quality) {
      // 실제 브라우저 캔버스라면 이 시점의 픽셀(=마스킹 이후)만 인코딩한다. 스텁은 그
      // 시점까지의 전체 순서를 blob 안에 실어 테스트가 "무엇을 몇 번째로 했는지" 확인하게 한다.
      resolve({ __fakeBlob: true, type, quality, sequence: [...sequence] });
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

  const drawImageCalls = canvas.sequence.filter((c) => c.op === 'drawImage');
  const fillRectCalls = canvas.sequence.filter((c) => c.op === 'fillRect');
  assert.equal(drawImageCalls.length, 1, 'drawImage 는 정확히 한 번만 호출돼야 한다');
  assert.deepEqual(drawImageCalls[0].args, [image, 0, 0, 400, 300]);
  assert.equal(fillRectCalls.length, 1, 'fillRect(마스킹) 가 한 번은 반드시 일어나야 한다');
  assert.deepEqual(fillRectCalls[0].args, [10, 20, 100, 40]);

  // 핵심: drawImage 가 fillRect 보다 먼저 일어나야 한다. 순서가 뒤집히면(fillRect 먼저 →
  // drawImage 가 마스킹을 덮어 원본을 되살림) 위 length/args 단언은 전부 그대로 통과하므로
  // 인덱스 비교로만 잡을 수 있다.
  const drawImageIndex = canvas.sequence.findIndex((c) => c.op === 'drawImage');
  const fillRectIndex = canvas.sequence.findIndex((c) => c.op === 'fillRect');
  assert.ok(
    drawImageIndex < fillRectIndex,
    `drawImage(${drawImageIndex}) 가 fillRect(${fillRectIndex}) 보다 먼저 일어나야 한다 — `
    + '순서가 바뀌면 마스킹이 원본에 덮여 사라진다(주민등록번호가 그대로 남는다)',
  );

  const fillStyleCalls = canvas.sequence.filter((c) => c.op === 'fillStyle');
  assert.ok(fillStyleCalls.some((c) => c.value === '#111'), 'fillStyle 이 #111 로 설정돼야 한다');
  // blob 자체는 원본 image 객체가 전혀 아니다 — 캔버스에서 새로 뽑아낸 결과물이다.
  assert.notEqual(blob, image);
  assert.equal(blob.__fakeBlob, true);
  assert.equal(blob.type, 'image/jpeg');
  // blob 에 실린 순서 기록도 같은 결론(그린 뒤 채웠다)을 담고 있어야 한다.
  const blobDrawIndex = blob.sequence.findIndex((c) => c.op === 'drawImage');
  const blobFillIndex = blob.sequence.findIndex((c) => c.op === 'fillRect');
  assert.ok(blobDrawIndex < blobFillIndex, 'blob 에 실린 기록도 그린 뒤 채운 순서여야 한다');
});

// ── 좌표계: 화면 엘리먼트 박스 ↔ 원본 자연 픽셀 ────────────────────────────
// 최종리뷰 C3. 드래그·오버레이는 <img> **엘리먼트 박스** 기준 비율인데 burn 은
// naturalWidth/Height 격자에 그린다. CSS 가 `object-fit: contain`(+ max-height: 60vh)라
// 가로세로비가 다르면 이미지가 레터박스되고, 그만큼 두 좌표계가 어긋난다 — 사용자가
// 화면에서 주민등록번호를 덮었는데 실제로는 엉뚱한 곳이 칠해져 번호가 그대로 올라간다.
// 서버는 픽셀을 못 본다. 이 변환이 유일한 방어선이라 아래 값들을 직접 못박는다.

test('contain 내용 영역: 3:2 이미지를 1:1 박스에 넣으면 위아래로 레터박스된다', () => {
  // 900x600(3:2)을 600x600 박스에: 축소율 2/3 → 600x400 이 세로 가운데(위 여백 100).
  assert.deepEqual(
    containContentRect({ width: 600, height: 600 }, { width: 900, height: 600 }),
    { x: 0, y: 100, w: 600, h: 400 },
  );
  // 600x900(2:3)을 600x600 박스에: 좌우로 레터박스(왼 여백 100).
  assert.deepEqual(
    containContentRect({ width: 600, height: 600 }, { width: 600, height: 900 }),
    { x: 100, y: 0, w: 400, h: 600 },
  );
  // 비율이 같으면 박스 전체가 곧 내용 영역이다(오늘 대부분의 경우).
  assert.deepEqual(
    containContentRect({ width: 450, height: 300 }, { width: 900, height: 600 }),
    { x: 0, y: 0, w: 450, h: 300 },
  );
});

test('레터박스된 사진에서도 마스킹 박스가 원본의 의도한 영역을 덮는다(3:2 이미지 · 1:1 박스)', () => {
  // 원본 900x600 에서 가리려는 자리(주민등록번호 뒷자리) = { x:90, y:390, w:450, h:90 }.
  // 화면(600x600 박스)에서 그 자리는 축소율 2/3 + 위 여백 100 만큼 옮겨 보이므로,
  // 사용자가 그 위에 박스를 맞추면 엘리먼트 박스 기준 비율은 아래 값이 된다.
  const ratio = { xr: 0.1, yr: 0.6, wr: 0.5, hr: 0.1 };
  const burned = elementRatioToImagePixels(
    ratio, { width: 600, height: 600 }, { width: 900, height: 600 },
  );
  assert.deepEqual(burned, { x: 90, y: 390, w: 450, h: 90 });

  // 고장난 변환(비율을 자연 픽셀에 그대로 곱하던 옛 코드)은 같은 입력에서 다른 사각형을
  // 낸다 — 세로로 30px 밀리고 30px 짧아 번호 아랫부분이 그대로 남는다.
  const naive = maskRatioToPixels(ratio, 900, 600);
  assert.notDeepEqual(naive, burned);
  assert.equal(naive.y, 360);
  assert.equal(naive.h, 60);
});

test('세로로 긴 사진이 좌우 레터박스돼도 같은 규칙으로 맞는다(2:3 이미지 · 1:1 박스)', () => {
  // 원본 600x900 에서 가리려는 자리 = { x:150, y:180, w:300, h:90 }.
  // 화면에서는 축소율 2/3 + 왼쪽 여백 100 → 엘리먼트 박스 비율 { 1/3, 0.2, 1/3, 0.1 }.
  const burned = elementRatioToImagePixels(
    { xr: 1 / 3, yr: 0.2, wr: 1 / 3, hr: 0.1 },
    { width: 600, height: 600 },
    { width: 600, height: 900 },
  );
  assert.deepEqual(burned, { x: 150, y: 180, w: 300, h: 90 });
});

test('레터박스 여백 위로 끌린 부분은 잘라 내고, 통째로 여백이면 null 로 알린다', () => {
  // 위쪽 여백(0~100)에 걸친 박스 — 이미지 안쪽(y>=100)만 남는다.
  const clipped = elementRatioToImagePixels(
    { xr: 0, yr: 0, wr: 0.5, hr: 0.5 },
    { width: 600, height: 600 },
    { width: 900, height: 600 },
  );
  assert.deepEqual(clipped, { x: 0, y: 0, w: 450, h: 300 });
  // 완전히 여백 안(0~100)인 박스는 가린 게 없다 — 조용히 0 크기를 주면 호출부가
  // "칠했다"고 믿어 버린다.
  assert.equal(
    elementRatioToImagePixels(
      { xr: 0, yr: 0, wr: 0.5, hr: 0.1 },
      { width: 600, height: 600 },
      { width: 900, height: 600 },
    ),
    null,
  );
});

test('엘리먼트 박스를 잴 수 없으면 자연 격자 기준으로 되돌린다', () => {
  const ratio = { xr: 0.1, yr: 0.6, wr: 0.5, hr: 0.1 };
  assert.deepEqual(
    elementRatioToImagePixels(ratio, null, { width: 900, height: 600 }),
    maskRatioToPixels(ratio, 900, 600),
  );
  assert.deepEqual(
    elementRatioToImagePixels(ratio, { width: 0, height: 0 }, { width: 900, height: 600 }),
    maskRatioToPixels(ratio, 900, 600),
  );
});

test('burnGuideMask: 규격 좌표 자리에 덮고, 그린 뒤에 덮는다', async () => {
  const calls = [];
  let fillStyle = null;
  const ctx = {
    drawImage: (...args) => calls.push({ op: 'drawImage', args }),
    fillRect: (...args) => calls.push({ op: 'fillRect', args }),
    set fillStyle(v) { fillStyle = v; calls.push({ op: 'fillStyle', value: v }); },
    get fillStyle() { return fillStyle; },
  };
  const canvas = { width: 0, height: 0, getContext: () => ctx, toBlob: (cb) => cb({ type: 'image/jpeg' }) };

  const blob = await burnGuideMask(canvas, { nodeName: 'VIDEO' }, 1920, 1080);
  assert.equal(blob.type, 'image/jpeg');

  const ops = calls.filter((c) => c.op === 'drawImage' || c.op === 'fillRect').map((c) => c.op);
  assert.deepEqual(ops, ['drawImage', 'fillRect'],
    'fillRect 가 drawImage 보다 먼저면 원본이 마스크 위에 다시 그려져 주민번호가 살아난다');

  // 좌표가 맞아도 칠하는 색이 불투명이 아니면(반투명·투명) 번호가 비친다 — buildMaskedBlob
  // 테스트가 이미 #111 을 확인하는데, 이 함수는 그보다 더 안전 필수적이므로 같은 수준으로 잡는다.
  const fillStyleCalls = calls.filter((c) => c.op === 'fillStyle');
  assert.ok(fillStyleCalls.some((c) => c.value === '#111'), 'fillStyle 이 #111 로 설정돼야 한다');

  const expected = rrnRectInFrame(1920, 1080);
  const [x, y, w, h] = calls.find((c) => c.op === 'fillRect').args;
  assert.deepEqual({ x, y, w, h }, expected, '규격 좌표와 어긋나면 번호가 안 가려진다');
});

test('burnGuideMask: 마스크 좌표 골든값 — 규격에서 직접 계산한 절대 픽셀', async () => {
  // 다른 테스트들은 전부 rrnRectInFrame 을 양변에 써서 순환이다 — 상수가 틀리거나
  // 축이 뒤바뀌어도 통과한다. 이 단언만은 규격(85.6 x 53.98mm, fill 0.86)에서
  // 손으로 계산한 절대 좌표를 박아, 좌표가 조용히 어긋나면 여기서 깨지게 한다.
  // 1920x1080 에서 가이드 = {x:224, y:76, w:1473, h:929}.
  const calls = [];
  let fillStyle = null;
  const ctx = {
    drawImage: () => calls.push({ op: 'drawImage' }),
    fillRect: (...args) => calls.push({ op: 'fillRect', args }),
    set fillStyle(v) { fillStyle = v; },
    get fillStyle() { return fillStyle; },
  };
  const canvas = { width: 0, height: 0, getContext: () => ctx, toBlob: (cb) => cb({ type: 'image/jpeg' }) };

  await burnGuideMask(canvas, { nodeName: 'VIDEO' }, 1920, 1080);

  const [x, y, w, h] = calls.find((c) => c.op === 'fillRect').args;
  assert.deepEqual({ x, y, w, h }, { x: 312, y: 615, w: 913, h: 130 });
  // 골든값은 좌표뿐 아니라 칠하는 색도 고정한다 — 좌표가 맞아도 반투명·투명이면 번호가 비친다.
  assert.equal(fillStyle, '#111');
});
