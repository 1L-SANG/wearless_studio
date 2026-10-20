import { baseProductPhoto, startVisualPolling } from '@/lib/draftVisuals.js';
import React, { useEffect, useMemo, useState } from 'react';
import { Info } from 'lucide-react';
import { api, isMockMode } from '@/lib/api/index.js';
import { OUTFIT_SLOT_LABELS, complementaryOutfitSlot, outfitSlotsFor, selectedOutfitIds } from '@/lib/outfitSelections.js';
import { buildOutfitBoardLayout, selectProductBoardImage, shoePairImageLayers } from '@/lib/outfitPresentation.js';
import { croppedImageStyle, getOutfitImageBounds, imageBoundsAspectRatio, loadOutfitImageBounds } from '@/lib/outfitImageBounds.js';
import { OutfitRoleIcon, outfitImage, outfitName } from './OutfitCatalog.jsx';
import styles from './OutfitSelection.module.css';

// 총장이 없는 후보는 카탈로그의 길이 분류를 사용한다. 실측을 분석 값으로 저장하지 않는다.
const DEFAULT_CM = { top: 64, inner: 64, outer: 76, bottom: 100, dress: 115 };
// Retain the ordinary display ratio too, so cached failures keep their layout on remount.
const originalImageRatios = new Map();
const LENGTH_KEYS = {
  mini: 'mini', 미니: 'mini', short: 'short', 숏: 'short',
  knee: 'knee', 무릎: 'knee', midi: 'midi', 미디: 'midi', maxi: 'maxi', 맥시: 'maxi',
  full: 'full', 풀: 'full', long: 'full', 롱: 'full', semi_long: 'full', 세미롱: 'full',
  cropped: 'cropped', crop: 'cropped', 크롭: 'cropped', semi_crop: 'cropped', 세미크롭: 'cropped',
  crop_short: 'short', '크롭·숏': 'short',
  regular: 'regular', basic: 'regular', 레귤러: 'regular', 기본: 'regular',
};
const TOP_LENGTH_CM = { cropped: 45, regular: 64, mini: 50, short: 50, full: 75 };
const DISPLAY_LENGTH_CM = {
  top: TOP_LENGTH_CM, inner: TOP_LENGTH_CM,
  bottom: { mini: 42, short: 48, knee: 56, midi: 72, maxi: 90, full: 100, cropped: 85 },
  outer: { mini: 50, short: 50, cropped: 45, full: 115 },
  dress: { mini: 50, short: 50, cropped: 45, full: 115 },
};
export function garmentDisplayLength(item, role) {
  const numeric = Number(item?.lengthCm || item?.cm || item?.totalLength || item?.measurements?.find?.((m) => m.key === 'totalLength')?.value);
  if (Number.isFinite(numeric) && numeric > 0) return numeric;
  const length = LENGTH_KEYS[String(item?.length || '').trim().toLowerCase()];
  const cm = DISPLAY_LENGTH_CM[role]?.[length];
  if (cm) return cm;
  return DEFAULT_CM[role] || 64;
}

export function OutfitBoard({ draftVisuals, analysis, product, projectId, onOpenRole, skeleton = false }) {
  const [visuals, setVisuals] = useState(null);
  const [ratios, setRatios] = useState({});
  const [measurements, setMeasurements] = useState({});
  const [maxHeight, setMaxHeight] = useState(440);
  const original = baseProductPhoto(product)?.src || '';
  const sourceKey = JSON.stringify([projectId, original]);
  useEffect(() => {
    setVisuals(null);
    if (!projectId && !isMockMode) return undefined;
    return startVisualPolling({ query: () => api.getProductVisuals(projectId), onResult: (result) => setVisuals({ sourceKey, result }),
      onStop: () => setVisuals(null) });
  }, [projectId, original]);
  const shownVisuals = !projectId && draftVisuals ? draftVisuals : visuals?.sourceKey === sourceKey ? visuals.result : null;
  useEffect(() => {
    const resize = () => setMaxHeight(Math.max(300, Math.min(718, window.innerHeight - 298)));
    resize(); window.addEventListener('resize', resize);
    return () => window.removeEventListener('resize', resize);
  }, []);
  const measurementFor = src => Object.hasOwn(measurements, src) ? measurements[src] : getOutfitImageBounds(src);
  const originalRatio = src => ratios[src] || originalImageRatios.get(src);
  const productSrc = selectProductBoardImage(shownVisuals, original);
  const complementary = complementaryOutfitSlot(analysis.clothingType);
  const selected = useMemo(() => Object.fromEntries(outfitSlotsFor(analysis.clothingType).map((role) => {
    const pool = role === complementary ? analysis.matchClothing : analysis.outfitRecommendation?.candidates?.[role];
    const id = analysis.outfitSelections?.slots?.[role]?.itemId ?? (role === complementary ? pool?.find((it) => it.selected)?.id : null);
    const item = pool?.find((it) => it.id === id);
    const src = outfitImage(item);
    const measurement = measurementFor(src);
    const fallbackAr = role === 'footwear' && measurement === undefined ? 2.1
      : (measurement !== undefined && originalRatio(src)) || item?.ar || (role === 'bottom' ? .52 : role === 'footwear' ? 2.1 : .95);
    return [role, { ...item, slot: role, src, name: outfitName(item),
      cm: role === 'footwear' ? undefined : garmentDisplayLength(item, role),
      ar: imageBoundsAspectRatio(measurement, fallbackAr) }];
  })), [analysis, complementary, ratios, measurements]);
  const optionalItem = role => {
    if (!analysis.outfitSelections?.optional?.[role]) return null;
    if (analysis.outfitRecommendation?.props?.[role]?.status === 'pending') return { slot: role, pending: true };
    if (role === 'bag') return selected.bag?.id ? selected.bag : null;
    return selectedOutfitIds(analysis.outfitSelections, role).map(id => {
      const item = analysis.outfitRecommendation?.candidates?.[role]?.find(candidate => candidate.id === id);
      return item ? { ...item, slot: role, src: outfitImage(item), name: outfitName(item) } : null;
    }).filter(Boolean);
  };
  const kind = analysis.clothingType || 'top';
  const productMeasurement = measurementFor(productSrc);
  const layout = buildOutfitBoardLayout({ clothingType: kind, product: { slot: 'product', src: productSrc, name: product?.name || '내 상품', cm: garmentDisplayLength(analysis, kind), ar: imageBoundsAspectRatio(productMeasurement, (productMeasurement !== undefined && originalRatio(productSrc)) || shownVisuals?.front?.width / shownVisuals?.front?.height || (kind === 'bottom' ? .52 : .85)) }, items: { ...selected, footwear: selected.footwear?.id ? selected.footwear : null,
    bag: optionalItem('bag'), accessory: optionalItem('accessory') }, maxHeight, width: 276 });
  const measurementSources = JSON.stringify([...new Set(layout.pieces.map(piece => piece.src).filter(Boolean))]);
  useEffect(() => {
    let active = true;
    for (const src of JSON.parse(measurementSources)) loadOutfitImageBounds(src).then(result => {
      if (active) setMeasurements(previous => previous[src] === result ? previous : { ...previous, [src]: result });
    });
    return () => { active = false; };
  }, [measurementSources]);
  const loaded = (event) => {
    const img = event.currentTarget; const ratio = img.naturalWidth / img.naturalHeight;
    if (ratio > 0) {
      const src = img.getAttribute('src');
      originalImageRatios.set(src, ratio);
      setRatios((prev) => prev[src] === ratio ? prev : { ...prev, [src]: ratio });
    }
  };
  const image = (item, layer, rear = false) => {
    const bounds = measurementFor(item.src)?.bounds;
    return <span key={rear ? 'rear' : 'front'} className={`${styles.pieceImage} ${layer ? styles.shoeCopy : ''}`} style={layer}>
      <img key={item.src} src={item.src} alt={rear ? '' : item.name} aria-hidden={rear || undefined} onLoad={loaded}
        className={bounds ? styles.croppedImage : undefined} style={croppedImageStyle(bounds)} />
    </span>;
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
          const preparing = piece.slot === 'product' && !projectId && draftVisuals?.front?.tidyStatus === 'processing';
          const content = preparing ? <span className={styles.ghost} role="status"><b>준비 중</b></span> : item?.src
            ? piece.slot === 'footwear' ? shoePairImageLayers().map((layer, index) => image(item, layer, index === 0)) : image(item)
            : <span className={styles.ghost}><OutfitRoleIcon role={piece.slot} /><b>{label}</b></span>;
          const box = { left: piece.x, top: piece.y, width: piece.width, height: piece.height, zIndex: piece.zIndex,
            visibility: !preparing && item?.src && measurementFor(item.src) === undefined ? 'hidden' : undefined };
          return piece.slot === 'product' || unavailable
            ? <div key={piece.slot} className={styles.piece} style={box}>{content}</div>
            : <button key={piece.slot} type="button" className={styles.piece} style={box} aria-label={`${label} 바꾸기`} onClick={() => onOpenRole(piece.slot)}>{content}</button>;
        })}
        {layout.accessories.map((piece) => <button key={`${piece.slot}-${piece.id || 'pending'}`} type="button"
          className={`${styles.boardAccessory} ${piece.pending ? styles.boardAccessoryPending : ''}`} aria-busy={!!piece.pending} style={{ left: piece.x, top: piece.y, width: piece.width, height: piece.height }}
          aria-label={`${OUTFIT_SLOT_LABELS[piece.slot]} 바꾸기`} onClick={() => onOpenRole(piece.slot)}>
          {!piece.pending && <img src={piece.src} alt={piece.name} />}
        </button>)}
      </div>
    </div>
  </aside>;
}
