import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

/* 색 하나를 추가하면 함께 고쳐야 하는 지점이 여섯 곳이다(2026-08-15 퍼플 추가 때
   컷 생성 색 메타·mock 조화표·컬러웨이 매핑 세 곳이 실제로 누락됐다). 다음 색 추가에서
   같은 누락이 반복되지 않게 각 지점의 존재를 계약으로 고정한다. */
const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');

test('퍼플이 팔레트 정본의 7번째 — 7열 그리드 첫 줄 맨 오른쪽', () => {
  const db = read('../../src/mock/db.js');
  const block = db.slice(db.indexOf('swatchColors: ['), db.indexOf('composeModes:'));
  const ids = [...block.matchAll(/id: '(\w+)'/g)].map((m) => m[1]);
  assert.equal(ids.length, 13);
  assert.equal(ids[6], 'purple');
  assert.equal(ids.at(-1), 'pink');   // 입력칸이 붙는 둘째 줄 마지막
});

test('퍼플이 색 관련 모든 지점에 등록돼 있다', () => {
  for (const [path, needle] of [
    ['../../src/features/product-input/colorAutofill.js', 'purple'],
    ['../../src/lib/colorwayMatching.js', 'purple'],
    ['../../src/mock/matchingRecommendation.js', "'purple'"],
    ['../../src/features/storyboard/Storyboard.jsx', "purple: '#7d5ba6'"],
  ]) assert.match(read(path), new RegExp(needle), `${path} 에 퍼플 누락`);
});

test('mock 조화표가 서버와 같은 쌍 수를 갖는다(패리티)', () => {
  const js = read('../../src/mock/matchingRecommendation.js');
  const block = js.slice(js.indexOf('export const COLOR_HARMONY = pairMap(['));
  const pairs = [...block.slice(0, block.indexOf(']);')).matchAll(/\['\w+',\s*'\w+',/g)];
  assert.equal(pairs.length, 105);   // 14색 완비: 14 * 15 / 2
});

test('색상 그리드는 7열 — 입력칸이 다른 칩 폭을 밀지 않는다', () => {
  const css = read('../../src/styles/features.css');
  assert.match(css, /\.swatch-grid \{[\s\S]*?repeat\(7, max-content\)/);
  assert.match(css, /\.swatch-grid \{[\s\S]*?justify-items: start/);
});
