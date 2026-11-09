import { getTemplate, pageTemplatesCatalog, sectionDefinition } from './catalog.js';

// Page order is the saved list, including the text runs hidden from the outline.
export function sectionRuns(blocks = []) {
  const runs = [];
  blocks.forEach((block, index) => {
    let run = runs.at(-1);
    if (run?.key !== block.pageSectionKey) {
      run = { key: block.pageSectionKey, kind: block.pageSectionKind, usage: block.pageSectionUsage,
        designId: block.pageDesignId, sectionId: block.sectionId, start: index, end: index, blocks: [] };
      runs.push(run);
    }
    run.blocks.push(block);
    run.end = index + 1;
  });
  return runs;
}

export function isTextOnlySection(run) {
  if (run.kind === 'fabric' && !(run.blocks || []).some(block => block.source !== 'text')) return true;
  const template = getTemplate(String(run.key).split('.')[0]);
  const section = template && sectionDefinition(template, run.key, run.kind, run.usage);
  return pageTemplatesCatalog.kinds[run.kind]?.storyboardTextCard === 'only' && !section?.photoSlots?.length;
}

export const visibleSectionRuns = blocks => sectionRuns(blocks).filter(run => !isTextOnlySection(run));

export function sectionInsertionIndex(blocks, beforeSectionKey = null) {
  if (!beforeSectionKey) return blocks.length;
  const run = sectionRuns(blocks).find(item => item.key === beforeSectionKey);
  if (!run || run.kind === 'first') return -1;
  return run.start;
}

export function moveTemplateSection(blocks, key, beforeSectionKey = null) {
  const run = sectionRuns(blocks).find(item => item.key === key);
  if (!run || run.kind === 'first' || beforeSectionKey === key) return blocks;
  const remaining = blocks.filter(block => block.pageSectionKey !== key);
  const index = sectionInsertionIndex(remaining, beforeSectionKey);
  if (index < 0) return blocks;
  const next = [...remaining.slice(0, index), ...run.blocks, ...remaining.slice(index)];
  return next.every((block, i) => block === blocks[i]) ? blocks : next;
}

export function changeTemplateSectionDesign(blocks, key, designId) {
  const run = sectionRuns(blocks).find(item => item.key === key);
  if (!run || run.designId === designId || pageTemplatesCatalog.designs[designId]?.kind !== run.kind) return blocks;
  return blocks.map(block => block.pageSectionKey === key ? { ...block, pageDesignId: designId } : block);
}
