/* =============================================================
   lib/storyboardExampleShuffle — 섹션 단위 '예시 셔플' (스펙 2026-08-14 §4)

   각 컷은 정체(컷 종류·샷·색상)를 유지한 채 같은 후보군 안에서만 생성예시를
   재추첨하고, 공간 세트는 세트 단위로 같은 조건의 다른 세트로 교체한다.
   고정(사용자 선택 = exampleSelectionOrigin 'user' / setSelectionOrigin 'user')은
   건드리지 않는다. 바꿀 것이 없으면 원본 참조를 그대로 돌려준다.
   ============================================================= */

import { assignGenerationExamples } from './generationExamples.js';
import { clearExampleSelection } from './storyboardExampleStaleness.js';
import { groupConsecutiveSpaceRuns, replaceSpaceSetRun } from './storyboardSpaceSets.js';
import {
  inferStoryboardSpaceSet,
  spaceSetGroupId,
  storyboardSpaceSetsFor,
} from './storyboardSpaceSetCatalog.js';

// 자동 선택(재추첨 대상) 낱개 컷 — 세트 소속·수동 예시·사용자 고정은 제외.
const isRerollableFlat = (block, sectionId) => (
  !!block
  && block.sectionId === sectionId
  && block.source === 'ai'
  && !block.spaceGroupId
  && block.exampleChoice !== 'manual'
  && block.exampleSelectionOrigin === 'auto'
  && !!block.exampleId
);

// 섹션 안의 자동 선택 세트 run — 사용자가 고른 세트(setSelectionOrigin 'user')는 물론,
// 멤버 한 컷이라도 예시를 직접 고정(origin 'user')했으면 그 세트도 교체하지 않는다
// (스펙 §4 "고정 컷 제외", Codex 리뷰 #3).
function autoSetRuns(blocks, sectionId) {
  return groupConsecutiveSpaceRuns(blocks).filter((run) => (
    run.kind === 'space'
    && run.items[0]?.sectionId === sectionId
    && run.items.every((block) => (
      block.setSelectionOrigin !== 'user' && block.exampleSelectionOrigin !== 'user'
    ))
  ));
}

/* rotation: 같은 섹션에서 셔플을 연타할 때 세트 후보를 순환시키는 정수(횟수 카운터).
   uid: 새 세트 run 의 spaceGroupId 인스턴스 키 생성기(주입 — 테스트 결정성). */
export function shuffleSectionExamples(blocks, {
  sectionId, catalog, product, gender, rotation = 0, uid = null,
}) {
  const list = Array.isArray(blocks) ? blocks : [];
  if (!sectionId) return list;
  let next = list;

  // ① 공간 세트 — 세트 단위 교체(같은 성별·의류 종류·세트 타입의 다른 발행 세트로).
  for (const run of autoSetRuns(next, sectionId)) {
    const current = inferStoryboardSpaceSet(run.spaceGroupId);
    if (!current) continue;
    const candidates = storyboardSpaceSetsFor({ gender, clothingType: product?.clothingType })
      .filter((set) => set.setType === current.setType && set.id !== current.id);
    if (!candidates.length) continue;
    const currentIndex = candidates.findIndex((set) => set.id > current.id);
    const pickAt = ((currentIndex < 0 ? 0 : currentIndex) + rotation) % candidates.length;
    const set = candidates[pickAt];
    next = replaceSpaceSetRun(next, run.spaceGroupId, set, {
      spaceGroupId: spaceSetGroupId(set.id, uid ? uid('sg') : `shuffle-${rotation}`),
      setSelectionOrigin: 'auto',
    });
  }

  // ② 낱개 컷 — 선택을 비우고 기존 배정기로 재추첨(직전 예시는 회피).
  // 바꿀 것이 없으면 배열 참조를 보존한다(호출부의 "무변경" 판정 근거).
  const avoidByBlockId = {};
  for (const block of next) {
    if (isRerollableFlat(block, sectionId)) avoidByBlockId[block.id] = block.exampleId;
  }
  if (Object.keys(avoidByBlockId).length) {
    next = next.map((block) => (
      avoidByBlockId[block.id] === undefined
        ? block
        : { ...clearExampleSelection(block), exampleSelectionOrigin: null }
    ));
    next = assignGenerationExamples(next, {
      catalog, product, gender, avoidByBlockId,
    }).blocks;
  }
  return next;
}
