import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import * as matching from '../../src/mock/matchingRecommendation.js';

const item = (id, colorGroup, extra = {}) => ({
  id, colorGroup, clothingType: 'bottom', gender: 'women', isActive: true,
  styleTags: ['daily'], category: '팬츠', ...extra,
});
const rank = (items, options = {}) => matching.recommendMatchingItems({
  items, clothingType: 'top', targetGenders: ['women'], styleTags: ['basic'],
  productColor: 'yellow', ...options,
});

test('yellow top promotes the reference brown bottom and keeps the weight-zero rollback', () => {
  const items = [item('a-gray', 'gray'), item('z-brown', 'brown')];
  assert.deepEqual(rank(items).map((x) => x.id), ['z-brown', 'a-gray']);
  assert.equal(rank(items, { colorWeight: 0 })[0].id, 'a-gray');
});

test('bottom products query upper/lower color pairs in the right direction', () => {
  const items = [item('a-gray', 'gray', { clothingType: 'top' }), item('z-yellow', 'yellow', { clothingType: 'top' })];
  assert.equal(rank(items, { clothingType: 'bottom', productColor: 'brown' })[0].id, 'z-yellow');
});

test('fine shades survive the product and analysis input paths', () => {
  assert.equal(matching.productColorFrom({ colors: [{ isBase: true, swatchId: 'red', name: '버건디' }] }), 'burgundy');
  assert.equal(matching.productColorFrom({ colors: [{ id: 'base', isBase: true }] }, {
    swatchSuggestions: [{ colorGroupId: 'other', swatchId: 'blue' }, { colorGroupId: 'base', swatchId: 'gray', colorName: '차콜' }],
  }), 'charcoal');
});

test('denim metadata distinguishes dark denim from plain navy trousers', () => {
  const items = [item('a-navy', 'navy', { colorName: '네이비', category: '트라우저' }),
    item('z-denim', 'navy', { colorName: '네이비', category: '데님팬츠' })];
  assert.equal(rank(items)[0].id, 'z-denim');
});

test('reranking preserves a user-selected matching garment', () => {
  const current = [{ id: 'match_women_bottom_05', selected: true, selOrder: 1 }];
  const result = matching.recommendLegacyMatchClothing({ productColor: 'yellow', current });
  assert.deepEqual(result.filter((x) => x.selected).map((x) => x.id), ['match_women_bottom_05']);
});

test('all 15 directional reference rows stay identical to the server', () => {
  const python = readFileSync(new URL('../../server/app/agents/color_harmony.py', import.meta.url), 'utf8');
  const block = python.split('TOP_BOTTOM_COLORS = {')[1].split('\n}')[0];
  const rows = Object.fromEntries([...block.matchAll(/"([^"]+)": \(([^)]*)\)/g)]
    .map(([, key, values]) => [key, [...values.matchAll(/"([^"]+)"/g)].map(([, value]) => value)]));
  assert.equal(Object.keys(rows).length, 15);
  assert.deepEqual(matching.TOP_BOTTOM_COLORS, rows);
});
