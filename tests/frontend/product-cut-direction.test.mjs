import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { defaultStoryboard } from '../../src/lib/api/shapes.js';
import { ensureSections } from '../../src/lib/sections.js';
import { assignGenerationExamples } from '../../src/lib/generationExamples.js';
import { directionSpecFromExample, generationExampleSelectionPatch } from '../../src/lib/storyboardExampleSelection.js';
import { isProductDetail, preserveDetailTargetBinding } from '../../src/lib/detailRecommendations.js';
import * as taxonomy from '../../src/lib/storyboardTaxonomy.js';
import { generationExampleStructuralRecipePatch } from '../../src/lib/storyboardExampleSelection.js';

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
  const catalog = JSON.parse(readFileSync(new URL('../../src/data/genExamples.json', import.meta.url), 'utf8'));
  const assigned = assignGenerationExamples(seeds, { catalog, product, gender: 'women' }).blocks;
  const ghosts = assigned.filter(block => block.cutType === 'product' && block.shot === 'ghost');
  assert.deepEqual(ghosts.map(block => block.exampleId), ['ex_product_top_ghost_01', 'ex_product_top_ghost_01']);
  const loaded = ensureSections(assigned).map(block => ({ ...block, ...feedback(block, {}, { genExamples: catalog }) }));
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

test('콘티 샷 전환 확정은 기존 원본 면을 보존하고 착용컷의 옆면은 앞면으로 시작한다', async () => {
  const body = board.slice(board.indexOf('  const commitPendingRecipe ='), board.indexOf('  const generationExamplePatch ='));
  for (const direction of ['front', 'back', 'side']) {
    let changes;
    const scope = { ...taxonomy, generationExampleStructuralRecipePatch, referenceFeedbackPatch: feedback,
      block: { id: 'card', source: 'ai', cutType: 'horizon', contentRole: 'fit', direction, shot: 'full', colorId: 'base' },
      pendingRecipe: { cutType: 'product', shot: 'ghost' }, pendingSaving: false, catalogs,
      effectiveSectionRole: 'product', hasDetailImage: false, colorOpts: [{ id: 'base' }], detailColorOpts: [{ id: 'base' }],
      clothingType: 'top', closureOptions: [], pendingInSpace: false,
      setPendingChoice() {}, setPendingSaving() {}, setPendingError() {}, setPendingRecipe() {},
      onAtomicChange: value => { changes = value; },
    };
    const commit = new Function('scope', `with (scope) { ${body}; return commitPendingRecipe; }`)(scope);
    await commit('example-ghost');
    assert.equal(changes.direction, direction === 'back' ? 'back' : 'front');
  }
});

test('제품 예시를 끌어 새 카드를 추가해도 예시의 뒷면이 원본 면을 정하지 않는다', () => {
  const catalog = JSON.parse(readFileSync(new URL('../../src/data/genExamples.json', import.meta.url), 'utf8'));
  const droppedExample = catalog.find(example => example.id === 'ex_product_bottom_detail_stone_pocket_01');
  assert.equal(droppedExample.direction, 'back');
  const purposePatch = taxonomy.blockPatchForContentRole(null, 'productOverview', { clothingType: 'bottom' });
  const body = board.slice(board.indexOf('    const insertionRecipe ='), board.indexOf('    const initialColorOpts ='));
  const recipe = new Function('droppedExample', 'droppedCutType', 'purposePatch', `${body}; return insertionRecipe;`)(droppedExample, droppedExample.cutType, purposePatch);
  assert.equal(recipe.direction, 'front');
  assert.equal(recipe.shot, 'detail');
});
