import test, { before } from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { pageTemplatesCatalog, getTemplate } from '../../src/lib/pageTemplates/catalog.js';
import { seedStoryboard } from '../../src/lib/pageTemplates/seed.js';
import { defaultPageStyle } from '../../src/lib/pageTemplates/pageStyle.js';
import { sectionRuns, visibleSectionRuns, moveTemplateSection, isTextOnlySection } from '../../src/lib/pageTemplates/sectionRuns.js';
import { sectionDesignPreviewSample } from '../../src/lib/pageTemplates/designPreview.js';
import { addTemplateSection, changeTemplateSectionDesign } from '../../src/lib/pageTemplates/sectionActions.js';
import { templateSectionName } from '../../src/lib/pageTemplates/slots.js';
import { assemblePage, wrapText } from '../../src/lib/pageTemplates/assemble.js';
import { assignGenerationExamples } from '../../src/lib/generationExamples.js';
import { canPlanSimilarCut } from '../../src/lib/storyboardSimilarCuts.js';
import { rejectionOfBlock } from '../../src/lib/identityScope.js';
import { packStageRows } from '../../src/lib/pageTemplates/storyboardStage.js';
import { examplePlace } from '../../src/lib/pageTemplates/storyboardGallery.js';
import { reuseSlotsForBlock, reuseSourceForSlot } from '../../src/lib/pageTemplates/sectionDependencies.js';
import { inferStoryboardSpaceSet } from '../../src/lib/storyboardSpaceSetCatalog.js';
import { spaceSetDisplayName } from '../../src/lib/spaceSetDisplayNames.js';
import { sectionDesignChoices, defaultSectionDesignId, currentSectionDesignId } from '../../src/lib/pageTemplates/designChoices.js';
import { storyboardPreviewCuts, storyboardPreviewSource } from '../../src/features/storyboard/storyboardPreview.js';
import { CATALOG_DRAG_MIME } from '../../src/lib/storyboardCatalogDrag.js';
import * as generation from '../../src/lib/generationExamples.js';
import { filterExamplesForModel, filterSpaceSetsForModel } from '../../src/lib/identityScope.js';
import { storyboardSpaceSetsFor, withStoryboardSpaceSetExamples } from '../../src/lib/storyboardSpaceSetCatalog.js';
import { filterGalleryExamples, visiblePlaceTabs } from '../../src/lib/pageTemplates/storyboardGallery.js';
import { isSourceBasedDetailRecipe } from '../../src/lib/detailRecommendations.js';
import rawExamples from '../../src/data/genExamples.json' with { type: 'json' };
import { loadStoryboardComponent, hookHarness, nodes, textOf } from './helpers/storyboardComponents.mjs';

const product = { clothingType: 'top', colors: [{ id: 'base', isBase: true }] };
const analysis = templateId => ({ targetGenders: ['women'], pageStyle: { ...defaultPageStyle(), templateId }, mdComment: '가볍게 입어요' });
const catalogsFor = templateId => ({ detailProduct: product, analysis: analysis(templateId), genExamples: [] });
const Icon = props => React.createElement('svg', { 'data-icon': props.name });
let Preview, Stage;
before(async () => {
  Preview = await loadStoryboardComponent('StoryboardPreview.jsx', 'StoryboardPreview', { wrapText });
  Stage = await loadStoryboardComponent('StoryboardStage.jsx', 'StoryboardStage', { Icon, packStageRows, templateSectionName,
    reuseSlotsForBlock, reuseSourceForSlot, inferStoryboardSpaceSet, spaceSetDisplayName, isTextOnlySection, examplePlace, canPlanSimilarCut, rejectionOfBlock,
    storyboardPreviewSource, StoryboardPreview: Preview });
});
const pageFor = (blocks, catalogs) => assemblePage({ storyboard: blocks, product, analysis: catalogs.analysis, cutResults: storyboardPreviewCuts(blocks, catalogs) });

test('actual assembler previews keep the real page height and chosen photo URLs', () => {
  const blocks = seedStoryboard(product, analysis('emo')), catalogs = catalogsFor('emo');
  blocks[0] = { ...blocks[0], thumb: 'https://images.test/current.webp' };
  const page = pageFor(blocks, catalogs).filter(block => block.pageSectionKey === blocks[0].pageSectionKey);
  const markup = renderToStaticMarkup(React.createElement(Preview, { blocks: page }));
  assert.ok(markup.includes(`viewBox="0 0 1000 ${page.reduce((n, b) => n + b.h, 0)}"`));
  assert.ok(markup.includes('href='));
  assert.ok(markup.includes(page[0].elements.find(el => el.type === 'image').src.replaceAll('&', '&amp;')));
  assert.doesNotMatch(markup, /\sstyle=/);
});

test('preview text uses faint lines at the assembler line height without sample words', () => {
  const markup = renderToStaticMarkup(React.createElement(Preview, { blocks: [{ id: 'b', h: 100, bg: 'white', elements: [
    { id: 'copy', type: 'text', x: 0, y: 0, w: 40, h: 50, text: 'first\nsecond', style: { size: 10, lineHeight: 14 } },
  ] }] }));
  assert.match(markup, /y="17.5"/); assert.doesNotMatch(markup, /first|second|<tspan|<text/);
});

test('outline hides text-only runs, numbers visible sections and protects the opening', async () => {
  const h = hookHarness(), blocks = seedStoryboard(product, analysis('emo')), catalogs = catalogsFor('emo');
  const List = await loadStoryboardComponent('StoryboardPageList.jsx', 'StoryboardPageList', { ...h.hooks, Icon, templateSectionName,
    StoryboardPreview: Preview, reuseSlotsForBlock, CATALOG_DRAG_MIME });
  const runs = visibleSectionRuns(blocks), calls = [], props = { runs, page: pageFor(blocks, catalogs), activeKey: runs[0].key,
    onSelect: key => calls.push(key), onAdd() {}, onRemove() {}, onMove() {}, onOverview() {}, sectionRefs: { current: new Map() } };
  const tree = h.render(List, props), all = nodes(tree);
  assert.equal(all.filter(node => node.props?.['data-section-key']).length, runs.length);
  assert.equal(all.some(node => textOf(node) === '상품 소개' || textOf(node) === '추천 대상'), false);
  assert.equal(all.filter(node => node.props?.['aria-label']?.endsWith('섹션 빼기')).length, runs.length - 1);
  assert.equal(all.some(node => node.props?.['aria-label'] === '첫 장 섹션 빼기'), false);
  assert.equal(all.filter(node => textOf(node) === 'Section 1').length, 1);
  assert.equal(all.filter(node => node.type === 'button' && textOf(node) === '＋ 섹션 추가').length, 1);
  all.find(node => node.props?.['aria-current'] === 'true').props.onClick();
  assert.deepEqual(calls, [runs[0].key]); h.close();
});

test('outline keyboard movement and drop use the lower visible run, preserving intervening text', async () => {
  const h = hookHarness(); let blocks = seedStoryboard(product, analysis('emo'));
  const List = await loadStoryboardComponent('StoryboardPageList.jsx', 'StoryboardPageList', { ...h.hooks, Icon, templateSectionName,
    StoryboardPreview: Preview, reuseSlotsForBlock, CATALOG_DRAG_MIME });
  const moves = [], drops = [];
  const props = () => ({ runs: visibleSectionRuns(blocks), page: [], onAdd() {}, onSelect() {}, onRemove() {}, onOverview() {},
    onMove(key, beforeKey) { moves.push([key, beforeKey]); blocks = moveTemplateSection(blocks, key, beforeKey); },
    onDropContent: (...args) => drops.push(args), sectionRefs: { current: new Map() } });
  let tree = h.render(List, props());
  const before = visibleSectionRuns(blocks), handle = nodes(tree).find(node => node.props?.['aria-label'] === 'Section 3 순서 바꾸기');
  handle.props.onKeyDown({ key: 'ArrowUp', preventDefault() {} });
  assert.deepEqual(moves, [[before[2].key, before[1].key]]);
  tree = h.render(List, props());
  const insert = nodes(tree).find(node => node.props?.['data-section-drop'] === visibleSectionRuns(blocks)[2].key);
  const transfer = { getData: () => '', types: ['text/example-id'] };
  insert.props.onDrop({ dataTransfer: transfer, preventDefault() {} });
  assert.equal(drops[0][1], visibleSectionRuns(blocks)[2].key); h.close();
});

test('section picker directly adds available photo sections with the default design', async () => {
  const h = hookHarness(), blocks = seedStoryboard(product, analysis('emo')), catalogs = catalogsFor('emo'), calls = [];
  const Picker = await loadStoryboardComponent('StoryboardSectionPicker.jsx', 'StoryboardSectionPicker', { ...h.hooks, Icon, pageTemplatesCatalog,
    getTemplate, addTemplateSection, changeTemplateSectionDesign, sectionRuns, templateSectionName, assemblePage,
    StoryboardPreview: Preview, sectionDesignPreviewSample, sectionDesignChoices, defaultSectionDesignId, currentSectionDesignId,
    storyboardPreviewCuts, NEW_SECTION_DRAG_MIME: 'new-section' });
  const props = { mode: 'add', blocks, catalogs, gender: 'women', onChoose: (...args) => calls.push(args), onDragChange() {} };
  const tree = h.render(Picker, props), options = nodes(tree).filter(node => node.type === 'button' && node.props.draggable !== undefined);
  assert.ok(options.length >= 2); assert.doesNotMatch(textOf(tree), /상품 소개|추천 대상/);
  options.find(node => textOf(node).startsWith('일상 착용컷')).props.onClick({ currentTarget: { querySelector: () => null } });
  assert.deepEqual(calls[0].slice(0, 2), ['wear', 'styling']); assert.ok(pageTemplatesCatalog.designs[calls[0][2]]); h.close();
});

test('actual stage omits shot controls, limits delete controls, and marks similar cuts over the original photo', () => {
  const blocks = seedStoryboard(product, analysis('emo')), catalogs = catalogsFor('emo'), run = sectionRuns(blocks)[0];
  const source = { ...run.blocks[0], exampleId: 'released', thumb: 'photo:source' };
  const child = { ...source, id: 'similar', slotKey: null, similarSourceBlockId: source.id };
  const all = [source, child, ...blocks.slice(1)], ordinals = new Map(all.filter(b => b.source !== 'text').map((b, i) => [b.id, i + 1]));
  const markup = renderToStaticMarkup(React.createElement(Stage, { run: { ...run, blocks: [source, child] }, number: 1, blocks: all,
    page: pageFor(all, catalogs), catalogs, photoOrdinals: ordinals, onSimilar() {}, onSelectPhoto() {}, onAddPhoto() {} }));
  assert.match(markup, /01번째 컷과 비슷하게/);
  assert.doesNotMatch(markup, /aria-label="1번째 컷 빼기"/);
  assert.match(markup, /aria-label="2번째 컷 빼기"/);
  assert.equal((markup.match(/>비슷한 컷 추가</g) || []).length, 1);
  assert.doesNotMatch(markup, /전신|상반신|하반신|\sstyle=/);
});

test('drawer focuses itself, keeps actionable clicks alive, closes on blank click or Escape and returns focus', async () => {
  const h = hookHarness(), listeners = new Map(), focus = [], closed = [];
  const trigger = { isConnected: true, focus: () => focus.push('trigger') };
  const panel = { focus: () => focus.push('panel'), contains: target => target.inside };
  const document = { activeElement: trigger, body: { classList: { add() {}, remove() {} } },
    addEventListener: (event, fn) => listeners.set(event, fn), removeEventListener: event => listeners.delete(event) };
  const Drawer = await loadStoryboardComponent('StoryboardDrawer.jsx', 'StoryboardDrawer', { ...h.hooks, Icon, document, createPortal: value => value });
  h.render(Drawer, { mode: 'photo', onClose: () => closed.push(true) }, tree => { tree.ref.current = panel; });
  assert.deepEqual(focus, ['panel']);
  listeners.get('click')({ target: { closest: () => 'button' } });
  assert.equal(closed.length, 0);
  listeners.get('click')({ target: { closest: () => null } });
  listeners.get('keydown')({ key: 'Escape', preventDefault() {} });
  assert.equal(closed.length, 2); h.close(); assert.deepEqual(focus, ['panel', 'trigger']);
});

test('studio photo gallery keeps published sets even when standalone examples cover every direction', async () => {
  const h = hookHarness();
  const Gallery = await loadStoryboardComponent('StoryboardExampleGallery.jsx', 'MoodGuide', { ...h.hooks, ...generation,
    filterExamplesForModel, filterSpaceSetsForModel, storyboardSpaceSetsFor, spaceSetDisplayName, examplePlace, filterGalleryExamples, visiblePlaceTabs,
    isSourceBasedDetailRecipe, ExampleThumb: 'example-thumb', MineImageTab: 'mine-images', ShotSegment: 'shots', Icon });
  const props = { catalogs: withStoryboardSpaceSetExamples({ genExamples: rawExamples }), cut: 'horizon', shot: 'full', clothingType: 'top', gender: 'women' };
  let tree = h.render(Gallery, props);
  assert.ok(nodes(tree).some(node => node.type === 'button' && textOf(node) === '세트컷'));
  const stack = nodes(tree).find(node => node.type === 'button' && node.props['aria-label']?.includes('컷 펼치기'));
  assert.ok(stack);
  stack.props.onClick(); tree = h.render(Gallery, props);
  assert.ok(nodes(tree).some(node => textOf(node) === '전체 예시'));
  tree = h.render(Gallery, { ...props, cut: 'product', shot: 'ghost', onExampleDrag() {} });
  assert.ok(nodes(tree).filter(node => node.type === 'button' && node.props.className?.includes('sb-excell')).every(node => node.props.draggable === false));
  h.close();
});
