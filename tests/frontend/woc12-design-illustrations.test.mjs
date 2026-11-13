import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';

let vite, Illustration;
before(async () => {
  vite = await createServer({ configFile: false, plugins: [react()], logLevel: 'silent',
    optimizeDeps: { noDiscovery: true, include: [] },
    server: { middlewareMode: true, watch: null, ws: false }, appType: 'custom' });
  ({ StoryboardDesignIllustration: Illustration } = await vite.ssrLoadModule('/src/features/storyboard/StoryboardDesignIllustration.jsx'));
});
after(() => vite?.close());

const render = props => {
  assert.equal(typeof Illustration, 'function', 'fixed silver illustration component exists');
  return renderToStaticMarkup(React.createElement(Illustration, props));
};
const paper = 'var(--bg-1)', photo = 'var(--sb-design-silver-photo)', pale = 'var(--sb-design-silver-pale)';
const title = 'var(--sb-design-silver-title)', text = 'var(--sb-design-silver-text)', outer = 'var(--sb-design-silver-outer)';
// First six: wire-silver.html geometry. Fabric pair: catalog layout in the same grammar.
// Tuple = x, y, width, height, rx, fill.
const variants = [
  { kind: 'first', designId: 'P01-D09', shape: 'first-centered', rects: [
    [0, 0, 150, 130, 0, paper], [14, 10, 122, 110, 2, photo],
    [47, 54, 56, 4.6, 2.3, paper], [38, 65, 74, 2.6, 1.3, paper], [50, 72, 50, 2.6, 1.3, paper],
  ] },
  { kind: 'first', designId: 'P01-SHOW-01', shape: 'first-product-name', rects: [
    [0, 0, 150, 130, 0, paper], [14, 10, 122, 88, 2, photo],
    [14, 106, 46, 4.6, 2.3, title], [14, 116, 72, 2.6, 1.3, text],
  ] },
  { kind: 'wear', decoration: 'basic', designId: 'P02-D01', shape: 'wear-basic', rects: [
    [0, 0, 150, 130, 0, paper], [24, 10, 102, 62, 2, photo], [24, 76, 102, 54, 2, photo],
  ] },
  { kind: 'wear', decoration: 'frame', designId: 'WEAR-STYLING-FRAME-COLUMN-02', shape: 'wear-frame', rects: [
    [0, 0, 150, 130, 0, paper], [0, 0, 150, 130, 0, outer], [30, 10, 90, 110, 0, paper],
    [58, 17, 34, 2.4, 1.2, text], [38, 25, 74, 40, 2, photo], [38, 69, 74, 40, 2, photo], [62, 113, 26, 2, 1, text],
  ] },
  { kind: 'points', designId: 'PT-PHOTO-CARD-01', shape: 'points-card', rects: [
    [0, 0, 150, 130, 0, pale], [24, 12, 102, 106, 0, paper], [24, 12, 102, 70, 2, photo],
    [55, 92, 40, 4.4, 2.2, title], [46, 103, 58, 2.6, 1.3, text], [54, 110, 42, 2.6, 1.3, text],
  ] },
  { kind: 'points', designId: 'PT-TEXT-PHOTO-01', shape: 'points-text-first', rects: [
    [0, 0, 150, 130, 0, paper], [14, 14, 46, 4.6, 2.3, title],
    [14, 26, 86, 2.6, 1.3, text], [14, 33, 64, 2.6, 1.3, text], [14, 44, 122, 74, 2, photo],
  ] },
  { kind: 'fabric', designId: 'T03-D01', shape: 'fabric-side', rects: [
    [0, 0, 150, 130, 0, paper], [14, 12, 34, 2.6, 1.3, text], [14, 22, 68, 4.6, 2.3, title],
    [14, 40, 58, 58, 2, photo], [82, 45, 54, 2.6, 1.3, text], [82, 52, 46, 2.6, 1.3, text],
    [82, 59, 50, 2.6, 1.3, text], [82, 78, 40, 2.6, 1.3, text], [82, 86, 48, 2, 1, title], [14, 110, 90, 2.6, 1.3, text],
  ] },
  { kind: 'fabric', designId: 'T03-D06', shape: 'fabric-overlay', rects: [
    [0, 0, 150, 130, 0, paper], [31, 10, 88, 110, 2, photo],
    [39, 20, 46, 4.6, 2.3, paper], [39, 30, 56, 2.6, 1.3, paper],
    [39, 87, 44, 2.6, 1.3, paper], [39, 98, 63, 2.6, 1.3, paper], [39, 105, 52, 2.6, 1.3, paper],
  ] },
];
const rectsOf = html => [...html.matchAll(/<rect\b([^>]*)>/g)].map(([, attributes]) => {
  const attrs = Object.fromEntries([...attributes.matchAll(/([\w-]+)="([^"]*)"/g)].map(([, key, value]) => [key, value]));
  return [...['x', 'y', 'width', 'height', 'rx'].map(key => Number(attrs[key] || 0)), attrs.fill];
});

for (const variant of variants) test(`WOC12 ${variant.shape} renders its fixed silver composition`, () => {
  const html = render(variant);
  assert.match(html, /viewBox="0 0 150 130"/);
  assert.ok(html.includes(`data-design-illustration="${variant.shape}"`));
  assert.deepEqual(rectsOf(html), variant.rects);
});

for (const [kind, base, paired] of [
  ['first', 'P01-D09', 'P01-D09-G'], ['first', 'P01-SHOW-01', 'P01-SHOW-01-G'],
  ['fabric', 'T03-D01', 'T03-D01-P'], ['fabric', 'T03-D06', 'T03-D06-P'],
]) test(`WOC12 ${paired} keeps the same illustration as its base design`, () => {
  assert.equal(render({ kind, designId: paired }), render({ kind, designId: base }));
});

for (const decoration of ['basic', 'frame']) test(`WOC12 ${decoration} ignores layout, frame count, photos and theme`, () => {
  const expected = render({ kind: 'wear', decoration });
  for (const layout of ['column', 'twoColumn', 'heroPair']) for (const framePhotos of [1, 2, 3]) {
    const layoutId = { column: 'COLUMN', twoColumn: '2COL', heroPair: 'HERO-PAIR' }[layout];
    assert.equal(render({ kind: 'wear', decoration, designId: `WEAR-STYLING-FRAME-${layoutId}-0${framePhotos}`,
      layout, framePhotos, blocks: [{ ownImages: ['/my-photo.jpg'] }], theme: { colorMode: 'point' } }), expected);
  }
});

test('WOC12 illustrations contain only decorative SVG rectangles with token fills and no visible words or images', () => {
  const fills = new Set([paper, photo, pale, title, text, outer]);
  for (const variant of variants) {
    const html = render(variant);
    assert.match(html, /^<svg\b[^>]*aria-hidden="true"/);
    assert.match(html, /focusable="false"/);
    assert.deepEqual(new Set([...html.matchAll(/<\/?([\w-]+)/g)].map(([, tag]) => tag)), new Set(['svg', 'rect']));
    assert.equal(html.replace(/<[^>]*>/g, ''), '');
    assert.doesNotMatch(html, /\s(?:style|href|src)=|#[\da-f]{3,8}\b|https?:|data:image|person|human|silhouette/i);
    assert.ok(rectsOf(html).every(rect => fills.has(rect[5])));
  }
});

test('WOC12 unsupported designs do not receive a misleading fixed composition', () => {
  assert.equal(render({ kind: 'review', designId: 'I09-D06' }), '');
  assert.equal(render({ kind: 'first', designId: 'unknown' }), '');
});
