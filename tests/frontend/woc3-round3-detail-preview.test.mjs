import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import * as details from '../../src/lib/detailRecommendations.js';
import * as preview from '../../src/features/storyboard/storyboardPreview.js';
import { selectPublicAnalysisPhotos } from '../../src/lib/publicAnalysisPhotos.js';
import { assemblePage } from '../../src/lib/pageTemplates/assemble.js';
import { sectionRuns, visibleSectionRuns } from '../../src/lib/pageTemplates/sectionRuns.js';

let vite, Stage, PageList, buildDraft;
const locationDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'location');
before(async () => {
  vite = await createServer({ configFile: false, plugins: [react()], optimizeDeps: { noDiscovery: true, include: [] },
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, watch: null, ws: false }, appType: 'custom', logLevel: 'silent' });
  ({ StoryboardStage: Stage } = await vite.ssrLoadModule('/src/features/storyboard/StoryboardStage.jsx'));
  ({ StoryboardPageList: PageList } = await vite.ssrLoadModule('/src/features/storyboard/StoryboardPageList.jsx'));
  ({ buildDraft } = await vite.ssrLoadModule('/src/mock/db.js'));
});
after(async () => {
  await vite?.close();
  if (locationDescriptor) Object.defineProperty(globalThis, 'location', locationDescriptor); else delete globalThis.location;
});
const requirePreview = () => assert.equal(typeof preview.assembleStoryboardPreview, 'function', 'a separate preview assembly path exists');
const full = templateId => {
  globalThis.location = { search: `?mockTemplate=${templateId}&mockDetail=full` };
  const draft = buildDraft(), catalogs = { detailProduct: draft.product, analysis: draft.analysis,
    detailRecommendations: draft.analysis.detailRecommendations, genExamples: [] };
  return { draft, catalogs, input: { storyboard: draft.storyboard, product: draft.product, analysis: draft.analysis, catalogs } };
};
const detailCards = blocks => blocks.filter(block => block.source === 'ai' && block.shot === 'detail');
const images = page => page.flatMap(block => block.elements).filter(el => el.type === 'image');
const unescapeAttribute = value => value.replace(/&#x([\da-f]+);/gi, (_, hex) => String.fromCodePoint(parseInt(hex, 16)))
  .replace(/&quot;/g, '"').replace(/&amp;/g, '&');
const attrs = str => Object.fromEntries([...str.matchAll(/([\w-]+)="([^"]*)"/g)].map(([, key, value]) => [key, unescapeAttribute(value)]));
const svgImages = html => [...html.matchAll(/<image\b([^>]+)>/g)].map(match => attrs(match[1]));
const imageTuple = image => [image.href, image.width, image.height, image.x, image.y];
const expectedTuple = el => [el.src, String(el.crop.iw), String(el.crop.ih), String(-el.crop.ox), String(-el.crop.oy)];

for (const template of ['emo', 'show']) test(`R3-2 ${template} mock full resolves four original regions in stage, outline and overview`, () => {
  requirePreview(); assert.equal(typeof details.detailTargetPreview, 'function');
  const { draft, catalogs, input } = full(template), saved = structuredClone(draft.storyboard);
  const base = draft.product.colors.find(color => color.isBase), sources = selectPublicAnalysisPhotos(base.images);
  const page = preview.assembleStoryboardPreview(input), cards = detailCards(draft.storyboard);
  assert.equal(cards.length, 4);
  const cropped = cards.map(card => {
    const candidate = draft.analysis.detailRecommendations.candidates.find(item => item.id === card.detailTargetId);
    const target = details.detailTargetPreview(card, draft.product, draft.analysis.detailRecommendations);
    assert.equal(target.src, sources[candidate.sourceIndex].src);
    assert.deepEqual(target.region, candidate.region);
    const legacy = details.detailTargetPresentation(card, draft.product, draft.analysis.detailRecommendations);
    assert.equal(legacy.src, base.images.find(image => image.slot === 'Front').src, 'existing retry/editor source behavior is unchanged');
    assert.equal(legacy.label, candidate.label); assert.deepEqual(Object.keys(legacy).sort(), ['label', 'src', 'target']);
    const el = images(page).find(image => image.sourceBlockId === card.id);
    assert.equal(el.src, target.src); assert.equal(el.previewDetailCrop, true); assert.ok(el.crop);
    return el;
  });
  assert.equal(new Set(cropped.map(el => JSON.stringify([el.src, el.crop]))).size, 4);
  const common = { blocks: draft.storyboard, page, catalogs,
    photoOrdinals: new Map(draft.storyboard.filter(block => block.source !== 'text').map((block, index) => [block.id, index + 1])) };
  for (const kind of ['points', 'fabric']) {
    const run = sectionRuns(draft.storyboard).find(run => run.kind === kind);
    const html = renderToStaticMarkup(React.createElement(Stage, { ...common, run, number: 3 }));
    const expected = cropped.filter(el => run.blocks.some(block => block.id === el.sourceBlockId));
    assert.deepEqual(svgImages(html).map(imageTuple), expected.map(expectedTuple));
    assert.ok(svgImages(html).length === (kind === 'points' ? 3 : 1));
  }
  const outline = renderToStaticMarkup(React.createElement(PageList, { runs: visibleSectionRuns(draft.storyboard), page,
    activeKey: `${template}.points`, sectionRefs: { current: new Map() } }));
  const overview = renderToStaticMarkup(React.createElement(Stage, { ...common, overview: true }));
  for (const html of [outline, overview]) {
    const actual = svgImages(html).map(imageTuple).map(tuple => JSON.stringify(tuple));
    for (const el of cropped) assert.ok(actual.includes(JSON.stringify(expectedTuple(el))), `${el.slotKey} renders the same cropped original`);
  }
  assert.deepEqual(draft.storyboard, saved, 'preview must not add fields to saved blocks');
});

for (const missing of ['target', 'source', 'index', 'invalid-region']) test(`R3-2 ${missing} falls back to the legacy whole-photo source`, () => {
  requirePreview(); assert.equal(typeof details.detailTargetPreview, 'function');
  const { draft, catalogs, input } = full('emo'), fabric = detailCards(draft.storyboard).find(card => card.pageSectionKind === 'fabric');
  const target = draft.analysis.detailRecommendations.candidates.find(item => item.id === fabric.detailTargetId);
  if (missing === 'target') fabric.detailTargetId = 'missing';
  if (missing === 'source') draft.product.colors[0].images[target.sourceIndex].src = null;
  if (missing === 'index') target.sourceIndex = 50;
  if (missing === 'invalid-region') target.region = { x: .9, y: 0, w: .3, h: .5 };
  assert.equal(details.detailTargetPreview(fabric, draft.product, catalogs.detailRecommendations), null);
  const page = preview.assembleStoryboardPreview(input), image = images(page).find(el => el.sourceBlockId === fabric.id);
  assert.equal(image.src, details.detailTargetPresentation(fabric, draft.product, catalogs.detailRecommendations).src);
  assert.equal(image.previewDetailCrop, undefined); assert.equal(image.crop, undefined);
});

test('R3-2 generated photos keep their normal assembly instead of being replaced by original-region previews', () => {
  requirePreview(); const { draft, input } = full('emo'), fabric = detailCards(draft.storyboard).find(card => card.pageSectionKind === 'fabric');
  const cutResults = [{ blockId: fabric.id, imageUrl: '/finished-fabric.webp', width: 900, height: 900 }];
  const page = preview.assembleStoryboardPreview({ ...input, cutResults });
  const actual = images(page).find(image => image.sourceBlockId === fabric.id);
  const expected = images(assemblePage({ ...input, cutResults })).find(image => image.sourceBlockId === fabric.id);
  assert.deepEqual(actual, expected); assert.equal(actual.src, '/finished-fabric.webp');
  assert.equal(actual.previewDetailCrop, undefined);
});

test('R3-2 measured source dimensions preserve a landscape original and its native pixel limit', () => {
  requirePreview(); const { draft, catalogs, input } = full('emo');
  const fabric = detailCards(draft.storyboard).find(card => card.pageSectionKind === 'fabric');
  const candidate = draft.analysis.detailRecommendations.candidates.find(item => item.id === fabric.detailTargetId);
  selectPublicAnalysisPhotos(draft.product.colors[0].images)[candidate.sourceIndex].src = '/landscape.jpg';
  const target = details.detailTargetPreview(fabric, draft.product, catalogs.detailRecommendations);
  catalogs.detailPreviewDimensions = { [target.src]: { width: 400, height: 200 } };
  const saved = structuredClone(draft.product);
  const image = images(preview.assembleStoryboardPreview(input)).find(el => el.sourceBlockId === fabric.id);
  assert.deepEqual(image.crop, { iw: 400, ih: 200, ox: 0, oy: 0 });
  assert.deepEqual(draft.product, saved, 'measured dimensions remain transient');
});

test('R3-2 measured bitmap dimensions replace an explicitly unknown source-size estimate', () => {
  requirePreview(); const { draft, catalogs, input } = full('emo');
  const fabric = detailCards(draft.storyboard).find(card => card.pageSectionKind === 'fabric');
  const candidate = draft.analysis.detailRecommendations.candidates.find(item => item.id === fabric.detailTargetId);
  const source = selectPublicAnalysisPhotos(draft.product.colors[0].images)[candidate.sourceIndex];
  Object.assign(source, { src: '/bitmap-original.webp', width: 1024, height: 1365, dimensionsKnown: false });
  catalogs.detailPreviewDimensions = { [source.src]: { width: 100, height: 150 } };
  const image = images(preview.assembleStoryboardPreview(input)).find(el => el.sourceBlockId === fabric.id);
  assert.deepEqual(image.crop, { iw: 100, ih: 150, ox: 0, oy: 0 });
});

test('R3-2 measured mock SVG originals keep four distinct regions without a bitmap pixel cap', () => {
  for (const template of ['emo', 'show']) {
    const { draft, catalogs, input } = full(template), measurements = {};
    const targets = detailCards(draft.storyboard).map(card => details.detailTargetPreview(card, draft.product, catalogs.detailRecommendations));
    class SvgImage {
      set src(value) {
        const svg = decodeURIComponent(value.split(',')[1]);
        this.naturalWidth = Number(svg.match(/width='(\d+)'/)[1]);
        this.naturalHeight = Number(svg.match(/height='(\d+)'/)[1]);
        this.onload();
      }
    }
    const cancel = preview.observeDetailPreviewDimensions(targets.map(target => target.src),
      (src, size) => { measurements[src] = size; }, { ImageCtor: SvgImage });
    for (const size of Object.values(measurements)) assert.equal(size.dimensionsKnown, false);
    catalogs.detailPreviewDimensions = measurements;
    const cropped = images(preview.assembleStoryboardPreview(input)).filter(el => el.previewDetailCrop);
    assert.equal(cropped.length, 4);
    assert.equal(new Set(cropped.map(el => JSON.stringify([el.src, el.crop]))).size, 4);
    cancel();
  }
});

for (const [name, size, expected] of [
  ['unknown estimate', {}, { iw: 1333, ih: 1777, ox: 400, oy: 244 }],
  ['explicit aspect estimate', { width: 1024, height: 1365, dimensionsKnown: false }, { iw: 1333, ih: 1777, ox: 400, oy: 244 }],
  ['native 1x', { width: 1000, height: 1500 }, { iw: 1000, ih: 1500, ox: 250, oy: 175 }],
  ['tiny native stays tiny', { width: 100, height: 150 }, { iw: 100, ih: 150, ox: 0, oy: 0 }],
]) test(`R3-2 crop zoom obeys the existing 30% / native limit (${name})`, () => {
  assert.equal(typeof preview.detailPreviewCrop, 'function');
  const target = { ...size, region: { x: .4, y: .2, w: .1, h: .1 } };
  assert.deepEqual(preview.detailPreviewCrop(400, 400, target), expected);
});

test('R3-2 existing worn-region previews remain exactly the same', () => {
  requirePreview();
  const fixture = JSON.parse(readFileSync(new URL('../fixtures/page_templates_cases.json', import.meta.url), 'utf8'));
  const input = structuredClone(fixture.cases.find(item => item.name === 'woc2-emo-crops').input);
  input.storyboard = input.storyboard.filter(block => block.pageSectionKind !== 'fabric');
  const catalogs = { detailProduct: input.product, detailRecommendations: input.analysis.detailRecommendations, analysis: input.analysis, genExamples: [] };
  const expected = assemblePage({ ...input, cutResults: preview.storyboardPreviewCuts(input.storyboard, catalogs) });
  assert.deepEqual(preview.assembleStoryboardPreview({ ...input, cutResults: undefined, catalogs }), expected);
  assert.ok(images(expected).some(image => image.pointItemKind === 'wornCrop'));
});
