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
import { sectionDesignChoices, defaultSectionDesignId, currentSectionDesignId } from '../../src/lib/pageTemplates/designChoices.js';
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
async function pickerHarness() {
  const hooks = hookHarness();
  const Component = await loadStoryboardComponent('StoryboardSectionPicker.jsx', 'StoryboardSectionPicker', {
    ...hooks.hooks, Icon: 'icon', pageTemplatesCatalog, addTemplateSection, changeTemplateSectionDesign,
    sectionRuns, sectionDesignChoices, defaultSectionDesignId, currentSectionDesignId, sectionDesignAxes,
    sectionDesignPreviewSample, templateSectionName, StoryboardPreview: Preview, NEW_SECTION_DRAG_MIME: 'application/x-section',
  });
  return { render: props => hooks.render(Component, { mode: 'design', catalogs, ...props }), close: hooks.close };
}

test('WOC6 wear choices expose a layout group while omitting the single decoration group', () => {
  const blocks = board(), run = sectionRuns(blocks).find(item => item.usage === 'styling');
  const html = render({ blocks, runKey: run.key });
  assert.match(html, /role="group" aria-label="배치"/);
  assert.match(html, /<h3[^>]*>배치<\/h3>/);
  assert.doesNotMatch(html, /aria-label="꾸밈"|<h3[^>]*>꾸밈<\/h3>/);
  assert.match(html, /<strong>한 열<\/strong>/);
  assert.match(html, /<strong>2열<\/strong>/);
  assert.match(html, /<strong>큰 사진 아래 두 칸<\/strong>/);
  assert.match(html, /previewWirePhoto/);
  assert.doesNotMatch(html, /<image|silhouette/i);
  assert.match(html, /섹션 빼기<\/button>/);
});

test('WOC6 points present two independently selected groups without a duplicate basic badge', () => {
  const blocks = addTemplateSection(board(), product, analysis, 'points');
  const run = sectionRuns(blocks).find(item => item.kind === 'points');
  assert.ok(run);
  const html = render({ blocks, runKey: run.key });
  assert.match(html, /role="group" aria-label="배치"/);
  assert.match(html, /role="group" aria-label="꾸밈"/);
  assert.equal((html.match(/aria-pressed="true"/g) || []).length, 2);
  assert.equal((html.match(/<small>기본<\/small>/g) || []).length, 1);
  assert.match(html, /<strong>기본<\/strong>/);
  assert.match(html, /<strong>부위별 카드<\/strong>/);
});

test('WOC6 add menu omits SNS while keeping both repeatable wear usages', () => {
  const blocks = board().filter(block => block.pageSectionKind !== 'sns');
  const html = render({ mode: 'add', blocks });
  assert.doesNotMatch(html, /SNS|data-default-design="(?:I09-D14|P07-D08)"/);
  assert.match(html, /카페, 골목, 집처럼 장소에서 찍은 사진/);
  assert.match(html, /단색 배경에서 앞, 옆, 뒤를 찍은 사진/);
});

test('WOC6 changing either points axis preserves the other axis through actual selection callbacks', async () => {
  let blocks = addTemplateSection(board(), product, analysis, 'points');
  const runKey = sectionRuns(blocks).find(item => item.kind === 'points').key;
  const original = structuredClone(blocks), chosen = [], h = await pickerHarness();
  const onChoose = (kind, usage, designId) => {
    chosen.push([kind, usage, designId]);
    blocks = changeTemplateSectionDesign(blocks, runKey, designId, { product });
  };
  try {
    let tree = h.render({ blocks, runKey, onChoose });
    assert.equal(selectable(group(tree, '배치')).filter(button => button.props['aria-pressed']).length, 1);
    assert.equal(selectable(group(tree, '꾸밈')).filter(button => button.props['aria-pressed']).length, 1);
    for (const [axis, expectedId] of [
      ['꾸밈', 'PT-COLUMN-CARD-01'], ['배치', 'PT-BELOW-CARD-01'],
      ['꾸밈', 'P06-D01'], ['배치', 'PT-COLUMN-01'],
    ]) {
      selectable(group(tree, axis)).find(button => !button.props['aria-pressed']).props.onClick();
      assert.deepEqual(chosen.at(-1), ['points', undefined, expectedId]);
      assert.ok(blocks.filter(block => block.pageSectionKey === runKey).every(block => block.pageDesignId === expectedId));
      tree = h.render({ blocks, runKey, onChoose });
      assert.equal(selectable(tree).filter(button => button.props['aria-pressed']).length, 2);
    }
    assert.deepEqual(blocks, original, 'returning to both defaults changes no stored block fields');
  } finally { h.close(); }
});

test('WOC6 one eligible layout omits its entire group and does not invent extra preview choices', () => {
  const original = board(), run = sectionRuns(original).find(item => item.usage === 'styling');
  const photo = run.blocks.find(block => block.source !== 'text');
  const blocks = original.filter(block => block.pageSectionKey !== run.key || block.source === 'text' || block.id === photo.id);
  const html = render({ blocks, runKey: run.key });
  assert.doesNotMatch(html, /role="group"|aria-pressed=|<h3/);
  assert.match(html, /일상 착용컷 디자인/);
  assert.match(html, /섹션 빼기<\/button>/);
  const singleProduct = { ...product, colors: [product.colors[0]] }, singleBlocks = board(singleProduct);
  const color = sectionRuns(singleBlocks).find(item => item.kind === 'color_product');
  assert.doesNotMatch(render({ blocks: singleBlocks, runKey: color.key, catalogs: { ...catalogs, detailProduct: singleProduct } }), /role="group"|aria-pressed=|<h3/);
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
    choices.find(button => nodes(button).some(node => node.type === 'strong' && textOf(node) === '한 열')).props.onClick();
    assert.deepEqual(chosen, [['wear', 'fit', 'WEAR-FIT-COLUMN-01']]);
    assert.deepEqual(blocks, snapshot);
  } finally { h.close(); }
});

test('WOC6 axis previews use target combination geometry with the current page tone', async () => {
  const initial = addTemplateSection(board(), product, analysis, 'points');
  const runKey = sectionRuns(initial).find(item => item.kind === 'points').key;
  const blocks = changeTemplateSectionDesign(initial, runKey, 'PT-BELOW-CARD-01', { product });
  const pointCatalogs = { ...catalogs, analysis: { ...analysis, pageStyle: { ...analysis.pageStyle, colorMode: 'point' } } };
  const tone = resolvePageTokens(pointCatalogs.analysis.pageStyle, baseColorOf(product).swatchId).colors;
  const h = await pickerHarness();
  try {
    const tree = h.render({ blocks, runKey, catalogs: pointCatalogs });
    const previews = nodes(tree).filter(node => node.type === Preview);
    assert.equal(previews.length, 4);
    for (const preview of previews) {
      assert.equal(preview.props.fit, true);
      assert.equal(preview.props.wireframe, true);
      assert.deepEqual(preview.props.toneColors, tone);
      assert.ok(preview.props.blocks.some(block => block.elements.some(element => element.type === 'image')));
    }
    const selectedLayout = selectable(group(tree, '배치')).find(button => button.props['aria-pressed']);
    const selectedDecoration = selectable(group(tree, '꾸밈')).find(button => button.props['aria-pressed']);
    assert.deepEqual(nodes(selectedLayout).find(node => node.type === Preview).props.blocks,
      nodes(selectedDecoration).find(node => node.type === Preview).props.blocks, 'both selected axes preview the same combined design');
    const basic = selectable(group(tree, '꾸밈')).find(button => !button.props['aria-pressed']);
    assert.notDeepEqual(nodes(basic).find(node => node.type === Preview).props.blocks,
      nodes(selectedDecoration).find(node => node.type === Preview).props.blocks, 'basic and card have distinct engine geometry');
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

test('WOC6 non-axis sections keep their existing flat selection and removal remains in the heading', () => {
  const blocks = addTemplateSection(board(), product, analysis, 'fabric');
  for (const run of sectionRuns(blocks).filter(item => ['fabric', 'review', 'faq', 'intro', 'for_you'].includes(item.kind))) {
    const html = render({ blocks, runKey: run.key });
    assert.doesNotMatch(html, /role="group"|<h3/);
    assert.match(html, /aria-pressed="true"/);
    assert.match(html, /<h2>[^<]+<\/h2><button[^>]*>섹션 빼기<\/button><\/div>/);
  }
});
