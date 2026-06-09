/* =============================================================
   features/editor-panels.jsx — left-panel content per toolbar tab.
   Exposes AIPanel / WardrobePanel / ImagePanel / TextPanel /
   FramePanel / ShapePanel.
   ============================================================= */
const { useState, useEffect, useRef } = React;

function PanelHead({ title, sub }) {
  return <><div className="panel-h">{title}</div>{sub && <div className="panel-sub">{sub}</div>}</>;
}

/* ---------- shared input atoms (used by 이미지 / 텍스트 props) ---------- */
// −  value  +  stepper with editable number (NumStepper — distinct from the wizard's Stepper)
function NumStepper({ value, min = 0, max = 9999, step = 1, onChange }) {
  const clamp = (v) => Math.min(max, Math.max(min, v));
  return (
    <div className="num-stepper">
      <button type="button" onClick={() => onChange(clamp(+(value - step).toFixed(2)))}><Icon name="minus" size={15} /></button>
      <input value={value} onChange={(e) => { const v = parseFloat(e.target.value); if (!isNaN(v)) onChange(clamp(v)); }} />
      <button type="button" onClick={() => onChange(clamp(+(value + step).toFixed(2)))}><Icon name="plus" size={15} /></button>
    </div>
  );
}
// icon (or short text) + editable number, optional suffix; labelText shows a word before the value
function NumField({ icon, iconText, labelText, value, min = -9999, max = 9999, onChange, suffix }) {
  return (
    <label className="numfield" title={labelText || undefined}>
      <span className="nf-ico">{iconText || <Icon name={icon} size={15} />}</span>
      {labelText && <span className="nf-label">{labelText}</span>}
      <input value={value} onChange={(e) => { const v = parseFloat(e.target.value); if (!isNaN(v)) onChange(Math.min(max, Math.max(min, v))); }} />
      {suffix && <span className="nf-suf">{suffix}</span>}
    </label>
  );
}
// compact dropdown
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
// swatch + opacity, with a "none" option (used for 폰트/배경 색상)
function ColorField({ value, opacity = 100, palette, allowNone, onColor, onOpacity }) {
  const isNone = allowNone && (!value || value === 'none');
  return (
    <div className="colorfield">
      <div className="cf-swatches">
        {allowNone && (
          <button type="button" className={`cf-sw cf-none${isNone ? ' on' : ''}`} title="없음" onClick={() => onColor('none')}><Icon name="ban" size={15} /></button>
        )}
        {palette.map((c) => (
          <button type="button" key={c} className={`cf-sw${value === c ? ' on' : ''}`} style={{ background: c }} onClick={() => onColor(c)} />
        ))}
      </div>
      {onOpacity && <NumField iconText="%" value={opacity} min={0} max={100} onChange={onOpacity} />}
    </div>
  );
}

/* ---------- Figma-style sectioned inspector atoms ---------- */
// a titled section with a hairline divider + optional header actions
function PanelSection({ title, actions, first, children }) {
  return (
    <div className={`psec${first ? ' first' : ''}`}>
      {title && <div className="psec-head"><span className="psec-title">{title}</span>{actions && <div className="psec-actions">{actions}</div>}</div>}
      {children}
    </div>
  );
}
// Figma fill/stroke row: swatch (opens palette) + hex label + opacity% + visibility eye.
// thumb = an image preview shown in the swatch slot (not clickable; opacity-only — used for 이미지 채움).
function SwatchField({ value, palette, opacity, allowNone, thumb, onColor, onOpacity, visible = true, onToggleVisible }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    const h = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    window.addEventListener('mousedown', h); return () => window.removeEventListener('mousedown', h);
  }, []);
  const isNone = allowNone && (!value || value === 'none');
  const hex = thumb ? '이미지' : isNone ? '없음' : (value || '').replace('#', '').toUpperCase();
  return (
    <div className="swatchfield" ref={ref}>
      <div className="sf-main">
        {thumb ? (
          <span className="sf-swatch sf-thumb"><img src={thumb} alt="" /></span>
        ) : (
          <button type="button" className={`sf-swatch${isNone ? ' none' : ''}`} style={isNone ? undefined : { background: value }} onClick={() => setOpen((o) => !o)} title="색상 선택">
            {isNone && <Icon name="ban" size={13} />}
          </button>
        )}
        <span className="sf-hex">{hex}</span>
        {onOpacity && !isNone && <span className="sf-op"><input value={Math.round(opacity)} onChange={(e) => { const v = parseFloat(e.target.value); if (!isNaN(v)) onOpacity(Math.min(100, Math.max(0, v))); }} /><i>%</i></span>}
        {onToggleVisible && <button type="button" className="sf-eye" onClick={onToggleVisible} title={visible ? '숨기기' : '표시'}><Icon name={visible ? 'eye' : 'eyeOff'} size={15} /></button>}
      </div>
      {open && !thumb && (
        <div className="sf-pop">
          {allowNone && <button type="button" className={`sf-po none${isNone ? ' on' : ''}`} title="없음" onClick={() => { onColor('none'); setOpen(false); }}><Icon name="ban" size={14} /></button>}
          {palette.map((c) => (
            <button type="button" key={c} className={`sf-po${value === c ? ' on' : ''}`} style={{ background: c }} onClick={() => { onColor(c); setOpen(false); }} />
          ))}
        </div>
      )}
    </div>
  );
}

/* ---------- AI ---------- */
function AIPanel({ catalogs, account, colorOpts = [], selectedEl, onGenerate, onVary }) {
  const [tab, setTab] = useState('new');
  const [cut, setCut] = useState('horizon');
  const [dir, setDir] = useState('front');
  const [shot, setShot] = useState('full');
  const [color, setColor] = useState(null);
  // 모델 기본값 = 첫 화면에서 선택한 모델 (PRD §11.8)
  const initialModel = (catalogs.models || []).find((m) => m.recommended) || (catalogs.models || [])[0];
  const [model, setModel] = useState(initialModel?.id || 'mA');
  // 색상 원은 입력 페이지에서 고른 색상만; 기본 선택은 첫 색상
  const colorVal = color || colorOpts[0]?.id || null;
  // 선택 색상 → 의류 탭의 색상 그룹으로 매핑 (색상 1 / 색상 2 / 기타)
  const colorIdx = Math.max(0, colorOpts.findIndex((c) => c.id === colorVal));
  const genGroup = colorIdx === 0 ? '색상 1' : colorIdx === 1 ? '색상 2' : '기타';
  const isProduct = cut === 'product';
  // 모델 토글 상태 (열면 패널이 모델 카드+생성 버튼까지 따라 스크롤)
  const [modelOpen, setModelOpen] = useState(false);
  const modelRef = useRef(null);
  // 일부 오버플로 컨테이너에서 native smooth 스크롤이 무시돼 직접 ease 애니메이션
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
        // 보장용 — 애니메이션 종료 후 정확히 맞춤 (rAF가 멈춘 환경에서도 최종 위치 보정)
        setTimeout(() => { p.scrollTop = p.scrollHeight; }, 320);
      }
    }
  };
  // 제품컷은 방향(앞면/뒷면)·샷(고스트/행거/플랫레이) 옵션이 달라짐 — 콘티보드와 동일 (PRD §9, §11)
  const dirOpts = isProduct ? [{ value: 'front', label: '앞면' }, { value: 'back', label: '뒷면' }] : catalogs.directions;
  const shotOpts = isProduct ? [{ value: 'ghost', label: '고스트컷' }, { value: 'hanger', label: '행거컷' }, { value: 'flatlay', label: '플랫레이샷' }] : catalogs.shotTypes;
  const dirVal = dirOpts.some((o) => o.value === dir) ? dir : dirOpts[0].value;
  const shotVal = shotOpts.some((o) => o.value === shot) ? shot : shotOpts[0].value;
  return (
    <div>
      <div className="seg" data-idx={tab === 'vary' ? 1 : 0}>
        <button className={tab === 'new' ? 'on' : ''} onClick={() => setTab('new')}>새 컷 추가</button>
        <button className={tab === 'vary' ? 'on' : ''} onClick={() => setTab('vary')}>현재 컷 변형</button>
      </div>
      {tab === 'new' ? (
        <div>
          <div className="insp-sec"><label className="lbl">컷 종류</label>
            <UnderlineTabs options={[{ value: 'horizon', label: '호리존컷' }, { value: 'daily', label: '일상컷' }, { value: 'product', label: '제품컷' }]} value={cut} onChange={(v) => setCut(v)} /></div>
          <div className="insp-sec"><label className="lbl">방향</label><Chips className="oneline" options={dirOpts} value={dirVal} onChange={setDir} /></div>
          <div className="insp-sec"><label className="lbl">샷 종류</label><Chips className="oneline" options={shotOpts} value={shotVal} onChange={setShot} /></div>

          {/* 분위기 예시 — 콘티보드와 동일(생성예시 / 내 레퍼런스). 컷·방향·샷에 따라 변함 */}
          <MoodGuide catalogs={catalogs} cut={cut} direction={dirVal} shot={shotVal} />

          <div className="insp-divider" />

          <div className="insp-sec"><label className="lbl">색상</label>
            <ColorDots colorOpts={colorOpts} value={colorVal} onChange={setColor} /></div>

          {/* 모델 — 토글 안에 접어둬 '새 이미지 생성'이 스크롤 없이 보이게.
              열면(>가 시계방향 90°) 패널이 모델 카드+생성 버튼까지 따라 내려감 */}
          <details ref={modelRef} className="insp-extra ai-model" open={modelOpen}>
            <summary onClick={toggleModel}><Icon name="chevRight" size={15} />모델<span className="ai-model-cur">{(catalogs.models || []).find((m) => m.id === model)?.name || ''}</span></summary>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8, marginTop: 12 }}>
              {(catalogs.models || []).map((m) => (
                <div key={m.id} className={`model-card${model === m.id ? ' on' : ''}`} style={{ width: 'auto' }} onClick={() => setModel(m.id)}>
                  <img src={m.thumb} alt={m.name} style={{ height: 104 }} />
                  <div className="nm" style={{ padding: '7px 8px', fontSize: 12 }}>{m.name}{m.recommended && <Icon name="star" size={12} fill="currentColor" className="star" />}</div>
                </div>
              ))}
            </div>
          </details>

          <Button variant="primary" block icon="sparkles" onClick={() => onGenerate({ group: genGroup })}>새 이미지 생성 · {catalogs.creditCosts?.editorImage ?? 1} 크레딧</Button>
        </div>
      ) : (
        <div>
          {selectedEl && selectedEl.type === 'image' ? (
            <>
              <div className="media-wrap" style={{ aspectRatio: '3/4', marginBottom: 14 }}><img src={selectedEl.src} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover' }} /></div>
              <Button variant="primary" block icon="sparkles" onClick={() => onVary('비슷한')} style={{ marginBottom: 14 }}>비슷한 컷 만들기 · {catalogs.creditCosts?.editorImage ?? 1} 크레딧</Button>
              <div className="insp-sec"><label className="lbl">변경 옵션</label>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {[['배경 변경', 'landscape'], ['포즈 변경', 'person'], ['표정 변경', 'smile']].map(([l, ic]) => (
                    <button key={l} className="chip" style={{ justifyContent: 'flex-start' }} onClick={() => onVary(l)}><Icon name={ic} size={15} />{l}</button>
                  ))}
                </div></div>
              <p className="hint">기존 이미지는 유지되고, 새 이미지가 의류 탭에 추가돼요.</p>
            </>
          ) : (
            <EmptyState icon="image" title="변형할 컷을 선택하세요" desc="캔버스나 의류 탭에서 이미지를 먼저 선택해주세요." />
          )}
        </div>
      )}
    </div>
  );
}

/* ---------- 의류 (wardrobe library) ---------- */
function WardrobePanel({ wardrobe, colorOpts = [], pendingSlot, onInsert, onUpload, onVaryImage, onDeleteSelected }) {
  // map a group key → the real color picked on the input page (circle + name)
  const colorFor = (group) => {
    const m = group.match(/색상\s*(\d+)/);
    if (m) { const c = colorOpts[+m[1] - 1]; if (c) return { hex: c.hex, name: c.label }; }
    return { hex: '#d4d4d8', name: group, neutral: true };
  };
  // 색상 그룹별 접기/펼치기 (기본 펼침)
  const [collapsed, setCollapsed] = useState({});
  const toggle = (group) => setCollapsed((c) => ({ ...c, [group]: !c[group] }));
  // 선택 삭제 (PRD §11.10) — 체크한 이미지를 의류 목록에서만 삭제 (캔버스는 유지)
  const [sel, setSel] = useState(() => new Set());
  const toggleSel = (id) => setSel((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  return (
    <div className="ward-panel">
      {pendingSlot && <div className="ward-fill-banner"><Icon name="image" size={15} />빈 칸에 넣을 의류를 선택하세요</div>}
      <Button variant="ghost" block icon="upload" onClick={onUpload} style={{ marginBottom: 16 }}>직접 이미지 업로드하기</Button>
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
                {imgs.map((im) => im.loading ? (
                  <div className="ward-cell loading" key={im.id}><Icon name="loader" size={18} className="spin" style={{ color: 'var(--fg-3)' }} /></div>
                ) : (
                  <div className={`ward-cell${sel.has(im.id) ? ' checked' : ''}`} key={im.id} onClick={() => onInsert(im)} title="클릭하면 캔버스에 삽입">
                    <img src={im.src} alt="" />
                    {/* 좌측 위 체크박스 (item 8) */}
                    <button className="ward-check" onClick={(e) => { e.stopPropagation(); toggleSel(im.id); }} title="선택">
                      {sel.has(im.id) && <Icon name="check" size={13} />}
                    </button>
                    {/* 호버 시 AI 편집 pill (item 7, PRD §11.10) */}
                    <button className="ai-flag" onClick={(e) => { e.stopPropagation(); onVaryImage(im); }} title="AI로 편집"><Icon name="wand" size={12} /><span>AI 편집</span></button>
                  </div>
                ))}
              </div>
            )}
          </div>
        );
      })}
      {/* 1개 이상 체크 시 — 패널 맨 아래 우측에 빨간 삭제 (item 8) */}
      {sel.size > 0 && (
        <div className="ward-delbar">
          <button type="button" className="ward-del" onClick={() => { onDeleteSelected([...sel]); setSel(new Set()); }}>
            <Icon name="trash" size={15} />삭제 ({sel.size})
          </button>
        </div>
      )}
    </div>
  );
}

/* ---------- 이미지 props ---------- */
function PropRange({ label, value, min, max, step, suffix, onChange }) {
  return (
    <div className="prop-row"><span className="pk">{label}</span>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <input className="range" type="range" min={min} max={max} step={step || 1} value={value} onChange={(e) => onChange(Number(e.target.value))} style={{ width: 110 }} />
        <span className="code" style={{ width: 42, textAlign: 'right' }}>{value}{suffix || ''}</span>
      </div></div>
  );
}
/* layer order controls for the selected element (PRD §11.15) */
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
// shape/line fill & stroke palette (achromatic Wearless + the one point color)
const SHAPE_PALETTE = ['#0e0d14', '#898989', '#d4d4d8', '#ffffff', '#4f88c9', '#d92d20'];
// line dash presets — order: dash 많은 점선 / dash 적은 점선 / 실선(default)
const LINE_DASH = [
  { id: 'dotted', label: '점선', preview: '3 5' },
  { id: 'dashed', label: '파선', preview: '12 9' },
  { id: 'solid', label: '실선', preview: '' },
];
// a field with a small label sitting ABOVE it (회전·둥근 모서리 etc, item 1)
function LabeledField({ label, children }) {
  return <div className="ff"><span className="ff-lbl">{label}</span>{children}</div>;
}
// images / shapes / lines edit here — Figma-style sectioned inspector (PRD feedback).
// the left-panel HEADER title is set contextually by the Editor (이미지/도형/선).
function ImagePanel({ el, onChange, onLayer }) {
  const [lock, setLock] = useState(true);
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
      <PanelSection title={isLine ? '선 크기' : '이미지 크기'} first>
        <div className="size-row">
          <NumField iconText="가로" value={Math.round(el.w)} min={20} max={2000} onChange={setW} />
          <NumField iconText="세로" value={Math.round(el.h)} min={20} max={2000} onChange={setH} />
          <button type="button" className={`lock-btn${lock ? ' on' : ''}`} onClick={() => setLock((v) => !v)} title="비율 고정"><Icon name={lock ? 'lock' : 'unlock'} size={15} /></button>
        </div>
      </PanelSection>

      {/* 회전 + 둥근 모서리 — 라벨을 위에 두고 한 줄에 나란히 (items 1·5) */}
      <PanelSection title="모양">
        <div className="field-2up labeled">
          <LabeledField label="회전"><NumField icon="rotate" value={el.rotate || 0} min={-180} max={180} suffix="°" onChange={(v) => onChange({ rotate: v })} /></LabeledField>
          {!isLine
            ? <LabeledField label="둥근 모서리"><NumField icon="cornerRadius" value={el.radius || 0} min={0} max={400} onChange={(v) => onChange({ radius: v })} /></LabeledField>
            : <span />}
        </div>
      </PanelSection>

      {/* 선 스타일 (item 6) — 점선/파선/실선 한 줄, 기본 실선 */}
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

      <PanelSection title={isLine ? '선 색상' : '채움'}>
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
          <Button variant="ghost" size="sm" block icon="crop">크롭 (캔버스에서 더블클릭)</Button>
        </PanelSection>
      )}
    </div>
  );
}

/* ---------- 텍스트 props ---------- */
const TEXT_PALETTE = ['#0e0d14', '#898989', '#ffffff', '#4f88c9', '#d92d20', '#067647'];
const HL_PALETTE = ['#fef3c7', '#dbeafe', '#dcfce7', '#fee2e2', '#f3f4f6', '#0e0d14'];
const WEIGHTS = [{ value: 300, label: 'Light' }, { value: 400, label: 'Regular' }, { value: 500, label: 'Medium' }, { value: 600, label: 'SemiBold' }, { value: 700, label: 'Bold' }];
function TextPanel({ el, catalogs, onChange, onLayer, onAddText }) {
  const has = el && el.type === 'text';
  const s = (has && el.style) || {};
  const setS = (p) => onChange({ style: { ...s, ...p } });
  return (
    <div className="fig-panel">
      <button type="button" className="add-text-btn" onClick={onAddText}><Icon name="type" size={17} />텍스트 추가</button>
      {!has ? (
        <div className="panel-sub" style={{ marginTop: 18 }}>위 버튼으로 텍스트를 추가하거나, 캔버스에서 텍스트를 클릭해 편집해요.</div>
      ) : (
        <>
          <PanelSection title="타이포그래피" first>
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

          <PanelSection title="하이라이트">
            <SwatchField value={s.bg || 'none'} palette={HL_PALETTE} allowNone onColor={(c) => setS({ bg: c })} />
          </PanelSection>
        </>
      )}
    </div>
  );
}

/* ---------- 프레임 ---------- */
function FramePanel({ catalogs, onAdd, onDragStart, onDragEnd }) {
  return (
    <div>
      <PanelHead title="프레임" sub="새 블록으로 추가돼요. 끌어 놓거나 클릭하세요." />
      <div className="frame-list">
        {catalogs.frames.map((f) => (
          <div className="frame-item" key={f.id} onClick={() => onAdd(f)} draggable
            onDragStart={(e) => { e.dataTransfer.effectAllowed = 'copy'; e.dataTransfer.setData('text/frame', f.id); onDragStart && onDragStart(); }}
            onDragEnd={() => onDragEnd && onDragEnd()}>
            <div className="frame-prev" style={{ gridTemplateColumns: `repeat(${f.cols}, 1fr)` }}>
              {Array.from({ length: f.cols }).map((_, i) => <i key={i} />)}
            </div>
            <div className="fl">{f.label}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ---------- 오브젝트 (도형/선 추가 + 블록 배경) ---------- */
const BLOCK_BG_OPTS = [
  { c: '#ffffff', label: '흰색' }, { c: '#f5f5f5', label: '연회색' }, { c: '#0e0d14', label: '잉크' },
];
function ShapePanel({ catalogs, onAdd, block, onBgChange }) {
  // drag payload so an object can be dropped onto a canvas block (PRD feedback #7)
  const dragStart = (e, type, id) => { e.dataTransfer.effectAllowed = 'copy'; e.dataTransfer.setData('text/object', `${type}:${id}`); };
  return (
    <div>
      {/* 블록 배경 — 블록이 선택됐을 때만 (도형/오브젝트 탭에 분류) */}
      {block && onBgChange && (
        <div style={{ marginBottom: 20 }}>
          <label className="lbl" style={{ marginBottom: 9 }}>블록 배경</label>
          <div className="block-bg-row">
            {BLOCK_BG_OPTS.map((o) => {
              const on = (block.bg || '#ffffff').toLowerCase() === o.c;
              return (
                <button key={o.c} className={`block-bg-sw${on ? ' on' : ''}`} onClick={() => onBgChange(block.id, o.c)}>
                  <span className="bg-chip" style={{ background: o.c }} />{o.label}
                </button>
              );
            })}
          </div>
          <p className="hint" style={{ marginTop: 8 }}>선택한 블록(<b style={{ color: 'var(--fg-1)' }}>{block.name}</b>)의 배경색이에요.</p>
        </div>
      )}
      <PanelHead title="오브젝트" sub="클릭하면 블록 중앙에, 드래그하면 원하는 블록에 추가돼요." />
      <label className="lbl" style={{ marginBottom: 9 }}>기본 도형</label>
      <div className="shape-list" style={{ marginBottom: 18 }}>
        {catalogs.shapes.map((s) => (
          <button className="shape-cell" key={s.id} title={s.label} draggable
            onClick={() => onAdd('shape', s.id)} onDragStart={(e) => dragStart(e, 'shape', s.id)}>
            {s.id === 'circle' && <span className="obj-prev circle" />}
            {s.id === 'rect' && <span className="obj-prev square" />}
            {s.id === 'triangle' && (
              <span className="obj-prev triangle">
                <svg viewBox="0 0 34 30"><polygon points="17,2 32,28 2,28" fill="#b6b6bd" /></svg>
              </span>
            )}
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
                <line x1={l.id === 'arrow-l' ? 7 : 1} y1="8" x2={l.id === 'arrow-r' ? 31 : 37} y2="8" stroke="#b6b6bd" strokeWidth="1.6" strokeLinecap="round" />
                {l.id === 'arrow-l' && <polyline points="8,3 2,8 8,13" fill="none" stroke="#b6b6bd" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />}
                {l.id === 'arrow-r' && <polyline points="30,3 36,8 30,13" fill="none" stroke="#b6b6bd" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />}
              </svg>
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}

/* ---------- 레이어 패널 (PRD §11.15 / 14 P2 "레이어 패널") ----------
   현재 블록의 요소를 스택 순서(위=최상단)로 나열. 드래그 재정렬 + 표시/잠금 토글. */
function layerMeta(el) {
  if (el.type === 'image') return { icon: 'image', label: '이미지', thumb: el.src };
  if (el.type === 'text') return { icon: 'type', label: (el.text || '텍스트').replace(/\n/g, ' ').slice(0, 18) || '텍스트' };
  if (el.type === 'line') return { icon: 'minus', label: '선' };
  const names = { circle: '원', rect: '사각형', triangle: '삼각형' };
  return { icon: 'shapes', label: names[el.shape] || '도형' };
}
function LayerPanel({ block, selEls = [], embedded, onSelect, onReorder, onToggle }) {
  const [dragId, setDragId] = useState(null);
  const [overId, setOverId] = useState(null);
  if (!block) return <EmptyState icon="layers" title="블록을 선택하세요" desc="블록을 클릭하면 그 안의 레이어가 순서대로 나와요." />;
  const rows = block.elements.map((el, idx) => ({ el, idx })).reverse(); // 위가 최상단(맨 앞)
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

Object.assign(window, { AIPanel, WardrobePanel, ImagePanel, TextPanel, FramePanel, ShapePanel, LayerPanel });
