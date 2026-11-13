import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import { defaultPageStyle } from '../../src/lib/pageTemplates/pageStyle.js';
import { selectFontStyle, selectColorModeStyle } from '../../src/lib/pageTemplates/pageStyle.js';
import { pageTemplatesCatalog as catalog } from '../../src/lib/pageTemplates/catalog.js';
let vite, Controls, Theme, Preview, Stage, PageList, createQueue, waitForStyle;
before(async () => {
  vite = await createServer({ configFile: false, plugins: [react()], logLevel: 'silent',
    optimizeDeps: { noDiscovery: true, include: [] }, resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, watch: null, ws: false }, appType: 'custom' });
  ({ StoryboardStyleControls: Controls } = await vite.ssrLoadModule('/src/features/storyboard/StoryboardStyleControls.jsx'));
  ({ StoryboardThemePicker: Theme } = await vite.ssrLoadModule('/src/features/storyboard/StoryboardThemePicker.jsx'));
  ({ StoryboardPreview: Preview } = await vite.ssrLoadModule('/src/features/storyboard/StoryboardPreview.jsx'));
  ({ StoryboardStage: Stage } = await vite.ssrLoadModule('/src/features/storyboard/StoryboardStage.jsx'));
  ({ StoryboardPageList: PageList } = await vite.ssrLoadModule('/src/features/storyboard/StoryboardPageList.jsx'));
  ({ createStoryboardStyleSaveQueue: createQueue } = await vite.ssrLoadModule('/src/features/storyboard/storyboardPageStyle.js'));
  ({ waitForAnalysisEditSave: waitForStyle } = await vite.ssrLoadModule('/src/features/product-input/saveRouting.js'));
});
test('WOC7 rapid actual font/tone handlers preserve both fields and re-entry waits for all queued selections', async () => {
  const source = readFileSync('src/features/storyboard/Storyboard.jsx', 'utf8');
  const start = source.indexOf('  const changePageStyle ='), end = source.indexOf('  const tone =', start);
  assert.ok(start >= 0 && end > start);
  const calls = [], done = [], errors = [], projectId = 'woc7-style-queue';
  const styleSaves = createQueue(style => new Promise((resolve, reject) => { calls.push(style); done.push({ resolve, reject }); }), { projectId });
  const initial = { ...defaultPageStyle(), templateId: 'show' };
  let current = { analysis: { pageStyle: initial } };
  const scope = { styleSaves, projectId, selectFontStyle, selectColorModeStyle,
    styleValueRef: { current: initial }, styleRequest: { current: 0 }, useAppStore: { getState: () => ({ projectId }) },
    setPageStyleError: error => errors.push(error), setCatalogs: fn => { current = fn(current); } };
  const choose = new Function('scope', `with(scope) { ${source.slice(start, end)}; return changePageStyle; }`)(scope);
  choose({ fontStyle: 'serif' }); choose({ colorMode: 'point' }); choose({ fontStyle: 'boldGothic' }); choose({ colorMode: 'mono' });
  assert.equal(current.analysis.pageStyle.fontStyle, 'boldGothic'); assert.equal(current.analysis.pageStyle.colorMode, 'mono');
  let reopened = false; const reopening = waitForStyle(projectId).then(() => { reopened = true; });
  for (let index = 0; index < 4; index++) {
    for (let i = 0; i < 8; i++) await Promise.resolve();
    assert.equal(calls.length, index + 1); assert.equal(reopened, false);
    if (index === 0) done[index].reject(new Error('stale failed font save')); else done[index].resolve();
  }
  await styleSaves.wait(); await reopening;
  assert.deepEqual(calls.map(s => [s.templateId, s.fontStyle, s.colorMode]), [
    ['show', 'serif', 'mono'], ['show', 'serif', 'point'], ['show', 'boldGothic', 'point'], ['show', 'boldGothic', 'mono'],
  ]);
  assert.ok(errors.every(error => !error), 'an earlier failed selection cannot overwrite the latest state');
  assert.equal(reopened, true);
});
test('WOC7 wireframe candidates tint only catalog accent surfaces and retain gray photos', () => {
  const paint = catalog.pointPalette.blue;
  const blocks = [{ id: 'tone', h: 200, bg: paint.accentBg, elements: [
    { id: 'panel', type: 'shape', shape: 'rect', x: 0, y: 0, w: 200, h: 100, fill: paint.accentBg },
    { id: 'dark', type: 'shape', shape: 'rect', x: 200, y: 0, w: 200, h: 100, fill: '#111111' },
    { id: 'photo', type: 'image', x: 0, y: 100, w: 100, h: 100, src: '/actual-photo.jpg' },
  ] }];
  const on = render(Preview, { blocks, wireframe: true, toneColors: paint });
  assert.equal((on.match(new RegExp(`fill="${paint.accentBg}"`, 'g')) || []).length, 2);
  assert.match(on, /previewWirePhoto/); assert.doesNotMatch(on, /actual-photo.jpg|fill="#111111"/);
  assert.doesNotMatch(render(Preview, { blocks, wireframe: true }), new RegExp(`fill="${paint.accentBg}"`));
});
test('WOC7 selected stage band and outline use the same page colors immediately', () => {
  const block = { id: 'hero', source: 'ai', pageSectionKey: 'emo.first', pageSectionKind: 'first', pageDesignId: 'P01-D09', slotKey: 'first.hero' };
  const run = { key: 'emo.first', kind: 'first', designId: 'P01-D09', blocks: [block] };
  const page = [{ id: 'p', pageSectionKey: run.key, h: 100, bg: catalog.pointPalette.blue.accentBg, elements: [] }];
  const old = page.map(b => ({ ...b, bg: '#ffffff' }));
  const stage = render(Stage, { run, blocks: [block], page, catalogs: {}, photoOrdinals: new Map([['hero', 1]]), toneColor: catalog.pointPalette.blue.accentBg });
  assert.match(stage, new RegExp(`fill="${catalog.pointPalette.blue.accentBg}"`));
  const props = { runs: [run], sectionRefs: { current: new Map() } };
  assert.doesNotMatch(render(PageList, { ...props, page: old }), new RegExp(`fill="${catalog.pointPalette.blue.accentBg}"`));
  assert.match(render(PageList, { ...props, page }), new RegExp(`fill="${catalog.pointPalette.blue.accentBg}"`));
});
after(() => vite?.close());
const product = swatch => ({ colors: [{ id: 'base', isBase: true, swatchId: swatch }] });
const render = (component, props) => renderToStaticMarkup(React.createElement(component, props));

test('WOC9 two adjacent controls show one tone specimen with token circles', () => {
  for (const [colorMode, label] of [['mono', '기본'], ['point', '의류 색감']]) {
    const html = render(Controls, { pageStyle: { ...defaultPageStyle(), colorMode }, product: product('blue') });
    assert.match(html, />글자 스타일</); assert.match(html, />페이지 색감</);
    assert.match(html, new RegExp(`aria-label="${label}" data-color-mode="${colorMode}"`));
    assert.match(html, /aria-label="이전 페이지 색감"/); assert.match(html, /aria-label="다음 페이지 색감"/);
    assert.equal((html.match(/<circle /g) || []).length, 3);
    if (colorMode === 'point') {
      assert.match(html, new RegExp(`fill="${catalog.pointPalette.blue.accentBg}"`));
      assert.match(html, new RegExp(`fill="${catalog.pointPalette.blue.accentInk}"`));
    }
    assert.doesNotMatch(html, /\sstyle="|aria-pressed/);
  }
  const css = readFileSync('src/features/storyboard/StoryboardTone.module.css', 'utf8');
  assert.match(css, /grid-auto-columns: var\(--sb-style-controls-width\)/);
  assert.doesNotMatch(css, /word-break:/);
});
test('WOC7 missing or neutral base hides the entire tone row even for a stored point choice', () => {
  for (const p of [undefined, { colors: [] }, ...[null, 'null', 'white', 'gray', 'black', 'unknown'].map(product)]) {
    const html = render(Controls, { pageStyle: { ...defaultPageStyle(), colorMode: 'point' }, product: p });
    assert.doesNotMatch(html, /페이지 색감|의류 색감/); assert.match(html, /글자 스타일/);
    assert.equal((html.match(/role="group"/g) || []).length, 1);
  }
  assert.match(render(Controls, { pageStyle: defaultPageStyle(), product: { colors: [{ swatchId: 'blue' }] } }), /페이지 색감/);
});
test('WOC7 template specimen uses current template role fonts, case and tracking', () => {
  for (const template of catalog.templates) {
    const html = render(Theme, { templateId: template.id, value: 'template' });
    const label = template.id === 'show' ? '기본' : '다정';
    const card = html.match(new RegExp(`<span[^>]*title="${label}"[\\s\\S]*?<\\/span>`))?.[0];
    assert.ok(card);
    for (const role of ['labelEn', 'titleKo']) {
      const font = template.fonts[role];
      assert.match(card, new RegExp(`font-family="${font.font}"`));
      assert.match(card, new RegExp(`font-weight="${font.weight}"`));
      assert.ok(card.includes(`letter-spacing="${font.trackingEm}em"`));
    }
    assert.match(card, template.id === 'show' ? />WEARLESS</ : />wearless</);
    assert.doesNotMatch(card, /\sstyle="/);
  }
});
