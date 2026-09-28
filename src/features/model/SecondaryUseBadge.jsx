import { ShieldCheck } from 'lucide-react';
import s from './SecondaryUseBadge.module.css';

export function SecondaryUseBadge({ enabled, className = '' }) {
  if (!enabled) return null;
  const classes = className ? `${s.badge} ${className}` : s.badge;
  return (
    <span aria-label="2차 활용 가능" className={classes}>
      <ShieldCheck aria-hidden="true" size={13} strokeWidth={2} />
      2차 활용 가능
    </span>
  );
}
