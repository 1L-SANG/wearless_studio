// Existing editor groupId links the ordinary rectangle and its text without
// object-library group selection. No storyboard metadata is introduced.
export const TITLE_PLATE_PREFIX = 'title-plate:';
export const titlePlateGroup = key => `${TITLE_PLATE_PREFIX}${key}`;
export const isTitlePlate = element => element?.type === 'shape' && element.shape === 'rect'
  && element.groupId?.startsWith(TITLE_PLATE_PREFIX);
export const hasTitlePlate = (element, elements) => Boolean(element?.groupId
  && elements?.some(item => !item.hidden && isTitlePlate(item) && item.groupId === element.groupId));

export function titlePlateBounds(texts, style, measure, maxWidth, center) {
  const w = Math.min(maxWidth, Math.max(...texts.map(measure), 1) + style.paddingX * 2);
  const top = Math.min(...texts.map(text => text.y)) - style.paddingTop;
  const bottom = Math.max(...texts.map(text => text.y + text.h)) + style.paddingBottom;
  return { x: center - w / 2, y: top, w, h: bottom - top };
}
