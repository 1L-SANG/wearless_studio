import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('../../src/features/analysis/AnalysisForm.jsx', import.meta.url), 'utf8');

test('analysis selection and confirmation no longer warm FaceRender', () => {
  assert.doesNotMatch(source, /warmFaceRender|warmedModelRef/);
});

test('confirmation proceeds for a saved real selection without a brand-category prompt', async () => {
  const start = source.indexOf('  const confirmAnalysis = async () =>');
  const end = source.indexOf('\n  };', start) + '\n  };'.length;
  const confirmations = [];
  const bindings = {
    a: { selectedModelId: '11111111-1111-1111-1111-111111111111', brandUseCategory: null },
    confirming: false, setConfirming() {}, onConfirmingChange() {}, composeModeSaveRef: { current: Promise.resolve() },
    mannequinGenerationCost: 0, onNext: (request) => confirmations.push(request),
    isRealModelSelection: () => true, warmFaceRender: () => assert.fail('FaceRender warmed'),
    toast: { push: () => assert.fail('category prompt shown') },
  };
  const confirm = new Function(...Object.keys(bindings), `${source.slice(start, end)}; return confirmAnalysis;`)(...Object.values(bindings));
  await confirm();
  assert.deepEqual(confirmations, [{ expectedQuote: { free: true, credits: 0 } }]);
});
