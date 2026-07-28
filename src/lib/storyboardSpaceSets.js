const defaultBlock = (item) => item;

const clearLayoutRow = (block) => {
  const { layoutRowId: _layoutRowId, layoutRowVersion: _layoutRowVersion, ...single } = block;
  return single;
};

const clearExample = (block) => ({
  ...block,
  exampleId: null,
  exampleSelectionOrigin: null,
  thumb: block.baseThumb || block.thumb,
  baseThumb: null,
});

const clearSpace = (block) => {
  const { spaceGroupId: _spaceGroupId, spaceVariation: _spaceVariation, ...single } = block;
  return single;
};

function roleForSetMember(sectionRole, cutType, previousRole) {
  if (sectionRole === 'fit') {
    if (cutType === 'horizon') return 'fit';
    if (cutType === 'mirror') return 'realWear';
    return 'coordination';
  }
  if (sectionRole === 'benefit' && ['hero', 'benefit'].includes(previousRole)) return previousRole;
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
    groups.push(members.length > 1
      ? { kind: 'space', spaceGroupId: groupId, items: members, start: index, end }
      : { kind: 'block', items: members, start: index, end });
    index = end;
  }
  return groups;
}

export function dissolveSingletonSpaceRuns(blocks) {
  const next = [...blocks];
  let changed = false;
  for (const run of groupConsecutiveSpaceRuns(blocks)) {
    const block = run.items[0];
    if (run.kind !== 'block' || !block?.spaceGroupId) continue;
    const index = next.findIndex((candidate) => candidate.id === block.id);
    if (index < 0) continue;
    next[index] = clearSpace(next[index]);
    changed = true;
  }
  return changed ? next : blocks;
}

export function dissolveSpaceSet(blocks, spaceGroupId) {
  let changed = false;
  const next = blocks.map((block) => {
    if (block.spaceGroupId !== spaceGroupId) return block;
    changed = true;
    return clearSpace(block);
  });
  return changed ? next : blocks;
}

export function moveBlockWithSpaceMembership(
  blocks,
  blockId,
  targetIndex,
  { targetSpaceGroupId = null, isPoseCompatible = () => true } = {},
) {
  const from = blocks.findIndex((block) => block.id === blockId);
  if (from < 0) return blocks;
  const next = [...blocks];
  let [moving] = next.splice(from, 1);
  const enteringSpace = !!targetSpaceGroupId && moving.source !== 'mine';
  if (enteringSpace) {
    const targetMember = next.find((block) => block.spaceGroupId === targetSpaceGroupId);
    moving = {
      ...clearLayoutRow(moving),
      spaceGroupId: targetSpaceGroupId,
      spaceVariation: targetMember?.spaceVariation || 'subtle',
      refScope: 'pose',
    };
    if (moving.exampleId && !isPoseCompatible(moving)) moving = clearExample(moving);
  } else {
    moving = clearSpace(clearLayoutRow(moving));
  }
  const adjustedTarget = from < targetIndex ? targetIndex - 1 : targetIndex;
  next.splice(Math.max(0, Math.min(adjustedTarget, next.length)), 0, moving);
  return dissolveSingletonSpaceRuns(next);
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
} = {}) {
  if (!set || !spaceGroupId) return [];
  return set.members.map((member, index) => {
    const previous = previousMembers[index] || template || {};
    const base = clearExample(clearLayoutRow({
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
      spaceVariation: 'subtle',
      refScope: 'pose',
      pose: 'auto',
      poseLabel: 'AI 자동',
    }));
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
