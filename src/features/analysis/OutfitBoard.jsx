import React, { useEffect, useMemo, useState } from 'react';
import { Info } from 'lucide-react';
import { api, isMockMode } from '@/lib/api/index.js';
import { OUTFIT_SLOT_LABELS, complementaryOutfitSlot, outfitSlotsFor } from '@/lib/outfitSelections.js';
import { buildOutfitBoardLayout, selectProductBoardImage } from '@/lib/outfitPresentation.js';
import { OutfitRoleIcon, outfitImage, outfitName } from './OutfitCatalog.jsx';
import styles from './OutfitSelection.module.css';

// 총장이 없는 후보는 카탈로그의 길이 분류를 사용한다. 실측을 분석 값으로 저장하지 않는다.
const DEFAULT_CM = { top: 64, inner: 64, outer: 76, bottom: 100, dress: 115, footwear: 16 };
export function garmentDisplayLength(item, role) {
  const numeric = Number(item?.lengthCm || item?.cm || item?.totalLength || item?.measurements?.find?.((m) => m.key === 'totalLength')?.value);
  if (numeric > 0) return numeric;
  const length = String(item?.length || '');
  if (/short|mini|숏|미니/.test(length)) return role === 'bottom' ? 42 : 50;
  if (/crop|크롭/.test(length)) return role === 'bottom' ? 85 : 45;
  if (/long|롱|full/.test(length)) return ['outer', 'dress'].includes(role) ? 115 : role === 'bottom' ? 100 : 75;
  return DEFAULT_CM[role] || 64;
}

export function OutfitBoard({ analysis, product, projectId, onOpenRole, skeleton = false }) {
  const [visuals, setVisuals] = useState(null);
  const [ratios, setRatios] = useState({});
  const [maxHeight, setMaxHeight] = useState(440);
  const original = product?.colors?.flatMap((c) => c.images || []).find((im) => im.slot === 'front')?.src
    || product?.colors?.flatMap((c) => c.images || [])[0]?.src || '';
  useEffect(() => {
    setVisuals(null);
    if (!projectId && !isMockMode) return undefined;
    let canceled = false; let timer;
    const poll = async () => {
      try {
        const result = await api.getProductVisuals(projectId);
        if (canceled) return;
        setVisuals(result);
        if (result?.front?.tidyStatus !== 'processing') return;
      } catch { if (canceled) return; }
      timer = setTimeout(poll, 5000);
    };
    void poll();
    return () => { canceled = true; clearTimeout(timer); };
  }, [projectId, original]);
  useEffect(() => {
    const resize = () => setMaxHeight(Math.max(300, Math.min(718, window.innerHeight - 298)));
    resize(); window.addEventListener('resize', resize);
    return () => window.removeEventListener('resize', resize);
  }, []);
  const productSrc = selectProductBoardImage(visuals, original);
  const complementary = complementaryOutfitSlot(analysis.clothingType);
  const selected = useMemo(() => Object.fromEntries(outfitSlotsFor(analysis.clothingType).map((role) => {
    const pool = role === complementary ? analysis.matchClothing : analysis.outfitRecommendation?.candidates?.[role];
    const id = analysis.outfitSelections?.slots?.[role]?.itemId ?? (role === complementary ? pool?.find((it) => it.selected)?.id : null);
    const item = pool?.find((it) => it.id === id);
    const src = outfitImage(item);
    return [role, { ...item, slot: role, src, name: outfitName(item), cm: garmentDisplayLength(item, role), ar: ratios[src] || item?.ar || (role === 'bottom' ? .52 : role === 'footwear' ? 2.1 : .95) }];
  })), [analysis, complementary, ratios]);
  const kind = analysis.clothingType || 'top';
  const layout = buildOutfitBoardLayout({ clothingType: kind, product: { slot: 'product', src: productSrc, name: product?.name || '내 상품', cm: garmentDisplayLength(analysis, kind), ar: ratios[productSrc] || visuals?.front?.width / visuals?.front?.height || (kind === 'bottom' ? .52 : .85) }, items: { ...selected, footwear: selected.footwear?.id ? selected.footwear : null,
    bag: analysis.outfitSelections?.optional?.bag ? selected.bag : null,
    accessory: analysis.outfitSelections?.optional?.accessory ? selected.accessory : null }, maxHeight, width: 276 });
  const loaded = (event) => {
    const img = event.currentTarget; const ratio = img.naturalWidth / img.naturalHeight;
    if (ratio > 0) setRatios((prev) => prev[img.getAttribute('src')] === ratio ? prev : { ...prev, [img.getAttribute('src')]: ratio });
  };
  return <aside className={styles.preview} aria-label="코디 미리보기">
    <h3>미리보기</h3>
    <div className={styles.reference}><Info size={12} aria-hidden="true" /><b>참고용</b><span>코디 느낌만 봐 주세요. 의류컷은 별개로 만들어요</span></div>
    <div className={`${styles.board} ${skeleton ? styles.boardLoading : ''}`} aria-busy={skeleton}>
      <div className={styles.boardCanvas} style={{ width: layout.width, height: layout.height }}>
        {layout.pieces.map((piece) => {
          const item = piece.slot === 'product' ? { src: productSrc, name: product?.name || '내 상품' } : selected[piece.slot];
          const label = piece.slot === 'product' ? '내 상품' : OUTFIT_SLOT_LABELS[piece.slot];
          const unavailable = analysis.outfitRecommendation?.roleStatus?.[piece.slot] === 'unavailable';
          const content = item?.src ? <img key={item.src} src={item.src} alt={item.name} onLoad={loaded} /> : <span className={styles.ghost}><OutfitRoleIcon role={piece.slot} /><b>{label}</b></span>;
          const box = { left: piece.x, top: piece.y, width: piece.width, height: piece.height, zIndex: piece.zIndex };
          return piece.slot === 'product' || unavailable
            ? <div key={piece.slot} className={styles.piece} style={box}>{content}</div>
            : <button key={piece.slot} type="button" className={styles.piece} style={box} aria-label={`${label} 바꾸기`} onClick={() => onOpenRole(piece.slot)}>{content}</button>;
        })}
        {layout.accessories.map((piece) => <button key={piece.slot} type="button"
          className={styles.boardAccessory} style={{ left: piece.x, top: piece.y, width: piece.width, height: piece.height }}
          aria-label={`${OUTFIT_SLOT_LABELS[piece.slot]} 바꾸기`} onClick={() => onOpenRole(piece.slot)}>
          <img src={piece.src} alt={piece.name} />
        </button>)}
      </div>
    </div>
  </aside>;
}
