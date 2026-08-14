/* =============================================================
   features/editor/EditorPanels.jsx — left-panel content per toolbar tab.
   Ported verbatim from reference/prototype/features/editor-panels.jsx.
   Only change: ES imports/exports (was window globals).
   ============================================================= */
import { useState, useEffect, useRef } from 'react';
import { Icon, Button, IconButton, Chips, EmptyState } from '@/components/ui.jsx';
import { UnderlineTabs, ColorDots, MoodGuide, OuterClosureIcon } from '@/features/storyboard/Storyboard.jsx';
import { ModelThumb } from '@/features/analysis/AnalysisForm.jsx';
import { SHAPE_D } from '@/features/editor/shapes.js';
import {
  ALL_CUT_TYPE_OPTIONS,
  inferContentRole,
} from '@/lib/storyboardTaxonomy.js';
import { selectGenerationExamples } from '@/lib/generationExamples.js';
import { detailDirectionFromExample } from '@/lib/storyboardExampleSelection.js';
import { thumbUrl } from '@/lib/imageCdn.js';
import { DEFAULT_BUBBLE_RADIUS, DEFAULT_BUBBLE_STROKE, DEFAULT_BUBBLE_STROKE_WIDTH, FRAME_LIBRARY_ITEMS, OBJECT_LIBRARY_ITEMS, WARDROBE_IMAGE_MIME, colorWithOpacity, encodeWardrobeImage, normalizeHexColor } from '@/features/editor/editorLibrary.js';
import { DEFAULT_EDITOR_COLOR_PRESETS, commitNumberDraft, hexToHsv, hsvToHex } from '@/features/editor/editorAppearance.js';
import { ContentPanel } from '@/features/editor/ContentPanel.jsx';

function PanelHead({ title, sub }) {
  return <><div className="panel-h">{title}</div>{sub && <div className="panel-sub">{sub}</div>}</>;
}

/* ---------- shared input atoms (used by 이미지 / 텍스트 props) ---------- */
function DraftNumberInput({ value, min = -Infinity, max = Infinity, onCommit, ariaLabel }) {
  const [draft, setDraft] = useState(String(value ?? ''));
  const focusedRef = useRef(false);
  const cancelRef = useRef(false);
  useEffect(() => {
    if (!focusedRef.current) setDraft(String(value ?? ''));
  }, [value]);
  const commit = () => {
    focusedRef.current = false;
    if (cancelRef.current) {
      cancelRef.current = false;
      setDraft(String(value ?? ''));
      return;
    }
    const next = commitNumberDraft(draft, { min, max, fallback: value });
    setDraft(String(next));
    onCommit(next);
  };
  return (
    <input value={draft} inputMode="decimal" aria-label={ariaLabel}
      onFocus={(event) => { focusedRef.current = true; event.currentTarget.select(); }}
      onChange={(event) => setDraft(event.target.value)} onBlur={commit}
      onKeyDown={(event) => {
        if (event.key === 'Enter') event.currentTarget.blur();
        if (event.key === 'Escape') { cancelRef.current = true; event.currentTarget.blur(); }
      }} />
  );
}

function NumStepper({ value, min = 0, max = 9999, step = 1, onChange }) {
  const clamp = (v) => Math.min(max, Math.max(min, v));
  return (
    <div className="num-stepper">
      <button type="button" onClick={() => onChange(clamp(+(value - step).toFixed(2)))}><Icon name="minus" size={15} /></button>
      <DraftNumberInput value={value} min={min} max={max} step={step} onCommit={onChange} ariaLabel="숫자 입력" />
      <button type="button" onClick={() => onChange(clamp(+(value + step).toFixed(2)))}><Icon name="plus" size={15} /></button>
    </div>
  );
}
function NumField({ icon, iconText, labelText, value, min = -9999, max = 9999, onChange, suffix }) {
  return (
    <label className="numfield" title={labelText || undefined}>
      <span className="nf-ico">{iconText || <Icon name={icon} size={15} />}</span>
      {labelText && <span className="nf-label">{labelText}</span>}
      <DraftNumberInput value={value} min={min} max={max} onCommit={onChange} ariaLabel={labelText || iconText || '숫자 입력'} />
      {suffix && <span className="nf-suf">{suffix}</span>}
    </label>
  );
}
function MiniSelect({ value, options, onChange }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    const h = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    window.addEventListener('mousedown', h); return () => window.removeEventListener('mousedown', h);
  }, []);
  const opts = options.map((o) => typeof o === 'string' ? { value: o, label: o } : o);
  const cur = opts.find((o) => o.value === value) || opts[0];
  return (
    <div className={`mini-select${open ? ' open' : ''}`} ref={ref}>
      <button type="button" className="ms-btn" onClick={() => setOpen((o) => !o)}><span>{cur?.label}</span><Icon name="chevDown" size={15} /></button>
      {open && <div className="ms-menu">{opts.map((o) => (
        <button type="button" key={o.value} className={`ms-opt${o.value === value ? ' on' : ''}`} onClick={() => { onChange(o.value); setOpen(false); }}>{o.label}{o.value === value && <Icon name="check" size={14} />}</button>
      ))}</div>}
    </div>
  );
}
/* ---------- Figma-style sectioned inspector atoms ---------- */
function PanelSection({ title, actions, first, children }) {
  return (
    <div className={`psec${first ? ' first' : ''}`}>
      {title && <div className="psec-head"><span className="psec-title">{title}</span>{actions && <div className="psec-actions">{actions}</div>}</div>}
      {children}
    </div>
  );
}
function SwatchField({ value, opacity, allowNone, thumb, onColor, onOpacity, visible = true, onToggleVisible }) {
  const normalized = normalizeHexColor(value);
  const [hexDraft, setHexDraft] = useState(normalized || '#000000');
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [paletteHsv, setPaletteHsv] = useState(() => hexToHsv(normalized || '#000000'));
  const fieldRef = useRef(null);
  const alpha = Math.min(100, Math.max(0, Math.round(opacity ?? 100)));
  useEffect(() => {
    if (!normalized) return;
    setHexDraft(normalized);
    setPaletteHsv(hexToHsv(normalized));
  }, [normalized]);
  useEffect(() => {
    const close = (event) => { if (fieldRef.current && !fieldRef.current.contains(event.target)) setPaletteOpen(false); };
    window.addEventListener('mousedown', close);
    return () => window.removeEventListener('mousedown', close);
  }, []);
  const isNone = allowNone && (!value || value === 'none');
  const colorPresets = DEFAULT_EDITOR_COLOR_PRESETS;
  const commitHex = () => {
    const next = normalizeHexColor(hexDraft);
    if (next && onColor) onColor(next);
    setHexDraft(next || normalized || '#000000');
  };
  const setColor = (color) => {
    const next = normalizeHexColor(color);
    if (!next || !onColor) return;
    setHexDraft(next); onColor(next); setPaletteOpen(false);
  };
  const setCustomColor = (nextHsv) => {
    const next = {
      h: Math.min(359, Math.max(0, Number(nextHsv.h) || 0)),
      s: Math.min(100, Math.max(0, Number(nextHsv.s) || 0)),
      v: Math.min(100, Math.max(0, Number(nextHsv.v) || 0)),
    };
    const hex = hsvToHex(next);
    setPaletteHsv(next); setHexDraft(hex); onColor(hex);
  };
  const pickPalette = (event) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const s = ((event.clientX - rect.left) / rect.width) * 100;
    const v = (1 - (event.clientY - rect.top) / rect.height) * 100;
    setCustomColor({ ...paletteHsv, s, v });
  };
  const renderedColor = normalized ? colorWithOpacity(normalized, alpha / 100) : (value || '#000000');
  return (
    <div className="swatchfield" ref={fieldRef}>
      <div className="sf-main">
        {thumb ? (
          <span className="sf-swatch sf-thumb"><img src={thumb} alt="" /></span>
        ) : (
          <button type="button" className={`sf-swatch${isNone ? ' none' : ''}`} title="기본 색상 열기"
            onClick={() => setPaletteOpen((open) => !open)} aria-expanded={paletteOpen}>
            {isNone ? <Icon name="ban" size={13} /> : <span className="sf-swatch-color" style={{ background: renderedColor }} />}
          </button>
        )}
        {thumb || isNone ? <span className="sf-hex-label">{thumb ? '이미지' : '없음'}</span> : (
          <input className="sf-hex" aria-label="HEX 색상" value={hexDraft}
            onFocus={(e) => e.currentTarget.select()} onChange={(e) => setHexDraft(e.target.value.toUpperCase())}
            onBlur={commitHex} onKeyDown={(e) => {
              if (e.key === 'Enter') e.currentTarget.blur();
              if (e.key === 'Escape') { setHexDraft(normalized || '#000000'); e.currentTarget.blur(); }
            }} />
        )}
        {onOpacity && !isNone && <span className="sf-op"><input aria-label="불투명도" value={alpha} onChange={(e) => { const v = parseFloat(e.target.value); if (!isNaN(v)) onOpacity(Math.min(100, Math.max(0, v))); }} /><i>%</i></span>}
        {allowNone && !isNone && onColor && <button type="button" className="sf-none-action" onClick={() => onColor('none')} title="색상 없음"><Icon name="ban" size={14} /></button>}
        {onToggleVisible && <button type="button" className="sf-eye" onClick={onToggleVisible} title={visible ? '숨기기' : '표시'}><Icon name={visible ? 'eye' : 'eyeOff'} size={15} /></button>}
      </div>
      {paletteOpen && !thumb && onColor && (
        <div className="sf-color-popover">
          <div className="sf-preset-grid" aria-label="기본 색상">
            {colorPresets.map((color) => (
              <button type="button" key={color} className={`sf-preset${normalized === color ? ' on' : ''}`}
                style={{ background: color }} title={color} aria-label={`${color} 색상`} onClick={() => setColor(color)} />
            ))}
          </div>
          <div className="sf-color-divider" />
          <div className="sf-color-popover-head"><strong>직접 색상</strong><span>{hexDraft}</span></div>
          <div className="sf-color-palette" role="slider" tabIndex="0" aria-label="채도와 명도"
            aria-valuetext={hexDraft}
            style={{ backgroundColor: hsvToHex({ h: paletteHsv.h, s: 100, v: 100 }) }}
            onPointerDown={(event) => { event.currentTarget.setPointerCapture(event.pointerId); pickPalette(event); }}
            onPointerMove={(event) => { if (event.currentTarget.hasPointerCapture(event.pointerId)) pickPalette(event); }}
            onPointerUp={(event) => event.currentTarget.releasePointerCapture(event.pointerId)}
            onKeyDown={(event) => {
              const delta = event.shiftKey ? 10 : 1;
              const patch = event.key === 'ArrowLeft' ? { s: paletteHsv.s - delta }
                : event.key === 'ArrowRight' ? { s: paletteHsv.s + delta }
                  : event.key === 'ArrowUp' ? { v: paletteHsv.v + delta }
                    : event.key === 'ArrowDown' ? { v: paletteHsv.v - delta }
                      : null;
              if (patch) { event.preventDefault(); setCustomColor({ ...paletteHsv, ...patch }); }
            }}>
            <span className="sf-color-palette-cursor" style={{ left: `${paletteHsv.s}%`, top: `${100 - paletteHsv.v}%` }} />
          </div>
          <label className="sf-hue-control">
            <span>색조</span>
            <input type="range" min="0" max="359" step="1" value={Math.round(paletteHsv.h)} aria-label="색조"
              onChange={(event) => setCustomColor({ ...paletteHsv, h: Number(event.target.value) })} />
          </label>
        </div>
      )}
      {onOpacity && !isNone && (
        <label className="sf-opacity">
          <span>투명도</span>
          <input type="range" min="0" max="100" step="1" value={alpha}
            onChange={(e) => onOpacity(Number(e.target.value))} />
        </label>
      )}
    </div>
  );
}

function RangeNumberControl({ label, value, onChange, min = 0, max = 100, step = 1 }) {
  const width = Number.isFinite(Number(value)) ? Number(value) : min;
  const commit = (next) => {
    const number = Number(next);
    if (Number.isFinite(number)) onChange(Math.min(max, Math.max(min, number)));
  };
  return (
    <div className="range-number-control">
      <div className="range-number-head"><span>{label}</span><strong>{width}px</strong></div>
      <div className="range-number-row">
        <input type="range" min={min} max={max} step={step} value={width} aria-label={label}
          onChange={(e) => commit(e.target.value)} />
        <label><DraftNumberInput value={width} min={min} max={max} step={step} onCommit={commit} ariaLabel={`${label} 숫자 입력`} /><span>px</span></label>
      </div>
    </div>
  );
}

function StrokeWidthControl(props) {
  return <RangeNumberControl label="테두리 굵기" min={0.5} max={12} step={0.5} {...props} />;
}

/* ---------- AI · 현재 이미지 수정 — 예시 카드 선택 + 누적 트레이 ---------- */
const VARY_CATS = [
  { id: 'cut', label: '컷 변경' }, { id: 'bg', label: '배경' },
  { id: 'pose', label: '포즈' }, { id: 'face', label: '표정' },
];
function VaryPanel({ catalogs, source, onPickRef, onGenerate, onSetCutType }) {
  const opts = catalogs.varyOptions || {};
  const [cat, setCat] = useState('cut');
  const [sel, setSel] = useState({});
  const [refBg, setRefBg] = useState(null); // 레퍼런스 배경 src — bg 프리셋 카드와 상호 배타
  const [cutDir, setCutDir] = useState('keep'); // 컷 변경 · 방향 — 'keep' = 현재 유지
  const [cutShot, setCutShot] = useState('keep'); // 컷 변경 · 샷 종류
  const busyRef = useRef(false); // 같은 틱 더블클릭으로 생성이 2번 나가는 것 방지
  if (!source) {
    return <EmptyState icon="image" title="변형할 컷을 선택하세요" desc="캔버스나 의류 탭에서 이미지를 먼저 선택해주세요." />;
  }
  // 소스 컷 종류 — AI 생성 컷은 생성 시 기록된 cutType 으로 알고, 직접 업로드는 미상(null).
  // 미상이면 '모델 착용 컷'으로 가정하고(B안), 질문 카드로 제품 사진 전환만 받는다.
  const srcType = source.cutType || null;
  const isProduct = srcType === 'product';
  // mirror 레시피 소스(ADR-0004): 방향 변경 없음, 샷 full/medium만, 포즈는 셀피 구도 자동이라 변형 대상 아님
  const isMirror = srcType === 'mirror';
  const dirOpts = isProduct ? catalogs.productDirections : catalogs.directions;
  // 현재 이미지 수정은 색상별 Detail 근거를 안전하게 연결할 수 없으므로 디테일샷을 제공하지 않는다.
  const shotOpts = isProduct ? catalogs.productShotTypes.filter((option) => option.value !== 'detail')
    : catalogs.shotTypes;
  const cats = isProduct ? VARY_CATS.filter((c) => c.id === 'cut' || c.id === 'bg')
    : isMirror ? VARY_CATS.filter((c) => c.id !== 'pose')
    : VARY_CATS;
  const safeCat = cats.some((c) => c.id === cat) ? cat : 'cut';
  const optLabel = (c, id) => (opts[c] || []).find((o) => o.id === id)?.label || id;
  const valLabel = (list, v) => (list || []).find((o) => o.value === v)?.label || v;
  // 칩/payload 순서 = 적용 우선순위 계약: 구도(방향·샷)가 기준 → 포즈·표정 → 배경(레퍼런스 포함)이 구도에 맞춰 따라온다
  const chips = [];
  if (cutDir && cutDir !== 'keep') chips.push({ key: 'dir', cat: '방향', type: 'direction', value: cutDir, label: valLabel(dirOpts, cutDir), clear: () => setCutDir('keep') });
  if (cutShot && cutShot !== 'keep') chips.push({ key: 'shot', cat: '샷 종류', type: 'shot', value: cutShot, label: valLabel(shotOpts, cutShot), clear: () => setCutShot('keep') });
  if (sel.pose) chips.push({ key: 'pose', cat: '포즈', type: 'pose', value: sel.pose, label: optLabel('pose', sel.pose), clear: () => setSel((s) => ({ ...s, pose: null })) });
  if (sel.face) chips.push({ key: 'face', cat: '표정', type: 'face', value: sel.face, label: optLabel('face', sel.face), clear: () => setSel((s) => ({ ...s, face: null })) });
  if (sel.bg || refBg) chips.push({ key: 'bg', cat: '배경', type: 'bg', value: sel.bg || 'ref',
    label: sel.bg ? optLabel('bg', sel.bg) : '레퍼런스 이미지', clear: () => { setSel((s) => ({ ...s, bg: null })); setRefBg(null); } });
  const n = chips.length;
  const hasChange = { bg: !!(sel.bg || refBg), pose: !!sel.pose, face: !!sel.face, cut: (cutDir && cutDir !== 'keep') || (cutShot && cutShot !== 'keep') };
  const cost = catalogs.creditCosts?.editorImage ?? 1;
  const pickCard = (oid) => { if (safeCat === 'bg') setRefBg(null); setSel((s) => ({ ...s, [safeCat]: s[safeCat] === oid ? null : oid })); };
  const clearAll = () => { setSel({}); setRefBg(null); setCutDir('keep'); setCutShot('keep'); };
  // 기준 전환 — 요소에 영구 저장(이미지당 1번만 답하면 됨). 옵션 세트가 바뀌므로 선택은 초기화.
  // '모델 착용 컷' 전환은 사람컷 대표값 styling 으로 기록한다 (ADR-0003).
  const setKind = (t) => { onSetCutType(t); clearAll(); setCat('cut'); };
  const pickRef = async () => { const picked = await onPickRef(); if (picked) { setRefBg(picked); setSel((s) => ({ ...s, bg: null })); } };
  const generate = () => {
    if (busyRef.current) return;
    busyRef.current = true; // 곧 의류 탭으로 전환되며 패널이 언마운트 — 같은 틱 더블클릭만 방어
    onGenerate({
      // 변형 대상 = 현재 변형 소스(캔버스 요소 또는 의류 이미지). cutType 미상이면 모델 착용 컷(styling)으로 가정.
      source: { id: source.id, src: source.src, cutType: srcType || 'styling' },
      // 변경 0개(빈 트레이) = '비슷한 컷 만들기' (PRD §10.8) — 빈 배열이 그 계약
      changes: chips.map((c) => ({ type: c.type, value: c.value, label: c.label })),
      refBg: refBg ? (refBg.url || refBg) : null,             // 표시용 URL (mock 계약 유지)
      refBgAssetId: refBg?.assetId || null,                   // 서버 첨부용 asset id (계약 §6)
    });
  };
  const catLabel = VARY_CATS.find((c) => c.id === safeCat).label;
  return (
    <div>
      {!srcType ? (
        <div className="vary-kind">
          <p className="vk-txt">모델 착용 컷 기준 옵션이에요. 제품만 나온 사진이면 알려주세요.</p>
          <button type="button" className="vk-btn" onClick={() => setKind('product')}>제품만 나온 사진이에요</button>
        </div>
      ) : (
        <div className="vary-kind compact">
          <span className="vk-txt">{isProduct ? '제품 사진 기준의 옵션이에요.' : '모델 착용 컷 기준의 옵션이에요.'}</span>
          <button type="button" className="vk-link" onClick={() => setKind(isProduct ? 'styling' : 'product')}>
            {isProduct ? '모델 착용 컷으로 전환' : '제품 사진으로 전환'}
          </button>
        </div>
      )}
      <div className="vary-tabs">
        <UnderlineTabs value={safeCat} onChange={setCat}
          options={cats.map((c) => ({ value: c.id, label: <>{c.label}{hasChange[c.id] && <span className="vary-dot" />}</> }))} />
      </div>
      {safeCat === 'cut' ? (
        <>
          {/* Chips 는 선택된 칩 재클릭 시 null 을 보냄 → '변경 없음'(keep) 으로 복귀시킨다 */}
          {!isMirror && <div className="insp-sec"><label className="lbl">방향</label>
            <Chips options={[{ value: 'keep', label: '변경 없음' }, ...dirOpts]} value={cutDir} onChange={(v) => setCutDir(v || 'keep')} /></div>}
          <div className="insp-sec"><label className="lbl">샷 종류</label>
            <Chips options={[{ value: 'keep', label: '변경 없음' }, ...shotOpts]} value={cutShot} onChange={(v) => setCutShot(v || 'keep')} /></div>
        </>
      ) : (
        <div className="insp-sec">
          <label className="lbl">{catLabel} 카드 선택</label>
          <div className="vary-grid">
            {(opts[safeCat] || []).map((o) => {
              const on = sel[safeCat] === o.id;
              return (
                <button type="button" key={o.id} className={`vary-card${on ? ' on' : ''}`} onClick={() => pickCard(o.id)}>
                  <span className="vc-check">{on && <Icon name="check" size={12} />}</span>
                  <img src={o.thumb} alt="" />
                  <span className="vc-label">{o.label}</span>
                </button>
              );
            })}
          </div>
        </div>
      )}
      {safeCat === 'bg' && (
        <details className="insp-extra vary-ref">
          <summary><Icon name="chevRight" size={15} />레퍼런스로 배경 지정{refBg && <span className="vr-badge">사용 중</span>}</summary>
          <div className="vary-ref-body">
            {refBg ? (
              <>
                {/* 업로드한 레퍼런스는 배경 카드와 같은 크기의 카드로 표시 */}
                <div className="vary-grid">
                  <span className="vary-card on vr-cardprev">
                    <span className="vc-check"><Icon name="check" size={12} /></span>
                    <img src={refBg?.url || refBg} alt="" />
                    <span className="vc-label">레퍼런스</span>
                  </span>
                </div>
                <Button variant="ghost" size="sm" icon="trash" onClick={() => setRefBg(null)} style={{ marginTop: 10 }}>해제</Button>
                <p className="hint" style={{ marginTop: 8 }}>배경은 선택한 컷 구도에 맞춰 적용돼요.</p>
              </>
            ) : (
              <>
                <Button variant="ghost" size="sm" block icon="upload" onClick={pickRef}>배경 레퍼런스 업로드</Button>
                <p className="hint" style={{ marginTop: 8 }}>원하는 배경 사진을 올리면 카드 대신 그 분위기로 배경을 바꿔요. 배경은 선택한 컷 구도에 맞춰 적용돼요.</p>
              </>
            )}
          </div>
        </details>
      )}
      {n > 0 && (
        <div className="vary-tray">
          <div className="vt-head">
            <span className="vt-title">변경 요약 ({n})</span>
            <button type="button" className="vt-clear" onClick={clearAll}>전체 해제</button>
          </div>
          <div className="vt-chips">
            {chips.map((c) => (
              <span className="vt-chip" key={c.key}>{c.cat} · {c.label}
                <button type="button" onClick={c.clear} title="해제"><Icon name="x" size={13} /></button>
              </span>
            ))}
          </div>
        </div>
      )}
      <Button variant="primary" block icon="sparkles" className="btn-glowring" onClick={generate} style={{ marginTop: 14 }}>
        {n > 0 ? `${n}개 변경 적용해서 생성 · ${cost} 크레딧` : `비슷한 컷 만들기 · ${cost} 크레딧`}
      </Button>
      <p className="hint" style={{ marginTop: 10 }}>
        {n > 0 ? '모든 변경이 한 장의 새 컷에 함께 반영돼요. 기존 이미지는 유지되고 새 컷은 의류 탭에 추가돼요.'
          : '변경 없이 생성하면 현재 컷과 비슷한 분위기의 새 컷을 만들어요. 새 컷은 의류 탭에 추가돼요.'}
      </p>
    </div>
  );
}

/* ---------- AI ---------- */
const NEW_CUT_DEFAULT_SHOT = { styling: 'full', horizon: 'full', mirror: 'full', product: 'ghost' };
export function AIPanel({ catalogs, fmModels, account, colorOpts = [], detailColorOpts = [], clothingType = 'top', matchClothing = [], exampleGender = null, varySource, onGenerate, onVaryGenerate, onPickRef, onPickMoodRef, onSetCutType }) {
  const [tab, setTab] = useState('vary');
  // 콘티보드와 같은 규칙 — 사용자는 컷 종류(촬영 방식)만 고르고, 사진 목적(contentRole)은 내부 자동 결정.
  const [cutType, setCutType] = useState('styling');
  const [dir, setDir] = useState('front');
  const [shot, setShot] = useState('full');
  const [color, setColor] = useState(null);
  // 실존(FaceMarket) 검증 모델 — 활성 라이선스 + 그리드 자산(assetsReady)까지 갖춰야 컷 생성 가능.
  // 목록이 비면(오프/미등록/mock 모드) 기존 가상모델(catalogs.models)로 폴백 — 서버도 동일 분기.
  const fmList = (fmModels || []).filter((m) => m.hasActiveLicense && m.assetsReady);
  const useFm = fmList.length > 0;
  const initialModel = useFm ? fmList[0]
    : (catalogs.models || []).find((m) => m.recommended) || (catalogs.models || [])[0];
  const [model, setModel] = useState(initialModel?.id || 'mA');
  useEffect(() => {   // 패널 열린 뒤 fm 카탈로그가 도착한 레이스 — mock id 선택을 실존 모델로 승격
    if (useFm && !fmList.some((m) => m.id === model)) setModel(fmList[0].id);
  }, [useFm]); // eslint-disable-line react-hooks/exhaustive-deps
  const [refImages, setRefImages] = useState([]);       // 내 레퍼런스 — NewCutRequest.refImages (계약 §6)
  const [exampleId, setExampleId] = useState(null);     // 촬영 연출 예시 — 예시 속 옷·신발·액세서리는 생성 근거에서 제외 (ADR-0004)
  const [refScope, setRefScope] = useState('all');
  const [outerClosure, setOuterClosure] = useState('open');
  const [matchIds, setMatchIds] = useState([]);
  const [matchOpen, setMatchOpen] = useState(false);
  const isProduct = cutType === 'product';
  const isMirror = cutType === 'mirror'; // mirror 레시피(ADR-0004): 방향 없음, 샷 full/medium만
  const [modelOpen, setModelOpen] = useState(false);
  const modelRef = useRef(null);
  const smoothScroll = (p, to, dur = 300) => {
    const from = p.scrollTop, d = to - from, t0 = performance.now();
    const step = (t) => { const k = Math.min(1, (t - t0) / dur); const e = k < .5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2; p.scrollTop = from + d * e; if (k < 1) requestAnimationFrame(step); };
    requestAnimationFrame(step);
  };
  const toggleModel = (e) => {
    e.preventDefault();
    const willOpen = !modelOpen;
    setModelOpen(willOpen);
    if (willOpen) {
      const p = (modelRef.current && modelRef.current.closest('.ed-left')) || (e.currentTarget && e.currentTarget.closest('.ed-left'));
      if (p) {
        requestAnimationFrame(() => requestAnimationFrame(() => smoothScroll(p, p.scrollHeight, 260)));
        setTimeout(() => { p.scrollTop = p.scrollHeight; }, 320);
      }
    }
  };
  const dirOpts = isProduct ? catalogs.productDirections : catalogs.directions;
  // 제품컷 샷(고스트·디테일)은 콘티보드처럼 MoodGuide 안의 샷 탭에서 고른다 — 디테일 상시 제공(2026-08-07 개편).
  const shotOpts = isProduct ? catalogs.productShotTypes : catalogs.shotTypes;
  const dirVal = dirOpts.some((o) => o.value === dir) ? dir : dirOpts[0].value;
  const shotVal = shotOpts.some((o) => o.value === shot) ? shot : shotOpts[0].value;
  // isDetail 은 검증된 shotVal 기준 — raw shot 을 읽으면 카탈로그 폴백 시 UI와 전송값이 어긋난다.
  const isDetail = isProduct && shotVal === 'detail';
  const activeColorOpts = isDetail ? detailColorOpts : colorOpts;
  const colorVal = activeColorOpts.some((option) => option.id === color)
    ? color : activeColorOpts[0]?.id || null;   // wardrobe 그룹 키 = colorId (계약 §3.6)
  // 갤러리·게이트용 성별 — 고른 모델(가상·실존)의 성별 우선, 못 찾으면 분석 기반(콘티보드 exampleGender 규칙).
  // 실존 모델은 catalogs.models 에 없어서 이 폴백이 없으면 null 이 되고, null 이면 착용컷 예시가 전부 닫힌다.
  const modelGender = [...(catalogs.models || []), ...fmList].find((item) => item.id === model)?.gender
    || exampleGender || null;
  const closureOptions = catalogs.outerClosureStates || [];
  const showOuterClosure = clothingType === 'outer' && !isProduct;
  const showMatchClothing = !isProduct && Array.isArray(matchClothing) && matchClothing.length > 0;
  // 콘티보드와 같은 게이트 — 발행 예시가 하나도 없는 컷 종류는 비활성(예시가 추가되면 자동 활성).
  const hasSelectableExamples = (cut, shotValue) => selectGenerationExamples(catalogs.genExamples, {
    cutType: cut, shot: shotValue, clothingType, gender: modelGender,
    appendSetOnly: cut !== 'product',
  }).length > 0;
  const cutTypeOptions = ALL_CUT_TYPE_OPTIONS.map((option) => {
    const shots = option.value === 'product' ? catalogs.productShotTypes : catalogs.shotTypes;
    return { ...option, disabled: !shots.some((item) => hasSelectableExamples(option.value, item.value)) };
  });
  // 콘티보드 settingsReset(storyboardExampleSelection.js)과 같은 규칙 — 이전 레시피의
  // 매칭 의류·아우터 열림·내 레퍼런스가 새 레시피에 숨은 채 전송되지 않게 전면 리셋.
  const resetRecipeSettings = () => {
    setMatchIds([]); setMatchOpen(false); setOuterClosure('open'); setRefImages([]);
  };
  const selectCutType = (value) => {
    if (value === cutType) return;
    const nextShotOpts = value === 'product' ? catalogs.productShotTypes : catalogs.shotTypes;
    const preferred = NEW_CUT_DEFAULT_SHOT[value] || 'full';
    // 기본 샷에 발행 예시가 없으면 예시가 있는 샷으로 — 빈 갤러리로 시작하지 않는다(콘티보드 동일).
    const nextShot = hasSelectableExamples(value, preferred) ? preferred
      : nextShotOpts.find((option) => hasSelectableExamples(value, option.value))?.value || preferred;
    setCutType(value); setDir('front'); setShot(nextShot);
    setExampleId(null); setRefScope('all');
    resetRecipeSettings();
  };
  const selectExample = (value) => {
    const replacing = !!exampleId && !!value && exampleId !== value;
    setExampleId(value);
    if (!value) setRefScope('all');
    if (replacing) resetRecipeSettings();
    // 디테일 컷의 방향은 예시에 내재 — 뒷면 디테일 예시를 고르면 back 을 내부 전송해
    // 서버가 BackDetail 사진을 근거로 쓴다(2026-08-07 오너 결정).
    if (isDetail) {
      const example = (catalogs.genExamples || []).find((item) => item.id === value);
      setDir(detailDirectionFromExample(example));
    }
  };
  return (
    <div>
      <div className="seg" data-idx={tab === 'vary' ? 1 : 0}>
        <button className={tab === 'new' ? 'on' : ''} onClick={() => setTab('new')}>새 이미지 추가</button>
        <button className={tab === 'vary' ? 'on' : ''} onClick={() => setTab('vary')}>현재 이미지 수정</button>
      </div>
      {tab === 'new' ? (
        <div>
          {/* 콘티보드와 같은 첫 선택 — 컷 종류(촬영 방식). 사진 목적은 inferContentRole 로 내부 결정 */}
          <div className="insp-sec"><label className="lbl">컷 종류</label>
            <UnderlineTabs options={cutTypeOptions} value={cutType} onChange={selectCutType} />
          </div>

          {/* 분위기 예시가 주인공 — 샷 종류는 갤러리의 아이콘 필터 (B+C안, ADR-0004) */}
          <MoodGuide catalogs={catalogs} cut={cutType} direction={isMirror ? null : dirVal} shot={shotVal}
            shotOptions={isProduct ? shotOpts : null}
            onShotChange={(v) => {
              setShot(v); setExampleId(null); setRefScope('all');
              // 고스트→디테일 전환 시 이전 '뒷면'이 숨은 채 BackDetail 근거로 새지 않게 — 콘티보드 동일 가드(Codex 리뷰 P1).
              if (isProduct) setDir('front');
            }} clothingType={clothingType} gender={modelGender}
            exampleId={exampleId} onExampleChange={selectExample}
            refScope={refScope} onRefScopeChange={setRefScope}
            refs={refImages} onRefsChange={setRefImages} onPickRef={onPickMoodRef} />
          {/* 디테일 컷은 방향 UI 없음 — 선택한 생성예시의 direction 라벨이 내부 결정 (selectExample) */}
          {!isMirror && !isDetail && <div className="insp-sec"><label className="lbl">방향</label><Chips className="oneline" options={dirOpts} value={dirVal} onChange={setDir} /></div>}

          {showOuterClosure && (
            <div className="insp-sec outer-closure-field">
              <div className="lbl" id="outer-closure-label-newcut">아우터 열림 정도</div>
              <div className="outer-closure-options" role="radiogroup" aria-labelledby="outer-closure-label-newcut">
                {closureOptions.map((option) => {
                  const on = outerClosure === option.value;
                  return (
                    <label key={option.value} className={`outer-closure-option${on ? ' on' : ''}`}>
                      <input type="radio" name="outer-closure-newcut" value={option.value}
                        checked={on} onChange={() => setOuterClosure(option.value)} />
                      <OuterClosureIcon state={option.value} />
                      <span>{option.label}</span>
                    </label>
                  );
                })}
              </div>
              <p className="outer-closure-hint">이 컷에서 아우터의 앞부분을 얼마나 열지 정해요.</p>
            </div>
          )}

          <div className="insp-divider" />

          <div className="insp-sec"><label className="lbl">색상</label>
            <ColorDots colorOpts={activeColorOpts} value={colorVal} onChange={setColor} /></div>

          {showMatchClothing && (
            <>
              <button className={`insp-detail-btn${matchOpen ? ' open' : ''}`} onClick={() => setMatchOpen((v) => !v)}>
                <Icon name="settings" size={17} />매칭 의류 바꾸기
              </button>
              {matchOpen && (
                <div className="sb-match-inline">
                  <div className="match-grid">
                    {matchClothing.map((m) => {
                      const on = matchIds.includes(m.id);
                      return (
                        <button key={m.id} className={`match-cell${on ? ' on' : ''}`} aria-pressed={on} onClick={() =>
                          setMatchIds(on ? [] : [m.id]) // 단일 선택 — matchClothingMax=1 (PRD §6.8, 콘티보드 동일)
                        }><img src={m.thumb} alt={m.name} /><span className="ml">{m.name}{on && <Icon name="check" size={12} />}</span></button>
                      );
                    })}
                  </div>
                </div>
              )}
            </>
          )}

          {!isProduct && <details ref={modelRef} className="insp-extra ai-model" open={modelOpen}>
            <summary onClick={toggleModel}><Icon name="chevRight" size={15} />모델</summary>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8, marginTop: 12 }}>
              {useFm ? fmList.map((m) => (
                <div key={m.id} className={`model-card fm-model img-only${model === m.id ? ' on' : ''}`} style={{ width: 'auto' }}
                  onClick={() => { setModel(m.id); setExampleId(null); setRefScope('all'); }}
                  title={`${m.displayName}${m.unitPrice != null ? ` · ₩${Number(m.unitPrice).toLocaleString('ko-KR')}/건` : ''}`}>
                  {m.coverImageUrl
                    ? <img src={m.coverImageUrl} alt={m.displayName} style={{ height: 104 }} />
                    : <ModelThumb uri={m.faceThumbUri} alt={m.displayName} />}
                  {m.status === 'verified' && <span className="fm-verified"><Icon name="check" size={11} />검증</span>}
                </div>
              )) : (catalogs.models || []).map((m) => (
                <div key={m.id} className={`model-card img-only${model === m.id ? ' on' : ''}`} style={{ width: 'auto' }} onClick={() => { setModel(m.id); setExampleId(null); setRefScope('all'); }}>
                  <img src={m.thumb} alt={m.name} style={{ height: 104 }} />
                </div>
              ))}
            </div>
          </details>}

          <Button variant="primary" block icon="sparkles" className="btn-glowring" onClick={() => onGenerate({
            contentRole: inferContentRole({ source: 'ai', cutType, shot: shotVal }),
            colorId: colorVal, cutType, direction: isMirror ? null : dirVal, shot: shotVal, modelId: model, exampleId, refScope,
            outerClosureState: showOuterClosure ? outerClosure : null,
            matchIds: isProduct ? [] : matchIds,
            refImages: refImages.map((r) => r?.url || r),                  // 표시용 URL (mock 계약 유지)
            refAssetIds: refImages.map((r) => r?.assetId).filter(Boolean), // 서버 첨부용 asset id (계약 §6)
          })}>새 이미지 생성 · {catalogs.creditCosts?.editorImage ?? 1} 크레딧</Button>
        </div>
      ) : (
        /* key=소스 id — 변형 대상이 바뀌면 패널 상태(선택/트레이/결과)를 통째로 초기화해 이미지 간 누수를 차단 */
        <VaryPanel key={varySource ? varySource.id : 'none'} catalogs={catalogs} source={varySource} onPickRef={onPickRef} onGenerate={onVaryGenerate} onSetCutType={onSetCutType} />
      )}
    </div>
  );
}

/* ---------- 의류 (wardrobe library) ---------- */
export function WardrobePanel({ wardrobe, colorOpts = [], pendingSlot, uploading = false, onInsert, onUpload, onVaryImage, onDeleteImage, isImageUsed, onFreshSeen, onImageDragStart, onImageDragEnd }) {
  // wardrobe 그룹 키 = colorId | 'misc' — 표시명은 colorOpts 에서 파생 (계약 §3.6)
  const colorFor = (group) => {
    if (group === 'misc') return { hex: '#d4d4d8', name: '기타', neutral: true };
    const c = colorOpts.find((x) => x.id === group);
    if (c) return { hex: c.hex, name: c.label };
    return { hex: '#d4d4d8', name: group, neutral: true };
  };
  const [collapsed, setCollapsed] = useState({});
  const toggle = (group) => setCollapsed((c) => ({ ...c, [group]: !c[group] }));
  return (
    <div className="ward-panel">
      {pendingSlot && <div className="ward-fill-banner"><Icon name="image" size={15} />빈 칸에 넣을 의류를 선택하세요</div>}
      <Button variant="ghost" block icon="upload" onClick={onUpload} disabled={uploading} style={{ marginBottom: uploading ? 8 : 16 }}>직접 이미지 업로드하기</Button>
      {uploading && (
        <div className="ward-upload-status" role="status" aria-live="polite">
          <Icon name="loader" size={16} className="spin" />
          <span>의류 이미지를 불러오는 중이에요</span>
        </div>
      )}
      {Object.entries(wardrobe).map(([group, imgs]) => {
        const c = colorFor(group);
        const open = !collapsed[group];
        return (
          <div className={`wardrobe-group${open ? '' : ' collapsed'}`} key={group}>
            <button type="button" className="wg-head" onClick={() => toggle(group)} aria-expanded={open}>
              <span className="wg-color">
                <span className={`wg-dot${c.neutral ? ' neutral' : ''}`} style={{ background: c.hex }} />
                <span className="wg-name">{c.name}</span>
                <span className="wg-count">{imgs.length}</span>
              </span>
              <Icon name="chevDown" size={16} className="wg-chev" />
            </button>
            {open && (
              <div className="wardrobe-grid">
                {imgs.map((im) => {
                  if (im.loading) return (
                    <div className="ward-cell loading" key={im.id}><Icon name="loader" size={18} className="spin" style={{ color: 'var(--fg-3)' }} /></div>
                  );
                  const used = Boolean(isImageUsed?.(im));
                  return (
                    <div className={`ward-cell${im.fresh ? ' fresh' : ''}`} key={im.id} onClick={() => onInsert(im)} title="클릭하거나 프레임으로 끌어 넣기"
                      draggable onDragStart={(e) => { const image = e.currentTarget.querySelector('img'); e.dataTransfer.effectAllowed = 'copy'; e.dataTransfer.setData(WARDROBE_IMAGE_MIME, encodeWardrobeImage(im, { width: image?.naturalWidth, height: image?.naturalHeight })); onImageDragStart?.(); }}
                      onDragEnd={() => onImageDragEnd?.()}
                      onAnimationEnd={im.fresh ? () => onFreshSeen && onFreshSeen(im.id) : undefined}>
                      <img src={thumbUrl(im.src, 240)} alt="" loading="lazy" decoding="async" />
                      <button type="button" className={`ward-trash${used ? ' disabled' : ''}`} draggable={false}
                        aria-label={used ? '현재 에디팅에 사용 중인 사진' : '의류 사진 삭제'} aria-disabled={used}
                        title={used ? '현재 에디팅에 사용 중이라 삭제할 수 없어요' : '사진 삭제'}
                        onPointerDown={(e) => e.stopPropagation()}
                        onClick={(e) => { e.stopPropagation(); onDeleteImage(im); }}>
                        <Icon name="trash" size={13} />
                      </button>
                      <button className="ai-flag" onClick={(e) => { e.stopPropagation(); onVaryImage(im); }} title="AI로 편집"><Icon name="wand" size={12} /><span>AI 편집</span></button>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

/* ---------- 이미지 props ---------- */
function LayerRow({ onLayer }) {
  return (
    <div className="prop-row"><span className="pk">레이어</span>
      <div style={{ display: 'flex', gap: 5 }}>
        <IconButton name="chevUp" size="sm" title="앞으로" onClick={() => onLayer('up')} />
        <IconButton name="chevDown" size="sm" title="뒤로" onClick={() => onLayer('down')} />
        <IconButton name="bringFront" size="sm" title="맨 앞으로" onClick={() => onLayer('front')} />
        <IconButton name="sendBack" size="sm" title="맨 뒤로" onClick={() => onLayer('back')} />
      </div></div>
  );
}
const SHAPE_PALETTE = ['#0e0d14', '#898989', '#d4d4d8', '#ffffff', '#4f88c9', '#d92d20'];
const LINE_DASH = [
  { id: 'dotted', label: '점선', preview: '3 5' },
  { id: 'dashed', label: '파선', preview: '12 9' },
  { id: 'solid', label: '실선', preview: '' },
];
function LabeledField({ label, children }) {
  return <div className="ff"><span className="ff-lbl">{label}</span>{children}</div>;
}
export function ImagePanel({ el, onChange, onLayer, onCrop, onCropReset, onReplace, onRemove, onVary, lock = true, onLock }) {
  // 비율 잠금은 에디터가 소유 — moveable keepRatio와 연동 (자물쇠 = keepRatio)
  const setLock = onLock || (() => {});
  if (!el || !['image', 'shape', 'line'].includes(el.type)) return <EmptyState icon="image" title="요소를 선택하세요" desc="캔버스에서 이미지·오브젝트를 클릭하면 속성이 여기에 나와요." />;
  const isImg = el.type === 'image', isLine = el.type === 'line', isShape = el.type === 'shape';
  const ratio = el.w / el.h || 1;
  const setW = (w) => onChange(lock ? { w, h: Math.max(20, Math.round(w / ratio)) } : { w });
  const setH = (h) => onChange(lock ? { h, w: Math.max(20, Math.round(h * ratio)) } : { h });
  const hasStroke = isShape && el.stroke && el.stroke !== 'none';
  const op = Math.round((el.opacity ?? 1) * 100);
  const curDash = el.dash || 'solid';
  return (
    <div className="fig-panel">
      {isImg && onVary && (
        <Button variant="ghost" block icon="wand" className="vary-jump" onClick={onVary} style={{ marginBottom: 16 }}>AI로 컷 변형하기</Button>
      )}
      {isImg && el.frameSlot && (
        <PanelSection title="프레임 이미지" first>
          <div className="frame-image-actions">
            <Button variant="quiet" size="sm" icon="refresh" onClick={() => onReplace?.(el)}>교체</Button>
            <Button variant="quiet" size="sm" icon="crop" disabled={!el.src} onClick={() => onCrop?.(el)}>자르기</Button>
            <Button variant="quiet" size="sm" icon="undo" disabled={!el.crop} onClick={() => onCropReset?.(el)}>초기화</Button>
            <Button variant="quiet" size="sm" icon="trash" disabled={!el.src} onClick={() => onRemove?.(el)}>빼내기</Button>
          </div>
        </PanelSection>
      )}
      <PanelSection title={isLine ? '선 크기' : '이미지 크기'} first={!isImg || !el.frameSlot}>
        <div className="size-row">
          <NumField iconText="가로" value={Math.round(el.w)} min={20} max={2000} onChange={setW} />
          <NumField iconText="세로" value={Math.round(el.h)} min={20} max={2000} onChange={setH} />
          <button type="button" className={`lock-btn${lock ? ' on' : ''}`} onClick={() => setLock((v) => !v)} title="비율 고정"><Icon name={lock ? 'lock' : 'unlock'} size={15} /></button>
        </div>
      </PanelSection>

      <PanelSection title="모양">
        <div className="field-2up labeled">
          <LabeledField label="회전"><NumField icon="rotate" value={el.rotate || 0} min={-180} max={180} suffix="°" onChange={(v) => onChange({ rotate: v })} /></LabeledField>
          {!isLine
            ? <LabeledField label="둥근 모서리"><NumField icon="cornerRadius" value={el.radius || 0} min={0} max={400} onChange={(v) => onChange({ radius: v })} /></LabeledField>
            : <span />}
        </div>
      </PanelSection>

      {isLine && (
        <PanelSection title="선 스타일">
          <div className="line-style-row">
            {LINE_DASH.map((o) => (
              <button key={o.id} type="button" className={`line-style${curDash === o.id ? ' on' : ''}`} title={o.label} onClick={() => onChange({ dash: o.id })}>
                <svg viewBox="0 0 64 12" preserveAspectRatio="none"><line x1="2" y1="6" x2="62" y2="6" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeDasharray={o.preview || undefined} /></svg>
              </button>
            ))}
          </div>
        </PanelSection>
      )}

      <PanelSection title={isLine ? '선 색상' : '채우기'}>
        {isImg ? (
          <SwatchField thumb={el.src} opacity={op} onOpacity={(v) => onChange({ opacity: v / 100 })} />
        ) : (
          <SwatchField value={el.fill || '#0e0d14'} palette={SHAPE_PALETTE} opacity={op} onColor={(c) => onChange({ fill: c })} onOpacity={(v) => onChange({ opacity: v / 100 })} />
        )}
      </PanelSection>

      {isLine && (
        <PanelSection title="굵기">
          <div className="field-2up"><NumField iconText="굵기" value={el.strokeWidth || 2.5} min={1} max={40} onChange={(v) => onChange({ strokeWidth: v })} /><span /></div>
        </PanelSection>
      )}

      {isShape && (
        <PanelSection title="테두리" actions={
          <button type="button" className="psec-act" title={hasStroke ? '테두리 제거' : '테두리 추가'} onClick={() => onChange({ stroke: hasStroke ? 'none' : '#0e0d14', strokeWidth: el.strokeWidth || 2 })}>
            <Icon name={hasStroke ? 'minus' : 'plus'} size={15} />
          </button>
        }>
          {hasStroke ? (
            <>
              <SwatchField value={el.stroke} palette={SHAPE_PALETTE} allowNone onColor={(c) => onChange({ stroke: c })} />
              <div className="field-2up" style={{ marginTop: 8 }}>
                <NumField iconText="굵기" value={el.strokeWidth || 2} min={0} max={40} onChange={(v) => onChange({ strokeWidth: v })} />
                <span />
              </div>
            </>
          ) : <div className="psec-empty">테두리 없음</div>}
        </PanelSection>
      )}

      {isImg && (
        <PanelSection title="자르기">
          <Button variant="ghost" size="sm" block icon="crop" onClick={() => onCrop && onCrop(el)}>크롭</Button>
        </PanelSection>
      )}
    </div>
  );
}

/* ---------- 텍스트 props ---------- */
const TEXT_PALETTE = ['#0e0d14', '#898989', '#ffffff', '#4f88c9', '#d92d20', '#067647'];
const HL_PALETTE = ['#fef3c7', '#dbeafe', '#dcfce7', '#fee2e2', '#f3f4f6', '#0e0d14'];
const WEIGHTS = [{ value: 300, label: 'Light' }, { value: 400, label: 'Regular' }, { value: 500, label: 'Medium' }, { value: 600, label: 'SemiBold' }, { value: 700, label: 'Bold' }];
export function TextPanel({ el, catalogs, onChange, onBubbleAppearanceChange, onLayer, onAddText, onAddGarmentText }) {
  const has = el && el.type === 'text';
  const isBubble = has && el.shape === 'bubble';
  const s = (has && el.style) || {};
  const setS = (p) => onChange({ style: { ...s, ...p } });
  const changeBubbleAppearance = onBubbleAppearanceChange || onChange;
  const bubbleStroke = isBubble && el.stroke !== 'none' ? (el.stroke || DEFAULT_BUBBLE_STROKE) : 'none';
  const bubbleStrokeWidth = Number.isFinite(Number(el?.strokeWidth)) ? Number(el.strokeWidth) : DEFAULT_BUBBLE_STROKE_WIDTH;
  const hasBubbleStroke = isBubble && bubbleStroke !== 'none';
  return (
    <div className="fig-panel">
      <button type="button" className="add-text-btn" onClick={onAddText}><Icon name="type" size={17} />텍스트 추가</button>
      {onAddGarmentText && (
        <>
          <button type="button" className="add-text-btn" onClick={onAddGarmentText} style={{ marginTop: 8 }}><Icon name="type" size={17} />옷 글자 덮기</button>
          <div className="panel-sub" style={{ marginTop: 8 }}>AI 컷의 뭉갠 글자 위에 정확한 글자를 얹어요. 정면·평평한 프린트에 잘 맞아요.</div>
        </>
      )}
      {!has ? (
        <div className="panel-sub" style={{ marginTop: 18 }}>위 버튼으로 텍스트를 추가하거나, 캔버스에서 텍스트를 클릭해 편집해요.</div>
      ) : (
        <>
          <PanelSection title="텍스트 박스" first>
            <div className="field-2up">
              <NumField iconText="가로" value={Math.round(el.w || 120)} min={1} max={10000}
                onChange={(w) => onChange({ w, ...(!isBubble && el.textSizing === 'auto' ? { textSizing: 'fixed' } : {}) })} />
              <NumField icon="rotate" labelText="회전" value={el.rotate || 0} min={-180} max={180} suffix="°" onChange={(rotate) => onChange({ rotate })} />
            </div>
            <div className="panel-sub" style={{ marginTop: 8 }}>텍스트는 좌우 가장자리로 폭을 조절하고, 회전은 여기서 정확히 입력할 수 있어요.</div>
          </PanelSection>

          <PanelSection title="타이포그래피">
            <MiniSelect value={s.font || 'Pretendard'} options={catalogs.fonts} onChange={(v) => setS({ font: v })} />
            <div className="field-2up" style={{ marginTop: 8 }}>
              <MiniSelect value={s.weight || 400} options={WEIGHTS} onChange={(v) => setS({ weight: v })} />
              <NumStepper value={s.size || 18} min={8} max={200} onChange={(v) => setS({ size: v })} />
            </div>
            <div className="field-2up" style={{ marginTop: 8 }}>
              <NumField icon="lineHeight" labelText="행간" value={s.lineHeight || Math.round((s.size || 18) * 1.4)} min={0} max={400} onChange={(v) => setS({ lineHeight: v })} />
              <NumField icon="letterSpacing" labelText="자간" value={s.tracking || 0} min={-5} max={20} onChange={(v) => setS({ tracking: v })} />
            </div>
            <div className="text-tool-row">
              <div className="seg-icons">
                {['left', 'center', 'right'].map((a) => <IconButton key={a} name={'align' + a[0].toUpperCase() + a.slice(1)} size="sm" active={(s.align || 'left') === a} onClick={() => setS({ align: a })} />)}
              </div>
              <div className="seg-icons">
                <IconButton name="bold" size="sm" active={s.weight >= 700} onClick={() => setS({ weight: s.weight >= 700 ? 400 : 700 })} />
                <IconButton name="italic" size="sm" active={s.italic} onClick={() => setS({ italic: !s.italic })} />
                <IconButton name="underline" size="sm" active={s.underline} onClick={() => setS({ underline: !s.underline })} />
                <IconButton name="strike" size="sm" active={s.strike} onClick={() => setS({ strike: !s.strike })} />
              </div>
            </div>
            <div className="text-tool-row">
              <span className="psec-mini">말머리</span>
              <div className="seg-icons">
                <IconButton name="minus" size="sm" active={!s.list || s.list === 'none'} onClick={() => setS({ list: 'none' })} title="없음" />
                <IconButton name="listBullet" size="sm" active={s.list === 'bullet'} onClick={() => setS({ list: 'bullet' })} title="글머리 기호" />
                <IconButton name="listOrdered" size="sm" active={s.list === 'ordered'} onClick={() => setS({ list: 'ordered' })} title="번호" />
              </div>
            </div>
          </PanelSection>

          <PanelSection title="글자 색상">
            <SwatchField value={s.color || '#0e0d14'} opacity={Math.round((s.opacity ?? 1) * 100)} palette={TEXT_PALETTE}
              onColor={(c) => setS({ color: c })} onOpacity={(v) => setS({ opacity: v / 100 })} />
          </PanelSection>

          {isBubble && (
            <>
              <PanelSection title="말풍선 배경">
                <SwatchField value={el.fill || '#ffffff'} opacity={Math.round((el.fillOpacity ?? 1) * 100)} palette={SHAPE_PALETTE}
                  onColor={(fill) => changeBubbleAppearance({ fill })} onOpacity={(value) => changeBubbleAppearance({ fillOpacity: value / 100 })} />
              </PanelSection>
              <PanelSection title="말풍선 모양">
                <RangeNumberControl label="둥근 모서리" value={el.radius ?? DEFAULT_BUBBLE_RADIUS} min={0} max={100}
                  onChange={(radius) => changeBubbleAppearance({ radius })} />
              </PanelSection>
              <PanelSection title="말풍선 테두리" actions={
                <button type="button" className="psec-act" title={hasBubbleStroke ? '테두리 제거' : '테두리 추가'}
                  onClick={() => changeBubbleAppearance({
                    stroke: hasBubbleStroke ? 'none' : DEFAULT_BUBBLE_STROKE,
                    strokeWidth: hasBubbleStroke ? bubbleStrokeWidth : Math.max(DEFAULT_BUBBLE_STROKE_WIDTH, bubbleStrokeWidth),
                  })}>
                  <Icon name={hasBubbleStroke ? 'minus' : 'plus'} size={15} />
                </button>
              }>
                {hasBubbleStroke ? (
                  <div className="bubble-border-controls">
                    <SwatchField value={bubbleStroke} palette={SHAPE_PALETTE} allowNone onColor={(stroke) => changeBubbleAppearance({ stroke })} />
                    <StrokeWidthControl value={bubbleStrokeWidth} onChange={(strokeWidth) => changeBubbleAppearance({ strokeWidth })} />
                  </div>
                ) : <div className="psec-empty">테두리 없음</div>}
              </PanelSection>
            </>
          )}

          <PanelSection title="하이라이트">
            <SwatchField value={s.bg || 'none'} palette={HL_PALETTE} allowNone onColor={(c) => setS({ bg: c })} />
          </PanelSection>
        </>
      )}
    </div>
  );
}

/* ---------- 프레임 ---------- */
const FRAME_LIBRARY_TABS = [
  { value: 'blank', label: '빈 프레임' },
  { value: 'example', label: '예시 프레임' },
  { value: 'guide', label: '안내 프레임' },
];

export function FramePanel({ onAdd, onDragStart, onDragEnd, recommendGender, onPickInfo }) {
  const [category, setCategory] = useState('blank');
  const frames = FRAME_LIBRARY_ITEMS.filter((frame) => (
    category === 'blank' ? !frame.template : frame.template
  ));
  return (
    <div>
      <PanelHead title="프레임" sub="종류를 고른 뒤 끌어 놓거나 클릭해 추가하세요." />
      <div className="frame-category-tabs">
        <UnderlineTabs options={FRAME_LIBRARY_TABS} value={category} onChange={setCategory} />
      </div>
      {category === 'guide' ? (
        <ContentPanel recommendGender={recommendGender} onPick={onPickInfo} showIntro={false}
          onDragStart={onDragStart} onDragEnd={onDragEnd} />
      ) : (
        <div className="frame-list">
          {frames.map((f) => (
            <div className="frame-item" key={f.id} onClick={() => onAdd(f)} draggable
              onDragStart={(e) => { e.dataTransfer.effectAllowed = 'copy'; e.dataTransfer.setData('text/frame', f.id); onDragStart && onDragStart(); }}
              onDragEnd={() => onDragEnd && onDragEnd()}>
              <div className={`frame-prev frame-layout-prev${f.preview ? ' template' : ''}`}>
                {f.slots.map((slot, i) => (
                  <i key={i} style={{
                    left: `${slot.x / 10}%`,
                    top: `${slot.y / f.h * 100}%`,
                    width: `${slot.w / 10}%`,
                    height: `${slot.h / f.h * 100}%`,
                    borderRadius: slot.radius ? `${Math.min(50, slot.radius / Math.min(slot.w, slot.h) * 100)}%` : undefined,
                    border: slot.stroke ? `${slot.strokeWidth || 2}px ${slot.dash || 'solid'} ${slot.stroke}` : undefined,
                    transform: slot.rotate ? `rotate(${slot.rotate}deg)` : undefined,
                  }}>
                    {slot.src && <img src={slot.src} alt="" loading="lazy" draggable={false} />}
                  </i>
                ))}
                {f.preview && <img src={f.preview} alt="" loading="lazy" draggable={false} />}
              </div>
              <div className="fl">{f.label}{f.recommended && <span className="frame-rec">추천</span>}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ---------- 오브젝트 (도형/선 추가 + 블록 배경) ---------- */
// 글리프는 캔버스 렌더와 같은 path(shapes.js)를 currentColor 로 그려 미리보기-실물 일치
function ShapeGlyph({ id }) {
  if (id === 'circle') return <span className="obj-prev circle" />;
  if (id === 'rect') return <span className="obj-prev square" />;
  const d = id === 'triangle' ? 'M50 8 L96 92 L4 92 Z' : SHAPE_D[id];
  return <svg className="obj-glyph" viewBox="0 0 100 100"><path d={d} fill="currentColor" /></svg>;
}
const BLOCK_BG_OPTS = [
  { c: '#ffffff', label: '흰색' }, { c: '#f5f5f5', label: '연회색' }, { c: '#0e0d14', label: '잉크' },
];
export function ShapePanel({ catalogs, onAdd, block, onBgChange }) {
  const dragStart = (e, type, id) => { e.dataTransfer.effectAllowed = 'copy'; e.dataTransfer.setData('text/object', `${type}:${id}`); };
  return (
    <div>
      {block && onBgChange && (
        <div style={{ marginBottom: 20 }}>
          <label className="lbl" style={{ marginBottom: 9 }}>블록 배경</label>
          <SwatchField value={block.bg || '#ffffff'} palette={BLOCK_BG_OPTS.map((o) => o.c)} opacity={Math.round((block.bgOpacity ?? 1) * 100)}
            onColor={(bg) => onBgChange(block.id, { bg })} onOpacity={(value) => onBgChange(block.id, { bgOpacity: value / 100 })} />
          <p className="hint" style={{ marginTop: 8 }}>배경만 투명해져요. 블록 안의 사진과 글자는 흐려지지 않아요.</p>
        </div>
      )}
      <PanelHead title="오브젝트" sub="클릭하면 블록 중앙에, 드래그하면 원하는 블록에 추가돼요." />
      <label className="lbl" style={{ marginBottom: 9 }}>추천 오브젝트</label>
      <div className="object-preset-list">
        {OBJECT_LIBRARY_ITEMS.map((item) => (
          <button className="object-preset-cell" key={item.id} draggable title={item.label}
            onClick={() => onAdd('preset', item.id)} onDragStart={(e) => dragStart(e, 'preset', item.id)}>
            <span className={`object-preset-glyph ${item.id}`}>{item.preview}</span>
            <span>{item.label}</span>
          </button>
        ))}
      </div>
      <label className="lbl" style={{ marginBottom: 9 }}>기본 도형</label>
      <div className="shape-list" style={{ marginBottom: 18 }}>
        {catalogs.shapes.map((s) => (
          <button className="shape-cell" key={s.id} title={s.label} draggable
            onClick={() => onAdd('shape', s.id)} onDragStart={(e) => dragStart(e, 'shape', s.id)}>
            <ShapeGlyph id={s.id} />
          </button>
        ))}
      </div>
      <label className="lbl" style={{ marginBottom: 9 }}>선</label>
      <div className="shape-list">
        {catalogs.lines.map((l) => (
          <button className="shape-cell" key={l.id} title={l.label} draggable
            onClick={() => onAdd('line', l.id)} onDragStart={(e) => dragStart(e, 'line', l.id)}>
            <span className="obj-prev line">
              <svg viewBox="0 0 38 16">
                <line x1={l.id === 'arrow-l' ? 7 : 1} y1="8" x2={l.id === 'arrow-r' ? 31 : 37} y2="8" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                {l.id === 'arrow-l' && <polyline points="8,3 2,8 8,13" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />}
                {l.id === 'arrow-r' && <polyline points="30,3 36,8 30,13" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />}
              </svg>
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}

/* ---------- 레이어 패널 ---------- */
function layerMeta(el) {
  if (el.type === 'image') return { icon: 'image', label: '이미지', thumb: el.src };
  if (el.type === 'text') return { icon: 'type', label: (el.text || '텍스트').replace(/\n/g, ' ').slice(0, 18) || '텍스트' };
  if (el.type === 'line') return { icon: 'minus', label: '선' };
  const names = { circle: '원', rect: '사각형', triangle: '삼각형', diamond: '마름모', star: '별', heart: '하트', hexagon: '육각형', bubble: '말풍선' };
  return { icon: 'shapes', label: names[el.shape] || '도형' };
}
export function LayerPanel({ block, selEls = [], embedded, onSelect, onReorder, onToggle }) {
  const [dragId, setDragId] = useState(null);
  const [overId, setOverId] = useState(null);
  if (!block) return <EmptyState icon="layers" title="블록을 선택하세요" desc="블록을 클릭하면 그 안의 레이어가 순서대로 나와요." />;
  const rows = block.elements.map((el, idx) => ({ el, idx })).filter(({ el }) => !el.system).reverse(); // 위가 최상단(맨 앞)
  return (
    <div>
      {!embedded && <PanelHead title="레이어" sub="위가 가장 앞이에요. 드래그로 순서를, 아이콘으로 표시·잠금을 바꿔요." />}
      {!block.elements.length ? (
        <div className="panel-sub" style={{ marginTop: 14 }}>이 블록에는 아직 요소가 없어요.</div>
      ) : (
        <div className="layer-list">
          {rows.map(({ el }) => {
            const m = layerMeta(el);
            const on = selEls.includes(el.id);
            return (
              <div key={el.id}
                className={`layer-row${on ? ' on' : ''}${dragId === el.id ? ' dragging' : ''}${overId === el.id ? ' over' : ''}${el.hidden ? ' is-hidden' : ''}`}
                draggable
                onDragStart={(e) => { e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/layer', el.id); setDragId(el.id); }}
                onDragEnd={() => { setDragId(null); setOverId(null); }}
                onDragOver={(e) => { if (dragId) { e.preventDefault(); setOverId(el.id); } }}
                onDrop={(e) => { e.preventDefault(); if (dragId && dragId !== el.id) onReorder(block.id, dragId, el.id); setDragId(null); setOverId(null); }}
                onClick={() => onSelect(block.id, el)}>
                <span className="lr-grip"><Icon name="gripV" size={15} /></span>
                <span className="lr-ico">{m.thumb ? <img src={m.thumb} alt="" /> : <Icon name={m.icon} size={15} />}</span>
                <span className="lr-label">{m.label}</span>
                <button type="button" className="lr-btn" title={el.hidden ? '표시' : '숨기기'} onClick={(e) => { e.stopPropagation(); onToggle(block.id, el.id, 'hidden'); }}><Icon name={el.hidden ? 'eyeOff' : 'eye'} size={15} /></button>
                <button type="button" className={`lr-btn${el.locked ? ' on' : ''}`} title={el.locked ? '잠금 해제' : '잠금'} onClick={(e) => { e.stopPropagation(); onToggle(block.id, el.id, 'locked'); }}><Icon name={el.locked ? 'lock' : 'unlock'} size={15} /></button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
