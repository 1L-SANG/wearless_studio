import React, { useEffect, useRef } from 'react';
import { customMatchWaitProgress } from '@/lib/smoothProgress.js';
import { Plus, X, ChevronRight, UserRound, Shirt, Footprints, ShoppingBag, Gem } from 'lucide-react';
import { colorGroupsForRole, colorGroupForItem } from '@/lib/outfitPresentation.js';
import styles from './OutfitSelection.module.css';

export const outfitImage = (item) => item?.boardUrl || item?.thumb || item?.thumbnailUrl || item?.imageUrl || '';
export const outfitName = (item) => item?.displayName || item?.name || '';
export function OutfitRoleIcon({ role, ...props }) {
  const Symbol = { model: UserRound, footwear: Footprints, bag: ShoppingBag, accessory: Gem }[role] || Shirt;
  return <Symbol aria-hidden="true" strokeWidth={1.4} {...props} />;
}
export function OutfitOrb() { return <span className={styles.orb} aria-hidden="true" />; }

export function OutfitColorChips({ role, items, value, onChange, drawer = false }) {
  const groups = colorGroupsForRole(role).filter((group) => items.some((item) => !item.isCustom && colorGroupForItem(item, role) === group.value));
  return <div className={styles.chips} aria-label="색 묶음">
    <button type="button" className={`${styles.chip} ${!value ? styles.chipSelected : ''}`} aria-pressed={!value} onClick={() => onChange('')}>
      {!drawer && <OutfitOrb />}{drawer ? '전체' : 'AI 추천'}
    </button>
    {groups.map((group) => <button type="button" key={group.value} className={`${styles.chip} ${value === group.value ? styles.chipSelected : ''}`}
      aria-pressed={value === group.value} onClick={() => onChange(group.value)}>
      <i className={styles.swatch} data-color={group.value} aria-hidden="true" />{group.label}
    </button>)}
  </div>;
}

export function OutfitCategoryChips({ items, value, onChange }) {
  const categories = [...new Set(items.filter(item => !item.isCustom).map(item => item.category).filter(Boolean))];
  if (!categories.length) return null;
  return <div className={styles.chips} aria-label="액세서리 종류">
    {['', ...categories].map(category => <button type="button" key={category}
      className={`${styles.chip} ${value === category ? styles.chipSelected : ''}`} aria-pressed={value === category}
      onClick={() => onChange(category)}>{category || '전체'}</button>)}
  </div>;
}

function CustomMatchProgress({ upload }) {
  const circle = useRef(null);
  useEffect(() => {
    let raf;
    const tick = () => {
      const progress = customMatchWaitProgress({ elapsedMs: performance.now() - upload.startedAt, resultAtMs: upload.resultAtMs });
      if (circle.current) circle.current.style.strokeDashoffset = String(100 - progress);
      if (progress < 100) raf = requestAnimationFrame(tick);
    };
    tick(); return () => cancelAnimationFrame(raf);
  }, [upload.startedAt, upload.resultAtMs]);
  return <span className={styles.progressRing} role="status" aria-label="내 옷 정리 중">
    <svg viewBox="0 0 48 48" aria-hidden="true"><circle className={styles.ringTrack} cx="24" cy="24" r="20" /><circle ref={circle} className={styles.ringValue} cx="24" cy="24" r="20" pathLength="100" /></svg>
  </span>;
}

export function OutfitTile({ item, role, selected, onSelect, creditLabel, busy, onDelete, deleting }) {
  const src = outfitImage(item);
  return <div className={styles.tileWrap}>
    <button type="button" className={`${styles.tile} ${selected ? styles.selected : ''}`}
      aria-pressed={selected} aria-busy={!!busy} title={item.isCompatible === false ? '현재 상품과 종류가 맞지 않아요' : undefined} disabled={item.isCompatible === false || !!busy} onClick={() => onSelect(item.id)}>
      <span className={`${styles.tilePicture} ${role === 'model' ? styles.modelPicture : ''} ${busy ? styles.cleaning : ''}`}>
        {src ? <img src={busy?.previewSrc || src} alt="" loading="lazy" /> : <OutfitRoleIcon role={role} />}
        {role !== 'model' && (item.inSets || item.isCustom) && !busy && <span className={styles.aiBadge}>
          {!item.isCustom && <OutfitOrb />}{item.isCustom ? '내 옷' : 'AI 추천'}
        </span>}
        {busy && <CustomMatchProgress upload={busy} />}
      </span>
      <span className={styles.tileName}>{outfitName(item)}{role === 'model' && <strong>{creditLabel}</strong>}{busy && <small>정리 중</small>}</span>
    </button>
    {item.isCustom && !busy && onDelete && <button type="button" className={styles.delete} disabled={deleting} aria-label="내 옷 삭제" onClick={onDelete}><X size={14} /></button>}
  </div>;
}
export function OutfitUploadTile({ onUpload, busy }) {
  if (busy) return <OutfitTile item={{ id: 'uploading', name: '내 옷', thumb: busy.previewSrc, isCustom: true }} role="bottom" busy={busy} />;
  return <button type="button" className={`${styles.tile} ${styles.uploadTile}`} onClick={onUpload}>
    <span className={styles.tilePicture}><span className={styles.plus}><Plus size={18} /></span></span>
    <span className={styles.tileName}>내 옷 올리기</span>
  </button>;
}
export function OutfitMore({ items, role, onClick }) {
  const unit = role === 'model' ? '명' : role === 'footwear' ? '켤레' : ['bag', 'accessory'].includes(role) ? '개' : '벌';
  return <button type="button" className={styles.more} onClick={onClick} aria-haspopup="dialog">
    <span className={styles.miniatures} aria-hidden="true">{items.slice(-3).map((item) => <span key={item.id}>{outfitImage(item) && <img src={outfitImage(item)} alt="" />}</span>)}</span>
    전체 {items.length}{unit} 보기<ChevronRight size={13} />
  </button>;
}
