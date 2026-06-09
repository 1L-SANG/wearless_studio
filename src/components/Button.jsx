/* =============================================================
   components/Button.jsx — Button + IconButton.
   Variants: primary (near-black pill CTA) / ghost / quiet / danger.
   ============================================================= */
import { Icon } from '@/components/Icon.jsx';
import styles from './Button.module.css';

export function Button({
  children, variant = 'ghost', size, block, onClick, disabled, icon, iconRight, type, style, title,
}) {
  const cls = [
    styles.btn,
    styles[variant],
    size === 'sm' && styles.sm,
    size === 'lg' && styles.lg,
    block && styles.block,
  ].filter(Boolean).join(' ');
  const isz = size === 'sm' ? 16 : 18;
  return (
    <button type={type || 'button'} disabled={disabled} style={style} title={title} className={cls} onClick={onClick}>
      {icon && <Icon name={icon} size={isz} />}
      {children}
      {iconRight && <Icon name={iconRight} size={isz} />}
    </button>
  );
}

export function IconButton({ name, onClick, active, size = 'md', title, stroke, disabled }) {
  const cls = [styles.iconbtn, size === 'sm' && styles.iconSm, active && styles.iconActive].filter(Boolean).join(' ');
  return (
    <button type="button" className={cls} onClick={onClick} title={title} disabled={disabled} aria-pressed={active || undefined}>
      <Icon name={name} size={size === 'sm' ? 15 : 17} stroke={stroke || 2} />
    </button>
  );
}

export default Button;
