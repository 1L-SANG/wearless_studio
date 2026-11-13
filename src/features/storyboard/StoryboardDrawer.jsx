import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Icon } from '@/components/ui.jsx';
import styles from './StoryboardPanels.module.css';

export function StoryboardDrawer({ mode, title, busy, onClose, children, focusKey }) {
  const ref = useRef(null), closeRef = useRef(onClose);
  const returnFocus = useRef(null);
  const [scrolled, setScrolled] = useState(false);
  closeRef.current = onClose;
  useEffect(() => {
    if (!ref.current?.contains?.(document.activeElement)) returnFocus.current = document.activeElement;
    ref.current?.focus({ preventScroll: true });
  }, [focusKey]);
  useEffect(() => {
    const dismiss = event => {
      if (ref.current?.contains(event.target)) return;
      if (event.target.closest('button, a, input, select, textarea, [role="button"], [draggable="true"], [data-storyboard-action]')) return;
      closeRef.current?.();
    };
    const escape = event => {
      if (event.key !== 'Escape' || event.defaultPrevented) return;
      event.preventDefault(); closeRef.current?.();
    };
    document.addEventListener('click', dismiss);
    document.addEventListener('keydown', escape);
    document.body.classList.add('storyboard-drawer-open');
    return () => {
      document.removeEventListener('click', dismiss);
      document.removeEventListener('keydown', escape);
      document.body.classList.remove('storyboard-drawer-open');
      const trigger = returnFocus.current;
      const fallback = document.querySelector?.('[data-section-key] [aria-current="true"]');
      (trigger?.isConnected ? trigger : fallback)?.focus({ preventScroll: true });
    };
  }, []);
  return createPortal(<aside ref={ref} className={`sb-v5-theme ${styles.drawer}${mode === 'design' ? ` ${styles.designDrawer}` : ''}`} tabIndex={-1} role="dialog"
    aria-label={mode === 'photo' ? '사진 바꾸기' : mode === 'design' ? '섹션 디자인 바꾸기' : '구성 편집'} aria-busy={busy || undefined}>
    <div className={`${styles.drawerBar}${scrolled ? ` ${styles.drawerScrolled}` : ''}`}>
      {title ? <h2>{title}</h2> : <div />}
      <button type="button" className={styles.close} disabled={busy} onClick={onClose} aria-label="옆 판 닫기"><Icon name="x" size={16} /></button>
    </div>
    <div className={styles.drawerBody} data-storyboard-scroll
      onScroll={event => setScrolled(event.currentTarget.scrollTop > 0)}>{children}</div>
  </aside>, document.body);
}
