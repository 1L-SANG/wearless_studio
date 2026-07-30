import test from 'node:test';
import assert from 'node:assert/strict';

import {
  CONTENT_ROLES,
  STORYBOARD_TAXONOMY_VERSION,
  assignInternalContentRoles,
  cutTypeOptionsForSection,
  generationExampleCompatible,
  generationExampleScopeAvailable,
  generationExampleStateAfterShotChange,
  isWornCrossShotPair,
  keepExampleOnShotChange,
  normalizedRecipePatch,
} from '../../src/lib/storyboardTaxonomy.js';

test('worn full and medium examples can stay selected without entering the current-shot gallery', () => {
  const example = {
    id: 'full-example', cutType: 'styling', shot: 'full', gender: 'women',
    applicableClothingTypes: ['top', 'outer'],
  };
  const full = { cutType: 'styling', shot: 'full', clothingType: 'top', gender: 'women' };
  const medium = { ...full, shot: 'medium' };

  assert.equal(generationExampleCompatible(example, full), true);
  assert.equal(generationExampleCompatible(example, medium), false);
  assert.equal(generationExampleCompatible(example, medium, { allowCrossShot: true }), true);
  assert.equal(generationExampleCompatible(example, { ...medium, cutType: 'horizon' }, { allowCrossShot: true }), false);
  assert.equal(generationExampleCompatible(example, { ...medium, clothingType: 'bottom' }, { allowCrossShot: true }), false);
  assert.equal(generationExampleCompatible(example, { ...medium, gender: 'men' }, { allowCrossShot: true }), false);
});

test('shot toggles retain examples only for the decided worn full-medium pair', () => {
  assert.equal(isWornCrossShotPair('styling', 'full', 'medium'), true);
  assert.equal(isWornCrossShotPair('mirror', 'medium', 'full'), true);
  assert.equal(keepExampleOnShotChange('horizon', 'full', 'medium'), true);
  assert.equal(keepExampleOnShotChange('styling', 'full', 'full'), true);
  assert.equal(keepExampleOnShotChange('product', 'ghost', 'detail'), false);
  assert.equal(keepExampleOnShotChange('styling', 'full', 'detail'), false);
});

test('shot-change state keeps both reference fields only for worn full-medium toggles', () => {
  const selected = { exampleId: 'example-1', refScope: 'pose' };

  for (const cutType of ['styling', 'horizon', 'mirror']) {
    assert.deepEqual(
      generationExampleStateAfterShotChange(cutType, 'full', 'medium', selected),
      { shot: 'medium', ...selected },
    );
  }
  assert.deepEqual(
    generationExampleStateAfterShotChange('product', 'ghost', 'detail', selected),
    { shot: 'detail', exampleId: null, refScope: 'all' },
  );
  assert.deepEqual(
    generationExampleStateAfterShotChange('styling', 'full', 'detail', selected),
    { shot: 'detail', exampleId: null, refScope: 'all' },
  );
});

test('an unavailable selected scope cannot silently fall back to the all asset', () => {
  assert.equal(generationExampleScopeAvailable(['all'], 'pose'), false);
  assert.equal(generationExampleScopeAvailable(['all'], 'bg'), false);
  assert.equal(generationExampleScopeAvailable(['all', 'pose'], 'all', { inSpace: true }), true);
  assert.equal(generationExampleScopeAvailable(['pose'], 'all', { isProduct: true }), false);
});

test('the first AI image in benefit is the only internally assigned hero', () => {
  const baseThumb = 'https://example.com/original.png';
  const blocks = [
    { id: 'mine', source: 'mine', sectionRole: 'benefit', contentRole: 'custom' },
    {
      id: 'first-ai', source: 'ai', sectionRole: 'benefit', contentRole: 'benefit',
      cutType: 'horizon', direction: 'front', shot: 'medium', taxonomyVersion: 2,
      exampleId: 'old-example', thumb: 'https://example.com/example.png', baseThumb,
    },
    {
      id: 'second-ai', source: 'ai', sectionRole: 'benefit', contentRole: 'hero',
      cutType: 'styling', direction: 'front', shot: 'full', taxonomyVersion: 2,
    },
  ];

  const normalized = assignInternalContentRoles(blocks);

  assert.equal(normalized[0], blocks[0]);
  assert.equal(normalized[1].contentRole, CONTENT_ROLES.HERO);
  assert.equal(normalized[1].cutType, 'horizon');
  assert.equal(normalized[1].exampleId, 'old-example');
  assert.equal(normalized[1].thumb, 'https://example.com/example.png');
  assert.equal(normalized[2].contentRole, CONTENT_ROLES.BENEFIT);
  assert.equal(normalized[2].cutType, 'styling');
});

test('the inspector offers cut types by section without exposing content roles', () => {
  assert.deepEqual(cutTypeOptionsForSection('benefit').map((option) => option.value), [
    'styling', 'horizon',
  ]);
  assert.deepEqual(cutTypeOptionsForSection('fit').map((option) => option.value), [
    'styling', 'horizon', 'mirror',
  ]);
  assert.deepEqual(cutTypeOptionsForSection('product').map((option) => option.value), [
    'product',
  ]);
});

test('a selected fit cut realigns the hidden role instead of being overwritten by it', () => {
  const mirror = normalizedRecipePatch({
    source: 'ai', sectionRole: 'fit', contentRole: 'coordination',
    cutType: 'mirror', shot: 'medium', faceExposure: 'same',
  }, CONTENT_ROLES.COORDINATION);
  const styling = normalizedRecipePatch({
    source: 'ai', sectionRole: 'fit', contentRole: 'fit',
    cutType: 'styling', direction: 'side', shot: 'medium',
  }, CONTENT_ROLES.FIT);

  assert.deepEqual(
    [mirror.contentRole, mirror.cutType, mirror.direction, mirror.shot, mirror.faceExposure],
    [CONTENT_ROLES.REAL_WEAR, 'mirror', null, 'medium', 'hide'],
  );
  assert.deepEqual(
    [styling.contentRole, styling.cutType, styling.direction, styling.shot],
    [CONTENT_ROLES.COORDINATION, 'styling', 'side', 'medium'],
  );
});

test('AI cards with no usable role receive the safe internal role for their section', () => {
  const normalized = assignInternalContentRoles([
    { id: 'benefit', source: 'ai', sectionRole: 'benefit', contentRole: 'custom' },
    { id: 'fit', source: 'ai', sectionRole: 'fit', contentRole: 'custom' },
    { id: 'product', source: 'ai', sectionRole: 'product' },
  ]);

  assert.deepEqual(
    normalized.map((block) => [block.contentRole, block.cutType, block.taxonomyVersion]),
    [
      [CONTENT_ROLES.HERO, 'styling', STORYBOARD_TAXONOMY_VERSION],
      [CONTENT_ROLES.COORDINATION, 'styling', STORYBOARD_TAXONOMY_VERSION],
      [CONTENT_ROLES.PRODUCT_OVERVIEW, 'product', STORYBOARD_TAXONOMY_VERSION],
    ],
  );
});

test('a valid internal composition is returned unchanged', () => {
  const blocks = [
    {
      id: 'hero', source: 'ai', sectionRole: 'benefit', contentRole: 'hero',
      title: '첫 장면', cutType: 'styling', direction: 'front', shot: 'full', taxonomyVersion: 2,
    },
    {
      id: 'fit', source: 'ai', sectionRole: 'fit', contentRole: 'fit',
      title: '핏 확인', cutType: 'horizon', direction: 'front', shot: 'full', taxonomyVersion: 2,
    },
  ];

  assert.equal(assignInternalContentRoles(blocks), blocks);
});

test('a saved block without an example cannot retain an orphaned scoped reference', () => {
  const [normalized] = assignInternalContentRoles([{
    id: 'hero', source: 'ai', sectionRole: 'benefit', contentRole: 'hero',
    title: '첫 장면', cutType: 'styling', direction: 'front', shot: 'full', taxonomyVersion: 2,
    exampleId: null, refScope: 'pose',
  }]);

  assert.equal(normalized.exampleId, null);
  assert.equal(normalized.refScope, 'all');
});

test('an internally normalized product image drops worn-only settings', () => {
  const [product] = assignInternalContentRoles([{
    id: 'product', source: 'ai', sectionRole: 'product', contentRole: 'fit',
    title: '핏 확인', cutType: 'horizon', direction: 'front', shot: 'full', taxonomyVersion: 2,
    matchIds: ['pants-1'], outerClosureState: 'closed', faceExposure: 'show',
    exampleId: 'worn-example', refScope: 'pose',
  }]);

  assert.equal(product.contentRole, CONTENT_ROLES.PRODUCT_OVERVIEW);
  assert.equal(product.cutType, 'product');
  assert.deepEqual(product.matchIds, []);
  assert.equal(product.outerClosureState, null);
  assert.equal(product.faceExposure, null);
  assert.equal(product.exampleId, null);
  assert.equal(product.refScope, 'all');
});
