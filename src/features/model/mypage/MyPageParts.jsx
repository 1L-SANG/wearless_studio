import { FileText, Image as ImageIcon, RotateCw } from 'lucide-react';
import { usageMonths, monthLabel } from './usageMonths.js';
import s from './MyPage.module.css';

export function EmptyPanel({ title, description, onRetry, image = false }) {
  const Icon = image ? ImageIcon : FileText;
  return <div className={s.emptyPanel}><Icon className={s.icon} aria-hidden="true" /><p>{title}</p>
    {description && <small>{description}</small>}
    {onRetry && <button type="button" className={s.quietButton} onClick={onRetry}><RotateCw className={s.icon} aria-hidden="true" />다시 불러오기</button>}
  </div>;
}

export function MonthSelect({ rows, month, onChange, label }) {
  return <select className={s.monthSelect} aria-label={label} value={month} onChange={event => onChange(event.target.value)}>
    {usageMonths(rows).map(value => <option value={value} key={value}>{monthLabel(value)}</option>)}
  </select>;
}
