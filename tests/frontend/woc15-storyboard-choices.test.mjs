import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import raw from '../../src/data/genExamples.json' with { type: 'json' };
import detailRecommendations from '../../src/mock/detailRecommendationsFixture.json' with { type: 'json' };
import { createLateDetailCatalog } from '../../src/features/storyboard/storyboardLateDetails.js';
import { seedStoryboard } from '../../src/lib/pageTemplates/seed.js';
import { defaultPageStyle } from '../../src/lib/pageTemplates/pageStyle.js';
import { pageTemplatesCatalog } from '../../src/lib/pageTemplates/catalog.js';
import { sectionRuns } from '../../src/lib/pageTemplates/sectionRuns.js';
import { addTemplateSection } from '../../src/lib/pageTemplates/sectionActions.js';
import { sectionDesignAxes } from '../../src/lib/pageTemplates/designAxes.js';
import { sectionDesignChoices, defaultSectionDesignId, currentSectionDesignId } from '../../src/lib/pageTemplates/designChoices.js';
import { sectionDesignPreviewSample } from '../../src/lib/pageTemplates/designPreview.js';
import { templateSectionName } from '../../src/lib/pageTemplates/slots.js';
import { removeStoryboardPhoto } from '../../src/lib/pageTemplates/photoActions.js';
import { assemblePage } from '../../src/lib/pageTemplates/assemble.js';
import { photoGalleryChoices, initialPhotoPlace } from '../../src/lib/pageTemplates/storyboardPhotoChoices.js';
import { generationExampleImageSources } from '../../src/lib/generationExamples.js';
import { storyboardSpaceSetsFor, withStoryboardSpaceSetExamples } from '../../src/lib/storyboardSpaceSetCatalog.js';
import { filterSpaceSetsForModel } from '../../src/lib/identityScope.js';
import { hookHarness, loadStoryboardComponent, nodes } from './helpers/storyboardComponents.mjs';

const product = { clothingType: 'top', colors: [{ id: 'base', isBase: true, images: [{ slot: 'Front', src: '/front.jpg' }] }] };
const analysis = { targetGenders: ['women'], pageStyle: defaultPageStyle(), detailRecommendations: { status: 'unavailable', candidates: [] } };
const catalog = withStoryboardSpaceSetExamples({ genExamples: raw }).genExamples;
const daily = blocks => sectionRuns(blocks).find(run => run.usage === 'styling');

test('WOC15 late fabric recommendations enter the section picker and seed without replacing edited blocks', async () => {
  const late = createLateDetailCatalog(), h = hookHarness();
  const blocks = seedStoryboard(product, analysis);
  blocks[0] = { ...blocks[0], title: '셀러가 고친 제목' };
  const before = structuredClone(blocks);
  let catalogs = { detailProduct: product, analysis, detailRecommendations: analysis.detailRecommendations, genExamples: catalog };
  const Picker = await loadStoryboardComponent('StoryboardSectionPicker.jsx', 'StoryboardSectionPicker', {
    ...h.hooks, pageTemplatesCatalog, sectionRuns, addTemplateSection, sectionDesignChoices, defaultSectionDesignId,
    currentSectionDesignId, sectionDesignPreviewSample, templateSectionName, StoryboardPreview: 'preview', NEW_SECTION_DRAG_MIME: 'section',
  });
  let added;
  const render = () => h.render(Picker, { mode: 'add', blocks, catalogs, gender: 'women', onChoose: (kind, usage, designId) => {
    added = addTemplateSection(blocks, product, catalogs.analysis, kind, usage, { designId });
  } });
  const fabricButton = tree => nodes(tree).find(node => node.type === 'button' && node.props['data-default-design'] === 'T03-D01');
  try {
    assert.equal(fabricButton(render()), undefined);
    late.remember('project', detailRecommendations);
    catalogs = late.merge('project', catalogs);
    const button = fabricButton(render());
    assert.ok(button, 'the new fabric section is available after the recommendation arrives');
    button.props.onClick({ currentTarget: { querySelector: () => null } });
    const fabric = added.filter(block => block.pageSectionKind === 'fabric' && block.source === 'ai');
    assert.equal(fabric.length, 1);
    assert.equal(fabric[0].detailTargetId, 'dt1_085eb2ee8b92884946934942');
    assert.equal(fabric[0].direction, 'front');
    assert.deepEqual(blocks, before);
    assert.deepEqual(added.filter(block => before.some(old => old.id === block.id)), before);
    assert.equal(catalogs.analysis.pageStyle, analysis.pageStyle);
    assert.equal(analysis.detailRecommendations.status, 'unavailable');
  } finally { h.close(); }
});

test('WOC15 every applicable styling example is reachable through a visible place tab', () => {
  for (const gender of ['women', 'men']) for (const clothingType of ['top', 'bottom', 'outer', 'dress']) {
    if (gender === 'men' && clothingType === 'dress') continue;
    const sets = storyboardSpaceSetsFor({ gender, clothingType }), options = { gender, clothingType, shot: 'all' };
    const all = photoGalleryChoices(catalog, sets, options);
    const visibleIds = new Set(all.tabs.flatMap(tab => photoGalleryChoices(catalog, sets, { ...options, place: tab.value }).entries)
      .flatMap(entry => entry.members || [entry.example]).map(example => example.id));
    assert.deepEqual(all.candidates.filter(example => !visibleIds.has(example.id)).map(example => example.id), [], `${gender}:${clothingType}`);
  }
});

test('WOC15 styling studio examples resolve to indoor while horizon stays in studio', () => {
  for (const [id, gender, clothingType] of [
    ['ex_styling_men_outer_full_06', 'men', 'outer'],
    ['ex_styling_men_outer_full_08', 'men', 'outer'],
    ['ex_styling_women_dress_medium_home_08', 'women', 'dress'],
  ]) {
    const example = catalog.find(item => item.id === id);
    assert.ok(example, id);
    assert.equal(initialPhotoPlace(example), 'indoor', id);
    const choices = photoGalleryChoices(catalog, storyboardSpaceSetsFor({ gender, clothingType }), { gender, clothingType, place: 'indoor' });
    assert.ok(choices.entries.flatMap(entry => entry.members || [entry.example]).some(item => item.id === id), id);
  }
  const horizon = photoGalleryChoices(catalog, storyboardSpaceSetsFor({ gender: 'women', clothingType: 'top' }),
    { cutTypes: ['horizon'], gender: 'women', clothingType: 'top', place: 'studio' });
  assert.equal(horizon.place, 'studio');
  assert.ok(horizon.entries.length);
  assert.ok(horizon.entries.flatMap(entry => entry.members || [entry.example]).every(example => example.cutType === 'horizon'));
});

const sample = raw.find(example => example.cutType === 'styling' && example.gender === 'women' && example.shot === 'medium');
const galleryExamples = Array.from({ length: 20 }, (_, i) => ({ ...sample, id: `woc15-${String(i).padStart(2, '0')}`,
  rank: i, placeType: i < 13 ? 'cafe-shop-interior' : 'home-interior', applicableClothingTypes: ['top'], shot: 'medium' }));
async function galleryHarness({ exampleId = 'woc15-08', sets = [] } = {}) {
  const h = hookHarness(), selected = [];
  const Gallery = await loadStoryboardComponent('StoryboardPhotoGallery.jsx', 'StoryboardPhotoGallery', {
    ...h.hooks, storyboardSpaceSetsFor: () => sets, filterSpaceSetsForModel, generationExampleImageSources, Icon: 'icon',
  });
  let props = { block: { id: 'photo', pageSectionKind: 'wear' }, catalogs: { genExamples: galleryExamples },
    cut: 'styling', clothingType: 'top', gender: 'women', exampleId, onExampleChange: id => selected.push(id) };
  return { selected, close: h.close, render(next = {}) { props = { ...props, ...next }; return h.render(Gallery, props); } };
}
const pager = tree => nodes(tree).find(node => node.type === 'photo-pages');
const filters = tree => nodes(tree).find(node => node.type === 'photo-filters');

test('WOC15 opening and reopening a selected second-page example keeps it visible without changing the selection', async () => {
  for (let opened = 0; opened < 2; opened++) {
    const h = await galleryHarness();
    try {
      let tree = h.render();
      assert.equal(pager(tree).props.page, 1);
      assert.ok(nodes(tree).some(node => node.props?.['data-example-id'] === 'woc15-08' && node.props['aria-pressed']));
      pager(tree).props.onChange(2); tree = h.render();
      assert.equal(pager(tree).props.page, 2);
      assert.deepEqual(h.selected, []);
    } finally { h.close(); }
  }
});

test('WOC15 a selected location group opens on its page and filters without it reset to page one', async () => {
  const h = await galleryHarness({ exampleId: 'woc15-09', sets: [{ id: 'woc15-place', members: [{ exampleId: 'woc15-08' }, { exampleId: 'woc15-09' }] }] });
  try {
    let tree = h.render();
    assert.equal(pager(tree).props.page, 1);
    assert.ok(nodes(tree).some(node => node.props?.['data-photo-choice'] === 'woc15-place' && node.props['aria-pressed']));
    filters(tree).props.onPlace('home'); h.render(); tree = h.render();
    assert.equal(pager(tree).props.page, 0);
    assert.equal(nodes(tree).some(node => node.props?.['aria-pressed'] === true), false);
    filters(tree).props.onPlace('cafe'); h.render(); tree = h.render();
    assert.equal(pager(tree).props.page, 1);
    assert.deepEqual(h.selected, []);
  } finally { h.close(); }
});

function wearBoard(designId) {
  const blocks = seedStoryboard(product, analysis), run = daily(blocks);
  const photos = run.blocks.slice(0, 2).map(block => ({ ...block, pageDesignId: designId }));
  return [...blocks.slice(0, run.start), ...photos, ...blocks.slice(run.end)];
}
function removalHarness(initial) {
  const source = readFileSync(new URL('../../src/features/storyboard/Storyboard.jsx', import.meta.url), 'utf8');
  const slice = (start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
  let state = initial;
  const undoEntryRef = { current: null }, scope = { blocks: initial, removeStoryboardPhoto, atomicSavingRef: { current: false },
    catalogs: { detailProduct: product }, undoEntryRef, setBlocks: value => { state = value; }, setUndoExiting() {}, setUndoEntry() {},
    scheduleUndoDismiss() {}, dismissUndo: () => { undoEntryRef.current = null; }, setPicker: null, setInsertedSectionKey() {},
    selectedId: initial[0].id, closeInspector() {}, toast: { push() {} } };
  const handlers = new Function('scope', `with (scope) {
    ${slice('  const showUndo =', '  const clearUndoForSnapshot =')}
    ${slice('  const undoLatest =', '  const enterUndo =')}
    ${slice('  const remove =', '  const addOptionalPhoto =')}
    return { remove, undoLatest };
  }`)(scope);
  return { get blocks() { return state; }, remove(id) { scope.blocks = state; handlers.remove(id); }, undo() { scope.blocks = state; handlers.undoLatest(); } };
}

for (const designId of ['WEAR-STYLING-2COL-01', 'WEAR-STYLING-FRAME-COLUMN-02']) {
  test(`WOC15 deleting down to one photo gives valid design choices and undo restores ${designId}`, () => {
    const initial = wearBoard(designId), before = structuredClone(initial), h = removalHarness(initial);
    h.remove(daily(initial).blocks[1].id);
    const run = daily(h.blocks), value = sectionDesignAxes(run, { storyboard: h.blocks, product });
    assert.equal(run.blocks.length, 1);
    assert.equal(value.currentLayoutId, 'column');
    assert.equal(value.currentDecorationId, designId.includes('FRAME') ? 'frame' : 'basic');
    assert.deepEqual(value.decorations.map(option => option.id), ['basic', 'frame']);
    const validIds = new Set(sectionDesignChoices(run, { storyboard: h.blocks, product }).map(design => design.id));
    assert.ok([...value.layouts, ...value.decorations].every(option => validIds.has(option.designId)));
    const assembled = assemblePage({ storyboard: h.blocks, product, analysis }).filter(block => block.pageSectionKey === run.key);
    const photos = assembled.flatMap(block => block.elements).filter(element => element.type === 'image' && !element.blur);
    assert.equal(photos.length, 1);
    assert.equal(photos[0].w, designId.includes('FRAME') ? 560 : 888);
    h.undo(); assert.deepEqual(h.blocks, before); assert.deepEqual(initial, before);
  });
}
