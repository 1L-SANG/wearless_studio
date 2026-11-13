import { uid } from './ids.js';
import {
  STORYBOARD_SPACE_SET_EXAMPLES,
  inferStoryboardSpaceSet,
  spaceSetGroupId,
  storyboardSpaceSetById,
} from './storyboardSpaceSetCatalog.js';

const EXAMPLES = new Map(STORYBOARD_SPACE_SET_EXAMPLES.map(example => [example.id, example]));
const detached = () => ({ spaceGroupId: null, spaceVariation: null, spaceSetMemberOrder: null,
  setSelectionOrigin: null, horizonBackgroundMode: null, refScope: 'all' });

// A released member always owns a location contract, even when only one photo is used.
// The current instance survives only when the chosen member belongs to that same set.
export function spaceSetSelectionPatch(block, exampleId, { origin = 'user', preserveGroup = true } = {}) {
  const example = EXAMPLES.get(exampleId);
  const set = example && storyboardSpaceSetById(example.spaceSetId);
  const member = set?.members.find(item => item.exampleId === exampleId);
  if (!member) return block?.spaceGroupId ? detached() : { refScope: 'all' };
  const sameSet = preserveGroup && inferStoryboardSpaceSet(block?.spaceGroupId)?.id === set.id;
  return {
    spaceGroupId: sameSet ? block.spaceGroupId : spaceSetGroupId(set.id, uid('sg')),
    spaceVariation: set.spaceVariation,
    spaceSetMemberOrder: member.order,
    setSelectionOrigin: sameSet ? (block.setSelectionOrigin || origin) : origin,
    refScope: member.cutType === 'styling' ? 'pose' : 'all',
    horizonBackgroundMode: member.cutType === 'horizon'
      ? (sameSet ? (block.horizonBackgroundMode || 'reference') : 'reference') : null,
  };
}

export function singleMemberSpaceSet(block, { origin = 'auto' } = {}) {
  const patch = spaceSetSelectionPatch(block, block?.exampleId, { origin, preserveGroup: false });
  if (patch.spaceGroupId) return { ...block, ...patch };
  const { spaceGroupId, spaceVariation, spaceSetMemberOrder, setSelectionOrigin,
    horizonBackgroundMode, ...single } = block || {};
  return { ...single, refScope: 'all' };
}
