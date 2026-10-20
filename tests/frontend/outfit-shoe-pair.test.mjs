import test from 'node:test';
import assert from 'node:assert/strict';
import * as presentation from '../../src/lib/outfitPresentation.js';

const near = (a, b) => assert.ok(Math.abs(a - b) < 1e-6, `${a} != ${b}`);
const measuredShoes = {
  sneaker: 448 / 233, loafer: 459 / 223, sandal: 454 / 168,
  chelsea: 446 / 350, longBoot: 349 / 583,
};
// Frozen from a5ee9b22 before changing shoe geometry. Garment geometry is held
// constant to isolate the shoe budget from independent image cropping changes.
const productCases = {
  top: { product: { cm: 64, ar: .85 }, before: [179.84, 122.24], propsBefore: [157.85765293977624, 122.24] },
  bottom: { product: { cm: 100, ar: .52 }, before: [281, 191], propsBefore: [213.0127182720543, 181.19926740115108] },
  outer: { product: { cm: 76, ar: .85 }, before: [189.55029585798815, 128.8402366863905], propsBefore: [141.39329936289056, 114.18352552187548] },
  dress: { product: { cm: 115, ar: .85 }, before: [216, 216], propsBefore: [170.14686248331105, 170.14686248331105] },
};
const boardInput = (clothingType, viewportHeight, ar) => ({
  clothingType, product: productCases[clothingType].product, width: 276, maxHeight: viewportHeight - 298,
  items: { top: { cm: 64, ar: .95 }, inner: { cm: 64, ar: .95 }, bottom: { cm: 100, ar: .52 }, footwear: { ar } },
});

test('pair size uses one shoe length, measured aspect, and a proportional 30cm height cap', () => {
  assert.equal(typeof presentation.footwearPairSize, 'function');
  for (const ar of [2.1, ...Object.values(measuredShoes)]) {
    const pair = presentation.footwearPairSize(ar);
    const shrink = Math.min(1, 30 / (presentation.SHOE_LENGTH_CM / ar * 1.08));
    near(pair.widthCm, presentation.SHOE_LENGTH_CM * shrink * 1.36);
    near(pair.heightCm, presentation.SHOE_LENGTH_CM / ar * shrink * 1.08);
    assert.ok(pair.heightCm <= 30);
  }
  near(presentation.footwearPairSize(.1).heightCm, 30);
  near(presentation.footwearPairSize(.1).widthCm, 30 / 1.08 * .1 * 1.36);
  assert.deepEqual(presentation.footwearPairSize(), presentation.footwearPairSize(2.1));
  assert.deepEqual(presentation.footwearPairSize(NaN), presentation.footwearPairSize(2.1));
});

test('rear shoe is right and above the front shoe, with the same default direction', () => {
  const layers = presentation.shoePairImageLayers();
  assert.equal(layers.length, 2);
  const [rear, front] = layers;
  near(parseFloat(rear.left), 36 / 1.36);
  near(parseFloat(rear.top), 0);
  near(parseFloat(front.left), 0);
  near(parseFloat(front.top), 8 / 1.08);
  for (const layer of layers) {
    near(parseFloat(layer.width), 100 / 1.36);
    near(parseFloat(layer.height), 100 / 1.08);
    assert.equal(layer.transform, undefined);
  }
  assert.ok(rear.zIndex < front.zIndex);
});

test('a one-pixel bounds change after 256px sampling keeps Chelsea pairs inside the old 16cm budget', () => {
  // 446×350 on a 480×640 source becomes about 178×140 at the sampling size.
  assert.ok(presentation.footwearPairSize(177 / 141).heightCm <= 16);
});

for (const [index, [width, height]] of [[1440, 900], [1280, 720]].entries()) {
  for (const [clothingType, sample] of Object.entries(productCases)) {
    test(`${width}×${height} ${clothingType}: ordinary shoes never reduce the product height`, () => {
      for (const props of [false, true]) for (const [name, ar] of Object.entries(measuredShoes)) {
        const input = boardInput(clothingType, height, ar);
        if (props) Object.assign(input.items, { bag: { src: 'bag' }, accessory: [{ src: '1' }, { src: '2' }, { src: '3' }] });
        const layout = presentation.buildOutfitBoardLayout(input);
        const product = layout.pieces.find(piece => piece.slot === 'product');
        const shoes = layout.pieces.at(-1);
        const before = (props ? sample.propsBefore : sample.before)[index];
        if (name !== 'longBoot') assert.ok(product.height >= before - 1e-6,
          `${name}, props=${props}: ${product.height} < ${before}`);
        assert.ok(layout.height <= height - 298 + 1e-6);
        near(shoes.height / presentation.footwearPairSize(ar).heightCm, product.height / sample.product.cm);
        near(shoes.y - Math.max(...layout.pieces.slice(0, -1).map(piece => piece.y + piece.height)), 10);
      }
    });
  }
}
