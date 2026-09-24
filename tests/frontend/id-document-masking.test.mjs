import test from 'node:test';
import assert from 'node:assert/strict';
import { ID_DOCUMENT_TYPES, burnGuideMask } from '../../src/features/model/idDocumentMasking.js';
import { rrnRectInFrame, guideRectPercent } from '../../src/features/model/idCardGeometry.js';
import { frameGalleryPhoto, galleryImageRect, setCaptureFrameStyle } from '../../src/features/model/idGalleryFraming.js';

test('v1 은 신분증 종류가 주민등록증 하나뿐이다(서버 ID_DOCUMENT_TYPES 와 같은 집합)', () => {
  assert.deepEqual(ID_DOCUMENT_TYPES.map((d) => d.value), ['rrc']);
  for (const doc of ID_DOCUMENT_TYPES) {
    assert.equal(typeof doc.label, 'string');
    assert.ok(doc.label.length > 0, `${doc.value} 라벨이 비어 있다`);
  }
});

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

test('앨범 사진은 표시와 같은 이동 및 확대 좌표로 그린 뒤 별도 캔버스에서 가려요', async () => {
  const source = fakeCanvas(), output = fakeCanvas();
  const image = { naturalWidth: 900, naturalHeight: 600 };
  assert.deepEqual(galleryImageRect(900, 600), { x: 0, y: 400, w: 1200, h: 800 });
  const blob = await frameGalleryPhoto(source, output, image, 1.5, { x: 40, y: -60 });
  assert.deepEqual(source.sequence.find(c => c.op === 'drawImage').args, [image, -260, 140, 1800, 1200]);
  assert.deepEqual(output.sequence.filter(c => ['drawImage', 'fillRect'].includes(c.op)).map(c => c.op), ['drawImage', 'fillRect']);
  assert.equal(output.sequence.find(c => c.op === 'drawImage').args[0], source);
  assert.equal(blob.type, 'image/jpeg');
});

test('안내 틀 CSS 변수는 프레임과 같은 좌표를 써요', () => {
  const values = {};
  setCaptureFrameStyle({ style: { setProperty: (key, value) => { values[key] = value; } } }, 1200, 1600);
  assert.equal(values['--frame-aspect'], '1200 / 1600');
  for (const [key, value] of Object.entries(guideRectPercent(1200, 1600))) assert.equal(values[`--guide-${key}`], `${value}%`);
});
