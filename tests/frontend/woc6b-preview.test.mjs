import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';
import { seedStoryboard } from '../../src/lib/pageTemplates/seed.js';
import { addTemplateSection, changeTemplateSectionDesign } from '../../src/lib/pageTemplates/sectionActions.js';
import { sectionRuns } from '../../src/lib/pageTemplates/sectionRuns.js';
import { sectionDesignPreviewSample } from '../../src/lib/pageTemplates/designPreview.js';
import { assemblePage, pointPhotosForRun } from '../../src/lib/pageTemplates/assemble.js';
import { defaultPageStyle } from '../../src/lib/pageTemplates/pageStyle.js';
import { resolvePageTokens } from '../../src/lib/pageTemplates/tokens.js';
import details from '../../src/mock/detailRecommendationsFixture.json' with { type: 'json' };

let vite, Preview;
before(async () => {
  vite = await createServer({ configFile: false, plugins: [react()], logLevel: 'silent',
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    optimizeDeps: { noDiscovery: true, include: [] }, server: { middlewareMode: true, watch: null, ws: false }, appType: 'custom' });
  ({ StoryboardPreview: Preview } = await vite.ssrLoadModule('/src/features/storyboard/StoryboardPreview.jsx'));
});
after(() => vite?.close());
const product = { name: '테스트 니트', clothingType: 'top', colors: [{ id: 'base', name: '블루', swatchId: 'blue', isBase: true,
  images: [{ slot: 'Front', src: '/front.jpg' }, { slot: 'Detail', src: '/detail.jpg' }] }] };
const analysis = { targetGenders: ['women'], pageStyle: defaultPageStyle(), detailRecommendations: details };
const run = blocks => sectionRuns(blocks).find(r => r.kind === 'points');
const images = page => page.flatMap(b => b.elements).filter(e => e.type === 'image');

for (const source of ['details', 'wornCrop']) test(`WOC6B points picker samples two ${source} photos without changing the actual page`, () => {
  const inputAnalysis = source === 'details' ? analysis : { ...analysis, detailRecommendations: { version: 1, status: 'unavailable', candidates: [] } };
  let blocks = seedStoryboard(product, inputAnalysis);
  if (!run(blocks)) blocks = addTemplateSection(blocks, product, inputAnalysis, 'points');
  const current = run(blocks), before = structuredClone(blocks);
  assert.equal(pointPhotosForRun(current.blocks, blocks).length, 3);
  const sampled = sectionDesignPreviewSample(blocks, current.key), sampledRun = run(sampled);
  assert.equal(pointPhotosForRun(sampledRun.blocks, sampled).length, 2);
  const actualPage = assemblePage({ storyboard: blocks, product, analysis: inputAnalysis }).filter(b => b.pageSectionKey === current.key);
  const samplePage = assemblePage({ storyboard: sampled, product, analysis: inputAnalysis }).filter(b => b.pageSectionKey === current.key);
  assert.equal(images(actualPage).length, 3); assert.equal(images(samplePage).length, 2);
  assert.deepEqual(blocks, before);
  const widths = ['PT-COLUMN-01', 'P06-D01'].map(id => images(assemblePage({ storyboard: sectionDesignPreviewSample(changeTemplateSectionDesign(blocks, current.key, id, { product }), current.key), product, analysis: inputAnalysis }).filter(b => b.pageSectionKey === current.key)).map(e => e.w));
  assert.ok(widths[0][0] > widths[1][0]); assert.equal(widths[0].length, 2); assert.equal(widths[1].length, 2);
});

test('WOC6B point cards use a distinct panel paint only in wireframes, retaining tone preview', () => {
  const blocks = seedStoryboard(product, analysis), current = run(blocks);
  for (const designId of ['PT-COLUMN-CARD-01', 'PT-BELOW-CARD-01']) {
    const storyboard = changeTemplateSectionDesign(blocks, current.key, designId, { product });
    const page = assemblePage({ storyboard, product, analysis }).filter(b => b.pageSectionKey === current.key);
    const html = renderToStaticMarkup(React.createElement(Preview, { blocks: page, wireframe: true }));
    assert.match(html, /fill="var\(--sb-wire-panel\)"/);
    assert.match(html, /previewWirePhoto/); assert.match(html, /previewWireCanvas/);
    const colors = resolvePageTokens({ ...analysis.pageStyle, colorMode: 'point' }, 'blue').colors;
    const tinted = assemblePage({ storyboard, product, analysis, pageStyle: { ...analysis.pageStyle, colorMode: 'point' } }).filter(b => b.pageSectionKey === current.key);
    const toneHtml = renderToStaticMarkup(React.createElement(Preview, { blocks: tinted, wireframe: true, toneColors: colors }));
    assert.match(toneHtml, /color-mix\(in srgb, var\(--sb-wire-panel\)/);
    assert.ok(toneHtml.includes(colors.accentBg));
    assert.doesNotMatch(renderToStaticMarkup(React.createElement(Preview, { blocks: page })), /sb-wire-panel/);
  }
  const css = readFileSync('src/styles/tokens.css', 'utf8');
  const token = name => { const raw = css.match(new RegExp(`${name}:\\s*([^;]+)`))?.[1]; assert.ok(raw, name); return raw.startsWith('var(') ? token(raw.slice(4,-1)) : raw; };
  const value = name => parseInt(token(name).slice(1,3), 16);
  assert.ok(value('--sb-wire-panel') > value('--sb-wire-photo'));
  assert.ok(value('--sb-wire-panel') < 245, 'panel distinct from near-white canvas');
});

test('WOC6B frame backdrop is blurred in real previews, dark in wireframes and never another photo position', async () => {
  const { storyboardPositions } = await import('../../src/lib/pageTemplates/storyboardPositions.js');
  const page = [{ id: 'frame', pageSectionKey: 'emo.wear_styling', h: 900, bg: '#ffffff', elements: [
    { id: 'background', type: 'image', x: 0, y: 0, w: 1000, h: 900, src: '/wear.jpg', blur: 48, sourceBlockId: 'wear1', slotKey: 'wear_styling.g1' },
    { id: 'photo', type: 'image', x: 220, y: 100, w: 560, h: 700, src: '/wear.jpg', sourceBlockId: 'wear1', slotKey: 'wear_styling.g1' },
  ] }];
  assert.equal(storyboardPositions(page, 'emo.wear_styling').length, 1);
  const html = renderToStaticMarkup(React.createElement(Preview, { blocks: page, onPositionClick() {} }));
  assert.match(html, /feGaussianBlur stdDeviation="48"/);
  assert.equal((html.match(/data-preview-position=/g) || []).length, 1);
  assert.doesNotMatch(html, /style=|foreignObject/);
  const wire = renderToStaticMarkup(React.createElement(Preview, { blocks: page, wireframe: true }));
  assert.match(wire, /previewWireBackdrop/); assert.doesNotMatch(wire, /feGaussianBlur|<image/);
  page[0].elements[0].blur = 0;
  assert.equal(storyboardPositions(page, 'emo.wear_styling').length, 1);
});

test('WOC6B R2 frame preview uses only the connected shop name without changing non-frame attribution', async () => {
  const { assembleStoryboardPreview } = await import('../../src/features/storyboard/storyboardPreview.js');
  const initial = seedStoryboard(product, analysis), current = sectionRuns(initial).find(r => r.usage === 'styling');
  const framed = changeTemplateSectionDesign(initial, current.key, 'WEAR-STYLING-FRAME-COLUMN-02', { product });
  const catalogs = { detailProduct: product, analysis, shopName: '테스트몰', profile: { display_name: '회원 실명' } };
  const pages = assembleStoryboardPreview({ storyboard: framed, catalogs });
  assert.ok(pages.filter(b => b.pageSectionKey === current.key).flatMap(b => b.elements).some(el => el.copyRole === 'who' && el.text === '테스트몰'));
  assert.deepEqual(assembleStoryboardPreview({ storyboard: initial, catalogs }), assembleStoryboardPreview({ storyboard: initial, catalogs: { ...catalogs, shopName: undefined } }));
  const source = readFileSync('src/features/storyboard/Storyboard.jsx', 'utf8');
  assert.match(source, /shopName = useStoryboardShopName\(blocks\)/);
  assert.doesNotMatch(source, /profileName|account\?\.name/);
  assert.ok(!JSON.stringify(pages).includes('회원 실명'));
  const unnamed = assembleStoryboardPreview({ storyboard: framed, catalogs: { ...catalogs, shopName: undefined } });
  assert.ok(!unnamed.filter(b => b.pageSectionKey === current.key).flatMap(b => b.elements).some(el => el.copyRole === 'who'));
});
