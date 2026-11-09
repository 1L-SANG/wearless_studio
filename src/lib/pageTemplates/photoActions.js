import { uid } from '../ids.js';
import { detachSpaceMembership } from '../storyboardSpaceSets.js';
import { assignGenerationExamples } from '../generationExamples.js';
import { reconcileSimilarCutMutation } from '../storyboardSimilarCuts.js';
import { sectionRuns } from './sectionRuns.js';
import { reconcileSectionDependencies } from './sectionDependencies.js';
import { wornCropRegions } from './seed.js';
import { getTemplate, sectionDefinition } from './catalog.js';

export function canReplaceStoryboardSet(blocks, groupId, set) {
  const current = blocks.filter(block => block.spaceGroupId === groupId);
  if (!current.length) return false;
  const kept = new Set(current.filter(block => !block.similarSourceBlockId).slice(0, set.members.length).map(block => block.id));
  const followers = current.filter(block => kept.has(block.similarSourceBlockId)).length;
  const outside = blocks.filter(block => block.pageSectionKey === current[0].pageSectionKey && block.source !== 'text' && block.spaceGroupId !== groupId).length;
  return outside + set.members.length + followers <= 12;
}

export function patchStoryboardPhoto(blocks, id, changes, product = {}) {
  const next = blocks.map(block => block.id === id
    ? changes.source === 'mine' ? detachSpaceMembership({ ...block, ...changes }) : { ...block, ...changes } : block);
  return reconcileSectionDependencies(blocks, reconcileSimilarCutMutation(blocks, next), product,
    { cropRegions: wornCropRegions[product.clothingType] || wornCropRegions.top });
}

export function addStoryboardPhoto(blocks, key, { catalog, product = {}, analysis, gender, identityKind } = {}) {
  const run = sectionRuns(blocks).find(item => item.key === key);
  const photos = run?.blocks.filter(block => block.source !== 'text') || [];
  if (!run || photos.length >= 12 || run.kind !== 'wear' || run.usage !== 'styling') return blocks;
  let host = photos.findLast(block => block.source === 'ai' && !block.similarSourceBlockId);
  if (!host) {
    const template = getTemplate(key.split('.')[0]);
    const slot = sectionDefinition(template, key, run.kind, run.usage, run.designId).photoSlots?.find(slot => slot.fill === 'generate');
    if (!slot) return blocks;
    const recipe = slot.recipe;
    host = { ...run.blocks[0], source: 'ai', cutType: recipe.cutType, shot: recipe.shot, direction: recipe.direction,
      contentRole: slot.contentRole, faceExposure: recipe.faceExposure, matchIds: [],
      colorId: (product.colors?.find(color => color.isBase) || product.colors?.[0])?.id };
    delete host.ownImages;
  }
  const added = { ...detachSpaceMembership(host), id: uid('blk'), slotKey: null, sectionCustom: true,
    exampleId: null, exampleSelectionOrigin: null, baseThumb: null, previewThumb: null };
  for (const field of ['similarSourceBlockId', 'seedFingerprint', 'seedProductKey', 'exampleChoice', 'pointItems']) delete added[field];
  const next = [...blocks.slice(0, run.end), added, ...blocks.slice(run.end)];
  return assignGenerationExamples(next, { catalog, product, analysis, gender, identityKind, onlyBlockIds: [added.id] }).blocks;
}

export function removeStoryboardPhoto(blocks, id, product = {}) {
  const block = blocks.find(item => item.id === id);
  if (!block || block.source === 'text' || block.pageSectionKind === 'first' && block.slotKey && !block.similarSourceBlockId) return blocks;
  const run = sectionRuns(blocks).find(item => item.key === block.pageSectionKey);
  if (run.blocks.length <= 1) return blocks;
  const removed = reconcileSimilarCutMutation(blocks, blocks.filter(item => item.id !== id));
  return reconcileSectionDependencies(blocks, removed, product, { cropRegions: wornCropRegions[product.clothingType] || wornCropRegions.top });
}
