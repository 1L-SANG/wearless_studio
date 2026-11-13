import test from 'node:test';
import assert from 'node:assert/strict';
import { addStoryboardPhoto, removeStoryboardPhoto, patchStoryboardPhoto, canReplaceStoryboardSet } from '../../src/lib/pageTemplates/photoActions.js';
import { seedStoryboard } from '../../src/lib/pageTemplates/seed.js';
import { defaultPageStyle } from '../../src/lib/pageTemplates/pageStyle.js';
import { validateStoryboardV4 } from '../../src/lib/pageTemplates/validateStoryboard.js';
import { withStoryboardSpaceSetExamples } from '../../src/lib/storyboardSpaceSetCatalog.js';
import raw from '../../src/data/genExamples.json' with { type: 'json' };
import { generationExampleSelectionPatch } from '../../src/lib/storyboardExampleSelection.js';
import { referenceFeedbackPatch } from '../../src/lib/storyboardExampleFeedback.js';
import { createSimilarCutBlock } from '../../src/lib/storyboardSimilarCuts.js';
import { addTemplateSection } from '../../src/lib/pageTemplates/sectionActions.js';

const product = { clothingType: 'top', colors: [{ id: 'base', isBase: true }] };
const analysis = { targetGenders: ['women'], pageStyle: defaultPageStyle() };
const catalog = withStoryboardSpaceSetExamples({ genExamples: raw }).genExamples;
test('a cut is appended inside its section with an automatically filled independent example', () => {
  const before = seedStoryboard(product, analysis);
  const run = before.find(b => b.pageSectionUsage === 'styling');
  const after = addStoryboardPhoto(before, run.pageSectionKey, { product, catalog, gender: 'women' });
  const added = after.find(b => !before.some(old => old.id === b.id));
  assert.ok(added.exampleId);
  assert.equal(added.spaceGroupId, undefined);
  assert.equal(after.indexOf(added), after.findLastIndex(b => b.pageSectionKey === run.pageSectionKey));
  validateStoryboardV4(after);
  assert.deepEqual(removeStoryboardPhoto(after, added.id, product), before);
  validateStoryboardV4(before);
});
test('photo deletion protects the first page and the last image of a photo-only section', () => {
  const before = seedStoryboard(product, analysis);
  assert.equal(removeStoryboardPhoto(before, before[0].id, product), before);
  let after = before;
  const key = before.find(b => b.pageSectionUsage === 'styling').pageSectionKey;
  for (const block of before.filter(b => b.pageSectionKey === key)) after = removeStoryboardPhoto(after, block.id, product);
  assert.equal(after.filter(b => b.pageSectionKey === key).length, 1);
  validateStoryboardV4(after);
});

test('the opening original stays required while an added similar photo can be removed', () => {
  const before = seedStoryboard(product, analysis), child = createSimilarCutBlock({ ...before[0], exampleId: 'released' }, 'opening-similar');
  const withChild = [before[0], child, ...before.slice(1)];
  assert.equal(removeStoryboardPhoto(withChild, before[0].id, product), withChild);
  assert.deepEqual(removeStoryboardPhoto(withChild, child.id, product), before);
});

test('changing a crop source to a side example or seller photo keeps the same available worn image', () => {
  const a = { targetGenders: ['women'], pageStyle: { ...defaultPageStyle(), templateId: 'show' } };
  const before = addTemplateSection(seedStoryboard(product, a), product, a, 'points');
  const sourceId = before.flatMap(block => block.pointItems || []).find(item => item.kind === 'wornCrop').sourceBlockId;
  const source = before.find(block => block.id === sourceId);
  const example = catalog.find(item => item.id === 'ex_horizon_women_top_full_profile_02');
  const patch = referenceFeedbackPatch(source, generationExampleSelectionPatch(source, example).patch, { genExamples: catalog });
  for (const changes of [patch, { source: 'mine', cutType: null, ownImages: ['seller:photo'] }]) {
    const after = patchStoryboardPhoto(before, source.id, changes, product);
    const crops = after.flatMap(block => block.pointItems || []).filter(item => item.kind === 'wornCrop');
    assert.ok(crops.length);
    assert.ok(crops.every(item => item.sourceBlockId === source.id));
    validateStoryboardV4(after);
  }
});

test('a seller photo replacing a detail source is no longer an AI detail point reference', () => {
  const a = { ...analysis, detailRecommendations: { version: 1, status: 'ready', candidates: [
    { id: 'neck', kind: 'structure', label: '목선', rank: 1, informationGroup: 'neck', photoUse: 'standalone', visibility: 'clear', direction: 'front' },
    { id: 'hem', kind: 'structure', label: '밑단', rank: 2, informationGroup: 'hem', photoUse: 'standalone', visibility: 'clear', direction: 'front' },
  ] } };
  const before = seedStoryboard(product, a), source = before.find(block => block.pageSectionKind === 'points' && block.source === 'ai');
  const after = patchStoryboardPhoto(before, source.id, { source: 'mine', cutType: null, ownImages: ['seller:detail'], thumb: 'seller:detail' }, product);
  assert.equal(after.flatMap(block => block.pointItems || []).some(item => item.sourceBlockId === source.id), false);
  assert.equal(after.find(block => block.id === source.id).source, 'mine');
  validateStoryboardV4(after);
});

test('only an all-seller-photo everyday-wear section can add an automatically-filled AI cut from its catalog recipe', () => {
  const before = seedStoryboard(product, analysis), key = before.find(block => block.pageSectionUsage === 'styling').pageSectionKey;
  let mine = before;
  for (const photo of before.filter(block => block.pageSectionKey === key && block.source !== 'text')) {
    mine = patchStoryboardPhoto(mine, photo.id, { source: 'mine', cutType: null, thumb: 'seller:wear', ownImages: ['seller:wear'] }, product);
  }
  const after = addStoryboardPhoto(mine, key, { product, catalog, gender: 'women' });
  const added = after.find(block => !before.some(old => old.id === block.id));
  assert.ok(added?.source === 'ai' && added.exampleId);
  assert.equal(added.pageSectionKey, key); validateStoryboardV4(after);
  for (const run of before.filter(block => block.pageSectionUsage !== 'styling')) {
    assert.equal(addStoryboardPhoto(before, run.pageSectionKey, { product, catalog, gender: 'women' }), before);
  }
});

test('set replacement capacity includes similar cuts retained after the original members', () => {
  const root = { id: 'root', source: 'ai', pageSectionKey: 'emo.wear_1', spaceGroupId: 'group' };
  const second = { ...root, id: 'second' };
  const blocks = [root, ...Array.from({ length: 10 }, (_, index) => ({ ...root, id: `similar-${index}`, similarSourceBlockId: root.id })), second];
  assert.equal(canReplaceStoryboardSet(blocks, 'group', { members: [1, 2, 3] }), false);
  assert.equal(canReplaceStoryboardSet(blocks, 'group', { members: [1, 2] }), true);
});
