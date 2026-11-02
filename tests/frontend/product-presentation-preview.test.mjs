import test, { before } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { transformWithEsbuild } from 'vite';
import { detailTargetPresentation, isProductDetail } from '../../src/lib/detailRecommendations.js';
import { generationExampleImageSources } from '../../src/lib/generationExamples.js';
import { productPresentationOf } from '../../src/lib/storyboardTaxonomy.js';
import { productSourcePhotos } from '../../src/lib/productSourcePhotos.js';

const source = readFileSync(new URL('../../src/features/storyboard/Storyboard.jsx', import.meta.url), 'utf8');
const product = { clothingType: 'dress', colors: [{ id: 'base', images: [
  { slot: 'Front', src: '/my-front.jpg' }, { slot: 'Back', src: '/my-back.jpg' },
] }] };
const catalogs = { detailProduct: product, genExamples: [{ id: 'old-example', thumb: '/example.jpg' }] };
const card = (changes = {}) => ({ id: 'product', source: 'ai', cutType: 'product', shot: 'ghost',
  direction: 'back', colorId: 'base', productPresentation: 'natural_light', ...changes });
let components;

before(async () => {
  const jsx = source.slice(source.indexOf('function storyboardPreviewSource('), source.indexOf('const ShuffleIcon ='));
  const { code } = await transformWithEsbuild(jsx, 'product-preview.jsx', { loader: 'jsx' });
  components = new Function('scope', `with (scope) { ${code}
    return { storyboardPreviewSource, StoryboardMedia, StoryboardCard, StoryboardFrame, StoryboardMoodGrid, StoryboardStack }; }`)({
    React, detailTargetPresentation, isProductDetail, generationExampleImageSources, productPresentationOf, productSourcePhotos,
    useContext: () => null, SimilarCutControlsContext: {}, useRef: () => ({ current: null }),
    useState: () => [false, () => {}], useLayoutEffect() {}, canPlanSimilarCut: () => false,
    cutNumber: (index, total) => `${index}/${total}`, mineImageUrl: value => typeof value === 'string' ? value : value?.url,
    BundleSwapArrows: () => null, StoryboardCardActions: () => null,
    StoryboardCaption: () => null, SelectionRing: () => null,
  });
});

const nodes = tree => !React.isValidElement(tree) ? []
  : [tree, ...React.Children.toArray(tree.props.children).flatMap(nodes)];
const media = (block, selectedCatalogs = catalogs) => components.StoryboardMedia({ block, catalogs: selectedCatalogs, index: 1, total: 1 });

test('explicit product previews show the selected source face despite an absent or retained example', () => {
  for (const productPresentation of ['ghost', 'flat', 'natural_light']) {
    for (const [direction, expected] of [['front', '/my-front.jpg'], ['back', '/my-back.jpg']]) {
      for (const exampleId of [null, 'old-example']) {
        const block = card({ productPresentation, direction, exampleId,
          ...(exampleId ? { thumb: '/stale.jpg', previewThumb: '/stale-preview.jpg' } : {}) });
        assert.equal(components.storyboardPreviewSource(block, catalogs), expected);
        const tree = media(block);
        const image = nodes(tree).find(node => node.type === 'img');
        assert.ok(image, 'available product sources must render without an example warning');
        assert.equal(image.props.src, expected);
        assert.equal(image.props.srcSet, undefined);
        assert.equal(image.props.alt, `내 상품 원본 · ${direction === 'back' ? '뒷면 사진' : '앞면 사진'}`);
        const markup = renderToStaticMarkup(tree);
        assert.match(markup, /sb-mine-badge/);
        assert.doesNotMatch(markup, /sb-missing-body|예시를 준비하지 못했어요/);
      }
    }
  }
});

test('explicit product cards with a source are ready in single, paired and four-cut layouts', () => {
  const items = [0, 1, 2, 3].map(index => ({ block: card({ id: `product-${index}` }), index: index + 1 }));
  const shared = { total: 4, catalogs, colorOpts: [], dragFor: () => ({}), onSelect() {}, onDuplicate() {}, onDelete() {} };
  for (const tree of [components.StoryboardCard({ ...shared, item: items[0] }),
    components.StoryboardFrame({ ...shared, items: items.slice(0, 2) }),
    components.StoryboardMoodGrid({ ...shared, items })]) {
    const cards = nodes(tree).filter(node => node.props.className?.match(/sb-cutcard|sb-frame-half/));
    assert.ok(cards.length);
    assert.ok(cards.every(node => !node.props.className.split(' ').includes('missing')));
  }
});

test('collapsed product stacks use the source face and describe it as the seller original', () => {
  const tree = components.StoryboardStack({ group: { items: [{ block: card({ exampleId: 'old-example' }), index: 1 }] }, total: 1, catalogs });
  const image = nodes(tree).find(node => node.type === 'img');
  assert.equal(image.props.src, '/my-back.jpg');
  assert.equal(image.props.srcSet, undefined);
  assert.equal(image.props.alt, '내 상품 원본 · 뒷면 사진');
});

test('a missing selected product face shows a source error instead of substituting another photo', () => {
  const frontOnly = { ...catalogs, detailProduct: { ...product, colors: [{ id: 'base', images: product.colors[0].images.slice(0, 1) }] } };
  const block = card({ exampleId: 'old-example', thumb: '/stale.jpg', previewThumb: '/stale-preview.jpg' });
  assert.equal(components.storyboardPreviewSource(block, frontOnly), null);
  const tree = media(block, frontOnly);
  assert.equal(nodes(tree).filter(node => node.type === 'img').length, 0);
  const markup = renderToStaticMarkup(tree);
  assert.match(markup, /상품 원본을 불러오지 못했어요/);
  assert.doesNotMatch(markup, /예시를 준비하지 못했어요|\/my-front.jpg|\/example.jpg/);
  const stack = components.StoryboardStack({ group: { items: [{ block, index: 1 }] }, total: 1, catalogs: frontOnly });
  assert.equal(nodes(stack).filter(node => node.type === 'img').length, 0);
  assert.match(renderToStaticMarkup(stack), /상품 원본을 불러오지 못했어요/);
});

test('unspecified legacy product presentation retains its example preview', () => {
  const block = card({ productPresentation: undefined, exampleId: 'old-example' });
  assert.equal(components.storyboardPreviewSource(block, catalogs), '/example.jpg');
  assert.equal(nodes(media(block)).find(node => node.type === 'img').props.src, '/example.jpg');
});
