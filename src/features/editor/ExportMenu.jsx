import { useEffect, useRef } from 'react';
import { Button, Icon } from '@/components/ui.jsx';
import styles from './ExportMenu.module.css';

export function buildExportMenuItems({ malls, publishEnabled, gifCount, onDownload, onPublish, onConnect, onRegisterCafe24, onGifs }) {
  const zigzagDisabled = malls?.disabled || malls?.items?.find((item) => item.mall === 'zigzag')?.status === 'connected';
  const cafe24Status = malls?.items?.find((item) => item.mall === 'cafe24')?.status || 'none';
  const cafe24Disabled = !malls || malls.disabled || malls.loading || !publishEnabled || cafe24Status === 'unavailable';
  const cafe24Go = !cafe24Disabled && cafe24Status !== 'connected';
  return [
    { id: 'download', label: '이미지로 다운로드', icon: 'download', kind: 'action', onSelect: onDownload },
    ...(publishEnabled ? [{ id: 'paste', label: '몰에 붙여넣기', icon: 'link', kind: 'action', onSelect: onPublish }] : []),
    { id: 'zigzag', label: zigzagDisabled ? '지그재그에 상품 등록' : '지그재그 연결하기',
      logo: '/assets/malls/zigzag-48.png', srcSet: '/assets/malls/zigzag-96.png 2x', kind: zigzagDisabled ? 'action' : 'go',
      disabled: zigzagDisabled, badge: zigzagDisabled ? '준비 중' : undefined, onSelect: onConnect },
    { id: 'cafe24', label: cafe24Go ? cafe24Status === 'reconnect_required' ? '카페24 다시 연결하기' : '카페24 연결하기' : '카페24에 상품 등록',
      logo: '/assets/malls/cafe24-48.png', srcSet: '/assets/malls/cafe24-96.png 2x', kind: cafe24Go ? 'go' : 'action',
      disabled: !!cafe24Disabled, badge: cafe24Disabled ? '준비 중' : undefined, onSelect: cafe24Go ? onConnect : onRegisterCafe24 },
    { id: 'ably', label: '에이블리에 상품 등록', logo: '/assets/malls/ably-48.png',
      srcSet: '/assets/malls/ably-96.png 2x', kind: 'action', disabled: true, badge: '준비 중' },
    ...(gifCount > 0 ? [{ id: 'gif', label: 'GIF 따로 받기', icon: 'gif', kind: 'action', onSelect: onGifs }] : []),
  ];
}

export function ExportMenu({ open, onClose, anchorRef, items }) {
  const ref = useRef(null);
  const latest = useRef({ onClose, anchorRef });
  latest.current = { onClose, anchorRef };
  const close = () => { latest.current.onClose(); latest.current.anchorRef.current?.focus(); };
  useEffect(() => {
    if (!open) return;
    ref.current?.querySelector('[role="menuitem"]:not(:disabled)')?.focus();
    const outside = (event) => {
      if (!ref.current?.contains(event.target) && !latest.current.anchorRef.current?.contains(event.target)) close();
    };
    document.addEventListener('pointerdown', outside);
    return () => document.removeEventListener('pointerdown', outside);
  }, [open]);
  if (!open) return null;
  const onKeyDown = (event) => {
    event.stopPropagation();
    if (event.key === 'Escape' || event.key === 'Tab') {
      if (event.key === 'Escape') event.preventDefault();
      close();
      return;
    }
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const buttons = [...event.currentTarget.querySelectorAll('[role="menuitem"]:not(:disabled)')];
    const current = buttons.indexOf(event.currentTarget.ownerDocument.activeElement);
    const index = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1
      : (current + (event.key === 'ArrowUp' ? -1 : 1) + buttons.length) % buttons.length;
    buttons[index]?.focus();
  };
  return <div ref={ref} className={styles.menu} role="menu" aria-label="다운로드와 쇼핑몰 등록" onKeyDown={onKeyDown}>
    {items.map((item) => <button type="button" key={item.id} role="menuitem" tabIndex={-1}
      className={`${styles.item} ${item.kind === 'go' ? styles.go : ''} ${item.id === 'gif' ? styles.gif : ''}`}
      disabled={item.disabled} aria-disabled={item.disabled || undefined}
      onClick={() => { close(); item.onSelect?.(); }}>
      {item.logo ? <img className={styles.logo} src={item.logo} srcSet={item.srcSet} alt="" />
        : item.icon && <Icon name={item.icon === 'gif' ? 'film' : item.icon} size={20} />}
      <span className={styles.label}>{item.label}</span>
      {item.kind === 'go' && <span aria-hidden="true">›</span>}
      {item.badge && <span className={styles.badge}>{item.badge}</span>}
    </button>)}
  </div>;
}

export function ExportSplitButton({ open, onToggle, onClose, anchorRef, items, onDownload, disabled, label = '다운로드' }) {
  return <div className={styles.split}>
    <Button size="sm" variant="primary" icon="download" className={styles.main} disabled={disabled} onClick={onDownload}>{label}</Button>
    <button type="button" ref={anchorRef} className={`btn btn-primary btn-sm ${styles.caret}`} disabled={disabled}
      aria-label="다운로드와 쇼핑몰 등록 메뉴" aria-haspopup="menu" aria-expanded={open}
      onClick={onToggle} onKeyDown={(event) => {
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); event.stopPropagation(); if (!open) onToggle(); }
      }}><Icon name="chevDown" size={16} /></button>
    <ExportMenu open={open} onClose={onClose} anchorRef={anchorRef} items={items} />
  </div>;
}
