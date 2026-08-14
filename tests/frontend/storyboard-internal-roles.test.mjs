import test from 'node:test';
import assert from 'node:assert/strict';

import {
  CONTENT_ROLES,
  STORYBOARD_TAXONOMY_VERSION,
  allowedCutTypeOptionsForSection,
  assignInternalContentRoles,
  cutTypeOptionsForSection,
  normalizedRecipePatch,
} from '../../src/lib/storyboardTaxonomy.js';
import { adoptSection, ensureSections } from '../../src/lib/sections.js';

test('taxonomy v2 projects migrate to the four official sections without losing cards', () => {
  const migrated = ensureSections([
    { id: 'hero', source: 'ai', taxonomyVersion: 2, sectionRole: 'benefit', contentRole: 'hero', cutType: 'styling' },
    { id: 'daily', source: 'ai', taxonomyVersion: 2, sectionRole: 'fit', contentRole: 'coordination', cutType: 'styling' },
    { id: 'studio', source: 'ai', taxonomyVersion: 2, sectionRole: 'fit', contentRole: 'fit', cutType: 'horizon' },
    { id: 'product', source: 'ai', taxonomyVersion: 2, sectionRole: 'product', contentRole: 'productOverview', cutType: 'product' },
  ]);

  assert.deepEqual(migrated.map((block) => block.id), ['hero', 'daily', 'studio', 'product']);
  assert.deepEqual(migrated.map((block) => block.sectionRole), [
    'hooking', 'styling', 'studio', 'product',
  ]);
  assert.ok(migrated.every((block) => block.taxonomyVersion === STORYBOARD_TAXONOMY_VERSION));
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

test('the inspector offers cut types by the four official sections without exposing content roles', () => {
  assert.deepEqual(cutTypeOptionsForSection('hooking').map((option) => option.value), [
    'styling', 'horizon',
  ]);
  assert.deepEqual(cutTypeOptionsForSection('styling').map((option) => option.value), [
    'styling',
  ]);
  assert.deepEqual(cutTypeOptionsForSection('studio').map((option) => option.value), [
    'horizon',
  ]);
  assert.deepEqual(cutTypeOptionsForSection('product').map((option) => option.value), [
    'product',
  ]);
  assert.deepEqual(
    [...new Set(['hooking', 'styling', 'studio', 'product'].flatMap((section) => (
      cutTypeOptionsForSection(section).map((option) => option.value)
    )))],
    ['styling', 'horizon', 'product'],
  );
  assert.deepEqual(allowedCutTypeOptionsForSection('styling').map((option) => option.value), [
    'styling', 'mirror',
  ], 'drag and section gates still accept saved mirror cuts');
});

test('a selected worn cut realigns the hidden role instead of being overwritten by it', () => {
  const mirror = normalizedRecipePatch({
    source: 'ai', sectionRole: 'styling', contentRole: 'coordination',
    cutType: 'mirror', shot: 'medium', faceExposure: 'same',
  }, CONTENT_ROLES.COORDINATION);
  const styling = normalizedRecipePatch({
    source: 'ai', sectionRole: 'studio', contentRole: 'fit',
    cutType: 'styling', direction: 'side', shot: 'medium',
  }, CONTENT_ROLES.FIT);

  assert.deepEqual(
    [mirror.contentRole, mirror.cutType, mirror.direction, mirror.shot, mirror.faceExposure],
    [CONTENT_ROLES.REAL_WEAR, 'mirror', null, 'medium', 'hide'],
  );
  assert.deepEqual(
    [styling.contentRole, styling.cutType, styling.direction, styling.shot],
    [CONTENT_ROLES.FIT, 'horizon', 'side', 'medium'],
  );
});

test('AI cards with no usable role receive the safe internal role for their section', () => {
  const normalized = assignInternalContentRoles([
    { id: 'hooking', source: 'ai', sectionRole: 'hooking', contentRole: 'custom' },
    { id: 'styling', source: 'ai', sectionRole: 'styling', contentRole: 'custom' },
    { id: 'studio', source: 'ai', sectionRole: 'studio', contentRole: 'custom' },
    { id: 'product', source: 'ai', sectionRole: 'product' },
  ]);

  assert.deepEqual(
    normalized.map((block) => [block.contentRole, block.cutType, block.taxonomyVersion]),
    [
      [CONTENT_ROLES.HERO, 'styling', STORYBOARD_TAXONOMY_VERSION],
      [CONTENT_ROLES.COORDINATION, 'styling', STORYBOARD_TAXONOMY_VERSION],
      [CONTENT_ROLES.FIT, 'horizon', STORYBOARD_TAXONOMY_VERSION],
      [CONTENT_ROLES.PRODUCT_OVERVIEW, 'product', STORYBOARD_TAXONOMY_VERSION],
    ],
  );
});

test('a valid internal composition is returned unchanged', () => {
  const blocks = [
    {
      id: 'hero', source: 'ai', sectionRole: 'hooking', contentRole: 'hero',
      title: '첫 장면', cutType: 'styling', direction: 'front', shot: 'full', faceExposure: 'same', taxonomyVersion: 3,
    },
    {
      id: 'fit', source: 'ai', sectionRole: 'studio', contentRole: 'fit',
      title: '핏 확인', cutType: 'horizon', direction: 'front', shot: 'full', faceExposure: 'same', taxonomyVersion: 3,
    },
  ];

  assert.equal(assignInternalContentRoles(blocks), blocks);
});

test('an internally normalized product image drops worn-only settings', () => {
  const [product] = assignInternalContentRoles([{
    id: 'product', source: 'ai', sectionRole: 'product', contentRole: 'fit',
    title: '핏 확인', cutType: 'horizon', direction: 'front', shot: 'full', taxonomyVersion: 2,
    matchIds: ['pants-1'], outerClosureState: 'closed', faceExposure: 'show',
  }]);

  assert.equal(product.contentRole, CONTENT_ROLES.PRODUCT_OVERVIEW);
  assert.equal(product.cutType, 'product');
  assert.deepEqual(product.matchIds, []);
  assert.equal(product.outerClosureState, null);
  assert.equal(product.faceExposure, null);
});


test('section moves retain compatible examples and clear all example metadata only when the recipe must change', () => {
  const compatible = {
    id: 'moving', source: 'ai', sectionId: 'hooking-section', sectionRole: 'hooking',
    contentRole: 'hero', cutType: 'styling', direction: 'front', shot: 'full',
    exampleId: 'example-1', exampleSelectionOrigin: 'auto',
    thumb: 'example.png', baseThumb: 'base.png',
  };
  const stylingHost = {
    id: 'styling-host', source: 'ai', sectionId: 'styling-section', sectionRole: 'styling',
    contentRole: 'coordination', cutType: 'styling', direction: 'front', shot: 'full',
    sectionTitle: '스타일링', sectionLayout: 'stack',
  };
  const retained = adoptSection([compatible, stylingHost], 'moving', 'styling-section', 'styling');
  assert.equal(retained[0].exampleId, 'example-1');
  assert.equal(retained[0].exampleSelectionOrigin, 'auto');
  assert.equal(retained[0].thumb, 'example.png');

  const product = {
    ...compatible, cutType: 'product', shot: 'ghost', contentRole: 'productOverview',
    sectionRole: 'product', sectionId: 'product-section',
  };
  const cleared = adoptSection([product, stylingHost], 'moving', 'styling-section', 'styling');
  assert.equal(cleared[0].exampleId, null);
  assert.equal(cleared[0].exampleSelectionOrigin, null);
  assert.equal(cleared[0].thumb, 'base.png');
  assert.equal(cleared[0].baseThumb, null);
});
