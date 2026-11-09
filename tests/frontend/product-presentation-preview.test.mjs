import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';
import { storyboardPreviewSource, storyboardPreviewCuts, assembleStoryboardPreview } from '../../src/features/storyboard/storyboardPreview.js';
import { sectionRuns } from '../../src/lib/pageTemplates/sectionRuns.js';
import { storyboardStage } from './helpers/storyboardStageHarness.mjs';
import { nodes } from './helpers/storyboardComponents.mjs';

const modes = ['ghost', 'flat', 'natural_light'];
const product = { clothingType: 'dress', colors: [
  { id: 'base', isBase: true, name: '검정', images: [{ slot: 'Front', src: '/base-front.jpg' }, { slot: 'Back', src: '/base-back.jpg' }] },
  { id: 'extra', name: '파랑', images: [{ slot: 'Front', src: '/extra-front.jpg' }, { slot: 'Back', src: '/extra-back.jpg' }] },
] };
const catalogs = { detailProduct: product, genExamples: [{ id: 'old-example', thumb: '/example.jpg' }] };
const card = (changes = {}) => ({ id: 'product', taxonomyVersion: 4, source: 'ai', cutType: 'product', shot: 'ghost',
  sectionId: 'products', sectionRole: 'product', contentRole: 'productOverview',
  pageSectionKey: 'emo.color_product', pageSectionKind: 'color_product', pageDesignId: 'CP-EMO-01', slotKey: 'color_product.back',
  direction: 'back', colorId: 'base', productPresentation: 'natural_light', ...changes });
const stageImages = tree => nodes(tree).filter(node => node.type === 'img');
const stageMissing = tree => nodes(tree).filter(node => node.props?.className === 'photoMissing');
const svgSources = markup => [...markup.matchAll(/<image\b[^>]*\bhref="([^"]*)"/g)].map(match => match[1]);
let vite, Stage, Preview, PageList;
before(async () => {
  vite = await createServer({ configFile: false, plugins: [react()], logLevel: 'silent', appType: 'custom',
    optimizeDeps: { noDiscovery: true, include: [] },
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, watch: null, ws: false } });
  ({ StoryboardStage: Stage } = await vite.ssrLoadModule('/src/features/storyboard/StoryboardStage.jsx'));
  ({ StoryboardPreview: Preview } = await vite.ssrLoadModule('/src/features/storyboard/StoryboardPreview.jsx'));
  ({ StoryboardPageList: PageList } = await vite.ssrLoadModule('/src/features/storyboard/StoryboardPageList.jsx'));
});
after(() => vite?.close());

test('explicit product previews use the selected color and face despite retained examples or stale thumbnails', async () => {
  for (const productPresentation of modes) {
    for (const [colorId, direction, expected] of [
      ['base', 'front', '/base-front.jpg'], ['base', 'back', '/base-back.jpg'],
      ['extra', 'front', '/extra-front.jpg'], ['extra', 'back', '/extra-back.jpg'],
    ]) {
      for (const exampleId of [null, 'old-example']) {
        const block = card({ productPresentation, direction, colorId, exampleId,
          ...(exampleId ? { thumb: '/stale.jpg', previewThumb: '/stale-preview.jpg' } : {}) });
        const saved = structuredClone(block);
        assert.equal(storyboardPreviewSource(block, catalogs), expected);
        assert.equal(storyboardPreviewCuts([block], catalogs)[0].imageUrl, expected);
        const tree = await storyboardStage([block], { catalogs });
        assert.deepEqual(stageImages(tree).map(image => image.props.src), [expected]);
        assert.equal(stageImages(tree)[0].props.srcSet, undefined);
        assert.equal(stageMissing(tree).length, 0);
        assert.deepEqual(block, saved, 'preview reads must not modify the generation recipe');
      }
    }
  }
});

test('single, paired and four-photo v5 stage groups retain each product source', async () => {
  const blocks = [
    card({ id: 'base-front', direction: 'front', slotKey: 'color_product.front1' }),
    card({ id: 'base-back' }),
    card({ id: 'extra-front', colorId: 'extra', direction: 'front', slotKey: 'color_product.front2' }),
    card({ id: 'extra-back', colorId: 'extra', slotKey: null }),
  ];
  for (const count of [1, 2, 4]) {
    const tree = await storyboardStage(blocks.slice(0, count), { catalogs });
    assert.deepEqual(stageImages(tree).map(image => image.props.src),
      ['/base-front.jpg', '/base-back.jpg', '/extra-front.jpg', '/extra-back.jpg'].slice(0, count));
    assert.equal(stageMissing(tree).length, 0);
  }
});

test('assembled page, outline and overview show the same explicit product sources in both templates', () => {
  for (const templateId of ['emo', 'show']) for (const productPresentation of modes) {
    const meta = { productPresentation, pageSectionKey: `${templateId}.color_product`,
      pageDesignId: templateId === 'emo' ? 'CP-EMO-01' : 'CP-SHOW-01', exampleId: 'old-example', previewThumb: '/stale-preview.jpg' };
    const blocks = [card({ ...meta, id: 'base-front', direction: 'front', slotKey: 'color_product.front1' }),
      card({ ...meta, id: 'extra-front', direction: 'front', colorId: 'extra', slotKey: 'color_product.front2' }),
      card({ ...meta, id: 'base-back' })];
    const saved = structuredClone(blocks);
    const page = assembleStoryboardPreview({ storyboard: blocks, product, catalogs });
    const runs = sectionRuns(blocks);
    const views = [React.createElement(Preview, { blocks: page }),
      React.createElement(PageList, { runs, page, activeKey: runs[0].key, sectionRefs: { current: new Map() } }),
      React.createElement(Stage, { blocks, page, catalogs, overview: true })];
    for (const view of views) {
      const markup = renderToStaticMarkup(view);
      assert.deepEqual(svgSources(markup).sort(), ['/base-back.jpg', '/base-front.jpg', '/extra-front.jpg']);
      assert.doesNotMatch(markup, /stale-preview|example\.jpg/);
    }
    assert.deepEqual(blocks, saved, 'display-only sources must not be persisted');
  }
});

test('a missing selected face or color shows a missing source without using another face, color or example', async () => {
  for (const productPresentation of modes) for (const colorId of ['base', 'extra', 'unknown']) {
    const selectedCatalogs = { ...catalogs, detailProduct: { ...product,
      colors: product.colors.map(color => color.id === colorId ? { ...color, images: color.images.slice(0, 1) } : color) } };
    const block = card({ productPresentation, colorId, exampleId: 'old-example', thumb: '/stale.jpg', previewThumb: '/stale-preview.jpg' });
    assert.equal(storyboardPreviewSource(block, selectedCatalogs), null);
    assert.equal(storyboardPreviewCuts([block], selectedCatalogs)[0].imageUrl, null);
    const tree = await storyboardStage([block], { catalogs: selectedCatalogs });
    assert.equal(stageImages(tree).length, 0);
    assert.equal(stageMissing(tree).length, 1);
    assert.doesNotMatch(renderToStaticMarkup(tree), /stale|example\.jpg|base-front\.jpg|extra-front\.jpg/);
    const page = assembleStoryboardPreview({ storyboard: [block], product: selectedCatalogs.detailProduct, catalogs: selectedCatalogs });
    assert.deepEqual(svgSources(renderToStaticMarkup(React.createElement(Preview, { blocks: page }))), []);
  }
});

test('unspecified legacy product presentation retains the example preview', async () => {
  const block = card({ productPresentation: undefined, exampleId: 'old-example' });
  assert.equal(storyboardPreviewSource(block, catalogs), '/example.jpg');
  const tree = await storyboardStage([block], { catalogs });
  assert.deepEqual(stageImages(tree).map(image => image.props.src), ['/example.jpg']);
  assert.equal(stageMissing(tree).length, 0);
});

test('own images and detail sources keep their distinct preview rules', async () => {
  const mine = card({ source: 'mine', cutType: null, shot: null, ownImages: ['/own.jpg'], productPresentation: 'flat' });
  const detail = card({ shot: 'detail', contentRole: 'detail', productPresentation: 'flat', exampleId: 'old-example', previewThumb: '/stale-preview.jpg' });
  for (const [block, expected] of [[mine, '/own.jpg'], [detail, '/base-back.jpg']]) {
    assert.equal(storyboardPreviewSource(block, catalogs), expected);
    const tree = await storyboardStage([block], { catalogs });
    assert.deepEqual(stageImages(tree).map(image => image.props.src), [expected]);
    assert.equal(stageMissing(tree).length, 0);
  }
});
