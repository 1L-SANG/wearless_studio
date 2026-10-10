import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { defaultStoryboard } from '../../src/lib/api/shapes.js';
import { ensureSections } from '../../src/lib/sections.js';
import { assignGenerationExamples } from '../../src/lib/generationExamples.js';
import { directionSpecFromExample, generationExampleSelectionPatch } from '../../src/lib/storyboardExampleSelection.js';
import { isProductDetail, preserveDetailTargetBinding } from '../../src/lib/detailRecommendations.js';

const board = readFileSync(new URL('../../src/features/storyboard/Storyboard.jsx', import.meta.url), 'utf8');
const feedbackBody = board.slice(board.indexOf('function referenceFeedbackPatch('), board.indexOf('\nconst CARD_DRAG_THRESHOLD'));
const feedback = new Function('preserveDetailTargetBinding', 'isProductDetail', 'directionSpecFromExample', 'exampleThumbFor',
  `${feedbackBody}; return referenceFeedbackPatch;`)(preserveDetailTargetBinding, isProductDetail, directionSpecFromExample, (_catalog, id) => `thumb:${id}`);
const example = (shot = 'ghost') => ({ id: `example-${shot}`, cutType: 'product', shot, direction: 'front',
  thumb: '/example.jpg', variants: ['all'], applicableClothingTypes: ['top'], gender: null, rank: 1 });
const catalogs = { genExamples: [example(), example('detail')] };

test('상의 확장형의 앞면과 뒷면 고스트는 불러오기와 자동 배정 뒤에도 서로 다른 면이다', () => {
  const product = { clothingType: 'top', colors: [{ id: 'base', isBase: true, images: [] }] };
  const seeds = defaultStoryboard(product.colors, 'extended', product);
  const assigned = assignGenerationExamples(seeds, { catalog: catalogs.genExamples, product, gender: 'women' }).blocks;
  const loaded = ensureSections(assigned).map(block => ({ ...block, ...feedback(block, {}, catalogs) }));
  assert.deepEqual(loaded.filter(block => block.cutType === 'product' && block.shot === 'ghost').map(block => block.direction), ['front', 'back']);
});

for (const shot of ['ghost', 'detail']) {
  for (const previousId of [null, 'old-example']) test(`${shot} 뒷면의 예시 ${previousId ? '교체' : '첫 선택'}는 고른 원본 면을 보존한다`, () => {
    const block = { source: 'ai', cutType: 'product', shot, direction: 'back', colorId: 'base', exampleId: previousId };
    const selected = generationExampleSelectionPatch(block, example(shot), { defaultColorId: 'base' }).patch;
    const result = { ...block, ...feedback(block, selected, catalogs) };
    assert.equal(result.direction, 'back');
  });
}

test('대상 없는 디테일의 자동 배정도 원본 면을 보존한다', () => {
  const block = { id: 'detail', source: 'ai', cutType: 'product', shot: 'detail', direction: 'back' };
  const result = assignGenerationExamples([block], { catalog: catalogs.genExamples, product: { clothingType: 'top' } });
  assert.equal(result.blocks[0].direction, 'back');
});

test('착용컷의 예시 선택과 불러오기는 예시 방향과 사선 표현을 따른다', () => {
  for (const cutType of ['styling', 'horizon']) {
    const worn = { id: 'quarter', cutType, direction: 'side', sideStyle: 'threeQuarter' };
    const block = { source: 'ai', cutType, direction: 'back', exampleId: 'old' };
    const patch = generationExampleSelectionPatch(block, worn).patch;
    const result = { ...block, ...feedback(block, patch, { genExamples: [worn] }) };
    assert.equal(result.direction, 'side');
    assert.equal(result.sideStyle, 'threeQuarter');
  }
});
