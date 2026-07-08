/* =============================================================
   features/mannequin — ③ 마네킹컷 생성·선택 (PRD §7, 원 UI 복원)
   상태 모델 (frontend_state_model.md §4): 선택 컷·구성 방식·조정 횟수는
   store(플로우 선택값) + patchProject 동기화. 컷 목록은 서버 상태.
   조정 값은 enum 토큰(slimmer/looser, shorter/longer)만 — 계약 §6.
   크레딧은 봉투 응답 { data, credits } 를 syncCredits 로 반영.
   생성 job 은 store.mannequinJob 로 라우트를 넘어 관측(ChromeLayout 배너·TopNav 재개).
   ============================================================= */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '@/lib/api/index.js';
import { useAppStore } from '@/store/useAppStore.js';
import { LIMITS } from '@/lib/limits.js';
import { Icon, Button, Modal, ProgressBar, useToast } from '@/components/ui.jsx';
import { PageHead, WizardCTA, useDoneGuard, DoneGuardModal } from '@/features/shell/shell.jsx';

/* 조정 상태 → 표시 라벨 (저장 값은 enum, 한국어는 표시 전용 — 계약 §1) */
const ADJ_LABELS = { slimmer: '더 슬림하게', looser: '더 여유있게', shorter: '더 짧게', longer: '더 길게' };
const MATCH_ADJ_LABELS = { slimmer: '슬림', looser: '여유', shorter: '숏기장', longer: '롱기장' };

function extractCuts(envelope) {
  if (Array.isArray(envelope)) return envelope;
  if (Array.isArray(envelope?.cuts)) return envelope.cuts;
  if (Array.isArray(envelope?.data?.cuts)) return envelope.data.cuts;
  return [];
}

let mannequinGenerationInflight = null;
let mannequinGenerationProjectId = null;

function updateMannequinJob(pid, patch) {
  const { projectId, setMannequinJob } = useAppStore.getState();
  if (projectId !== pid) return;
  setMannequinJob({ projectId: pid, ...patch });
}

function generationProgressFor(pid) {
  const job = useAppStore.getState().mannequinJob;
  return job?.projectId === pid ? Number(job.progress) || 0 : 0;
}

function requestMannequinGeneration(pid) {
  if (mannequinGenerationInflight && mannequinGenerationProjectId === pid) {
    return mannequinGenerationInflight;
  }
  updateMannequinJob(pid, { status: 'running', progress: generationProgressFor(pid), errorMessage: '' });
  mannequinGenerationProjectId = pid;
  mannequinGenerationInflight = api.generateMannequins(pid, {
    onProgress: (next) => updateMannequinJob(pid, { status: 'running', progress: next, errorMessage: '' }),
  }).finally(() => {
    if (mannequinGenerationProjectId === pid) {
      mannequinGenerationInflight = null;
      mannequinGenerationProjectId = null;
    }
  });
  return mannequinGenerationInflight;
}

function MannequinLoading({ progress }) {
  // 서버 progress 는 체크포인트(5→15→35→85→100)로 띄엄띄엄 온다. 그 사이를 완만히 채워
  // 바가 멈춘 것처럼 보이지 않게 한다(서버값이 바닥, 다음 체크포인트 직전까지만 크리프).
  const [shown, setShown] = useState(progress);
  useEffect(() => {
    setShown((s) => Math.max(s, progress));
    const id = setInterval(() => {
      setShown((s) => {
        const ceil = progress >= 85 ? 99 : progress >= 35 ? 82 : progress >= 15 ? 33 : 13;
        return s < ceil ? Math.min(ceil, s + 1) : s;
      });
    }, 700);
    return () => clearInterval(id);
  }, [progress]);
  const p = Math.max(progress, shown);
  const stage = p < 30 ? '의류 정보 분석 중' : p < 60 ? '핏과 실루엣 정리 중'
    : p < 80 ? '마네킹 생성 중' : '결과를 확인하는 중';
  return (
    <div className="wizard">
      <PageHead title="마네킹컷을 만들고 있어요" sub="AI가 상품의 핏과 실루엣을 기준 마네킹에 입혀보고 있어요." />
      <div className="surface">
        <div style={{ maxWidth: 520, margin: '0 auto 26px' }}><ProgressBar value={p} label={stage} /></div>
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

function MannequinError({ message, onRetry }) {
  return (
    <div className="wizard">
      <PageHead title="마네킹컷 생성" sub="입력한 상품 사진을 기준으로 다시 시도할 수 있어요." />
      <div className="surface" style={{ textAlign: 'center', padding: '48px 24px' }}>
        <p style={{ marginBottom: 18 }}>{message || '마네킹컷을 만들지 못했어요. 다시 시도해 주세요.'}</p>
        <Button variant="primary" onClick={onRetry}>다시 시도</Button>
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
    // 계약 §6: enum 토큰만 전달, '현재(변경 없음)' = 필드 생략.
    // 매칭만 바꿔도 활성화되므로, 매칭 변경분도 함께 넘겨서 실제로 반영되게 한다.
    const matchAdjust = matchChanged
      ? { clothingId: mainMatch.id,
          fitAdjust: mainMatch.fit && mainMatch.fit !== 'origin' ? mainMatch.fit : undefined,
          lengthAdjust: mainMatch.length && mainMatch.length !== 'origin' ? mainMatch.length : undefined }
      : null;
    onAdjust({
      fitAdjust: fit === 'current' ? undefined : fit,
      lengthAdjust: length === 'current' ? undefined : length,
      matchAdjust,
    });
    setFit('current'); setLength('current');
  };
  return (
    <div className="surface inspector adjust-panel">
      <div className="sec-title" style={{ fontSize: 15 }}>세부 조정</div>
      <div className="pill pill-soft" style={{ marginTop: 12, marginBottom: 4 }}>{`조정 가능 횟수 ${adjustLeft}/${LIMITS.mannequinAdjustMax}`}</div>

      {/* 메인 의류 카드 row */}
      <ClothingCard open={mainOpen} onToggle={() => setMainOpen((o) => !o)} eyebrow="메인 의류" name={productName || '상품'} icon={mainIcon}>
        <div className="ap-sec"><label className="lbl">총기장</label>
          {SEG(length, [['더 짧게', 'shorter'], ['현재', 'current', true], ['더 길게', 'longer']], setLength)}</div>
        <div className="ap-sec"><label className="lbl">핏</label>
          {SEG(fit, [['더 슬림하게', 'slimmer'], ['현재', 'current', true], ['더 여유있게', 'looser']], setFit)}</div>
      </ClothingCard>

      {/* 매칭 의류 카드 row (접힘 = 단순 row) */}
      {mainMatch && (
        <ClothingCard open={matchOpen} onToggle={() => setMatchOpen((o) => !o)} eyebrow="매칭 의류" name={mainMatch.name} icon={matchIcon}>
          <div className="ap-sec"><label className="lbl">총기장</label>
            {SEG(mainMatch.length || 'origin', [['더 짧게', 'shorter'], ['현재', 'origin', true], ['더 길게', 'longer']], (v) => onMatchAdjust(mainMatch.id, { length: v }))}</div>
          <div className="ap-sec"><label className="lbl">핏</label>
            {SEG(mainMatch.fit || 'origin', [['더 슬림하게', 'slimmer'], ['현재', 'origin', true], ['더 여유있게', 'looser']], (v) => onMatchAdjust(mainMatch.id, { fit: v }))}</div>
        </ClothingCard>
      )}

      <Button variant="primary" block className="btn-glowring" disabled={adjustLeft <= 0 || busy || noChange} style={{ marginTop: 16 }}
        onClick={applyAdjust}>의류 조정하기 · {creditCosts?.mannequinAdjust ?? 1} 크레딧</Button>
      {adjustLeft <= 0 && <p className="hint" style={{ marginTop: 10 }}>이번 프로젝트의 조정 횟수를 모두 사용했어요.</p>}
    </div>
  );
}

function Candidate({ letter, cuts, selectedId, onSelect, labelFor }) {
  // 서버 컷 id 는 UUID — 후보 내 현재 선택(없으면 첫 버전)을 대표컷으로.
  const current = cuts.find((c) => c.id === selectedId);
  const big = current || cuts[0];
  if (!big) return null;
  return (
    <div className="candidate">
      <div className={`big${big.id === selectedId ? ' on' : ''}`} onClick={() => onSelect(big.id)}>
        <img src={big.src} alt={`마네킹 ${letter}`} />
        {big.id === selectedId && <span className="pill pill-ink selflag">선택됨</span>}
      </div>
      <div className="cap">마네킹 {letter} · {labelFor(big)}</div>
      <div className="history-strip">
        {cuts.map((c, i) => (
          <div key={c.id} className={`h${c.id === selectedId ? ' on' : ''}`} onClick={() => onSelect(c.id)}>
            <img src={c.src} alt="" /><span className="v">{`${letter}-${c.version ?? i}`}</span>
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
      <div className="mode-flow">{(cur.flow || []).map((f, i) => <span className="flow-pill" key={i}>{f}</span>)}</div>
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
                  <div className="mode-flow">{(m.flow || []).map((f, i) => <span className="flow-pill" key={i}>{f}</span>)}</div>
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
  const [errorMsg, setErrorMsg] = useState('');
  const [progress, setProgress] = useState(0);
  const [cuts, setCuts] = useState([]);
  const [busy, setBusy] = useState(false);
  const [matchClothing, setMatchClothing] = useState(null);
  const [productName, setProductName] = useState('');
  const [clothingType, setClothingType] = useState('top');
  const [catalogs, setCatalogs] = useState(null);
  const [colorCount, setColorCount] = useState(1);
  const [picking, setPicking] = useState(false);
  const [confirmRegen, setConfirmRegen] = useState(false);
  const toast = useToast();

  // 플로우 선택값 — store 가 보유, patchProject 로 서버 동기화 (ADR-0002)
  const projectId = useAppStore((s) => s.projectId);
  const selectedId = useAppStore((s) => s.selectedMannequinId);
  const selectMannequin = useAppStore((s) => s.selectMannequin);
  const composeMode = useAppStore((s) => s.composeMode);
  const setComposeMode = useAppStore((s) => s.setComposeMode);
  const adjustCount = useAppStore((s) => s.adjustCount);
  const setAdjustCount = useAppStore((s) => s.setAdjustCount);
  const syncCredits = useAppStore((s) => s.syncCredits);
  const mannequinJob = useAppStore((s) => s.mannequinJob);
  const adjustLeft = Math.max(0, LIMITS.mannequinAdjustMax - adjustCount);
  const doneBlocked = useDoneGuard();   // 생성 완료 후 초안 재진입 제한 (PRD §10.17)
  const loadRunRef = useRef(0);

  const loadMannequins = useCallback(async () => {
    const runId = ++loadRunRef.current;
    setPhase('loading');
    setErrorMsg('');
    setProgress(0);
    window.scrollTo({ top: 0 });
    document.querySelector('.app-main')?.scrollTo({ top: 0 });
    let pid = null;
    try {
      await useAppStore.getState().loadProject();
      if (loadRunRef.current !== runId) return;
      pid = useAppStore.getState().projectId;
      if (!pid) { navigate('/create/input', { replace: true }); return; }  // 콜드 진입(복원 불가) → 입력
      api.getMatchClothing(pid).then((m) => { if (loadRunRef.current === runId) setMatchClothing(m); });
      api.getProduct(pid).then((p) => {
        if (loadRunRef.current !== runId) return;
        setProductName(p?.name || '');
        setClothingType(p?.clothingType || 'top');
        setColorCount((p?.colors || []).length || 1);
      });
      api.getCatalogs().then((c) => { if (loadRunRef.current === runId) setCatalogs(c); });
      setProgress(generationProgressFor(pid));
      let list = await api.getMannequins(pid);
      if (loadRunRef.current !== runId) return;
      if (list.length) {
        updateMannequinJob(pid, { status: 'idle', progress: 100, errorMessage: '' });
      }
      if (!list.length) {
        // 최초 진입 — A/B 생성 + 크레딧 차감 (재진입은 무과금 getMannequins).
        // 모듈 스코프 in-flight 합류로 StrictMode 이중 실행·라우트 이탈-복귀에도 1회만 차감.
        const { data, credits } = await requestMannequinGeneration(pid);
        list = extractCuts(data);
        syncCredits(credits);
      }
      if (!list.length) throw new Error('생성된 마네킹컷을 찾지 못했어요. 다시 시도해 주세요.');
      updateMannequinJob(pid, { status: 'idle', progress: 100, errorMessage: '' });
      if (loadRunRef.current !== runId) return;
      setCuts(list);
      if (!useAppStore.getState().selectedMannequinId && list[0]) selectMannequin(list[0].id);
      setPhase('ready');
    } catch (err) {
      const message = err?.message || '마네킹 정보를 불러오지 못했어요. 다시 시도해 주세요.';
      if (pid) {
        try {  // 생성은 실패했지만 기존 컷이 있으면(재진입) 그걸로 화면을 살린다
          const fallback = await api.getMannequins(pid);
          if (fallback.length) {
            updateMannequinJob(pid, { status: 'idle', progress: 100, errorMessage: '' });
            if (loadRunRef.current !== runId) return;
            setCuts(fallback);
            if (!useAppStore.getState().selectedMannequinId && fallback[0]) selectMannequin(fallback[0].id);
            setPhase('ready');
            return;
          }
        } catch { /* 원래 생성 실패 메시지를 보여준다. */ }
        updateMannequinJob(pid, { status: 'error', progress: generationProgressFor(pid), errorMessage: message });
      }
      if (loadRunRef.current !== runId) return;
      setErrorMsg(message);
      setPhase('error');
      toast.push(message, { icon: 'alertTri' });
    }
  }, [navigate, selectMannequin, syncCredits, toast]);

  useEffect(() => {
    loadMannequins();
    return () => { loadRunRef.current += 1; };
  }, [loadMannequins]);

  const selected = cuts.find((c) => c.id === selectedId);
  const cutsA = cuts.filter((c) => c.candidate === 'A');
  const cutsB = cuts.filter((c) => c.candidate === 'B');
  const loadingProgress = mannequinJob?.status === 'running'
    && (!projectId || mannequinJob.projectId === projectId)
    ? Math.max(0, Math.min(100, Number(mannequinJob.progress) || 0))
    : progress;

  // 컷 표시 라벨 파생 — 저장 값(enum)을 화면에서만 한국어로 (계약 §1)
  const labelFor = (c) => {
    const fitTxt = c.fitAdjust ? ADJ_LABELS[c.fitAdjust] : (catalogs?.fits?.find((f) => f.value === c.baseFit)?.label || (c.baseFit === 'slim' ? '슬림핏' : '정핏'));
    const lenTxt = c.lengthAdjust ? ADJ_LABELS[c.lengthAdjust] : '원본 기장';
    let matchTxt = '';
    if (c.matchAdjust) {
      const m = (matchClothing || []).find((x) => x.id === c.matchAdjust.clothingId);
      const parts = [c.matchAdjust.lengthAdjust && MATCH_ADJ_LABELS[c.matchAdjust.lengthAdjust], c.matchAdjust.fitAdjust && MATCH_ADJ_LABELS[c.matchAdjust.fitAdjust]].filter(Boolean);
      if (parts.length) matchTxt = ` · 매칭 ${[m?.name, ...parts].filter(Boolean).join(' ')}`;
    }
    return `${fitTxt} / ${lenTxt}${matchTxt}`;
  };

  const refreshAdjustCount = async () => {
    // adjustCount 는 서버(project)가 원본 — 응답으로만 갱신 (frontend_state_model.md §3)
    const p = await api.getProject(projectId);
    setAdjustCount(p.adjustCount);
  };

  const adjust = async ({ fitAdjust, lengthAdjust, matchAdjust }) => {
    if (adjustLeft <= 0 || busy) return; setBusy(true);
    try {
      const base = selected || cuts[0];
      const { data: next, credits } = await api.adjustMannequin(projectId, { baseId: base.id, fitAdjust, lengthAdjust, matchAdjust, onProgress: setProgress });
      setCuts((c) => [...c, next]); selectMannequin(next.id);
      syncCredits(credits); await refreshAdjustCount();
      // 매칭 변경분은 새 컷에 반영됐으니 기준값(origin)으로 되돌려 버튼이 계속 켜진 채
      // 같은 변경으로 재차 차감되는 걸 막는다 (메인 fit/length 가 current 로 리셋되는 것과 동일)
      if (matchAdjust) setMatchClothing((mc) => mc.map((m) => m.id === matchAdjust.clothingId ? { ...m, fit: 'origin', length: 'origin' } : m));
      toast.push('조정 결과를 만들었어요', { icon: 'wand' });
    } catch (err) {
      toast.push(err?.message || '조정에 실패했어요. 다시 시도해 주세요.', { icon: 'alertTri' });
    } finally { setBusy(false); }
  };

  const regenerate = async () => {
    if (busy) return; setBusy(true);
    const pid = projectId;
    // 재생성은 실서버 image job(~1분) — 로딩 화면 + 전역 배너(mannequinJob)로 진행을 보여준다
    updateMannequinJob(pid, { status: 'running', progress: 0, errorMessage: '' });
    setPhase('loading');
    try {
      const { data, credits } = await api.regenerateMannequin(pid, {
        onProgress: (p) => updateMannequinJob(pid, { status: 'running', progress: p, errorMessage: '' }),
      });
      const next = extractCuts(data);
      if (next.length) {
        setCuts(next);
        const sel = next.find((c) => c.isSelected) || next[0];
        if (sel) selectMannequin(sel.id);
      }
      syncCredits(credits); await refreshAdjustCount();
      updateMannequinJob(pid, { status: 'idle', progress: 100, errorMessage: '' });
      setPhase('ready');
      toast.push('후보 A/B를 새로 생성했어요', { icon: 'refresh' });
    } catch (err) {
      const message = err?.message || '재생성에 실패했어요. 다시 시도해 주세요.';
      updateMannequinJob(pid, { status: 'idle', progress: 0, errorMessage: '' });
      setPhase('ready');   // 기존 컷은 유효 — 에러 화면 대신 토스트
      toast.push(message, { icon: 'alertTri' });
    } finally { setBusy(false); }
  };

  if (phase === 'loading') return <>{doneBlocked && <DoneGuardModal />}<MannequinLoading progress={loadingProgress} /></>;
  if (phase === 'error') return <>{doneBlocked && <DoneGuardModal />}<MannequinError message={errorMsg} onRetry={loadMannequins} /></>;

  const candidates = (
    <div className="surface">
      <div className="cand-head">
        <div>
          <div className="sec-title">마네킹컷 선택</div>
          <div className="sec-sub" style={{ marginTop: 5 }}>선택한 컷이 실제 착용컷 생성의 기준이 돼요.</div>
        </div>
        <Button variant="ghost" size="sm" icon="refresh" disabled={busy}
          style={{ borderRadius: 'var(--r-4)' }} onClick={() => setConfirmRegen(true)}>
          마네킹컷 전부 재생성 · {catalogs?.creditCosts?.mannequinGenerate ?? 2} 크레딧
        </Button>
      </div>
      <div className="candidate-row">
        <Candidate letter="A" cuts={cutsA} selectedId={selectedId} onSelect={selectMannequin} labelFor={labelFor} />
        <Candidate letter="B" cuts={cutsB} selectedId={selectedId} onSelect={selectMannequin} labelFor={labelFor} />
      </div>
    </div>
  );
  const matchAdjustLocal = (id, patch) => setMatchClothing((mc) => mc.map((m) => m.id === id ? { ...m, ...patch } : m));
  const panel = <AdjustPanel selected={selected} adjustLeft={adjustLeft} onAdjust={adjust} busy={busy} productName={productName} clothingType={clothingType} matchClothing={matchClothing} onMatchAdjust={matchAdjustLocal} creditCosts={catalogs?.creditCosts} />;
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
      {doneBlocked && <DoneGuardModal />}
      <PageHead title="원하는 느낌으로 구현된 이미지를 선택해주세요" sub="핏과 총기장을 조정해 의류 재현도를 높여보세요." />
      {body}
      <WizardCTA>
        <Button variant="primary" size="lg" iconRight="arrowRight" onClick={() => navigate('/create/storyboard')}>상세페이지 초안 만들기</Button>
      </WizardCTA>

      {confirmRegen && (
        <Modal onClose={() => setConfirmRegen(false)}>
          <h3>마네킹컷을 다시 생성할까요?</h3>
          <p>후보 A/B가 새로운 컷으로 교체되고, 지금까지의 조정 결과는 사라져요. 크레딧이 차감됩니다.</p>
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
