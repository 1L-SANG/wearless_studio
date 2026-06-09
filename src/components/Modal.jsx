/* =============================================================
   components/Modal.jsx — centered overlay, Escape to close (PRD §14.5).
   ============================================================= */
import { useEffect } from 'react';
import styles from './Modal.module.css';

export function Modal({ children, onClose, wide }) {
  useEffect(() => {
    const h = (e) => e.key === 'Escape' && onClose && onClose();
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [onClose]);

  return (
    <div className={styles.overlay} onClick={onClose}>
      <div className={`${styles.modal} ${wide ? styles.wide : ''}`} onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
        {children}
      </div>
    </div>
  );
}

export default Modal;
