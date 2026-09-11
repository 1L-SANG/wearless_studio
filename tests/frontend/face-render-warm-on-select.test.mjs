import test from 'node:test';
import assert from 'node:assert/strict';

import { isRealModelSelection } from '../../src/features/analysis/modelSelection.js';

// AnalysisForm 의 워밍 규칙(모델을 고르는 순간 1회)을 순수 함수로 재현해 고정한다.
// 컴포넌트 렌더 없이 검증하는 이유: 이 규칙의 값은 "언제 보내고 언제 안 보내는가" 하나이고,
// 그건 selectedModelId 의 변화만으로 정해진다.
function warmReducer(calls) {
  let warmed = null;
  return (modelId) => {
    if (!isRealModelSelection(modelId)) { warmed = null; return; }
    if (warmed === modelId) return;
    warmed = modelId;
    calls.push(modelId);
  };
}

const REAL = '11111111-1111-1111-1111-111111111111';
const REAL2 = '22222222-2222-2222-2222-222222222222';

test('REAL 모델을 고르면 한 번 보낸다', () => {
  const calls = [];
  const select = warmReducer(calls);
  select(REAL);
  assert.deepEqual(calls, [REAL]);
});

test('같은 REAL 을 다시 골라도 추가 호출이 없다', () => {
  const calls = [];
  const select = warmReducer(calls);
  select(REAL);
  select(REAL);
  select(REAL);
  assert.deepEqual(calls, [REAL]);
});

test('가상 모델은 보내지 않는다', () => {
  const calls = [];
  const select = warmReducer(calls);
  for (const id of ['mA', 'mB', null, undefined, '']) select(id);
  assert.deepEqual(calls, []);
});

test('REAL → 가상 → REAL 이면 두 번', () => {
  const calls = [];
  const select = warmReducer(calls);
  select(REAL);
  select('mA');
  select(REAL);
  assert.deepEqual(calls, [REAL, REAL]);
});

test('다른 REAL 로 바꾸면 그 모델로 한 번 더', () => {
  const calls = [];
  const select = warmReducer(calls);
  select(REAL);
  select(REAL2);
  assert.deepEqual(calls, [REAL, REAL2]);
});

test('AnalysisForm 이 같은 규칙을 쓴다', async () => {
  const { readFile } = await import('node:fs/promises');
  const src = await readFile(new URL('../../src/features/analysis/AnalysisForm.jsx', import.meta.url), 'utf8');
  assert.match(src, /warmedModelRef/);
  assert.match(src, /if \(warmedModelRef\.current === modelId\) return;/);
  assert.match(src, /\[a\.selectedModelId\]/);
  // 확인 버튼의 기존 호출도 남아 있어야 한다(서버가 60초 중복을 무시한다)
  assert.match(src, /void warmFaceRender\(a\.selectedModelId\)/);
});
