import { isNeutralPageSwatch } from './pageTemplates/tokens.js';
import { outerClosureRange } from './outerClosure.js';
import { updateHorizonGroupBackground, horizonBackgroundMode } from './horizonBackground.js';
import { inheritSimilarCutRecipes } from './storyboardSimilarCuts.js';

export function setPhotoGroupId(block, blocks = []) {
  const source = block?.similarSourceBlockId ? blocks.find(item => item.id === block.similarSourceBlockId) : block;
  if (source?.source !== 'ai' || !source.spaceGroupId) return null;
  const originals = blocks.filter(item => item.source === 'ai' && item.spaceGroupId === source.spaceGroupId && !item.similarSourceBlockId);
  return originals.length >= 2 ? source.spaceGroupId : null;
}

export function setOptionState(members = [], product = {}, frontClosure, colorOpts = []) {
  const worn = members.filter(block => block.source === 'ai' && ['styling', 'horizon'].includes(block.cutType));
  const colors = product.colors || [], base = colors.find(color => color.isBase) || colors[0];
  const colorFor = block => colors.find(color => String(color.id) === String(block?.colorId ?? base?.id));
  const chromatic = worn.find(block => !isNeutralPageSwatch(colorFor(block)?.swatchId));
  const colorId = (chromatic || worn[0])?.colorId;
  return {
    outerVisible: product.clothingType === 'outer' && outerClosureRange(frontClosure) !== 'hidden',
    outerClosed: worn.length > 0 && worn.every(block => block.outerClosureState === 'closed'),
    chromatic: Boolean(chromatic), color: colorOpts.find(color => String(color.id) === String(colorId)),
    backgroundOn: worn.some(block => horizonBackgroundMode(block) === 'garment-tone'),
  };
}

export function updateSetOuterClosure(blocks, groupId, closed) {
  if (!groupId) return blocks;
  const state = closed ? 'closed' : 'open';
  const next = blocks.map(block => block.spaceGroupId === groupId && block.source === 'ai'
    && ['styling', 'horizon'].includes(block.cutType) ? { ...block, outerClosureState: state } : block);
  return inheritSimilarCutRecipes(next);
}

export function normalizeNeutralSetBackground(blocks, groupId, product) {
  const members = blocks.filter(block => block.spaceGroupId === groupId);
  return setOptionState(members, product).chromatic ? blocks : updateHorizonGroupBackground(blocks, groupId, 'reference');
}
