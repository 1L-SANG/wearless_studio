import test from 'node:test';
import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';

import { uniqueGenerationCutCount } from '../../src/lib/generationCutCount.js';
import { shuffleSectionExamples } from '../../src/lib/storyboardExampleShuffle.js';
import genExamples from '../../src/data/genExamples.json' with { type: 'json' };
import { entryStylingMembers } from '../../src/lib/storyboardEntryPlacement.js';
import {
  spaceSetGroupId,
  spaceSetIdFromGroupId,
  storyboardSpaceSetsFor,
} from '../../src/lib/storyboardSpaceSetCatalog.js';

const ai = (id, extra = {}) => ({
  id, source: 'ai', sectionId: 'sec-a', sectionRole: 'styling',
  cutType: 'styling', shot: 'full', direction: 'front', colorId: 'base',
  exampleId: 'ex-1', pose: 'auto', refScope: 'all', matchIds: [], ...extra,
});

test('크레딧 견적은 동일 설정 복제를 1장으로 접는다 — 서버 dedup(ADR-0011)과 동일 규칙', () => {
  const blocks = [
    ai('a'),
    ai('a-copy'),                          // 완전 동일 복제 → 접힘
    ai('b', { colorId: 'ivory' }),         // 색상 다름 → 별도 생성(변주 대상)
    ai('c', { direction: 'back' }),        // 방향 다름 → 별도 생성
    ai('set-1', { spaceGroupId: 'sg1' }),  // 세트 멤버는 접지 않는다
    ai('set-2', { spaceGroupId: 'sg1' }),
    { id: 'mine', source: 'mine', ownImages: ['m.png'] },   // 내 사진은 생성 아님
  ];
  assert.equal(uniqueGenerationCutCount(blocks), 5);
  assert.equal(uniqueGenerationCutCount([]), 0);
  assert.equal(uniqueGenerationCutCount(null), 0);
});

test('세트별 셔플은 기존 run 크기를 유지한다 — 엔트리 2멤버가 3멤버로 늘지 않는다', () => {
  // 엔트리 시드와 동일하게: 카탈로그 스타일링 세트의 엔트리 멤버 2개만 배치된 run.
  const sets = storyboardSpaceSetsFor({ gender: 'women', clothingType: 'top' })
    .filter((set) => set.setType === 'styling');
  assert.ok(sets.length >= 2, '교체 후보가 필요하다');
  const current = sets[0];
  const groupId = spaceSetGroupId(current.id, 'count-test');
  const members = entryStylingMembers(current);
  assert.equal(members.length, 2);
  const blocks = members.map((member, index) => ai(`m-${index}`, {
    cutType: member.cutType, shot: member.shot, direction: member.direction,
    spaceGroupId: groupId, spaceSetMemberOrder: member.order,
    setSelectionOrigin: 'auto', exampleSelectionOrigin: 'auto', refScope: 'pose',
    exampleId: member.exampleId,
  }));

  const next = shuffleSectionExamples(blocks, {
    sectionId: 'sec-a',
    catalog: [],
    product: { clothingType: 'top' },
    gender: 'women',
    rotation: 1,
    onlySpaceGroupId: groupId,
  });

  assert.notEqual(next, blocks, '세트가 교체되어야 한다');
  const nextGroupIds = [...new Set(next.map((block) => block.spaceGroupId))];
  assert.equal(nextGroupIds.length, 1);
  assert.notEqual(spaceSetIdFromGroupId(nextGroupIds[0]), current.id);
  // 핵심: 멤버 수가 늘지 않는다(Codex PR#142 리뷰 — 2멤버 → 3멤버 확장 버그).
  assert.equal(next.length, 2);
  // 스타일링 교체는 엔트리 규칙(풀+미디움 우선)을 따른다.
  assert.deepEqual(next.map((block) => block.shot).sort(), ['full', 'medium']);
});


/* ---------- 컷 하나만 다시 뽑기(카드별 셔플 아이콘, 2026-08-16) ---------- */

test('낱개 셔플은 그 컷만 바꾸고 나머지는 손대지 않는다', () => {
  // 빈 카탈로그로 돌리면 재배정이 아예 안 일어나 원본 배열이 그대로 나오고, 어떤 단정이든
  // 통과한다(2026-08-17 리뷰: 헛도는 테스트). 실제 예시 카탈로그로 돌린다.
  const catalog = genExamples;
  assert.ok(catalog.length > 0, '카탈로그가 비면 이 테스트는 아무것도 검사하지 않는다');
  const blocks = [ai('a'), ai('b'), ai('c')];
  const next = shuffleSectionExamples(blocks, {
    sectionId: 'sec-a', catalog, product: { clothingType: 'top' }, gender: 'women',
    onlyBlockId: 'b',
  });
  assert.notEqual(next, blocks, '실제로 재배정이 일어나야 검사가 의미 있다');
  assert.equal(next.length, 3);
  assert.deepEqual(next.map((block) => block.id), ['a', 'b', 'c'], '순서 유지');
  // 옆 컷의 예시가 덩달아 바뀌면 "한 컷만 다시 뽑기"가 아니다(배정기는 보드 전체를 다시
  // 훑으므로 객체 정체성은 바뀔 수 있지만 내용은 그대로여야 한다).
  assert.equal(next[0].exampleId, blocks[0].exampleId);
  assert.equal(next[2].exampleId, blocks[2].exampleId);
  assert.equal(next[1].id, 'b');
});

test('낱개 셔플은 직접 고른 예시도 바꾼다 — 그 컷을 지목한 명시 조작이기 때문', () => {
  // 실제 발행 카탈로그를 써야 재추첨이 실제로 일어난다(빈 카탈로그로는 뽑을 후보가 없어
  // "무변경"이 정답이다 — 아래 별도 테스트에서 그 경계를 따로 고정한다).
  const seed = genExamples.find((example) => (
    example.cutType === 'styling' && example.shot === 'full' && !example.setOnly
  ));
  assert.ok(seed, '스타일링 풀샷 예시가 카탈로그에 있어야 한다');
  const blocks = [ai('pinned', {
    exampleId: seed.id, exampleSelectionOrigin: 'user', exampleChoice: 'manual',
  })];
  const next = shuffleSectionExamples(blocks, {
    sectionId: 'sec-a', catalog: genExamples, product: { clothingType: 'top' }, gender: 'women',
    onlyBlockId: 'pinned',
  });
  assert.notEqual(next, blocks, '고정 컷도 재추첨 대상이다');
  assert.ok(next[0].exampleId, '예시를 비운 채 두지 않는다');
  // 'manual' 표식이 남으면 배정기가 건너뛰어 예시가 빈 채로 남는다.
  assert.ok(!('exampleChoice' in next[0]), '자동 배정으로 되돌린다');
  // 셔플로 새로 뽑힌 예시는 더는 '사용자 고정'이 아니다 — 다음 섹션 셔플의 대상이 된다.
  assert.equal(next[0].exampleSelectionOrigin, 'auto');
});

test('낱개 셔플이 뽑을 후보가 없으면 컷을 비우지 않고 원본을 돌려준다', () => {
  // 조건이 바뀌어 저장된 예시가 더는 발행되지 않는 컷에서 셔플을 눌러도, 예시를 지운 채
  // 빈 카드로 남기면 안 된다 — 호출부가 "바꿀 수 있는 예시가 없어요"로 안내한다(자체 리뷰).
  const blocks = [ai('stale', { exampleId: 'ex-gone' })];
  const next = shuffleSectionExamples(blocks, {
    sectionId: 'sec-a', catalog: [], product: { clothingType: 'top' }, gender: 'women',
    onlyBlockId: 'stale',
  });
  assert.equal(next, blocks, '원본 참조 그대로 = 무변경');
  assert.equal(next[0].exampleId, 'ex-gone', '예시가 비워지지 않는다');
});

test('낱개 셔플 대상이 아니면 원본을 그대로 돌려준다 — 세트 멤버·내 사진·예시 없는 컷', () => {
  const opts = { sectionId: 'sec-a', catalog: [], product: { clothingType: 'top' }, gender: 'women' };
  const setMember = [ai('s', { spaceGroupId: 'sg1' })];
  assert.equal(shuffleSectionExamples(setMember, { ...opts, onlyBlockId: 's' }), setMember);
  const mine = [{ id: 'm', source: 'mine', sectionId: 'sec-a', ownImages: ['m.png'] }];
  assert.equal(shuffleSectionExamples(mine, { ...opts, onlyBlockId: 'm' }), mine);
  const empty = [ai('e', { exampleId: null })];
  assert.equal(shuffleSectionExamples(empty, { ...opts, onlyBlockId: 'e' }), empty);
  const other = [ai('x', { sectionId: 'sec-b' })];
  assert.equal(shuffleSectionExamples(other, { ...opts, onlyBlockId: 'x' }), other, '다른 섹션은 대상 아님');
  assert.equal(shuffleSectionExamples([ai('a')], { ...opts, onlyBlockId: 'nope' }).length, 1);
});


test('낱개 셔플은 배정기에게 그 컷만 맡긴다 — 옆 컷이 조용히 바뀌면 안 된다', () => {
  const shuffle = readFileSync(new URL('../../src/lib/storyboardExampleShuffle.js', import.meta.url), 'utf8');
  const one = shuffle.slice(shuffle.indexOf('if (onlyBlockId)'), shuffle.indexOf('// ① 공간 세트'));
  // onlyBlockIds 를 안 넘기면 배정기가 보드 전체를 다시 훑는다(2026-08-17 리뷰).
  assert.match(one, /onlyBlockIds: \[onlyBlockId\]/);
});
