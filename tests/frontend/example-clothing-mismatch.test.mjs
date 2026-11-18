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
const excludedDressIds = [
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

test('dress gallery and initial fit slots exclude the A2 rotation and four top examples', () => {
  const excluded = id => id?.startsWith(rotationPrefix) || excludedDressIds.includes(id);
  const gallery = horizonGallery('dress');
  assert.ok(gallery.some(example => example.setOnly && example.shot === 'full'));
  assert.ok(gallery.every(example => !excluded(example.id)));
  for (const templateId of ['emo', 'show']) {
    const blocks = initialBlocks('dress', templateId);
    const fit = blocks.filter(block => /^wear_fit\.(front|side|back)$/.test(block.slotKey || ''));
    assert.deepEqual(fit.map(block => block.direction), ['front', 'side', 'back']);
    assert.ok(blocks.every(block => !excluded(block.exampleId)));
    assert.ok(fit.every(block => !block.exampleId || byId.get(block.exampleId).applicableClothingTypes.includes('dress')));
  }
});

test('an empty dress fit slot opens its photo gallery at the slot’s own framing', () => {
  for (const templateId of ['emo', 'show']) {
    const fit = initialBlocks('dress', templateId).filter(block => /^wear_fit\.(front|side|back)$/.test(block.slotKey || '') && !block.exampleId);
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
  const stale = { id: 'stale', source: 'ai', cutType: 'horizon', exampleId: excludedDressIds[0],
    exampleSelectionOrigin: 'user', thumb: 'example', baseThumb: 'placeholder' };
  const valid = { ...stale, id: 'valid', exampleId: satinId };
  const repaired = stripStaleExampleSelections([stale, valid], catalog, { gender, clothingType: 'dress' });
  assert.equal(repaired[0].exampleId, null);
  assert.equal(repaired[0].thumb, 'placeholder');
  assert.equal(repaired[1], valid);
});
