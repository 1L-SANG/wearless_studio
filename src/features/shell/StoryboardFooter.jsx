import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { CompanyInfoRows } from '@/components/CompanyInfoRows.jsx';
import { WEARLESS_LEGAL_URLS } from '@/lib/legalLinks.js';
import styles from './StoryboardFooter.module.css';

export function StoryboardFooter() {
  const [open, setOpen] = useState(false);
  const popupId = useId();
  const triggerRef = useRef(null);
  const popupRef = useRef(null);
  const closePopup = useCallback(() => {
    setOpen(false);
    triggerRef.current?.focus({ preventScroll: true });
  }, []);

  useEffect(() => {
    if (!open) return undefined;

    popupRef.current?.focus({ preventScroll: true });
    const onPointerDown = (event) => {
      if (popupRef.current?.contains(event.target) || triggerRef.current?.contains(event.target)) return;
      closePopup();
    };
    const onKeyDown = (event) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      closePopup();
    };
    document.addEventListener('pointerdown', onPointerDown, true);
    document.addEventListener('keydown', onKeyDown, true);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown, true);
      document.removeEventListener('keydown', onKeyDown, true);
    };
  }, [open, closePopup]);

  return (
    <footer className={styles.footer} data-storyboard-footer data-business-open={open ? 'true' : undefined}>
      <nav className={styles.links} aria-label="법적 고지">
        <a className={styles.link} href={WEARLESS_LEGAL_URLS.terms}>이용약관</a>
        <a className={styles.link} href={WEARLESS_LEGAL_URLS.privacy}><strong>개인정보처리방침</strong></a>
        <button
          ref={triggerRef}
          type="button"
          className={styles.link}
          aria-haspopup="dialog"
          aria-expanded={open}
          aria-controls={open ? popupId : undefined}
          onClick={() => { if (open) closePopup(); else setOpen(true); }}
        >사업자 정보</button>
      </nav>
      {open && (
        <section ref={popupRef} id={popupId} className={styles.popup} role="dialog" aria-label="사업자 정보" tabIndex={-1}
          onClick={event => event.stopPropagation()}>
          <div className={styles.popupHeader}>
            <strong>사업자 정보</strong>
            <button type="button" className={styles.close} aria-label="사업자 정보 닫기" onClick={closePopup}>닫기</button>
          </div>
          <div className={styles.company}><CompanyInfoRows /></div>
        </section>
      )}
    </footer>
  );
}

export default StoryboardFooter;
