import React, { useEffect, useId, useRef, useState } from 'react';
import { pageTemplatesCatalog } from '../../lib/pageTemplates/catalog.js';
import { baseColorOf, resolvePageTokens } from '../../lib/pageTemplates/tokens.js';
import { observePreviewMotion } from '../../lib/pageTemplates/previewMotion.js';
import styles from './PageTemplatePicker.module.css';

export function TemplatePreviewImage({ template, full = false, hovered = false, focused = false }) {
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(!template.preview.src);
  const viewportRef = useRef(null);
  const imageRef = useRef(null);
  const available = loaded && !failed;
  useEffect(() => {
    if (!available || full || !viewportRef.current || !imageRef.current) return undefined;
    return observePreviewMotion({
      viewport: viewportRef.current, image: imageRef.current, hovered, focused,
      media: query => window.matchMedia(query),
      makeObserver: typeof ResizeObserver === 'undefined' ? null : callback => new ResizeObserver(callback),
    });
  }, [available, full, hovered, focused]);
  return <span ref={viewportRef} className={`${styles.preview} ${full ? styles.fullPreview : ''} ${available ? styles.hasLongImage : styles.fallback}`}
    aria-hidden={!full || undefined}>
    {!failed && <img ref={imageRef} className={`${styles.longImage} ${loaded ? '' : styles.loading}`}
      src={template.preview.src} alt={full ? `${template.name} 상세페이지 견본` : ''} loading={full ? 'eager' : 'lazy'}
      onLoad={() => setLoaded(true)} onError={() => { setFailed(true); setLoaded(false); }} />}
    {!available && <span className={styles.fallbackSheet}>
      <img src={template.preview.fallbackSrc} alt={full ? `${template.name} 견본 사진` : ''} loading={full ? 'eager' : 'lazy'} />
      <span className={styles.sampleTitle}><small>Daily collection</small><b>오늘의 옷</b></span>
    </span>}
  </span>;
}

export function TemplatePreviewDialog({ template, onClose, returnFocusTo }) {
  const dialogRef = useRef(null);
  const closeButtonRef = useRef(null);
  const titleId = useId();
  useEffect(() => {
    const dialog = dialogRef.current;
    const previous = returnFocusTo || document.activeElement;
    dialog.showModal();
    closeButtonRef.current.focus({ preventScroll: true });
    return () => { dialog.close(); if (previous?.isConnected) previous.focus({ preventScroll: true }); };
  }, [returnFocusTo]);
  return <dialog ref={dialogRef} className={`${styles.previewDialog} ${styles[template.id] || ''}`}
    role="dialog" aria-modal="true" aria-labelledby={titleId} onCancel={event => { event.preventDefault(); onClose(); }}
    onClick={event => { if (event.target === event.currentTarget) onClose(); }}>
    <div className={styles.dialogHead}><h2 id={titleId}>{template.name} 전체 보기</h2>
      <div className="modal-actions"><button ref={closeButtonRef} type="button" className="btn" autoFocus onClick={onClose}>닫기</button></div>
    </div>
    <div className={styles.dialogScroll} tabIndex={0} aria-label={`${template.name} 견본 상세페이지`}>
      <TemplatePreviewImage template={template} full />
    </div>
  </dialog>;
}

export function TemplateOption({ template, value, onChange, onPreview }) {
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  return <article className={`${styles.card} ${styles[template.id] || ''}`}
    onPointerEnter={event => { if (event.pointerType === 'mouse') setHovered(true); }} onPointerLeave={() => setHovered(false)}
    onFocusCapture={event => setFocused(event.target.matches(':focus-visible'))}
    onBlurCapture={event => { if (!event.currentTarget.contains(event.relatedTarget)) setFocused(false); }}>
    <label className={styles.choice}>
      <input type="radio" name="page-template" value={template.id} checked={value === template.id}
        onChange={() => onChange(template.id)} />
      <TemplatePreviewImage template={template} hovered={hovered} focused={focused} />
      <span className={styles.label}><b>{template.name}</b><small>{template.preview.description}</small></span>
    </label>
    <div className={`modal-actions ${styles.previewActions}`}>
      <button type="button" className={`btn ${styles.previewButton}`} aria-label={`${template.name} 전체 보기`}
        onClick={event => onPreview(template.id, event.currentTarget)}>전체 보기</button>
    </div>
  </article>;
}

export function PageColorModePicker({ value = 'mono', templateId, product, onChange, disabled = false }) {
  const base = baseColorOf(product);
  const label = base.name || pageTemplatesCatalog.swatchLabels[base.swatchId];
  return <fieldset className={`${styles.picker} ${styles.colorPicker}`} disabled={disabled}>
    <legend>색</legend>
    <p className={styles.note}>옷 색에 맞추면 라벨, 제목, 옅은 바탕이 바뀌어요.</p>
    <div className={styles.colorCards}>{[
      ['mono', '모노톤', '기본이에요'], ['point', '옷 색에 맞춤', label ? `대표 색 ${label}` : '옷 대표 색에 맞춰요'],
    ].map(([mode, title, description]) => {
      const { colors } = resolvePageTokens({ templateId, colorMode: mode }, base.swatchId);
      return <label key={mode} className={`${styles.card} ${styles.colorChoice}`}>
        <input type="radio" name="page-color-mode" value={mode} checked={value === mode} onChange={() => onChange?.(mode)} />
        <svg className={styles.colorPreview} viewBox="0 0 240 112" aria-hidden="true" focusable="false">
          <rect width="240" height="112" fill={colors.canvas} />
          <rect x="20" y="16" width="50" height="5" rx="2" fill={colors.accentInk} />
          <rect x="20" y="31" width="116" height="8" rx="3" fill={colors.ink} />
          <rect x="20" y="55" width="200" height="38" rx="8" fill={colors.accentBg} />
          <path d="m32 72 4 4 7-8" fill="none" stroke={colors.accentInk} strokeWidth="2" />
          <rect x="152" y="72" width="56" height="4" rx="2" fill={colors.muted} />
          <rect x="20" y="104" width="200" height="1" fill={colors.accentLine} />
        </svg>
        <span className={styles.label}><b>{title}</b><small>{description}</small></span>
      </label>;
    })}</div>
  </fieldset>;
}

export function PageTemplatePicker({ value, onChange, colorMode = 'mono', onColorModeChange, product, disabled = false }) {
  const [previewId, setPreviewId] = useState(null);
  const previewTriggerRef = useRef(null);
  const templates = pageTemplatesCatalog.templates.filter(template => template.selectable);
  const preview = templates.find(template => template.id === previewId);
  const showPreview = (id, trigger) => { previewTriggerRef.current = trigger; setPreviewId(id); };
  return <>
    <fieldset className={styles.picker} disabled={disabled}>
      <legend>상세페이지 템플릿</legend>
      <p className={styles.note}>견본 사진이에요. 내 상품 사진으로 만들어져요.</p>
      <div className={styles.cards}>{templates.map(template => <TemplateOption key={template.id}
        template={template} value={value} onChange={onChange} onPreview={showPreview} />)}</div>
    </fieldset>
    <PageColorModePicker value={colorMode} templateId={value} product={product} onChange={onColorModeChange} disabled={disabled} />
    {preview && <TemplatePreviewDialog template={preview} returnFocusTo={previewTriggerRef.current} onClose={() => setPreviewId(null)} />}
  </>;
}
