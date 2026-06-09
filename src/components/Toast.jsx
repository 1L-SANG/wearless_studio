/* =============================================================
   components/Toast.jsx — top-right toast system (PRD §14.5).
   White surface, fade-out, optional undo action.
   ============================================================= */
import { createContext, useContext, useState, useCallback } from 'react';
import { Icon } from '@/components/Icon.jsx';
import styles from './Toast.module.css';

const ToastCtx = createContext(null);

export function ToastProvider({ children }) {
  const [list, setList] = useState([]);

  const push = useCallback((msg, opts = {}) => {
    const id = Math.random().toString(36).slice(2);
    setList((l) => [...l, { id, msg, ...opts }]);
    if (!opts.sticky) {
      const dur = opts.duration || 2600;
      setTimeout(() => setList((l) => l.map((t) => (t.id === id ? { ...t, exiting: true } : t))), dur);
      setTimeout(() => setList((l) => l.filter((t) => t.id !== id)), dur + 420);
    }
    return id;
  }, []);

  const dismiss = useCallback((id) => {
    setList((l) => l.map((t) => (t.id === id ? { ...t, exiting: true } : t)));
    setTimeout(() => setList((l) => l.filter((t) => t.id !== id)), 420);
  }, []);

  return (
    <ToastCtx.Provider value={{ push, dismiss }}>
      {children}
      <div className={styles.host}>
        {list.map((t) => (
          <div className={`${styles.toast} ${t.exiting ? styles.out : ''}`} key={t.id}>
            {t.icon && <Icon name={t.icon} size={17} />}
            <span>{t.msg}</span>
            {t.undo && <span className={styles.undo} onClick={() => { t.undo(); dismiss(t.id); }}>되돌리기</span>}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export const useToast = () => useContext(ToastCtx);

export default ToastProvider;
