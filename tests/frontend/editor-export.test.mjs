import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
  EXPORT_WIDTH,
  exportFileName,
  fitPixelRatio,
  isAssetBytesUrl,
  stitchLayout,
  toBytesUrl,
} from '../../src/features/editor/editorExport.js';

const AID = '123e4567-e89b-42d3-a456-426614174000';

test('toBytesUrl: /file 자산 URL을 /bytes로 바꾼다 (상대·절대·비uuid id — id 검증은 서버 몫)', () => {
  // ?e=2 는 과거 immutable 사본을 우회하는 현재 capability 버전이다.
  // 1년짜리로 캐시에 박아 둬서, 주소를 갈지 않으면 서버를 고쳐도 계속 차단된다.
  assert.equal(toBytesUrl(`/v1/assets/${AID}/file`), `/v1/assets/${AID}/bytes?e=2`);
  assert.equal(
    toBytesUrl(`https://api.wearless.app/v1/assets/${AID}/file`),
    `https://api.wearless.app/v1/assets/${AID}/bytes?e=2`,
  );
  // 프론트가 서버보다 엄격하면 어긋난다 — id 모양은 경로 수준만 본다 (리뷰 반영)
  assert.equal(toBytesUrl('/v1/assets/stable-1/file'), '/v1/assets/stable-1/bytes?e=2');
  assert.equal(toBytesUrl(`/v1/assets/${AID}/file?e=1`), `/v1/assets/${AID}/bytes?e=2`);
});

test('isAssetBytesUrl: 쿼리가 붙어도 핵심 자산으로 판정한다', () => {
  // 이 판정이 깨지면 상품컷 실패가 soft 로 강등돼 빈 이미지인 채 "저장 완료" 로 속인다.
  assert.equal(isAssetBytesUrl(`/v1/assets/${AID}/bytes?e=2`), true);
  assert.equal(isAssetBytesUrl(`/v1/assets/${AID}/bytes`), true);
  assert.equal(isAssetBytesUrl(toBytesUrl(`/v1/assets/${AID}/file`)), true);
  assert.equal(isAssetBytesUrl(`/v1/assets/${AID}/file`), false);
  assert.equal(isAssetBytesUrl('https://cdn.example.com/img.png'), false);
  assert.equal(isAssetBytesUrl(null), false);
});

test('toBytesUrl: blob·data·외부·비자산 URL은 그대로 둔다', () => {
  for (const src of [
    'blob:http://localhost:5173/abc',
    'data:image/png;base64,xyz',
    'https://cdn.example.com/img.png',
    '/assets/brand/logo.svg',
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

test('fitPixelRatio: 한계 안에서 2 유지, 넘치면 축소, 한 장이 불가능하면 null(ZIP 안내)', () => {
  assert.equal(fitPixelRatio(5000), 2);         // 5000*2=10000 < 30000
  assert.ok(fitPixelRatio(20000) < 2);           // 20000*2=40000 > 30000 → 축소
  assert.ok(fitPixelRatio(20000) >= 1);
  assert.equal(fitPixelRatio(30001), null);      // 배율 1로도 못 담음 → 호출부가 ZIP 안내
  assert.equal(fitPixelRatio(0), 2);
});

test('exportFileName: 금지문자 제거·빈 값 기본, 캡처 폭 계약 고정', () => {
  assert.equal(exportFileName('아이보리 니트 셋업'), '아이보리 니트 셋업.png');
  assert.equal(exportFileName('a/b:c*d?e"f<g>h|i'), 'a b c d e f g h i.png');
  assert.equal(exportFileName(''), '상세페이지.png');
  assert.equal(exportFileName('니트', '블록01'), '니트_블록01.png');
  assert.equal(EXPORT_WIDTH, 1000); // .ed-canvas 설계 폭과 같아야 한다 (features.css .ed-canvas)
});


test('내보내기 캡처에서 빈 사진 자리는 빠진다 — 완성본에 회색 네모가 찍히면 안 된다', () => {
  const source = readFileSync(new URL('../../src/features/editor/editorExport.js', import.meta.url), 'utf8');
  const chrome = source.slice(source.indexOf('const CHROME_SELECTOR'), source.indexOf("].join(',')"));
  // 사진을 비워 둔 칸('＋ 여기에 사진 넣기')은 편집기 안내지 상품 페이지 내용이 아니다.
  assert.match(chrome, /'\.el-slot'/);
  assert.match(chrome, /'\.slot-add'/, '＋ 버튼도 함께 빠진다');
  // 실제로 제거하는 코드와 이어져 있어야 목록이 의미가 있다.
  assert.match(source, /clone\.querySelectorAll\(CHROME_SELECTOR\)\.forEach\(\(n\) => n\.remove\(\)\)/);
});
