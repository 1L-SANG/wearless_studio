import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { buildOutfitBoardLayout } from '../../src/lib/outfitPresentation.js';
import * as presentation from '../../src/lib/outfitPresentation.js';
import { extensionModelFee } from '../../src/lib/limits.js';
import { toMatchItem } from '../../src/lib/api/matchingItems.js';
import { outfitHarness, nodes } from './helpers/outfitHarness.mjs';

const near = (a, b) => assert.ok(Math.abs(a - b) < 1e-6, `${a} != ${b}`);
const css = file => readFileSync(new URL(`../../src/features/analysis/${file}`, import.meta.url), 'utf8');
const rule = (source, selector) => source.slice(source.indexOf(`${selector} {`) + selector.length + 2).split('}')[0];

test('모델 이용료는 더해지는 크레딧으로 표시하고 포함 요금은 무료다', () => {
  assert.equal(typeof presentation.outfitModelCredit, 'function');
  assert.equal(presentation.outfitModelCredit(extensionModelFee('starter', 'mF')), '+19 크레딧');
  assert.equal(presentation.outfitModelCredit(extensionModelFee('seller', 'mF')), '+10 크레딧');
  assert.equal(presentation.outfitModelCredit(extensionModelFee('pro', 'mF')), '무료');
  assert.equal(presentation.outfitModelCredit(extensionModelFee('starter', 'mA')), '무료');
});

test('2단계 기둥은 운영 1단계 폭과 독립적이고 접힌 요약도 같은 폭이다', () => {
  const source = css('AnalysisStepFlow.module.css');
  const second = rule(source, '.second');
  assert.match(second, /max-width:\s*912px/);
  assert.match(second, /margin-inline:\s*auto/);
  assert.match(source, /\.flow\[data-folded="true"\] > \.summary/);
  assert.match(source, /:has\(\.flow\[data-folded="true"\]\).*:global\(\.pi-summary\)/);
  assert.doesNotMatch(source, /:global\(\.wizard\):has\(\.flow\)\s*\{[^}]*max-width/);
  for (const viewport of [960, 1440, 1920]) {
    const operatingWidth = Math.min(viewport, 1180) - 48;
    const secondWidth = Math.min(operatingWidth, Number(/max-width:\s*(\d+)px/.exec(second)[1]));
    assert.equal(secondWidth, 912);
    near((viewport - secondWidth) / 2, viewport / 2 - 456);
  }
});

test('모든 펼친 3열 타일은 목록 칸을 채우며 4:5를 유지한다', () => {
  const source = css('OutfitSelection.module.css');
  assert.match(rule(source, '.grid'), /repeat\(3, minmax\(0, 1fr\)\)/);
  const picture = rule(source, '.tilePicture');
  assert.match(picture, /aspect-ratio:\s*4\s*\/\s*5/);
  assert.doesNotMatch(picture, /height:\s*clamp/);
  const panelWidth = 912 - 2 * 16 - 300 - 20 + 2 * 8 - 2 * 16;
  const gap = Number(/gap:\s*\d+px\s+(\d+)px/.exec(rule(source, '.grid'))[1]);
  const tileWidth = (panelWidth - gap * 2) / 3;
  near(tileWidth, 170.66666666666666);
  near(tileWidth * 5 / 4, 213.33333333333331);
  near(tileWidth * 3 + gap * 2, panelWidth);
  assert.match(rule(source, '.modelPicture > img'), /width:\s*104%/);
  assert.match(rule(source, '.modelPicture > img'), /height:\s*104%/);
  assert.match(rule(source, '.modelPicture > img'), /object-position:\s*50% 38%/);
});

test('boardUrl은 서비스 매핑과 공용 타일에서 우선하며 없으면 썸네일을 쓴다', async (t) => {
  const h = await outfitHarness(); t.after(() => h.close());
  const { outfitImage, OutfitTile } = await h.load('/src/features/analysis/OutfitCatalog.jsx');
  const row = toMatchItem({ id: 'a', boardUrl: 'transparent', thumb: 'thumb', thumbnailUrl: 'thumbnail', imageUrl: 'image' });
  assert.equal(row.boardUrl, 'transparent');
  assert.equal(outfitImage(row), 'transparent');
  const tile = h.render(OutfitTile, { item: row, role: 'bottom', onSelect() {} });
  assert.equal(nodes(tile).find(n => n.type === 'img').props.src, 'transparent');
  assert.equal(outfitImage({ ...row, boardUrl: null }), 'thumb');
  assert.equal(outfitImage({ thumbnailUrl: 'thumbnail' }), 'thumbnail');
  assert.equal(toMatchItem({ id: 'legacy' }).boardUrl, null);
});

for (const [name, input] of [
  ['긴 바지', { clothingType: 'bottom', product: { cm: 140, ar: .3 }, items: { top: { cm: 36, ar: .8 }, footwear: { ar: 4.8 } } }],
  ['긴 원피스', { clothingType: 'dress', product: { cm: 160, ar: .25 }, items: { footwear: { ar: 3 } } }],
  ['아우터 묶음', { clothingType: 'outer', product: { cm: 150, ar: .25 }, items: { inner: { cm: 80, ar: .4 }, bottom: { cm: 38, ar: 2.8 }, footwear: { ar: 5 } } }],
  ['짧은 상의와 긴 하의', { clothingType: 'top', product: { cm: 30, ar: .8 }, items: { bottom: { cm: 140, ar: 1.4 }, footwear: { ar: 4 } } }],
]) {
  test(`보드는 ${name}, 신발, 소품 두 원을 최종 높이와 폭 안에 맞춘다`, () => {
    for (const maxHeight of [300, 440, 502, 718]) {
      const layout = buildOutfitBoardLayout({ ...input, width: 276, maxHeight,
        items: { ...input.items, bag: { src: 'bag' }, accessory: { src: 'accessory' } } });
      assert.ok(layout.height <= maxHeight + 1e-6, `${layout.height} exceeds ${maxHeight}`);
      assert.equal(layout.accessories?.length, 2);
      for (const p of [...layout.pieces, ...layout.accessories]) {
        assert.ok(p.x >= 10 - 1e-6 && p.y >= 10 - 1e-6, JSON.stringify(p));
        assert.ok(p.x + p.width <= 266 + 1e-6, JSON.stringify(p));
        assert.ok(p.y + p.height <= layout.height - 10 + 1e-6, JSON.stringify(p));
      }
      const shoes = layout.pieces.at(-1), above = layout.pieces.slice(0, -1);
      near(shoes.y - Math.max(...above.map(p => p.y + p.height)), 10);
      const product = layout.pieces.find(p => p.slot === 'product');
      near(product.height / input.product.cm, shoes.height / 16);
      if (input.clothingType === 'outer') {
        const inner = layout.pieces.find(p => p.slot === 'inner');
        near(product.height / input.product.cm, inner.height / 80);
        const bottom = layout.pieces.find(p => p.slot === 'bottom');
        near(bottom.y - Math.max(product.y + product.height, inner.y + inner.height), 10);
      }
    }
  });
}

test('최종 보드 맞춤은 보정 뒤 넓어진 하의와 긴 신발에도 같은 배율과 10px 간격을 쓴다', () => {
  const layout = buildOutfitBoardLayout({ clothingType: 'outer', width: 276, maxHeight: 300,
    product: { cm: 150, ar: .25 }, items: { inner: { cm: 80, ar: .4 },
      bottom: { cm: 38, ar: 2.8 }, footwear: { cm: 65, ar: 1.1 } } });
  const [outer, inner, bottom, shoes] = layout.pieces;
  near(outer.height / 150, inner.height / 80);
  near(outer.height / 150, shoes.height / 65);
  near(shoes.y - bottom.y - bottom.height, 10);
  near(layout.height, 300);
  assert.ok(bottom.width <= 256);
});
