import React, { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import styles from './StoryboardLayout.module.css';

// Kept local so no shared modal styling/positioning changes affect other flows.
export function StoryboardRemoveDialog({ name, consequence, onCancel, onConfirm }) {
  const ref = useRef(null), previous = useRef(null);
  useEffect(() => {
    previous.current = document.activeElement;
    ref.current?.querySelector('button')?.focus();
    const key = event => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopImmediatePropagation(); onCancel(); }
      if (event.key !== 'Tab') return;
      const controls = [...(ref.current?.querySelectorAll('button') || [])];
      if (event.shiftKey && document.activeElement === controls[0]) { event.preventDefault(); controls.at(-1)?.focus(); }
      else if (!event.shiftKey && document.activeElement === controls.at(-1)) { event.preventDefault(); controls[0]?.focus(); }
    };
    document.addEventListener('keydown', key, true);
    return () => {
      document.removeEventListener('keydown', key, true);
      const target = previous.current?.isConnected ? previous.current
        : document.querySelector('[data-section-key] [aria-current="true"]') || document.querySelector('[data-section-key] button');
      target?.focus({ preventScroll: true });
    };
  }, []);
  const dialog = <div className={`sb-v5-theme ${styles.removeOverlay}`} data-storyboard-action onClick={event => { if (event.target === event.currentTarget) onCancel(); }}>
    <div ref={ref} role="dialog" aria-modal="true" aria-labelledby="conti-remove-title" aria-describedby="conti-remove-consequence" className={styles.removeDialog}>
      <h2 id="conti-remove-title">{name} 섹션을 뺄까요?</h2><p id="conti-remove-consequence">{consequence || '이 섹션의 사진이 페이지에서 빠져요'}</p>
      <div className="modal-actions"><button type="button" onClick={onCancel}>그대로 두기</button><button type="button" onClick={onConfirm}>빼기</button></div>
    </div>
  </div>;
  return typeof document !== 'undefined' && document.body ? createPortal(dialog, document.body) : dialog;
}
