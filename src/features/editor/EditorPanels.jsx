/* =============================================================
   features/editor/EditorPanels.jsx — left-panel content per toolbar tab.
   Ported verbatim from reference/prototype/features/editor-panels.jsx.
   Only change: ES imports/exports (was window globals).
   ============================================================= */
import { useState, useEffect, useMemo, useRef } from 'react';
import { Icon, Button, IconButton, Chips, EmptyState, UploadPendingTile } from '@/components/ui.jsx';
import { UnderlineTabs, ColorDots, MoodGuide, OuterClosureIcon } from '@/features/storyboard/Storyboard.jsx';
import { ModelThumb } from '@/features/analysis/AnalysisForm.jsx';
import { SHAPE_D } from '@/features/editor/shapes.js';
import {
  ALL_CUT_TYPE_OPTIONS,
  inferContentRole,
} from '@/lib/storyboardTaxonomy.js';
import { hasSelectableGenerationExamples } from '@/lib/generationExamples.js';
import {
  detailDirectionFromExample,
  generationExampleStructuralRecipePatch,
} from '@/lib/storyboardExampleSelection.js';
import { thumbUrl } from '@/lib/imageCdn.js';
import { DEFAULT_BUBBLE_RADIUS, DEFAULT_BUBBLE_STROKE, DEFAULT_BUBBLE_STROKE_WIDTH, FRAME_LIBRARY_ITEMS, OBJECT_LIBRARY_ITEMS, WARDROBE_IMAGE_MIME, colorWithOpacity, encodeWardrobeImage, normalizeHexColor } from '@/features/editor/editorLibrary.js';
import { DEFAULT_EDITOR_COLOR_PRESETS, commitNumberDraft, hexToHsv, hsvToHex, speechBubblePath } from '@/features/editor/editorAppearance.js';
import { DEFAULT_TEXT_PRESET, TEXT_MUTED, TEXT_PRESETS, activeTextPreset, quickStylePatch, textPresetBox } from '@/features/editor/presets/textPresets.js';
import { TEXT_PRESET_DRAG_PREFIX } from '@/features/editor/editorImageDrop.js';
import { speechBubbleFitOptions } from '@/features/editor/editorBubbleFit.js';
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
        /* 트랙 자체가 결과를 보여준다 — 체커보드(=비침) 위에 현재 색이 왼쪽 0%에서
           오른쪽 100%로 차오른다. 브라우저 기본 슬라이더는 "무엇이 얼마나 투명해지는지"를
           숫자로만 말해서, 값을 옮기며 눈으로 맞추기 어려웠다(오너 8/16 미감 지적). */
        <label className="sf-opacity" style={{ '--sf-op-color': normalized || 'var(--fg-1)' }}>
          <span>투명도</span>
          <span className="sf-opacity-track">
            <input type="range" min="0" max="100" step="1" value={alpha} aria-label="투명도"
              onChange={(e) => onOpacity(Number(e.target.value))} />
          </span>
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

/* ---------- AI · 현재 이미지 수정 — 지금 고른 컷을 바탕으로 한 장 더 ---------- */
/* 서브탭 이름은 '현재 이미지 수정'이고, 그 안에 생성 방식이 둘 있다(오너 2026-08-16):
   ① 같은 장소 이미지 생성 — 아래에서 고른 방향·포즈·표정만 바꾸고 장소는 그대로
   ② 비슷한 컷 만들기 — 고른 것 없이 현재 컷과 비슷한 분위기로 한 장

   배경 변경은 뺐다: 장면을 통째로 다시 그리는 유일한 항목이라 옷 정체성이 흔들릴 위험이
   가장 큰데 이 경로에는 품질 검사(QC)가 없고, 프리셋 배경은 셀러가 콘티에서 고른 연출과
   무관해 페이지의 장소 일관성도 깨뜨린다. 배경을 안 건드리면 프롬프트의 freeze 계약
   (cut_vary_v1.txt)이 장소를 그대로 유지해 준다 = "같은 장소, 다른 위치".
   거리(풀샷·미디움샷)도 뺐다(오너 2026-08-16) — 같은 장소에서 자리를 옮기는 기능에
   '얼마나 크게 찍을지'가 섞이면 결과가 원본과 다른 컷처럼 보인다. */
const VARY_CATS = [
  { id: 'cut', label: '방향' },
  { id: 'pose', label: '포즈' },
  { id: 'face', label: '표정' },
];
function VaryPanel({ catalogs, source, onGenerate }) {
  const opts = catalogs.varyOptions || {};
  const [cat, setCat] = useState('cut');
  const [sel, setSel] = useState({});
  const [cutDir, setCutDir] = useState('keep'); // 방향 — 'keep' = 현재 유지
  const busyRef = useRef(false); // 같은 틱 더블클릭으로 생성이 2번 나가는 것 방지
  if (!source) {
    return <EmptyState icon="image" title="수정할 컷을 골라주세요" desc="캔버스나 의류 탭에서 이미지를 먼저 골라주세요." />;
  }
  // 소스 컷 종류 — AI 생성 컷은 생성 시 기록된 cutType 으로 안다. 직접 업로드는 미상이고,
  // 그때는 사람컷 대표값(styling)으로 가정한다. 셀러에게 되묻지 않는다(오너 8/16):
  // 아무것도 하기 전에 사진 분류부터 시키는 질문 카드였고, 답도 대개 이미 알고 있다.
  const srcType = source.cutType || null;
  const isProduct = srcType === 'product';
  // mirror 레시피 소스(ADR-0004): 방향 변경 없음, 포즈는 셀피 구도 자동이라 변형 대상 아님
  const isMirror = srcType === 'mirror';
  const dirOpts = isProduct ? catalogs.productDirections : catalogs.directions;
  // 제품컷엔 사람이 없다 — 포즈·표정은 성립하지 않는다. 거울컷은 방향도 포즈도 못 바꾸니
  // 표정만 남는다(거리를 뺀 뒤로 '방향' 칸이 통째로 비기 때문).
  const cats = isProduct ? VARY_CATS.filter((c) => c.id === 'cut')
    : isMirror ? VARY_CATS.filter((c) => c.id === 'face')
    : VARY_CATS;
  const safeCat = cats.some((c) => c.id === cat) ? cat : cats[0].id;
  const optLabel = (c, id) => (opts[c] || []).find((o) => o.id === id)?.label || id;
  const valLabel = (list, v) => (list || []).find((o) => o.value === v)?.label || v;
  // 칩/payload 순서 = 적용 우선순위 계약: 방향이 기준 → 포즈 → 표정이 그 위에 얹힌다
  const chips = [];
  if (cutDir && cutDir !== 'keep') chips.push({ key: 'dir', cat: '방향', type: 'direction', value: cutDir, label: valLabel(dirOpts, cutDir), clear: () => setCutDir('keep') });
  if (sel.pose) chips.push({ key: 'pose', cat: '포즈', type: 'pose', value: sel.pose, label: optLabel('pose', sel.pose), clear: () => setSel((s) => ({ ...s, pose: null })) });
  if (sel.face) chips.push({ key: 'face', cat: '표정', type: 'face', value: sel.face, label: optLabel('face', sel.face), clear: () => setSel((s) => ({ ...s, face: null })) });
  const n = chips.length;
  const hasChange = { pose: !!sel.pose, face: !!sel.face, cut: !!cutDir && cutDir !== 'keep' };
  const cost = catalogs.creditCosts?.editorImage ?? 1;
  const pickCard = (oid) => setSel((s) => ({ ...s, [safeCat]: s[safeCat] === oid ? null : oid }));
  const clearAll = () => { setSel({}); setCutDir('keep'); };
  /* 생성 방식 두 가지 — 서버 계약(§6)은 하나다. changes 배열이 곧 방식이다:
     고른 변경을 담아 보내면 '같은 장소 이미지 생성', 빈 배열이면 '비슷한 컷 만들기'.
     refBg 는 배경 변경을 뺀 뒤로 보내지 않는다(계약은 그대로라 생략만 하면 된다). */
  const runGenerate = (changes) => {
    if (busyRef.current) return;
    busyRef.current = true; // 곧 의류 탭으로 전환되며 패널이 언마운트 — 같은 틱 더블클릭만 방어
    onGenerate({
      // 변형 대상 = 현재 변형 소스(캔버스 요소 또는 의류 이미지). cutType 미상이면 모델 착용 컷(styling)으로 가정.
      source: { id: source.id, src: source.src, cutType: srcType || 'styling' },
      changes,
    });
  };
  const generateSamePlace = () => runGenerate(chips.map((c) => ({ type: c.type, value: c.value, label: c.label })));
  const generateSimilar = () => runGenerate([]);
  const catLabel = VARY_CATS.find((c) => c.id === safeCat).label;
  return (
    <div>
      <div className="vary-tabs">
        <UnderlineTabs value={safeCat} onChange={setCat}
          options={cats.map((c) => ({ value: c.id, label: <>{c.label}{hasChange[c.id] && <span className="vary-dot" />}</> }))} />
      </div>
      {safeCat === 'cut' ? (
        /* Chips 는 선택된 칩 재클릭 시 null 을 보냄 → '변경 없음'(keep) 으로 복귀시킨다 */
        <div className="insp-sec"><label className="lbl">보는 방향</label>
          <Chips options={[{ value: 'keep', label: '변경 없음' }, ...dirOpts]} value={cutDir} onChange={(v) => setCutDir(v || 'keep')} /></div>
      ) : (
        <div className="insp-sec">
          <label className="lbl">{catLabel} 고르기</label>
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
      {/* 같은 장소 이미지 생성 — 아래 CTA 와 **모양·크기·테두리가 같고 색만 반대**다
          (오너 2026-08-16). 그래서 variant 는 primary 그대로 두고 채움/글자만 뒤집는다 —
          ghost 로 두면 높이·라운드·회전 테두리가 달라져 다른 종류의 버튼처럼 보인다. */}
      <Button variant="primary" block icon="sparkles" className="btn-glowring btn-invert" onClick={generateSamePlace} style={{ marginTop: 14 }}>
        {n > 0 ? `같은 장소 이미지 생성 · ${n}개 변경 · ${cost} 크레딧` : `같은 장소 이미지 생성 · ${cost} 크레딧`}
      </Button>
      <Button variant="primary" block icon="sparkles" className="btn-glowring" onClick={generateSimilar} style={{ marginTop: 8 }}>
        {`비슷한 컷 만들기 · ${cost} 크레딧`}
      </Button>
      <p className="hint" style={{ marginTop: 10 }}>
        {n > 0 ? '고른 변경이 한 장의 새 컷에 함께 반영돼요. 장소는 그대로예요. 기존 이미지는 유지되고 새 컷은 의류 탭에 추가돼요.'
          : '위쪽은 장소를 그대로 두고 방향·포즈·표정만 바꿔요. 아래쪽은 현재 컷과 비슷한 분위기로 한 장 더 만들어요. 새 컷은 의류 탭에 추가돼요.'}
      </p>
    </div>
  );
}

/* ---------- AI ---------- */
const NEW_CUT_DEFAULT_SHOT = { styling: 'full', horizon: 'full', mirror: 'full', product: 'ghost' };
export function AIPanel({ catalogs, fmModels, account, colorOpts = [], detailColorOpts = [], clothingType = 'top', matchClothing = [], exampleGender = null, varySource, onGenerate, onVaryGenerate, onPickMoodRef }) {
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
  // 컷 탭의 선택은 원래 레시피로 유지한다. 거울 예시는 구조 패치만 파생해 얹으므로
  // 일반 예시를 다시 고르면 별도 상태 복구 없이 탭이 가리키던 레시피로 돌아간다.
  const selectedExample = (catalogs.genExamples || []).find((example) => example.id === exampleId) || null;
  const galleryCutType = cutType;
  const galleryIsProduct = galleryCutType === 'product';
  const galleryDirectionOptions = galleryIsProduct ? catalogs.productDirections : catalogs.directions;
  const galleryShotOptions = galleryIsProduct ? catalogs.productShotTypes : catalogs.shotTypes;
  const galleryDirectionVal = galleryDirectionOptions.some((option) => option.value === dir)
    ? dir : galleryDirectionOptions[0].value;
  const galleryShotVal = galleryShotOptions.some((option) => option.value === shot)
    ? shot : galleryShotOptions[0].value;
  const baseRecipe = {
    source: 'ai',
    contentRole: inferContentRole({ source: 'ai', cutType: galleryCutType, shot: galleryShotVal }),
    cutType: galleryCutType,
    direction: galleryDirectionVal,
    shot: galleryShotVal,
  };
  const effectiveRecipe = {
    ...baseRecipe,
    ...generationExampleStructuralRecipePatch(baseRecipe, selectedExample),
  };
  const effectiveCutType = effectiveRecipe.cutType;
  const isProduct = effectiveCutType === 'product';
  const isMirror = effectiveCutType === 'mirror'; // mirror 레시피(ADR-0004): 방향 없음, 샷 full/medium만
  const effectiveDirectionOptions = isProduct ? catalogs.productDirections : catalogs.directions;
  const effectiveShotOptions = isProduct ? catalogs.productShotTypes : catalogs.shotTypes;
  const effectiveDirectionVal = effectiveDirectionOptions.some((option) => option.value === effectiveRecipe.direction)
    ? effectiveRecipe.direction : effectiveDirectionOptions[0].value;
  const effectiveShotVal = effectiveShotOptions.some((option) => option.value === effectiveRecipe.shot)
    ? effectiveRecipe.shot : effectiveShotOptions[0].value;
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
  // isDetail 은 검증된 effectiveShotVal 기준 — raw shot 을 읽으면 카탈로그 폴백 시 UI와 전송값이 어긋난다.
  const isDetail = isProduct && effectiveShotVal === 'detail';
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
  const hasSelectableExamples = (cut, shotValue) => hasSelectableGenerationExamples(catalogs.genExamples, {
    cutType: cut, shot: shotValue, clothingType, gender: modelGender,
    appendSetOnly: cut !== 'product',
    appendMirror: cut === 'styling',
  });
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
          <MoodGuide catalogs={catalogs} cut={galleryCutType} blockCutType={effectiveCutType}
            direction={galleryDirectionVal} shot={galleryShotVal}
            shotOptions={galleryIsProduct ? galleryShotOptions : null}
            onShotChange={(v) => {
              setShot(v); setExampleId(null); setRefScope('all');
              // 고스트→디테일 전환 시 이전 '뒷면'이 숨은 채 BackDetail 근거로 새지 않게 — 콘티보드 동일 가드(Codex 리뷰 P1).
              if (isProduct) setDir('front');
            }} clothingType={clothingType} gender={modelGender}
            includeMirrorExamples={galleryCutType === 'styling'}
            exampleId={exampleId} onExampleChange={selectExample}
            refScope={refScope} onRefScopeChange={setRefScope}
            refs={refImages} onRefsChange={setRefImages} onPickRef={onPickMoodRef} />
          {/* 디테일 컷은 방향 UI 없음 — 선택한 생성예시의 direction 라벨이 내부 결정 (selectExample) */}
          {!isMirror && !isDetail && <div className="insp-sec"><label className="lbl">방향</label><Chips className="oneline" options={effectiveDirectionOptions} value={effectiveDirectionVal} onChange={setDir} /></div>}

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
            contentRole: effectiveRecipe.contentRole,
            colorId: colorVal, cutType: effectiveCutType, direction: isMirror ? null : effectiveDirectionVal, shot: effectiveShotVal, modelId: model, exampleId, refScope,
            outerClosureState: showOuterClosure ? outerClosure : null,
            matchIds: isProduct ? [] : matchIds,
            refImages: refImages.map((r) => r?.url || r),                  // 표시용 URL (mock 계약 유지)
            refAssetIds: refImages.map((r) => r?.assetId).filter(Boolean), // 서버 첨부용 asset id (계약 §6)
          })}>새 이미지 생성 · {catalogs.creditCosts?.editorImage ?? 1} 크레딧</Button>
        </div>
      ) : (
        /* key=소스 id — 변형 대상이 바뀌면 패널 상태(선택/트레이/결과)를 통째로 초기화해 이미지 간 누수를 차단 */
        <VaryPanel key={varySource ? varySource.id : 'none'} catalogs={catalogs} source={varySource} onGenerate={onVaryGenerate} />
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
      {pendingSlot && (
        <div className="ward-fill-banner" role="status" aria-live="polite">
          <span className="ward-fill-banner-icon"><Icon name="imagePlus" size={18} /></span>
          <span>
            <strong>프레임에 넣을 사진을 선택하세요</strong>
            <small>아래 사진을 한 번 누르면 바로 들어가요.</small>
          </span>
        </div>
      )}
      <Button variant="ghost" block icon="upload" onClick={onUpload} disabled={uploading} style={{ marginBottom: uploading ? 8 : 16 }}>직접 이미지 업로드하기</Button>
      {/* 업로드 중에는 사진이 들어올 자리를 로고 타일로 먼저 보여준다 — 입력 페이지와 같은
          얼굴(오너 8/15). 상태를 늘리지 않고 uploading 플래그만으로 렌더한다. */}
      {uploading && (
        <div className="wardrobe-grid" style={{ marginBottom: 16 }} role="status" aria-live="polite">
          <UploadPendingTile className="ward-cell" />
          <span className="sr-only">의류 이미지를 불러오는 중이에요</span>
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
                  // slow = 화면 대기(3분)를 넘겨 백그라운드 추적 중 — 실패가 아니라 진행 중이다.
                  if (im.loading) return (
                    <div className={`ward-cell loading${im.slow ? ' slow' : ''}`} key={im.id}
                      title={im.slow ? '아직 만들어지고 있어요 — 완성되면 여기에 나타나요' : '만드는 중이에요'}>
                      <Icon name="loader" size={18} className="spin" style={{ color: 'var(--fg-3)' }} />
                      {im.slow && <small>조금 더 걸려요</small>}
                    </div>
                  );
                  const used = Boolean(isImageUsed?.(im));
                  return (
                    <div className={`ward-cell${im.fresh ? ' fresh' : ''}${pendingSlot ? ' select-target' : ''}`} key={im.id} onClick={(e) => { const image = e.currentTarget.querySelector('img'); onInsert({ ...im, width: image?.naturalWidth || im.width, height: image?.naturalHeight || im.height }); }} title={pendingSlot ? '이 사진을 프레임에 넣기' : '클릭하거나 프레임으로 끌어 넣기'}
                      draggable onDragStart={(e) => { const image = e.currentTarget.querySelector('img'); e.dataTransfer.effectAllowed = 'copy'; e.dataTransfer.setData(WARDROBE_IMAGE_MIME, encodeWardrobeImage(im, { width: image?.naturalWidth, height: image?.naturalHeight })); onImageDragStart?.(); }}
                      onDragEnd={() => onImageDragEnd?.()}
                      onAnimationEnd={im.fresh ? () => onFreshSeen && onFreshSeen(im.id) : undefined}>
                      <img src={thumbUrl(im.src, 240)} alt="" loading="lazy" decoding="async" />
                      {pendingSlot && <span className="ward-pick-check" aria-hidden="true"><Icon name="check" size={15} /></span>}
                      {/* 삭제는 앱 관례대로 우측 위 X — 휴지통 대신(오너 8/15). 네이티브
                          disabled 를 쓰지 않는 이유: 클릭이 통과해야 "사용 중이라 못 지워요"
                          안내가 뜬다. draggable=false·pointerdown 차단은 셀 드래그 방지용. */}
                      <button type="button" className={`ward-rm${used ? ' disabled' : ''}`} draggable={false}
                        aria-label={used ? '현재 에디팅에 사용 중인 사진' : '의류 사진 삭제'} aria-disabled={used}
                        title={used ? '현재 에디팅에 사용 중이라 삭제할 수 없어요' : '사진 삭제'}
                        onPointerDown={(e) => e.stopPropagation()}
                        onClick={(e) => { e.stopPropagation(); onDeleteImage(im); }}>
                        <Icon name="x" size={12} />
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
export function ImagePanel({ el, onChange, onLayer, onCrop, lock = true, onLock }) {
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
      {/* 'AI로 컷 변형하기' 점프 버튼 제거(오너 8/15) — 같은 기능은 좌측 AI 탭(선택된 컷이
          자동으로 수정 대상이 된다)과 의류 타일의 'AI 편집' 뱃지로 계속 갈 수 있다. */}
      {/* '프레임 이미지' 액션 묶음(교체·자르기·초기화·빼내기) 제거(오너 8/16).
          교체·빼내기는 빈 칸 클릭과 Delete 로, 자르기는 아래 '자르기' 섹션과 더블클릭으로
          이미 되는 일이라 같은 화면에 두 벌이 있었다. */}
      <PanelSection title={isLine ? '선 크기' : '이미지 크기'} first>
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
/* 회색 스와치는 프리셋 회색과 같은 값 — 다르면 "같은 회색으로 되돌릴" 길이 없다. */
const TEXT_PALETTE = ['#0e0d14', TEXT_MUTED, '#ffffff', '#4f88c9', '#d92d20', '#067647'];
const HL_PALETTE = ['#fef3c7', '#dbeafe', '#dcfce7', '#fee2e2', '#f3f4f6', '#0e0d14'];
const WEIGHTS = [{ value: 300, label: 'Light' }, { value: 400, label: 'Regular' }, { value: 500, label: 'Medium' }, { value: 600, label: 'SemiBold' }, { value: 700, label: 'Bold' }];
/* 텍스트 프리셋 드래그 시작. 놓일 자리 미리보기는 **블록 안에서만** 그린다(.text-drop-ghost) —
   커서를 따라다니는 그림까지 같은 문구로 그렸더니 블록 위에서 글자가 둘로 겹쳐 보였다
   (오너 2026-08-16). 그래서 커서 그림은 투명한 1px 로 비우고, 위치·크기를 정확히 말해 주는
   블록 안 상자 하나만 남긴다(그쪽은 블록 좌표계라 배율·스크롤과 항상 일치한다). */
function startTextPresetDrag(event, presetKey) {
  event.dataTransfer.effectAllowed = 'copy';
  event.dataTransfer.setData('text/object', `text:${presetKey}`);
  // 드래그 중에는 getData 가 막혀 있고 types 만 읽을 수 있다 — 어떤 프리셋인지 블록이
  // 알아야 놓일 자리 미리보기를 정확히 그리므로 종류를 타입 이름에 실어 보낸다.
  event.dataTransfer.setData(`${TEXT_PRESET_DRAG_PREFIX}${presetKey}`, presetKey);
  if (typeof document === 'undefined' || !event.dataTransfer.setDragImage) return;
  const blank = document.createElement('div');
  blank.className = 'text-drag-ghost';
  document.body.appendChild(blank);
  event.dataTransfer.setDragImage(blank, 0, 0);
  // 드래그가 시작된 뒤에 지워야 브라우저가 스냅샷을 뜬 다음이 된다.
  setTimeout(() => blank.remove(), 0);
}

export function TextPanel({ el, catalogs, onChange, onBubbleAppearanceChange, onLayer, onAddText }) {
  const has = el && el.type === 'text';
  const isBubble = has && el.shape === 'bubble';
  const s = (has && el.style) || {};
  const setS = (p) => onChange({ style: { ...s, ...p } });
  const changeBubbleAppearance = onBubbleAppearanceChange || onChange;
  const bubbleStroke = isBubble && el.stroke !== 'none' ? (el.stroke || DEFAULT_BUBBLE_STROKE) : 'none';
  const bubbleStrokeWidth = Number.isFinite(Number(el?.strokeWidth)) ? Number(el.strokeWidth) : DEFAULT_BUBBLE_STROKE_WIDTH;
  const hasBubbleStroke = isBubble && bubbleStroke !== 'none';
  // 선택 중에는 추가 목록을 맨 위에 두지 않는다 — 같은 4개 이름이 "추가"와 "스타일 전환"
  // 두 의미로 나란히 보이면 스타일을 바꾸려다 빈 요소를 새로 만드는 오클릭이 난다(리뷰 반영).
  // 대신 아래쪽 "새로 추가" 섹션으로 내려 추가 수단 자체는 항상 남긴다.
  const activePresetKey = has ? activeTextPreset(s) : null;
  const presetList = (
    <div className="text-preset-list">
      {/* 누르면 자동 자리, 끌어다 놓으면 놓은 자리 — 오브젝트·프레임과 같은 'text/object'
          운반 형식이라 블록이 이미 갖고 있는 드롭 하이라이트를 그대로 탄다(오너 8/16). */}
      {TEXT_PRESETS.map((p) => (
        <button key={p.key} type="button" className="text-preset-item" draggable
          aria-label={`${p.label} 추가`} title={`${p.label} — 누르면 추가, 끌어다 놓으면 그 자리에`}
          onDragStart={(e) => startTextPresetDrag(e, p.key)}
          onClick={() => onAddText?.(p.key)}>
          {/* 축소판 스타일은 프리셋 데이터에서 직접 그린다 — CSS에 복제하면 값이 갈라진다 */}
          <span className="tp-sample" style={{ fontSize: p.previewSize, fontWeight: p.style.weight, color: p.style.color, letterSpacing: p.style.tracking }}>{p.sample || p.label}</span>
          <span className="tp-meta">{p.style.size}px<br />{p.hint}</span>
        </button>
      ))}
      {/* 그냥 한 줄 넣고 싶을 때 — 예전 '텍스트 추가' 버튼 그대로다(오너 8/16). 스타일을 골라
          주지 않고 기본 프리셋으로 만든다: 크기를 이름표에 박아 두면 그것도 '고정 스타일'이
          하나 더 생기는 셈이라 오너가 물렸던 방식이 된다. 위 카드 셋과 같은 카드 시각·같은
          조작(누르기/끌기)이라 넷이 한 덩어리로 읽힌다. */}
      <button type="button" className="add-text-btn" draggable
        title="텍스트 추가 — 누르면 추가, 끌어다 놓으면 그 자리에"
        onDragStart={(e) => startTextPresetDrag(e, DEFAULT_TEXT_PRESET)}
        onClick={() => onAddText?.()}>
        <Icon name="type" size={17} />텍스트 추가
      </button>
    </div>
  );
  return (
    <div className="fig-panel">
      {!has ? (
        <>
          {presetList}
          <div className="panel-sub" style={{ marginTop: 14 }}>누르면 바로 입력할 수 있고, 끌어다 놓으면 원하는 자리에 들어가요. 캔버스의 텍스트를 클릭하면 편집해요.</div>
        </>
      ) : (
        <>
          {/* 말풍선은 제외 — 자체 튜닝된 행간·색을 칩이 덮으면 짝 말풍선과 어긋난다.
              칩 시각은 앱 공용 Chips — 활성 칩 재클릭은 가드로 무시(불필요한 히스토리 방지). */}
          {!isBubble && (
            <PanelSection title="빠른 스타일" first>
              <Chips className="quad-chips" allowDeselect={false}
                options={TEXT_PRESETS.map((p) => ({ value: p.key, label: p.label }))}
                value={activePresetKey}
                onChange={(key) => { if (key && key !== activePresetKey) setS(quickStylePatch(key)); }} />
              <div className="panel-sub" style={{ marginTop: 8, marginBottom: 0 }}>내용은 그대로, 크기·굵기·색만 한 번에 바뀌어요.</div>
            </PanelSection>
          )}
          <PanelSection title="텍스트 박스" first={isBubble}>
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

          {/* 추가 수단은 선택 중에도 남긴다 — 없애면 소제목을 쓴 직후 설명글을 붙일 방법이
              "빈 곳을 클릭해 선택 해제"뿐이라 발견 불가능하다(리뷰 반영). 제목으로 칩과 구분. */}
          <PanelSection title="새로 추가">
            {presetList}
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
      {/* 제목은 좌측 패널 래퍼가 그린다(Editor.jsx) — 여기서 또 그리면 "프레임 프레임"이 된다 */}
      <div className="panel-sub">종류를 고른 뒤 끌어 놓거나 클릭해 추가하세요.</div>
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
                {!f.preview && (f.elements || []).filter((element) => element.type === 'text').map((element, index) => (
                  <b className="frame-native-copy" key={`${element.text}-${index}`} style={{
                    left: `${element.x / 10}%`,
                    top: `${element.y / f.h * 100}%`,
                    width: `${element.w / 10}%`,
                    height: `${element.h / f.h * 100}%`,
                    fontSize: `${Math.max(3, (element.style?.size || 20) / 8)}px`,
                    fontWeight: element.style?.weight || 400,
                    textAlign: element.style?.align || 'left',
                  }}>{element.text}</b>
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

/* ---------- 오브젝트 (추천 프리셋 / 도형·선 탭 + 블록 배경) ----------
   2026-08-14 개편: 제목이 맨 위, 추가할 것은 언더라인 탭 두 갈래(추천/도형·선),
   블록 배경은 구분선 아래 맨 밑. 미리보기는 전부 같은 굵기의 선화(line-art) 한 언어. */
// 글리프는 캔버스 렌더와 같은 path(shapes.js) — 생성 기본값(흰 채움+잉크 테두리)과 같은 모습
function ShapeGlyph({ id }) {
  if (id === 'circle') return <svg className="obj-glyph" viewBox="0 0 100 100"><circle cx="50" cy="50" r="44" fill="#fff" stroke="currentColor" strokeWidth="6" /></svg>;
  if (id === 'rect') return <svg className="obj-glyph" viewBox="0 0 100 100"><rect x="8" y="8" width="84" height="84" rx="14" fill="#fff" stroke="currentColor" strokeWidth="6" /></svg>;
  const d = id === 'triangle' ? 'M50 8 L96 92 L4 92 Z' : SHAPE_D[id];
  return <svg className="obj-glyph" viewBox="0 0 100 100"><path d={d} fill="#fff" stroke="currentColor" strokeWidth="6" strokeLinejoin="round" /></svg>;
}
/* 추천 오브젝트 아이콘 — 오브젝트의 성격을 한 글자짜리 라벨 칩으로 압축한 글리프.
   실물을 그대로 축소한 미니어처도 해 봤지만(8/16 오전), 132×62 칸에서는 글자가 뭉개져
   무엇인지 알기 어려웠다. 오너가 지목한 예전 아이콘으로 되돌린다(8/16) — 라벨 문구는
   editorLibrary 의 item.preview 가 정본이고, 칩 모양만 CSS 가 오브젝트별로 입힌다. */
const OBJECT_PANEL_TABS = [
  { value: 'preset', label: '추천 오브젝트' },
  { value: 'shape', label: '도형·선' },
];
const BLOCK_BG_OPTS = [
  { c: '#ffffff', label: '흰색' }, { c: '#f5f5f5', label: '연회색' }, { c: '#0e0d14', label: '잉크' },
];
export function ShapePanel({ catalogs, onAdd, block, onBgChange }) {
  const [tab, setTab] = useState('preset');
  const dragStart = (e, type, id) => { e.dataTransfer.effectAllowed = 'copy'; e.dataTransfer.setData('text/object', `${type}:${id}`); };
  return (
    <div>
      {/* 제목은 좌측 패널 래퍼가 그린다(Editor.jsx) — 중복 방지 */}
      <div className="panel-sub">클릭하면 블록 중앙에, 드래그하면 원하는 자리에 놓여요.</div>
      <UnderlineTabs options={OBJECT_PANEL_TABS} value={tab} onChange={setTab} />
      {tab === 'preset' ? (
        <div className="object-preset-list" style={{ marginTop: 16 }}>
          {OBJECT_LIBRARY_ITEMS.map((item) => (
            <button className="object-preset-cell" key={item.id} draggable title={item.label}
              onClick={() => onAdd('preset', item.id)} onDragStart={(e) => dragStart(e, 'preset', item.id)}>
              <span className={`object-preset-glyph ${item.id}`}>{item.preview}</span>
              <span className="object-preset-name">{item.label}</span>
            </button>
          ))}
        </div>
      ) : (
        <div style={{ marginTop: 16 }}>
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
          <div className="shape-list line3">
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
      )}
      {block && onBgChange && (
        <div className="obj-bg-sect">
          <label className="lbl" style={{ marginBottom: 9 }}>블록 배경 <span className="obj-bg-scope">선택한 블록에만 적용</span></label>
          <SwatchField value={block.bg || '#ffffff'} palette={BLOCK_BG_OPTS.map((o) => o.c)} opacity={Math.round((block.bgOpacity ?? 1) * 100)}
            onColor={(bg) => onBgChange(block.id, { bg })} onOpacity={(value) => onBgChange(block.id, { bgOpacity: value / 100 })} />
          <p className="hint" style={{ marginTop: 8 }}>배경만 투명해져요 — 사진과 글자는 그대로.</p>
        </div>
      )}
    </div>
  );
}

/* ---------- 레이어 패널 ---------- */
function layerMeta(el) {
  if (el.type === 'image') return { icon: 'image', label: '이미지', thumb: thumbUrl(el.src, 64) };  // .lr-ico 28px × DPR2
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
                /* 드롭 허용 판정은 React 상태(dragId)가 아니라 드래그 데이터로 한다 —
                   상태가 아직 커밋되기 전이면 preventDefault 를 건너뛰어 drop 자체가
                   막히고, 끌어다 놔도 아무 일이 없는 것처럼 보인다. */
                onDragOver={(e) => {
                  if (![...e.dataTransfer.types].includes('text/layer')) return;
                  e.preventDefault();
                  setOverId(el.id);
                }}
                onDrop={(e) => {
                  e.preventDefault();
                  const fromId = e.dataTransfer.getData('text/layer') || dragId;
                  if (fromId && fromId !== el.id) onReorder(block.id, fromId, el.id);
                  setDragId(null); setOverId(null);
                }}
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
