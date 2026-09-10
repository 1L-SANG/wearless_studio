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
test('the ten FAQ titles follow the approved list and omit the held protection claim', () => {
  // 승인본은 커밋되지 않는 mockups/ 폴더의 faq_final.md 라 CI 에는 없다. 제목 10개를 여기에 고정한다.
  const expected = [
    'FaceMarket에서 모델은 무슨 일을 하나요?',
    '지원할 때 무엇이 필요한가요?',
    '모델 경력이 없어도 지원할 수 있나요?',
    '승인은 어떻게 되고 언제 알 수 있나요?',
    '승인 뒤 등록할 때는 무엇을 하나요?',
    '제가 돈 쓰는 부분은 없는 건가요?',
    '얼마를 받나요? 언제 받나요?',
    '내 얼굴은 어디에 쓰이고, 어디에는 안 쓰이나요?',
    '내 얼굴이 어디에 쓰였는지 확인할 수 있나요?',
    '조건은 나중에 바꿀 수 있나요?',
  ];
  assert.deepEqual(APPLY_START_FAQ.map(({ q }) => q), expected);
  assert.ok(APPLY_START_FAQ.every(({ a }) => typeof a === 'string' && a.trim().length > 40));
  assert.ok(!APPLY_START_FAQ.some(({ q }) => q.includes('확실히 지켜지는')));
});
test('public start is routed in LandingShell and links to the guarded application', () => {
  assert.match(read('../../src/apps/facemarket/App.jsx'), /path="apply" element=\{<ApplyStartPage \/>\}/);
  const page = read('../../src/features/facemarket-landing/pages/ApplyStartPage.jsx');
  assert.match(page, /<LandingShell/);
  assert.match(page, /to="\/model\/apply"/);
});
