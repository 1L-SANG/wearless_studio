import { getBlockRenderHeight } from './editorGeometry.js';
import { isIntroAvatarElement } from './editorIntroAvatar.js';
import { useLayoutEffect, useRef } from 'react';
import { fitTitlePlateBlocks, introPanelFor } from './editorTitlePlateFit.js';
import { hasTitlePlate } from '../../lib/pageTemplates/titlePlate.js';

function paintGroup(node, elements, groupId) {
  const root = node?.parentElement;
  if (!root) return;
  const nodes = new Map([...root.querySelectorAll('[data-elid]')].map(item => [item.dataset.elid, item]));
  for (const element of elements || []) {
    const target = nodes.get(element.id);
    if (!target || (groupId ? element.groupId !== groupId : !introPanelFor(element, elements) && !isIntroAvatarElement(element))) continue;
    target.style.display = element.hidden ? 'none' : '';
    target.style.left = `${element.x}px`; target.style.top = `${element.y}px`;
    target.style.width = `${element.w}px`;
    target.style.height = element.type === 'text' ? 'auto' : `${element.h}px`;
  }
}

// Like bubble fitting: preview geometry in the live DOM while typing, commit
// the measured height on blur, and restore model geometry if editing is canceled.
export function useTitlePlatePreview(element, elements, ref, editing, block) {
  const previewed = useRef(false);
  const blockPreview = useRef(null);
  useLayoutEffect(() => {
    if (!editing && previewed.current) {
      paintGroup(ref.current, elements, introPanelFor(element, elements) ? null : element.groupId);
      if (blockPreview.current) {
        const { node, height, exportHeight } = blockPreview.current;
        node.style.height = block ? `${getBlockRenderHeight(block)}px` : height;
        node.dataset.exportHeight = block ? String(getBlockRenderHeight(block, { forExport: true })) : exportHeight;
        blockPreview.current = null;
      }
      previewed.current = false;
    }
  }, [editing, elements, element.groupId, block]);
  return (node, text) => {
    const intro = introPanelFor(element, elements);
    if (!editing || !(intro || hasTitlePlate(element, elements))) return null;
    let h = Math.max(1, Math.ceil(node.offsetHeight));
    const before = block || { id: 'live', elements };
    const next = { ...before, elements: elements.map(item => item.id === element.id ? { ...item, text, h } : item) };
    let fitted = fitTitlePlateBlocks([before], [next])[0];
    paintGroup(node, fitted.elements, intro ? null : element.groupId);
    if (intro) {
      // Restoring the avatar narrows the body. Measure that width on this input,
      // before committing/exporting, rather than waiting for the next keystroke.
      h = Math.max(1, Math.ceil(node.offsetHeight));
      const measured = { ...fitted, elements: fitted.elements.map(item => item.id === element.id ? { ...item, h } : item) };
      fitted = fitTitlePlateBlocks([fitted], [measured])[0];
      paintGroup(node, fitted.elements, null);
      const canvas = node.closest?.('.canvas-block');
      if (canvas && block) {
        blockPreview.current ||= { node: canvas, height: canvas.style.height, exportHeight: canvas.dataset.exportHeight };
        canvas.style.height = `${getBlockRenderHeight(fitted)}px`;
        canvas.dataset.exportHeight = String(getBlockRenderHeight(fitted, { forExport: true }));
        if (node.parentElement) node.parentElement.scrollTop = 0;
      }
    }
    previewed.current = true;
    return { h };
  };
}
