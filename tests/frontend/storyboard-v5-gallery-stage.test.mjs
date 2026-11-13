import test from 'node:test';
import assert from 'node:assert/strict';
import * as gallery from '../../src/lib/pageTemplates/storyboardGallery.js';
import { packStageRows } from '../../src/lib/pageTemplates/storyboardStage.js';
import { seedStoryboard } from '../../src/lib/pageTemplates/seed.js';
import { defaultPageStyle } from '../../src/lib/pageTemplates/pageStyle.js';
import { storyboardSpaceSetsFor, withStoryboardSpaceSetExamples } from '../../src/lib/storyboardSpaceSetCatalog.js';
import { assignGenerationExamples } from '../../src/lib/generationExamples.js';
import rawExamples from '../../src/data/genExamples.json' with { type: 'json' };

const catalog = withStoryboardSpaceSetExamples({ genExamples: rawExamples }).genExamples;
test('place tabs separate park from nature and expose a selected hidden category', () => {
  assert.equal(gallery.placeCategory('park-garden').value, 'park');
  assert.equal(gallery.placeCategory('waterfront').value, 'nature');
  assert.equal(gallery.placeCategory('resort-terrace').value, 'nature');
  assert.equal(gallery.placeCategory('urban-alley').value, 'alley');
  assert.equal(gallery.placeCategory('storefront-street').value, 'street');
  assert.equal(gallery.placeCategory('urban-building-exterior').value, 'building');
  assert.ok(gallery.visiblePlaceTabs('nature', false).some(tab => tab.value === 'nature'));
});
test('manual gallery keeps used examples selectable and filters shot without changing the saved block', () => {
  const examples = [{ id: 'full', shot: 'full', cutType: 'styling', placeType: 'park-garden' },
    { id: 'medium', shot: 'medium', cutType: 'styling', placeType: 'waterfront' }];
  assert.deepEqual(gallery.filterGalleryExamples(examples, { shot: 'all', place: 'all' }).map(e => e.id), ['full', 'medium']);
  assert.deepEqual(gallery.filterGalleryExamples(examples, { shot: 'medium', place: 'nature' }).map(e => e.id), ['medium']);
  const used = gallery.usedGalleryExampleIds([{ id: 'selected', exampleId: 'full' }, { id: 'other', exampleId: 'medium' }], 'selected');
  assert.equal(used.has('medium'), true);
  assert.equal(used.has('full'), false);
  assert.equal(gallery.filterGalleryExamples(examples, { used }).length, 2);
});
test('a dragged standalone example gets another shot at the same place', () => {
  const examples = [{ id: 'a', shot: 'full', cutType: 'styling', placeType: 'park-garden' },
    { id: 'b', shot: 'medium', cutType: 'styling', placeType: 'park-garden' },
    { id: 'c', shot: 'medium', cutType: 'styling', placeType: 'home-interior' }];
  assert.deepEqual(gallery.samePlaceExamplePair(examples[0], examples, []).map(e => e.id), ['a', 'b']);
});
test('automatic filling still gives an unused eligible example priority', () => {
  const product = { clothingType: 'top', colors: [{ id: 'base', isBase: true }] };
  const first = seedStoryboard(product, { targetGenders: ['women'], pageStyle: defaultPageStyle() })[0];
  const choices = catalog.filter(e => e.cutType === first.cutType && e.shot === first.shot && !e.setOnly && e.gender === 'women' && e.applicableClothingTypes.includes('top')).slice(0, 3);
  const previous = { ...first, exampleId: choices[0].id, exampleSelectionOrigin: 'user' };
  const fresh = { ...first, id: 'fresh', slotKey: null, exampleId: null, exampleSelectionOrigin: null };
  const result = assignGenerationExamples([previous, fresh], { catalog: choices, product, gender: 'women', onlyBlockIds: ['fresh'] });
  assert.notEqual(result.blocks[1].exampleId, previous.exampleId);
});

const photo = (id, extra = {}) => ({ id, source: 'ai', colorId: 'base', ...extra });
test('four-column stage keeps a short bundle intact and puts the add tile last', () => {
  const blocks = [photo('a'), photo('b', { spaceGroupId: 'set1' }), photo('c', { spaceGroupId: 'set1' }),
    photo('d', { spaceGroupId: 'set2' }), photo('e', { spaceGroupId: 'set2' })];
  const rows = packStageRows(blocks, { add: true });
  assert.deepEqual(rows.map(row => row.flatMap(group => group.blocks.map(b => b.id))), [['a', 'b', 'c'], ['d', 'e']]);
  assert.equal(rows.at(-1).at(-1).kind, 'add');
  assert.ok(rows.every(row => row.reduce((n, group) => n + (group.kind === 'add' ? 1 : group.blocks.length), 0) <= 4));
});
test('long bundles continue across rows and twelve cuts hide the add tile', () => {
  const blocks = Array.from({ length: 12 }, (_, i) => photo(String(i), { spaceGroupId: i < 6 ? 'long' : 'second' }));
  const rows = packStageRows(blocks, { add: true });
  assert.deepEqual(rows.map(row => row.flatMap(group => group.blocks).length), [4, 2, 4, 2]);
  assert.equal(rows.flat().some(group => group.kind === 'add'), false);
});
