import { uid } from '../ids.js';
import { productPresentationExample } from '../productPresentationExamples.js';
import { inferStoryboardSpaceSet } from '../storyboardSpaceSetCatalog.js';
import { createSpaceSetMembers, nextUnusedSpaceSetMember, unusedSpaceSetMembers, nextUnusedSimilarSpaceSetMember } from '../storyboardSpaceSets.js';
import { horizonBackgroundMode } from '../horizonBackground.js';
import { normalizeNeutralSetBackground, setPhotoGroupId } from '../storyboardSetOptions.js';
import { reconcileHorizonGroupBackground } from '../horizonBackgroundAvailability.js';
import { withStageAdditions } from './storyboardStage.js';
import { sectionRuns, changeTemplateSectionDesign } from './sectionRuns.js';
import { baseDesignId, pairedDesignId, isPairedDesign } from './pairedDesigns.js';
import { firstGhostModePending, originalProductRun, reconcileSectionDependencies, reuseSlotsForBlock, reuseSourceForSlot } from './sectionDependencies.js';
import { productSectionState } from './productPresentation.js';
import { DETAIL_PHOTO_CAP, seedDetailTargets } from './detailBudget.js';
import { wearUsageForBlock } from './slots.js';
import { getTemplate, sectionDefinition } from './catalog.js';
import { directionSpecFromExample } from '../storyboardExampleSelection.js';
import { productSourcePhotos } from '../productSourcePhotos.js';
import { cutTypeDisplay } from '../storyboardTaxonomy.js';

const baseColor = product => (product?.colors?.find(color => color.isBase) || product?.colors?.[0])?.id;
const exampleSrc = (product, mode) => productPresentationExample(product.clothingType || 'top', mode, 'front')?.src;
const metaFor = run => ({ taxonomyVersion: 4, pageSectionKey: run.key, pageSectionKind: run.kind, pageDesignId: run.designId,
  sectionId: run.sectionId, sectionLayout: 'stack', sectionCustom: true });
const nextSlot = (run, slot) => {
  const prefix = `${run.key.split('.').slice(1).join('.')}.${slot}`;
  let index = 1;
  while (run.blocks.some(block => block.slotKey === `${prefix}${index}`)) index++;
  return `${prefix}${index}`;
};

export function similarSpaceSetMember(blocks, source) {
  const groupId = setPhotoGroupId(source, blocks), set = groupId && inferStoryboardSpaceSet(groupId);
  if (!set) return null;
  const member = nextUnusedSimilarSpaceSetMember(set, blocks.filter(block => block.spaceGroupId === groupId), source);
  return member ? { groupId, set, member } : null;
}

// Both entry points use the same member recipe; only the insertion position differs.
export function insertSpaceSetMember(blocks, key, item, context = {}, at = null) {
  const run = sectionRuns(blocks).find(run => run.key === key);
  if (!run || run.blocks.filter(block => block.source !== 'text').length >= 12) return blocks;
  const group = run.blocks.filter(block => block.spaceGroupId === item.groupId), host = group[0];
  if (!host || !unusedSpaceSetMembers(item.set, group).some(member => member.exampleId === item.member.exampleId)) return blocks;
  const [added] = createSpaceSetMembers(item.set, host, { spaceGroupId: item.groupId, members: [item.member],
    makeId: context.makeId || (() => uid('blk')), setSelectionOrigin: host.setSelectionOrigin || 'user' });
  const definition = sectionDefinition(getTemplate(key.split('.')[0]), key, run.kind, run.usage, run.designId);
  added.slotKey = nextSlot(run, definition.photoSlots.find(slot => slot.fill === 'generate')?.slotKey || 'wear');
  Object.assign(added, directionSpecFromExample(item.member));
  added.spaceVariation = host.spaceVariation || item.set.spaceVariation || 'subtle';
  added.horizonBackgroundMode = item.member.cutType === 'horizon' ? horizonBackgroundMode(host) : null;
  for (const field of ['seedProductKey', 'seedFingerprint', 'pointItems', 'exampleChoice', 'previewThumb']) delete added[field];
  const groupIds = new Set(group.map(block => block.id));
  const index = at ?? blocks.findLastIndex(block => block.pageSectionKey === key
    && (block.spaceGroupId === item.groupId || groupIds.has(block.similarSourceBlockId))) + 1;
  let next = [...blocks.slice(0, index), added, ...blocks.slice(index)];
  next = normalizeNeutralSetBackground(next, item.groupId, context.product);
  return reconcileHorizonGroupBackground(next, item.groupId, context.product, context.garmentColorEvidence, item.set.id);
}

// Ephemeral descriptions only. No tile, candidate preview or option flag is
// serialized into the storyboard; actions recheck these conditions at click time.
export function stageAdditions(blocks, key, { product = {}, analysis = {} } = {}) {
  const run = sectionRuns(blocks).find(run => run.key === key);
  if (!run) return [];
  if (run.kind === 'first') {
    if (isPairedDesign(run.designId) || !pairedDesignId(run.designId) || firstGhostModePending(blocks)) return [];
    return [{ id: 'firstGhost', kind: 'firstGhost', label: '고스트컷', src: exampleSrc(product, 'ghost') }];
  }
  if (run.kind === 'fabric') {
    if (isPairedDesign(run.designId) || !pairedDesignId(run.designId) || !run.blocks.some(b => b.source !== 'text')) return [];
    const slot = reuseSlotsForBlock({ ...run.blocks[0], pageDesignId: pairedDesignId(run.designId) }).find(slot => slot.slotKey === 'product');
    const source = slot && reuseSourceForSlot(blocks, slot, product);
    if (!source) return [];
    const sourceRun = originalProductRun(blocks), mode = sourceRun && productSectionState(blocks, sourceRun.key).value;
    // A legacy undecided/all-upload run still has a reusable front photo. Show
    // that actual source rather than inventing a presentation mode.
    return [{ id: 'fabricProduct', kind: 'fabricProduct', label: cutTypeDisplay('product').label, src: mode ? exampleSrc(product, mode)
      : source.source === 'mine' ? source.ownImages[0] : productSourcePhotos(product, source.colorId).front?.src }];
  }
  if (run.kind === 'points') {
    const target = seedDetailTargets(analysis).fabric;
    if (!target || blocks.some(b => b.pageSectionKind === 'fabric' || b.detailTargetId === target.id)
      || blocks.filter(b => b.source === 'ai' && b.shot === 'detail').length >= DETAIL_PHOTO_CAP) return [];
    return [{ id: 'pointsFabric', kind: 'pointsFabric', label: '원단 확대', target,
      block: { ...metaFor(run), id: 'preview-fabric', source: 'ai', contentRole: 'detail', sectionRole: 'product', cutType: 'product', shot: 'detail',
        direction: target.direction, colorId: baseColor(product), detailTargetId: target.id, detailTargetOrigin: 'user', faceExposure: null, matchIds: [], refScope: 'all' } }];
  }
  const photos = run.blocks.filter(b => b.source !== 'text');
  if (run.kind !== 'wear' || photos.length >= 12) return [];
  const candidates = [...new Set(photos.map(b => b.spaceGroupId).filter(Boolean))].flatMap(groupId => {
    const group = photos.filter(b => b.spaceGroupId === groupId), set = inferStoryboardSpaceSet(groupId);
    if (!['styling', 'horizon-sequence', 'horizon-rotation'].includes(set?.setType)) return [];
    const member = nextUnusedSpaceSetMember(set, group);
    return member ? [{ id: `spaceMember:${groupId}`, kind: 'spaceMember', label: set.setType === 'styling' ? '같은 장소 1컷 더' : '1컷 더', groupId,
      set, member, src: member.thumb || member.thumbUrl || member.allUrl }] : [];
  });
  return withStageAdditions(photos, candidates, { add: wearUsageForBlock(photos[0] || run.blocks[0]) === 'styling' })
    .filter(block => block.paleAddition).map(block => block.paleAddition);
}

export function addStageAddition(blocks, key, requested, context = {}) {
  const item = stageAdditions(blocks, key, context).find(item => item.id === requested?.id);
  if (!item) return blocks;
  const run = sectionRuns(blocks).find(run => run.key === key), makeId = context.makeId || (() => uid('blk'));
  let next;
  if (['firstGhost', 'fabricProduct'].includes(item.kind)) {
    next = changeTemplateSectionDesign(blocks, key, pairedDesignId(run.designId));
  } else if (item.kind === 'spaceMember') {
    next = insertSpaceSetMember(blocks, key, item, context);
  } else {
    const added = { ...item.block, id: makeId(), slotKey: nextSlot(run, 'detail') };
    const point = { kind: 'detail', sourceBlockId: added.id, detailTargetId: added.detailTargetId };
    next = blocks.map(block => block.pageSectionKey === key && block.source === 'text'
      ? { ...block, pointItems: [...(block.pointItems || run.blocks.filter(b => b.source === 'ai' && b.shot === 'detail')
        .map(b => ({ kind: 'detail', sourceBlockId: b.id, detailTargetId: b.detailTargetId }))), point] } : block);
    next = [...next.slice(0, run.end), added, ...next.slice(run.end)];
  }
  return reconcileSectionDependencies(blocks, next, context.product, { makeId });
}

export function removePairedPhoto(blocks, key, product = {}) {
  const run = sectionRuns(blocks).find(run => run.key === key);
  if (!run || !isPairedDesign(run.designId)) return blocks;
  return reconcileSectionDependencies(blocks, changeTemplateSectionDesign(blocks, key, baseDesignId(run.designId)), product);
}
