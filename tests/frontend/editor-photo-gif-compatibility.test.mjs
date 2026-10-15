import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';
import JSZip from 'jszip';
import { replaceEditorPhoto, transferEditorPhoto } from '../../src/features/editor/editorPhotoEditing.js';
import { convertGifToStill } from '../../src/features/editor/presets/infoPresets.js';
import { mergeEditorImagesIntoWardrobe } from '../../src/features/editor/editorWardrobe.js';
import { packageBlockDownload, packageStorefrontZip } from '../../src/features/editor/editorExport.js';

const gif = { id: 'gif', src: '/v1/assets/gif/file', kind: 'gif', posterSrc: '/v1/assets/poster/file',
  userUploaded: true, wardrobeGroup: 'misc', gifMeta: { width: 640, height: 800, frames: 2, delayMs: 1500, bytes: 19 } };
const photo = { id: 'photo', type: 'image', src: '/photo.jpg', frameSlot: true, x: 40, y: 60, w: 400, h: 500, radius: 12 };
const sourceBlock = () => ({ id: 'source', kind: 'size', h: 800, info: { diagramSrc: photo.src }, elements: [photo] });
let vite, ImagePanel;
before(async () => {
  const root = fileURLToPath(new URL('../..', import.meta.url));
  vite = await createServer({ configFile: false, root, logLevel: 'silent',
    optimizeDeps: { noDiscovery: true, include: [] },
    server: { middlewareMode: true, watch: null, ws: false }, appType: 'custom',
    resolve: { alias: { '@': `${root}/src` } }, esbuild: { jsx: 'automatic' } });
  ({ ImagePanel } = await vite.ssrLoadModule('/src/features/editor/EditorPanels.jsx'));
});
after(() => vite?.close());

function nodes(root) {
  if (!React.isValidElement(root)) return [];
  return [root, ...React.Children.toArray(root.props.children).flatMap(nodes)];
}
test('merged image inspector keeps photo crop/download and GIF information/conversion actions', () => {
  const calls = [];
  const props = { onChange() {}, onLayer() {}, onCrop: value => calls.push(['crop', value]),
    onDownload: value => calls.push(['download', value]), onConvertGif: value => calls.push(['still', value]) };
  const photoTree = ImagePanel({ ...props, el: photo });
  const crop = nodes(photoTree).find(n => n.props.children === '자르기' && n.props.onClick);
  const download = nodes(photoTree).find(n => n.props.children === '원본 다운로드' && n.props.onClick);
  crop.props.onClick(); download.props.onClick();
  assert.deepEqual(calls, [['crop', photo], ['download', photo]]);
  const gifElement = { ...photo, ...gif };
  const gifTree = ImagePanel({ ...props, el: gifElement });
  nodes(gifTree).find(n => n.props.children === '첫 장을 정지 이미지로 사용').props.onClick();
  assert.deepEqual(calls[2], ['still', gifElement]);
  const html = renderToStaticMarkup(React.createElement(ImagePanel, { ...props, el: gifElement }));
  assert.match(html, /image-properties/);
  assert.match(html, /2장/);
  assert.match(html, /캔버스 모양/);
  assert.match(html, /첫 장을 정지 이미지로 사용/);
});

test('photo to GIF to photo replacement keeps the frame and synchronizes then removes GIF facts', () => {
  const swapped = replaceEditorPhoto([sourceBlock()], photo.id, gif);
  const el = swapped[0].elements[0];
  assert.equal(el.w, 400); assert.equal(el.h, 500); assert.equal(el.x, 40); assert.equal(el.y, 60);
  assert.equal(el.kind, 'gif'); assert.deepEqual(el.gifMeta, gif.gifMeta);
  assert.equal(swapped[0].info.diagramSrc, gif.src);
  const restored = replaceEditorPhoto(swapped, photo.id, { src: '/another.jpg' });
  assert.equal(restored[0].info.diagramSrc, '/another.jpg');
  assert.equal(restored[0].elements[0].kind, undefined);
  assert.equal(restored[0].elements[0].posterSrc, undefined);
  assert.equal(restored[0].elements[0].gifMeta, undefined);
});

test('GIF first-frame conversion updates the saved form without changing the photo frame', () => {
  const swapped = replaceEditorPhoto([sourceBlock()], photo.id, gif);
  const still = convertGifToStill(swapped[0], photo.id);
  assert.equal(still.info.diagramSrc, gif.posterSrc);
  assert.equal(still.elements[0].src, gif.posterSrc);
  assert.equal(still.elements[0].gifMeta, undefined);
  assert.equal(still.elements[0].w, 400); assert.equal(still.elements[0].h, 500);
});

test('GIF block transfer and JSON reload retain the original GIF and its poster in the library', () => {
  const swapped = replaceEditorPhoto([sourceBlock()], photo.id, gif);
  const moved = transferEditorPhoto([...swapped, { id: 'target', kind: 'twocol', contentRole: 'custom', h: 800, elements: [] }], {
    elementId: photo.id, sourceBlockId: 'source', targetBlockId: 'target', x: 70, y: 80, emptyId: 'empty',
  });
  assert.equal(moved[0].info.diagramSrc, null);
  assert.equal(moved[0].elements[0].kind, undefined);
  assert.equal(moved[1].elements[0].frameSlot, false);
  const restored = JSON.parse(JSON.stringify(moved));
  const wardrobe = mergeEditorImagesIntoWardrobe({ blocks: restored });
  assert.equal(wardrobe.misc.length, 1);
  assert.equal(wardrobe.misc[0].src, gif.src);
  assert.equal(wardrobe.misc[0].posterSrc, gif.posterSrc);
  assert.deepEqual(wardrobe.misc[0].gifMeta, gif.gifMeta);
});

test('block and full-page ZIPs keep the raster receipt and GIF original separate', async () => {
  const nativeFetch = globalThis.fetch;
  const gifBytes = 'GIF89a-original';
  const calls = [];
  globalThis.fetch = async url => {
    assert.match(String(url), /\/v1\/assets\/gif\/bytes\?e=/);
    return new Response(new Blob([gifBytes], { type: 'image/gif' }), { headers: { 'content-type': 'image/gif' } });
  };
  const sign = async (blob, request) => {
    calls.push(request.kind);
    if (request.kind === 'zip') {
      const unsigned = await JSZip.loadAsync(await blob.arrayBuffer());
      assert.equal(Object.keys(unsigned.files).some(name => name.endsWith('.gif')), false);
    } else assert.equal(await blob.text(), 'raster-only');
    return { blob, verifyUrl: '/receipt', warning: null };
  };
  try {
    const png = new Blob(['raster-only'], { type: 'image/png' });
    const opts = { provenance: { projectId: 'local-test' }, gifImages: [gif] };
    const block = await packageBlockDownload(png, 'block.png', opts, sign);
    const blockZip = await JSZip.loadAsync(await block.blob.arrayBuffer());
    assert.equal(await blockZip.file('block.png').async('string'), 'raster-only');
    assert.equal(await blockZip.file('gif_01.gif').async('string'), gifBytes);
    const page = await packageStorefrontZip([png], 'png', opts, sign);
    const pageZip = await JSZip.loadAsync(await page.blob.arrayBuffer());
    const rasters = Object.keys(pageZip.files).filter(name => name.endsWith('.png'));
    assert.equal(rasters.length, 1);
    assert.equal(await pageZip.file(rasters[0]).async('string'), 'raster-only');
    assert.equal(await pageZip.file('gif_01.gif').async('string'), gifBytes);
    assert.deepEqual(calls, ['block_png', 'zip']);
    assert.equal(block.verifyUrl, '/receipt'); assert.equal(page.verifyUrl, '/receipt');
  } finally { globalThis.fetch = nativeFetch; }
});
