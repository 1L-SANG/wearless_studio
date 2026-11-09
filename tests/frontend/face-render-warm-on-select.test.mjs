import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { defaultPageStyle, validPageStyle } from '../../src/lib/pageTemplates/pageStyle.js';

const source = readFileSync(new URL('../../src/features/analysis/AnalysisForm.jsx', import.meta.url), 'utf8');

test('analysis selection and confirmation no longer warm FaceRender', () => {
  assert.doesNotMatch(source, /warmFaceRender|warmedModelRef/);
});

test('confirmation proceeds for a saved real selection without a brand-category prompt', async () => {
  const start = source.indexOf('  const confirmAnalysis = async () =>');
  const end = source.indexOf('\n  };', start) + '\n  };'.length;
  const confirmations = [];
  const style = defaultPageStyle();
  const bindings = {
    a: { selectedModelId: '11111111-1111-1111-1111-111111111111', brandUseCategory: null, pageStyle: style },
    confirming: false, onBeforeConfirm: undefined, setConfirming() {}, onConfirmingChange() {}, pageStyleSaveRef: { current: Promise.resolve() }, pageStyleValueRef: { current: style }, setPageStyleError() {},
    pageStyleReady: true, validPageStyle, onChange: () => assert.fail('unchanged template saved again'),
    mannequinGenerationCost: 0, onNext: (request) => confirmations.push(request),
    commitMdComment() {},
    isRealModelSelection: () => true, warmFaceRender: () => assert.fail('FaceRender warmed'),
    toast: { push: () => assert.fail('category prompt shown') },
  };
  const persist = source.slice(source.indexOf('  const persistSelectedPageStyle ='), source.indexOf('  const prepareOutfit ='));
  const confirm = new Function(...Object.keys(bindings), `${persist}\n${source.slice(start, end)}; return confirmAnalysis;`)(...Object.values(bindings));
  await confirm();
  assert.deepEqual(confirmations, [{ expectedQuote: { free: true, credits: 0 } }]);
});
