/* =============================================================
   components/ui.jsx — shared atoms + primitives (dumb, reusable)
   Ported verbatim from reference/prototype/components/ui.jsx.
   Only change vs prototype: React hooks via ES import, components
   exported (instead of window.*). Markup + classNames unchanged.
   ============================================================= */
import { useState, useEffect, useRef, useCallback, createContext, useContext } from 'react';

/* ---- Lucide icon paths (stroke, 24x24) ---- */
const ICONS = {};
Object.assign(ICONS, {
  sparkles: '<path d="m12 3-1.9 5.8a2 2 0 0 1-1.3 1.3L3 12l5.8 1.9a2 2 0 0 1 1.3 1.3L12 21l1.9-5.8a2 2 0 0 1 1.3-1.3L21 12l-5.8-1.9a2 2 0 0 1-1.3-1.3z"/>',
  shirt: '<path d="M20.38 3.46 16 2a4 4 0 0 1-8 0L3.62 3.46a2 2 0 0 0-1.34 2.23l.58 3.47a1 1 0 0 0 .99.84H6v10c0 .55.45 1 1 1h10c.55 0 1-.45 1-1V10h2.15a1 1 0 0 0 .99-.84l.58-3.47a2 2 0 0 0-1.34-2.23z"/>',
  image: '<rect width="18" height="18" x="3" y="3" rx="2" ry="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/>',
  imagePlus: '<path d="M16 5h6"/><path d="M19 2v6"/><path d="M21 11.5V19a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h7.5"/><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/><circle cx="9" cy="9" r="2"/>',
  layout: '<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M3 9h18"/><path d="M9 21V9"/>',
  type: '<path d="M4 7V4h16v3"/><path d="M9 20h6"/><path d="M12 4v16"/>',
  shapes: '<path d="M8.3 10a.7.7 0 0 1-.626-1.079L11.4 3a.7.7 0 0 1 1.198-.043L16.3 8.9a.7.7 0 0 1-.572 1.1Z"/><rect x="3" y="14" width="7" height="7" rx="1"/><circle cx="17.5" cy="17.5" r="3.5"/>',
  eye: '<path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/>',
  save: '<path d="M15.2 3a2 2 0 0 1 1.4.6l3.8 3.8a2 2 0 0 1 .6 1.4V19a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/><path d="M17 21v-7a1 1 0 0 0-1-1H8a1 1 0 0 0-1 1v7"/><path d="M7 3v4a1 1 0 0 0 1 1h7"/>',
  download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" x2="12" y1="15" y2="3"/>',
  undo: '<path d="M3 7v6h6"/><path d="M21 17a9 9 0 0 0-9-9 9 9 0 0 0-6 2.3L3 13"/>',
  redo: '<path d="M21 7v6h-6"/><path d="M3 17a9 9 0 0 1 9-9 9 9 0 0 1 6 2.3L21 13"/>',
  plus: '<path d="M5 12h14"/><path d="M12 5v14"/>',
  plusCircle: '<circle cx="12" cy="12" r="10"/><path d="M8 12h8"/><path d="M12 8v8"/>',
  wand: '<path d="m3 21 9-9"/><path d="M15 4V2"/><path d="M15 16v-2"/><path d="M8 9h2"/><path d="M20 9h2"/><path d="M17.8 11.8 19 13"/><path d="M15 9h0"/><path d="M17.8 6.2 19 5"/><path d="m3 21 9-9"/><path d="M12.2 6.2 11 5"/>',
  refresh: '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M3 21v-5h5"/>',
  landscape: '<path d="m2 17 5.5-7 4 5L15 11l7 8"/><circle cx="8" cy="7" r="2"/><rect x="2" y="3" width="20" height="18" rx="2"/>',
  person: '<circle cx="12" cy="5" r="1"/><path d="m9 20 3-6 3 6"/><path d="m6 8 6 2 6-2"/><path d="M12 10v4"/>',
  smile: '<circle cx="12" cy="12" r="10"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/><line x1="9" x2="9.01" y1="9" y2="9"/><line x1="15" x2="15.01" y1="9" y2="9"/>',
  upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" x2="12" y1="3" y2="15"/>',
  trash: '<path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
  checkSquare: '<path d="m9 11 3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>',
  circle: '<circle cx="12" cy="12" r="10"/>',
  chevUp: '<path d="m18 15-6-6-6 6"/>',
  chevDown: '<path d="m6 9 6 6 6-6"/>',
  chevLeft: '<path d="m15 18-6-6 6-6"/>',
  chevRight: '<path d="m9 18 6-6-6-6"/>',
  x: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  arrowRight: '<path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>',
  arrowUp: '<path d="M12 19V5"/><path d="m5 12 7-7 7 7"/>',
  arrowLeft: '<path d="M19 12H5"/><path d="m12 19-7-7 7-7"/>',
  layers: '<path d="M12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.84z"/><path d="M2 12a1 1 0 0 0 .58.91l8.6 3.91a2 2 0 0 0 1.65 0l8.58-3.9A1 1 0 0 0 22 12"/><path d="M2 17a1 1 0 0 0 .58.91l8.6 3.91a2 2 0 0 0 1.65 0l8.58-3.9A1 1 0 0 0 22 17"/>',
  bringFront: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M4 16a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2"/>',
  sendBack: '<rect x="4" y="4" width="12" height="12" rx="2"/><path d="M20 8a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H10a2 2 0 0 1-2-2"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M12 2v2m0 16v2M4.93 4.93l1.41 1.41m11.32 11.32 1.41 1.41M2 12h2m16 0h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/>',
  user: '<path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
  library: '<path d="m16 6 4 14"/><path d="M12 6v14"/><path d="M8 8v12"/><path d="M4 4v16"/>',
  alertTri: '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
  alertCircle: '<circle cx="12" cy="12" r="10"/><line x1="12" x2="12" y1="8" y2="12"/><line x1="12" x2="12.01" y1="16" y2="16"/>',
  loader: '<line x1="12" x2="12" y1="2" y2="6"/><line x1="12" x2="12" y1="18" y2="22"/><line x1="4.93" x2="7.76" y1="4.93" y2="7.76"/><line x1="16.24" x2="19.07" y1="16.24" y2="19.07"/><line x1="2" x2="6" y1="12" y2="12"/><line x1="18" x2="22" y1="12" y2="12"/><line x1="4.93" x2="7.76" y1="19.07" y2="16.24"/><line x1="16.24" x2="19.07" y1="7.76" y2="4.93"/>',
  lock: '<rect width="18" height="11" x="3" y="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
  unlock: '<rect width="18" height="11" x="3" y="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 9.9-1"/>',
  eyeOff: '<path d="M10.73 5.08A10.43 10.43 0 0 1 12 5c7 0 10 7 10 7a13.16 13.16 0 0 1-1.67 2.68"/><path d="M6.61 6.61A13.5 13.5 0 0 0 2 12s3 7 10 7a9.74 9.74 0 0 0 5.39-1.61"/><path d="M9.88 9.88a3 3 0 1 0 4.24 4.24"/><line x1="2" x2="22" y1="2" y2="22"/>',
  pencil: '<path d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z"/>',
  copy: '<rect width="14" height="14" x="8" y="8" rx="2" ry="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>',
  gripV: '<circle cx="9" cy="6" r="1.3"/><circle cx="9" cy="12" r="1.3"/><circle cx="9" cy="18" r="1.3"/><circle cx="15" cy="6" r="1.3"/><circle cx="15" cy="12" r="1.3"/><circle cx="15" cy="18" r="1.3"/>',
  info: '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
  star: '<path d="M11.525 2.295a.53.53 0 0 1 .95 0l2.31 4.679a2.12 2.12 0 0 0 1.595 1.16l5.166.756a.53.53 0 0 1 .294.904l-3.736 3.638a2.12 2.12 0 0 0-.611 1.878l.882 5.14a.53.53 0 0 1-.771.56l-4.618-2.428a2.12 2.12 0 0 0-1.973 0L6.69 21.01a.53.53 0 0 1-.77-.56l.881-5.139a2.12 2.12 0 0 0-.611-1.879L2.453 9.795a.53.53 0 0 1 .294-.906l5.166-.755a2.12 2.12 0 0 0 1.595-1.16z"/>',
  move: '<polyline points="5 9 2 12 5 15"/><polyline points="9 5 12 2 15 5"/><polyline points="15 19 12 22 9 19"/><polyline points="19 9 22 12 19 15"/><line x1="2" x2="22" y1="12" y2="12"/><line x1="12" x2="12" y1="2" y2="22"/>',
  alignLeft: '<line x1="21" x2="3" y1="6" y2="6"/><line x1="15" x2="3" y1="12" y2="12"/><line x1="17" x2="3" y1="18" y2="18"/>',
  alignCenter: '<line x1="21" x2="3" y1="6" y2="6"/><line x1="17" x2="7" y1="12" y2="12"/><line x1="19" x2="5" y1="18" y2="18"/>',
  alignRight: '<line x1="21" x2="3" y1="6" y2="6"/><line x1="21" x2="9" y1="12" y2="12"/><line x1="21" x2="7" y1="18" y2="18"/>',
  bold: '<path d="M6 12h9a4 4 0 0 1 0 8H7a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h7a4 4 0 0 1 0 8"/>',
  italic: '<line x1="19" x2="10" y1="4" y2="4"/><line x1="14" x2="5" y1="20" y2="20"/><line x1="15" x2="9" y1="4" y2="20"/>',
  underline: '<path d="M6 4v6a6 6 0 0 0 12 0V4"/><line x1="4" x2="20" y1="20" y2="20"/>',
  strike: '<path d="M16 4H9a3 3 0 0 0-2.83 4"/><path d="M14 12a4 4 0 0 1 0 8H6"/><line x1="4" x2="20" y1="12" y2="12"/>',
  crop: '<path d="M6 2v14a2 2 0 0 0 2 2h14"/><path d="M18 22V8a2 2 0 0 0-2-2H2"/>',
  rotate: '<path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/>',
  minus: '<path d="M5 12h14"/>',
  grid: '<rect width="7" height="7" x="3" y="3" rx="1"/><rect width="7" height="7" x="14" y="3" rx="1"/><rect width="7" height="7" x="14" y="14" rx="1"/><rect width="7" height="7" x="3" y="14" rx="1"/>',
  maximize: '<path d="M8 3H5a2 2 0 0 0-2 2v3"/><path d="M21 8V5a2 2 0 0 0-2-2h-3"/><path d="M3 16v3a2 2 0 0 0 2 2h3"/><path d="M16 21h3a2 2 0 0 0 2-2v-3"/>',
  search: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
  clock: '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
  coins: '<circle cx="8" cy="8" r="6"/><path d="M18.09 10.37A6 6 0 1 1 10.34 18"/><path d="M7 6h1v4"/><path d="m16.71 13.88.7.71-2.82 2.82"/>',
  link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
  unlink: '<path d="m18.84 12.25 1.72-1.71a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="m5.17 11.75-1.71 1.71a5 5 0 0 0 7.07 7.07l1.71-1.71"/><line x1="8" x2="8" y1="2" y2="5"/><line x1="2" x2="5" y1="8" y2="8"/><line x1="16" x2="16" y1="19" y2="22"/><line x1="19" x2="22" y1="16" y2="16"/>',
  listBullet: '<line x1="8" x2="21" y1="6" y2="6"/><line x1="8" x2="21" y1="12" y2="12"/><line x1="8" x2="21" y1="18" y2="18"/><circle cx="3.6" cy="6" r="1.2"/><circle cx="3.6" cy="12" r="1.2"/><circle cx="3.6" cy="18" r="1.2"/>',
  listOrdered: '<line x1="10" x2="21" y1="6" y2="6"/><line x1="10" x2="21" y1="12" y2="12"/><line x1="10" x2="21" y1="18" y2="18"/><path d="M4 6h1v4"/><path d="M4 10h2"/><path d="M6 18H4c0-1 2-1.4 2-2.5S5 14 4 14.5"/>',
  lineHeight: '<path d="M3 5h11"/><path d="M3 12h11"/><path d="M3 19h11"/><path d="m19 4-2 2.5h4z"/><path d="m19 20-2-2.5h4z"/><path d="M19 7v10"/>',
  letterSpacing: '<path d="M3 4v16"/><path d="M21 4v16"/><path d="m8 16 4-9 4 9"/><path d="M9.4 13h5.2"/>',
  cornerRadius: '<path d="M4 20v-8a8 8 0 0 1 8-8h8"/>',
  droplet: '<path d="M12 22a7 7 0 0 0 7-7c0-2-1-3.9-3-5.5s-3.5-4-4-6.5c-.5 2.5-2 4.9-4 6.5C6 11.1 5 13 5 15a7 7 0 0 0 7 7z"/>',
  ban: '<circle cx="12" cy="12" r="10"/><path d="m4.9 4.9 14.2 14.2"/>',
});

export function Icon({ name, size = 20, stroke = 2, fill = 'none', style, className }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill={fill}
      stroke={fill === 'none' ? 'currentColor' : 'none'} strokeWidth={stroke}
      strokeLinecap="round" strokeLinejoin="round" style={style} className={className}
      dangerouslySetInnerHTML={{ __html: ICONS[name] || '' }} />
  );
}

export function Button({ children, variant = 'ghost', size, block, onClick, disabled, icon, iconRight, type, style }) {
  return (
    <button type={type || 'button'} disabled={disabled} style={style}
      className={`btn btn-${variant}${size ? ' btn-' + size : ''}${block ? ' btn-block' : ''}`}
      onClick={onClick}>
      {icon && <Icon name={icon} size={size === 'sm' ? 16 : 18} />}
      {children}
      {iconRight && <Icon name={iconRight} size={size === 'sm' ? 16 : 18} />}
    </button>
  );
}
export function IconButton({ name, onClick, active, size = 'md', title, stroke }) {
  return (
    <button className={`iconbtn ${size === 'sm' ? 'sm ' : ''}${active ? 'active' : ''}`} onClick={onClick} title={title}>
      <Icon name={name} size={size === 'sm' ? 15 : 17} stroke={stroke || 2} />
    </button>
  );
}

export function Field({ label, opt, hint, ...props }) {
  return (
    <div className="field-row">
      {label && <label className="lbl">{label}{opt && <span className="opt">{opt}</span>}</label>}
      <input className="field" {...props} />
      {hint && <span className="hint">{hint}</span>}
    </div>
  );
}

/* segmented chips — single or multi select. value = string | string[] */
export function Chips({ options, value, onChange, multi, star, className }) {
  const arr = multi ? (value || []) : [value];
  const toggle = (v) => {
    if (multi) {
      const set = new Set(value || []);
      set.has(v) ? set.delete(v) : set.add(v);
      onChange([...set]);
    } else onChange(v === value ? null : v);
  };
  return (
    <div className={`chips${className ? ' ' + className : ''}`}>
      {options.map((o) => {
        const v = typeof o === 'string' ? o : o.value;
        const label = typeof o === 'string' ? o : o.label;
        const on = arr.includes(v);
        return (
          <button key={v} className={`chip${on ? ' on' : ''}`} onClick={() => toggle(v)}>
            {star && on && <Icon name="star" size={13} fill="currentColor" className="chip-star" />}
            {label}
          </button>
        );
      })}
    </div>
  );
}

export function TagInput({ tags, onChange, max = 5, placeholder = '내용을 입력하고 Enter' }) {
  const [v, setV] = useState('');
  const add = () => { const t = v.trim(); if (t && tags.length < max && !tags.includes(t)) { onChange([...tags, t]); setV(''); } };
  return (
    <div className="taginput">
      {tags.map((t, i) => (
        <span className="tag" key={i}>{t}<span className="x" onClick={() => onChange(tags.filter((_, j) => j !== i))}><Icon name="x" size={13} /></span></span>
      ))}
      {tags.length < max && (
        <input value={v} onChange={(e) => setV(e.target.value)} placeholder={placeholder}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add(); } }} />
      )}
    </div>
  );
}

export function Toggle({ on, onChange }) {
  return <div className={`tg${on ? ' on' : ''}`} onClick={() => onChange(!on)} role="switch" aria-checked={on}></div>;
}

export function ProgressBar({ value, label, sub }) {
  return (
    <div>
      {(label || value != null) && (
        <div className="progress-label">
          <span className="caption" style={{ color: 'var(--fg-1)', fontWeight: 500 }}>{label}</span>
          <span className="pct">{value}%</span>
        </div>
      )}
      <div className="progress"><i style={{ width: value + '%' }}></i></div>
      {sub && <p className="hint" style={{ marginTop: 9 }}>{sub}</p>}
    </div>
  );
}

export function Checklist({ items }) {
  return (
    <div className="checklist">
      {items.map((it) => (
        <div key={it.key} className={`check-item ${it.status === 'done' ? 'done' : it.status === 'running' ? 'running' : ''}`}>
          <span className="ci">
            {it.status === 'done' ? <Icon name="check" size={13} /> : it.status === 'running' ? <Icon name="loader" size={13} className="spin" /> : <span style={{ width: 5, height: 5, borderRadius: '50%', background: 'currentColor' }} />}
          </span>
          {it.label}
        </div>
      ))}
    </div>
  );
}

export function Modal({ children, onClose, wide }) {
  useEffect(() => { const h = (e) => e.key === 'Escape' && onClose && onClose(); window.addEventListener('keydown', h); return () => window.removeEventListener('keydown', h); }, [onClose]);
  return (
    <div className="overlay" onClick={onClose}>
      <div className={`modal${wide ? ' wide' : ''}`} onClick={(e) => e.stopPropagation()}>{children}</div>
    </div>
  );
}

export function EmptyState({ icon = 'image', title, desc, action }) {
  return (
    <div className="empty">
      <div className="ico"><Icon name={icon} size={24} /></div>
      <h3>{title}</h3>{desc && <p>{desc}</p>}
      {action && <div style={{ marginTop: 18 }}>{action}</div>}
    </div>
  );
}
export function ErrorState({ title = '문제가 발생했어요', desc, onRetry }) {
  return (
    <div className="error-state">
      <div className="ico"><Icon name="alertTri" size={24} /></div>
      <h3>{title}</h3>{desc && <p>{desc}</p>}
      {onRetry && <div style={{ marginTop: 18 }}><Button variant="ghost" icon="refresh" onClick={onRetry}>다시 시도</Button></div>}
    </div>
  );
}
export function Skeleton({ w, h, r, style }) {
  return <div className="skeleton" style={{ width: w, height: h, borderRadius: r, ...style }} />;
}

/* thumbnail grid for poses / backgrounds / examples */
export function ThumbGrid({ items, value, onChange, cols = 4, labels }) {
  return (
    <div className={`thumb-grid${cols === 3 ? ' cols3' : ''}`}>
      {items.map((it) => {
        const on = value === it.id;
        if (it.auto) return (
          <button key={it.id} className={`tg-cell auto${on ? ' on' : ''}`} onClick={() => onChange(it.id)}>
            <Icon name="sparkles" size={16} />{it.label}
          </button>
        );
        return (
          <button key={it.id} className={`tg-cell${on ? ' on' : ''}`} onClick={() => onChange(it.id)}>
            <img src={it.thumb} alt="" />
            {labels && <span className="lab">{it.label}</span>}
          </button>
        );
      })}
    </div>
  );
}

/* ---- Toast system ---- */
const ToastCtx = createContext(null);
export function ToastProvider({ children }) {
  const [list, setList] = useState([]);
  const push = useCallback((msg, opts = {}) => {
    const id = Math.random().toString(36).slice(2);
    setList((l) => [...l, { id, msg, ...opts }]);
    if (!opts.sticky) {
      const dur = opts.duration || 2600;
      setTimeout(() => setList((l) => l.map((t) => t.id === id ? { ...t, exiting: true } : t)), dur);
      setTimeout(() => setList((l) => l.filter((t) => t.id !== id)), dur + 420);
    }
    return id;
  }, []);
  const dismiss = useCallback((id) => {
    setList((l) => l.map((t) => t.id === id ? { ...t, exiting: true } : t));
    setTimeout(() => setList((l) => l.filter((t) => t.id !== id)), 420);
  }, []);
  return (
    <ToastCtx.Provider value={{ push, dismiss }}>
      {children}
      <div className="toast-host">
        {list.map((t) => (
          <div className={`toast${t.exiting ? ' out' : ''}`} key={t.id}>
            {t.icon && <Icon name={t.icon} size={17} />}
            <span>{t.msg}</span>
            {t.undo && <span className="undo" onClick={() => { t.undo(); dismiss(t.id); }}>되돌리기</span>}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}
export const useToast = () => useContext(ToastCtx);

export { ICONS };
