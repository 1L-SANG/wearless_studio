/* =============================================================
   components/Progress.jsx — ProgressBar + Checklist (PRD §14.5).
   Progress fill is data-driven → width via a CSS custom property.
   ============================================================= */
import { Icon } from '@/components/Icon.jsx';
import styles from './Progress.module.css';

export function ProgressBar({ value = 0, label, sub }) {
  return (
    <div className={styles.wrap}>
      {(label || value != null) && (
        <div className={styles.labelRow}>
          {label && <span className={styles.label}>{label}</span>}
          <span className={styles.pct}>{value}%</span>
        </div>
      )}
      <div className={styles.track}>
        <i className={styles.fill} style={{ '--pct': `${value}%` }} />
      </div>
      {sub && <p className={styles.sub}>{sub}</p>}
    </div>
  );
}

export function Checklist({ items = [] }) {
  return (
    <div className={styles.checklist}>
      {items.map((it) => (
        <div key={it.key} className={`${styles.item} ${it.status === 'done' ? styles.done : ''} ${it.status === 'running' ? styles.running : ''}`}>
          <span className={styles.ci}>
            {it.status === 'done'
              ? <Icon name="check" size={13} />
              : it.status === 'running'
                ? <Icon name="loader" size={13} className="spin" />
                : <span className={styles.dot} />}
          </span>
          {it.label}
        </div>
      ))}
    </div>
  );
}

export default ProgressBar;
