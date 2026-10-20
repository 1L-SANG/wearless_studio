import test from 'node:test';
import assert from 'node:assert/strict';
import { outfitHarness, nodes, drain } from './helpers/outfitHarness.mjs';
import { footwearPairSize } from '../../src/lib/outfitPresentation.js';

const boardProps = () => ({
  analysis: { clothingType: 'top', matchClothing: [{ id: 'bottom', selected: true, thumb: '/bounds-bottom.png' }],
    outfitSelections: { slots: { footwear: { itemId: 'shoe' } } },
    outfitRecommendation: { candidates: { footwear: [{ id: 'shoe', name: '신발', thumb: 'https://images.wearless.kr/bounds-shoe.png' }] } } },
  product: { name: '판매 상품', colors: [{ isBase: true, images: [{ slot: 'Front', src: '/bounds-product.png' }] }] },
  onOpenRole() {},
});
const imageNodes = tree => nodes(tree).filter(node => node.type === 'img');
const boardNodes = tree => nodes(tree).filter(node => node.props?.style?.zIndex === 1 && node.props?.['aria-label'] === '신발 바꾸기');

test('board crops product, garments and two shoes while keeping display URLs and CORS attributes intact', async () => {
  const previous = { Image: globalThis.Image, document: globalThis.document, window: globalThis.window, location: globalThis.location };
  const requests = [];
  globalThis.location = { href: 'https://ai.wearless.kr/analysis' };
  globalThis.window = { innerHeight: 900, addEventListener() {}, removeEventListener() {} };
  globalThis.Image = class {
    naturalWidth = 480; naturalHeight = 640;
    set src(value) { requests.push({ src: value, crossOrigin: this.crossOrigin }); queueMicrotask(() => this.onload?.()); }
    decode() { return Promise.resolve(); }
  };
  globalThis.document = { createElement: () => ({ getContext: () => ({ drawImage() {},
    getImageData(_x, _y, width, height) {
      const data = new Uint8ClampedArray(width * height * 4);
      for (let y = 64; y < 192; y++) for (let x = 24; x < 168; x++) data[(y * width + x) * 4 + 3] = 255;
      return { data };
    },
  }) }) };
  const h = await outfitHarness({ getProductVisuals: async () => null });
  try {
    const { OutfitBoard } = await h.load('/src/features/analysis/OutfitBoard.jsx');
    const props = boardProps();
    h.render(OutfitBoard, props);
    const before = h.render(OutfitBoard, props);
    assert.equal(imageNodes(before).filter(node => node.props.src.includes('bounds-shoe')).length, 2);
    assert.equal(imageNodes(before).every(node => node.props.style === undefined), true);
    // A normal img load must not replace the 2.1 shoe assumption while measurement is pending.
    const beforeShoe = boardNodes(before)[0];
    const displayShoe = imageNodes(before).find(node => node.props.src.includes('bounds-shoe') && node.props.alt);
    displayShoe.props.onLoad({ currentTarget: { naturalWidth: 480, naturalHeight: 640, getAttribute: () => displayShoe.props.src } });
    assert.equal(boardNodes(h.render(OutfitBoard, props))[0].props.style.height, beforeShoe.props.style.height);
    await drain();
    const after = h.render(OutfitBoard, props);
    for (const img of imageNodes(after)) {
      assert.equal(img.props.crossOrigin, undefined);
      assert.equal(img.props.src.includes('bbox='), false);
      assert.deepEqual(img.props.style, { width: `${100 / .75}%`, height: '200%', left: `${-100 * .125 / .75}%`, top: '-50%' });
    }
    const shoes = imageNodes(after).filter(node => node.props.src.includes('bounds-shoe'));
    assert.deepEqual(shoes.map(node => node.props.alt), ['', '신발']);
    assert.equal(shoes[0].props['aria-hidden'], true);
    assert.deepEqual(requests, [
      { src: '/bounds-product.png', crossOrigin: undefined }, { src: '/bounds-bottom.png', crossOrigin: undefined },
      { src: 'https://images.wearless.kr/bounds-shoe.png?bbox=1', crossOrigin: 'anonymous' },
    ]);
    assert.equal(nodes(after).find(node => node.props?.['aria-busy'] != null).props['aria-busy'], false);
  } finally { await h.close(); Object.assign(globalThis, previous); }
});

test('failed measurements keep the displayed image at its original ratio', async () => {
  const previous = { Image: globalThis.Image, window: globalThis.window };
  globalThis.window = { innerHeight: 900, addEventListener() {}, removeEventListener() {} };
  globalThis.Image = class { set src(value) { queueMicrotask(() => this.onerror?.()); } };
  const h = await outfitHarness({ getProductVisuals: async () => null });
  try {
    const { OutfitBoard } = await h.load('/src/features/analysis/OutfitBoard.jsx');
    const props = boardProps();
    const before = h.render(OutfitBoard, props);
    for (const img of imageNodes(before)) img.props.onLoad?.({ currentTarget: {
      naturalWidth: 480, naturalHeight: 640, getAttribute: () => img.props.src,
    } });
    await drain();
    const after = h.render(OutfitBoard, props);
    assert.ok(imageNodes(after).every(img => img.props.style === undefined));
    const product = nodes(after).find(node => node.type === 'div' && node.props?.style?.zIndex === 10);
    const shoes = boardNodes(after)[0];
    assert.ok(Math.abs(shoes.props.style.height / product.props.style.height - footwearPairSize(.75).heightCm / 64) < 1e-6);
  } finally { await h.close(); Object.assign(globalThis, previous); }
});
