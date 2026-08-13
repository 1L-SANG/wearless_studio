import { DEFAULT_BUBBLE_RADIUS, DEFAULT_BUBBLE_STROKE, DEFAULT_BUBBLE_STROKE_WIDTH } from './editorLibrary.js';

const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

export function isSpeechBubbleElement(element) {
  return Boolean(element?.type === 'text' && element?.shape === 'bubble');
}

const containsTextCenter = (bubble, text) => {
  const centerX = Number(text.x || 0) + Number(text.w || 0) / 2;
  const centerY = Number(text.y || 0) + Number(text.h || 0) / 2;
  return centerX >= Number(bubble.x || 0)
    && centerX <= Number(bubble.x || 0) + Number(bubble.w || 0)
    && centerY >= Number(bubble.y || 0)
    && centerY <= Number(bubble.y || 0) + Number(bubble.h || 0);
};

const area = (element) => Number(element?.w || 0) * Number(element?.h || 0);

function legacyCopyForBubble(elements, bubble, usedCopyIds) {
  const copies = (elements || []).filter((element) => (
    element.type === 'text' && element.shape !== 'bubble' && !usedCopyIds.has(element.id)
  ));
  if (bubble.bubblePairId) {
    const paired = copies.find((copy) => copy.bubblePairId === bubble.bubblePairId);
    if (paired) return paired;
  }

  const grouped = bubble.groupId
    ? copies.filter((copy) => copy.groupId === bubble.groupId)
    : [];
  const groupedInside = grouped.filter((copy) => containsTextCenter(bubble, copy));
  if (groupedInside.length) return groupedInside.sort((a, b) => area(a) - area(b))[0];
  if (grouped.length === 1) return grouped[0];

  const inside = copies.filter((copy) => containsTextCenter(bubble, copy));
  return inside.sort((a, b) => area(a) - area(b))[0] || null;
}

function legacyBubbleFit(bubble, copy) {
  const configured = copy.bubbleFit || {};
  const padX = Number(configured.padX ?? Math.max(16, Number(copy.x || 0) - Number(bubble.x || 0)));
  const padTop = Number(configured.padTop ?? Math.max(12, Number(copy.y || 0) - Number(bubble.y || 0)));
  const currentBottom = Number(bubble.y || 0) + Number(bubble.h || 0)
    - Number(copy.y || 0) - Number(copy.h || 0);
  return {
    minWidth: 0,
    maxWidth: Number(configured.maxWidth ?? Math.max(Number(copy.w || 660), 660)),
    padX,
    padTop,
    padBottom: Number(configured.padBottom ?? Math.max(28, currentBottom)),
    anchor: configured.anchor || (bubble.flipX ? 'right' : 'left'),
  };
}

function mergeLegacyPair(bubble, copy) {
  const {
    id: _copyId,
    type: _copyType,
    shape: _copyShape,
    x: _copyX,
    y: _copyY,
    w: _copyW,
    h: _copyH,
    rotate: _copyRotate,
    opacity: copyOpacity,
    bubblePairId: _copyPairId,
    bubbleFit: _copyFit,
    text,
    style,
    groupId: copyGroupId,
    libraryItemId: copyLibraryItemId,
    ...copyMetadata
  } = copy;
  const { bubblePairId: _bubblePairId, opacity: bubbleOpacity, ...bubbleLayer } = bubble;
  const mergedStyle = { ...(style || {}) };
  if (copyOpacity != null && copyOpacity !== 1 && mergedStyle.opacity == null) {
    mergedStyle.opacity = copyOpacity;
  }
  const merged = {
    ...bubbleLayer,
    ...copyMetadata,
    type: 'text',
    shape: 'bubble',
    id: bubble.id,
    x: bubble.x,
    y: bubble.y,
    w: bubble.w,
    h: bubble.h,
    text: text || '',
    style: mergedStyle,
    bubbleFit: legacyBubbleFit(bubble, copy),
  };
  if (!merged.stroke) merged.stroke = DEFAULT_BUBBLE_STROKE;
  if (merged.stroke !== 'none' && merged.strokeWidth == null) merged.strokeWidth = DEFAULT_BUBBLE_STROKE_WIDTH;
  if (merged.radius == null) merged.radius = DEFAULT_BUBBLE_RADIUS;
  if (bubbleOpacity != null && bubbleOpacity !== 1) merged.fillOpacity = bubbleOpacity;
  const groupId = copyGroupId || bubble.groupId;
  const libraryItemId = copyLibraryItemId || bubble.libraryItemId;
  if (groupId) merged.groupId = groupId;
  if (libraryItemId) merged.libraryItemId = libraryItemId;
  return merged;
}

/**
 * Converts documents saved by the former two-layer speech-bubble model into
 * the current one-element model. Unrelated elements keep their object identity
 * and an already normalized array is returned as-is.
 */
export function mergeSpeechBubbleElements(elements) {
  const all = elements || [];
  const usedCopyIds = new Set();
  const mergedByBubbleId = new Map();

  all.filter((element) => element.type === 'shape' && element.shape === 'bubble').forEach((bubble) => {
    const copy = legacyCopyForBubble(all, bubble, usedCopyIds);
    if (!copy) return;
    usedCopyIds.add(copy.id);
    mergedByBubbleId.set(bubble.id, mergeLegacyPair(bubble, copy));
  });

  if (!mergedByBubbleId.size) return all;
  return all.flatMap((element) => {
    if (usedCopyIds.has(element.id)) return [];
    return mergedByBubbleId.get(element.id) || element;
  });
}

export function patchSelectedBubbleAppearance(blocks, selectedIds, patch) {
  const selected = new Set(selectedIds || []);
  if (!selected.size) return blocks;
  return (blocks || []).map((block) => ({
    ...block,
    elements: (block.elements || []).map((element) => (
      selected.has(element.id) && isSpeechBubbleElement(element)
        ? { ...element, ...patch }
        : element
    )),
  }));
}

export function speechBubbleFitOptions(element) {
  const configured = element?.bubbleFit || {};
  return {
    // 예전 프리셋의 minWidth 는 입력이 일정 길이를 넘을 때까지 폭을 고정했다.
    // 말풍선은 첫 글자부터 내용 폭을 따라가며, 사용자가 정한 maxWidth 에서만 줄바꿈한다.
    minWidth: 0,
    maxWidth: Number(configured.maxWidth ?? Math.max(Number(element?.w || 660), 660)),
    padX: Number(configured.padX ?? 24),
    padTop: Number(configured.padTop ?? 20),
    padBottom: Number(configured.padBottom ?? 40),
    anchor: configured.anchor || (element?.flipX ? 'right' : 'left'),
  };
}

export function bubbleTextWidth(element, naturalWidth) {
  const fit = speechBubbleFitOptions(element);
  const maxWidth = Math.max(fit.minWidth, fit.maxWidth);
  // Canvas font metrics can trim the final antialiasing pixel, so retain a
  // tiny safety gutter while still honoring the configured maximum.
  return Math.round(clamp(Math.ceil(Number(naturalWidth || 0)) + 4, fit.minWidth, maxWidth));
}

export function fitBubbleToText(element, metrics = {}) {
  if (!isSpeechBubbleElement(element)) return null;
  const options = speechBubbleFitOptions(element);
  const textWidth = bubbleTextWidth(element, metrics.naturalWidth);
  const textHeight = Math.max(1, Math.ceil(Number(metrics.renderedHeight || 1)));
  const width = Math.round(textWidth + options.padX * 2);
  const height = Math.round(textHeight + options.padTop + options.padBottom);
  const oldRight = Number(element.x || 0) + Number(element.w || 0);
  const x = options.anchor === 'right' ? oldRight - width : Number(element.x || 0);

  return {
    elementPatch: {
      x: Math.round(x),
      y: Math.round(Number(element.y || 0)),
      w: width,
      h: height,
    },
    textWidth,
    textHeight,
  };
}
