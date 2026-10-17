import test from 'node:test';
import assert from 'node:assert/strict';
import * as presentation from '../../src/lib/outfitPresentation.js';
import * as progress from '../../src/lib/smoothProgress.js';

const helper = (name) => {
  assert.equal(typeof presentation[name], 'function', `${name} is available`);
  return presentation[name];
};
const near = (actual, expected, tolerance = 0.000001) => assert.ok(
  Math.abs(actual - expected) <= tolerance, `${actual} should be near ${expected}`,
);

test('catalog colors use role-specific broad groups, including Korean names and hex metadata', () => {
  const group = helper('colorGroupForItem');
  const cases = [
    [{ colorName: '아이보리' }, 'bottom', 'white'],
    [{ colorName: '워시드카키', colorGroup: 'green' }, 'bottom', 'beige'],
    [{ colorName: '멜란지그레이' }, 'inner', 'gray'],
    [{ colorKey: 'light_blue' }, 'top', 'blue'],
    [{ colorFamily: 'navy' }, 'top', 'blue'],
    [{ colorFamily: 'navy' }, 'bag', 'black'],
    [{ colorName: '진주' }, 'accessory', 'white'],
    [{ colorName: '골드' }, 'accessory', 'gold'],
    [{ colorGroup: 'camel' }, 'footwear', 'brown'],
    [{ colorHex: '#d8c4a3' }, 'bottom', 'beige'],
    [{ color: { hex: '#15141a' } }, 'footwear', 'black'],
    [{ colorHex: '#d2b069' }, 'accessory', 'gold'],
    [{ colorName: '핑크' }, 'top', null],
    [{ colorGroup: 'white' }, 'model', null],
    [{}, 'bottom', null],
  ];
  for (const [item, role, expected] of cases) assert.equal(group(item, role), expected);
  assert.deepEqual(helper('colorGroupsForRole')('top').map(x => x.value), ['white', 'beige', 'gray', 'blue', 'black']);
  assert.deepEqual(presentation.colorGroupsForRole('footwear').map(x => x.value), ['white', 'brown', 'black']);
  assert.deepEqual(presentation.colorGroupsForRole('accessory').map(x => x.value), ['white', 'gold', 'brown', 'black']);
  assert.deepEqual(presentation.colorGroupsForRole('model'), []);
});

test('main grid puts the current item first, then set items without disturbing AI order', () => {
  const sort = helper('sortOutfitItems');
  const items = [
    { id: 'white-other', colorGroup: 'white' },
    { id: 'black-ai', colorGroup: 'black', inSets: true },
    { id: 'white-ai', colorGroup: 'white', inSets: true },
    { id: 'selected', colorGroup: 'black' },
  ];
  assert.deepEqual(sort(items, { role: 'bottom', selectedId: 'selected' }).map(x => x.id),
    ['selected', 'black-ai', 'white-ai', 'white-other']);
  assert.deepEqual(items.map(x => x.id), ['white-other', 'black-ai', 'white-ai', 'selected']);
  assert.deepEqual(sort(items, { role: 'bottom', selectedId: 'selected', drawer: true }).map(x => x.id),
    ['white-ai', 'white-other', 'black-ai', 'selected']);
});

test('filters combine name search with color group and preserve all candidates for AI default', () => {
  const filter = helper('filterOutfitItems');
  const items = [
    { id: 'one', name: '크림 코튼 팬츠', colorName: '크림' },
    { id: 'two', name: '베이지 팬츠', colorName: '샌드베이지' },
    { id: 'three', name: '블랙 티셔츠', colorGroup: 'black' },
  ];
  assert.deepEqual(filter(items, { role: 'bottom' }), items);
  assert.deepEqual(filter(items, { role: 'bottom', color: 'white', search: ' 팬츠 ' }).map(x => x.id), ['one']);
  assert.deepEqual(filter(items, { role: 'bottom', search: '화이트' }).map(x => x.id), ['one']);
  assert.deepEqual(filter(items, { role: 'bottom', color: 'black', search: '팬츠' }), []);
});

test('model drawer search uses displayName when the catalog has no item name', () => {
  const models = [{ id: 'one', displayName: '가을' }, { id: 'two', displayName: '지윤' }];
  assert.deepEqual(helper('filterOutfitItems')(models, { role: 'model', search: ' 가을 ' }).map(x => x.id), ['one']);
});

test('compressed matching height uses the 0.7 ratio and invalid measurements remain finite', () => {
  const height = helper('compressedMatchHeight');
  near(height(128, 64, 100), 174.9379318309245);
  near(height(100, 100, 64), 73.16880830837224);
  assert.equal(height(100, 64, 64), 100);
  assert.equal(height(100, 0, 100), 100);
  assert.equal(height(100, 64, -1), 100);
  assert.equal(height(NaN, 64, 100), 0);
});

test('board stacks top and bottom without overlap and keeps product and shoes at physical scale', () => {
  const layout = helper('buildOutfitBoardLayout')({ clothingType: 'top',
    product: { cm: 64, ar: 0.95, name: '상품' },
    items: { bottom: { cm: 100, ar: 0.52 }, footwear: { ar: 2 } },
  });
  assert.deepEqual(layout.pieces.map(x => x.slot), ['product', 'bottom', 'footwear']);
  const [product, bottom, shoes] = layout.pieces;
  assert.equal(layout.width, 244);
  near(product.height, 128);
  near(product.width, 121.6);
  near(product.x, 61.2);
  assert.equal(product.y, 10);
  near(bottom.height, 174.9379318309245);
  near(bottom.y - product.y - product.height, 10);
  near(shoes.y - bottom.y - bottom.height, 10);
  near(shoes.height, 32);
  near(layout.height, 374.9379318309245);
});

test('outer is behind its offset inner and matching bottom uses the visible product bundle height', () => {
  const layout = helper('buildOutfitBoardLayout')({ clothingType: 'outer',
    product: { cm: 80, ar: 0.8 },
    items: { inner: { cm: 60, ar: 0.9 }, bottom: { cm: 100, ar: 0.52 }, footwear: { ar: 2 } },
  });
  const [product, inner, bottom, shoes] = layout.pieces;
  assert.equal(inner.slot, 'inner');
  assert.ok(inner.zIndex > product.zIndex);
  near((inner.x + inner.width / 2) - (product.x + product.width / 2), 44);
  near(inner.y - product.y, product.height * 0.3);
  near(bottom.y - Math.max(product.y + product.height, inner.y + inner.height), 10);
  near(bottom.height / (inner.y + inner.height - product.y), 1.129807620631004);
  near(shoes.y - bottom.y - bottom.height, 10);
});

test('bottom layouts put matching top first and dresses do not invent garment slots', () => {
  const layout = helper('buildOutfitBoardLayout');
  const bottom = layout({ clothingType: 'bottom', product: { cm: 100, ar: 0.52 },
    items: { top: { cm: 64, ar: 0.95 }, footwear: { ar: 2 } } });
  assert.deepEqual(bottom.pieces.map(x => x.slot), ['top', 'product', 'footwear']);
  near(bottom.pieces[0].height / bottom.pieces[1].height, 0.7316880830837224);
  const dress = layout({ clothingType: 'dress', product: { cm: 110, ar: 0.6 }, items: {} });
  assert.deepEqual(dress.pieces.map(x => x.slot), ['product', 'footwear']);
  assert.ok(dress.height <= 440);
  for (const piece of layout({ clothingType: 'outer', product: {}, items: {} }).pieces) {
    for (const key of ['x', 'y', 'width', 'height']) assert.ok(Number.isFinite(piece[key]));
  }
});

test('product preview uses tidy only when ready, with cutout and original fallbacks', () => {
  const image = helper('selectProductBoardImage');
  const front = { tidyUrl: 'tidy', cutoutUrl: 'cutout' };
  assert.equal(image({ front: { ...front, tidyStatus: 'ready' } }, 'original'), 'tidy');
  for (const tidyStatus of ['processing', 'failed', 'off']) {
    assert.equal(image({ front: { ...front, tidyStatus } }, 'original'), 'cutout');
    assert.equal(image({ front: { tidyUrl: 'stale-tidy', tidyStatus } }, 'original'), 'original');
  }
  assert.equal(image({ front: { tidyStatus: 'ready' } }, 'original'), 'original');
  assert.equal(image(null, 'original'), 'original');
});

test('the first-use hint is claimed once and unavailable storage stays quiet', () => {
  const claim = helper('claimOutfitHint');
  const stored = new Map();
  const storage = { getItem: key => stored.get(key), setItem: (key, value) => stored.set(key, value) };
  assert.equal(claim(storage), true);
  assert.equal(stored.get('wearless.outfit.v18.hintSeen'), '1');
  assert.equal(claim(storage), false);
  assert.equal(claim(null), false);
  assert.equal(claim({ getItem() { throw new Error('blocked'); } }), false);
  assert.equal(claim({ getItem() { return null; }, setItem() { throw new Error('quota'); } }), false);
});

const pending = { status: 'pending', contextKey: 'a', fallbackReady: true,
  sets: [{ slots: { bottom: 'fallback' } }], candidates: { bottom: [{ id: 'fallback' }] } };
const ready = { status: 'ready', contextKey: 'a', sets: [{ slots: { bottom: 'ai' } }],
  candidates: { bottom: [{ id: 'ai' }] } };

test('pending has at most 1500ms of skeleton before usable fallback', () => {
  const initial = helper('initialRecommendationState');
  const advance = helper('advanceRecommendationState');
  const start = initial(pending, 100);
  assert.equal(start.skeleton, true);
  assert.equal(advance(start, pending, 1599).skeleton, true);
  const fallback = advance(start, pending, 1600);
  assert.equal(fallback.skeleton, false);
  assert.equal(fallback.recommendation.status, 'fallback');
  assert.equal(fallback.recommendation.sets[0].slots.bottom, 'fallback');
  assert.equal(pending.status, 'pending');
  assert.equal(initial(ready).skeleton, false);
  assert.equal(initial({ status: 'unavailable' }).skeleton, false);
});

test('ready during skeleton appears immediately, while late ready waits for explicit acceptance', () => {
  const initial = helper('initialRecommendationState');
  const advance = helper('advanceRecommendationState');
  const accept = helper('acceptLateRecommendation');
  assert.equal(advance(initial(pending), ready, 1499).recommendation, ready);
  const fallback = advance(initial(pending), pending, 1500);
  const late = advance(fallback, ready, 2500);
  assert.equal(late.recommendation.sets[0].slots.bottom, 'fallback');
  assert.equal(late.lateRecommendation, ready);
  const applied = accept(late);
  assert.equal(applied.recommendation, ready);
  assert.equal(applied.lateRecommendation, null);
  assert.equal(applied.skeleton, false);
  assert.equal(accept(applied), applied);
  assert.equal(advance(initial(pending), ready, 1500).lateRecommendation, ready);
});

test('a new recommendation context starts its own skeleton and removes stale late results', () => {
  const initial = helper('initialRecommendationState');
  const advance = helper('advanceRecommendationState');
  const old = advance(advance(initial(pending), pending, 1500), ready, 2000);
  const next = advance(old, { ...pending, contextKey: 'b' }, 3000);
  assert.equal(next.skeleton, true);
  assert.equal(next.enteredAt, 3000);
  assert.equal(next.lateRecommendation, null);
  assert.equal(next.recommendation.contextKey, 'b');
});

test('closing a drawer preserves expanded row, selections, filters and scroll', () => {
  const close = helper('closeOutfitDrawer');
  const state = { drawer: { slot: 'bottom' }, open: 'bottom', scrollY: 420,
    selectedId: 'chosen', filters: { bottom: 'blue' } };
  assert.deepEqual(close(state), { ...state, drawer: null });
  assert.equal(state.drawer.slot, 'bottom');
});

test('custom upload progress matches reference values at 1, 6, 12 and 15 seconds', () => {
  assert.equal(typeof progress.customMatchWaitProgress, 'function');
  const cases = [
    [null, [6.666666666666667, 40, 80, 89.74358974358974]],
    [7000, [6.666666666666667, 40, 100, 100]],
    [12000, [6.666666666666667, 40, 80, 100]],
    [18000, [6.666666666666667, 40, 80, 89.74358974358974]],
  ];
  for (const [resultAtMs, expected] of cases) {
    for (const [index, elapsedMs] of [1000, 6000, 12000, 15000].entries()) {
      near(progress.customMatchWaitProgress({ elapsedMs, resultAtMs }), expected[index]);
    }
  }
});

test('custom upload completion follows the mock ramp continuously and never finishes before a result', () => {
  assert.equal(typeof progress.customMatchWaitProgress, 'function');
  const curve = progress.customMatchWaitProgress;
  for (const [resultAtMs, completedAt] of [[7000, 9188], [12000, 13350], [18000, 19292]]) {
    assert.ok(curve({ elapsedMs: completedAt - 1, resultAtMs }) < 100);
    assert.equal(curve({ elapsedMs: completedAt, resultAtMs }), 100);
    near(curve({ elapsedMs: resultAtMs, resultAtMs }), curve({ elapsedMs: resultAtMs }));
    let prior = 0;
    for (let elapsedMs = 0; elapsedMs <= completedAt + 1000; elapsedMs += 10) {
      const current = curve({ elapsedMs, resultAtMs });
      assert.ok(current >= prior && current <= 100);
      prior = current;
    }
  }
  assert.ok(curve({ elapsedMs: 600000 }) < 99);
  assert.equal(curve({ elapsedMs: -100 }), 0);
});
