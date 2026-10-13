import { editorSlotKind } from '../../lib/editorSlotKind.js';
import { classifyEditorAssetInfo } from '../../lib/editorAssetInfo.js';
import { OUTER_CLOSURE_VALUES, outerClosureRange, outerClosureDisplayValue, outerClosureCaption } from '../../lib/outerClosure.js';
import { productSourcePhotos } from '../../lib/productSourcePhotos.js';
import { selectableDetailTargets, detailTargetPresentation } from '../../lib/detailRecommendations.js';
import { editorImageCreditCost } from '../../lib/limits.js';

export function canvasAiActionCreditCost(kind, action, creditCosts) {
  return editorImageCreditCost({ mode: ['worn', 'upload'].includes(kind) ? 'vary' : 'new',
    variationMode: action }, creditCosts);
}

const RECIPE_FIELDS = Object.freeze([
  'sectionRole', 'contentRole', 'cutType', 'shot', 'direction', 'sideStyle', 'colorId',
  'faceExposure', 'modelId', 'matchIds', 'matchIdsOrigin', 'refAssetIds', 'refScope',
  'exampleId', 'exampleSelectionOrigin', 'outerClosureState', 'detailTargetId',
  'detailTargetOrigin', 'spaceGroupId', 'spaceVariation', 'spaceSetMemberOrder', 'horizonBackgroundMode',
]);
const clone = (value) => Array.isArray(value) ? value.map(clone)
  : value && typeof value === 'object' ? Object.fromEntries(Object.entries(value).map(([key, item]) => [key, clone(item)])) : value;
const resolvedRecipe = (info) => info?.recipe || info?.metadata?.generation?.recipe || info?.generation?.recipe || null;
const resolvedCutType = (info) => resolvedRecipe(info)?.cutType || info?.cutType || info?.cut_type || null;
const currentValue = (info, field, fallback) => Object.hasOwn(info?.current || {}, field) ? info.current[field] : fallback;
const resolvedColor = (info) => currentValue(info, 'colorId', resolvedRecipe(info)?.colorId ?? info?.colorId ?? info?.color_id ?? null);
const resolvedDirection = (info) => currentValue(info, 'direction', resolvedRecipe(info)?.direction ?? null);

// Server detail targets belong to the base color and must match the requested face.
function availableDetails(info, product, recommendations) {
  const colorId = resolvedColor(info), direction = resolvedDirection(info);
  const colors = product?.colors || [];
  const base = colors.find((color) => color.isBase) || colors[0];
  const disabled = Boolean(product && colorId && String(colorId) !== String(base?.id));
  const candidates = disabled ? [] : selectableDetailTargets(recommendations)
    .filter((candidate) => !['front', 'back'].includes(direction) || candidate.direction === direction);
  return { colorId, direction, disabled, candidates };
}

/** Filled images use server asset facts. Only empty slots use storyboard linkage. */
export function canvasAiKind({ block, element, storyboard = [], assetRecipe = null } = {}) {
  const slotKind = editorSlotKind(block, element, storyboard);
  if (slotKind !== 'filled') return slotKind;
  return classifyEditorAssetInfo(assetRecipe);
}

export function canvasAiRo(label) {
  const text = String(label ?? '');
  const last = [...text.trim()].at(-1)?.charCodeAt(0);
  const final = last >= 0xac00 && last <= 0xd7a3 ? (last - 0xac00) % 28 : 0;
  return `${text}${final && final !== 8 ? '으로' : '로'}`;
}

const directBranch = (id, label, description, extra = {}) => ({
  id, action: id, label, direct: true, ring: id === 'fill', description, options: [], ...extra,
});
const optionBranch = (id, label, options) => ({ id, action: id, label, direct: false, ring: false, description: null, options });

export function buildCanvasAiMenu({ block, element, storyboard = [], assetRecipe = null, product = null,
  clothingType = 'top', frontClosure = null, detailRecommendations = null, failedCutRetry = null, colorOpts = [] } = {}) {
  const kind = canvasAiKind({ block, element, storyboard, assetRecipe });
  const recipe = resolvedRecipe(assetRecipe);
  const range = outerClosureRange(frontClosure);
  const closure = currentValue(assetRecipe, 'outerClosureState', recipe?.outerClosureState ?? null);
  const direction = resolvedDirection(assetRecipe);
  const closureKnown = OUTER_CLOSURE_VALUES.includes(closure);
  const current = {
    colorId: resolvedColor(assetRecipe),
    direction: ['front', 'back'].includes(direction) ? direction : null,
    outerClosureState: closureKnown && range !== 'hidden' ? outerClosureDisplayValue(closure, range) : null,
    detailTargetId: currentValue(assetRecipe, 'detailTargetId', recipe?.detailTargetId || null),
  };
  const source = element?.src ? { ...element, cutType: resolvedCutType(assetRecipe), wardrobeGroup: current.colorId || null } : null;
  const menu = { kind, branches: [], current, source };
  if (['pending', 'unsupported'].includes(kind)) return menu;
  if (['planned', 'unplanned'].includes(kind)) {
    menu.branches = [directBranch('upload', '내 사진 넣기', '내 컴퓨터에서 사진을 넣어요', { price: '무료' }),
      kind === 'planned' ? directBranch('retry', '원래 콘티대로 다시', '원래 콘티대로 다시 생성해요', { disabled: !failedCutRetry || failedCutRetry.retryable === false })
        : directBranch('fill', 'AI로 채우기', '빈 칸에 어울리는 컷을 생성해요')];
    return menu;
  }

  const productColors = product?.colors || [];
  const displayColors = colorOpts.length ? colorOpts.map((option) => {
    const id = option.id ?? option.value;
    const color = productColors.find((item) => item.id === id);
    return color ? { ...color, ...option, id } : null;
  }).filter(Boolean) : productColors;
  const colors = displayColors.filter((color) => productSourcePhotos(product, color.id).front).map((color) => {
    const photos = productSourcePhotos(product, color.id);
    const isCurrent = current.colorId !== null && color.id === current.colorId;
    const label = color.label || color.name || color.id;
    return { value: color.id, label, hex: color.hex || color.color || null, src: photos.front.src,
      current: isCurrent, disabled: isCurrent || (kind === 'ghost' && !photos[current.direction || 'front']), description: `${canvasAiRo(label)} 생성해요` };
  });
  const colorBranch = optionBranch('color', '색상', colors);
  const detail = availableDetails(assetRecipe, product, detailRecommendations);
  const details = detail.candidates.map((candidate) => {
    const isCurrent = current.detailTargetId !== null && candidate.id === current.detailTargetId;
    return { value: candidate.id, label: candidate.label, direction: candidate.direction,
      src: detailTargetPresentation({ cutType: 'product', shot: 'detail', direction: candidate.direction, detailTargetId: candidate.id }, product, detailRecommendations)?.src || null,
      current: isCurrent, disabled: isCurrent, description: `${candidate.label} 디테일을 생성해요` };
  });
  const detailBranch = (label) => ({ ...optionBranch('detail', label, details),
    ...(detail.disabled ? { disabled: true, description: '디테일은 기준색 사진에서만 생성해요' } : {}) });

  if (['worn', 'upload'].includes(kind)) {
    menu.branches = [directBranch('similar', '비슷한 컷', '지금 이미지와 비슷한 컷을 생성해요'), colorBranch];
    if (clothingType === 'outer' && range !== 'hidden') {
      const values = range === 'binary' ? ['closed', 'open'] : OUTER_CLOSURE_VALUES;
      menu.branches.push(optionBranch('closure', '열림 정도', values.map((value) => {
        const label = outerClosureCaption(value, range);
        const isCurrent = current.outerClosureState !== null && value === current.outerClosureState;
        return { value, label, current: isCurrent, disabled: isCurrent, description: `${canvasAiRo(label)} 생성해요` };
      })));
    }
  } else if (kind === 'ghost') {
    const photos = productSourcePhotos(product, current.colorId, 'ghost');
    menu.branches = [colorBranch, optionBranch('direction', '앞뒷면', ['front', 'back'].map((value) => {
      const label = value === 'back' ? '뒷면' : '앞면';
      const isCurrent = current.direction !== null && value === current.direction;
      return { value, label, src: photos[value]?.src || null, current: isCurrent, disabled: isCurrent || !photos[value], description: `${label}을 생성해요` };
    })), detailBranch('디테일')];
  } else if (kind === 'detail') {
    menu.branches = [directBranch('reshoot', '같은 부위 다시', '같은 부위를 다시 생성해요'), detailBranch('다른 부위')];
  } else if (kind === 'product_unknown') menu.branches = [detailBranch('디테일')];
  return menu;
}

function preservedRecipe(info) {
  const recipe = resolvedRecipe(info);
  if (!recipe) return {};
  return Object.fromEntries(RECIPE_FIELDS.filter((field) => recipe[field] !== undefined).map((field) => [field, clone(recipe[field])]));
}

function productRecipe(info) {
  const recipe = preservedRecipe(info);
  const colorId = resolvedColor(info), direction = resolvedDirection(info);
  return { ...recipe, ...(recipe.colorId === undefined && colorId !== null ? { colorId } : {}),
    ...(recipe.direction === undefined && ['front', 'back'].includes(direction) ? { direction } : {}) };
}

/** Builds the public request only; server-owned origin and recipe records stay on the server. */
export function buildCanvasAiRequest({ action, value, source = null, assetRecipe = null, product = null, slot = null, failedCutRetry = null, detailRecommendations = null } = {}) {
  if (action === 'upload') return null;
  if (action === 'fill') return slot?.blockId && slot?.elementId ? { mode: 'fill', slot: { blockId: slot.blockId, elementId: slot.elementId } } : null;
  if (action === 'retry') return failedCutRetry?.retryable !== false && failedCutRetry?.request ? clone(failedCutRetry.request) : null;
  if (!source?.src) return null;
  const kind = classifyEditorAssetInfo(assetRecipe);
  const cutType = resolvedCutType(assetRecipe);
  if (action === 'similar' && ['worn', 'upload'].includes(kind)) return { mode: 'vary', variationMode: 'similar', source: { src: source.src, cutType } };
  if (action === 'reshoot' && kind === 'detail') return { mode: 'vary', source: { src: source.src, cutType: 'product' } };
  if (action === 'color' && typeof value === 'string' && value) {
    if (['worn', 'upload'].includes(kind)) return { mode: 'vary', variationMode: 'color', source: { src: source.src }, colorId: value };
    if (kind === 'ghost') return { mode: 'new', ...productRecipe(assetRecipe), cutType: 'product', shot: 'ghost', colorId: value };
  }
  if (action === 'closure' && ['worn', 'upload'].includes(kind) && OUTER_CLOSURE_VALUES.includes(value)) return {
    mode: 'vary', variationMode: 'closure', source: { src: source.src }, outerClosureState: value,
  };
  if (action === 'direction' && kind === 'ghost' && ['front', 'back'].includes(value)) return {
    mode: 'new', ...productRecipe(assetRecipe), cutType: 'product', shot: 'ghost', direction: value,
  };
  if (action === 'detail' && ['ghost', 'detail', 'product_unknown'].includes(kind)) {
    const detail = availableDetails(assetRecipe, product, detailRecommendations);
    const candidate = detail.candidates.find((item) => item.id === value);
    if (!candidate) return null;
    const recipe = productRecipe(assetRecipe), colorId = detail.colorId;
    const direction = ['front', 'back'].includes(detail.direction) ? detail.direction : candidate.direction;
    return { mode: 'new', ...recipe, cutType: 'product', shot: 'detail', ...(colorId !== null ? { colorId } : {}),
      direction, detailTargetId: candidate.id, detailTargetOrigin: 'user' };
  }
  return null;
}
