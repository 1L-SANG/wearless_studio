import test from 'node:test';
import assert from 'node:assert/strict';
import { renderToStaticMarkup } from 'react-dom/server';
import { hookHarness, loadStoryboardComponent, nodes, textOf } from './helpers/storyboardComponents.mjs';
import { spaceSetDisplayName } from '../../src/lib/spaceSetDisplayNames.js';
import { inferStoryboardSpaceSet } from '../../src/lib/storyboardSpaceSetCatalog.js';
import { examplePlace } from '../../src/lib/pageTemplates/storyboardGallery.js';
async function panel(count, props = {}) {
  const h = hookHarness();
  const set = { id: 'set', name: '장소', setType: 'styling', members: Array.from({ length: count }, (_, i) => ({ exampleId: `e${i}`, thumb: `/photo-${i}.webp` })) };
  const Component = await loadStoryboardComponent('StoryboardSpaceSetPanel.jsx', 'StoryboardSpaceSetPanel', { ...h.hooks,
    Icon: 'svg', filterSpaceSetsForModel: value => value, storyboardSpaceSetsFor: () => [set], inferStoryboardSpaceSet,
    examplePlace, PlaceTabs: () => null, spaceSetDisplayName });
  const tree = h.render(Component, { mode: 'add', onChoose() {}, ...props });
  h.close(); return { tree, set };
}
for (const count of [3, 4, 5, 7]) test(`${count}-photo location set always displays a representative plus every member`, async () => {
  const { tree } = await panel(count);
  const html = renderToStaticMarkup(tree);
  assert.equal((html.match(/<img/g) || []).length, count + 1);
  assert.ok(textOf(tree).includes(`${count}장`));
});
test('choosing a set uses the complete set, without a photo-count selector', async () => {
  const chosen = [], { tree, set } = await panel(5, { onChoose: value => chosen.push(value) });
  nodes(tree).find(node => node.type === 'button').props.onClick();
  assert.deepEqual(chosen, [set]);
  assert.equal(nodes(tree).some(node => node.type === 'select' || node.type === 'input'), false);
});
test('a set that would exceed the remaining space is visibly disabled', async () => {
  const { tree } = await panel(5, { remaining: 2 });
  assert.equal(nodes(tree).find(node => node.type === 'button').props.disabled, true);
  assert.ok(textOf(tree).includes('자리가 부족해요'));
});
