import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { colorGroupForItem, colorGroupsForRole, filterOutfitItems, sortOutfitItems,
  buildOutfitBoardLayout } from '../../src/lib/outfitPresentation.js';
import { seedMatchingItems } from '../../src/mock/seedMatchingItems.js';
import { v18FixtureCandidates } from '../../src/mock/outfitV18Fixtures.js';
import { outfitHarness, nodes, textOf } from './helpers/outfitHarness.mjs';

let h, garmentDisplayLength, OutfitColorChips;
before(async () => {
  h = await outfitHarness();
  ({ garmentDisplayLength } = await h.load('/src/features/analysis/OutfitBoard.jsx'));
  ({ OutfitColorChips } = await h.load('/src/features/analysis/OutfitCatalog.jsx'));
});
after(async () => h?.close());

const tables = {
  garment: {
    white: ['white', 'ivory', 'cream'], beige: ['beige', 'camel', 'brown', 'khaki', 'tan'],
    gray: ['gray', 'charcoal'], blue: ['lightblue', 'blue', 'navy'], black: ['black'],
    color: ['green', 'olive', 'red', 'burgundy', 'pink', 'purple', 'yellow', 'orange'],
  },
  shoes: { white: ['white', 'ivory', 'cream'], brown: ['brown', 'beige', 'tan', 'camel'], black: ['black'] },
  bag: { white: ['white', 'ivory', 'cream'], gray: ['gray', 'silver'],
    brown: ['beige', 'camel', 'brown'], black: ['black', 'navy'] },
  accessory: { white: ['white', 'ivory', 'pearl'], silver: ['silver', 'gray'], gold: ['gold'],
    brown: ['beige', 'camel', 'brown'], black: ['black', 'navy'] },
};
for (const [role, table] of Object.entries({ top: tables.garment, bottom: tables.garment,
  inner: tables.garment, footwear: tables.shoes, shoes: tables.shoes, bag: tables.bag,
  accessory: tables.accessory, acc: tables.accessory })) {
  test(`${role}: every supported color filters into exactly one ordered group`, () => {
    const items = Object.values(table).flat().map(colorGroup => ({ id: colorGroup, colorGroup }));
    assert.deepEqual(colorGroupsForRole(role).map(group => group.value), Object.keys(table));
    for (const [color, expected] of Object.entries(table)) {
      assert.deepEqual(filterOutfitItems(items, { role, color }).map(item => item.id), expected);
    }
    const sorted = sortOutfitItems([...items].reverse(), { role, drawer: true });
    assert.deepEqual(sorted.map(item => colorGroupForItem(item, role)),
      Object.entries(table).flatMap(([color, keys]) => keys.map(() => color)));
    assert.equal(colorGroupForItem({ colorGroup: 'unknown' }, role), null);
  });
}

test('catalog keys override conflicting names; khaki and olive names stay distinct', () => {
  for (const colorGroup of ['khaki', 'beige']) {
    assert.equal(colorGroupForItem({ colorGroup, colorName: '올리브 그린' }, 'bottom'), 'beige');
  }
  for (const field of ['colorGroup', 'color_group']) {
    assert.equal(colorGroupForItem({ [field]: 'green', colorKey: 'khaki', colorName: '워시드 카키' }, 'bottom'), 'color');
    assert.equal(colorGroupForItem({ [field]: 'ivory', colorName: '블랙' }, 'footwear'), 'white');
    assert.equal(colorGroupForItem({ [field]: 'silver', colorName: '골드' }, 'accessory'), 'silver');
  }
  const names = {
    화이트: 'white', 아이보리: 'white', 크림: 'white', 베이지: 'beige', 카멜: 'beige', 브라운: 'beige',
    카키: 'beige', 워시드카키: 'beige', 그레이: 'gray', 차콜: 'gray', 라이트블루: 'blue', 블루: 'blue', 네이비: 'blue',
    블랙: 'black', 그린: 'color', 올리브: 'color', 올리브그린: 'color', 레드: 'color', 버건디: 'color',
    핑크: 'color', 퍼플: 'color', 옐로우: 'color', 오렌지: 'color',
  };
  for (const [colorName, expected] of Object.entries(names)) {
    assert.equal(colorGroupForItem({ colorName }, 'top'), expected, colorName);
  }
  for (const [colorName, expected] of [['진주', 'white'], ['실버', 'silver'], ['은색', 'silver'], ['골드', 'gold']]) {
    assert.equal(colorGroupForItem({ colorName }, 'accessory'), expected);
  }
});

test('chips show only non-custom candidate groups and emit the new filter values', () => {
  for (const [role, items, labels, values] of [
    ['top', [{ colorGroup: 'green' }, { colorGroup: 'red' }, { colorGroup: 'blue', isCustom: true }], ['AI 추천', '컬러'], ['', 'color']],
    ['bag', [{ colorGroup: 'gray' }, { colorGroup: 'ivory' }], ['AI 추천', '화이트', '그레이'], ['', 'white', 'gray']],
    ['accessory', [{ colorGroup: 'silver' }, { colorGroup: 'gold' }], ['AI 추천', '실버', '골드'], ['', 'silver', 'gold']],
  ]) {
    const clicked = [];
    const tree = h.render(OutfitColorChips, { role, items, value: values.at(-1), onChange: value => clicked.push(value) });
    const buttons = nodes(tree).filter(node => node.type === 'button');
    assert.deepEqual(buttons.map(textOf), labels);
    buttons.forEach(button => button.props.onClick());
    assert.deepEqual(clicked, values);
    assert.equal(buttons.at(-1).props['aria-pressed'], true);
  }
});

const bottomLengths = [
  [42, ['mini', '미니']], [48, ['short', '숏']], [56, ['knee', '무릎']],
  [72, ['midi', '미디']], [90, ['maxi', '맥시']], [100, ['full', '풀', '롱']],
];
test('bottom length categories accept English and Korean without conflating shorts and mini skirts', () => {
  for (const [cm, aliases] of bottomLengths) for (const length of aliases) {
    assert.equal(garmentDisplayLength({ length }, 'bottom'), cm, length);
  }
  assert.equal(garmentDisplayLength({ length: ' MIDI ' }, 'bottom'), 72);
  assert.equal(garmentDisplayLength({ length: 'unknown' }, 'bottom'), 100);
});

test('tops and inners use cropped 45 and regular/default 64, with real measurements taking priority', () => {
  for (const role of ['top', 'inner']) {
    for (const length of ['cropped', 'crop', '크롭']) assert.equal(garmentDisplayLength({ length }, role), 45);
    for (const length of ['regular', '레귤러', '기본', 'unknown', undefined]) assert.equal(garmentDisplayLength({ length }, role), 64);
  }
  for (const item of [{ lengthCm: 83 }, { cm: 83 }, { totalLength: '83' }, { measurements: [{ key: 'totalLength', value: '83' }] }]) {
    assert.equal(garmentDisplayLength({ length: 'mini', ...item }, 'bottom'), 83);
  }
  assert.equal(garmentDisplayLength({ lengthCm: Infinity, length: 'midi' }, 'bottom'), 72);
  for (const [role, length, cm] of [
    ['top', 'semi_crop', 45], ['top', '세미크롭', 45], ['top', 'semi_long', 75], ['top', '세미롱', 75],
    ['outer', 'crop_short', 50], ['outer', '크롭·숏', 50], ['bottom', 'cropped', 85],
  ]) assert.equal(garmentDisplayLength({ length }, role), cm);
  assert.equal(garmentDisplayLength({ length: '롱' }, 'outer'), 115);
  assert.equal(garmentDisplayLength({}, 'dress'), 115);
});

test('all new lengths retain the 0.7 proportions and fit R1 bounds after final scaling', () => {
  const near = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-6, `${actual} != ${expected}`);
  for (const [bottomCm, [length]] of bottomLengths) for (const topLength of ['cropped', 'regular']) {
    const topCm = topLength === 'cropped' ? 45 : 64;
    const bottom = { cm: garmentDisplayLength({ length }, 'bottom'), ar: bottomCm <= 56 ? 1.5 : .7 };
    const top = { cm: garmentDisplayLength({ length: topLength }, 'top'), ar: 1.2 };
    for (const clothingType of ['top', 'bottom', 'outer']) for (const width of [244, 276]) for (const maxHeight of [300, 440, 718]) {
      const product = clothingType === 'bottom' ? bottom : clothingType === 'outer' ? { cm: 115, ar: .8 } : top;
      const layout = buildOutfitBoardLayout({ clothingType, product, width, maxHeight,
        items: { top, inner: top, bottom, footwear: { ar: 3 }, bag: { src: 'bag' }, accessory: { src: 'accessory' } } });
      assert.ok(layout.height <= maxHeight + 1e-6);
      for (const piece of [...layout.pieces, ...layout.accessories]) {
        for (const key of ['x', 'y', 'width', 'height']) assert.ok(Number.isFinite(piece[key]));
        assert.ok(piece.x >= 10 - 1e-6 && piece.y >= 10 - 1e-6);
        assert.ok(piece.x + piece.width <= width - 10 + 1e-6);
        assert.ok(piece.y + piece.height <= layout.height - 10 + 1e-6);
      }
      const prod = layout.pieces.find(piece => piece.slot === 'product');
      const match = layout.pieces.find(piece => piece.slot === (clothingType === 'bottom' ? 'top' : 'bottom'));
      const bundleCm = clothingType === 'outer' ? Math.max(115, 115 * .3 + topCm) : product.cm;
      const bundleHeight = prod.height * bundleCm / product.cm;
      near(match.height / bundleHeight, ((clothingType === 'bottom' ? topCm : bottomCm) / bundleCm) ** .7);
      const shoes = layout.pieces.at(-1);
      near(shoes.y - Math.max(...layout.pieces.slice(0, -1).map(piece => piece.y + piece.height)), 10);
    }
  }
});

const liveColors = {
  top: ['beige', 'black', 'blue', 'brown', 'gray', 'green', 'ivory', 'navy', 'red', 'white'],
  bottom: ['beige', 'black', 'blue', 'brown', 'gray', 'green', 'ivory', 'navy', 'white'],
  inner: ['black', 'brown', 'gray', 'green', 'ivory', 'navy', 'white'],
  footwear: ['black', 'brown', 'ivory', 'white'], bag: ['black', 'brown', 'gray', 'ivory'],
  accessory: ['black', 'brown', 'gold', 'ivory', 'silver'],
};
test('mock catalog and v18 fixtures cover the measured colors and lengths with local illustrations', () => {
  const fixtures = v18FixtureCandidates({ clothingType: 'outer', targetGenders: ['women'] });
  for (const [role, colors] of Object.entries(liveColors)) {
    const items = ['top', 'bottom'].includes(role) ? seedMatchingItems.filter(item => item.clothingType === role) : fixtures[role];
    for (const color of colors) assert.ok(items.some(item => item.colorGroup === color), `${role}/${color}`);
    for (const item of items) {
      assert.ok(colorGroupForItem(item, role), `${role}/${item.id}`);
      for (const key of ['imageUrl', 'thumbnailUrl', 'thumb', 'boardUrl']) if (item[key]) {
        assert.ok(item[key].startsWith('data:image/svg+xml;') || item[key].startsWith('/assets/'), `${item.id}/${key}`);
      }
    }
  }
  const bottoms = seedMatchingItems.filter(item => item.clothingType === 'bottom');
  for (const [, [length]] of bottomLengths) assert.ok(bottoms.some(item => item.length === length), length);
  for (const length of ['cropped', 'regular']) assert.ok(seedMatchingItems.some(item => item.clothingType === 'top' && item.length === length));
  assert.ok(fixtures.inner.every(item => item.length === 'regular'));
  for (const role of ['footwear', 'bag', 'accessory']) assert.ok(fixtures[role].every(item => item.length === 'na'));
});
