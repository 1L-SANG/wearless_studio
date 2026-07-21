import test from 'node:test';
import assert from 'node:assert/strict';

import {
  CONTENT_ROLES,
  STORYBOARD_TAXONOMY_VERSION,
  assignInternalContentRoles,
} from '../../src/lib/storyboardTaxonomy.js';

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
  assert.equal(normalized[1].cutType, 'styling');
  assert.equal(normalized[1].exampleId, null);
  assert.equal(normalized[1].thumb, baseThumb);
  assert.equal(normalized[2].contentRole, CONTENT_ROLES.BENEFIT);
  assert.equal(normalized[2].cutType, 'horizon');
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
