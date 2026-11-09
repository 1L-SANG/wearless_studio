import { sectionRuns } from './sectionRuns.js';
import { getTemplate, sectionDefinition } from './catalog.js';

// Presentation-only sample. Never pass this board to persistence, validation or
// credit calculation; real blocks and their order remain the editor's state.
export function sectionDesignPreviewSample(blocks, key) {
  const run = sectionRuns(blocks).find(run => run.key === key);
  if (!run) return blocks;
  const photos = run.blocks.filter(block => block.source !== 'text');
  if (!photos.length) return blocks;
  const definition = sectionDefinition(getTemplate(key.split('.')[0]), key, run.kind, run.usage);
  const rotation = run.kind === 'wear' && definition.photoSlots.some(slot => slot.recipe?.set === 'rotation');
  const daily = run.kind === 'wear' && !rotation;
  const structural = photos.filter(block => block.slotKey && !block.similarSourceBlockId);
  const sources = daily ? photos : structural.length ? structural : photos;
  const size = daily ? 4 : rotation ? definition.photoSlots.length : sources.length;
  const instance = key.split('.').slice(1).join('.');
  const sample = Array.from({ length: size }, (_, index) => {
    const source = sources[index % sources.length];
    return { ...source, id: index < sources.length ? source.id : `preview:${source.id}:${index}`,
      ...(daily ? { slotKey: `${instance}.g${index + 1}` } : rotation ? {
        slotKey: `${instance}.${definition.photoSlots[index].slotKey}`,
        direction: definition.photoSlots[index].recipe.direction,
      } : {}) };
  });
  return [...blocks.slice(0, run.start), ...run.blocks.filter(block => block.source === 'text'), ...sample, ...blocks.slice(run.end)];
}
