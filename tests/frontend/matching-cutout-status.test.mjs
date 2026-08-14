import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { mergeMatchClothing, toMatchItem } from '../../src/lib/api/matchingItems.js';

test('toMatchItem 이 cutoutStatus 를 통과시킨다', () => {
  const item = toMatchItem({ id: 'custom_x', name: '내 바지', isCustom: true,
    cutoutStatus: 'processing' }, null);
  assert.equal(item.cutoutStatus, 'processing');
});

test('시드 아이템은 cutoutStatus 가 없다', () => {
  const item = toMatchItem({ id: 'match_women_top_01', name: '시드' }, null);
  assert.equal(item.cutoutStatus ?? null, null);
});

// 2026-08-13 리뷰 M8 — 5초 폴링이 analysis 를 통째로 치환하면 저장 왕복 중이던 편집이
// 한 틱 되돌아간다. 폴링이 새로 아는 건 매칭 목록뿐이다.
test('폴링 머지는 matchClothing 만 갈아끼우고 편집 중인 값을 지킨다', () => {
  const editing = { suggestedName: '방금 고친 이름', sellingPoints: ['골지'],
    matchClothing: [{ id: 'custom_x', cutoutStatus: 'processing' }] };
  const polled = { suggestedName: '서버에 저장된 옛 이름', sellingPoints: [],
    matchClothing: [{ id: 'custom_x', cutoutStatus: 'ready' }] };

  const merged = mergeMatchClothing(editing, polled);

  assert.equal(merged.suggestedName, '방금 고친 이름');
  assert.deepEqual(merged.sellingPoints, ['골지']);
  assert.equal(merged.matchClothing[0].cutoutStatus, 'ready');
});

test('폴링 응답이 비정상이면 아무것도 바꾸지 않는다', () => {
  const prev = { suggestedName: '그대로', matchClothing: [] };
  assert.equal(mergeMatchClothing(prev, undefined), prev);
  assert.equal(mergeMatchClothing(prev, {}), prev);
  assert.equal(mergeMatchClothing(prev, { matchClothing: null }), prev);
  assert.equal(mergeMatchClothing(null, { matchClothing: [] }), null);
});

test('누끼 폴링 경로는 전체 치환(applyAnalysisReplacement)을 쓰지 않는다', () => {
  const source = readFileSync(
    new URL('../../src/features/analysis/AnalysisForm.jsx', import.meta.url), 'utf8',
  );
  const effect = source.slice(
    source.indexOf('const hasPendingCutout'),
    source.indexOf('const commitSp ='),
  );
  assert.match(effect, /applyMatchClothingRefresh\(actual\)/);
  assert.doesNotMatch(effect, /applyAnalysisReplacement/);
  // 인터벌 클로저의 옛 analysis 로 덮어쓰지 않게 함수형 업데이트여야 한다
  assert.match(source, /onAnalysisReplace\(\(prev\) => mergeMatchClothing\(prev, nextAnalysis\)\)/);
});
