import test from 'node:test';
import assert from 'node:assert/strict';
import { nodes } from './helpers/storyboardComponents.mjs';
import { storyboardPhotoPanelHarness } from './helpers/storyboardPhotoPanelHarness.mjs';
import { storyboardSpaceSetsFor, spaceSetGroupId } from '../../src/lib/storyboardSpaceSetCatalog.js';
const set = storyboardSpaceSetsFor({ gender: 'women', clothingType: 'top' }).find(item => item.setType === 'horizon-rotation');
const block = { id: 'photo', source: 'ai', cutType: 'horizon', shot: 'full', direction: 'front', contentRole: 'fit', exampleId: set.members[0].exampleId,
  spaceGroupId: spaceSetGroupId(set.id, 'inspector'), colorId: 'base' };

test('R5 a studio set member has no photo inspector or per-photo settings', async () => {
  const h = await storyboardPhotoPanelHarness({ block, blocks: [block, { ...block, id: 'other', exampleId: set.members[1].exampleId }] });
  try { assert.equal(h.render(), null); assert.equal(h.changes.length, 0); } finally { h.close(); }
});
test('an ordinary photo keeps example selection, color choice, matching and seller-photo replacement', async () => {
  const h = await storyboardPhotoPanelHarness({ block: { ...block, spaceGroupId: null }, matchClothing: [{ id: 'pants', name: '바지' }] });
  const tree = h.render();
  assert.ok(nodes(tree).some(node => node.type === 'mood-guide' && node.props.onUseMine));
  assert.ok(nodes(tree).some(node => node.type === 'color-dots'));
  assert.ok(nodes(tree).some(node => node.type === 'underline-tabs')); h.close();
});
test('R5 an indirect request cannot reopen a set member or change its saved settings', async () => {
  const h = await storyboardPhotoPanelHarness({ block, blocks: [block, { ...block, id: 'other', exampleId: set.members[1].exampleId }], requestedRecipe: { blockId: block.id, cutType: 'styling', shot: 'medium' } });
  try { h.render(); assert.equal(h.render(), null); assert.equal(h.changes.length, 0); assert.deepEqual(h.block, block); } finally { h.close(); }
});
