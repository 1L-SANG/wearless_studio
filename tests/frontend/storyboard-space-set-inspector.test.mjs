import test from 'node:test';
import assert from 'node:assert/strict';
import { nodes } from './helpers/storyboardComponents.mjs';
import { storyboardPhotoPanelHarness } from './helpers/storyboardPhotoPanelHarness.mjs';
import { storyboardSpaceSetsFor, spaceSetGroupId } from '../../src/lib/storyboardSpaceSetCatalog.js';
const set = storyboardSpaceSetsFor({ gender: 'women', clothingType: 'top' }).find(item => item.setType === 'horizon-rotation');
const block = { id: 'photo', source: 'ai', cutType: 'horizon', shot: 'full', direction: 'front', contentRole: 'fit', exampleId: set.members[0].exampleId,
  spaceGroupId: spaceSetGroupId(set.id, 'inspector'), colorId: 'base' };

test('a studio set member keeps a one-photo example gallery and offers a whole-set replacement action', async () => {
  const calls = [], h = await storyboardPhotoPanelHarness({ block, onChangeSpaceSet: () => calls.push('replace'), spaceContext: { set, siblings: [block] } });
  const tree = h.render(), gallery = nodes(tree).find(node => node.type === 'mood-guide');
  assert.ok(gallery); assert.equal(gallery.props.exampleId, block.exampleId);
  const button = nodes(tree).find(node => node.type === 'button' && node.props.children === '다른 스튜디오 세트로 바꾸기');
  button.props.onClick(); assert.deepEqual(calls, ['replace']);
  assert.ok(nodes(tree).some(node => node.type === 'horizon-background'));
  assert.equal(nodes(tree).some(node => node.type === 'underline-tabs'), false); h.close();
});
test('an ordinary photo keeps example selection, color choice, matching and seller-photo replacement', async () => {
  const h = await storyboardPhotoPanelHarness({ block: { ...block, spaceGroupId: null }, matchClothing: [{ id: 'pants', name: '바지' }] });
  const tree = h.render();
  assert.ok(nodes(tree).some(node => node.type === 'mood-guide' && node.props.onUseMine));
  assert.ok(nodes(tree).some(node => node.type === 'color-dots'));
  assert.ok(nodes(tree).some(node => node.type === 'underline-tabs')); h.close();
});
test('replacing a set member with a different example clears membership in the saved change', async () => {
  const next = { id: 'new', cutType: 'horizon', shot: 'medium', direction: 'side', variants: ['all'] };
  const h = await storyboardPhotoPanelHarness({ block, spaceContext: { set, siblings: [block] }, catalogs: {
    genExamples: [next], shotTypes: [{ value: 'full' }, { value: 'medium' }], productShotTypes: [{ value: 'ghost' }],
  } });
  const guide = nodes(h.render()).find(node => node.type === 'mood-guide');
  await guide.props.onExampleChange('new', 'all');
  assert.equal(h.block.spaceGroupId, null); assert.equal(h.block.spaceSetMemberOrder, null); assert.equal(h.block.setSelectionOrigin, null);
  assert.equal(h.block.refScope, 'all'); assert.equal(h.block.shot, 'medium'); h.close();
});
