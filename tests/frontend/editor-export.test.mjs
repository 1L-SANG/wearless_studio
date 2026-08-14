import test from 'node:test';
import assert from 'node:assert/strict';

import {
  EXPORT_WIDTH,
  exportFileName,
  fitPixelRatio,
  stitchLayout,
  toBytesUrl,
} from '../../src/features/editor/editorExport.js';

const AID = '123e4567-e89b-42d3-a456-426614174000';

test('toBytesUrl: /file 자산 URL만 /bytes로 바꾼다 (상대·절대)', () => {
  assert.equal(toBytesUrl(`/v1/assets/${AID}/file`), `/v1/assets/${AID}/bytes`);
  assert.equal(
    toBytesUrl(`https://api.wearless.app/v1/assets/${AID}/file`),
    `https://api.wearless.app/v1/assets/${AID}/bytes`,
  );
});

test('toBytesUrl: blob·data·외부·비자산 URL은 그대로 둔다', () => {
  for (const src of [
    'blob:http://localhost:5173/abc',
    'data:image/png;base64,xyz',
    'https://cdn.example.com/img.png',
    '/assets/brand/logo.svg',
    `/v1/assets/not-a-uuid/file`,
    '',
    null,
  ]) {
    assert.equal(toBytesUrl(src), src);
  }
});

test('stitchLayout: 세로 오프셋 누적 + 최대 폭', () => {
  const { width, height, offsets } = stitchLayout([
    { width: 2000, height: 800 },
    { width: 2000, height: 1200 },
    { width: 2000, height: 500 },
  ]);
  assert.equal(width, 2000);
  assert.equal(height, 2500);
  assert.deepEqual(offsets, [0, 800, 2000]);
});

test('fitPixelRatio: 캔버스 한계 안에서는 2 유지, 넘으면 낮추되 1 밑으로는 안 내려간다', () => {
  assert.equal(fitPixelRatio(5000), 2);        // 5000*2=10000 < 30000
  assert.ok(fitPixelRatio(20000) < 2);          // 20000*2=40000 > 30000 → 축소
  assert.ok(fitPixelRatio(20000) >= 1);
  assert.equal(fitPixelRatio(100000), 1);       // 아무리 길어도 1 보장(브라우저가 최종 방어)
  assert.equal(fitPixelRatio(0), 2);
});

test('exportFileName: 금지문자 제거·빈 값 기본, 캡처 폭 계약 고정', () => {
  assert.equal(exportFileName('아이보리 니트 셋업'), '아이보리 니트 셋업.png');
  assert.equal(exportFileName('a/b:c*d?e"f<g>h|i'), 'a b c d e f g h i.png');
  assert.equal(exportFileName(''), '상세페이지.png');
  assert.equal(exportFileName('니트', '블록1'), '니트_블록1.png');
  assert.equal(EXPORT_WIDTH, 1000); // .ed-canvas 설계 폭과 같아야 한다 (features.css .ed-canvas)
});
