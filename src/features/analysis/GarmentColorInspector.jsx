import React, { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { ChevronRight } from 'lucide-react';
import { baseProductPhoto } from '@/lib/draftVisuals.js';
import { ZERO_SLIDERS } from '@/lib/garmentColorTone.js';
import { GarmentColorPanel, ToneCanvas, colorSwatch } from './GarmentColorPanel.jsx';
import outfitStyles from './OutfitSelection.module.css';
import styles from './GarmentColorPanel.module.css';

const PUSH_MIN_WIDTH = 1280;
const PANEL_W = 300;
const GAP = 12;
const EDGE = 16;
const SHIFT_MS = 280;
const reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;

/* 넓은 창은 카드 오른쪽 빈 곳이 모자란 만큼만 본문을 왼쪽으로 밀고 판을 붙인다(9차 시안).
   좁은 창은 오른쪽 서랍으로 덮고 초점을 판 안에 가둔다. */
export function useGarmentColorInspector({ garment, cardRef }) {
  const [open, setOpen] = useState(false);
  const [drawer, setDrawer] = useState(false);
  const [currentId, setCurrentId] = useState(null);
  const [position, setPosition] = useState(null);
  const rowRef = useRef(null);
  const panelRef = useRef(null);
  const opener = useRef(null);
  const shifted = useRef(null);
  const baseId = garment.colors[0]?.colorId || null;

  const unshift = useCallback(() => {
    const wizard = shifted.current;
    if (!wizard) return;
    shifted.current = null;
    wizard.style.left = '0px';
    setTimeout(() => {
      if (shifted.current === wizard) return;
      wizard.style.removeProperty('left'); wizard.style.removeProperty('position'); wizard.style.removeProperty('transition');
    }, reducedMotion() ? 0 : SHIFT_MS);
  }, []);

  const close = useCallback((returnFocus = true) => {
    if (!open) return;
    garment.flush();
    setOpen(false); setPosition(null); unshift();
    setCurrentId(null);
    if (returnFocus) {
      // 서랍이 앱을 inert로 막아 둔 채면 초점이 옮겨지지 않는다. 막기를 먼저 풀고 돌려준다.
      const root = document.getElementById('root');
      if (root) root.inert = false;
      (opener.current || rowRef.current)?.focus({ preventScroll: true });
    }
  }, [open, garment, unshift]);

  const openPanel = useCallback((from) => {
    if (open) { close(); return; }
    opener.current = from || rowRef.current;
    const isDrawer = window.innerWidth < PUSH_MIN_WIDTH;
    setDrawer(isDrawer);
    if (!isDrawer && cardRef.current && rowRef.current) {
      const card = cardRef.current.getBoundingClientRect();
      const row = rowRef.current.getBoundingClientRect();
      const viewport = document.documentElement.clientWidth;
      const shift = Math.max(0, Math.min(PANEL_W + GAP + EDGE - (viewport - card.right), card.left - EDGE));
      const wizard = cardRef.current.closest('.wizard');
      if (wizard && shift > 0) {
        wizard.style.position = 'relative';
        wizard.style.transition = reducedMotion() ? 'none' : `left ${SHIFT_MS}ms cubic-bezier(.2, 0, 0, 1)`;
        wizard.style.left = `${-shift}px`;
        shifted.current = wizard;
      }
      setPosition({ top: row.top + window.scrollY - 8, left: card.right + window.scrollX - shift + GAP });
    }
    setCurrentId(baseId);
    setOpen(true);
    garment.prepare(baseId);
  }, [open, close, cardRef, baseId, garment]);

  useEffect(() => { if (open) panelRef.current?.focus({ preventScroll: true }); }, [open]);
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (event) => {
      if (event.key === 'Escape') { close(); return; }
      if (event.key !== 'Tab' || !drawer || !panelRef.current) return;
      const focusable = [...panelRef.current.querySelectorAll('button:not(:disabled):not([tabindex="-1"]), input:not(:disabled), [tabindex="0"]')].filter((node) => node.offsetParent !== null);
      if (!focusable.length) return;
      const first = focusable[0], last = focusable[focusable.length - 1];
      if (event.shiftKey && (document.activeElement === first || document.activeElement === panelRef.current)) { last.focus(); event.preventDefault(); }
      else if (!event.shiftKey && document.activeElement === last) { first.focus(); event.preventDefault(); }
    };
    const onResize = () => close(false);
    document.addEventListener('keydown', onKey);
    window.addEventListener('resize', onResize);
    return () => { document.removeEventListener('keydown', onKey); window.removeEventListener('resize', onResize); };
  }, [open, drawer, close]);
  useEffect(() => () => unshift(), [unshift]);
  // 좁은 창 서랍은 모달이다. 뒤의 앱 전체(상단 메뉴 포함)를 inert로 두고 닫을 때 푼다. 판은 body 포털이라 영향받지 않는다.
  useEffect(() => {
    if (!open || !drawer) return undefined;
    const root = document.getElementById('root');
    if (!root) return undefined;
    root.inert = true;
    return () => { root.inert = false; };
  }, [open, drawer]);

  const current = garment.colors.find((color) => color.colorId === (currentId || baseId)) || null;
  const panel = open && garment.colors.length > 0 ? createPortal(<>
    {drawer && <button type="button" className={styles.scrim} aria-label="색 맞추기 닫기" onClick={() => close()} />}
    <GarmentColorPanel panelRef={panelRef} colors={garment.colors} currentId={current?.colorId} onSelect={(colorId) => { setCurrentId(colorId); garment.prepare(colorId); }} onRetry={garment.prepare} sources={garment.sources}
      onSliders={garment.setSliders} onCommit={garment.flush} onDone={() => close()} drawer={drawer}
      style={!drawer && position ? { top: position.top, left: position.left } : undefined} />
  </>, document.body) : null;

  /* 보드의 내 상품 그림: 판이 열려 있으면 고른 색상, 닫혀 있으면 기준 색상을 맞춘 색으로 보여 준다. */
  const shown = open ? current : garment.colors[0];
  const shownSource = shown ? garment.sources[shown.colorId] : null;
  const board = garment.enabled && garment.colors.length > 0 ? {
    open, onOpen: openPanel,
    source: shownSource?.preview && (open || shown.colorId !== baseId || !Object.values(shown.sliders || {}).every((value) => !value)) ? shownSource : null,
    sliders: shown?.sliders || ZERO_SLIDERS,
  } : null;

  return { open, close, openPanel, rowRef, panel, board };
}

export function GarmentColorRow({ garment, inspector, product }) {
  const base = garment.colors[0];
  if (!garment.enabled || !base) return null;
  const source = garment.sources[base.colorId];
  const swatch = colorSwatch(base, source);
  const photo = baseProductPhoto(product)?.src;
  return <section className={outfitStyles.row} aria-label="내 상품 색">
    <div className={outfitStyles.rowHeading}>
      <button ref={inspector.rowRef} type="button" className={outfitStyles.rowButton} aria-expanded={inspector.open} aria-controls="garment-color-panel"
        onClick={(event) => inspector.openPanel(event.currentTarget)}>
        <span className={outfitStyles.thumb}>
          {source?.preview ? <ToneCanvas source={source} sliders={base.sliders} className={styles.rowCanvas} width={168} height={196} />
            : photo ? <img src={photo} alt="" width="96" height="120" /> : null}
        </span>
        <span className={outfitStyles.rowText}>
          <span className={outfitStyles.role}>내 상품</span>
          <span className={`${outfitStyles.value} ${styles.rowValue}`}>{swatch && <span className={styles.dot} style={{ '--swatch': swatch }} />}{base.name}</span>
          <span className={`${outfitStyles.change} ${styles.rowChange}`}>색 확인<ChevronRight size={13} /></span>
        </span>
      </button>
    </div>
  </section>;
}
