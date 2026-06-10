/* =============================================================
   features/mannequin — ③ 마네킹컷 생성·선택 (PRD §7)
   Ported verbatim from reference/prototype/features/mannequin.jsx.
   Only change: ES imports; onNext → navigate('/create/storyboard').
   (Regenerate already respects the 2/session adjust cap — kept.)
   ============================================================= */
import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '@/lib/api/index.js';
import { Icon, Button, Modal, ProgressBar, useToast } from '@/components/ui.jsx';
import { PageHead, WizardCTA } from '@/features/shell/shell.jsx';

function MannequinLoading({ progress }) {
  const stage = progress < 30 ? '의류 정보 분석 중' : progress < 60 ? '핏·주름 정리하는 중'
    : progress < 80 ? '마네킹컷 생성 중' : '결과를 확인하는 중';
  return (
    <div className="wizard">
      <PageHead title="마네킹컷을 만들고 있어요" sub="AI가 상품의 핏과 실루엣을 기준 마네킹에 입혀보고 있어요." />
      <div className="surface">
        <div style={{ maxWidth: 520, margin: '0 auto 26px' }}><ProgressBar value={progress} label={stage} /></div>
        <div className="candidate-row" style={{ maxWidth: 560, margin: '0 auto' }}>
          {['마네킹 A', '마네킹 B'].map((n) => (
            <div className="candidate" key={n}>
              <div className="big"><div className="busy-tile" style={{ position: 'absolute', inset: 0 }}>
                <Icon name="loader" size={22} className="spin" />작업 중</div></div>
              <div className="cap">{n}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/* 의류 옵션 row — 체크(=펼침) 시 sky 테두리 + glow-sky 제목 fill (첨부 시안 톤) */
function ClothingCard({ open, onToggle, eyebrow, name, icon, children }) {
  return (
    <div className={`adj-card${open ? ' on' : ''}`}>
      <button type="button" className="adj-card-head" onClick={onToggle} aria-expanded={open}>
        <span className={`adj-check${open ? ' on' : ''}`}>{open && <Icon name="check" size={13} />}</span>
        <span className="adj-ico"><Icon name={icon} size={17} /></span>
        <span className="adj-meta">
          <span className="adj-eyebrow">{eyebrow}</span>
          <span className="adj-name">{name}</span>
        </span>
        <Icon name={open ? 'chevUp' : 'chevDown'} size={16} className="adj-chev" />
      </button>
      {open && <div className="adj-card-body">{children}</div>}
    </div>
  );
}

const TOP_TYPES = ['top', 'outer', 'dress'];
const SEG = (cur, opts, onPick) => (
  <div className="seg-chips">
    {opts.map(([l, v, isCur]) => (
      <button key={v} className={`chip${cur === v ? ' on' : ''}${isCur ? ' is-current' : ''}`} onClick={() => onPick(v)}>{l}</button>
    ))}
  </div>
);

function AdjustPanel({ selected, adjustLeft, onAdjust, busy, productName, clothingType, matchClothing, onMatchAdjust, creditCosts }) {
  // 가운데 '현재' = 지금 선택된 마네킹의 기준. 양옆은 그 기준에서의 조정 방향.
  const [fit, setFit] = useState('current');
  const [length, setLength] = useState('current');
  const [mainOpen, setMainOpen] = useState(false);   // 체크박스 = 펼침 (기본 접힘 = 단순 row)
  const [matchOpen, setMatchOpen] = useState(false);
  // 메인 매칭 의류 = 분석 페이지에서 첫 번째로 선택한 매칭 의류
  const selMatch = (matchClothing || []).filter((m) => m.selected).sort((x, y) => (x.selOrder || 0) - (y.selOrder || 0));
  const mainMatch = selMatch[0];
  // 선택된 마네킹이 바뀌면 '현재' 기준도 바뀌므로 가운데로 리셋
  useEffect(() => { setFit('current'); setLength('current'); }, [selected?.id]);
  const mainChanged = fit !== 'current' || length !== 'current';
  const matchChanged = !!mainMatch && ((mainMatch.length && mainMatch.length !== 'origin') || (mainMatch.fit && mainMatch.fit !== 'origin'));
  const noChange = !mainChanged && !matchChanged;   // 매칭만 바꿔도 활성화 (req)
  // 아이콘은 의류 종류에 맞춰서: 상의쪽 = shirt, 하의쪽 = pants
  const mainIcon = TOP_TYPES.includes(clothingType) ? 'shirt' : 'pants';
  const matchIcon = mainIcon === 'shirt' ? 'pants' : 'shirt';
  const applyAdjust = () => {
    const fitLabel = fit === 'slim' ? '슬림' : fit === 'loose' ? '여유' : '';
    const lenLabel = length === 'short' ? '숏' : length === 'long' ? '롱' : '';
    onAdjust({ fit: fitLabel, length: lenLabel });
    setFit('current'); setLength('current');
  };
  return (
    <div className="surface inspector adjust-panel">
      <div className="sec-title" style={{ fontSize: 15 }}>세부 조정</div>
      <div className="pill pill-soft" style={{ marginTop: 12, marginBottom: 4 }}>{`조정 가능 횟수 ${adjustLeft}/2`}</div>

      {/* 메인 의류 카드 row */}
      <ClothingCard open={mainOpen} onToggle={() => setMainOpen((o) => !o)} eyebrow="메인 의류" name={productName || '상품'} icon={mainIcon}>
        <div className="ap-sec"><label className="lbl">총기장</label>
          {SEG(length, [['더 짧게', 'short'], ['현재', 'current', true], ['더 길게', 'long']], setLength)}</div>
        <div className="ap-sec"><label className="lbl">핏</label>
          {SEG(fit, [['더 슬림하게', 'slim'], ['현재', 'current', true], ['더 여유있게', 'loose']], setFit)}</div>
      </ClothingCard>

      {/* 매칭 의류 카드 row (접힘 = 단순 row) */}
      {mainMatch && (
        <ClothingCard open={matchOpen} onToggle={() => setMatchOpen((o) => !o)} eyebrow="매칭 의류" name={mainMatch.name} icon={matchIcon}>
          <div className="ap-sec"><label className="lbl">총기장</label>
            {SEG(mainMatch.length || 'origin', [['더 짧게', 'short'], ['현재', 'origin', true], ['더 길게', 'long']], (v) => onMatchAdjust(mainMatch.id, { length: v }))}</div>
          <div className="ap-sec"><label className="lbl">핏</label>
            {SEG(mainMatch.fit || 'origin', [['더 슬림하게', 'slim'], ['현재', 'origin', true], ['더 여유있게', 'loose']], (v) => onMatchAdjust(mainMatch.id, { fit: v }))}</div>
        </ClothingCard>
      )}

      <Button variant="primary" block disabled={adjustLeft <= 0 || busy || noChange} style={{ marginTop: 16 }}
        onClick={applyAdjust}>의류 조정하기 · {creditCosts?.mannequinAdjust ?? 1} 크레딧</Button>
      {adjustLeft <= 0 && <p className="hint" style={{ marginTop: 10 }}>이번 세션의 조정 횟수를 모두 사용했어요.</p>}
    </div>
  );
}

function Candidate({ letter, cuts, selectedId, onSelect }) {
  const head = cuts.find((c) => c.id === letter + '-0') || cuts[0];
  const current = cuts.find((c) => c.id === selectedId && c.candidate === letter);
  const big = current || head;
  return (
    <div className="candidate">
      <div className={`big${big.id === selectedId ? ' on' : ''}`} onClick={() => onSelect(big.id)}>
        <img src={big.src} alt={big.id} />
        {big.id === selectedId && <span className="pill pill-ink selflag">선택됨</span>}
      </div>
      <div className="cap">마네킹 {letter} · {big.fitLabel} / {big.lengthLabel}</div>
      <div className="history-strip">
        {cuts.map((c) => (
          <div key={c.id} className={`h${c.id === selectedId ? ' on' : ''}`} onClick={() => onSelect(c.id)}>
            <img src={c.src} alt={c.id} /><span className="v">{c.id}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// 우측 탭의 '상세페이지 구성' 미니 카드 + 좌측으로 펼쳐지는 선택 플라이아웃
function ComposeModeMini({ modes, mode, colorCount, picking, onToggle, onPick }) {
  const cur = (modes || []).find((m) => m.value === mode) || (modes || [])[0];
  if (!cur) return null;
  return (
    <div className="surface compose-mini">
      <div className="sec-title" style={{ fontSize: 15 }}>상세페이지 구성</div>
      <div className="cm-mini-name">
        <Icon name="layout" size={15} />{cur.label}
      </div>
      <div className="md">{cur.desc}</div>
      <div className="mode-flow">{cur.flow.map((f, i) => <span className="flow-pill" key={i}>{f}</span>)}</div>
      {cur.count && <div className="cm-count">예상 {cur.count}컷</div>}
      <Button variant={picking ? 'primary' : 'ghost'} block icon={picking ? 'check' : 'layout'} onClick={onToggle} style={{ marginTop: 16 }}>
        {picking ? '구성 방식 선택 완료' : '구성 방식 변경'}
      </Button>
      {picking && (
        <>
          <div className="mode-pop-backdrop" onClick={onToggle} />
          <div className="mode-pop">
            {modes.map((m) => {
              const disabled = m.value === 'extended' && colorCount < 2;
              return (
                <div key={m.value} className={`mode-card pop${mode === m.value ? ' current' : ''}${disabled ? ' disabled' : ''}`}
                  onClick={() => !disabled && onPick(m.value)}>
                  {m.recommended && <span className="pill pill-soft badge-rec">추천</span>}
                  {mode === m.value && <span className="pop-now">현재</span>}
                  <h4>{m.label}</h4>
                  <div className="md">{m.desc}</div>
                  <div className="mode-flow">{m.flow.map((f, i) => <span className="flow-pill" key={i}>{f}</span>)}</div>
                  {m.count && <div className="pop-count">예상 {m.count}컷</div>}
                  {disabled && <div className="hint" style={{ marginTop: 10 }}>색상 2개 이상부터</div>}
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}

export function Mannequin() {
  const navigate = useNavigate();
  const [phase, setPhase] = useState('loading');
  const [progress, setProgress] = useState(0);
  const [cuts, setCuts] = useState([]);
  const [selectedId, setSelectedId] = useState('A-0');
  const [adjustLeft, setAdjustLeft] = useState(2);
  const [busy, setBusy] = useState(false);
  const [matchClothing, setMatchClothing] = useState(null);
  const [productName, setProductName] = useState('');
  const [clothingType, setClothingType] = useState('top');
  const [catalogs, setCatalogs] = useState(null);
  const [colorCount, setColorCount] = useState(1);
  const [composeMode, setComposeMode] = useState('basic'); // 기본형 디폴트
  const [picking, setPicking] = useState(false);
  const [confirmRegen, setConfirmRegen] = useState(false);
  const toast = useToast();

  useEffect(() => {
    window.scrollTo({ top: 0 });
    document.querySelector('.app-main')?.scrollTo({ top: 0 });
    api.generateMannequins({ onProgress: setProgress }).then((m) => { setCuts(m); setPhase('ready'); });
    api.getMatchClothing().then(setMatchClothing);
    api.getProduct().then((p) => { setProductName(p?.name || ''); setClothingType(p?.clothingType || 'top'); setColorCount((p?.colors || []).length || 1); });
    api.getCatalogs().then(setCatalogs);
  }, []);

  const selected = cuts.find((c) => c.id === selectedId);
  const cutsA = cuts.filter((c) => c.candidate === 'A');
  const cutsB = cuts.filter((c) => c.candidate === 'B');

  const adjust = async ({ fit, length }) => {
    if (adjustLeft <= 0) return; setBusy(true);
    const base = selected || cuts[0];
    const next = await api.adjustMannequin({ baseId: base.id, fit, length });
    setCuts((c) => [...c, next]); setSelectedId(next.id); setAdjustLeft((n) => n - 1); setBusy(false);
    toast.push('조정 결과를 만들었어요', { icon: 'wand' });
  };
  const regenerate = async () => {
    if (adjustLeft <= 0) return; setBusy(true);
    const all = await api.regenerateMannequins({}); setCuts(all); setAdjustLeft((n) => n - 1); setBusy(false);
    toast.push('후보 A/B를 새로 생성했어요', { icon: 'refresh' });
  };

  if (phase === 'loading') return <MannequinLoading progress={progress} />;

  const candidates = (
    <div className="surface">
      <div className="cand-head">
        <div>
          <div className="sec-title">마네킹컷 선택</div>
          <div className="sec-sub" style={{ marginTop: 5 }}>선택한 컷이 실제 착용컷 생성의 기준이 돼요.</div>
        </div>
        <Button variant="ghost" size="sm" icon="refresh" disabled={adjustLeft <= 0 || busy}
          style={{ borderRadius: 'var(--r-4)' }} onClick={() => setConfirmRegen(true)}>
          마네킹컷 전부 재생성 · {catalogs?.creditCosts?.mannequinGenerate ?? 2} 크레딧
        </Button>
      </div>
      <div className="candidate-row">
        <Candidate letter="A" cuts={cutsA} selectedId={selectedId} onSelect={setSelectedId} />
        <Candidate letter="B" cuts={cutsB} selectedId={selectedId} onSelect={setSelectedId} />
      </div>
    </div>
  );
  const matchAdjust = (id, patch) => setMatchClothing((mc) => mc.map((m) => m.id === id ? { ...m, ...patch } : m));
  const panel = <AdjustPanel selected={selected} adjustLeft={adjustLeft} onAdjust={adjust} busy={busy} productName={productName} clothingType={clothingType} matchClothing={matchClothing} onMatchAdjust={matchAdjust} creditCosts={catalogs?.creditCosts} />;
  const modes = catalogs?.composeModes || [];
  const pickMode = (v) => { setComposeMode(v); setPicking(false); };
  // 우측 컬럼 = 세부 조정 패널(위) + 상세페이지 구성 미니 카드(아래)
  const rightCol = (
    <div className="mq-side">
      {panel}
      <ComposeModeMini modes={modes} mode={composeMode} colorCount={colorCount} picking={picking} onToggle={() => setPicking((p) => !p)} onPick={pickMode} />
    </div>
  );

  const body = <div className="mannequin-layout">{candidates}{rightCol}</div>;

  return (
    <div className="wizard wide">
      <PageHead title="원하는 느낌으로 구현된 이미지를 선택해주세요" sub="핏과 총기장을 조정해 의류 재현도를 높여보세요." />
      {body}
      <WizardCTA>
        <Button variant="primary" size="lg" iconRight="arrowRight" onClick={() => navigate('/create/storyboard')}>초안 생성하기</Button>
      </WizardCTA>

      {confirmRegen && (
        <Modal onClose={() => setConfirmRegen(false)}>
          <h3>마네킹컷을 다시 생성할까요?</h3>
          <p>후보 A/B가 새로운 컷으로 교체되고, 지금까지의 조정 결과는 사라져요. 조정 횟수도 1회 차감됩니다.</p>
          <div className="modal-actions">
            <Button variant="ghost" onClick={() => setConfirmRegen(false)}>취소</Button>
            <Button variant="primary" onClick={() => { setConfirmRegen(false); regenerate(); }}>재생성</Button>
          </div>
        </Modal>
      )}
    </div>
  );
}

export default Mannequin;
