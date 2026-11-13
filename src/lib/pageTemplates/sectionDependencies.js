import { uid } from '../ids.js';
import { singleMemberSpaceSet } from '../storyboardSetSelection.js';
import { getTemplate, pageTemplatesCatalog, sectionDefinition, walkLayout } from './catalog.js';
import { sectionRuns } from './sectionRuns.js';
import { wornCropRegions } from './seed.js';
import { productSectionState } from './productPresentation.js';
import { baseDesignId } from './pairedDesigns.js';
import { wornCropRegion, wornCropSourceAspect, sameWornCropSourceImage } from './wornCropGeometry.js';

const localSlot = block => String(block.slotKey || '').replace(`${String(block.pageSectionKey).split('.').slice(1).join('.')}.`, '');
const matches = (key, slot) => key === slot || key.startsWith(`${slot}.`) || new RegExp(`^${slot}\\d+$`).test(key);
const baseColorId = product => (product?.colors?.find(color => color.isBase) || product?.colors?.[0])?.id;

export function originalProductRun(blocks) {
  const template = getTemplate(String(blocks[0]?.pageSectionKey).split('.')[0]);
  const definition = template?.sections.find(section => section.kind === 'color_product');
  return definition && sectionRuns(blocks).find(run => run.key === `${template.id}.${definition.instanceKey}`) || null;
}

export function firstGhostModePending(blocks) {
  const run = originalProductRun(blocks);
  return Boolean(run && ['missing', 'mixed'].includes(productSectionState(blocks, run.key).status));
}

export function openingSourceProduct(run, product = {}) {
  if (run?.kind !== 'first' || product?.colors?.length) return product;
  return { ...product, colors: [{ id: run.blocks.find(block => block.source !== 'text')?.colorId, isBase: true }] };
}

// Reordering a source section must not silently pick a different source. The
// catalog's original instance owns the reference. Removing it creates an
// independent slot instead of borrowing from a different same-kind section.
export function reuseSourceForSlot(blocks, slot, product = {}) {
  const ref = slot.reuseFrom;
  if (!ref) return null;
  const template = getTemplate(String(blocks[0]?.pageSectionKey).split('.')[0]);
  const original = template?.sections.find(section => section.kind === ref.kind && (!ref.usage || section.usage === ref.usage));
  const originalKey = original && `${template.id}.${original.instanceKey}`;
  const productReference = ref.kind === 'color_product' && ref.slot === 'front';
  if (productReference && slot.slotKey === 'ghost'
    && productSectionState(blocks, originalKey).value !== 'ghost') return null;
  const candidates = blocks.filter(block => block.source !== 'text' && !block.similarSourceBlockId
    && block.pageSectionKind === ref.kind && (!ref.usage || block.pageSectionUsage === ref.usage || block.pageSectionKey === originalKey)
    && (ref.color !== 'base' || String(block.colorId) === String(baseColorId(product))) && matches(localSlot(block), ref.slot)
    && (!productReference || (block.source === 'mine' && slot.slotKey !== 'ghost'
      ? (block.direction == null || block.direction === 'front') && Array.isArray(block.ownImages) && typeof block.ownImages[0] === 'string' && !!block.ownImages[0]
      : block.source === 'ai' && block.cutType === 'product' && block.shot === 'ghost' && block.direction === 'front')));
  const ordered = candidates.filter(block => block.pageSectionKey === originalKey);
  return ordered[ref.index || 0] || null;
}

export function reuseSlotsForBlock(block) {
  const template = getTemplate(String(block?.pageSectionKey).split('.')[0]);
  if (!template) return [];
  const used = new Set();
  walkLayout(pageTemplatesCatalog.designs[block.pageDesignId]?.layout, node => {
    if (node.slot) used.add(node.slot);
    for (const slot of node.slots || []) used.add(slot);
  });
  return sectionDefinition(template, block.pageSectionKey, block.pageSectionKind, block.pageSectionUsage, block.pageDesignId)
    .photoSlots?.filter(slot => slot.fill === 'reuse' && used.has(slot.slotKey)) || [];
}

export const isReuseFallback = block => ['ai', 'mine'].includes(block.source) && reuseSlotsForBlock(block).some(slot => localSlot(block) === slot.slotKey);

export function isWornCropSource(block) {
  if (block?.pageSectionKind !== 'wear') return false;
  if (block.source === 'mine') return Array.isArray(block.ownImages) && typeof block.ownImages[0] === 'string' && !!block.ownImages[0];
  return block.source === 'ai' && ['styling', 'horizon'].includes(block.cutType) && ['full', 'medium'].includes(block.shot);
}

function sourcePriority(block) {
  const template = getTemplate(String(block.pageSectionKey).split('.')[0]);
  const section = template && sectionDefinition(template, block.pageSectionKey, block.pageSectionKind, block.pageSectionUsage, block.pageDesignId);
  const slot = section?.photoSlots?.find(slot => matches(localSlot(block), slot.slotKey)) || section?.photoSlots?.[0];
  const cutType = block.cutType || slot?.recipe?.cutType;
  if (block.direction === 'front' && cutType === 'horizon') return slot?.recipe?.set === 'rotation' ? 0 : 1;
  if (block.direction === 'front' && cutType === 'styling' && ['full', 'medium'].includes(block.shot)) return 2;
  return 3;
}

export function preferredWornCropSource(blocks) {
  return blocks.filter(isWornCropSource).sort((a, b) => sourcePriority(a) - sourcePriority(b))[0] || null;
}

export function reconcileSectionDependencies(before, after, product = {}, {
  makeId = () => uid('blk'), cropRegions = wornCropRegions[product.clothingType] || wornCropRegions.top,
  cutResults = [],
} = {}) {
  const previousById = new Map(before.map(block => [block.id, block]));
  let next = after.filter(block => !isReuseFallback(previousById.get(block.id) || {})
    || reuseSlotsForBlock(block).some(slot => localSlot(block) === slot.slotKey));
  for (const run of sectionRuns(after)) {
    const first = run.blocks[0];
    for (const slot of reuseSlotsForBlock(first)) {
      const targetKey = `${run.key.split('.').slice(1).join('.')}.${slot.slotKey}`;
      const own = next.find(block => block.pageSectionKey === run.key && block.slotKey === targetKey && block.source !== 'text');
      const source = reuseSourceForSlot(next, slot, product);
      if (run.kind === 'fabric' && slot.slotKey === 'product' && !source) {
        // Fabric's optional photo is always a reuse, never a billable fallback.
        next = next.filter(block => block.id !== own?.id).map(block => block.pageSectionKey === run.key
          ? { ...block, pageDesignId: baseDesignId(block.pageDesignId) } : block);
        continue;
      }
      if (source && own) next = next.filter(block => block.id !== own.id);
      if (run.kind === 'first' && slot.slotKey === 'ghost') {
        if (source || firstGhostModePending(next)) continue;
        const fallback = {
          id: own?.id || makeId(), source: 'ai', taxonomyVersion: 4,
          pageSectionKey: run.key, pageSectionKind: 'first', pageDesignId: first.pageDesignId,
          sectionId: first.sectionId, sectionLayout: 'stack', sectionCustom: true, slotKey: targetKey,
          contentRole: slot.contentRole, sectionRole: 'product', cutType: 'product', shot: 'ghost',
          productPresentation: 'ghost', direction: 'front', faceExposure: null,
          colorId: baseColorId(product), angle: 'same', refScope: 'all', matchIds: [], refImages: [], refAssetIds: [],
        };
        if (own) next = next.map(block => block.id === own.id ? fallback : block);
        else {
          const end = next.findLastIndex(block => block.pageSectionKey === run.key) + 1;
          next = [...next.slice(0, end), fallback, ...next.slice(end)];
        }
        continue;
      }
      if (source || own) continue;
      const previousSource = reuseSourceForSlot(before, slot, product);
      const prototype = previousSource || before.find(block => block.source === 'ai'
        && block.pageSectionKind === slot.reuseFrom.kind && (!slot.reuseFrom.usage || block.pageSectionUsage === slot.reuseFrom.usage));
      const recipe = slot.reuseFrom.kind === 'color_product'
        ? { cutType: 'product', shot: 'ghost', productPresentation: 'ghost', direction: 'front', faceExposure: null }
        : { cutType: 'styling', shot: 'full', direction: 'front', faceExposure: 'same' };
      const fallback = singleMemberSpaceSet({ ...(prototype || recipe), id: makeId(), source: prototype?.source || 'ai', taxonomyVersion: 4,
        pageSectionKey: first.pageSectionKey, pageSectionKind: first.pageSectionKind, pageDesignId: first.pageDesignId,
        ...(first.pageSectionUsage ? { pageSectionUsage: first.pageSectionUsage } : {}),
        sectionId: first.sectionId, sectionLayout: 'stack', sectionCustom: true, slotKey: targetKey,
        contentRole: slot.contentRole, sectionRole: recipe.cutType === 'product' ? 'product' : 'styling',
        colorId: prototype?.colorId || baseColorId(product), refScope: 'all', matchIds: prototype?.matchIds || [],
      });
      for (const key of ['seedFingerprint', 'seedProductKey', 'similarSourceBlockId', 'pointItems', 'pageSectionUsage', 'sectionTitle']) delete fallback[key];
      const end = next.findLastIndex(block => block.pageSectionKey === run.key) + 1;
      next = [...next.slice(0, end), fallback, ...next.slice(end)];
    }
  }
  const preferred = preferredWornCropSource(next);
  const byId = new Map(next.map(block => [block.id, block]));
  const removedPointRuns = new Set();
  next = next.map(block => {
    if (!Array.isArray(block.pointItems)) return block;
    const derived = block.pointItems.some(item => item.kind === 'wornCrop');
    const independentDetails = block.pointItems.some(item => item.kind === 'detail'
      && byId.get(item.sourceBlockId)?.pageSectionKey === block.pageSectionKey
      && byId.get(item.sourceBlockId)?.shot === 'detail');
    if (derived && !preferred && !independentDetails) {
      removedPointRuns.add(block.pageSectionKey);
      return block;
    }
    // All three crops in a derived run use the same actual worn image. Keep an
    // existing valid source stable when sections are moved or more are added.
    const retainedSource = derived && block.pointItems.map(item => byId.get(item.sourceBlockId)).find(isWornCropSource);
    const cropSource = retainedSource || preferred;
    const sourceAspect = wornCropSourceAspect(cropSource, { product, cutResults });
    const previousItems = previousById.get(block.id)?.pointItems || [];
    const items = block.pointItems.flatMap(item => {
      if (item.kind !== 'wornCrop') {
        const source = byId.get(item.sourceBlockId);
        return source?.source === 'ai' && source.shot === 'detail' && source.pageSectionKey === block.pageSectionKey ? [item] : [];
      }
      if (!cropSource) return [];
      const previousItem = previousItems.find(previous => previous.kind === 'wornCrop' && previous.bodyPart === item.bodyPart);
      const sourceChanged = previousItem && (previousItem.sourceBlockId !== item.sourceBlockId
        || !sameWornCropSourceImage(previousById.get(item.sourceBlockId), cropSource));
      // Explicit saved/caller-supplied geometry is authoritative, including undo
      // snapshots. A source-only edit still gets a fresh source-aware rectangle.
      const explicitRegion = previousItem && JSON.stringify(previousItem.cropRegion) !== JSON.stringify(item.cropRegion);
      if (item.sourceBlockId === cropSource.id && (!sourceChanged || explicitRegion)) return [item];
      const currentRegion = cropRegions.find(([bodyPart]) => bodyPart === item.bodyPart)?.[2];
      return [{ ...item, sourceBlockId: cropSource.id, cropRegion: wornCropRegion(currentRegion || item.cropRegion, sourceAspect) }];
    });
    // Structural detail rows never fill a missing detail with a worn crop.
    const wanted = derived ? 3 : items.length;
    for (let i = items.length; cropSource && i < wanted && cropRegions[i]; i++) {
      const [bodyPart, label, cropRegion] = cropRegions[i];
      items.push({ kind: 'wornCrop', sourceBlockId: cropSource.id, bodyPart, label,
        cropRegion: wornCropRegion(cropRegion, sourceAspect) });
    }
    return JSON.stringify(items) === JSON.stringify(block.pointItems) ? block : { ...block, pointItems: items };
  });
  return removedPointRuns.size ? next.filter(block => !removedPointRuns.has(block.pageSectionKey)) : next;
}
