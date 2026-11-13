import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import examples from '../../src/data/genExamples.json' with { type: 'json' };
import details from '../../src/mock/detailRecommendationsFixture.json' with { type: 'json' };
import { withStoryboardSpaceSetExamples } from '../../src/lib/storyboardSpaceSetCatalog.js';
import { defaultPageStyle } from '../../src/lib/pageTemplates/pageStyle.js';
import { seedStoryboard } from '../../src/lib/pageTemplates/seed.js';
import { storyboardStage, stagePhoto } from './helpers/storyboardStageHarness.mjs';
import { nodes } from './helpers/storyboardComponents.mjs';

let vite, Controls, PhotoPanel;
before(async () => {
  vite = await createServer({ configFile: false, plugins: [react()], logLevel: 'silent', optimizeDeps: { noDiscovery: true, include: [] },
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, watch: null, hmr: false, ws: false }, appType: 'custom' });
  ({ StoryboardStyleControls: Controls } = await vite.ssrLoadModule('/src/features/storyboard/StoryboardStyleControls.jsx'));
  ({ StoryboardPhotoPanel: PhotoPanel } = await vite.ssrLoadModule('/src/features/storyboard/StoryboardPhotoPanel.jsx'));
});
after(() => vite?.close());
const read = path => readFileSync(`src/${path}`, 'utf8');
const cssRule = (css, selector) => css.slice(css.indexOf(`${selector} {`) + selector.length + 2).split('}')[0];
const render = (component, props) => renderToStaticMarkup(React.createElement(component, props));
const product = { clothingType: 'top', colors: [
  { id: 'base', isBase: true, name: '블랙', swatchId: 'black', images: [{ slot: 'Front', src: '/front.jpg' }, { slot: 'Back', src: '/back.jpg' }, { slot: 'Detail', src: '/detail.jpg' }] },
  { id: 'blue', name: '블루', swatchId: 'blue', images: [{ slot: 'Front', src: '/blue.jpg' }] },
] };
const analysis = { pageStyle: defaultPageStyle(), targetGenders: ['women'], detailRecommendations: details };
const catalogs = { ...withStoryboardSpaceSetExamples({ genExamples: examples }), detailProduct: product, analysis, detailRecommendations: details,
  shotTypes: [{ value: 'full', label: '전신' }, { value: 'medium', label: '상반신' }], productShotTypes: [{ value: 'ghost', label: '전체컷' }, { value: 'detail', label: '디테일' }] };
const blocks = seedStoryboard(product, analysis);

test('WOC9 tone labels, accessible names and tooltips share the short copy', () => {
  for (const [colorMode, label] of [['mono', '기본'], ['point', '의류 색감']]) {
    const html = render(Controls, { pageStyle: { ...defaultPageStyle(), colorMode }, product: { colors: [{ swatchId: 'blue' }] } });
    assert.match(html, new RegExp(`aria-label="${label}"`));
    assert.match(html, new RegExp(`title="${label}"`));
    assert.match(html, new RegExp(`>${label}<`));
    assert.doesNotMatch(html, /의류 색감에 맞추기/);
  }
});

test('WOC9 equal carousel columns preserve the header edge and put dots beside labels', () => {
  const tokens = read('styles/tokens.css'), tone = read('features/storyboard/StoryboardTone.module.css');
  const width = Number(tokens.match(/--sb-style-controls-width: (\d+)px;/)?.[1]);
  assert.equal(width, 256);
  assert.match(tone, /grid-auto-columns: var\(--sb-style-controls-width\)/);
  assert.match(cssRule(tone, '.pickerHead'), /display: flex[^}]*justify-content: space-between/);
  assert.match(cssRule(tone, '.toneName'), /font: 600 var\(--sb-fs-sub\)/);
  const layout = read('features/storyboard/StoryboardLayout.module.css');
  assert.match(cssRule(layout, '.styleControlsWrap'), /justify-self: end/);
  assert.match(layout, /\.drawerOpen \.styleControlsWrap \{ justify-self: start/);
  assert.match(cssRule(layout, '.continueBar'), /right: var\(--sb-page-edge\)/);
  assert.match(cssRule(layout, '.page:global(.sb-page) > :global(.page-head)'), /width: 100%; margin-inline: 0/);
  assert.match(cssRule(tone, '.slide'), /grid-area: 1 \/ 1/);
  assert.equal(44 + 6 + (width - 2 * 44 - 2 * 6) / 2, width / 2);
});

for (const [kind, usage] of [['first'], ['wear', 'styling'], ['wear', 'fit'], ['fabric'], ['color_product']]) {
  test(`WOC8 R2 ${kind}/${usage || ''} examples precede the separate garment color section`, () => {
    const block = blocks.find(b => b.source === 'ai' && b.pageSectionKind === kind && (!usage || b.pageSectionUsage === usage));
    assert.ok(block);
    const html = render(PhotoPanel, { block, blocks, catalogs, clothingType: 'top', exampleGender: 'women', colorOpts: [], detailColorOpts: [], onChange() {} });
    const marker = kind === 'color_product' ? '섹션 촬영 방식' : kind === 'fabric' ? '예시 사진' : 'photoGallery';
    const gallery = html.indexOf(marker), color = html.indexOf('의류 색상 선택');
    assert.ok(gallery >= 0, `${kind} examples exist`); assert.ok(color > gallery, `${kind} color choices follow examples`);
    const single = render(PhotoPanel, { block, blocks, catalogs: { ...catalogs, detailProduct: { ...product, colors: [product.colors[0]] } },
      clothingType: 'top', exampleGender: 'women', colorOpts: [], detailColorOpts: [], onChange() {} });
    assert.doesNotMatch(single, /의류 색상 선택|garmentColorField/);
  });
}

test('WOC8 R2 color choices have their own separator and fixed ghost settings stay readonly', () => {
  const panel = read('features/storyboard/StoryboardPanels.module.css');
  assert.match(panel, /\.garmentColorField:global\(\.insp-sec\) \{[^}]*margin-top: var\(--sp-20\)[^}]*border-top: 1px solid var\(--ring\)/);
  const block = blocks.find(b => b.pageSectionKind === 'color_product');
  const html = render(PhotoPanel, { block, blocks, catalogs, clothingType: 'top', fixedProduct: true });
  assert.doesNotMatch(html, /의류 색상 선택|garmentColorField/); assert.match(html, /기준 색상/);
});

test('WOC8 R2 the card-width header is a sibling of the only stage scrolling body', async () => {
  const stage = await storyboardStage([stagePhoto('one')]);
  const body = nodes(stage).find(n => n.props?.className?.split(' ').includes('stageBody'));
  const head = nodes(stage).find(n => n.props?.className === 'stageHead');
  assert.equal(stage.props['data-storyboard-scroll'], undefined);
  assert.equal(body.props['data-storyboard-scroll'], true);
  assert.ok(!nodes(body).includes(head));
  const css = read('features/storyboard/StoryboardLayout.module.css');
  assert.match(cssRule(css, '.stage'), /display: flex[^}]*flex-direction: column[^}]*overflow: hidden/);
  assert.doesNotMatch(cssRule(css, '.stage'), /overflow-y: auto|scrollbar-gutter/);
  assert.match(cssRule(css, '.stageHead'), /flex: none/);
  assert.match(cssRule(css, '.stageBody'), /min-height: 0[^}]*overflow-y: auto[^}]*scrollbar-gutter: stable/);
});

test('WOC8 R2 section changes remount just the scroll body while tone updates preserve it', async () => {
  const bodyOf = tree => nodes(tree).find(n => n.props?.className?.split(' ').includes('stageBody'));
  const first = bodyOf(await storyboardStage([stagePhoto('one')]));
  assert.equal(first.key, 'emo.wear_styling');
  const tinted = bodyOf(await storyboardStage([stagePhoto('one')], { toneColor: '#edf4ff' }));
  assert.equal(tinted.key, first.key);
  const second = bodyOf(await storyboardStage([stagePhoto('two', { pageSectionKey: 'emo.wear_2' })]));
  assert.equal(second.key, 'emo.wear_2'); assert.notEqual(second.key, first.key);
});
