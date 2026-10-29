import { useEffect, useRef } from 'react';
import { Modal } from '@/components/ui.jsx';
import styles from './MallRegisterModal.module.css';

// A05에서 공통 묶음 등록 화면으로 교체한다. 삭제된 단일 몰 API를 호출하지 않는다.
export function MallRegisterModal({ onClose, anchorRef }) {
  const closeRef = useRef(null);
  useEffect(() => {
    closeRef.current?.focus();
    return () => anchorRef?.current?.focus();
  }, [anchorRef]);
  return <Modal onClose={onClose} narrow><div className={styles.dialog} role="dialog" aria-modal="true" aria-labelledby="mall-register-title"
    onKeyDown={(event) => { if (event.key === 'Tab') { event.preventDefault(); closeRef.current?.focus(); } }}>
    <header className={styles.header}><h2 id="mall-register-title">쇼핑몰 등록 준비 중</h2></header>
    <div className={styles.body}><p>판매 기본 정보를 먼저 저장해 두세요.</p></div>
    <div className="modal-actions"><button type="button" className="btn btn-ghost" ref={closeRef} onClick={onClose}>닫기</button></div>
  </div></Modal>;
}
