import { pageTemplatesCatalog } from '../../lib/pageTemplates/catalog.js';
import { measureTextWidth } from '../../lib/pageTemplates/assemble.js';
import { isTitlePlate, titlePlateBounds } from '../../lib/pageTemplates/titlePlate.js';

// Normal rectangles stay independently selectable/deletable. Refit only linked
// text edits; loading a page or resizing the rectangle itself does nothing.
export function fitTitlePlateBlocks(previous, next) {
  if (!previous || !next || previous === next) return next;
  let changed = false;
  const result = next.map(block => {
    const before = previous.find(item => item.id === block.id);
    if (!before || before === block) return block;
    let elements = block.elements;
    for (const plate of elements.filter(isTitlePlate)) {
      const oldPlate = before.elements.find(item => item.id === plate.id && isTitlePlate(item));
      if (!oldPlate) continue;
      const oldTexts = before.elements.filter(item => item.type === 'text' && item.groupId === plate.groupId).sort((a, b) => a.y - b.y);
      const texts = elements.filter(item => item.type === 'text' && item.groupId === plate.groupId);
      if (!texts.length || texts.length === oldTexts.length && texts.every(text => {
        const old = oldTexts.find(item => item.id === text.id);
        return old && old.h === text.h && old.w === text.w && old.text === text.text && old.style?.size === text.style?.size;
      })) continue;
      let delta = 0;
      const moved = new Map();
      for (const old of oldTexts) {
        const text = texts.find(item => item.id === old.id);
        if (!text) { delta -= old.h; continue; }
        moved.set(text.id, text.y === old.y && delta ? { ...text, y: text.y + delta } : text);
        delta += text.h - old.h;
      }
      const adjusted = texts.map(text => moved.get(text.id) || text);
      const catalog = pageTemplatesCatalog;
      const bounds = titlePlateBounds(adjusted, catalog.firstTitlePlate,
        text => measureTextWidth(text.text || text.placeholder, text.w, text.style.size, catalog.textMetrics, text.style.tracking),
        catalog.canvasWidth * (catalog.firstTitleBand.right - catalog.firstTitleBand.left), plate.x + plate.w / 2);
      elements = elements.map(item => item.id === plate.id ? { ...item, ...bounds } : moved.get(item.id) || item);
      changed = true;
    }
    return elements === block.elements ? block : { ...block, elements };
  });
  return changed ? result : next;
}
