import { fitIntroAvatar, fitIntroBlockHeight } from './editorIntroAvatar.js';
import { pageTemplatesCatalog } from '../../lib/pageTemplates/catalog.js';
import { measureTextWidth, estimateTextHeight } from '../../lib/pageTemplates/assemble.js';
import { isTitlePlate, titlePlateBounds } from '../../lib/pageTemplates/titlePlate.js';

// T01-D07 already links each ordinary rectangle to its paragraph with groupId.
// Reuse that link; library speech bubbles and unrelated rectangles stay separate.
export function introPanelFor(element, elements) {
  if (!element?.groupId?.startsWith('template-copy:')) return null;
  const group = (elements || []).filter(item => item.groupId === element.groupId && !item.hidden);
  if (!group.some(item => item.type === 'text' && item.copyRole === 'body' && item.paragraphIndex != null)) return null;
  return group.find(item => item.type === 'shape' && item.shape === 'rect') || null;
}

function fitIntroPanels(previous, next) {
  let elements = next;
  const hasBody = elements.some(item => item.copyRole === 'body' && String(item.text || '').trim());
  const columns = pageTemplatesCatalog.designs['T01-D07'].layout.find(node => node.columns).columns;
  const spec = columns.flatMap(col => col.nodes).find(node => node.splitParagraphs === 'body');
  const titleGap = spec.nodes.find(node => node.copyRole === 'title').gap;
  for (const oldPanel of previous.filter(item => item.type === 'shape' && introPanelFor(item, previous) === item)) {
    const panel = elements.find(item => item.id === oldPanel.id);
    if (!panel || panel.hidden) continue;
    const oldTexts = previous.filter(item => item.type === 'text' && item.groupId === panel.groupId);
    const texts = elements.filter(item => item.type === 'text' && item.groupId === panel.groupId)
      .sort((a, b) => Number(b.copyRole === 'title') - Number(a.copyRole === 'title') || a.y - b.y);
    const shouldHide = text => {
      const old = oldTexts.find(item => item.id === text.id);
      if (old && old.text === text.text && Boolean(old.hidden) !== Boolean(text.hidden)) return Boolean(text.hidden);
      return text.copyRole === 'title' && !String(text.text || '').trim() && hasBody;
    };
    if (!texts.length || texts.length === oldTexts.length && texts.every(text => {
      const old = oldTexts.find(item => item.id === text.id);
      return old && old.h === text.h && old.w === text.w && old.text === text.text && old.style?.size === text.style?.size
        && Boolean(old.hidden) === Boolean(text.hidden)
        && (text.copyRole !== 'title' || Boolean(text.hidden) === shouldHide(text));
    })) continue;
    const oldVisible = oldTexts.filter(text => !text.hidden);
    const topPadding = oldVisible.length ? Math.max(0, Math.min(...oldVisible.map(text => text.y)) - oldPanel.y) : spec.padding[0];
    const bottomPadding = oldVisible.length ? Math.max(0, oldPanel.y + oldPanel.h - Math.max(...oldVisible.map(text => text.y + text.h))) : spec.padding[0];
    const moved = new Map();
    let cursor = panel.y + topPadding;
    for (const text of texts) {
      const empty = !String(text.text || '').trim();
      const old = oldTexts.find(item => item.id === text.id);
      const hidden = text.copyRole === 'title' && (empty || !String(old?.text || '').trim()) ? shouldHide(text) : Boolean(text.hidden);
      const h = empty && !hidden ? estimateTextHeight(text.placeholder || '내용을 입력해 주세요', text.w,
        text.style.size, text.style.lineHeight, pageTemplatesCatalog.textMetrics, text.style.tracking) : text.h;
      const { hidden: _hidden, ...visible } = text;
      moved.set(text.id, { ...visible, y: cursor, h, ...(hidden ? { hidden: true } : {}) });
      if (!hidden) cursor += h + (text.copyRole === 'title' ? titleGap : 0);
    }
    const h = Math.max(1, cursor - panel.y + bottomPadding);
    const growth = h - panel.h;
    elements = elements.map(item => item.id === panel.id ? { ...item, h }
      : moved.get(item.id) || (growth && item.y >= panel.y + panel.h && introPanelFor(item, elements)
        ? { ...item, y: item.y + growth } : item));
  }
  return elements;
}

function fitReviewAuthors(previous, next) {
  let elements = next;
  for (const old of previous.filter(el => /^items\.\d+\.who$/.test(el.copyRole))) {
    const author = elements.find(el => el.id === old.id);
    if (!author || author.text === old.text && author.h === old.h && Boolean(author.hidden) === Boolean(old.hidden)) continue;
    const body = elements.find(el => el.copyRole === author.copyRole.replace(/\.who$/, '.text'));
    if (!body || !author.groupId?.startsWith('template-copy:')) continue;
    const visibilityEdit = author.text === old.text && Boolean(author.hidden) !== Boolean(old.hidden);
    const hidden = visibilityEdit ? Boolean(author.hidden) : !String(author.text || '').trim();
    const spec = pageTemplatesCatalog.designs['I09-D06'].layout.find(node => node.type === 'overlay')
      .groups.flatMap(group => group.nodes).find(node => node.type === 'list');
    const gap = spec.items.find(node => node.copyRole === 'items.n.text').gap;
    const delta = (hidden ? 0 : author.h + gap) - (old.hidden ? 0 : old.h + gap);
    const oldGroup = previous.filter(el => el.groupId === old.groupId && !el.hidden);
    const bottom = Math.max(...oldGroup.map(el => el.y + el.h));
    elements = elements.map(el => {
      if (el.id === author.id) {
        const { hidden: _hidden, ...visible } = el;
        return { ...visible, y: body.y + body.h + (hidden ? 0 : gap), ...(hidden ? { hidden: true } : {}) };
      }
      if (el.groupId === author.groupId && el.type === 'shape') return { ...el, h: Math.max(1, el.h + delta) };
      if (el.groupId !== author.groupId && el.groupId?.startsWith('template-copy:') && el.y >= bottom) return { ...el, y: el.y + delta };
      return el;
    });
  }
  return elements;
}

// Normal rectangles stay independently selectable/deletable. Refit only linked
// text edits; loading a page or resizing the rectangle itself does nothing.
export function fitTitlePlateBlocks(previous, next) {
  if (!previous || !next || previous === next) return next;
  let changed = false;
  const result = next.map(block => {
    const before = previous.find(item => item.id === block.id);
    if (!before || before === block) return block;
    let elements = fitIntroAvatar(before.elements, block.elements);
    if (elements !== block.elements) changed = true;
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
    elements = fitReviewAuthors(before.elements, elements);
    const fitted = fitIntroPanels(before.elements, elements);
    if (fitted !== elements) { elements = fitted; changed = true; }
    const resized = fitIntroBlockHeight(before, elements === block.elements ? block : { ...block, elements });
    if (resized !== block) changed = true;
    return resized;
  });
  return changed ? result : next;
}
