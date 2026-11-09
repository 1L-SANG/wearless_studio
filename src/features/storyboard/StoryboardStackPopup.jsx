import React, { useEffect, useRef, useState } from 'react';
import { Icon } from '@/components/ui.jsx';
import { generationExampleImageSources } from '../../lib/generationExamples.js';
import { stackPopupColumns, stackPopupPlacement } from '../../lib/pageTemplates/storyboardGallery.js';
import { spaceSetDisplayName } from '../../lib/spaceSetDisplayNames.js';
import { ExampleThumb } from './StoryboardControls.jsx';
import styles from './StoryboardPanels.module.css';

export function StoryboardStackPopup({ entry, selectedId, anchor, galleryElement, keyboardOpen, row, column,
  usedExampleIds, onClose, onChoose, onDrag, onDragEnd }) {
  const ref = useRef(null), callbacks = useRef(null), keyboard = useRef(keyboardOpen), finishDrag = useRef(null);
  callbacks.current = { onClose, onChoose, onDrag, onDragEnd };
  const [columns, setColumns] = useState(() => stackPopupColumns(entry.members.length, galleryElement?.getBoundingClientRect().width));
  useEffect(() => {
    const popup = ref.current;
    if (!popup || !anchor || !galleryElement) return undefined;
    const doc = popup.ownerDocument, view = doc.defaultView;
    const body = anchor.closest('[data-storyboard-scroll]') || galleryElement;
    let current = Math.max(0, entry.members.findIndex(example => example.id === selectedId));
    const tiles = () => [...popup.querySelectorAll('[data-example-id]')];
    const focus = index => {
      const elements = tiles();
      current = Math.max(0, Math.min(elements.length - 1, index));
      elements.forEach((element, at) => { element.tabIndex = at === current ? 0 : -1; });
      elements[current]?.focus({ preventScroll: true });
      elements[current]?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' });
    };
    const place = (mayScroll = false) => {
      const gallery = galleryElement.getBoundingClientRect();
      setColumns(stackPopupColumns(entry.members.length, gallery.width));
      const placement = stackPopupPlacement({ anchor: anchor.getBoundingClientRect(), gallery,
        viewport: body.getBoundingClientRect(), width: popup.offsetWidth, height: popup.offsetHeight, scrollTop: body.scrollTop });
      popup.dataset.side = placement.side;
      popup.dataset.ready = 'true';
      if (mayScroll && placement.scrollBy) body.scrollTop += placement.scrollBy;
    };
    // CSS anchors keep the top-layer popup attached during scrolling. Older engines
    // use the same gallery's static row/column layout, outside the clipped page strip.
    let native = false;
    if (typeof popup.showPopover === 'function' && view?.CSS?.supports?.('anchor-name: --sb-active-photo-stack')
      && view.CSS.supports('left: anchor(left)') && view.CSS.supports('width: anchor-size(width)')) {
      try {
        popup.setAttribute('popover', 'manual');
        popup.dataset.floating = 'native';
        popup.showPopover();
        native = true;
      } catch { popup.removeAttribute('popover'); }
    }
    if (!native) popup.dataset.floating = 'fallback';
    place(true);
    focus(current);
    const stop = event => { event.preventDefault(); event.stopPropagation(); event.stopImmediatePropagation?.(); };
    const keydown = event => {
      keyboard.current = true;
      if (event.key === 'Escape' || event.key === 'Tab') { stop(event); callbacks.current.onClose(true); return; }
      if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
      stop(event);
      const elements = tiles(), at = elements.indexOf(doc.activeElement);
      if (at < 0) { focus(current); return; }
      const last = elements.length - 1, currentRow = Math.floor(at / columns), lastRow = Math.floor(last / columns);
      focus(event.key === 'Home' ? 0 : event.key === 'End' ? last
        : event.key === 'ArrowLeft' ? at - 1 : event.key === 'ArrowRight' ? at + 1
          : event.key === 'ArrowUp' ? (currentRow > 0 ? at - columns : at)
            : currentRow < lastRow ? Math.min(last, at + columns) : at);
    };
    const click = event => {
      if (popup.contains(event.target)) return;
      const other = event.target.closest?.('[data-storyboard-stack]');
      if (other && other.dataset.storyboardStack !== entry.id && galleryElement.contains(other)) return;
      stop(event);
      callbacks.current.onClose(other === anchor || event.detail === 0);
    };
    const pointer = () => { keyboard.current = false; };
    const focusin = event => {
      if (popup.contains(event.target)) return;
      event.stopPropagation(); focus(current);
    };
    const reposition = () => place(false);
    doc.addEventListener('keydown', keydown, true);
    doc.addEventListener('click', click, true);
    doc.addEventListener('pointerdown', pointer, true);
    doc.addEventListener('focusin', focusin, true);
    body.addEventListener?.('scroll', reposition, { passive: true });
    view?.addEventListener('resize', reposition);
    return () => {
      doc.removeEventListener('keydown', keydown, true);
      doc.removeEventListener('click', click, true);
      doc.removeEventListener('pointerdown', pointer, true);
      doc.removeEventListener('focusin', focusin, true);
      body.removeEventListener?.('scroll', reposition);
      view?.removeEventListener('resize', reposition);
      if (native) { try { popup.hidePopover(); } catch { /* Already removed from the top layer. */ } }
    };
  }, [entry.id, anchor, galleryElement, columns]);

  const name = spaceSetDisplayName(entry.set);
  return <div ref={ref} className={styles.stackPopup} role="dialog" aria-label={`${name}, ${entry.members.length}장 중 한 장 고르기`}
    data-cols={columns} data-row={row} data-column={column} data-many={entry.members.length > 8 || undefined} data-floating="fallback">
    <i className={styles.stackPopupPointer} aria-hidden="true" />
    <p className={styles.stackPopupHeading}><b>{name}</b><span>{entry.members.length}장</span></p>
    <div className={styles.stackPopupGrid}>{entry.members.map(example => <button type="button" key={example.id}
      className={`sb-excell ${styles.example}${example.id === selectedId ? ' sel' : ''}`} data-example-id={example.id}
      aria-label={`${name} ${entry.members.indexOf(example) + 1}번째 사진`} aria-pressed={example.id === selectedId}
      tabIndex={example.id === selectedId || (!entry.members.some(item => item.id === selectedId) && example === entry.members[0]) ? 0 : -1}
      draggable={Boolean(onDrag)} onDragStart={event => {
        callbacks.current.onDrag?.(event, example);
        let finished = false;
        finishDrag.current = () => {
          if (finished) return;
          finished = true; callbacks.current.onDragEnd?.();
        };
        // Keep native cleanup on the source even after React removes the popup.
        event.currentTarget?.addEventListener?.('dragend', finishDrag.current, { once: true });
        const view = ref.current?.ownerDocument.defaultView;
        if (view?.requestAnimationFrame) view.requestAnimationFrame(() => callbacks.current.onClose(false));
        else callbacks.current.onClose(false);
      }} onDragEnd={() => finishDrag.current?.()}
      onClick={event => {
        callbacks.current.onChoose(example.id);
        callbacks.current.onClose(event.detail === 0 || keyboard.current, example.id);
      }}>
      <ExampleThumb example={{ ...example, thumb: generationExampleImageSources(example).src || example.thumb }} />
      {example.id === selectedId && <span className="ck"><Icon name="check" size={12} stroke={1.5} /></span>}
      {usedExampleIds.has(example.id) && <span className={styles.used}>이미 사용</span>}
    </button>)}</div>
  </div>;
}
