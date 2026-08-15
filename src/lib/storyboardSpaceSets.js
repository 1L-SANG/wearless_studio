import { spaceSetIdFromGroupId } from './storyboardSpaceSetCatalog.js';

const defaultBlock = (item) => item;

const clearLayoutRow = (block) => {
  const { layoutRowId: _layoutRowId, layoutRowVersion: _layoutRowVersion, ...single } = block;
  return single;
};

export function detachSpaceMembership(block) {
  const {
    spaceGroupId: _spaceGroupId,
    spaceVariation: _spaceVariation,
    spaceSetMemberOrder: _spaceSetMemberOrder,
    ...single
  } = block;
  return { ...single, refScope: 'all' };
}

function roleForSetMember(sectionRole, cutType, previousRole) {
  if (sectionRole === 'styling') {
    if (cutType === 'mirror') return 'realWear';
    return 'coordination';
  }
  if (sectionRole === 'studio') return 'fit';
  if (sectionRole === 'hooking' && ['hero', 'benefit'].includes(previousRole)) return previousRole;
  return previousRole;
}

export function groupConsecutiveSpaceRuns(items, getBlock = defaultBlock) {
  const groups = [];
  for (let index = 0; index < items.length;) {
    const first = getBlock(items[index]);
    const groupId = first?.spaceGroupId;
    if (!groupId) {
      groups.push({ kind: 'block', items: [items[index]], start: index, end: index + 1 });
      index += 1;
      continue;
    }
    let end = index + 1;
    while (end < items.length) {
      const candidate = getBlock(items[end]);
      if (candidate?.spaceGroupId !== groupId || candidate?.sectionId !== first.sectionId) break;
      end += 1;
    }
    const members = items.slice(index, end);
    groups.push({ kind: 'space', spaceGroupId: groupId, items: members, start: index, end });
    index = end;
  }
  return groups;
}

export function rekeySeparatedSpaceRuns(blocks, nextGroupId) {
  if (!Array.isArray(blocks) || !blocks.length) return blocks;
  const seen = new Set();
  const replacements = new Map();
  for (const run of groupConsecutiveSpaceRuns(blocks)) {
    if (run.kind !== 'space') continue;
    const originalGroupId = run.spaceGroupId;
    if (!seen.has(originalGroupId)) {
      seen.add(originalGroupId);
      continue;
    }
    if (typeof nextGroupId !== 'function') {
      throw new TypeError('nextGroupId is required to rekey a separated space run');
    }
    const setId = spaceSetIdFromGroupId(originalGroupId);
    if (!setId) continue;
    const replacementGroupId = nextGroupId(setId, run, originalGroupId);
    if (spaceSetIdFromGroupId(replacementGroupId) !== setId || seen.has(replacementGroupId)) {
      throw new Error('nextGroupId must return a unique instance id for the same space set');
    }
    seen.add(replacementGroupId);
    for (let index = run.start; index < run.end; index += 1) {
      replacements.set(index, replacementGroupId);
    }
  }
  if (!replacements.size) return blocks;
  return blocks.map((block, index) => replacements.has(index)
    ? { ...block, spaceGroupId: replacements.get(index) }
    : block);
}

export function nextUnusedSpaceSetMember(set, blocks) {
  if (!set?.members?.length) return null;
  const usedExampleIds = new Set((blocks || []).map((block) => block.exampleId).filter(Boolean));
  const usedOrders = new Set((blocks || []).map((block) => block.spaceSetMemberOrder).filter(Number.isFinite));
  return [...set.members]
    .sort((left, right) => left.order - right.order)
    .find((member) => (
      !usedExampleIds.has(member.exampleId)
      && !usedOrders.has(member.order)
    )) || null;
}

export function nextSpaceSetMemberReservation(set, blocks) {
  const member = nextUnusedSpaceSetMember(set, blocks);
  if (!member) return null;
  const host = (blocks || []).find((block) => block.spaceGroupId) || blocks?.[0] || {};
  return {
    member,
    blockPatch: {
      spaceGroupId: host.spaceGroupId,
      spaceVariation: host.spaceVariation || set.spaceVariation || 'subtle',
      refScope: 'pose',
      spaceSetMemberOrder: member.order,
      setSelectionOrigin: host.setSelectionOrigin || 'user',
    },
  };
}

export function dissolveSingletonSpaceRuns(blocks) {
  const next = [...blocks];
  let changed = false;
  for (const run of groupConsecutiveSpaceRuns(blocks)) {
    const block = run.items[0];
    if (run.items.length !== 1 || !block?.spaceGroupId) continue;
    const index = next.findIndex((candidate) => candidate.id === block.id);
    if (index < 0) continue;
    next[index] = detachSpaceMembership(next[index]);
    changed = true;
  }
  return changed ? next : blocks;
}

export function dissolveSpaceSet(blocks, spaceGroupId) {
  let changed = false;
  const next = blocks.map((block) => {
    if (block.spaceGroupId !== spaceGroupId) return block;
    changed = true;
    return detachSpaceMembership(block);
  });
  return changed ? next : blocks;
}

export function moveBlockWithSpaceMembership(
  blocks,
  blockId,
  targetIndex,
  { targetSpaceGroupId = null, nextGroupId = null } = {},
) {
  const from = blocks.findIndex((block) => block.id === blockId);
  if (from < 0) return blocks;
  const next = [...blocks];
  let [moving] = next.splice(from, 1);
  const staysInCurrentSpace = moving.source !== 'mine'
    && !!moving.spaceGroupId
    && moving.spaceGroupId === targetSpaceGroupId;
  if (staysInCurrentSpace) {
    moving = {
      ...clearLayoutRow(moving),
      refScope: 'pose',
    };
  } else {
    moving = detachSpaceMembership(clearLayoutRow(moving));
  }
  const adjustedTarget = from < targetIndex ? targetIndex - 1 : targetIndex;
  next.splice(Math.max(0, Math.min(adjustedTarget, next.length)), 0, moving);
  // 남은 멤버가 1개여도 세트를 풀지 않는다 — 세트 유지는 오너 확정(2026-07-29)
  return rekeySeparatedSpaceRuns(next, nextGroupId);
}

export function moveSpaceSetRun(blocks, spaceGroupId, targetIndex) {
  const run = groupConsecutiveSpaceRuns(blocks)
    .find((candidate) => candidate.kind === 'space' && candidate.spaceGroupId === spaceGroupId);
  if (!run) return blocks;
  const members = blocks.slice(run.start, run.end);
  const next = [...blocks];
  next.splice(run.start, members.length);
  const adjustedTarget = run.start < targetIndex ? targetIndex - members.length : targetIndex;
  next.splice(Math.max(0, Math.min(adjustedTarget, next.length)), 0, ...members);
  return next;
}

export function createSpaceSetMembers(set, template, {
  spaceGroupId,
  makeId = (_member, index) => `${spaceGroupId}-${index + 1}`,
  previousMembers = [],
  setSelectionOrigin = 'user',
  // 교체 시 넣을 멤버 목록 오버라이드 — 셔플이 기존 run 크기(예: 엔트리 2멤버)를
  // 유지할 때 쓴다. 없으면 카탈로그 멤버 전부(갤러리 세트 추가 = 통째 배치).
  members = null,
} = {}) {
  if (!set || !spaceGroupId) return [];
  return (members || set.members).map((member, index) => {
    const previous = previousMembers[index] || template || {};
    const base = clearLayoutRow({
      ...template,
      ...previous,
      id: previousMembers[index]?.id || makeId(member, index),
      source: 'ai',
      cutType: member.cutType,
      direction: member.direction,
      shot: member.shot,
      contentRole: roleForSetMember(
        previous.sectionRole || template?.sectionRole,
        member.cutType,
        previous.contentRole || template?.contentRole,
      ),
      spaceGroupId,
      spaceVariation: set.spaceVariation || 'subtle',
      refScope: 'pose',
      exampleId: member.exampleId || null,
      exampleSelectionOrigin: member.exampleId ? 'user' : null,
      setSelectionOrigin,
      thumb: member.thumb || member.thumbUrl || member.allUrl || previous.thumb || template?.thumb,
      baseThumb: null,
      spaceSetMemberOrder: member.order || index + 1,
      pose: 'auto',
      poseLabel: 'AI 자동',
      // `내 사진`은 사용자가 그 블록에 직접 붙인 장면 참고다. 다른 공간 세트를
      // 선택하거나 교체할 때 이전 블록의 사진을 새 멤버로 암묵 복사하면, 발행된
      // 세트 plate/예시와 경쟁하는 숨은 입력이 된다. 세트 선택은 새 촬영 결정을
      // 만드는 동작이므로 명시적으로 비운다.
      refImages: [],
      refAssetIds: [],
    });
    return member.cutType === 'product'
      ? { ...base, matchIds: [], faceExposure: null, outerClosureState: null }
      : base;
  });
}

export function replaceSpaceSetRun(blocks, oldSpaceGroupId, set, options = {}) {
  const run = groupConsecutiveSpaceRuns(blocks)
    .find((candidate) => candidate.kind === 'space' && candidate.spaceGroupId === oldSpaceGroupId);
  if (!run) return blocks;
  const previousMembers = blocks.slice(run.start, run.end);
  const members = createSpaceSetMembers(set, previousMembers[0], {
    ...options,
    previousMembers,
  });
  if (!members.length) return blocks;
  const next = [...blocks];
  next.splice(run.start, previousMembers.length, ...members);
  return next;
}

export function insertSpaceSet(blocks, index, set, template, options = {}) {
  const members = createSpaceSetMembers(set, template, options);
  if (!members.length) return blocks;
  const next = [...blocks];
  next.splice(Math.max(0, Math.min(index, next.length)), 0, ...members);
  return next;
}
