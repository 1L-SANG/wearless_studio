import { PRODUCT_PRESENTATION_OPTIONS, productPresentationOf } from '../storyboardTaxonomy.js';
import { sectionRuns } from './sectionRuns.js';

export const isProductSectionPhoto = block => block?.pageSectionKind === 'color_product'
  && block.source === 'ai' && block.cutType === 'product' && block.shot === 'ghost';

export function productSectionState(blocks, key) {
  const runs = sectionRuns(blocks).filter(run => run.key === key);
  if (runs.length > 1) throw new Error('noncontiguous_storyboard_section');
  const photos = (runs[0]?.blocks || []).filter(isProductSectionPhoto);
  const modes = photos.map(productPresentationOf);
  const value = modes.length && modes[0] && modes.every(mode => mode === modes[0]) ? modes[0] : null;
  return { key, value, ids: photos.map(block => block.id), count: photos.length,
    status: !photos.length ? 'empty' : value ? 'selected' : modes.every(mode => !mode) ? 'missing' : 'mixed' };
}

export function changeProductSectionPresentation(blocks, key, value) {
  if (!PRODUCT_PRESENTATION_OPTIONS.some(option => option.value === value)) throw new Error('invalid_product_presentation');
  const state = productSectionState(blocks, key);
  if (!state.count || state.value === value) return blocks;
  const ids = new Set(state.ids);
  return blocks.map(block => ids.has(block.id) ? { ...block, productPresentation: value } : block);
}

export function productSectionNeedingSelection(blocks) {
  return sectionRuns(blocks).map(run => productSectionState(blocks, run.key))
    .find(state => ['missing', 'mixed'].includes(state.status)) || null;
}

// Call on deliberate insertion/move only. Merely loading an old board never fills
// missing values, and existing single-image/editor recipes stay independent.
export function inheritProductSectionPresentation(before, after) {
  const previous = new Map(before.map(block => [block.id, block]));
  return after.map(block => {
    if (!isProductSectionPhoto(block)) return block;
    const old = previous.get(block.id);
    if (old?.pageSectionKey === block.pageSectionKey && isProductSectionPhoto(old)) return block;
    const state = productSectionState(before, block.pageSectionKey);
    if (state.count && !state.value) throw new Error('product_presentation_required');
    const existingRun = before.some(item => item.pageSectionKey === block.pageSectionKey);
    return { ...block, productPresentation: state.value || (existingRun ? 'ghost' : productPresentationOf(block) || 'ghost') };
  });
}
