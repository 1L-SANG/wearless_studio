import { isStoryboardSpaceSetEligible } from './storyboardSpaceSetCatalog.js';
import { storedExampleConditionStatus } from './generationExamples.js';
import { detachSpaceMembership } from './storyboardSpaceSets.js';
import { stripHookFrameFields } from './storyboardHookFrame.js';

export const CATALOG_DRAG_MIME = 'application/x-wearless-set';

export function resolveCatalogDrag(payload, sets, context = {}) {
  if (!payload || typeof payload !== 'object' || context.locked
    || context.canAdd === false || context.targetSpaceGroupId) return null;
  const set = sets.find((candidate) => candidate.id === payload.setId);
  if (!set) return null;
  const choices = independentSpaceSetChoices(set, context);
  if (payload.exampleId == null) {
    return choices.length === set.members.length && choices.length ? { set, member: null } : null;
  }
  const choice = choices.find(({ member }) => member.exampleId === payload.exampleId);
  return choice ? { set, member: choice.member } : null;
}

// A catalog insert never splits a saved row, hook frame, or coherent set run.
export function insertCatalogBlocks(blocks, index, added) {
  const target = Math.max(0, Math.min(index, blocks.length));
  const before = blocks[target - 1];
  const after = blocks[target];
  if (before && after && ['spaceGroupId', 'hookFrameId', 'layoutRowId'].some((field) => (
    before[field] && before[field] === after[field]
  ))) throw new Error('catalog_drop_inside_bundle');
  const next = [...blocks];
  next.splice(target, 0, ...added);
  return next;
}

// 세트 갤러리에서 고른 컷 하나가 들어갈 섹션과 자리. 섹션 끝에 붙이고, 섹션이 비어 있으면
// 갤러리를 연 자리(fallbackIndex)에 넣는다. 빈 섹션 자리표시(empty:*)는 첫 컷이 들어가면 실제
// 섹션으로 바뀌므로 매번 다시 찾는다. 2026-09-26: 빈 섹션 첫 컷이 후킹 앞(0)에 들어갔고,
// 자리만 고치면 두 번째 컷부터 같은 역할 섹션이 둘로 쪼개졌다.
export function catalogMemberTarget(blocks, { targetSid, targetRole, spaceGroupId, fallbackIndex }) {
  const host = spaceGroupId ? blocks.find((block) => block.spaceGroupId === spaceGroupId) : null;
  const sectionRole = targetRole || host?.sectionRole;
  const live = (id) => id && blocks.some((block) => block.sectionId === id && block.sectionRole === sectionRole);
  const sectionId = live(targetSid) ? targetSid
    : live(host?.sectionId) ? host.sectionId
    : blocks.find((block) => block.sectionRole === sectionRole)?.sectionId || targetSid || host?.sectionId;
  const end = blocks.reduce((last, block, index) => (
    block.sectionId === sectionId && block.sectionRole === sectionRole ? index + 1 : last
  ), -1);
  return { sectionId, sectionRole, index: end >= 0 ? end : (fallbackIndex ?? blocks.length) };
}

export function setCatalogDragImage(event, members) {
  if (!event.dataTransfer?.setDragImage || !members?.length) return () => {};
  const ghost = document.createElement('div');
  ghost.className = 'sb-catalog-drag-ghost';
  ghost.setAttribute('aria-hidden', 'true');
  members.slice(0, 3).forEach((member) => {
    const image = document.createElement('img');
    image.className = 'sb-catalog-drag-layer';
    image.src = member.thumb || member.thumbUrl || member.allUrl || '';
    image.alt = '';
    ghost.appendChild(image);
  });
  const count = document.createElement('span');
  count.className = 'sb-catalog-drag-count';
  count.textContent = members.length + '컷';
  ghost.appendChild(count);
  document.body.appendChild(ghost);
  event.dataTransfer.setDragImage(ghost, 65, 85);
  return () => ghost.remove();
}

export function spaceSetSupportsSection(set, sectionRole) {
  return sectionRole === 'studio'
    ? String(set?.setType || '').startsWith('horizon') && set.members.every(member => member.cutType === 'horizon')
    : sectionRole === 'styling' && set?.setType === 'styling' && set.members.every(member => ['styling', 'mirror'].includes(member.cutType));
}

export function independentSpaceSetChoices(set, { catalog = [], gender, clothingType, sectionRole } = {}) {
  if (!spaceSetSupportsSection(set, sectionRole) || !isStoryboardSpaceSetEligible(set, { gender, clothingType })) return [];
  return set.members.flatMap(member => {
    const example = catalog.find(candidate => candidate.id === member.exampleId);
    if (!example || example.spaceSetId !== set.id || example.cutType !== member.cutType
      || example.shot !== member.shot || example.direction !== member.direction
      || storedExampleConditionStatus(example, { cutType: member.cutType, clothingType, gender, refScope: 'all' }) !== 'valid') return [];
    return [{ member, example }];
  });
}

// Independent catalog cuts use their published complete reference, never a set plate/pose binding.
export function createIndependentCatalogMembers(set, exampleIds, template, { makeId, ...context }) {
  const requested = new Set(exampleIds);
  const choices = independentSpaceSetChoices(set, context).filter(({ member }) => requested.has(member.exampleId));
  if (!requested.size || choices.length !== requested.size || requested.size !== exampleIds.length) throw new Error('catalog_member_unavailable');
  const { layoutRowId, layoutRowVersion, setSelectionOrigin, ownImages, exampleChoice, ...base } = stripHookFrameFields(detachSpaceMembership(template || {}));
  return choices.map(({ member, example }, index) => ({ ...base, id: makeId(member, index), source: 'ai',
    sectionRole: context.sectionRole, contentRole: context.sectionRole === 'studio' ? 'fit' : member.cutType === 'mirror' ? 'realWear' : 'coordination',
    cutType: member.cutType, direction: member.direction, shot: member.shot, exampleId: example.id, exampleSelectionOrigin: 'user',
    refScope: 'all', thumb: example.thumb || example.assetUrl, baseThumb: null, pose: 'auto', poseLabel: 'AI 자동',
    angle: 'same', faceExposure: member.cutType === 'mirror' ? 'hide' : (base.faceExposure || 'same'), refImages: [], refAssetIds: [],
  }));
}
