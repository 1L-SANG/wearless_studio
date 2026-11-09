import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { defaultPageStyle } from '../../src/lib/pageTemplates/pageStyle.js';

let vite, Preview, List, Picker, Stage, positions, placement, seed, runs, add;
const product = { clothingType: 'top', colors: [{ id: 'base', isBase: true }] };
const analysis = { targetGenders: ['women'], pageStyle: { ...defaultPageStyle(), templateId: 'emo' } };
const html = (component, props) => renderToStaticMarkup(React.createElement(component, props));
before(async () => {
  vite = await createServer({ configFile: false, plugins: [react()], optimizeDeps: { noDiscovery: true, include: [] },
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, watch: null }, appType: 'custom', logLevel: 'silent' });
  ({ StoryboardPreview: Preview } = await vite.ssrLoadModule('/src/features/storyboard/StoryboardPreview.jsx'));
  ({ StoryboardPageList: List } = await vite.ssrLoadModule('/src/features/storyboard/StoryboardPageList.jsx'));
  ({ StoryboardSectionPicker: Picker } = await vite.ssrLoadModule('/src/features/storyboard/StoryboardSectionPicker.jsx'));
  ({ StoryboardStage: Stage } = await vite.ssrLoadModule('/src/features/storyboard/StoryboardStage.jsx'));
  ({ seedStoryboard: seed } = await vite.ssrLoadModule('/src/lib/pageTemplates/seed.js'));
  ({ sectionRuns: runs } = await vite.ssrLoadModule('/src/lib/pageTemplates/sectionRuns.js'));
  ({ addTemplateSection: add } = await vite.ssrLoadModule('/src/lib/pageTemplates/sectionActions.js'));
});
after(async () => { await vite?.close(); });

test('A3/A4 all storyboard previews hide sample words; design wireframes contain no photos or color styling', () => {
  const blocks = [{ id: 'a', pageSectionKey: 'emo.first', h: 400, bg: '#fdd', elements: [
    { id: 'photo', type: 'image', x: 0, y: 0, w: 200, h: 300, src: '/photo.jpg' },
    { id: 'title', type: 'text', x: 200, y: 10, w: 700, h: 100, text: 'SAMPLE_SECRET_WORD', style: { size: 40, color: '#f00' } },
  ] }];
  assert.doesNotMatch(html(Preview, { blocks }), /SAMPLE_SECRET_WORD|<text|<tspan/);
  const frame = html(Preview, { blocks, wireframe: true });
  assert.doesNotMatch(frame, /<image|photo.jpg|#fdd|#f00/);
  assert.match(frame, /previewTextLine/); assert.match(frame, /previewWirePhoto/);
});
test('A1/A5 outline has one header add action, no between-section add buttons, and focusable reorder arrows', () => {
  const blocks = seed(product, analysis);
  const visible = runs(blocks).filter(run => !['intro', 'for_you'].includes(run.kind));
  const markup = html(List, { runs: visible, page: [], activeKey: visible[0].key, sectionRefs: { current: new Map() } });
  assert.equal((markup.match(/>＋ 섹션 추가</g) || []).length, 1);
  assert.doesNotMatch(markup, /앞에 섹션 추가|페이지 끝에 섹션 추가|>＋</);
  assert.match(markup, /data-move-action="up"/); assert.match(markup, /data-move-action="down"/);
  assert.match(markup, /aria-disabled="true"/);
});
test('A4/A5/A8 add list contains only currently addable photo kinds, directly usable default wireframes', () => {
  const blocks = seed(product, analysis);
  const markup = html(Picker, { mode: 'add', blocks, catalogs: { detailProduct: product, analysis, genExamples: [] } });
  assert.doesNotMatch(markup, /상품 소개|추천 대상|디자인을 고르면|이미 들어 있는 섹션/);
  assert.match(markup, /일상 착용컷/); assert.match(markup, /스튜디오 착용컷/);
  assert.match(markup, /data-default-design=/); assert.match(markup, /previewWirePhoto/);
});
test('A5 automatic insertion follows default order and the last matching run, without splitting hidden text runs', async () => {
  placement ||= await vite.ssrLoadModule('/src/lib/pageTemplates/sectionPlacement.js');
  const blocks = seed(product, analysis);
  const key = placement.automaticSectionInsertionKey(blocks, 'wear', 'styling');
  const original = runs(blocks), last = original.findLastIndex(run => run.kind === 'wear' && run.usage === 'styling');
  assert.equal(key, original[last + 1]?.key || null);
  const after = add(blocks, product, analysis, 'wear', 'styling', { beforeSectionKey: key });
  const inserted = runs(after).find(run => !original.some(old => old.key === run.key));
  assert.ok(inserted); assert.equal(after[inserted.end]?.pageSectionKey || null, key);
});
test('A9 position labels use assembled reading order, mixed sizes, and no label for a singleton', async () => {
  positions ||= await vite.ssrLoadModule('/src/lib/pageTemplates/storyboardPositions.js');
  const page = [{ id: 's', pageSectionKey: 'emo.wear_styling', h: 1400, elements: [
    { id: 'b', type: 'image', sourceBlockId: 'b', x: 510, y: 0, w: 450, h: 600 },
    { id: 'a', type: 'image', sourceBlockId: 'a', x: 0, y: 0, w: 500, h: 1200 },
    { id: 'c', type: 'image', sourceBlockId: 'c', x: 510, y: 610, w: 450, h: 600 },
  ] }];
  const list = positions.storyboardPositions(page, 'emo.wear_styling');
  assert.deepEqual(list.map(item => item.label), ['큰 자리', '작은 자리 1', '작은 자리 2']);
  assert.equal(positions.storyboardPositions([{ ...page[0], elements: [page[0].elements[0]] }], 'emo.wear_styling')[0].label, null);
});
test('A9 swapping keeps settings/similar followers together and refuses fixed positions and accepts the same kind across places', async () => {
  positions ||= await vite.ssrLoadModule('/src/lib/pageTemplates/storyboardPositions.js');
  const base = { source: 'ai', cutType: 'styling', pageSectionKey: 'emo.wear_styling', pageSectionKind: 'wear', pageSectionUsage: 'styling', spaceGroupId: 'one' };
  const blocks = [{ ...base, id: 'a', shot: 'full' }, { ...base, id: 'sim', similarSourceBlockId: 'a' }, { ...base, id: 'b', shot: 'medium' }];
  const moved = positions.swapStoryboardPositions(blocks, 'a', 'b');
  assert.deepEqual(moved.map(block => block.id), ['b', 'a', 'sim']);
  assert.equal(moved[0].shot, 'medium');
  assert.equal(positions.swapStoryboardPositions(blocks.map(block => block.id === 'b' ? { ...block, spaceGroupId: 'two' } : block), 'a', 'b')[0].id, 'b');
  for (const kind of ['first', 'review', 'color', 'sns', 'fabric']) {
    assert.ok(positions.positionSwapReason({ ...base, pageSectionKind: kind }));
  }
  assert.ok(positions.positionSwapReason({ ...base, cutType: 'horizon', direction: 'front', slotKey: 'wear_fit.front' }));
});
test('A7 replacement selects a whole group and only dims other photographs', () => {
  const blocks = seed(product, analysis), run = runs(blocks).find(run => run.kind === 'wear' && run.usage === 'styling');
  const photos = run.blocks.filter(block => block.source !== 'text').map(block => ({ ...block, spaceGroupId: 'one' }));
  photos.push({ ...photos[0], id: 'other', spaceGroupId: 'two' });
  const markup = html(Stage, { run: { ...run, blocks: photos }, blocks: photos, page: [], catalogs: {}, photoOrdinals: new Map(photos.map((b,i)=>[b.id,i+1])), selectedGroupId: 'one', dimOthers: true });
  assert.equal((markup.match(/aria-pressed="true"/g)||[]).length, photos.length - 1);
  assert.equal((markup.match(/dimPhoto/g)||[]).length, 1);
});
test('A1/A2/A11 scoped CSS contains strip dimensions, centered orbs, panel tokens and dark drop indicator', () => {
  const css = readFileSync(new URL('../../src/features/storyboard/StoryboardLayout.module.css', import.meta.url), 'utf8');
  const tokens = readFileSync(new URL('../../src/styles/tokens.css', import.meta.url), 'utf8');
  assert.match(tokens, /--sb-toc-thumb:\s*184px/); assert.match(tokens, /1360px/);
  assert.match(tokens, /--sb-panel-shadow:/); assert.match(css, /var\(--sb-panel-shadow\)/);
  assert.match(css, /margin-left:\s*-320px/); assert.match(css, /margin-left:\s*-60px/);
  assert.match(css, /var\(--ring-flow-5\)/); assert.doesNotMatch(css, /color:\s*var\(--ring-flow-/);
});


test('B4 signature-only panel preserves the selected signature thumbnail for both genders', async () => {
  const { storyboardPhotoPanelHarness } = await import('./helpers/storyboardPhotoPanelHarness.mjs');
  const { nodes } = await import('./helpers/storyboardComponents.mjs');
  const { storyboardPreviewSource } = await import('../../src/features/storyboard/storyboardPreview.js');
  const { signatureExamplesForBlock } = await import('../../src/lib/signatureCutPool.js');
  for (const gender of ['women', 'men']) {
    const block = seed(product, { ...analysis, pageStyle: { ...analysis.pageStyle, templateId: 'show' } })[0];
    const examples = signatureExamplesForBlock(block, { product, gender });
    const h = await storyboardPhotoPanelHarness({ block, exampleGender: gender, catalogs: { detailProduct: product, genExamples: [], shotTypes: [{ value: 'medium' }], productShotTypes: [] } });
    const guide = nodes(h.render()).find(node => node.type === 'mood-guide');
    assert.deepEqual(guide.props.catalogs.genExamples.map(example => example.id), examples.map(example => example.id));
    guide.props.onExampleChange(examples[0].id, 'all');
    assert.equal(h.block.thumb, examples[0].thumb);
    assert.equal(storyboardPreviewSource({ ...h.block, previewThumb: '/stale.webp' }, { genExamples: [], detailProduct: product }), examples[0].thumb);
    h.close();
  }
});

test('A9 a selected crop highlights only its own thumbnail position', () => {
  const blocks = [{ id: 'section', h: 600, elements: ['a','b','c'].map((id,index) => ({ id, type: 'image', sourceBlockId: 'same', x: 0, y: index*200, w: 300, h: 200, src: '/x' })) }];
  const markup = html(Preview, { blocks, selectedSourceId: 'same', selectedPositionId: 'section:b' });
  assert.equal((markup.match(/positionHighlight/g)||[]).length, 1);
});

test('A5 flight captures a one-time menu row before insertion removes the source node', async () => {
  const { loadStoryboardComponent } = await import('./helpers/storyboardComponents.mjs');
  const clone = { setAttribute() {}, removeAttribute() {}, remove() {}, animate() { return { finished: Promise.resolve() }; } };
  let connected = true, captured;
  const source = { cloneNode: () => clone, getBoundingClientRect: () => connected ? { left: 20, top: 30, width: 64, height: 84 } : { width: 0, height: 0 } };
  const Capture = await loadStoryboardComponent('sectionFlight.js', 'captureSectionPreview');
  captured = Capture(source); connected = false;
  const Fly = await loadStoryboardComponent('sectionFlight.js', 'flySectionPreview', {
    document: { body: { appendChild() {} } }, window: { matchMedia: () => ({ matches: false }) },
  });
  const result = Fly(captured, { getBoundingClientRect: () => ({ left: 100, top: 120, width: 184, height: 240 }) });
  assert.equal(typeof result, 'function');
});

test('B5 preview and stage use the editor crop offset contract for positive crop origins', () => {
  const image = { id: 'crop', type: 'image', sourceBlockId: 'source', x: 0, y: 0, w: 500, h: 600, src: '/source.webp', crop: { iw: 1000, ih: 1200, ox: 200, oy: 100 } };
  const page = [{ id: 'p', h: 600, pageSectionKey: 'emo.points', pageSectionKind: 'points', elements: [image] }];
  const markup = html(Preview, { blocks: page });
  assert.match(markup, /x="-200" y="-100"/);
  const source = { id: 'source', source: 'ai', cutType: 'styling', pageSectionKind: 'wear', pageSectionKey: 'emo.wear_styling', thumb: '/source.webp' };
  const lead = { id: 'lead', source: 'text', pageSectionKind: 'points', pageSectionKey: 'emo.points', pageDesignId: 'P06-D01', pointItems: [{ kind: 'wornCrop', sourceBlockId: 'source' }] };
  const stage = html(Stage, { run: { key: 'emo.points', kind: 'points', blocks: [lead] }, blocks: [source, lead], page, catalogs: {}, photoOrdinals: new Map([['source',1]]) });
  assert.match(stage, /x="-200" y="-100"/); assert.match(stage, /Section 1 사진에서 따와요/);
});

test('A9 crop position IDs survive moving the whole saved run', async () => {
  positions ||= await vite.ssrLoadModule('/src/lib/pageTemplates/storyboardPositions.js');
  const section = (id, e) => ({ id, pageSectionKey: 'emo.points', h: 500, elements: [{ id: e, slotKey: 'points.detail2', sourceBlockId: 'source', type: 'image', x: 0, y: 0, w: 500, h: 500 }] });
  assert.equal(positions.storyboardPositions([section('b17','e17')], 'emo.points')[0].viewId,
    positions.storyboardPositions([section('b5','e5')], 'emo.points')[0].viewId);
});

test('A1 opening count excludes its borrowed ghost appearance while crop-only sections count their positions', () => {
  const blocks = seed(product, { ...analysis, pageStyle: { ...analysis.pageStyle, templateId: 'show' } });
  const first = runs(blocks)[0], own = first.blocks.find(block => block.source !== 'text');
  const page = [{ id: 'first', pageSectionKey: first.key, h: 500, elements: [own.id, 'borrowed'].map((id,index) => ({ id, type: 'image', sourceBlockId: id, x: index*500, y: 0, w: 500, h: 500 })) }];
  const markup = html(List, { runs: [first], page, activeKey: first.key, sectionRefs: { current: new Map() } });
  assert.match(markup, />1컷</); assert.doesNotMatch(markup, />2컷</);
  const lead = { id: 'points', source: 'text', pageSectionKind: 'points', pageSectionKey: 'emo.points' };
  const cropPage = [{ id: 'crops', pageSectionKey: 'emo.points', h: 1500, elements: [0,1,2].map(i => ({
    id: `crop${i}`, type: 'image', sourceBlockId: own.id, slotKey: `points.detail${i+1}`, x: 0, y: i*500, w: 500, h: 500,
  })) }];
  const cropMarkup = html(List, { runs: [{ key: 'emo.points', kind: 'points', blocks: [lead] }], page: cropPage, sectionRefs: { current: new Map() } });
  assert.match(cropMarkup, />3컷</);
});

test('A9 real detail swaps move complete target bindings and validate for adjacent/non-adjacent positions', async () => {
  positions ||= await vite.ssrLoadModule('/src/lib/pageTemplates/storyboardPositions.js');
  const { validateStoryboardV4 } = await vite.ssrLoadModule('/src/lib/pageTemplates/validateStoryboard.js');
  const { pointPhotosForRun } = await vite.ssrLoadModule('/src/lib/pageTemplates/assemble.js');
  for (const count of [2, 3]) {
    const detailAnalysis = { ...analysis, detailRecommendations: { version: 1, status: 'ready', candidates: ['neck','hem','seam'].slice(0,count).map(id => ({ id, kind: 'structure', label: id, informationGroup: id, rank: 1, visibility: 'clear', photoUse: 'standalone', direction: 'front' })) } };
    const blocks = seed(product, detailAnalysis), run = runs(blocks).find(run => run.kind === 'points');
    const photos = run.blocks.filter(block => block.source !== 'text'), lead = run.blocks.find(block => Array.isArray(block.pointItems));
    const swapped = positions.swapStoryboardPositions(blocks, photos[0].id, photos.at(-1).id);
    const result = swapped.find(block => block.id === lead.id), swappedRun = runs(swapped).find(item => item.key === run.key);
    assert.deepEqual(result.pointItems, [...lead.pointItems].reverse());
    if (count === 3) assert.equal(result.pointItems[1], lead.pointItems[1]);
    assert.doesNotThrow(() => validateStoryboardV4(swapped));
    assert.deepEqual(pointPhotosForRun(swappedRun.blocks, swapped).map(photo => photo.id), swappedRun.blocks.filter(photo => photo.source !== 'text').map(photo => photo.id));
    for (const item of result.pointItems) assert.equal(item.detailTargetId, swapped.find(photo => photo.id === item.sourceBlockId).detailTargetId);
    assert.deepEqual(lead.pointItems.map(item => item.sourceBlockId), photos.map(block => block.id), 'undo binding stays intact');
    const life = runs(blocks).find(run => run.kind === 'wear' && run.usage === 'styling').blocks.filter(block => block.source !== 'text');
    assert.doesNotThrow(() => validateStoryboardV4(positions.swapStoryboardPositions(blocks, life[0].id, life[1].id)));
  }
});
