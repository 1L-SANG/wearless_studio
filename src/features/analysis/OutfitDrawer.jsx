import React, { useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { X, Search, Plus } from 'lucide-react';
import { filterOutfitItems, sortOutfitItems } from '@/lib/outfitPresentation.js';
import { OutfitColorChips, OutfitTile } from './OutfitCatalog.jsx';
import styles from './OutfitSelection.module.css';

export function OutfitDrawer({ role, label, items, selectedId, onSelect, onClose, onUpload, modelCredit }) {
  const [search, setSearch] = useState('');
  const [color, setColor] = useState('');
  const [limit, setLimit] = useState(24);
  const panel = useRef(null);
  const heading = useId();
  const latestClose = useRef(onClose);
  latestClose.current = onClose;
  useEffect(() => {
    const previous = document.activeElement;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    panel.current?.querySelector('input')?.focus({ preventScroll: true });
    const focusables = () => [...(panel.current?.querySelectorAll('button:not([disabled]), input, [tabindex="0"]') || [])];
    const onKey = (event) => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); latestClose.current(); }
      if (event.key !== 'Tab') return;
      const controls = focusables(); const first = controls[0]; const last = controls.at(-1);
      if (event.shiftKey && (document.activeElement === first || !panel.current?.contains(document.activeElement))) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && (document.activeElement === last || !panel.current?.contains(document.activeElement))) { event.preventDefault(); first?.focus(); }
    };
    const trapFocus = (event) => { if (!panel.current?.contains(event.target)) focusables()[0]?.focus({ preventScroll: true }); };
    document.addEventListener('keydown', onKey, true);
    document.addEventListener('focusin', trapFocus);
    return () => {
      document.removeEventListener('keydown', onKey, true); document.removeEventListener('focusin', trapFocus);
      document.body.style.overflow = overflow;
      if (previous?.isConnected) previous.focus({ preventScroll: true });
    };
  }, []);
  const filtered = sortOutfitItems(filterOutfitItems(items.filter((item) => !item.isCustom), { role, color, search }), { role, drawer: true });
  return createPortal(<div className={styles.drawerBackdrop} onPointerDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <section className={styles.drawer} role="dialog" aria-modal="true" aria-labelledby={heading} ref={panel}>
      <header className={styles.drawerHead}><h2 id={heading}>{label} 전체 보기</h2>
        {onUpload && !items.some((item) => item.isCustom) && <button type="button" className={styles.more} onClick={onUpload}><Plus size={14} />내 옷 추가</button>}
        <button type="button" className={styles.close} aria-label="전체 보기 닫기" onClick={onClose}><X size={20} /></button>
      </header>
      <label className={styles.search}><Search size={17} aria-hidden="true" /><span className="sr-only">이름 찾기</span><input value={search} placeholder="이름 찾기" onChange={(event) => { setSearch(event.target.value); setLimit(24); }} /></label>
      {role !== 'model' && <OutfitColorChips role={role} items={items} value={color} drawer onChange={(value) => { setColor(value); setLimit(24); }} />}
      <div className={styles.drawerScroll}>
        <div className={styles.grid}>{filtered.slice(0, limit).map((item) => <OutfitTile key={item.id} role={role} item={item} selected={selectedId === item.id} onSelect={onSelect} creditLabel={modelCredit?.(item)} />)}</div>
        {!filtered.length && <p className={styles.noResults}>찾는 옷이 없어요.</p>}
        {limit < filtered.length && <button type="button" className={styles.more} onClick={() => setLimit((n) => n + 24)}>24개 더 보기</button>}
      </div>
    </section>
  </div>, document.body);
}
