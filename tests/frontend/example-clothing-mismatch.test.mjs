import test from 'node:test';
import assert from 'node:assert/strict';
import examples from '../../src/data/genExamples.json' with { type: 'json' };
import { assignGenerationExamples } from '../../src/lib/generationExamples.js';
import { seedStoryboard } from '../../src/lib/pageTemplates/seed.js';
import { photoGalleryChoices, initialPhotoFraming } from '../../src/lib/pageTemplates/storyboardPhotoChoices.js';
import { defaultPageStyle } from '../../src/lib/pageTemplates/pageStyle.js';
import { signatureCutById } from '../../src/lib/signatureCutPool.js';
import { storyboardSpaceSetsFor, withStoryboardSpaceSetExamples } from '../../src/lib/storyboardSpaceSetCatalog.js';
import { stripStaleExampleSelections } from '../../src/lib/storyboardExampleStaleness.js';

const { genExamples: catalog } = withStoryboardSpaceSetExamples({ genExamples: examples });
const byId = new Map(catalog.map(example => [example.id, example]));
const sharedTopIds = [
  'ex_horizon_women_top_full_profile_02',
  'ex_horizon_women_top_full_profile_right_02',
  'ex_horizon_women_top_full_back_02',
  'ex_horizon_women_top_medium_back_01',
];
const satinId = 'ex_horizon_women_dress_medium_front_satin_01_v2';
const rotationPrefix = 'ss_set_horizon_women_bottom_threetimes_7563033280596_prod01_';
const gender = 'women';
const identityKind = 'virtual';

function initialBlocks(clothingType, templateId, category = {}) {
  const projectId = 'example-clothing-mismatch';
  const product = { clothingType, colors: [{ id: 'base', isBase: true, images: [{ slot: 'Back' }] }] };
  const analysis = { clothingType, targetGenders: [gender], ...category,
    pageStyle: { ...defaultPageStyle(), templateId } };
  const seeded = seedStoryboard(product, analysis, { projectId, identityKind });
  return assignGenerationExamples(seeded, { catalog, product, analysis, gender, projectId, identityKind }).blocks;
}

function horizonGallery(clothingType) {
  const sets = storyboardSpaceSetsFor({ gender, clothingType });
  return photoGalleryChoices(catalog, sets, { cutTypes: ['horizon'], studioStyle: 'horizon',
    clothingType, gender, identityKind, shot: 'all', place: 'studio' }).candidates;
}

// 2026-11-19 오너 결정: 원피스는 상의, 하의와 같이 쓰는 회전 세트와 상의 사진을 골라 쓴다.
test('dress gallery keeps the shared rotation set and four top examples, and fit slots are filled', () => {
  const gallery = horizonGallery('dress');
  assert.ok(gallery.some(example => example.id?.startsWith(rotationPrefix)));
  for (const id of sharedTopIds) assert.ok(gallery.some(example => example.id === id), id);
  for (const templateId of ['emo', 'show']) {
    const fit = initialBlocks('dress', templateId).filter(block => /^wear_fit\.(front|side|back)$/.test(block.slotKey || ''));
    assert.deepEqual(fit.map(block => block.direction), ['front', 'side', 'back']);
    assert.ok(fit.every(block => block.exampleId && byId.get(block.exampleId).applicableClothingTypes.includes('dress')));
  }
});

test('a cleared fit slot opens its photo gallery at the slot’s own framing', () => {
  for (const templateId of ['emo', 'show']) {
    const fit = initialBlocks('dress', templateId).filter(block => /^wear_fit\.(front|side|back)$/.test(block.slotKey || ''))
      .map(block => ({ ...block, exampleId: null }));
    assert.ok(fit.length);
    assert.ok(fit.every(block => initialPhotoFraming(block, null) === block.shot));
  }
});

test('women’s top gallery and showroom seed exclude the satin dress', () => {
  assert.ok(horizonGallery('dress').some(example => example.id === satinId));
  assert.ok(horizonGallery('top').every(example => example.id !== satinId));
  assert.ok(initialBlocks('top', 'show').every(block => block.exampleId !== satinId));
});

test('women’s bottom hero and background slots prefer the product’s bottom kind', () => {
  for (const [category, kind] of [
    [{ subCategory: 'midi_skirt' }, 'skirt'],
    [{ customCategory: '플리츠 치마' }, 'skirt'],
    [{ subCategory: 'slacks' }, 'pants'],
  ]) {
    for (const [templateId, expectedSlots] of [
      ['emo', ['faq.background', 'first.hero', 'review.background']],
      ['show', ['first.hero']],
    ]) {
      const picks = initialBlocks('bottom', templateId, category)
        .filter(block => block.slotKey === 'first.hero' || block.slotKey?.endsWith('.background'));
      assert.deepEqual(picks.map(block => block.slotKey).sort(), expectedSlots);
      for (const block of picks) {
        const example = byId.get(block.exampleId) || signatureCutById(block.exampleId);
        assert.equal(example?.bottomKind, kind, `${templateId} ${block.slotKey}`);
      }
    }
  }
});

test('stale picks clear independently', () => {
  const stale = { id: 'stale', source: 'ai', cutType: 'horizon', exampleId: satinId,
    exampleSelectionOrigin: 'user', thumb: 'example', baseThumb: 'placeholder' };
  const valid = { ...stale, id: 'valid', exampleId: sharedTopIds[0] };
  const repaired = stripStaleExampleSelections([stale, valid], catalog, { gender, clothingType: 'top' });
  assert.equal(repaired[0].exampleId, null);
  assert.equal(repaired[0].thumb, 'placeholder');
  assert.equal(repaired[1], valid);
});
