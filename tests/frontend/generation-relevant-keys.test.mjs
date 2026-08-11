import test from 'node:test';
import assert from 'node:assert/strict';

import { isGenerationRelevantAnalysisPatch } from '../../src/lib/generationRelevance.js';

test('a patch touching only a non-generation-relevant key is not flagged', () => {
  assert.equal(isGenerationRelevantAnalysisPatch({ suggestedName: 'x' }), false);
});

test('renaming a color swatch does not trigger paid mannequin regeneration', () => {
  assert.equal(isGenerationRelevantAnalysisPatch({
    colors: [{ id: 'color-1', swatchId: 'black', name: '블랙' }],
  }), false);
});

test('a representative pre-existing generation-relevant key still returns true', () => {
  assert.equal(isGenerationRelevantAnalysisPatch({ fitProfile: { category: 'top' } }), true);
});

test('null, undefined and empty-object patches are never generation-relevant', () => {
  assert.equal(isGenerationRelevantAnalysisPatch(null), false);
  assert.equal(isGenerationRelevantAnalysisPatch(undefined), false);
  assert.equal(isGenerationRelevantAnalysisPatch({}), false);
});
