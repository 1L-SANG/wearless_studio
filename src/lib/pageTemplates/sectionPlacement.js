import { getTemplate } from './catalog.js';
import { sectionRuns } from './sectionRuns.js';
import { wearUsageForBlock } from './slots.js';

// Return a run boundary, never a photo index. Hidden text runs keep their place.
export function automaticSectionInsertionKey(blocks, kind, usage) {
  const runs = sectionRuns(blocks);
  const same = run => run.kind === kind && (kind !== 'wear' || wearUsageForBlock(run.blocks[0]) === usage);
  const last = runs.findLastIndex(same);
  if (last >= 0) return runs[last + 1]?.key || null;
  const template = getTemplate(String(blocks[0]?.pageSectionKey || '').split('.')[0]);
  const sections = template?.sections || [];
  const rank = (sectionKind, sectionUsage) => {
    const index = sections.findIndex(section => section.kind === sectionKind && (sectionKind !== 'wear' || section.usage === sectionUsage));
    // A showroom's newly added everyday photos follow its opening studio album.
    if (index < 0 && sectionKind === 'wear') return sections.findIndex(section => section.kind === 'wear') + .5;
    return index < 0 ? sections.length : index;
  };
  const target = rank(kind, usage);
  return runs.find(run => run.kind !== 'first' && rank(run.kind, wearUsageForBlock(run.blocks[0])) > target)?.key || null;
}
