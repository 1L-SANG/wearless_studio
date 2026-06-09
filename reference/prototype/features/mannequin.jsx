/* =============================================================
   features/mannequin.jsx — ③ 마네킹컷 생성·선택 (PRD §7)
   30s-feel progress → A/B candidates + history + adjust panel.
   확정 레이아웃: 좌측 후보 A/B + 우측 세부조정 패널.
   ============================================================= */
const { useState, useEffect, useCallback } = React;

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

function AdjustPanel({ selected, adjustLeft, onAdjust, onRegenerate, busy, productName, matchClothing, onToggleMatch, onMatchAdjust, creditCosts }) {
  // 가운데 '현재' = 지금 선택된 마네킹의 기준. 양옆은 그 기준에서의 조정 방향.
  const [fit, setFit] = useState('current');
  const [length, setLength] = useState('current');
  const [matchOpen, setMatchOpen] = useState(false); // 매칭 의류는 처음엔 접혀 있음
  const [confirmRegen, setConfirmRegen] = useState(false);
  // 메인 매칭 의류 = 분석 페이지에서 첫 번째로 선택한 매칭 의류
  const selMatch = (matchClothing || []).filter((m) => m.selected).sort((x, y) => (x.selOrder || 0) - (y.selOrder || 0));
  const mainMatch = selMatch[0];
  // 선택된 마네킹이 바뀌면 '현재' 기준도 바뀌므로 가운데로 리셋
  useEffect(() => { setFit('current'); setLength('current'); }, [selected?.id]);
  const noChange = fit === 'current' && length === 'current';
  const applyAdjust = () => {
    const fitLabel = fit === 'slim' ? '슬림' : fit === 'loose' ? '여유' : '';
    const lenLabel = length === 'short' ? '숏' : length === 'long' ? '롱' : '';
    onAdjust({ fit: fitLabel, length: lenLabel });
    setFit('current'); setLength('current');
  };
  return (
    <div className="surface inspector adjust-panel">
      <div className="sec-title" style={{ fontSize: 15 }}>세부 조정</div>
      <div className="pill pill-soft" style={{ marginTop: 12 }}>{`조정 가능 횟수 ${adjustLeft}/2`}</div>

      {/* 메인 의류 — 상품명으로 묶은 조정 그룹 */}
      <div className="ap-group">
        <div className="ap-group-eyebrow">메인 의류</div>
        <div className="ap-group-head"><Icon name="shirt" size={14} />{productName || '상품'}</div>
        <div className="ap-sec">
          <label className="lbl">총기장</label>
          <div className="seg-chips">
            {[['더 짧게', 'short'], ['현재', 'current'], ['더 길게', 'long']].map(([l, v]) => (
              <button key={v} className={`chip${length === v ? ' on' : ''}${v === 'current' ? ' is-current' : ''}`} onClick={() => setLength(v)}>{l}</button>
            ))}
          </div>
        </div>
        <div className="ap-sec">
          <label className="lbl">핏</label>
          <div className="seg-chips">
            {[['더 슬림하게', 'slim'], ['현재', 'current'], ['더 여유있게', 'loose']].map(([l, v]) => (
              <button key={v} className={`chip${fit === v ? ' on' : ''}${v === 'current' ? ' is-current' : ''}`} onClick={() => setFit(v)}>{l}</button>
            ))}
          </div>
        </div>
      </div>

      {/* 매칭 의류 — 토글로 펼쳐서 조정 (기본 닫힘) */}
      {mainMatch && (
        <div className={`ap-group ap-collapsible${matchOpen ? ' open' : ''}`}>
          <button className="ap-group-toggle" onClick={() => setMatchOpen((o) => !o)} aria-expanded={matchOpen}>
            <span className="ap-tg-text">
              <span className="ap-group-eyebrow">매칭 의류</span>
              <span className="ap-group-head"><Icon name="layers" size={14} />{mainMatch.name}</span>
            </span>
            <Icon name={matchOpen ? 'chevUp' : 'chevDown'} size={18} />
          </button>
          {matchOpen && (
            <div className="ap-collapse-body">
              <div className="ap-sec">
                <label className="lbl">총기장</label>
                <div className="seg-chips">
                  {[['더 짧게', 'short'], ['현재', 'origin'], ['더 길게', 'long']].map(([l, v]) => (
                    <button key={v} className={`chip${(mainMatch.length || 'origin') === v ? ' on' : ''}${v === 'origin' ? ' is-current' : ''}`} onClick={() => onMatchAdjust(mainMatch.id, { length: v })}>{l}</button>
                  ))}
                </div>
              </div>
              <div className="ap-sec">
                <label className="lbl">핏</label>
                <div className="seg-chips">
                  {[['더 슬림하게', 'slim'], ['현재', 'origin'], ['더 여유있게', 'loose']].map(([l, v]) => (
                    <button key={v} className={`chip${(mainMatch.fit || 'origin') === v ? ' on' : ''}${v === 'origin' ? ' is-current' : ''}`} onClick={() => onMatchAdjust(mainMatch.id, { fit: v })}>{l}</button>
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>
      )}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 20 }}>
        <Button variant="primary" block disabled={adjustLeft <= 0 || busy || noChange}
          onClick={applyAdjust}>의류 조정하기 · {creditCosts?.mannequinAdjust ?? 1} 크레딧</Button>
        <Button variant="ghost" block disabled={adjustLeft <= 0 || busy} onClick={() => setConfirmRegen(true)}
          style={{ color: 'rgba(14,13,20,.8)', minHeight: 'var(--cta-height)', borderRadius: 'var(--cta-radius)' }}>마네킹컷 전부 재생성 · {creditCosts?.mannequinGenerate ?? 2} 크레딧</Button>
      </div>
      {adjustLeft <= 0 && <p className="hint" style={{ marginTop: 10 }}>이번 세션의 조정 횟수를 모두 사용했어요.</p>}
      {confirmRegen && (
        <Modal onClose={() => setConfirmRegen(false)}>
          <h3>마네킹컷을 다시 생성할까요?</h3>
          <p>후보 A/B가 새로운 컷으로 교체되고, 지금까지의 조정 결과는 사라져요. 조정 횟수도 1회 차감됩니다.</p>
          <div className="modal-actions">
            <Button variant="ghost" onClick={() => setConfirmRegen(false)}>취소</Button>
            <Button variant="primary" onClick={() => { setConfirmRegen(false); onRegenerate(); }}>재생성</Button>
          </div>
        </Modal>
      )}
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

function Mannequin({ onNext }) {
  const [phase, setPhase] = useState('loading');
  const [progress, setProgress] = useState(0);
  const [cuts, setCuts] = useState([]);
  const [selectedId, setSelectedId] = useState('A-0');
  const [adjustLeft, setAdjustLeft] = useState(2);
  const [busy, setBusy] = useState(false);
  const [matchClothing, setMatchClothing] = useState(null);
  const [productName, setProductName] = useState('');
  const [catalogs, setCatalogs] = useState(null);
  const [colorCount, setColorCount] = useState(1);
  const [composeMode, setComposeMode] = useState('basic'); // 기본형 디폴트
  const [picking, setPicking] = useState(false);
  const toast = useToast();

  useEffect(() => {
    window.scrollTo({ top: 0 });
    document.querySelector('.app-main')?.scrollTo({ top: 0 });
    api.generateMannequins({ onProgress: setProgress }).then((m) => { setCuts(m); setPhase('ready'); });
    api.getMatchClothing().then(setMatchClothing);
    api.getProduct().then((p) => { setProductName(p?.name || ''); setColorCount((p?.colors || []).length || 1); });
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
      <div className="sec-title" style={{ marginBottom: 4 }}>마네킹컷 선택</div>
      <div className="sec-sub" style={{ marginBottom: 18 }}>선택한 컷이 실제 착용컷 생성의 기준이 돼요.</div>
      <div className="candidate-row">
        <Candidate letter="A" cuts={cutsA} selectedId={selectedId} onSelect={setSelectedId} />
        <Candidate letter="B" cuts={cutsB} selectedId={selectedId} onSelect={setSelectedId} />
      </div>
    </div>
  );
  const toggleMatch = (id) => setMatchClothing((mc) => mc.map((m) => m.id === id ? { ...m, selected: !m.selected } : m));
  const matchAdjust = (id, patch) => setMatchClothing((mc) => mc.map((m) => m.id === id ? { ...m, ...patch } : m));
  const panel = <AdjustPanel selected={selected} adjustLeft={adjustLeft} onAdjust={adjust} onRegenerate={regenerate} busy={busy} productName={productName} matchClothing={matchClothing} onToggleMatch={toggleMatch} onMatchAdjust={matchAdjust} creditCosts={catalogs?.creditCosts} />;
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
        <Button variant="primary" size="lg" iconRight="arrowRight" onClick={onNext}>이 마네킹컷으로 콘티 만들기</Button>
      </WizardCTA>
    </div>
  );
}

window.Mannequin = Mannequin;
