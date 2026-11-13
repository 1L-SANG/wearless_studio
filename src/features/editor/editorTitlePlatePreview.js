import { useLayoutEffect, useRef } from 'react';
import { fitTitlePlateBlocks } from './editorTitlePlateFit.js';
import { hasTitlePlate } from '../../lib/pageTemplates/titlePlate.js';

function paintGroup(node, elements, groupId) {
  const root = node?.parentElement;
  if (!root) return;
  const nodes = new Map([...root.querySelectorAll('[data-elid]')].map(item => [item.dataset.elid, item]));
  for (const element of elements || []) {
    const target = nodes.get(element.id);
    if (!target || element.groupId !== groupId) continue;
    target.style.left = `${element.x}px`; target.style.top = `${element.y}px`;
    target.style.width = `${element.w}px`;
    target.style.height = element.type === 'text' ? 'auto' : `${element.h}px`;
  }
}

// Like bubble fitting: preview geometry in the live DOM while typing, commit
// the measured height on blur, and restore model geometry if editing is canceled.
export function useTitlePlatePreview(element, elements, ref, editing) {
  const previewed = useRef(false);
  useLayoutEffect(() => {
    if (!editing && previewed.current) {
      paintGroup(ref.current, elements, element.groupId);
      previewed.current = false;
    }
  }, [editing, elements, element.groupId]);
  return (node, text) => {
    if (!editing || !hasTitlePlate(element, elements)) return null;
    const h = Math.max(1, Math.ceil(node.offsetHeight));
    const before = { id: 'live', elements };
    const next = { ...before, elements: elements.map(item => item.id === element.id ? { ...item, text, h } : item) };
    const fitted = fitTitlePlateBlocks([before], [next])[0];
    paintGroup(node, fitted.elements, element.groupId);
    previewed.current = true;
    return { h };
  };
}
