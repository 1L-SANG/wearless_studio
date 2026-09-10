import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { landingNavItems, landingNavAction, facemarketRootTarget } from '../../src/features/facemarket-landing/facemarketRootTarget.js';
import { APPLY_START_FAQ } from '../../src/features/facemarket-landing/applyStartFaq.js';
const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');

test('apply is the first public navigation destination and a permitted return path', () => {
  assert.deepEqual(landingNavItems()[0], { to: '/apply', label: '모델 지원', protected: false });
  assert.equal(landingNavAction('/apply', { session: null, loading: true }), 'navigate');
  assert.equal(facemarketRootTarget('/apply'), '/apply');
});
test('the ten FAQ titles and answers match the approved source, omitting the held protection claim', () => {
  const source = read('../../mockups/facemarket_flows_20260909/apply/faq/faq_final.md').split('## 지원 시작 페이지 FAQ 11개')[1].split('## 별도 페이지 후보')[0];
  const expected = [...source.matchAll(/\*\*Q\. (.+?)\*\*\n([\s\S]*?)(?=\n\n\*\*Q\.|$)/g)]
    .map(([, q, a]) => ({ q, a: a.trim() })).filter(({ q }) => q !== '제 얼굴이 확실히 지켜지는 건가요?');
  assert.equal(APPLY_START_FAQ.length, 10);
  assert.deepEqual(APPLY_START_FAQ, expected);
});
test('public start is routed in LandingShell and links to the guarded application', () => {
  assert.match(read('../../src/apps/facemarket/App.jsx'), /path="apply" element=\{<ApplyStartPage \/>\}/);
  const page = read('../../src/features/facemarket-landing/pages/ApplyStartPage.jsx');
  assert.match(page, /<LandingShell/);
  assert.match(page, /to="\/model\/apply"/);
});
