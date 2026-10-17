import test from 'node:test';
import assert from 'node:assert/strict';
import { isGenerationRelevantAnalysisPatch } from '../../src/lib/generationRelevance.js';
import { mergeMatchClothing } from '../../src/lib/api/matchingItems.js';

const outfit = await import('../../src/lib/outfitSelections.js').catch(() => ({}));
const selections = (slots) => ({ version: 1, slots });

test('판매 상품 종류에 필요한 역할만 포함한다', () => {
  assert.equal(typeof outfit.outfitSlotsFor, 'function');
  assert.deepEqual(outfit.outfitSlotsFor('outer'), ['inner', 'bottom', 'footwear', 'bag', 'accessory']);
  assert.deepEqual(outfit.outfitSlotsFor('top'), ['bottom', 'footwear', 'bag', 'accessory']);
  assert.deepEqual(outfit.outfitSlotsFor('bottom'), ['top', 'footwear', 'bag', 'accessory']);
  assert.deepEqual(outfit.outfitSlotsFor('dress'), ['footwear', 'bag', 'accessory']);
});

test('사용자가 비운 역할은 재추천으로 채우지 않으며 종류 전환에서는 불가능한 역할만 제거한다', () => {
  assert.equal(typeof outfit.mergeOutfitAnalysis, 'function');
  const current = { clothingType: 'dress', outfitSelections: selections({ footwear: { itemId: null, origin: 'user' },
    bag: { itemId: 'bag-user', origin: 'user' }, inner: { itemId: 'old-inner', origin: 'ai' } }) };
  const next = { outfitSelections: selections({ footwear: { itemId: 'shoes-ai', origin: 'ai' }, bag: { itemId: 'bag-ai', origin: 'ai' } }),
    outfitRecommendation: { version: 1, status: 'ready', candidates: { footwear: [], bag: [] } } };
  const actual = outfit.mergeOutfitAnalysis(current, next);
  assert.deepEqual(actual.outfitSelections.slots.footwear, { itemId: null, origin: 'user' });
  assert.deepEqual(actual.outfitSelections.slots.bag, { itemId: 'bag-user', origin: 'user' });
  assert.equal('inner' in actual.outfitSelections.slots, false);
});

test('보완 의류 선택과 역할 선택을 동기화하고 다른 역할을 matchIds에 섞지 않는다', () => {
  assert.equal(typeof outfit.setOutfitSelection, 'function');
  const current = { clothingType: 'top', matchClothing: [{ id: 'pants', selected: true }, { id: 'skirt', selected: false }],
    outfitSelections: selections({ bottom: { itemId: 'pants', origin: 'ai' }, footwear: { itemId: 'shoe', origin: 'ai' } }),
    outfitRecommendation: { candidates: { bottom: [{ id: 'pants' }, { id: 'skirt' }], footwear: [{ id: 'shoe' }] } } };
  const patch = outfit.setOutfitSelection(current, 'bottom', 'skirt');
  assert.deepEqual(patch.outfitSelections.slots.bottom, { itemId: 'skirt', origin: 'user' });
  assert.deepEqual(patch.matchClothing.filter((item) => item.selected).map((item) => item.id), ['skirt']);
  assert.deepEqual(outfit.setOutfitSelection({ ...current, ...patch }, 'bottom', null).matchClothing.filter((item) => item.selected), []);
  assert.equal(outfit.setOutfitSelection(current, 'footwear', 'forged'), null);
});

test('저장 응답이 최근 사용자 선택을 되돌리지 않는다', () => {
  assert.equal(typeof outfit.mergeOutfitAnalysis, 'function');
  const current = { clothingType: 'top', matchClothing: [{ id: 'a', selected: false }, { id: 'b', selected: true }],
    outfitSelections: selections({ bottom: { itemId: 'b', origin: 'user' }, footwear: { itemId: null, origin: 'user' } }) };
  const response = { matchClothing: [{ id: 'a', selected: true }, { id: 'b', selected: false }],
    outfitSelections: selections({ bottom: { itemId: 'a', origin: 'user' }, footwear: { itemId: 'shoe', origin: 'ai' } }) };
  const actual = outfit.mergeOutfitAnalysis(current, response);
  assert.equal(actual.outfitSelections.slots.bottom.itemId, 'b');
  assert.equal(actual.outfitSelections.slots.footwear.itemId, null);
  assert.deepEqual(actual.matchClothing.filter((item) => item.selected).map((item) => item.id), ['b']);
});

test('추천 설명과 origin 변경은 생성 변경이 아니고 실제 아이템 변경만 판정한다', () => {
  const prior = { outfitSelections: selections({ footwear: { itemId: 'shoe', origin: 'ai' } }) };
  assert.equal(isGenerationRelevantAnalysisPatch({ outfitRecommendation: { reason: '새 설명' } }, prior), false);
  assert.equal(isGenerationRelevantAnalysisPatch({ outfitSelections: selections({ footwear: { itemId: 'shoe', origin: 'user' } }) }, prior), false);
  assert.equal(isGenerationRelevantAnalysisPatch({ outfitSelections: selections({ footwear: { itemId: null, origin: 'user' } }) }, prior), true);
});

test('문맥이 다른 오래된 저장 응답은 후보와 선택을 반영하지 않는다', () => {
  assert.equal(typeof outfit.mergeOutfitSaveResponse, 'function');
  const current = { clothingType: 'top', subCategory: '셔츠', outfitRecommendation: { contextKey: 'latest' } };
  const before = { clothingType: 'top', subCategory: '티셔츠' };
  const response = { outfitRecommendation: { contextKey: 'stale' } };
  assert.equal(outfit.mergeOutfitSaveResponse(current, response, {
    requestContextKey: outfit.outfitContextKey(before, { colors: [] }), product: { colors: [] },
  }), current);
});

test('종류를 바꿀 때 이전 보완 의류를 새 역할로 이월하지 않는다', () => {
  assert.equal(typeof outfit.normalizeOutfitSelections, 'function');
  const actual = outfit.normalizeOutfitSelections({ version: 1, slots: { bottom: { itemId: 'pants', origin: 'user' } } },
    'bottom', [{ id: 'pants', clothingType: 'bottom', selected: true }]);
  assert.equal(actual.slots.top.itemId, null);
  assert.equal('bottom' in actual.slots, false);
});

test('핏과 소재도 추천 문맥과 오래된 응답 판정에 포함된다', () => {
  for (const patch of [{ fit: 'over' }, { materials: [{ name: '울', ratio: 100 }] }]) {
    assert.equal(outfit.isOutfitContextPatch(patch), true);
    const old = { clothingType: 'outer', fit: 'regular', materials: [{ name: '면', ratio: 100 }] };
    const current = { ...old, ...patch };
    assert.notEqual(outfit.outfitContextKey(old, {}), outfit.outfitContextKey(current, {}));
    assert.equal(outfit.mergeOutfitSaveResponse(current, { outfitRecommendation: { contextKey: 'old' } }, {
      requestContextKey: outfit.outfitContextKey(old, {}), product: {},
    }), current);
  }
});

test('이미 빈 보완 의류를 사용자 의도로 비울 때 생성 정보를 바꾸지 않는다', () => {
  const current = { clothingType: 'top', matchClothing: [{ id: 'pants', selected: false }],
    outfitSelections: selections({ bottom: { itemId: null, origin: 'ai' } }) };
  const patch = outfit.setOutfitSelection(current, 'bottom', null);
  assert.equal('matchClothing' in patch, false);
  assert.equal(isGenerationRelevantAnalysisPatch(patch, current), false);
});

test('누끼 상태 폴링도 최근 코디 선택을 보존한다', () => {
  const current = { clothingType: 'top', matchClothing: [{ id: 'a', selected: false }, { id: 'b', selected: true }],
    outfitSelections: selections({ bottom: { itemId: 'b', origin: 'user' } }) };
  const actual = mergeMatchClothing(current, { matchClothing: [{ id: 'a', selected: true }, { id: 'b', selected: false, cutoutStatus: 'ready' }] });
  assert.deepEqual(actual.matchClothing.filter((item) => item.selected).map((item) => item.id), ['b']);
  assert.equal(actual.matchClothing.find((item) => item.id === 'b').cutoutStatus, 'ready');
});

test('복원된 보완 후보의 종류가 맞지 않으면 isCompatible 누락이어도 선택하지 않는다', () => {
  const current = { clothingType: 'top', matchClothing: [{ id: 'wrong', clothingType: 'top' }],
    outfitSelections: { slots: {} } };
  assert.equal(outfit.setOutfitSelection(current, 'bottom', 'wrong'), null);
});
