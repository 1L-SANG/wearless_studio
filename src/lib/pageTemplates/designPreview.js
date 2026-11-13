import { sectionRuns } from './sectionRuns.js';
import { getTemplate, sectionDefinition } from './catalog.js';
import { wearUsageForBlock } from './slots.js';
import { pointPhotosForRun } from './assemble.js';

// Presentation-only sample. Never pass this board to persistence, validation or
// credit calculation; real blocks and their order remain the editor's state.
export function sectionDesignPreviewSample(blocks, key) {
  const run = sectionRuns(blocks).find(run => run.key === key);
  if (!run) return blocks;
  if (run.kind === 'points') {
    const lead = run.blocks.find(block => block.source === 'text' && Array.isArray(block.pointItems));
    const shown = pointPhotosForRun(run.blocks, blocks).slice(0, 2);
    const sourceIds = new Set(shown.filter(photo => !photo.pointItem).map(photo => photo.id));
    const items = shown.map(photo => photo.pointItem || lead?.pointItems.find(item => item.kind === 'detail' && item.sourceBlockId === photo.id)).filter(Boolean);
    const sample = run.blocks.filter(block => block.source === 'text' || sourceIds.has(block.id))
      .map(block => block === lead ? { ...block, pointItems: items } : block);
    return [...blocks.slice(0, run.start), ...sample, ...blocks.slice(run.end)];
  }
  const photos = run.blocks.filter(block => block.source !== 'text');
  if (!photos.length) return blocks;
  const definition = sectionDefinition(getTemplate(key.split('.')[0]), key, run.kind, run.usage);
  const rotation = run.kind === 'wear' && definition.photoSlots.some(slot => slot.recipe?.set === 'rotation');
  const daily = run.kind === 'wear' && wearUsageForBlock(photos[0]) === 'styling';
  const structural = photos.filter(block => block.slotKey && !block.similarSourceBlockId);
  const sources = daily ? photos : structural.length ? structural : photos;
  const size = daily ? 4 : run.kind === 'wear' ? 3 : sources.length;
  const instance = key.split('.').slice(1).join('.');
  const sample = Array.from({ length: size }, (_, index) => {
    const source = sources[index % sources.length];
    return { ...source, id: index < sources.length ? source.id : `preview:${source.id}:${index}`,
      // A canonical sample group makes the layout grammar visible even when
      // this page currently contains only two-member location groups. These
      // synthetic bindings never leave the preview path.
      ...(run.kind === 'wear' ? { spaceGroupId: `preview:${key}` } : {}),
      ...(daily ? { slotKey: `${instance}.g${index + 1}` } : rotation ? {
        slotKey: `${instance}.${definition.photoSlots[index].slotKey}`,
        direction: definition.photoSlots[index].recipe.direction,
      } : {}) };
  });
  return [...blocks.slice(0, run.start), ...run.blocks.filter(block => block.source === 'text'), ...sample, ...blocks.slice(run.end)];
}
