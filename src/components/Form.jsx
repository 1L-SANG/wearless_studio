/* =============================================================
   components/Form.jsx — form atoms shared across screens:
   Field · Chips · TagInput · Toggle · Segmented · Tabs (PRD §14.5).
   ============================================================= */
import { useState } from 'react';
import { Icon } from '@/components/Icon.jsx';
import styles from './Form.module.css';

export function Field({ label, opt, hint, ...props }) {
  return (
    <div className={styles.fieldRow}>
      {label && <label className={styles.lbl}>{label}{opt && <span className={styles.opt}>{opt}</span>}</label>}
      <input className={styles.field} {...props} />
      {hint && <span className={styles.hint}>{hint}</span>}
    </div>
  );
}

/* segmented chips — single or multi select. value = string | string[] */
export function Chips({ options, value, onChange, multi, star }) {
  const arr = multi ? (value || []) : [value];
  const toggle = (v) => {
    if (multi) {
      const set = new Set(value || []);
      set.has(v) ? set.delete(v) : set.add(v);
      onChange([...set]);
    } else onChange(v === value ? null : v);
  };
  return (
    <div className={styles.chips}>
      {options.map((o) => {
        const v = typeof o === 'string' ? o : o.value;
        const label = typeof o === 'string' ? o : o.label;
        const on = arr.includes(v);
        return (
          <button key={v} type="button" className={`${styles.chip} ${on ? styles.chipOn : ''}`} onClick={() => toggle(v)}>
            {star && on && <Icon name="star" size={13} fill="currentColor" className={styles.chipStar} />}
            {label}
          </button>
        );
      })}
    </div>
  );
}

export function TagInput({ tags, onChange, max = 5, placeholder = '내용을 입력하고 Enter' }) {
  const [v, setV] = useState('');
  const add = () => {
    const t = v.trim();
    if (t && tags.length < max && !tags.includes(t)) { onChange([...tags, t]); setV(''); }
  };
  return (
    <div className={styles.taginput}>
      {tags.map((t, i) => (
        <span className={styles.tag} key={i}>
          {t}
          <span className={styles.tagx} onClick={() => onChange(tags.filter((_, j) => j !== i))}><Icon name="x" size={13} /></span>
        </span>
      ))}
      {tags.length < max && (
        <input
          className={styles.taginputField}
          value={v}
          onChange={(e) => setV(e.target.value)}
          placeholder={placeholder}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add(); } }}
          onBlur={add}
        />
      )}
    </div>
  );
}

export function Toggle({ on, onChange }) {
  return (
    <button
      type="button"
      className={`${styles.tg} ${on ? styles.tgOn : ''}`}
      onClick={() => onChange(!on)}
      role="switch"
      aria-checked={on}
    />
  );
}

/* white-pill sliding segmented control */
export function Segmented({ options, value, onChange }) {
  return (
    <div className={styles.segmented}>
      {options.map((o) => {
        const v = typeof o === 'string' ? o : o.value;
        const label = typeof o === 'string' ? o : o.label;
        return (
          <button key={v} type="button" className={`${styles.seg} ${value === v ? styles.segOn : ''}`} onClick={() => onChange(v)}>
            {label}
          </button>
        );
      })}
    </div>
  );
}

/* underline sliding tabs */
export function Tabs({ options, value, onChange }) {
  return (
    <div className={styles.tabs}>
      {options.map((o) => {
        const v = typeof o === 'string' ? o : o.value;
        const label = typeof o === 'string' ? o : o.label;
        return (
          <button key={v} type="button" className={`${styles.tab} ${value === v ? styles.tabOn : ''}`} onClick={() => onChange(v)}>
            {label}
          </button>
        );
      })}
    </div>
  );
}

export default Field;
