import { getTemplate, pageTemplatesCatalog, sectionDefinition } from './catalog.js';
import { pointPhotosForRun } from './assemble.js';
import { wearUsageForBlock } from './slots.js';
import { reuseSourceForSlot, openingSourceProduct } from './sectionDependencies.js';
import { baseDesignId } from './pairedDesigns.js';

const definitionFor = run => sectionDefinition(getTemplate(String(run.key).split('.')[0]), run.key, run.kind, run.usage);
const photosFor = (run, storyboard) => pointPhotosForRun(run.blocks, storyboard);
const matchesSlot = (key, slot) => slot === '*' || key === slot || key.startsWith(`${slot}.`)
  || key.startsWith(slot) && /^\d+$/.test(key.slice(slot.length));

export function defaultSectionDesignId(run, storyboard = run?.blocks || []) {
  if (!run) return null;
  const definition = definitionFor(run);
  return !photosFor(run, storyboard).length && definition.fallbackDesignId || definition.designId;
}

export function currentSectionDesignId(run, storyboard = run?.blocks || []) {
  if (!run) return null;
  const definition = definitionFor(run);
  return !photosFor(run, storyboard).length && definition.fallbackDesignId || baseDesignId(run.designId) || definition.designId;
}

// Count the layout's photo positions, including reuse positions. Counting assembled
// images would also count overflow photos and hide an incompatible empty position.
function countDesignPhotos(run, designId, storyboard, borrowedSlots = new Set()) {
  const definition = definitionFor(run), templateId = String(run.key).split('.')[0];
  const photos = photosFor(run, storyboard);
  const matches = (card, slot = '*') => {
    const key = String(card.slotKey || '').replace(`${definition.instanceKey}.`, '');
    return Boolean(key && (slot === '*' || key === slot || key.startsWith(`${slot}.`) || new RegExp(`^${slot}\\d+$`).test(key)));
  };
  const count = (list = []) => list.reduce((sum, node) => {
    if (node.whenTemplate && node.whenTemplate !== templateId) return sum;
    if (node.type === 'photo' || node.type === 'overlay') return sum + (borrowedSlots.has(node.slot) ? 0 : 1);
    const selected = node.slots ? node.slots.filter(slot => !borrowedSlots.has(slot)).length
      : borrowedSlots.has(node.slot) ? 0 : photos.filter(card => matches(card, node.slot)).length;
    if (node.type === 'photoGrid' || node.type === 'pointRows') return sum + selected;
    if (node.type === 'photoStack') return sum + selected * count(node.items);
    return sum + count(node.nodes) + count(node.items) + count(node.captions)
      + (node.columns || []).reduce((n, column) => n + count(column.nodes), 0);
  }, 0);
  return count(pageTemplatesCatalog.designs[designId]?.layout);
}

export function sectionDesignPhotoCount(run, designId, storyboard = run.blocks) {
  return countDesignPhotos(run, designId, storyboard);
}

// The opening may show the product photo again without generating another cut.
// Only an available borrowed source is free; otherwise its position counts.
export function sectionDesignGenerationCount(run, designId, storyboard = run.blocks, product = {}) {
  if (run.kind !== 'first') return sectionDesignPhotoCount(run, designId, storyboard);
  const definition = sectionDefinition(getTemplate(String(run.key).split('.')[0]), run.key, run.kind, run.usage, designId);
  const sourceProduct = openingSourceProduct(run, product);
  const borrowed = new Set((definition.photoSlots || []).filter(slot => slot.fill === 'reuse'
    && reuseSourceForSlot(storyboard, slot, sourceProduct)).map(slot => slot.slotKey));
  return countDesignPhotos(run, designId, storyboard, borrowed);
}

// A color design must expose every sale color through a visible front position.
// Grid/stack slots expand with real cards; fixed slot lists have a finite capacity.
function displaysProductColors(run, design, storyboard, product) {
  if (run.kind !== 'color_product') return true;
  const definition = definitionFor(run), photos = photosFor(run, storyboard);
  const localKey = card => String(card.slotKey || '').replace(`${definition.instanceKey}.`, '');
  const fronts = photos.filter(card => matchesSlot(localKey(card), 'front'));
  const colors = product?.colors?.length ? product.colors.map(color => color.id) : fronts.map(card => card.colorId);
  const represented = new Set();
  const selected = slot => photos.filter(card => matchesSlot(localKey(card), slot));
  const visit = (list = [], current = []) => {
    for (const node of list) {
      if (node.whenTemplate && node.whenTemplate !== String(run.key).split('.')[0]) continue;
      const cards = node.slot === '$current' ? current : node.slots ? node.slots.flatMap(selected) : selected(node.slot);
      if (['photo', 'overlay', 'photoGrid', 'pointRows'].includes(node.type)) {
        for (const card of cards) if (fronts.includes(card)) represented.add(card.colorId);
      }
      visit(node.nodes, current); visit(node.captions, current);
      visit(node.items, node.type === 'photoStack' ? cards : current);
      for (const column of Array.isArray(node.columns) ? node.columns : []) visit(column.nodes, current);
      for (const group of [...(node.groups || []), ...(node.overlayGroups || [])]) visit(group.nodes, current);
    }
  };
  visit(design.layout);
  return colors.every(id => represented.has(id));
}

export function sectionDesignChoices(run, { storyboard = run?.blocks || [], product } = {}) {
  if (!run) return [];
  // A paired attachment is toggled on the stage, not a new layout choice. Apply
  // the unchanged candidate rules to its base layout and retain it on selection.
  run = { ...run, designId: baseDesignId(run.designId) };
  const definition = definitionFor(run);
  if (!photosFor(run, storyboard).length && definition.fallbackDesignId) return [pageTemplatesCatalog.designs[definition.fallbackDesignId]];
  const current = run.designId || definition.designId;
  const count = sectionDesignGenerationCount(run, current, storyboard, product);
  const wearBlock = run.blocks.find(block => ['horizon', 'styling'].includes(block.cutType)) || run.blocks[0];
  const usage = wearUsageForBlock(wearBlock) || run.usage;
  const cropped = run.blocks.some(block => block.pointItems?.some(item => item.kind === 'wornCrop'));
  const choices = Object.values(pageTemplatesCatalog.designs).filter(design => !design.pickerHidden && design.kind === run.kind
    && (run.kind !== 'wear' || design.usage === usage)
    && (!cropped || design.supportsWornCrop !== false)
    && sectionDesignGenerationCount(run, design.id, storyboard, product) === count
    && displaysProductColors(run, design, storyboard, product));
  const defaultId = defaultSectionDesignId(run, storyboard);
  return [...choices.filter(design => design.id === defaultId), ...choices.filter(design => design.id !== defaultId)];
}
