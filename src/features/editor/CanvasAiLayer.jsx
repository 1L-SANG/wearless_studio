import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { CREDIT_COSTS, editorImageCreditCost } from '../../lib/limits.js';
import { canvasAiActionCreditCost } from './canvasAiActions.js';
import { calculateCanvasAiPlacement } from './canvasAiPlacement.js';
import { canvasAiProgressSize, canvasAiProgressVisible } from './canvasAiApply.js';

export function CanvasAiClosureGlyph({ value = 'open' }) {
  const index = ['closed', 'openToChest', 'partial', 'openToWaist', 'open'].indexOf(value);
  const i = Math.max(0, index);
  const stop = [6.4, 9.8, 13.2, 16.6, 18.6][i];
  return <svg className="canvas-ai-closure-glyph" viewBox="0 0 24 24" aria-hidden="true">
    <path d="M8 4.2 L12 5.8 L16 4.2 L19.3 5.6 L21.3 18.8 L18.9 19.2 L18.2 10.6 L18.2 20.8 L5.8 20.8 L5.8 10.6 L5.1 19.2 L2.7 18.8 L4.7 5.6 Z" fill="currentColor" />
    {i > 0 && <path d={i === 4 ? `M9.6 5 L14.4 5 L13.5 ${stop} L10.5 ${stop} Z` : `M9.6 5 L14.4 5 L12 ${stop} Z`} className="canvas-ai-glyph-paper" />}
    {i < 4 && <path d={`M12 ${stop} L12 20.8`} className="canvas-ai-glyph-line" />}
    <circle cx="12" cy={stop + .9} r="1.5" className="canvas-ai-glyph-paper" />
  </svg>;
}

function CanvasAiIcon({ name }) {
  return <svg className="canvas-ai-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {name === 'similar' || name === 'reshoot' ? <><rect x="3" y="7" width="13" height="14" rx="2" /><circle cx="7.5" cy="11.5" r="1.3" /><path d="m3.5 19 3.3-3.3a1.4 1.4 0 0 1 2 0L13 19.8M8 7V5a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-2" /></>
      : name === 'direction' ? <path d="m3 7 5 5-5 5V7m18 0-5 5 5 5V7M12 20v2M12 14v2M12 8v2M12 2v2" />
        : name === 'detail' ? <><circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3M11 8v6M8 11h6" /></>
          : name === 'upload' ? <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4m14-7-5-5-5 5M12 3v12" />
            : <><path d="M16 5h6M19 2v6M21 11.5V19a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h7.5m8.5 12-3.09-3.09a2 2 0 0 0-2.82 0L6 21" /><circle cx="9" cy="9" r="2" /></>}
  </svg>;
}
const Check = () => <svg className="canvas-ai-check" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><path d="M2.2 6.3 4.8 8.8 9.8 3.4" /></svg>;
const Chevron = () => <svg className="canvas-ai-chevron" viewBox="0 0 10 10" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><path d="M2 3.5 5 6.5 8 3.5" /></svg>;

function ProductColor({ hex, className = '' }) {
  const ref = useRef(null);
  useLayoutEffect(() => {
    ref.current?.style.setProperty('--canvas-ai-color', hex || 'var(--fg-3)');
    const n = parseInt(String(hex).replace('#', ''), 16);
    const dark = .299 * (n >> 16) + .587 * ((n >> 8) & 255) + .114 * (n & 255) < 150;
    ref.current?.style.setProperty('--canvas-ai-color-ink', dark ? 'var(--bg-1)' : 'var(--fg-1)');
    ref.current?.parentElement?.style.setProperty('--canvas-ai-color-ink', dark ? 'var(--bg-1)' : 'var(--fg-1)');
  }, [hex]);
  return <i ref={ref} className={`canvas-ai-product-color ${className}`} />;
}

export function CanvasAiProgress({ job, onCancel, onDismiss, slot = false }) {
  const progressRef = useRef(null);
  useLayoutEffect(() => {
    const node = progressRef.current;
    if (!node) return;
    const measure = () => {
      const rect = node.getBoundingClientRect();
      const size = canvasAiProgressSize(rect);
      node.classList.toggle('is-compact', rect.height < 120);
      node.classList.toggle('is-tiny', rect.height < 60);
      if (slot) {
        node.style.setProperty('--canvas-ai-progress-width', `${size.width}px`);
        node.style.setProperty('--canvas-ai-progress-height', `${size.height}px`);
      }
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, [slot, job?.status, job?.dismissed]);
  if (!canvasAiProgressVisible(job)) return null;
  const failed = job.status === 'failed';
  return <div ref={progressRef} className={`canvas-ai-progress${slot ? ' canvas-ai-slot-progress' : ''} is-${job.status}`}
    role="status" aria-live="polite" onPointerDown={(e) => e.stopPropagation()} onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
    {job.contextFill?.label && <span className="canvas-ai-progress-label">{job.contextFill.label}</span>}
    <div className="canvas-ai-progress-center">
      {failed ? <><strong>만들지 못했어요</strong><p>{job.error?.message || '잠시 뒤 다시 눌러 주세요.'}</p>
        {!job.error?.submissionUnknown && <small>크레딧은 나가지 않았어요</small>}</>
        : <><span className="canvas-ai-logo" aria-hidden="true"><i className="canvas-ai-logo-base" /><i className="canvas-ai-logo-fill" /></span>
          {job.status === 'cancel' && <button type="button" className="canvas-ai-cancel" onClick={onCancel}>취소</button>}</>}
    </div>
    {job.status === 'cancel' && <span className="canvas-ai-timer" aria-hidden="true"><i /></span>}
    {job.status === 'busy' && <small className="canvas-ai-progress-hint">다른 사진을 골라도 돼요</small>}
    {failed && <button type="button" className="canvas-ai-close" aria-label="결과 자리 닫기" onClick={onDismiss}>×</button>}
  </div>;
}

export function CanvasAiLayer({ block, element, scale, wrapRef, menu, loading, assetError,
  job, visible, creditCosts = { editorImage: CREDIT_COSTS.editorImage }, onAction, onCancel, onDismiss, onUpload,
  onReplace, onAdd, replaceReason }) {
  const layerRef = useRef(null);
  const menuRef = useRef(null);
  const orbRef = useRef(null);
  const triggerMeasureRef = useRef(null);
  const labelMeasureRef = useRef(null);
  const [open, setOpen] = useState(false);
  const [branchId, setBranchId] = useState(null);
  const [hover, setHover] = useState(null);
  const [fixed, setFixed] = useState(false);
  const [menuUp, setMenuUp] = useState(false);
  const [placed, setPlaced] = useState(false);
  const [showLabel, setShowLabel] = useState(false);
  const busy = job?.status === 'busy' || job?.status === 'cancel';
  const result = job?.result?.data || job?.response?.data || job?.result;
  const showResult = job && !job.isSlot && !job.dismissed;
  const branch = menu.branches.find((item) => item.id === branchId);
  const close = (focus = false) => {
    setOpen(false); setBranchId(null); setHover(focus ? { description: '이 사진으로 새 컷을 생성해요', orb: true } : null);
    if (focus) orbRef.current?.focus();
  };
  useEffect(() => { close(); }, [element.id, element.src]);
  useEffect(() => { if (!visible) close(); }, [visible]);
  useEffect(() => { if (job?.status === 'cancel') close(); }, [job?.status]);
  useEffect(() => {
    if (!open) return;
    const outside = (e) => { if (!layerRef.current?.contains(e.target)) close(); };
    const key = (e) => {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(true); }
    };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', key, true);
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', key, true); };
  }, [open]);

  useLayoutEffect(() => {
    if (!visible) return;
    const layer = layerRef.current;
    const wrap = wrapRef.current;
    const labelNode = labelMeasureRef.current;
    const triggerNode = triggerMeasureRef.current;
    const blockNode = layer?.closest('.canvas-block');
    const elementNode = [...(blockNode?.querySelectorAll('[data-elid]') || [])]
      .find((node) => node.dataset.elid === element.id);
    if (!layer || !wrap || !blockNode || !elementNode || !labelNode || !triggerNode) return;
    let frame;
    let active = true;
    const measure = () => {
      const branchWidth = Math.max(0, ...[...layer.querySelectorAll('.canvas-ai-measure > .canvas-ai-branch')]
        .map((node) => node.getBoundingClientRect().width));
      layer.style.setProperty('--canvas-ai-branch-width', `${branchWidth}px`);
      const rect = elementNode.getBoundingClientRect();
      const viewportRect = wrap.getBoundingClientRect();
      layer.style.setProperty('--canvas-ai-trigger-max-width', `${Math.max(46, viewportRect.width - 32)}px`);
      const triggerWidth = triggerNode.getBoundingClientRect().width;
      const labelWidth = labelNode.getBoundingClientRect().width;
      const menuHeight = menuRef.current?.scrollHeight || 0;
      const ratio = Math.max(.1, Number(job?.sourceHeight || element.h) / Math.max(1, Number(job?.sourceWidth || element.w)));
      const frameHeight = Math.min(150 * ratio, Math.max(60, viewportRect.height - 108));
      const placement = calculateCanvasAiPlacement({ blockRect: blockNode.getBoundingClientRect(),
        elementRect: rect, viewportRect, scale, triggerWidth, labelWidth, menuHeight, resultHeight: frameHeight + 84 });
      const values = { right: `${placement.right}px`, top: `${placement.top}px`,
        'menu-top': `${(placement.menuTop - placement.top) * scale}px`,
        'menu-max-height': `${placement.menuMaxHeight}px`, 'read-width': `${placement.readWidth}px`,
        'result-right': `${(placement.resultRight - placement.right) * scale}px`,
        'result-top': `${(placement.resultTop - placement.top) * scale}px`,
        'result-height': `${frameHeight}px` };
      Object.entries(values).forEach(([name, value]) => layer.style.setProperty(`--canvas-ai-${name}`, value));
      setFixed(placement.fixed); setShowLabel(placement.showLabel); setMenuUp(placement.menuUp); setPlaced(true);
    };
    const queue = () => { if (!active) return; cancelAnimationFrame(frame); frame = requestAnimationFrame(measure); };
    measure();
    document.fonts?.ready.then(queue);
    wrap.addEventListener('scroll', queue, { passive: true });
    window.addEventListener('resize', queue);
    const observer = new ResizeObserver(queue);
    observer.observe(wrap); observer.observe(blockNode);
    observer.observe(labelNode);
    observer.observe(triggerNode);
    if (orbRef.current) observer.observe(orbRef.current);
    if (menuRef.current) observer.observe(menuRef.current);
    return () => { active = false; cancelAnimationFrame(frame); wrap.removeEventListener('scroll', queue); window.removeEventListener('resize', queue); observer.disconnect(); };
  }, [visible, element, block, scale, wrapRef, open, branchId, loading, job]);

  const keyMenu = (e) => {
    e.stopPropagation();
    if (e.key === 'Escape') { e.preventDefault(); close(true); return; }
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) return;
    e.preventDefault();
    const items = [...menuRef.current.querySelectorAll('[role="menuitem"]')].filter((item) => item.getAttribute('aria-disabled') !== 'true');
    if (!items.length) return;
    const current = items.indexOf(document.activeElement);
    const index = e.key === 'Home' ? 0 : e.key === 'End' ? items.length - 1
      : (current + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
    items[index].focus();
  };
  const describe = (item, actionId = item?.id) => setHover(item?.description ? { description: busy ? '지금 만들고 있어요' : item.description,
    price: busy ? null : item.price || `${canvasAiActionCreditCost(menu.kind, actionId, creditCosts)} 크레딧` } : busy ? { description: '지금 만들고 있어요' } : null);
  const resultCost = job?.response?.creditsCharged ?? job?.result?.creditsCharged
    ?? editorImageCreditCost(job?.request, creditCosts);
  const action = (id, value) => {
    if (busy) return;
    const node = [...(layerRef.current?.closest('.canvas-block')?.querySelectorAll('[data-elid]') || [])]
      .find((item) => item.dataset.elid === element.id)?.querySelector('img');
    close();
    onAction(id, value, { width: node?.naturalWidth, height: node?.naturalHeight });
  };
  const focusMenu = () => requestAnimationFrame(() => menuRef.current?.querySelector('[role="menuitem"][aria-disabled="false"]')?.focus());
  const branchIcon = (item) => item.id === 'closure' ? <CanvasAiClosureGlyph /> : item.id === 'color'
    ? <span className="canvas-ai-color-dots">{(item.options || []).slice(0, 3).map((opt) => <ProductColor key={opt.value} hex={opt.hex} />)}</span>
    : <CanvasAiIcon name={item.id} />;
  const branchButton = (item) => <button type="button" role="menuitem" key={item.id}
    aria-disabled={busy || item.disabled ? 'true' : 'false'} aria-expanded={item.direct ? undefined : branchId === item.id}
    className={`canvas-ai-pill canvas-ai-branch${item.ring ? ' btn-glowring' : ''}${branchId === item.id ? ' is-open' : ''}`}
    onMouseEnter={() => describe(item)} onMouseLeave={() => setHover(null)} onFocus={() => describe(item)} onBlur={() => setHover(null)}
    onClick={() => {
      if (busy || item.disabled) return;
      if (item.id === 'upload') { close(); onUpload(); }
      else if (item.direct) action(item.id);
      else { setBranchId(branchId === item.id ? null : item.id); setHover(null); focusMenu(); }
    }}>{branchIcon(item)}<span>{item.label}</span>{!item.direct && <Chevron />}</button>;
  if (!visible) return null;
  return <div ref={layerRef} className={`canvas-ai-layer${placed ? ' is-placed' : ''}${fixed ? ' is-fixed' : ''}${menuUp ? ' is-up' : ''}`}
    onPointerDown={(e) => e.stopPropagation()} onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
    <div className="canvas-ai-measure" aria-hidden="true">{menu.branches.map((item) => <span key={item.id}
      className={`canvas-ai-pill canvas-ai-branch${item.ring ? ' btn-glowring' : ''}`}>
      {branchIcon(item)}<span>{item.label}</span>{!item.direct && <Chevron />}</span>)}
      <span ref={triggerMeasureRef} className="canvas-ai-trigger-measure canvas-ai-orb is-labelled">
        <span className="canvas-ai-orb-hit" /><span ref={labelMeasureRef} className="canvas-ai-label">AI 생성</span>
      </span></div>
    <button type="button" ref={orbRef} className={`canvas-ai-orb${showLabel ? ' is-labelled' : ''}`} aria-label="AI 생성"
      aria-haspopup="menu" aria-expanded={open}
      onMouseEnter={() => { if (!open) setHover({ description: '이 사진으로 새 컷을 생성해요', orb: true }); }}
      onMouseLeave={() => setHover(null)} onFocus={() => { if (!open) setHover({ description: '이 사진으로 새 컷을 생성해요', orb: true }); }}
      onBlur={() => setHover(null)} onClick={() => { setOpen(!open); setBranchId(null); setHover(busy ? { description: '지금 만들고 있어요' } : null); }}
      onKeyDown={(e) => {
        if (['ArrowDown', 'ArrowUp'].includes(e.key)) { e.preventDefault(); setOpen(true); focusMenu(); }
        if (e.key === 'Enter' || e.key === ' ') focusMenu();
      }}><span className="canvas-ai-orb-hit" aria-hidden="true"><span className="canvas-ai-core"><span className="canvas-ai-light-a" /><span className="canvas-ai-light-b" /><span className="canvas-ai-highlight" /></span></span>
      {showLabel && <span className="canvas-ai-label">AI 생성</span>}</button>
    {hover && (!hover.orb || (!open && !showLabel)) && <div className={`canvas-ai-read${hover.orb ? ' is-orb' : ''}`}><b>{hover.description}</b>{hover.price && <small>{hover.price}</small>}</div>}
    {open && <div ref={menuRef} className="canvas-ai-menu" role="menu" aria-label="이 사진으로 새 컷" onKeyDown={keyMenu}>
      {loading ? <p className="canvas-ai-menu-message" role="status">불러오는 중</p>
        : assetError ? <p className="canvas-ai-menu-message">{assetError.message || '사진 정보를 불러오지 못했어요.'}</p>
          : menu.branches.length === 0 ? <p className="canvas-ai-menu-message">이 사진으로 만들 수 있는 컷이 없어요.</p>
            : branch ? <>{menuUp && <div className={`canvas-ai-options${branch.id === 'closure' ? ' is-closure' : ''}`}>{renderOptions(branch)}</div>}{branchButton(branch)}
              {!menuUp && <div className={`canvas-ai-options${branch.id === 'closure' ? ' is-closure' : ''}`}>{renderOptions(branch)}</div>}</>
              : menu.branches.map(branchButton)}
    </div>}
    {showResult && !open && <div className="canvas-ai-result">
      <div className="canvas-ai-frame" onMouseEnter={() => describe({ description: 'AI로 생성한 새 컷', price: `${resultCost} 크레딧` })}
        onMouseLeave={() => setHover(null)} onFocus={() => describe({ description: 'AI로 생성한 새 컷', price: `${resultCost} 크레딧` })}
        onBlur={() => setHover(null)}>
        {job.status === 'arrived' ? <><img src={result?.src} alt="AI로 생성한 새 컷" draggable={false} /><span className="canvas-ai-new">새 컷</span>
          <button type="button" className="canvas-ai-close" aria-label="결과 자리 닫기" onClick={onDismiss}>×</button></>
          : <CanvasAiProgress job={job} onCancel={onCancel} onDismiss={onDismiss} />}
      </div>
      {job.status === 'arrived' && <div className="canvas-ai-result-actions modal-actions">
        <button type="button" className="canvas-ai-pill canvas-ai-replace" disabled={Boolean(replaceReason)} onClick={onReplace}>현재 이미지와 교체</button>
        <button type="button" className="canvas-ai-pill canvas-ai-add" onClick={onAdd}>새 블록에 추가</button>
        {replaceReason && <p>{replaceReason}</p>}
      </div>}
    </div>}
  </div>;

  function renderOptions(selectedBranch) {
    return selectedBranch.options.map((option) => {
      const disabled = busy || option.disabled || option.current;
      return <button type="button" role="menuitem" key={option.value} aria-disabled={disabled ? 'true' : 'false'}
        aria-label={`${option.label}${option.current ? ', 지금 값' : ''}`}
        className={`canvas-ai-option${selectedBranch.id === 'color' ? ' is-color' : ' canvas-ai-pill'}${option.current ? ' is-current' : ''}`}
        onMouseEnter={() => describe(option, selectedBranch.id)} onMouseLeave={() => setHover(null)} onFocus={() => describe(option, selectedBranch.id)} onBlur={() => setHover(null)}
        onClick={() => { if (!disabled) action(selectedBranch.id, option.value); }}>
        {selectedBranch.id === 'color' ? <><span>{option.label}</span><span className="canvas-ai-color-dot"><ProductColor hex={option.hex} />{option.current && <Check />}</span></>
          : <>{selectedBranch.id === 'closure' ? <CanvasAiClosureGlyph value={option.value} /> : option.src && <span className="canvas-ai-source-picture"><img src={option.src} alt="" /></span>}
            <span>{option.label}</span>{option.current && <Check />}</>}
      </button>;
    });
  }
}
