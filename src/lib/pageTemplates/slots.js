import { getTemplate, sectionDefinition, pageTemplatesCatalog } from './catalog.js';

const usageForRecipe = recipe => recipe?.cutType === 'horizon' || ['sequence', 'rotation'].includes(recipe?.set)
  ? 'fit' : recipe?.cutType === 'styling' || recipe?.set === 'styling' ? 'styling' : undefined;

export function wearUsageForBlock(block, catalog = pageTemplatesCatalog) {
  if (block?.pageSectionKind !== 'wear') return undefined;
  const physical = usageForRecipe(block) || usageForRecipe(block.recipe);
  if (physical) return physical;
  const template = getTemplate(String(block.pageSectionKey || '').split('.')[0], catalog);
  const original = template?.sections.find(section => `${template.id}.${section.instanceKey}` === block.pageSectionKey);
  const recipe = original?.photoSlots?.find(slot => slot.recipe)?.recipe;
  return usageForRecipe(recipe) || catalog.designs[block.pageDesignId]?.usage || block.pageSectionUsage
    || Object.entries(catalog.kinds.wear.namesByUsage).find(([, name]) => name === block.name)?.[0];
}

export const templateSectionName = (block, catalog = pageTemplatesCatalog) => {
  const kind = catalog.kinds[block?.pageSectionKind];
  if (!kind?.namesByUsage) return kind?.name || block?.name;
  // Saved titles and old usage values remain intact; photos and recipes name the run.
  const usage = wearUsageForBlock(block, catalog);
  return kind.namesByUsage[usage] || kind.name || block.name;
};

// Slot constraints are read from the shared catalog, never trusted from a client patch.
export function photoSlotForBlock(block, { includeReuse = false } = {}) {
  if (block?.source !== 'ai' || !block.slotKey) return null;
  const template = getTemplate(String(block.pageSectionKey || '').split('.')[0]);
  if (!template) return null;
  const section = sectionDefinition(template, block.pageSectionKey, block.pageSectionKind, block.pageSectionUsage, block.pageDesignId);
  const key = String(block.slotKey).replace(`${section.instanceKey}.`, '');
  const slot = section.photoSlots?.find(slot => (slot.fill === 'generate' || includeReuse)
    && (key === slot.slotKey || key.startsWith(`${slot.slotKey}.`) || new RegExp(`^${slot.slotKey}\\d+$`).test(key))) || null;
  return slot && block.pageSectionKind === 'wear' && block.pageSectionUsage === 'styling'
    ? { ...slot, recipe: { ...slot.recipe, allowedCutTypes: ['styling', 'horizon'] } } : slot;
}
