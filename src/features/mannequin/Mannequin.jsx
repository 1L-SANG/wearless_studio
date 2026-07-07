/* =============================================================
   features/mannequin — ③ 의류 재현성 높이기 (PRD §7, fit-profile 이미지 중심 UI)
   가운데 큰 컷(내 옷 = 매칭 하의까지 입은 모습) → 아래 '확인 카드'.
   축(핏·기장·… + 매칭 의류 핏)을 하나씩 순차 확인 — '조정하기' 하면 이미지 옆에
   예시가 세로로 떠서 비교하며 고른다(방식 1). 매칭 하의도 컷에 보이므로 조정 시 재생성(유료).
   전부 확인되면 카드가 '상세페이지 구성'(기본형/확장형) 선택으로 전환 → 이 구성으로 만들기.
   - 변경 0건 → 구성 선택 후 다음 단계 / 변경 ≥1건 → 수정 반영 재생성(새 버전 히스토리).
   컷 목록은 서버 상태, 선택 컷·구성은 store + patchProject 동기화.
   설계·규칙: documents/mannequin_ui_direction.md · 목업 documents/mockups/mannequin-ui-matching.html
   ============================================================= */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '@/lib/api/index.js';
import { useAppStore } from '@/store/useAppStore.js';
import { CREDIT_COSTS } from '@/lib/limits.js';
import { axesFor, fitProfileCategory } from '@/lib/fitAxes.js';
import { fitExampleImage } from '@/lib/fitExampleImages.js';
import { Icon, Button, ErrorState, ProgressBar, useToast } from '@/components/ui.jsx';
import { PageHead, useDoneGuard, DoneGuardModal } from '@/features/shell/shell.jsx';
import './Mannequin.css';

const AXIS_LABELS = { fit: '핏', length: '기장', cut: '핏', silhouette: '실루엣' };
// 질문 톤: "~ 조정할까요?" (참고: length 는 사용자 요청 '기장 길이 조정 여부'를 일관 톤 유지 위해 질문형으로)
const AXIS_QUESTIONS = {
  fit: '의류 핏을 조정할까요?',
  length: '기장 길이를 조정할까요?',
  cut: '핏을 조정할까요?',
  silhouette: '실루엣을 조정할까요?',
};
const MATCH_KEY = '__match';
const MATCH_NAME = '매칭 의류 핏';
const MATCH_QUESTION = '매칭 의류의 핏도 조정할까요?';

const cutImage = (cut) => cut?.imageUrl || cut?.src || '';
const isMenOnly = (genders) => Array.isArray(genders) && genders.length > 0 && genders.every((g) => g === 'men');
const validAxisValue = (values, value) => values.some((v) => v.value === value);
const axisIsDone = (s) => s?.mode === 'keep' || s?.mode === 'picked';
const hasMatchFor = (category) => category === 'top' || category === 'outer';

function derivedGender(analysis, product) {
  const genders = analysis?.targetGenders?.length ? analysis.targetGenders : product?.targetGenders;
  return isMenOnly(genders) ? 'men' : 'women';
}

function autoAxisValues(axisDefs, analysis) {
  const values = {};
  if (axisDefs.fit && analysis?.fit && validAxisValue(axisDefs.fit, analysis.fit)) {
    values.fit = analysis.fit;
  }
  return values;
}

function createFitProfileDraft(product, analysis) {
  const category = fitProfileCategory(product?.clothingType, analysis?.subCategory) || 'top';
  const gender = derivedGender(analysis, product);
  const axisDefs = axesFor(category, gender);
  const existing = analysis?.fitProfile?.category === category && analysis?.fitProfile?.gender === gender
    ? analysis.fitProfile
    : null;
  const axes = Object.fromEntries(Object.keys(axisDefs).map((axis) => [axis, null]));
  Object.keys(axes).forEach((axis) => {
    if (existing?.axes && Object.prototype.hasOwnProperty.call(existing.axes, axis)) {
      axes[axis] = existing.axes[axis] ?? null;
    }
  });
  const source = existing?.source || 'auto';
  const autoValues = autoAxisValues(axisDefs, analysis);
  if (source === 'auto') {
    Object.entries(autoValues).forEach(([axis, value]) => {
      if (axes[axis] == null) axes[axis] = value;
    });
  }
  const draft = { category, gender, axes, source, version: 1 };
  if (existing?.matchCut != null) draft.matchCut = existing.matchCut;   // 매칭 하의 선택 유지(garment_ref)
  return draft;
}

// 스텝 상태머신 초깃값: pending → (keep | changing → picked). 축 + (해당되면) 매칭 스텝.
function initStepState(axisDefs, withMatch) {
  const keys = [...Object.keys(axisDefs), ...(withMatch ? [MATCH_KEY] : [])];
  return Object.fromEntries(keys.map((k) => [k, { mode: 'pending', pick: null, pickLb: null }]));
}

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

  updateMannequinJob(pid, {
    status: 'running',
    progress: generationProgressFor(pid),
    errorMessage: '',
  });

  mannequinGenerationProjectId = pid;
  mannequinGenerationInflight = api.generateMannequins(pid, {
    onProgress: (next) => updateMannequinJob(pid, {
      status: 'running',
      progress: next,
      errorMessage: '',
    }),
  }).finally(() => {
    if (mannequinGenerationProjectId === pid) {
      mannequinGenerationInflight = null;
      mannequinGenerationProjectId = null;
    }
  });

  return mannequinGenerationInflight;
}

function MannequinLoading({ progress }) {
  const stage = progress < 30 ? '의류 정보 분석 중' : progress < 60 ? '핏과 실루엣 정리 중'
    : progress < 80 ? '마네킹 생성 중' : '결과를 확인하는 중';
  return (
    <div className="wizard">
      <PageHead title="마네킹컷을 만들고 있어요" sub="AI가 상품의 핏과 실루엣을 기준 마네킹에 입혀보고 있어요." />
      <div className="surface mq-loading">
        <div className="mq-loading-progress"><ProgressBar value={progress} label={stage} /></div>
        <div className="candidate-row mq-loading-candidates">
          {['마네킹 A', '마네킹 B'].map((name) => (
            <div className="candidate" key={name}>
              <div className="big">
                <div className="busy-tile">
                  <Icon name="loader" size={22} className="spin" />작업 중
                </div>
              </div>
              <div className="cap">{name}</div>
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
      <div className="surface">
        <ErrorState
          title="마네킹컷을 만들지 못했어요"
          desc={message || '생성 서버에 일시적인 문제가 발생했어요.'}
          onRetry={onRetry}
        />
      </div>
    </div>
  );
}

// 가운데 "내 옷" 컬럼: 큰 컷(태그 없음) + 버전 썸네일 스트립.
function MineColumn({ selected, cuts, selectedCutId, onSelect }) {
  return (
    <div className="fit-mine-col">
      <div className="fit-mine-img">
        {selected
          ? <img src={cutImage(selected)} alt={`내 마네킹컷 버전 ${selected.version}`} />
          : <div className="busy-tile">마네킹컷이 아직 없어요</div>}
      </div>
      {cuts.length > 1 && (
        <div className="fit-strip" role="group" aria-label="버전 목록">
          {cuts.map((cut) => (
            <button
              type="button"
              key={cut.id}
              className={`fit-ver${cut.id === selectedCutId ? ' on' : ''}`}
              onClick={() => onSelect(cut.id)}
              aria-label={`버전 ${cut.version} 선택`}
              aria-pressed={cut.id === selectedCutId}
            >
              <img src={cutImage(cut)} alt="" />
              <span className="fit-ver-chip">v{cut.version}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// 예시 타일 버튼들(참고용). 이미지 없으면 텍스트 타일로 폴백.
function ExampleTiles({ axisKey, category, gender, values, onPick }) {
  return (
    <>
      {values.map((v) => {
        const img = fitExampleImage(category, gender, axisKey, v.value);
        return (
          <button
            type="button"
            key={v.value}
            role="option"
            aria-selected="false"
            className={`fit-tile${img ? '' : ' text'}`}
            aria-label={`${v.label}(으)로 조정`}
            onClick={() => onPick(v.value, v.label)}
          >
            <span className="fit-tile-tag" aria-hidden="true">예시</span>
            {img
              ? <img src={img} alt="" loading="lazy" />
              : <span className="fit-tile-ph">{v.label}</span>}
            <span className="fit-tile-lb">{v.label}</span>
          </button>
        );
      })}
    </>
  );
}

export function Mannequin() {
  const navigate = useNavigate();
  const [phase, setPhase] = useState('loading');
  const [errorMsg, setErrorMsg] = useState('');
  const [progress, setProgress] = useState(0);
  const [cuts, setCuts] = useState([]);
  const [busy, setBusy] = useState(false);
  const [analysis, setAnalysis] = useState(null);
  const [fitProfileDraft, setFitProfileDraft] = useState(null);
  const [stepState, setStepState] = useState({});
  const [catalogs, setCatalogs] = useState(null);
  const [colorCount, setColorCount] = useState(1);
  const submittingRef = useRef(false);   // 결제(재생성) 이중 제출 방지 — busy 반영 전 연타 차단
  const { push: pushToast } = useToast();

  // 플로우 선택값 — store 가 보유, patchProject 로 서버 동기화 (ADR-0002)
  const projectId = useAppStore((s) => s.projectId);
  const selectedId = useAppStore((s) => s.selectedMannequinId);
  const selectMannequin = useAppStore((s) => s.selectMannequin);
  const composeMode = useAppStore((s) => s.composeMode);
  const setComposeMode = useAppStore((s) => s.setComposeMode);
  const syncCredits = useAppStore((s) => s.syncCredits);
  const mannequinJob = useAppStore((s) => s.mannequinJob);
  const doneBlocked = useDoneGuard();   // 생성 완료 후 초안 재진입 제한 (PRD §10.17)
  const loadRunRef = useRef(0);

  const category = fitProfileDraft?.category;
  const gender = fitProfileDraft?.gender;
  const axisDefs = useMemo(() => axesFor(category, gender), [category, gender]);
  const axisEntries = useMemo(() => Object.entries(axisDefs), [axisDefs]);
  const matchValues = useMemo(() => axesFor('pants', gender)?.cut || [], [gender]);
  const hasMatching = hasMatchFor(category);
  // 순차 확인 스텝 = 축들 + (상의/아우터면) 매칭 의류 핏
  const steps = useMemo(() => {
    const a = axisEntries.map(([key, values]) => ({ key, values, kind: 'axis' }));
    return hasMatching ? [...a, { key: MATCH_KEY, values: matchValues, kind: 'match' }] : a;
  }, [axisEntries, hasMatching, matchValues]);

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
      const [nextProduct, nextAnalysis, nextCatalogs] = await Promise.all([
        api.getProduct(pid),
        api.getAnalysis(pid),
        api.getCatalogs(),
      ]);
      if (loadRunRef.current !== runId) return;
      setProgress(generationProgressFor(pid));
      setAnalysis(nextAnalysis);
      setCatalogs(nextCatalogs);
      setColorCount((nextProduct?.colors || []).length || 1);
      const draft = createFitProfileDraft(nextProduct, nextAnalysis);
      setFitProfileDraft(draft);
      setStepState(initStepState(axesFor(draft.category, draft.gender), hasMatchFor(draft.category)));

      let list = await api.getMannequins(pid);
      if (list.length) {
        updateMannequinJob(pid, { status: 'idle', progress: 100, errorMessage: '' });
      }
      if (loadRunRef.current !== runId) return;
      if (!list.length) {
        const { data, credits } = await requestMannequinGeneration(pid);
        list = extractCuts(data);
        syncCredits(credits);
      }
      if (!list.length) throw new Error('생성된 마네킹컷을 찾지 못했어요. 다시 시도해 주세요.');
      updateMannequinJob(pid, { status: 'idle', progress: 100, errorMessage: '' });
      if (loadRunRef.current !== runId) return;
      setCuts(list);
      const selectedCut = list.find((cut) => cut.isSelected) || list[0];
      if (selectedCut && useAppStore.getState().selectedMannequinId !== selectedCut.id) {
        selectMannequin(selectedCut.id);
      }
      setPhase('ready');
    } catch (err) {
      const message = err?.message || '마네킹 정보를 불러오지 못했어요. 다시 시도해 주세요.';
      if (pid) {
        try {
          const fallback = await api.getMannequins(pid);
          if (fallback.length) {
            updateMannequinJob(pid, { status: 'idle', progress: 100, errorMessage: '' });
            if (loadRunRef.current !== runId) return;
            setCuts(fallback);
            const selectedCut = fallback.find((cut) => cut.isSelected) || fallback[0];
            if (selectedCut && useAppStore.getState().selectedMannequinId !== selectedCut.id) {
              selectMannequin(selectedCut.id);
            }
            setPhase('ready');
            return;
          }
        } catch { /* 원래 생성 실패 메시지를 보여준다. */ }
        updateMannequinJob(pid, {
          status: 'error',
          progress: generationProgressFor(pid),
          errorMessage: message,
        });
      }
      if (loadRunRef.current !== runId) return;
      setErrorMsg(message);
      setPhase('error');
      pushToast(message, { icon: 'alertTri' });
    }
  }, [navigate, selectMannequin, syncCredits, pushToast]);

  useEffect(() => {
    loadMannequins();
    return () => { loadRunRef.current += 1; };
  }, [loadMannequins]);

  const selected = cuts.find((c) => c.id === selectedId) || cuts.find((c) => c.isSelected) || cuts[0];
  const selectedCutId = selected?.id || selectedId;
  const loadingProgress = mannequinJob?.status === 'running'
    && (!projectId || mannequinJob.projectId === projectId)
    ? Math.max(0, Math.min(100, Number(mannequinJob.progress) || 0))
    : progress;

  // 스텝 표시 헬퍼
  const stepName = (step) => (step.kind === 'match' ? MATCH_NAME : (AXIS_LABELS[step.key] || step.key));
  const stepQuestion = (step) => (step.kind === 'match'
    ? MATCH_QUESTION
    : (AXIS_QUESTIONS[step.key] || `${stepName(step)}을(를) 조정할까요?`));
  const stepExCategory = (step) => (step.kind === 'match' ? 'pants' : category);
  const stepExAxis = (step) => (step.kind === 'match' ? 'cut' : step.key);

  // 파생값 — 순차: 첫 미완료 스텝이 '현재'
  const doneCount = steps.filter((s) => axisIsDone(stepState[s.key])).length;
  const allDone = steps.length === 0 || doneCount === steps.length;
  const changedSteps = steps.filter((s) => stepState[s.key]?.mode === 'picked');
  const changedNames = changedSteps.map(stepName);
  const activeIdx = steps.findIndex((s) => !axisIsDone(stepState[s.key]));
  const cur = activeIdx >= 0 ? steps[activeIdx] : null;
  const changingStep = cur && stepState[cur.key]?.mode === 'changing' ? cur : null;
  const needsRegen = changedSteps.length > 0;

  const setStep = (key, patch) => setStepState((prev) => ({ ...prev, [key]: { ...prev[key], ...patch } }));
  const keepStep = (key) => setStep(key, { mode: 'keep', pick: null, pickLb: null });
  const changeStep = (key) => setStep(key, { mode: 'changing' });
  const cancelStep = (key) => setStep(key, { mode: 'pending' });
  const pickStep = (key, value, label) => setStep(key, { mode: 'picked', pick: value, pickLb: label });
  const editStep = (key) => setStep(key, { mode: 'changing', pick: null, pickLb: null });

  const chooseCut = (cutId) => {
    setCuts((prev) => prev.map((cut) => ({ ...cut, isSelected: cut.id === cutId })));
    selectMannequin(cutId);
  };

  // draft + 사용자가 고른 값으로 재생성용 fitProfile 구성. keep=현재값 유지, picked=덮어씀.
  // 매칭 하의(matchCut)도 profile 안에 포함 → 재생성 시 garment_ref 로 함께 저장.
  const buildFitProfile = () => {
    const axes = { ...(fitProfileDraft.axes || {}) };
    let anyPicked = false;
    axisEntries.forEach(([key]) => {
      const s = stepState[key];
      if (s?.mode === 'picked' && s.pick != null) { axes[key] = s.pick; anyPicked = true; }
    });
    const profile = { ...fitProfileDraft, axes, source: anyPicked ? 'seller' : fitProfileDraft.source };
    const m = stepState[MATCH_KEY];
    if (m?.mode === 'picked' && m.pick != null) profile.matchCut = m.pick;
    return profile;
  };

  const regenerate = async () => {
    if (submittingRef.current) return;   // 연타로 인한 이중 재생성·이중 차감 방지
    submittingRef.current = true;
    const profile = buildFitProfile();
    setBusy(true);
    setProgress(0);
    try {
      const { data, credits } = await api.regenerateMannequin(projectId, {
        fitProfile: profile,   // 매칭(matchCut) 포함 — garment_ref 로 저장, 재생성에 반영
        onProgress: setProgress,
      });
      const nextCuts = extractCuts(data);
      setCuts(nextCuts);
      const nextSelected = nextCuts.find((cut) => cut.isSelected) || nextCuts.at(-1);
      if (nextSelected) selectMannequin(nextSelected.id);
      setFitProfileDraft(profile);
      setAnalysis((prev) => ({ ...(prev || {}), fitProfile: profile }));
      syncCredits(credits);
      setStepState(initStepState(axisDefs, hasMatching));   // 새 컷을 다시 확인하는 루프
      pushToast('새 마네킹 버전을 추가했어요. 다시 확인해 주세요.', { icon: 'refresh' });
    } catch (err) {
      pushToast(err?.message || '마네킹 재생성에 실패했어요. 다시 시도해 주세요.', { icon: 'alertTri' });
    } finally {
      setBusy(false);
      submittingRef.current = false;
    }
  };

  const onCta = () => {
    if (!allDone || busy) return;
    if (needsRegen) { regenerate(); return; }
    navigate('/create/storyboard');   // 구성(composeMode)은 store 로 이미 반영됨
  };

  if (phase === 'loading') return <>{doneBlocked && <DoneGuardModal />}<MannequinLoading progress={loadingProgress} /></>;
  if (phase === 'error') return <>{doneBlocked && <DoneGuardModal />}<MannequinError message={errorMsg} onRetry={loadMannequins} /></>;

  const modes = catalogs?.composeModes || [];

  return (
    <div className="wizard wide fit-page">
      {doneBlocked && <DoneGuardModal />}
      <PageHead title="의류 재현성 높이기" sub="실제 의류와 비슷해지게끔 조정해보세요." />

      <div className={`fit-stage${changingStep ? ' comparing' : ''}`}>
        <MineColumn selected={selected} cuts={cuts} selectedCutId={selectedCutId} onSelect={chooseCut} />
        {changingStep && (
          <div className="fit-ex-col">
            <div className="fit-ex-head">원하는 {stepName(changingStep)}의 예시를 선택해주세요.</div>
            <div className="fit-ex-track" role="listbox" aria-label={`${stepName(changingStep)} 예시`}>
              <ExampleTiles
                axisKey={stepExAxis(changingStep)}
                category={stepExCategory(changingStep)}
                gender={gender}
                values={changingStep.values}
                onPick={(value, label) => pickStep(changingStep.key, value, label)}
              />
            </div>
          </div>
        )}
      </div>

      <div className="fit-ask">
        {/* 확인 항목 칩 — 완료 전에도 모든 스텝을 고스트로 표시해 공간을 미리 확보(버튼 밀림 방지) */}
        {steps.length > 0 && (
          <div className="fit-doner" style={{ minHeight: steps.length >= 3 ? 62 : 31 }}>
            {steps.map((step) => {
              const s = stepState[step.key];
              const name = stepName(step);
              if (!axisIsDone(s)) return <span className="fit-chip ghost" key={step.key}>{name}</span>;
              return (
                <span className="fit-chip" key={step.key}>
                  <span className="fit-chip-t">✓ {s.mode === 'keep' ? `${name} 유지` : <>{name} → <b>{s.pickLb}</b></>}</span>
                  <button type="button" className="fit-edit" onClick={() => editStep(step.key)}>수정</button>
                </span>
              );
            })}
          </div>
        )}

        {changingStep ? (
          <div className="fit-changing">
            <span className="fit-changing-t"><b>{stepName(changingStep)}</b> 조정 중… 옆 예시를 골라주세요</span>
            <button type="button" className="fit-cancel" onClick={() => cancelStep(changingStep.key)}>취소</button>
          </div>
        ) : cur ? (
          <>
            <div className="fit-q">{stepQuestion(cur)}</div>
            <div className="fit-choice">
              <button type="button" className="keep" onClick={() => keepStep(cur.key)}>유지하기</button>
              <button type="button" className="change" onClick={() => changeStep(cur.key)}>조정하기</button>
            </div>
            {cur.kind === 'match' && <p className="fit-note">조정하면 새로 생성돼요 · {CREDIT_COSTS.mannequinGenerate} 크레딧</p>}
          </>
        ) : needsRegen ? (
          <div className="fit-final">
            {busy ? (
              <div className="fit-cta-progress"><ProgressBar value={progress} label="마네킹 생성 중" /></div>
            ) : (
              <p className="fit-fmsg"><b>{changedNames.join('·')}</b> 조정했어요 — 다시 생성해서 확인해요</p>
            )}
            <Button variant="primary" size="lg" block disabled={busy} onClick={onCta}>
              수정사항 반영하여 재생성 · {CREDIT_COSTS.mannequinGenerate} 크레딧
            </Button>
          </div>
        ) : (
          <div className="fit-final">
            <div className="fit-q">상세페이지 구성방식을 선택해주세요.</div>
            <div className="fit-cmp2">
              {modes.map((m) => {
                const disabled = m.value === 'extended' && colorCount < 2;
                const on = composeMode === m.value;
                return (
                  <button
                    type="button"
                    key={m.value}
                    className={`fit-cmp${on ? ' on' : ''}${disabled ? ' off' : ''}`}
                    disabled={disabled}
                    aria-pressed={on}
                    onClick={() => setComposeMode(m.value)}
                  >
                    <b>{m.label}</b>
                    <span>{m.desc}</span>
                    {m.count && <em>예상 {m.count}컷</em>}
                    {disabled && <span className="fit-cmp-off">색상 2개 이상부터</span>}
                  </button>
                );
              })}
            </div>
            <Button variant="primary" size="lg" block iconRight="arrowRight" onClick={onCta}>
              이 구성으로 만들기
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}

export default Mannequin;
