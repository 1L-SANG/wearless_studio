import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import { seedStoryboard } from '../../src/lib/pageTemplates/seed.js';
import { defaultPageStyle } from '../../src/lib/pageTemplates/pageStyle.js';
import { addTemplateSection, changeTemplateSectionDesign } from '../../src/lib/pageTemplates/sectionActions.js';
import { sectionRuns } from '../../src/lib/pageTemplates/sectionRuns.js';
import { pageTemplatesCatalog } from '../../src/lib/pageTemplates/catalog.js';
import { sectionDesignChoices, sectionDesignPhotoCount, defaultSectionDesignId, currentSectionDesignId } from '../../src/lib/pageTemplates/designChoices.js';
import { uniqueGenerationCutCount } from '../../src/lib/generationCutCount.js';
import { sectionDesignPreviewSample } from '../../src/lib/pageTemplates/designPreview.js';
import { templateSectionName } from '../../src/lib/pageTemplates/slots.js';
import { baseColorOf, resolvePageTokens } from '../../src/lib/pageTemplates/tokens.js';
import { stageAdditions, addStageAddition } from '../../src/lib/pageTemplates/stageAdditions.js';
import { hookHarness, loadStoryboardComponent, nodes, textOf } from './helpers/storyboardComponents.mjs';

let vite, Picker, Preview, sectionDesignAxes;
before(async () => {
  vite = await createServer({ configFile: false, plugins: [react()],
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, watch: null, ws: false }, appType: 'custom',
    optimizeDeps: { noDiscovery: true, include: [] }, logLevel: 'silent' });
  ({ StoryboardSectionPicker: Picker } = await vite.ssrLoadModule('/src/features/storyboard/StoryboardSectionPicker.jsx'));
  ({ StoryboardPreview: Preview } = await vite.ssrLoadModule('/src/features/storyboard/StoryboardPreview.jsx'));
  ({ sectionDesignAxes } = await import('../../src/lib/pageTemplates/designAxes.js'));
});
after(() => vite?.close());

const product = { clothingType: 'top', colors: [
  { id: 'base', isBase: true, name: '아이보리', swatchId: 'ivory', images: [{ slot: 'Front', src: '/front.jpg' }, { slot: 'Back', src: '/back.jpg' }] },
  { id: 'blue', name: '블루', images: [{ slot: 'Front', src: '/blue-front.jpg' }] },
] };
const analysis = { targetGenders: ['women'], pageStyle: defaultPageStyle() };
const catalogs = { detailProduct: product, analysis, genExamples: [] };
const board = (value = product) => seedStoryboard(value, analysis);
const render = props => renderToStaticMarkup(React.createElement(Picker, { mode: 'design', catalogs, ...props }));
const group = (tree, label) => nodes(tree).find(node => node.props?.role === 'group' && node.props['aria-label'] === label);
const buttons = tree => nodes(tree).filter(node => node.type === 'button');
const selectable = tree => buttons(tree).filter(node => typeof node.props['aria-pressed'] === 'boolean');
const actualPreviews = tree => nodes(tree)
  .filter(node => node.type?.$$typeof === Symbol.for('react.memo'))
  .map(node => node.type.type(node.props));
const finished = tree => nodes(tree).find(node => node.props?.role === 'region' && node.props['aria-label'] === '완성 모습');
async function pickerHarness() {
  const hooks = hookHarness();
  const Component = await loadStoryboardComponent('StoryboardSectionPicker.jsx', 'StoryboardSectionPicker', {
    ...hooks.hooks, Icon: 'icon', pageTemplatesCatalog, addTemplateSection, changeTemplateSectionDesign,
    sectionRuns, sectionDesignChoices, defaultSectionDesignId, currentSectionDesignId, sectionDesignAxes,
    sectionDesignPreviewSample, templateSectionName, StoryboardPreview: Preview, NEW_SECTION_DRAG_MIME: 'application/x-section',
  });
  return { render: props => hooks.render(Component, { mode: 'design', catalogs, ...props }), close: hooks.close };
}

test('WOC10 daily wear separates layout glyphs, actual decoration thumbnails and the finished section', () => {
  const blocks = board(), run = sectionRuns(blocks).find(item => item.usage === 'styling');
  const html = render({ blocks, runKey: run.key });
  assert.match(html, /role="group" aria-label="배치"/);
  assert.match(html, /<h3[^>]*>배치<\/h3>/);
  assert.match(html, /role="group" aria-label="꾸밈"/);
  assert.match(html, /aria-label="꾸밈 · 액자"/);
  for (const [layout, name] of [['column', '한 열'], ['twoColumn', '2열'], ['heroPair', '큰 사진 아래 두 칸']]) {
    assert.match(html, new RegExp(`aria-label="배치 · ${name}"`));
    assert.match(html, new RegExp(`data-layout-glyph="wear:${layout}"`));
  }
  const layoutButtons = [...html.matchAll(/<button[^>]*aria-label="배치 · [\s\S]*?<\/button>/g)].map(match => match[0]);
  assert.equal(layoutButtons.length, 3);
  assert.ok(layoutButtons.every(button => !/<image|data-design-thumbnail|previewWirePhoto/.test(button)));
  assert.match(html, /<image/);
  assert.match(html, /role="region" aria-label="완성 모습" tabindex="0"/);
  assert.doesNotMatch(html, /previewWirePhoto|섹션 빼기|<small>기본<\/small>/);
});

test('WOC10 points present two flat designs with one selected choice and the full finished section', () => {
  const blocks = addTemplateSection(board(), product, analysis, 'points');
  const run = sectionRuns(blocks).find(item => item.kind === 'points');
  assert.ok(run);
  const html = render({ blocks, runKey: run.key });
  assert.doesNotMatch(html, /aria-label="배치"|aria-label="꾸밈"/);
  assert.match(html, /role="group" aria-label="디자인"/);
  assert.equal((html.match(/aria-pressed=/g) || []).length, 2);
  assert.equal((html.match(/aria-pressed="true"/g) || []).length, 1);
  assert.match(html, /aria-label="디자인 · 사진과 글 칸 카드"/);
  assert.match(html, /aria-label="디자인 · 제목, 설명, 사진"/);
  assert.match(html, /role="region" aria-label="완성 모습"/);
  assert.doesNotMatch(html, /<small>기본<\/small>|부위별 카드|섹션 빼기/);
});

test('WOC6 add menu omits SNS while keeping both repeatable wear usages', () => {
  const blocks = board().filter(block => block.pageSectionKind !== 'sns');
  const html = render({ mode: 'add', blocks });
  assert.doesNotMatch(html, /SNS|data-default-design="(?:I09-D14|P07-D08)"/);
  assert.match(html, /카페, 골목, 집처럼 장소에서 찍은 사진/);
  assert.match(html, /단색 배경에서 앞, 옆, 뒤를 찍은 사진/);
});

test('WOC11 flat point selection callbacks preserve saved blocks, photo counts and undo snapshots', async () => {
  let blocks = addTemplateSection(board(), product, analysis, 'points');
  const runKey = sectionRuns(blocks).find(item => item.kind === 'points').key;
  const original = structuredClone(blocks), chosen = [], h = await pickerHarness();
  const eraseDesign = list => list.map(({ pageDesignId, ...block }) => block);
  const generationCount = uniqueGenerationCutCount(blocks, product);
  const photoCount = sectionDesignPhotoCount(sectionRuns(blocks).find(run => run.key === runKey), 'PT-PHOTO-CARD-01', blocks);
  const undo = [];
  const onChoose = (kind, usage, designId) => {
    undo.push(structuredClone(blocks));
    chosen.push([kind, usage, designId]);
    blocks = changeTemplateSectionDesign(blocks, runKey, designId, { product });
  };
  try {
    let tree = h.render({ blocks, runKey, onChoose });
    assert.equal(group(tree, '배치'), undefined);
    assert.equal(group(tree, '꾸밈'), undefined);
    assert.equal(selectable(tree).length, 2);
    assert.equal(selectable(tree).filter(button => button.props['aria-pressed']).length, 1);
    for (const [name, expectedId] of [
      ['제목, 설명, 사진', 'PT-TEXT-PHOTO-01'], ['사진과 글 칸 카드', 'PT-PHOTO-CARD-01'],
    ]) {
      selectable(tree).find(button => button.props['aria-label'] === `디자인 · ${name}`).props.onClick();
      assert.deepEqual(chosen.at(-1), ['points', undefined, expectedId]);
      assert.ok(blocks.filter(block => block.pageSectionKey === runKey).every(block => block.pageDesignId === expectedId));
      assert.deepEqual(eraseDesign(blocks), eraseDesign(original));
      assert.equal(uniqueGenerationCutCount(blocks, product), generationCount);
      assert.equal(sectionDesignPhotoCount(sectionRuns(blocks).find(run => run.key === runKey), expectedId, blocks), photoCount);
      tree = h.render({ blocks, runKey, onChoose });
      assert.equal(selectable(tree).filter(button => button.props['aria-pressed']).length, 1);
    }
    assert.deepEqual(blocks, original, 'returning to the default changes no stored block fields');
    blocks = undo.pop();
    assert.ok(blocks.filter(block => block.pageSectionKey === runKey).every(block => block.pageDesignId === 'PT-TEXT-PHOTO-01'));
    blocks = undo.pop();
    assert.deepEqual(blocks, original, 'both undo snapshots restore exact saved blocks');
  } finally { h.close(); }
});

test('WOC6B one eligible layout omits its group while daily decoration remains', () => {
  const original = board(), run = sectionRuns(original).find(item => item.usage === 'styling');
  const photo = run.blocks.find(block => block.source !== 'text');
  const blocks = original.filter(block => block.pageSectionKey !== run.key || block.source === 'text' || block.id === photo.id);
  const html = render({ blocks, runKey: run.key });
  assert.doesNotMatch(html, /aria-label="배치"/);
  assert.match(html, /aria-label="꾸밈"/);
  assert.match(html, /aria-label="일상 착용컷 완성 모습"/);
  assert.doesNotMatch(html, /섹션 빼기/);
  const singleProduct = { ...product, colors: [product.colors[0]] }, singleBlocks = board(singleProduct);
  const color = sectionRuns(singleBlocks).find(item => item.kind === 'color_product');
  const singleHtml = render({ blocks: singleBlocks, runKey: color.key, catalogs: { ...catalogs, detailProduct: singleProduct } });
  assert.doesNotMatch(singleHtml, /role="group"|aria-pressed=/);
  assert.match(singleHtml, /role="region" aria-label="완성 모습"/);
});

test('WOC6 legacy hidden wear remains unselected while new axis choices can replace it', async () => {
  const original = board(), run = sectionRuns(original).find(item => item.usage === 'fit');
  const blocks = original.map(block => block.pageSectionKey === run.key ? { ...block, pageDesignId: 'P04-D01' } : block);
  const snapshot = structuredClone(blocks), chosen = [], h = await pickerHarness();
  try {
    const tree = h.render({ blocks, runKey: run.key, onChoose: (...args) => chosen.push(args) });
    const choices = selectable(group(tree, '배치'));
    assert.equal(choices.length, 3);
    assert.ok(choices.every(button => !button.props['aria-pressed']));
    choices.find(button => button.props['aria-label'] === '배치 · 한 열').props.onClick();
    assert.deepEqual(chosen, [['wear', 'fit', 'WEAR-FIT-COLUMN-01']]);
    assert.deepEqual(blocks, snapshot);
  } finally { h.close(); }
});

test('WOC10 flat point previews retain every borrowed photo, target geometry and current page tone', async () => {
  const initial = addTemplateSection(board(), product, analysis, 'points')
    .map(block => block.source === 'text' ? block : { ...block, source: 'mine', ownImages: [`actual:${block.id}`], thumb: `actual:${block.id}` });
  const runKey = sectionRuns(initial).find(item => item.kind === 'points').key;
  const blocks = changeTemplateSectionDesign(initial, runKey, 'PT-TEXT-PHOTO-01', { product });
  const pointCatalogs = { ...catalogs, analysis: { ...analysis, pageStyle: { ...analysis.pageStyle, colorMode: 'point' } } };
  const tone = resolvePageTokens(pointCatalogs.analysis.pageStyle, baseColorOf(product).swatchId).colors;
  const h = await pickerHarness();
  try {
    const tree = h.render({ blocks, runKey, catalogs: pointCatalogs });
    const previews = actualPreviews(tree);
    assert.equal(previews.length, 3, 'both choices and the completed section use the real preview');
    for (const preview of previews) {
      assert.equal(preview.type, Preview);
      assert.notEqual(preview.props.fit, true);
      assert.notEqual(preview.props.wireframe, true);
      assert.equal(preview.props.emphasizeText, true);
      const photos = preview.props.blocks.flatMap(page => page.elements).filter(element => element.type === 'image');
      assert.equal(photos.length, 3, 'all saved point items remain visible instead of the old two-item sample');
      assert.ok(photos.every(photo => initial.some(block => block.id === photo.sourceBlockId) && photo.src === `actual:${photo.sourceBlockId}`));
    }
    const selected = selectable(tree).find(button => button.props['aria-pressed']);
    assert.equal(selected.props['aria-label'], '디자인 · 제목, 설명, 사진');
    const plain = actualPreviews(selected)[0].props.blocks;
    const card = actualPreviews(selectable(tree).find(button => !button.props['aria-pressed']))[0].props.blocks;
    assert.equal(actualPreviews(finished(tree))[0].props.blocks, plain, 'the selected thumbnail and finished section share the complete composition');
    assert.notDeepEqual(plain, card, 'flat choices retain distinct photo/text geometry');
    const elements = pages => pages.flatMap(page => page.elements);
    assert.deepEqual(elements(plain).filter(element => element.type === 'image').map(element => [element.x, element.w, element.h]), Array.from({ length: 3 }, () => [40, 920, 1150]));
    assert.deepEqual(elements(card).filter(element => element.type === 'image').map(element => [element.x, element.w, element.h]), Array.from({ length: 3 }, () => [60, 880, 1100]));
    assert.ok(plain.every(page => page.bg === '#ffffff'));
    assert.ok(card.every(page => page.bg === tone.panel));
    assert.ok(elements(card).some(element => element.type === 'shape' && element.fill === '#ffffff' && element.w === 880));
    const title = elements(plain).find(element => element.copyRole === 'items.0.name');
    const desc = elements(plain).find(element => element.copyRole === 'items.0.desc');
    const photo = elements(plain).find(element => element.type === 'image');
    assert.equal(desc.y - title.y - title.h, 66);
    assert.equal(photo.y - desc.y - desc.h, 24);
  } finally { h.close(); }
});

test('WOC6 flat first choices retain paired ghost behavior and the first header cannot remove its section', async () => {
  const original = board(), runKey = original[0].pageSectionKey, context = { product, analysis };
  const addition = stageAdditions(original, runKey, context).find(item => item.kind === 'firstGhost');
  const blocks = addStageAddition(original, runKey, addition, context), chosen = [], h = await pickerHarness();
  try {
    const tree = h.render({ blocks, runKey, onChoose: (...args) => chosen.push(args) });
    assert.equal(group(tree, '배치'), undefined);
    assert.equal(group(tree, '꾸밈'), undefined);
    assert.doesNotMatch(textOf(tree), /섹션 빼기/);
    assert.equal(selectable(tree).length, 2);
    selectable(tree).find(button => !button.props['aria-pressed']).props.onClick();
    assert.deepEqual(chosen, [['first', undefined, 'P01-SHOW-01']]);
    const next = changeTemplateSectionDesign(blocks, runKey, chosen[0][2], { product });
    assert.equal(next[0].pageDesignId, 'P01-SHOW-01-G');
    assert.deepEqual(next.map(block => block.id), blocks.map(block => block.id));
  } finally { h.close(); }
});

test('WOC10 non-axis sections keep eligible flat choices and always expose the finished composition', () => {
  const blocks = addTemplateSection(board(), product, analysis, 'fabric');
  for (const run of sectionRuns(blocks).filter(item => ['fabric', 'review', 'faq', 'intro', 'for_you'].includes(item.kind))) {
    const html = render({ blocks, runKey: run.key });
    const choices = sectionDesignChoices(run, { storyboard: blocks, product });
    assert.doesNotMatch(html, /aria-label="배치"|aria-label="꾸밈"|섹션 빼기|<small>기본<\/small>/);
    assert.equal((html.match(/aria-pressed=/g) || []).length, choices.length > 1 ? choices.length : 0, run.kind);
    assert.equal((html.match(/aria-pressed="true"/g) || []).length, choices.length > 1 ? 1 : 0, run.kind);
    assert.match(html, /<h3>완성 모습<\/h3>/);
    assert.match(html, /role="region" aria-label="완성 모습" tabindex="0"/);
    assert.match(html, /role="img" aria-label="[^"]+ 완성 모습"/);
  }
});
