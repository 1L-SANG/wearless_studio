/* =============================================================
   components/States.jsx — EmptyState · ErrorState · Skeleton.
   Loading / empty / error surfaces (PRD §15.1, §15.2).
   NOTE on styling: per agents.md no static inline styles. Skeleton
   sizes are irreducibly dynamic, so they are passed as CSS custom
   properties (not static style) and consumed in the module.
   ============================================================= */
import { Icon } from '@/components/Icon.jsx';
import { Button } from '@/components/Button.jsx';
import styles from './States.module.css';

export function EmptyState({ icon = 'image', title, desc, action }) {
  return (
    <div className={styles.empty}>
      <div className={styles.ico}><Icon name={icon} size={24} /></div>
      <h3 className={styles.title}>{title}</h3>
      {desc && <p className={styles.desc}>{desc}</p>}
      {action && <div className={styles.action}>{action}</div>}
    </div>
  );
}

export function ErrorState({ title = '문제가 발생했어요', desc, onRetry }) {
  return (
    <div className={`${styles.empty} ${styles.error}`}>
      <div className={styles.ico}><Icon name="alertTri" size={24} /></div>
      <h3 className={styles.title}>{title}</h3>
      {desc && <p className={styles.desc}>{desc}</p>}
      {onRetry && <div className={styles.action}><Button variant="ghost" icon="refresh" onClick={onRetry}>다시 시도</Button></div>}
    </div>
  );
}

export function Skeleton({ w, h, r, className }) {
  // dynamic dimensions → CSS custom properties (not static inline styling)
  const cssVars = {
    '--sk-w': w != null ? (typeof w === 'number' ? `${w}px` : w) : '100%',
    '--sk-h': h != null ? (typeof h === 'number' ? `${h}px` : h) : '16px',
    '--sk-r': r != null ? (typeof r === 'number' ? `${r}px` : r) : 'var(--r-4)',
  };
  return <div className={`${styles.skeleton} ${className || ''}`} style={cssVars} />;
}

export default EmptyState;
