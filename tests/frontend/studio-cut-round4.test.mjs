import test from 'node:test';
import assert from 'node:assert/strict';
import raw from '../../src/data/genExamples.json' with { type: 'json' };
import { withStoryboardSpaceSetExamples, storyboardSpaceSetsFor, STORYBOARD_SPACE_SETS } from '../../src/lib/storyboardSpaceSetCatalog.js';
import { photoGalleryChoices, photoChoicePages, photoChoicePageForExample } from '../../src/lib/pageTemplates/storyboardPhotoChoices.js';
import * as gallery from '../../src/lib/pageTemplates/storyboardGallery.js';
import * as generation from '../../src/lib/generationExamples.js';
import * as identity from '../../src/lib/identityScope.js';
import { isSourceBasedDetailRecipe } from '../../src/lib/detailRecommendations.js';
import { hookHarness, loadStoryboardComponent, nodes, textOf } from './helpers/storyboardComponents.mjs';
import { storyboardPhotoPanelHarness } from './helpers/storyboardPhotoPanelHarness.mjs';

const catalogs = { ...withStoryboardSpaceSetExamples({ genExamples: raw }),
  shotTypes: [{ value: 'full' }, { value: 'medium' }], productShotTypes: [{ value: 'ghost' }, { value: 'detail' }] };
const choicesFor = (gender, extra = {}) => photoGalleryChoices(catalogs.genExamples,
  storyboardSpaceSetsFor({ gender, clothingType: 'top' }),
  { gender, clothingType: 'top', cutTypes: ['horizon'], studioStyle: 'horizon', shot: 'all', place: 'studio', ...extra });
const blockFor = (example, extra = {}) => ({ id: 'selected', source: 'ai', taxonomyVersion: 4,
  pageSectionKey: 'show.first', pageSectionKind: 'first', pageDesignId: 'P01-SHOW-01', sectionId: 'first',
  slotKey: 'first.hero', contentRole: 'hero', cutType: example.cutType, shot: example.shot,
  direction: example.direction, exampleId: example.id, ...extra });
const cardNodes = tree => nodes(tree).filter(node => node.props?.['data-photo-choice']);

async function photoHarness(props) {
  const h = hookHarness();
  const Gallery = await loadStoryboardComponent('StoryboardPhotoGallery.jsx', 'StoryboardPhotoGallery', {
    ...h.hooks, ...generation, ...identity, storyboardSpaceSetsFor, Icon: 'icon',
  });
  let current = { catalogs, clothingType: 'top', gender: 'men', cut: 'horizon', studioStyle: 'horizon', ...props };
  return { render(next = {}) { current = { ...current, ...next }; h.render(Gallery, current); return h.render(Gallery, current); }, close: h.close };
}

for (const [gender, before, after] of [['men', 16, 8], ['women', 17, 9]]) {
  test(`R4 ${gender} top studio gallery stacks same-shoot directions and preserves published location sets`, () => {
    const ungrouped = choicesFor(gender, { groupStudioShoots: false }).entries;
    const grouped = choicesFor(gender).entries;
    assert.equal(ungrouped.length, before); assert.equal(grouped.length, after);
    const front = `ex_horizon_${gender}_top_full_front_02`;
    const shoot = grouped.find(entry => entry.members?.some(member => member.id === front));
    assert.ok(shoot); assert.equal(shoot.kind, 'stack');
    assert.equal(shoot.members.length, 4);
    assert.ok(shoot.members.some(member => member.id === `ex_horizon_${gender}_top_full_back_02`));
    assert.ok(!shoot.members.some(member => member.id === `ex_horizon_${gender}_top_full_side_03`), 'shooting numbers remain distinct');
    assert.deepEqual(grouped.filter(entry => entry.set).map(entry => entry.id).sort(), ungrouped.filter(entry => entry.set).map(entry => entry.id).sort());
    const ids = entries => entries.flatMap(entry => entry.members || [entry.example]).map(example => example.id).sort();
    assert.deepEqual(ids(grouped), ids(ungrouped), 'grouping neither loses nor duplicates a selectable photo');
  });
}

test('R4 one shoot-key rule keeps garment, gender, framing, shooting number and named variants separate', () => {
  assert.equal(typeof gallery.studioExampleShootKey, 'function');
  const ex = (id, cutType = 'horizon', studioStyle = null) => ({ id, cutType, studioStyle });
  const key = id => gallery.studioExampleShootKey(ex(id));
  const base = key('ex_horizon_men_top_full_front_02');
  assert.ok(base);
  for (const direction of ['back', 'side', 'side_right', 'profile', 'profile_right']) {
    assert.equal(key(`ex_horizon_men_top_full_${direction}_02`), base);
  }
  for (const other of ['women_top_full_front_02', 'men_outer_full_front_02', 'men_top_medium_front_02',
    'men_top_full_front_03', 'men_top_full_front_satin_02', 'men_top_full_front_02_v2']) {
    assert.notEqual(key(`ex_horizon_${other}`), base);
  }
  assert.equal(key('ex_horizon_women_dress_medium_front_satin_01_v2'), key('ex_horizon_women_dress_medium_back_satin_01_v2'));
  assert.equal(gallery.studioExampleShootKey(ex('ex_styling_men_outer_full_front_room_08_v2', 'styling')), null);
  assert.ok(gallery.studioExampleShootKey(ex('ex_styling_men_outer_full_front_room_08_v2', 'styling', 'minimal')));
  for (const id of ['unknown', 'ss_horizon_zara_s02_01_v2', 'sig_men_01']) assert.equal(key(id), null);
});

test('R4 shoot cover is front unless the selected direction belongs to it; one-photo shoots stay ordinary', () => {
  const list = choicesFor('men').entries;
  const frontId = 'ex_horizon_men_top_full_front_02', backId = 'ex_horizon_men_top_full_back_02';
  const cover = list.find(entry => entry.members?.some(example => example.id === backId));
  assert.equal(cover?.example.id, frontId);
  const selected = choicesFor('men', { selectedId: backId }).entries.find(entry => entry.id === cover.id);
  assert.equal(selected.example.id, backId);
  const singletonId = 'ex_horizon_women_dress_medium_front_satin_01_v2';
  const singleton = choicesFor('women').entries.find(entry => entry.example.id === singletonId);
  assert.equal(singleton.kind, 'example'); assert.equal(singleton.members, undefined);
});

test('R4 minimal direction variants stack only in the studio picker while normal styling remains unchanged', () => {
  const template = raw.find(example => example.cutType === 'styling' && example.gender === 'men' && example.applicableClothingTypes.includes('outer'));
  const synthetic = ['back', 'front'].map(direction => ({ ...template,
    id: `ex_styling_men_outer_full_${direction}_room_08_v2`, direction, shot: 'full', studioStyle: 'minimal' }));
  const options = { gender: 'men', clothingType: 'outer', cutTypes: ['styling'], studioStyle: 'minimal', place: 'studio' };
  const minimal = photoGalleryChoices(synthetic, [], options).entries;
  assert.equal(minimal.length, 1); assert.equal(minimal[0].kind, 'stack'); assert.equal(minimal[0].example.direction, 'front');
  const styling = synthetic.map(({ studioStyle: _tag, ...example }) => example);
  const context = { ...options, studioStyle: null, place: 'cafe' };
  assert.deepEqual(photoGalleryChoices(styling, [], context).entries, photoGalleryChoices(styling, [], { ...context, groupStudioShoots: false }).entries);
  assert.ok(gallery.groupGalleryExamples(styling, [], { groupStudioShoots: true }).every(entry => entry.kind === 'example'));
});

test('R5 R4 shoot card opens in the top tray, dims other cards and saves the selected direction photo', async () => {
  const front = raw.find(example => example.id === 'ex_horizon_men_top_full_front_02');
  const back = raw.find(example => example.id === 'ex_horizon_men_top_full_back_02');
  const panel = await storyboardPhotoPanelHarness({ block: blockFor(front), catalogs, exampleGender: 'men', clothingType: 'top' });
  const guide = nodes(panel.render()).find(node => node.type === 'mood-guide');
  const h = await photoHarness(guide.props);
  try {
    let tree = h.render();
    const stack = cardNodes(tree).find(node => node.props['data-example-id'] === front.id);
    assert.equal(stack.props['aria-expanded'], true); assert.equal(stack.props['aria-pressed'], true);
    assert.doesNotMatch(textOf(stack), /\d+장/);
    stack.props.onClick(); tree = h.render();
    const open = nodes(tree).find(node => node.props?.['data-photo-tray']);
    assert.ok(open); assert.ok(cardNodes(tree).some(node => node.props.className.includes('dimmed')));
    const grid = nodes(tree).find(node => node.props?.className === 'photoGrid');
    assert.ok(nodes(tree).indexOf(open) < nodes(tree).indexOf(grid), 'R5 keeps the tray above the unchanged card grid');
    await nodes(open).find(node => node.props?.['data-example-id'] === back.id).props.onClick();
    assert.equal(panel.block.exampleId, back.id); assert.equal(panel.block.cutType, 'horizon');
    assert.equal(panel.block.direction, 'back'); assert.equal(panel.block.refScope, 'all');
    assert.ok(!panel.block.spaceGroupId, 'a display shoot stack is not a stored place-set instance');
  } finally { h.close(); panel.close(); }
});

test('R4 selected shoot member locates its page and checks the cover when the gallery opens', async () => {
  const back = raw.find(example => example.id === 'ex_horizon_men_top_full_back_02');
  const entries = choicesFor('men', { selectedId: back.id, projectId: 'round4-page' }).entries;
  const pages = photoChoicePages(entries);
  const expected = photoChoicePageForExample(pages, back.id);
  assert.equal(expected, pages.findIndex(page => page.some(entry => entry.members?.some(member => member.id === back.id))));
  const h = await photoHarness({ block: blockFor(back, { pageSectionKind: 'wear' }), exampleId: back.id, projectId: 'round4-page' });
  try {
    let tree = h.render();
    const initialPages = photoChoicePages(choicesFor('men', { selectedId: back.id, projectId: 'round4-page', shot: back.shot }).entries);
    assert.equal(nodes(tree).find(node => node.type === 'photo-pages').props.page, photoChoicePageForExample(initialPages, back.id));
    assert.equal(cardNodes(tree).find(node => node.props['data-example-id'] === back.id)?.props['aria-pressed'], true);
    nodes(tree).find(node => node.type === 'photo-framing').props.onChange('full'); tree = h.render();
    const cover = cardNodes(tree).find(node => node.props['data-example-id'] === back.id);
    assert.ok(cover); assert.equal(cover.props['aria-pressed'], true);
    assert.equal(nodes(tree).find(node => node.type === 'photo-pages').props.page, photoChoicePageForExample(initialPages, back.id));
  } finally { h.close(); }
  assert.ok(pages.every(page => page.length <= 6));
  const signature = { id: 'sig_men_01' };
  assert.ok(photoChoicePages(entries, signature).every(page => page[0].example === signature && page.length <= 6 && page.slice(1).length <= 5));
});

test('R4 editor shared grouping defaults retain separate flat cards', () => {
  const published = raw.filter(example => example.cutType === 'horizon' && example.gender === 'men' && example.applicableClothingTypes.includes('top'));
  assert.equal(gallery.groupGalleryExamples(published, []).length, published.length);
  assert.ok(gallery.groupGalleryExamples(published, []).every(entry => entry.kind === 'example'));
  assert.deepEqual(gallery.groupGalleryExamples(published, [], { setsOnly: true, groupStudioShoots: true }), []);
});

test('R4 actual editor horizon gallery keeps every standalone direction as an independently selectable card', async () => {
  const h = hookHarness(), chosen = [];
  const EditorGallery = await loadStoryboardComponent('StoryboardExampleGallery.jsx', 'MoodGuide', {
    ...h.hooks, ...generation, ...identity, isSourceBasedDetailRecipe, storyboardSpaceSetsFor: () => [],
    Icon: 'icon', ExampleThumb: 'example-thumb', ShotSegment: 'shot-types', MineImageTab: 'mine-images',
  });
  const published = raw.filter(example => example.cutType === 'horizon' && example.gender === 'men' && example.applicableClothingTypes.includes('top'));
  const props = { catalogs: { ...catalogs, genExamples: published }, cut: 'horizon', shot: 'full',
    clothingType: 'top', gender: 'men', gallerySeedFactory: () => 1, onExampleChange: id => chosen.push(id) };
  try {
    const tree = h.render(EditorGallery, props);
    const cards = nodes(tree).filter(node => node.type === 'button' && node.props.className?.startsWith('sb-excell '));
    assert.equal(cards.length, published.length);
    assert.ok(cards.every(node => node.props['aria-expanded'] === undefined && !node.props['data-storyboard-stack']));
    for (const card of cards) card.props.onClick({});
    assert.deepEqual(chosen.sort(), published.map(example => example.id).sort());
  } finally { h.close(); }
});

test('R4 released styling galleries retain their original entry structure for every product condition', () => {
  for (const gender of ['men', 'women']) for (const clothingType of ['top', 'bottom', 'outer', 'dress']) {
    for (const place of ['cafe', 'home', 'indoor', 'park']) {
      const sets = storyboardSpaceSetsFor({ gender, clothingType });
      const options = { gender, clothingType, place, cutTypes: ['styling'], shot: 'all' };
      assert.deepEqual(photoGalleryChoices(catalogs.genExamples, sets, options).entries,
        photoGalleryChoices(catalogs.genExamples, sets, { ...options, groupStudioShoots: false }).entries);
    }
  }
});
