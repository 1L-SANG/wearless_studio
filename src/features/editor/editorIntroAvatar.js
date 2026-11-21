import { getBlockContentBottom, introSectionPadding } from './editorGeometry.js';
import { pageTemplatesCatalog } from '../../lib/pageTemplates/catalog.js';
import { estimateTextHeight } from '../../lib/pageTemplates/assemble.js';

export const isIntroAvatarElement = element => element?.groupId?.startsWith('template-intro-avatar:');

// The catalog's hidden avatar elements retain their original geometry and identity.
// No editor-only geometry fields are needed in saved pages.
export function fitIntroAvatar(previous, next) {
  const bodies = next.filter(el => el.copyRole === 'body' && el.paragraphIndex != null);
  const who = next.find(el => el.copyRole === 'who' && isIntroAvatarElement(el));
  if (!who || !bodies.length) return next;
  const oldBodies = previous.filter(el => el.copyRole === 'body' && el.paragraphIndex != null);
  if (bodies.length === oldBodies.length && bodies.every(el => oldBodies.some(old => old.id === el.id && old.text === el.text))) return next;
  const hidden = !bodies.some(el => !el.hidden && String(el.text || '').trim());
  const wasHidden = !oldBodies.some(el => !el.hidden && String(el.text || '').trim());
  if (wasHidden === hidden) return next;
  const oldWho = previous.find(el => el.id === who.id || el.copyRole === 'who' && isIntroAvatarElement(el));
  // A completed server layout already moved its avatar and panels. A direct
  // text edit leaves visibility unchanged and still needs the local adjustment.
  if (oldWho && Boolean(oldWho.hidden) !== Boolean(who.hidden) && Boolean(who.hidden) === hidden) return next;
  const design = pageTemplatesCatalog.designs['T01-D07'];
  const columns = design.layout.find(node => node.columns).columns;
  const column = columns.find(col => col.nodes.some(node => node.copyRole === 'who'));
  const name = column.nodes.find(node => node.copyRole === 'who');
  const dx = (hidden ? -1 : 1) * column.x;
  const dy = (hidden ? -1 : 1) * (who.h + name.gap);
  const groups = new Set(bodies.map(el => el.groupId));
  const fullWidth = pageTemplatesCatalog.canvasWidth - 2 * design.padding;
  const widthChanges = new Map(next.filter(el => el.type === 'shape' && groups.has(el.groupId))
    .map(el => [el.groupId, (hidden ? fullWidth : column.w) - el.w]));
  return next.map(el => {
    if (isIntroAvatarElement(el)) {
      const { hidden: _hidden, ...visible } = el;
      return hidden ? { ...visible, hidden: true } : visible;
    }
    if (!groups.has(el.groupId)) return el;
    const moved = { ...el, x: el.x + dx, y: el.y + dy, w: el.w + (widthChanges.get(el.groupId) || 0) };
    if (el.type === 'text') moved.h = Math.max(el.h,
      estimateTextHeight(el.text || el.placeholder, moved.w, el.style.size,
        el.style.lineHeight, pageTemplatesCatalog.textMetrics, el.style.tracking));
    return moved;
  });
}

// Track the seller's height floor only in memory, keyed by immutable elements
// and height so undo snapshots keep their own floor. No saved block fields.
const heightFloors = new WeakMap();
export function fitIntroBlockHeight(previous, next) {
  if (next.pageDesignId !== 'T01-D07' || !Number.isFinite(previous.h)) return next;
  const padding = introSectionPadding();
  const oldNatural = getBlockContentBottom(previous) + padding;
  const prior = heightFloors.get(previous.elements)?.get(previous.h);
  const floor = next.h !== previous.h ? next.h
    : prior ?? (Math.abs(previous.h - oldNatural) <= 1 ? 0 : previous.h);
  const oldTexts = previous.elements.filter(el => el.type === 'text');
  const texts = next.elements.filter(el => el.type === 'text');
  const contentChanged = oldTexts.length !== texts.length || texts.some(el => {
    const old = oldTexts.find(item => item.id === el.id);
    return !old || old.text !== el.text || old.h !== el.h || old.w !== el.w || old.style?.size !== el.style?.size || Boolean(old.hidden) !== Boolean(el.hidden);
  });
  // Keep an explicit seller height in h. Rendering may grow beyond that floor;
  // saving the automatic growth over h would lose the floor after a reload.
  const h = contentChanged ? floor || getBlockContentBottom(next) + padding : next.h;
  const heights = heightFloors.get(next.elements) || new Map();
  heights.set(h, floor); heightFloors.set(next.elements, heights);
  return h === next.h ? next : { ...next, h };
}
