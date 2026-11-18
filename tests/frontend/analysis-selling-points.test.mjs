import test from 'node:test';
import assert from 'node:assert/strict';

import { applySellingPointEdit, sellingPointSuggestions } from '../../src/features/analysis/sellingPoints.js';

const base = { sellingPoints: ['골지 짜임', '라운드넥'], aiSuggestedPoints: ['골지 짜임'] };

const suggestions = [
  { point: '드롭숄더 실루엣', evidence: '어깨선이 내려와 있어요' },
  { point: '도톰한 짜임감', evidence: '굵은 골이 보여요' },
];

test('추천은 공백을 빼고 고른 특징을 비교하고 삭제·수정 뒤 다시 보여 준다', () => {
  const input = { featureSuggestions: suggestions, sellingPoints: ['드롭 숄더실루엣'], clothingType: 'top' };
  const before = structuredClone(input);
  assert.deepEqual(sellingPointSuggestions(input), [suggestions[1]]);
  assert.deepEqual(input, before);
  assert.deepEqual(sellingPointSuggestions({ ...input, sellingPoints: [] }), suggestions);
  const edited = applySellingPointEdit({ sellingPoints: [suggestions[0].point], aiSuggestedPoints: [], index: 0, text: '넓은 어깨선' });
  assert.deepEqual(sellingPointSuggestions({ ...input, ...edited }), suggestions);
});

test('특징이 8개이거나 특징 뽑기가 도는 동안에는 추천을 숨긴다', () => {
  assert.deepEqual(sellingPointSuggestions({ featureSuggestions: suggestions, sellingPoints: Array(8).fill('선택') }), []);
  assert.deepEqual(sellingPointSuggestions({ featureSuggestions: suggestions, pending: true }), []);
  assert.deepEqual(sellingPointSuggestions({}), []);
  assert.deepEqual(sellingPointSuggestions({ featureSuggestions: [] }), []);
});

test('상의와 아우터만 띄어 쓴 핏 이름까지 추천에서 거른다', () => {
  const names = ['슬림핏', '레귤러핏', '세미오버핏', '오버핏', '루즈핏', '박시핏', '오버사이즈', '오버 핏', '세미 오버 핏'];
  for (const clothingType of ['top', 'outer']) {
    for (const name of names) {
      assert.deepEqual(sellingPointSuggestions({ clothingType,
        featureSuggestions: [{ point: `${name} 실루엣`, evidence: '' }, ...suggestions] }), suggestions);
    }
  }
  for (const [clothingType, subCategory] of [['bottom', 'jeans'], ['bottom', 'mini_skirt'], ['dress', 'mini_dress']]) {
    const fit = { point: '슬림핏 실루엣', evidence: '몸선을 따라 붙어요' };
    assert.deepEqual(sellingPointSuggestions({ clothingType, subCategory, featureSuggestions: [fit] }), [fit]);
  }
});

const suggestPoint = (analysis, point) => sellingPointSuggestions({ ...analysis, featureSuggestions: [{ point }] })
  .map((suggestion) => suggestion.point);

test('소매 길이 칸이 있을 때만 길이 표현을 거르고 모양과 장식은 남긴다', () => {
  const top = { clothingType: 'top', targetGenders: ['women'] };
  const sleeveless = { ...top, fitRead: { sleeveType: 'sleeveless' } };
  const pants = { clothingType: 'bottom', subCategory: 'jeans' };
  for (const point of ['짧은 캡소매', '반팔 소매', '긴팔 소매', '7부 소매', '소매 기장', '손등 덮는 소매']) {
    assert.deepEqual(suggestPoint(top, point), [], point);
    assert.deepEqual(suggestPoint(sleeveless, point), [point]);
    assert.deepEqual(suggestPoint(pants, point), [point]);
  }
  for (const point of ['퍼프 소매', '소매 끝 밴딩', '소매 핀턱', '드롭숄더 소매', '소매 리본']) {
    assert.deepEqual(suggestPoint(top, point), [point]);
  }
  assert.deepEqual(suggestPoint(top, '손 등 덮는 소 매'), []);
});

test('기장 칸이 있을 때만 옷 길이 표현을 거르고 밑단 모양과 장식은 남긴다', () => {
  const pants = { clothingType: 'bottom', subCategory: 'jeans' };
  const menTop = { clothingType: 'top', targetGenders: ['men'] };
  const womenTop = { ...menTop, targetGenders: ['women'] };
  const leggings = { ...pants, subCategory: 'leggings' };
  for (const point of ['크롭 기장', '미니 기장', '롱 기장', '무릎 기장', '맥시 길이', '발목 기장']) {
    assert.deepEqual(suggestPoint(womenTop, point), [], point);
    assert.deepEqual(suggestPoint(pants, point), []);
    assert.deepEqual(suggestPoint(menTop, point), [point]);
    assert.deepEqual(suggestPoint(leggings, point), [point]);
  }
  for (const point of ['밑단 레이스', '언밸런스 밑단', '물결 밑단', '밑단 셔링']) {
    assert.deepEqual(suggestPoint(pants, point), [point]);
  }
  assert.deepEqual(suggestPoint(womenTop, '크롭기장'), []);
});

test('플레어, 와이드, 부츠컷, A라인 실루엣은 옷 종류와 관계없이 추천에 남긴다', () => {
  for (const [clothingType, subCategory] of [['top', 'tshirt'], ['outer', 'jacket'], ['dress', 'mini_dress'],
    ['bottom', 'mini_skirt'], ['bottom', 'jeans']]) {
    for (const targetGenders of [['women'], ['men']]) {
      for (const point of ['플레어 실루엣', '와이드 실루엣', '부츠컷 실루엣', 'A라인']) {
        assert.deepEqual(suggestPoint({ clothingType, subCategory, targetGenders }, point), [point]);
      }
    }
  }
});

test('고른 것과 핏 이름을 뺀 다음 원래 순서로 세 개만 남긴다', () => {
  const remaining = [...suggestions, { point: '사선 절개', evidence: '' }];
  const input = { clothingType: 'top', sellingPoints: ['라운드 넥'], featureSuggestions: [
    { point: '라운드넥', evidence: '' }, { point: '오버 핏', evidence: '' }, ...remaining,
  ] };
  assert.deepEqual(sellingPointSuggestions(input), remaining);
  assert.deepEqual(sellingPointSuggestions({ featureSuggestions: [...remaining, { point: '추가', evidence: '' }] }), remaining);
});

test('rewrites the chip and drops it from the AI ledger', () => {
  // AI 제안을 셀러가 고친 순간 'AI 제안' 표식도 원장에서 빠져야 한다
  assert.deepEqual(applySellingPointEdit({ ...base, index: 0, text: '두꺼운 골지 짜임' }), {
    sellingPoints: ['두꺼운 골지 짜임', '라운드넥'],
    aiSuggestedPoints: [],
  });
});

test('keeps other AI suggestions untouched', () => {
  assert.deepEqual(applySellingPointEdit({
    sellingPoints: ['골지 짜임', '라운드넥'],
    aiSuggestedPoints: ['골지 짜임', '라운드넥'],
    index: 1,
    text: '깊은 라운드넥',
  }), { sellingPoints: ['골지 짜임', '깊은 라운드넥'], aiSuggestedPoints: ['골지 짜임'] });
});

test('trims surrounding spaces', () => {
  assert.deepEqual(applySellingPointEdit({ ...base, index: 1, text: '  브이넥  ' })?.sellingPoints,
    ['골지 짜임', '브이넥']);
});

test('empty text deletes the chip, like the x button', () => {
  assert.deepEqual(applySellingPointEdit({ ...base, index: 0, text: '   ' }), {
    sellingPoints: ['라운드넥'],
    aiSuggestedPoints: [],
  });
});

test('no patch when the text did not change', () => {
  assert.equal(applySellingPointEdit({ ...base, index: 0, text: '골지 짜임' }), null);
  assert.equal(applySellingPointEdit({ ...base, index: 0, text: ' 골지 짜임 ' }), null);
});

test('reverts instead of creating a duplicate chip', () => {
  assert.equal(applySellingPointEdit({ ...base, index: 1, text: '골지 짜임' }), null);
});

test('ignores an out-of-range or missing index', () => {
  for (const index of [-1, 2, null, undefined, 1.5, '0']) {
    assert.equal(applySellingPointEdit({ ...base, index, text: '브이넥' }), null);
  }
});

test('empty text only cancels when deletion is not confirmed', () => {
  // 포커스 이탈(blur)로 목록이 줄면 뒤이어 오는 click 의 인덱스가 밀려 엉뚱한 칩이 열린다
  assert.equal(applySellingPointEdit({ ...base, index: 0, text: '', allowDelete: false }), null);
  assert.equal(applySellingPointEdit({ ...base, index: 0, text: '  ', allowDelete: false }), null);
});

test('strips invisible characters instead of saving a blank-looking chip', () => {
  // U+200B(zero-width space)만 남은 입력은 눈에 빈 칩인데 문구가 있는 것으로 저장됐다
  assert.deepEqual(applySellingPointEdit({ ...base, index: 0, text: '\u200B\uFEFF' }), {
    sellingPoints: ['라운드넥'],
    aiSuggestedPoints: [],
  });
  assert.deepEqual(applySellingPointEdit({ ...base, index: 1, text: '브이'+'\u200B'+'넥' })?.sellingPoints,
    ['골지 짜임', '브이넥']);
});

test('treats decomposed and composed Hangul as the same text', () => {
  // 조합형(NFD)으로 같은 문구를 넣으면 눈에 똑같은 칩이 두 개 생겼다
  assert.equal(applySellingPointEdit({ ...base, index: 0, text: '라운드넥'.normalize('NFD') }), null);
  assert.equal(applySellingPointEdit({ ...base, index: 0, text: '골지 짜임'.normalize('NFD') }), null);
  assert.deepEqual(applySellingPointEdit({ ...base, index: 1, text: '브이넥'.normalize('NFD') })?.sellingPoints,
    ['골지 짜임', '브이넥'.normalize('NFC')]);
});

test('does not throw on a non-string text', () => {
  assert.deepEqual(applySellingPointEdit({ ...base, index: 1, text: 123 })?.sellingPoints,
    ['골지 짜임', '123']);
  // null/undefined 는 빈 문구와 같게 다룬다 — 확정이면 삭제, 확정 아니면 취소
  assert.deepEqual(applySellingPointEdit({ ...base, index: 1, text: null })?.sellingPoints, ['골지 짜임']);
  assert.equal(applySellingPointEdit({ ...base, index: 1, text: undefined, allowDelete: false }), null);
});

test('survives a missing AI ledger', () => {
  assert.deepEqual(applySellingPointEdit({ sellingPoints: ['골지 짜임'], index: 0, text: '브이넥' }), {
    sellingPoints: ['브이넥'],
    aiSuggestedPoints: [],
  });
});
