import { useLayoutEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { setPreviewPlacement } from '../../lib/storyboardSetPreviewPlacement.js';
import './SpaceSetHoverPreview.css';

// Fixed outside dock adopted from the canonical StoryboardSetPicker preview.
export default function SpaceSetHoverPreview({ preview, onEnter, onLeave, onClose, onChooseMember, onDragStart, onDragEnd, canChooseMember, dragging }) {
  const ref = useRef(null);
  const scrollRef = useRef(null);
  const current = useRef({ preview, onClose });
  current.current = { preview, onClose };
  useLayoutEffect(() => {
    const place = () => {
      const { preview: active, onClose: close } = current.current;
      if (!active.anchor.isConnected || !active.panel.isConnected) { close(); return; }
      const panel = active.panel.getBoundingClientRect();
      const card = active.anchor.getBoundingClientRect();
      const position = setPreviewPlacement({ panel, card, side: 'left', viewportWidth: innerWidth, viewportHeight: innerHeight });
      for (const key of ['left', 'top', 'width', 'height']) ref.current.style.setProperty(`--set-preview-${key}`, `${position[key]}px`);
      ref.current.dataset.placement = position.placement;
      ref.current.style.setProperty('--set-preview-row-height', `${(position.height - 12 - 16) / Math.min(3, active.set.members.length)}px`);
    };
    const onScroll = (event) => {
      if (ref.current.contains(event.target)) return;
      const { preview: active, onClose: close } = current.current;
      const card = active.anchor.getBoundingClientRect(), panel = active.panel.getBoundingClientRect();
      if (!active.pinned && !dragging && (card.bottom <= Math.max(0, panel.top) || card.top >= Math.min(innerHeight, panel.bottom))) close();
      else place();
    };
    place();
    const observer = new ResizeObserver(place); observer.observe(preview.panel);
    window.addEventListener('resize', place); document.addEventListener('scroll', onScroll, true);
    return () => { observer.disconnect(); window.removeEventListener('resize', place); document.removeEventListener('scroll', onScroll, true); };
  }, [preview.panel, dragging, preview.set.members.length]);
  useLayoutEffect(() => {
    ref.current.style.setProperty('--set-preview-visible-count', Math.min(3, preview.set.members.length));
    scrollRef.current.scrollTop = 0;
  }, [preview.set.id, preview.set.members.length]);
  return createPortal(
    <div ref={ref} className={`sb-set-hover-preview${dragging ? ' is-dragging' : ''}`} id="sb-set-cut-preview" role="region" aria-label="세트 구성 미리보기"
      onMouseEnter={onEnter} onMouseLeave={onLeave} onFocusCapture={onEnter}
      onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) onLeave(); }}
      onKeyDown={(event) => { if (event.key === 'Escape') { event.preventDefault(); onClose(); } }}>
      <button type="button" className="sb-set-hover-close" aria-label="세트 미리보기 닫기" onClick={onClose}>×</button>
      <div ref={scrollRef} className="sb-set-hover-scroll" tabIndex={0} aria-label="구성 사진 스크롤">
        <div className="sb-set-hover-images">
          {preview.set.members.map((member, index) => (
            <button type="button" className="sb-set-hover-cut" key={member.exampleId || index}
              disabled={!canChooseMember(preview.set, member)} draggable={canChooseMember(preview.set, member)}
              onDragStart={(event) => onDragStart(event, preview.set, member)} onDragEnd={onDragEnd}
              onClick={() => onChooseMember(preview.set, member)} aria-label={`${index + 1}번째 컷 추가`}>
              <span className="sb-set-hover-photo"><img src={member.thumb} alt="" draggable={false} /><b>{index + 1}</b></span>
            </button>
          ))}
        </div>
      </div>
    </div>, document.body,
  );
}
