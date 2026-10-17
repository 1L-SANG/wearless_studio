import React, { useEffect, useRef, useState } from 'react';
import { flushSync } from 'react-dom';
import { ArrowRight, ChevronDown, ChevronUp } from 'lucide-react';
import { claimOutfitHint } from '@/lib/outfitPresentation.js';
import { WizardCTA } from '@/features/shell/shell.jsx';
import styles from './AnalysisStepFlow.module.css';

const easeInOutCubic = (t) => t < .5 ? 4 * t ** 3 : 1 - (-2 * t + 2) ** 3 / 2;

export function AnalysisStepFlow({ inline, analysis, product, children, outfit, cta, beforeEnter }) {
  const [stage, setStage] = useState('analysis');
  const [folded, setFolded] = useState(false);
  const [hint, setHint] = useState(false);
  const [entering, setEntering] = useState(false);
  const [error, setError] = useState('');
  const [inputOpen, setInputOpen] = useState(false);
  const rootRef = useRef(null);
  const analysisRef = useRef(null);
  const headRef = useRef(null);
  const glideRef = useRef(null);
  const aliveRef = useRef(true);
  useEffect(() => { aliveRef.current = true; return () => { aliveRef.current = false; glideRef.current?.cancel(); }; }, []);
  useEffect(() => {
    if (!hint) return undefined;
    const dismiss = () => setHint(false);
    const events = ['pointerdown', 'keydown', 'wheel', 'touchstart', 'scroll'];
    // 진입 좌표 보정의 마지막 scroll 이벤트는 안내를 지우지 않는다.
    const timer = setTimeout(() => events.forEach((type) => window.addEventListener(type, dismiss, { capture: true, passive: true })), 80);
    return () => { clearTimeout(timer); events.forEach((type) => window.removeEventListener(type, dismiss, true)); };
  }, [hint]);
  const showHint = () => {
    try { setHint(claimOutfitHint(window.localStorage)); } catch { /* 저장소 자체의 getter도 실패할 수 있다. */ }
  };
  const enter = async () => {
    if (entering) return;
    setEntering(true); setError('');
    try { await beforeEnter?.(); } catch { if (aliveRef.current) { setError('분석 정보를 저장하지 못했어요. 다시 눌러 주세요.'); setEntering(false); } return; }
    if (!aliveRef.current) return;
    const root = rootRef.current?.closest('.wizard') || rootRef.current;
    const initialY = window.scrollY;
    flushSync(() => { setStage('entering'); setFolded(false); });
    const targetTop = () => Math.max(0, window.scrollY + headRef.current.getBoundingClientRect().top - 76);
    const done = () => {
      setStage('outfit'); setEntering(false); showHint();
      headRef.current?.focus({ preventScroll: true });
    };
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      flushSync(() => { setFolded(true); setStage('outfit'); });
      window.scrollTo(0, targetTop()); done(); return;
    }
    let from = initialY; let to = targetTop(); let swapped = false; let raf = 0; let startedAt = null; let lastSet;
    const oldTransform = root.style.transform; const oldWillChange = root.style.willChange;
    const oldScrollBehavior = document.documentElement.style.scrollBehavior;
    document.documentElement.style.scrollBehavior = 'auto';
    document.documentElement.setAttribute('data-outfit-gliding', 'true');
    if (to - from > window.innerHeight * 1.5) from = to - window.innerHeight;
    const distance = to - from;
    root.style.willChange = 'transform';
    window.scrollTo(0, to); lastSet = window.scrollY;
    root.style.transform = `translateY(${to - from}px)`;
    const events = ['wheel', 'touchstart', 'touchmove', 'pointerdown', 'keydown'];
    const cleanup = () => {
      cancelAnimationFrame(raf); events.forEach((event) => window.removeEventListener(event, finish)); window.removeEventListener('scroll', onScroll);
      root.style.transform = oldTransform; root.style.willChange = oldWillChange;
      document.documentElement.style.scrollBehavior = oldScrollBehavior;
      document.documentElement.removeAttribute('data-outfit-gliding'); glideRef.current = null;
    };
    const swap = () => {
      if (swapped) return; swapped = true;
      const before = headRef.current.getBoundingClientRect().top; const y = window.scrollY;
      flushSync(() => setFolded(true));
      window.scrollBy(0, headRef.current.getBoundingClientRect().top - before);
      const shift = window.scrollY - y; from += shift; to += shift; lastSet = window.scrollY;
    };
    const finish = () => { swap(); window.scrollTo(0, to); cleanup(); done(); };
    const onScroll = () => { if (Math.abs(window.scrollY - lastSet) > 1.5) finish(); };
    const tick = (now) => {
      startedAt ??= now;
      const progress = Math.min(1, (now - startedAt) / 800);
      const y = from + distance * easeInOutCubic(progress);
      root.style.transform = `translateY(${(to - y).toFixed(2)}px)`;
      const menuBottom = document.querySelector('.topnav')?.getBoundingClientRect().bottom || 60;
      if (!swapped && analysisRef.current.getBoundingClientRect().bottom <= menuBottom + .5) swap();
      if (progress === 1) { finish(); return; }
      raf = requestAnimationFrame(tick);
    };
    glideRef.current = { cancel: cleanup };
    events.forEach((event) => window.addEventListener(event, finish, { passive: true })); window.addEventListener('scroll', onScroll, { passive: true });
    raf = requestAnimationFrame(tick);
  };
  const editAnalysis = () => {
    setStage('analysis'); setFolded(false); setHint(false);
    requestAnimationFrame(() => analysisRef.current?.scrollIntoView({ block: 'start', behavior: 'instant' }));
  };
  const photos = product?.colors?.flatMap((color) => color.images || []) || [];
  const info = [analysis.customCategory || analysis.subCategory, ...(analysis.materials || []).map((material) => material.name || material.material).filter(Boolean)].filter(Boolean).slice(0, 3).join(' · ');
  return <div className={styles.flow} ref={rootRef} data-stage={stage} data-folded={folded}>
    {!inline && <div className={`${styles.summary} surface`}><div className={styles.summaryRow}>
      <div className={styles.photoSlot}>{photos.slice(0, 3).map((photo) => <img key={photo.id || photo.src} src={photo.src} alt="상품 사진" />)}</div>
      <div className={styles.summaryText}><h2>{product?.name || '상품 이미지'}</h2><p>이미지 {photos.length}장 · 색상 {product?.colors?.length || 1}</p></div>
      <button type="button" className={styles.summaryToggle} aria-expanded={inputOpen} onClick={() => setInputOpen((value) => !value)}>{inputOpen ? '접기' : '펼치기'}{inputOpen ? <ChevronUp size={15} /> : <ChevronDown size={15} />}</button>
    </div>{inputOpen && <div className={styles.photos}>{photos.map((photo) => <img key={photo.id || photo.src} src={photo.src} alt="상품 사진" />)}</div>}</div>}
    {folded && <div className={`${styles.summary} surface`}><div className={styles.summaryRow}>
      <div className={styles.photoSlot}><div className={styles.brandTile}><img src="/assets/brand/logo.svg" alt="" /><img src="/assets/brand/wordmark.png" alt="Wearless" /></div></div>
      <div className={styles.summaryText}><h2>AI가 분석한 정보</h2><p>{info || '종류 · 소재 · 실측 · 강조 특징'}</p></div>
      <button type="button" className={styles.summaryToggle} onClick={editAnalysis}>펼치기<ChevronDown size={15} /></button>
    </div></div>}
    <div ref={analysisRef} className={styles.analysis} hidden={folded}>
      <div className="af-inline-head"><div><div className="af-head-title">AI가 분석한 정보예요</div><div className={styles.subtitle}>틀린 부분이 있으면 직접 수정해주세요.</div></div></div>
      <div className="af-body af-cards merged-card">{children}</div>
      <WizardCTA className="wizard-cta-analysis"><button type="button" className="btn btn-primary btn-lg" disabled={entering} onClick={enter}>코디 고르기<ArrowRight size={18} /></button></WizardCTA>
      {error && <p role="alert" className={styles.error}>{error}</p>}
    </div>
    {stage !== 'analysis' && <section className={styles.second}>
      <header className={styles.outfitHead} ref={headRef} tabIndex={-1}><h2>AI가 고른 코디예요</h2><p>고칠 게 없으면 바로 확정해요.</p>{hint && <span className={styles.hint} role="status">줄을 누르면 바꿀 수 있어요</span>}</header>
      {outfit}
      <WizardCTA className={`wizard-cta-analysis ${styles.confirm}`}>{cta}</WizardCTA>
    </section>}
  </div>;
}

export function OutfitCreditLabel({ cost, label }) {
  const position = label.indexOf(String(cost));
  return <span className={styles.credit} aria-live="polite">{label.slice(0, position)}<span className={styles.creditAmount} key={cost}>{cost}</span>{label.slice(position + String(cost).length)}</span>;
}
