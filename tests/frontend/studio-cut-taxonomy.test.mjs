import test from 'node:test';
import assert from 'node:assert/strict';
import examples from '../../src/data/genExamples.json' with { type: 'json' };
import setsRelease from '../../src/data/storyboardSpaceSets.json' with { type: 'json' };
import serverExamples from '../../server/app/data/example_assets.json' with { type: 'json' };
import serverSets from '../../server/app/data/space_set_assets.json' with { type: 'json' };
import tier1 from '../fixtures/minimal-studio-tier1-20261106.json' with { type: 'json' };
import { cutTypeDisplay, isSupportedCutType, assignInternalContentRoles, allowedCutTypeOptionsForSection } from '../../src/lib/storyboardTaxonomy.js';
import { isMinimalExample, studioStyleForSet } from '../../src/lib/studioExamples.js';
import { normalizeStoryboardSpaceSetRelease, STORYBOARD_SPACE_SETS, STORYBOARD_SPACE_SET_EXAMPLES, withStoryboardSpaceSetExamples } from '../../src/lib/storyboardSpaceSetCatalog.js';
import { selectGenerationExamples, assignGenerationExamples } from '../../src/lib/generationExamples.js';
import { cutDisplaySelection, studioStyleOptions } from '../../src/lib/cutDisplaySelection.js';
import { photoGalleryChoices } from '../../src/lib/pageTemplates/storyboardPhotoChoices.js';
import { seedStoryboard, resolveStoryboardEntry } from '../../src/lib/pageTemplates/seed.js';
import { pickEntrySets } from '../../src/lib/storyboardEntryPlacement.js';
import { getTemplate } from '../../src/lib/pageTemplates/catalog.js';
import { photoSlotForBlock, slotAllowsCutType, wearUsageForBlock } from '../../src/lib/pageTemplates/slots.js';
import { storyboardPhotoPanelHarness } from './helpers/storyboardPhotoPanelHarness.mjs';
import { nodes, textOf, hookHarness, loadStoryboardComponent } from './helpers/storyboardComponents.mjs';
import { filterSpaceSetsForModel } from '../../src/lib/identityScope.js';
import { storyboardSpaceSetsFor } from '../../src/lib/storyboardSpaceSetCatalog.js';
import { generationExampleImageSources } from '../../src/lib/generationExamples.js';

const catalog = [...examples, ...STORYBOARD_SPACE_SET_EXAMPLES];
const minimal = examples.find(isMinimalExample);
const options = { catalog, shots: ['full', 'medium'], clothingType: 'outer', gender: 'men' };
const showSlots = getTemplate('show').sections.filter(section => section.kind === 'wear')
  .flatMap(section => section.photoSlots.map(slot => ({ section, slot })));
const blockFor = ({ section, slot }, patch = {}) => ({
  id: 'old-photo', taxonomyVersion: 4, source: 'ai', sectionId: section.instanceKey,
  pageSectionKey: `show.${section.instanceKey}`, pageSectionKind: 'wear', pageSectionUsage: 'fit',
  pageDesignId: section.designId, slotKey: `${section.instanceKey}.${slot.slotKey}`,
  cutType: 'styling', shot: 'full', direction: 'front', exampleId: minimal.id, ...patch,
});

test('display classification uses only stored cutType and the selected example tag', () => {
  for (const [cut, id, label, child] of [
    ['styling', null, '스타일링컷', null], ['styling', 'unknown', '스타일링컷', null],
    ['styling', minimal.id, '스튜디오컷', '미니멀컷'], ['horizon', null, '스튜디오컷', '호리존컷'],
    ['horizon', minimal.id, '스튜디오컷', '호리존컷'], ['product', minimal.id, '제품컷', null],
  ]) {
    const display = cutTypeDisplay(cut, id);
    assert.equal(display.label, label); assert.equal(display.studioLabel, child);
  }
  assert.equal(cutTypeDisplay('styling', minimal.id, [{ ...minimal, studioStyle: undefined }]).value, 'styling');
  assert.equal(cutTypeDisplay(null).label, null);
  for (const cut of ['studio', 'minimal', 'daily']) assert.equal(isSupportedCutType(cut), false);
});

test('only exact tier1 IDs are tagged and frontend/server examples and members agree', () => {
  const expected = tier1.flatMap(place => place.photos).filter(photo => photo.status === 'live').map(photo => photo.id).sort();
  const front = [...examples, ...setsRelease.sets.flatMap(set => set.members.map(member => ({ ...member, id: member.exampleId })))];
  const back = [...Object.entries(serverExamples.assets).map(([id, entry]) => ({ ...entry, id })),
    ...serverSets.sets.flatMap(set => set.members.map(member => ({ ...member, id: member.exampleId })))];
  const ids = entries => entries.filter(isMinimalExample).map(entry => entry.id).sort();
  assert.deepEqual(ids(front), expected); assert.deepEqual(ids(back), expected);
  assert.equal(expected.length, 16);
  const serverById = new Map(back.map(entry => [entry.id, entry]));
  for (const entry of front) assert.equal(entry.studioStyle, serverById.get(entry.id)?.studioStyle, entry.id);
  assert.equal(STORYBOARD_SPACE_SETS.filter(set => set.studioStyle === 'minimal').length, 3);
});

test('mixed minimal sets fail release validation and all-minimal sets retain the tag', () => {
  assert.throws(() => studioStyleForSet([minimal, { cutType: 'styling' }]), /mixed_studio_style_set/);
  const mixed = structuredClone(setsRelease);
  const set = mixed.sets.find(set => set.members.some(isMinimalExample));
  delete set.members[0].studioStyle;
  assert.throws(() => normalizeStoryboardSpaceSetRelease(mixed), /mixed_studio_style_set/);
  for (const set of STORYBOARD_SPACE_SETS.filter(set => set.studioStyle === 'minimal')) assert.ok(set.members.every(isMinimalExample));
});

test('both gallery selectors partition minimal and ordinary styling for every gender and garment', () => {
  for (const gender of ['men', 'women']) for (const clothingType of ['top', 'bottom', 'outer', 'dress']) {
    const sets = STORYBOARD_SPACE_SETS.filter(set => set.gender === gender && set.applicableClothingTypes.includes(clothingType));
    for (const studioStyle of [null, 'minimal']) {
      const context = { cutType: 'styling', gender, clothingType, studioStyle, includeSetOnly: true, limit: catalog.length };
      const selected = ['full', 'medium'].flatMap(shot => selectGenerationExamples(catalog, { ...context, shot }));
      assert.ok(selected.every(example => isMinimalExample(example) === (studioStyle === 'minimal')));
      const choices = photoGalleryChoices(catalog, sets, { ...context, cutTypes: ['styling'], place: 'all', shot: 'all' });
      assert.ok(choices.candidates.every(example => isMinimalExample(example) === (studioStyle === 'minimal')));
      assert.ok(choices.entries.every(entry => !entry.set || (entry.set.studioStyle === 'minimal') === (studioStyle === 'minimal')));
    }
  }
});

test('styling automatic examples, entry sets, and template seeds exclude minimal', () => {
  for (const gender of ['men', 'women']) for (const clothingType of ['top', 'bottom', 'outer', 'dress']) {
    const assigned = assignGenerationExamples(['full', 'medium'].map(shot => ({ id: shot, source: 'ai', cutType: 'styling', shot })),
      { catalog, product: { clothingType }, gender }).blocks;
    assert.ok(assigned.every(block => !isMinimalExample(catalog.find(example => example.id === block.exampleId))));
    for (const projectId of ['p1', 'p2', 'p3']) {
      assert.ok(pickEntrySets({ gender, clothingType, projectId, stylingCount: 3 }).stylingSets
        .every(set => !set || set.studioStyle !== 'minimal'));
      const seeded = seedStoryboard({ clothingType }, { targetGenders: [gender] }, { projectId });
      assert.ok(seeded.every(block => !isMinimalExample(catalog.find(example => example.id === block.exampleId))));
    }
  }
});

test('switches clear incompatible examples even when cutType stays styling; minimal chooses immediately', () => {
  const normal = catalog.find(example => example.cutType === 'styling' && !isMinimalExample(example) && example.gender === 'men');
  const original = { cutType: 'styling', exampleId: normal.id, shot: 'full' };
  const min = cutDisplaySelection(original, { value: 'horizon', studioStyle: 'minimal' }, options);
  assert.equal(min.cutType, 'styling'); assert.ok(isMinimalExample(catalog.find(example => example.id === min.exampleId)));
  assert.deepEqual(min, cutDisplaySelection(original, { value: 'horizon', studioStyle: 'minimal' }, options));
  const styling = cutDisplaySelection(min, { value: 'styling' }, options);
  assert.equal(styling.cutType, 'styling'); assert.equal(styling.exampleId, null);
  const horizon = cutDisplaySelection(min, { value: 'horizon' }, options);
  assert.equal(horizon.cutType, 'horizon'); assert.equal(horizon.exampleId, null);
  assert.ok(cutDisplaySelection(horizon, { value: 'horizon', studioStyle: 'minimal' }, options).exampleId);
});

test('minimal is disabled without eligible examples; set-only examples count and no empty minimal can be selected', () => {
  const context = { ...options, gender: 'women', clothingType: 'outer' };
  assert.equal(studioStyleOptions([], context)[0].disabled, true);
  assert.equal(studioStyleOptions(catalog, { ...options, gender: 'men', clothingType: 'dress' })[0].disabled, true);
  assert.equal(cutDisplaySelection({}, { value: 'horizon', studioStyle: 'minimal' }, { ...options, catalog: [] }), null);
  assert.equal(studioStyleOptions(STORYBOARD_SPACE_SET_EXAMPLES, { ...options, clothingType: 'top' })[0].disabled, false);
  assert.equal(studioStyleOptions([], context)[0].disabledReason, '이 조건은 아직 서비스에 공개되지 않았어요');
});

test('all show horizon-only slots accept minimal, reject ordinary styling and retain the slot purpose', () => {
  assert.deepEqual(showSlots.map(({ slot }) => slot.slotKey), ['g', 'front', 'side', 'back']);
  for (const item of showSlots) {
    const block = blockFor(item);
    const recipe = photoSlotForBlock(block).recipe;
    assert.deepEqual(recipe.allowedCutTypes, ['horizon']);
    assert.equal(slotAllowsCutType(recipe, block), true);
    assert.deepEqual(allowedCutTypeOptionsForSection(block).map(option => option.label), ['스튜디오컷']);
    const [result] = assignInternalContentRoles([block]);
    assert.equal(result.cutType, 'styling'); assert.equal(result.exampleId, minimal.id);
    assert.equal(result.contentRole, item.slot.contentRole); assert.equal(wearUsageForBlock(result), 'fit');
    for (const exampleId of [null, 'unknown', examples.find(example => example.cutType === 'styling' && !isMinimalExample(example)).id]) {
      const [reverted] = assignInternalContentRoles([blockFor(item, { exampleId, studioStyle: 'minimal' })]);
      assert.equal(reverted.cutType, 'horizon'); assert.equal(reverted.exampleId, null);
    }
  }
  assert.equal(slotAllowsCutType({ allowedCutTypes: ['styling'] }, { cutType: 'styling', exampleId: minimal.id }), true);
});

test('old saved minimal, horizon and empty blocks open without migration or new fields', () => {
  for (const patch of [{}, { cutType: 'horizon', exampleId: null }, { cutType: 'horizon', exampleId: undefined }]) {
    const old = blockFor(showSlots[1], patch);
    const opened = resolveStoryboardEntry([old], { clothingType: 'outer' }, { targetGenders: ['men'] });
    assert.equal(opened.notice, null);
    const [normalized] = assignInternalContentRoles(opened.blocks);
    assert.equal(normalized.cutType, old.cutType);
    assert.equal(normalized.exampleId, old.exampleId);
    assert.equal(cutTypeDisplay(normalized.cutType, normalized.exampleId).studioStyle, old.cutType === 'styling' ? 'minimal' : 'horizon');
    assert.equal(Object.hasOwn(normalized, 'studioStyle'), false);
  }
  const hydrated = withStoryboardSpaceSetExamples({ genExamples: examples });
  assert.equal(hydrated.genExamples.filter(isMinimalExample).length, 16);
});

const panelCatalogs = { genExamples: catalog, shotTypes: [{ value: 'full' }, { value: 'medium' }],
  productShotTypes: [{ value: 'ghost' }, { value: 'detail' }] };

test('R3 showroom photo-panel browsing waits for a photo before saving minimal or horizon', async () => {
  const h = await storyboardPhotoPanelHarness({ block: blockFor(showSlots[1], { cutType: 'horizon', exampleId: null }),
    catalogs: panelCatalogs, clothingType: 'outer', exampleGender: 'men' });
  try {
    let tree = h.render();
    const chips = nodes(tree).find(node => node.type === 'mood-guide');
    assert.equal(chips.props.studioStyle, 'horizon');
    chips.props.onStudioStyleChange('minimal');
    await Promise.resolve();
    assert.equal(h.changes.length, 0);
    const pendingGuide = nodes(h.render()).find(node => node.type === 'mood-guide');
    await pendingGuide.props.onExampleChange(minimal.id);
    assert.equal(h.changes.length, 1);
    assert.equal(h.block.cutType, 'styling');
    assert.ok(isMinimalExample(catalog.find(example => example.id === h.block.exampleId)));
    assert.equal(h.block.contentRole, 'fit');
    assert.equal(Object.hasOwn(h.block, 'studioStyle'), false);
    tree = h.render();
    assert.equal(nodes(tree).find(node => node.type === 'mood-guide').props.studioStyle, 'minimal');
    nodes(tree).find(node => node.type === 'mood-guide').props.onStudioStyleChange('horizon');
    tree = h.render();
    const guide = nodes(tree).find(node => node.type === 'mood-guide');
    assert.equal(guide.props.cut, 'horizon'); assert.equal(guide.props.exampleId, null);
    await guide.props.onExampleChange(catalog.find(example => example.cutType === 'horizon' && example.gender === 'men').id);
    assert.equal(h.block.cutType, 'horizon');
  } finally { h.close(); }
});

test('R3 styling-tab browsing preserves minimal until an ordinary photo is selected', async () => {
  const block = { ...blockFor(showSlots[0]), pageSectionUsage: 'styling' };
  const h = await storyboardPhotoPanelHarness({ block, catalogs: panelCatalogs, clothingType: 'outer', exampleGender: 'men' });
  try {
    const tabs = nodes(h.render()).find(node => node.type === 'underline-tabs');
    assert.equal(tabs.props.value, 'horizon'); tabs.props.onChange('styling');
    await Promise.resolve();
    assert.equal(h.changes.length, 0); assert.equal(h.block.exampleId, minimal.id);
    const next = catalog.find(example => example.cutType === 'styling' && !isMinimalExample(example) && example.gender === 'men' && example.applicableClothingTypes.includes('outer'));
    await nodes(h.render()).find(node => node.type === 'mood-guide').props.onExampleChange(next.id);
    assert.equal(h.block.cutType, 'styling'); assert.equal(h.block.exampleId, next.id);
    assert.equal(cutTypeDisplay(h.block.cutType, h.block.exampleId).label, '스타일링컷');
  } finally { h.close(); }
});

test('R3 minimal photo gallery begins with a signature followed by minimal cards', async () => {
  const h = hookHarness();
  const Gallery = await loadStoryboardComponent('StoryboardPhotoGallery.jsx', 'StoryboardPhotoGallery', {
    ...h.hooks, filterSpaceSetsForModel, storyboardSpaceSetsFor, generationExampleImageSources, Icon: 'icon',
  });
  const props = { block: { id: 'first', pageSectionKind: 'first' }, catalogs: panelCatalogs, cut: 'styling',
    shot: 'full', clothingType: 'outer', gender: 'men', exampleId: minimal.id,
    signature: { id: 'sig_test', cutType: 'horizon' } };
  try {
    h.render(Gallery, props);
    const tree = h.render(Gallery, props);
    const cards = nodes(tree).filter(node => node.props?.['data-example-id']);
    assert.ok(cards.length);
    assert.equal(cards[0].props['data-example-id'], 'sig_test');
    assert.ok(cards.slice(1).every(node => isMinimalExample(catalog.find(example => example.id === node.props['data-example-id']))));
    assert.ok(textOf(tree).includes('시그니처'));
  } finally { h.close(); }
});

test('a single-photo horizon gallery remains reachable after selecting the studio category', async () => {
  const h = hookHarness();
  const Gallery = await loadStoryboardComponent('StoryboardPhotoGallery.jsx', 'StoryboardPhotoGallery', {
    ...h.hooks, filterSpaceSetsForModel, storyboardSpaceSetsFor, generationExampleImageSources, Icon: 'icon',
  });
  const selected = catalog.find(example => example.cutType === 'horizon' && example.gender === 'men');
  const props = { block: { id: 'first', pageSectionKind: 'first' }, catalogs: panelCatalogs, cut: 'horizon',
    shot: selected.shot, clothingType: 'outer', gender: 'men', exampleId: selected.id };
  try {
    h.render(Gallery, props);
    const tree = h.render(Gallery, props);
    const cards = nodes(tree).filter(node => node.props?.['data-example-id']);
    assert.ok(cards.length);
    assert.ok(cards.every(node => catalog.find(example => example.id === node.props['data-example-id'])?.cutType === 'horizon'));
  } finally { h.close(); }
});
