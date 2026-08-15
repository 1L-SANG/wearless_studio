import test from 'node:test';
import assert from 'node:assert/strict';

import { uniqueGenerationCutCount } from '../../src/lib/generationCutCount.js';
import { shuffleSectionExamples } from '../../src/lib/storyboardExampleShuffle.js';
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
