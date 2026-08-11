import test from 'node:test';
import assert from 'node:assert/strict';

import { applySellingPointEdit } from '../../src/features/analysis/sellingPoints.js';

const base = { sellingPoints: ['골지 짜임', '라운드넥'], aiSuggestedPoints: ['골지 짜임'] };

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
