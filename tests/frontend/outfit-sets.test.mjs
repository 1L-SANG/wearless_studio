import test from 'node:test';
import assert from 'node:assert/strict';
import * as outfit from '../../src/lib/outfitSelections.js';
import { isGenerationRelevantAnalysisPatch } from '../../src/lib/generationRelevance.js';
const sets = [{ slots: { bottom: 'b1', footwear: 's1', bag: 'bag1', accessory: null }, reason: 'one' },
  { slots: { bottom: 'b2', footwear: 's2', bag: 'bag2', accessory: null }, reason: 'two' }];
const analysis = () => ({ clothingType: 'top', matchClothing: [{ id: 'b1' }, { id: 'b2' }],
  modelId: 'chosen-model', outfitRecommendation: { sets, candidates: { bag: [{ id: 'bag1' }, { id: 'bag2' }] } },
  outfitSelections: { version: 1, setIndex: 0, optional: { bag: false, accessory: false }, slots: {
    bottom: { itemId: 'mine', origin: 'user' }, footwear: { itemId: null, origin: 'user' },
    bag: { itemId: 'remembered', origin: 'user' } } } });

test('세트 적용은 직접 선택을 바꾸고 user null과 꺼진 소품 기억을 유지한다', () => {
  assert.equal(typeof outfit.applyOutfitSet, 'function');
  const current = analysis(), patch = outfit.applyOutfitSet(current, 1);
  assert.equal(patch.outfitSelections.slots.bottom.itemId, 'b2');
  assert.deepEqual(patch.outfitSelections.slots.footwear, { itemId: null, origin: 'user' });
  assert.equal(patch.outfitSelections.slots.bag.itemId, 'remembered');
  assert.equal(patch.outfitSelections.optional.bag, false);
  assert.equal(patch.outfitSelections.setIndex, 1);
  assert.deepEqual(patch.matchClothing.filter(x => x.selected).map(x => x.id), ['b2']);
  assert.equal('modelId' in patch, false);
});

test('소품은 기본 꺼짐이며 켜면 현 세트, 다시 켜면 기억한 항목을 사용한다', () => {
  const current = analysis(); delete current.outfitSelections;
  assert.deepEqual(outfit.normalizeOutfitSelections(null, 'top').optional, { bag: false, accessory: false });
  const on = { ...current, ...outfit.toggleOutfitOptional(current, 'bag', true) };
  assert.equal(on.outfitSelections.slots.bag.itemId, 'bag1');
  const off = { ...on, ...outfit.toggleOutfitOptional(on, 'bag', false) };
  assert.equal(off.outfitSelections.slots.bag.itemId, 'bag1');
  assert.equal('bag' in outfit.outfitSelectionIds(off.outfitSelections), false);
  assert.equal(isGenerationRelevantAnalysisPatch({ outfitSelections: off.outfitSelections }, on), true);
  off.outfitSelections.setIndex = 1;
  assert.equal(outfit.toggleOutfitOptional(off, 'bag', true).outfitSelections.slots.bag.itemId, 'bag1');
});

test('pending 뒤 늦은 AI 응답은 모든 현재 선택과 소품 상태를 보존한다', () => {
  const current = analysis();
  current.outfitRecommendation.status = 'pending';
  current.outfitSelections.slots.bottom = { itemId: 'b1', origin: 'ai' };
  const response = { outfitSelections: { version: 1, slots: { bottom: { itemId: 'b2', origin: 'ai' } } },
    outfitRecommendation: { ...current.outfitRecommendation, status: 'ready' } };
  const actual = outfit.mergeOutfitAnalysis(current, response);
  assert.deepEqual(actual.outfitSelections, outfit.normalizeOutfitSelections(current.outfitSelections, 'top'));
  assert.equal(actual.outfitRecommendation.status, 'ready');
});

test('새 문맥의 첫 세트는 이전 세트 번호를 물려받지 않는다', () => {
  const current = analysis();
  current.outfitRecommendation.contextKey = 'old';
  current.outfitSelections.setIndex = 2;
  current.outfitSelections.slots.bottom.origin = 'ai';
  const response = { outfitRecommendation: { ...current.outfitRecommendation, contextKey: 'new', sets: sets.slice(0, 1) },
    outfitSelections: { version: 1, setIndex: 0, slots: { bottom: { itemId: 'b1', origin: 'ai' } } } };
  const actual = outfit.mergeOutfitAnalysis(current, response);
  assert.equal(actual.outfitSelections.setIndex, 0);
  assert.equal(actual.outfitSelections.slots.bottom.itemId, 'b1');
});
