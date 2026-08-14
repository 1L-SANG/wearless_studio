import test from 'node:test';
import assert from 'node:assert/strict';

import { defaultStoryboard } from '../../src/lib/api/shapes.js';
import { matchingItemForColor } from '../../src/lib/colorwayMatching.js';

const MATCHES = [
  {
    id: 'match-light', name: '아이보리 셔츠', selected: true, selOrder: 1,
    isCompatible: true, colorName: '아이보리', colorGroup: 'ivory', colorBrightness: 93,
  },
  {
    id: 'match-dark', name: '블랙 셔츠', selected: false,
    isCompatible: true, colorName: '블랙', colorGroup: 'black', colorBrightness: 4,
  },
];

test('base color keeps the selected main garment and extra colors use compatible unselected candidates', () => {
  assert.equal(
    matchingItemForColor({ name: '블랙', swatchId: 'black' }, MATCHES, { preferMain: true }).id,
    'match-light',
  );
  assert.equal(
    matchingItemForColor({ name: '아이보리', swatchId: 'ivory' }, MATCHES).id,
    'match-dark',
  );
  assert.equal(
    matchingItemForColor({ name: '블랙', swatchId: 'black' }, MATCHES).id,
    'match-light',
  );
});

test('unknown color, a single selection, and incompatible selections fall back safely', () => {
  assert.equal(matchingItemForColor({ name: '미정' }, MATCHES).id, 'match-light');
  assert.equal(matchingItemForColor({ swatchId: 'ivory' }, [{ ...MATCHES[1], selected: true }]).id, 'match-dark');
  assert.equal(matchingItemForColor({ swatchId: 'ivory' }, [
    { ...MATCHES[0], isCompatible: false },
  ]), null);
});

test('Korean color aliases also receive a contrasting compatible garment', () => {
  assert.equal(matchingItemForColor({ name: '스카이 블루' }, MATCHES).id, 'match-dark');
  assert.equal(matchingItemForColor({ name: '크림 아이보리' }, MATCHES).id, 'match-dark');
});

test('extended seed gives each extra color one full-medium pair with one matching garment', () => {
  const colors = [
    { id: 'base', name: '블랙', swatchId: 'black', isBase: true, images: [] },
    { id: 'ivory', name: '아이보리', swatchId: 'ivory', images: [] },
  ];
  const blocks = defaultStoryboard(colors, 'extended', {
    projectId: 'colorway-pair', clothingType: 'top', targetGenders: ['women'], matchClothing: MATCHES,
  });
  const baseWorn = blocks.filter((block) => (
    ['styling', 'horizon', 'mirror'].includes(block.cutType) && block.colorId === 'base'
  ));
  const pair = blocks.filter((block) => block.colorwayGroupId);

  assert.ok(baseWorn.length > 0);
  assert.ok(baseWorn.every((block) => block.matchIds.join() === 'match-light'));
  assert.deepEqual(pair.map((block) => block.shot), ['full', 'medium']);
  assert.ok(pair.every((block) => block.matchIds.join() === 'match-dark'));
  assert.equal(new Set(pair.map((block) => block.layoutRowId)).size, 1);
});

test('extended entry assigns each color once and shares that match across its full-medium pair', () => {
  const colors = [
    { id: 'base', name: '블랙', swatchId: 'black', isBase: true, images: [] },
    { id: 'ivory', name: '아이보리', swatchId: 'ivory', images: [] },
    { id: 'navy', name: '네이비', swatchId: 'navy', images: [] },
  ];
  const blocks = defaultStoryboard(colors, 'extended', {
    projectId: 'colorway-entry-matches', clothingType: 'top', targetGenders: ['women'],
    matchClothing: MATCHES,
  });
  const worn = blocks.filter((block) => ['styling', 'horizon', 'mirror'].includes(block.cutType));
  const byColor = Map.groupBy(worn, (block) => block.colorId);

  assert.ok(byColor.get('base').every((block) => block.matchIds.join() === 'match-light'));
  assert.deepEqual(byColor.get('ivory').map((block) => block.matchIds), [
    ['match-dark'], ['match-dark'],
  ]);
  assert.deepEqual(byColor.get('navy').map((block) => block.matchIds), [
    ['match-light'], ['match-light'],
  ]);
});

test('colorway cuts keep pose automatic so one shared example can vary naturally', () => {
  const pair = defaultStoryboard([
    { id: 'base', name: '블랙', isBase: true, images: [] },
    { id: 'ivory', name: '아이보리', images: [] },
  ], 'extended', {
    projectId: 'colorway-poses', clothingType: 'top', targetGenders: ['women'],
  }).filter((block) => block.colorwayGroupId);

  assert.deepEqual(pair.map((block) => block.pose), ['auto', 'auto']);
  assert.deepEqual(pair.map((block) => block.poseLabel), ['AI 자동', 'AI 자동']);
  assert.ok(pair.every((block) => !Object.hasOwn(block, 'expression')));
});

test('extended groups every non-base color as one section 3 set even when base is not first', () => {
  const colors = [
    { id: 'ivory', name: '아이보리', images: [] },
    { id: 'base', name: '블랙', isBase: true, images: [] },
    { id: 'blue', name: '블루', images: [] },
  ];
  const extended = defaultStoryboard(colors, 'extended', {
    projectId: 'colorway-sets', clothingType: 'bottom', targetGenders: ['men'],
  });
  const basic = defaultStoryboard(colors, 'basic', {
    projectId: 'colorway-sets', clothingType: 'bottom', targetGenders: ['men'],
  });
  const sets = new Map();
  for (const block of extended.filter((item) => item.colorwayGroupId)) {
    const pair = sets.get(block.colorwayGroupId) || [];
    pair.push(block);
    sets.set(block.colorwayGroupId, pair);
  }

  assert.equal(basic.some((block) => block.colorwayGroupId), false);
  assert.deepEqual([...sets.keys()], ['colorway__ivory', 'colorway__blue']);
  for (const pair of sets.values()) {
    assert.equal(pair.length, 2);
    assert.ok(pair.every((block) => block.sectionRole === 'studio'));
    assert.deepEqual(pair.map((block) => block.shot), ['full', 'medium']);
    assert.equal(new Set(pair.map((block) => block.layoutRowId)).size, 1);
    assert.ok(pair.every((block) => block.pose === 'auto'));
    assert.ok(pair.every((block) => !Object.hasOwn(block, 'expression')));
  }
});

test('three extra colors reuse automatic pose instead of carrying color-specific directions', () => {
  const blocks = defaultStoryboard([
    { id: 'base', name: '블랙', isBase: true, images: [] },
    { id: 'ivory', name: '아이보리', images: [] },
    { id: 'sky', name: '스카이블루', images: [] },
    { id: 'gray', name: '그레이', images: [] },
  ], 'extended', {
    projectId: 'three-colorway-sets', clothingType: 'top', targetGenders: ['men'],
  }).filter((block) => block.colorwayGroupId);

  assert.deepEqual(blocks.map((block) => [block.colorId, block.shot, block.pose]), [
    ['ivory', 'full', 'auto'],
    ['ivory', 'medium', 'auto'],
    ['sky', 'full', 'auto'],
    ['sky', 'medium', 'auto'],
    ['gray', 'full', 'auto'],
    ['gray', 'medium', 'auto'],
  ]);
});
