import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { nodes, textOf, hookHarness, loadStoryboardComponent } from './helpers/storyboardComponents.mjs';
import { sectionRuns, visibleSectionRuns, isTextOnlySection, moveTemplateSection } from '../../src/lib/pageTemplates/sectionRuns.js';
import { seedStoryboard } from '../../src/lib/pageTemplates/seed.js';
import { addTemplateSection } from '../../src/lib/pageTemplates/sectionActions.js';
import { automaticSectionInsertionKey } from '../../src/lib/pageTemplates/sectionPlacement.js';
import { defaultPageStyle } from '../../src/lib/pageTemplates/pageStyle.js';
import { validateStoryboardV4 } from '../../src/lib/pageTemplates/validateStoryboard.js';

const read = path => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');
const postcss = createRequire(import.meta.resolve('vite'))('postcss');
const css = postcss.parse(read('src/features/storyboard/StoryboardLayout.module.css'));
const rule = selector => Object.fromEntries((css.nodes.find(node => node.type === 'rule' && node.selector === selector)?.nodes || []).filter(node => node.type === 'decl').map(node => [node.prop,node.value]));
const product = { clothingType: 'top', colors: [{ id: 'base', isBase: true }] };
const analysis = (templateId, fabric = false) => ({ targetGenders: ['women'], pageStyle: { ...defaultPageStyle(), templateId },
  detailRecommendations: { version: 1, status: 'ready', candidates: fabric ? [{ id: 'weave', kind: 'fabric', label: '원단', rank: 1,
    informationGroup: 'fabric', visibility: 'clear', photoUse: 'standalone', direction: 'front' }] : [] } });
// Explicitly reconstruct an already-saved fallback; new seeds omit this run.
const savedFabricFallback = templateId => seedStoryboard(product, analysis(templateId, true)).map(block => {
  if (block.pageSectionKind !== 'fabric') return block;
  const { taxonomyVersion, pageSectionKey, pageSectionKind, pageDesignId, sectionId, sectionTitle, sectionLayout, sectionCustom, id } = block;
  return { taxonomyVersion, pageSectionKey, pageSectionKind, pageDesignId, sectionId, sectionTitle, sectionLayout, sectionCustom,
    id, source: 'text', slotKey: null, cutType: null, contentRole: null, sectionRole: null };
});
const withClass = (tree, name) => nodes(tree).filter(node => String(node.props?.className || '').includes(name));
let vite, List, Picker, Stage, assemble, previewCuts;
before(async () => {
  vite = await createServer({ configFile: false, plugins: [react()], optimizeDeps: { noDiscovery: true, include: [] },
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, watch: null, ws: false }, appType: 'custom', logLevel: 'silent' });
  ({ StoryboardPageList: List } = await vite.ssrLoadModule('/src/features/storyboard/StoryboardPageList.jsx'));
  ({ StoryboardSectionPicker: Picker } = await vite.ssrLoadModule('/src/features/storyboard/StoryboardSectionPicker.jsx'));
  ({ StoryboardStage: Stage } = await vite.ssrLoadModule('/src/features/storyboard/StoryboardStage.jsx'));
  ({ assemblePage: assemble } = await vite.ssrLoadModule('/src/lib/pageTemplates/assemble.js'));
  ({ storyboardPreviewCuts: previewCuts } = await vite.ssrLoadModule('/src/features/storyboard/storyboardPreview.js'));
});
after(async () => { await vite?.close(); });

test('R3-1 outline header has only the title and add pill; overview is after the last section with its callback', async () => {
  const h = hookHarness(), blocks = seedStoryboard(product, analysis('emo')), calls = [];
  const Component = await loadStoryboardComponent('StoryboardPageList.jsx', 'StoryboardPageList', { ...h.hooks,
    Icon: 'icon', templateSectionName: block => block.pageSectionKind, StoryboardPreview: 'preview' });
  const tree = h.render(Component, { runs: visibleSectionRuns(blocks), page: [], sectionRefs: { current: new Map() },
    onOverview: () => calls.push('overview') });
  const head = withClass(tree, 'outlineHead')[0];
  assert.equal(textOf(head), '페이지 구성＋ 섹션 추가');
  assert.deepEqual(nodes(head).filter(node => node.type === 'button').map(textOf), ['＋ 섹션 추가']);
  const scroll = withClass(tree, 'outlineScroll')[0], children = nodes(scroll);
  const overview = children.find(node => node.type === 'button' && textOf(node) === '전체 보기');
  assert.ok(overview); assert.equal(children.at(-1), overview);
  assert.ok(children.findLastIndex(node => node.props?.['data-section-key']) < children.indexOf(overview));
  overview.props.onClick(); assert.deepEqual(calls, ['overview']); h.close();
});
test('R3-1 narrow header cannot wrap and the owner title correction stays intact', () => {
  assert.equal(rule('.outlineHead')['flex-wrap'], 'nowrap');
  assert.equal(rule('.outlineHead > span, .outlineHead button')['white-space'], 'nowrap');
  assert.equal(rule('.outlineHead > span, .outlineHead button')['flex-shrink'], '0');
  assert.equal(rule('.sectionTitle').gap, 'var(--sp-3)');
  assert.equal(rule('.sectionTitle small')['margin-left'], '0');
  assert.ok(53 + 81 + 23 + 3 + 3 <= 184 - 20);
  assert.equal(rule('.outlineOverview')['font-size'], 'var(--sb-fs-meta)');
  assert.ok(rule('.outlineHead button, .back')['text-decoration'] === 'underline');
});
for (const templateId of ['emo','show']) for (const hasPhoto of [false,true]) test(`R3-2 ${templateId} fabric ${hasPhoto ? 'photo stays visible' : 'old saved text fallback stays hidden'}`, () => {
  const blocks = hasPhoto ? seedStoryboard(product, analysis(templateId, true)) : savedFabricFallback(templateId), snapshot = structuredClone(blocks);
  const run = sectionRuns(blocks).find(run => run.kind === 'fabric'); assert.ok(run);
  assert.equal(run.blocks.some(block => block.source !== 'text'), hasPhoto);
  assert.equal(isTextOnlySection(run), !hasPhoto);
  assert.equal(visibleSectionRuns(blocks).some(item => item.key === run.key), hasPhoto);
  assert.deepEqual(blocks, snapshot); assert.equal(validateStoryboardV4(blocks), blocks);
});
for (const source of ['none', 'candidate', 'Detail', 'BackDetail']) test(`R3-2 missing fabric menu requires an analysis fabric candidate (${source})`, () => {
  const hasPhoto = source === 'candidate', p = ['Detail', 'BackDetail'].includes(source)
    ? { ...product, colors: [{ ...product.colors[0], images: [{ slot: source, src: '/seller-closeup.jpg' }] }] } : product;
  const a = analysis('emo', source === 'candidate'), blocks = seedStoryboard(p, a).filter(block => block.pageSectionKind !== 'fabric');
  const markup = renderToStaticMarkup(React.createElement(Picker, { mode: 'add', blocks,
    catalogs: { detailProduct: p, analysis: a, genExamples: [] }, gender: 'women' }));
  assert.equal(markup.includes('원단 설명'), hasPhoto);
});
test('R3-2 hidden fabric remains at the boundary through automatic insertion, move and undo', () => {
  const blocks = savedFabricFallback('emo').filter(block => ['first','fabric'].includes(block.pageSectionKind) || block.pageSectionKey === 'emo.wear_fit');
  const fabric = blocks.find(block => block.pageSectionKind === 'fabric');
  const key = automaticSectionInsertionKey(blocks, 'wear', 'styling'); assert.equal(key, 'emo.wear_fit');
  const added = addTemplateSection(blocks, product, analysis('emo'), 'wear', 'styling', { beforeSectionKey: key });
  assert.equal(validateStoryboardV4(added), added);
  const newKey = sectionRuns(added).find(run => !sectionRuns(blocks).some(old => old.key === run.key)).key;
  assert.deepEqual(sectionRuns(added).map(run => run.key), ['emo.first','emo.fabric',newKey,'emo.wear_fit']);
  const moved = moveTemplateSection(added, 'emo.wear_fit', newKey); assert.equal(validateStoryboardV4(moved), moved);
  assert.deepEqual(visibleSectionRuns(moved).map(run => run.key), ['emo.first','emo.wear_fit',newKey]);
  assert.equal(moved[1], fabric);
  const undo = moveTemplateSection(moved, 'emo.wear_fit', null); assert.deepEqual(undo, added);
});
test('R3-2 overview and crop source numbering use the same fabric-aware visible runs', () => {
  const a = analysis('emo'), initial = savedFabricFallback('emo').filter(block => ['first','fabric'].includes(block.pageSectionKind) || block.pageSectionKey === 'emo.wear_fit');
  const blocks = addTemplateSection(initial, product, a, 'points'), run = sectionRuns(blocks).find(run => run.kind === 'points');
  const catalogs = { detailProduct: product }, page = assemble({ storyboard: blocks, product, analysis: a, cutResults: previewCuts(blocks, catalogs) });
  assert.ok(page.some(block => block.pageSectionKey === 'emo.fabric'), 'the final page keeps its fabric text fallback');
  const markup = renderToStaticMarkup(React.createElement(Stage, { blocks, page, catalogs, run, number: 3,
    photoOrdinals: new Map(blocks.map((block,i)=>[block.id,i+1])) }));
  assert.match(markup, /Section 2 사진에서 따와요/); assert.doesNotMatch(markup, /Section 3 사진에서 따와요/);
  const overview = renderToStaticMarkup(React.createElement(Stage, { blocks, page, catalogs, overview: true }));
  assert.doesNotMatch(overview, /원단 설명 편집/); assert.match(overview, /디테일 요소 편집/);
});
test('R3-2 an old active text-fabric key falls back to an editable visible run', () => {
  const source = read('src/features/storyboard/Storyboard.jsx');
  const declaration = source.match(/const activeRun = [^\n]+/)[0];
  const blocks = seedStoryboard(product, analysis('emo'));
  const active = new Function('runs','visibleRuns','activeSectionKey', `${declaration}; return activeRun;`)(sectionRuns(blocks), visibleSectionRuns(blocks), 'emo.fabric');
  assert.equal(active.key, 'emo.first');
});
