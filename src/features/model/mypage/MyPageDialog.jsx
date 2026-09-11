import { useEffect, useRef } from 'react';
import s from './MyPage.module.css';

export function MyPageDialog({ title, children, onClose, busy = false, certificate = false, returnFocusRef }) {
  const ref = useRef(null);
  const headingRef = useRef(null);
  useEffect(() => {
    const opener = returnFocusRef?.current || document.activeElement;
    const dialog = ref.current;
    dialog.showModal();
    return () => { dialog.close(); if (opener?.isConnected) opener.focus(); };
  }, []);
  useEffect(() => { headingRef.current?.focus(); }, [title]);
  return <dialog ref={ref} className={`${s.dialog} ${certificate ? s.certificateDialog : ''}`} aria-labelledby="mypage-dialog-title"
    onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}>
    <div className={s.sectionHeading}>
      <h2 id="mypage-dialog-title" ref={headingRef} tabIndex={-1}>{title}</h2>
      <button className={s.textLink} type="button" onClick={onClose} disabled={busy} aria-label="대화상자 닫기">닫기</button>
    </div>
    {children}
  </dialog>;
}
