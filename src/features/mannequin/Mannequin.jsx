/* =============================================================
   features/mannequin — ③ 마네킹컷 생성·선택 (PRD §7, fit-profile P2)
   컷 목록은 서버 상태, 선택 컷은 store + patchProject 동기화.
   핏 프로필 편집은 로컬 draft 로 유지하고, 재생성 확정 때만 저장한다.
   크레딧은 봉투 응답 { data, credits } 를 syncCredits 로 반영.
   ============================================================= */
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '@/lib/api/index.js';
import { useAppStore } from '@/store/useAppStore.js';
import { CREDIT_COSTS } from '@/lib/limits.js';
import { axesFor, fitProfileCategory } from '@/lib/fitAxes.js';
import { Icon, Button, Modal, ProgressBar, useToast } from '@/components/ui.jsx';
import { PageHead, WizardCTA, useDoneGuard, DoneGuardModal } from '@/features/shell/shell.jsx';
import './Mannequin.css';

const AXIS_LABELS = {
  fit: '핏',
  length: '기장',
  cut: '실루엣',
  silhouette: '실루엣',
};
const CATEGORY_LABELS = {
  top: '상의',
  pants: '팬츠',
  skirt: '스커트',
  dress: '원피스',
  outer: '아우터',
};
const GENDER_LABELS = { women: '여성', men: '남성' };

const cutImage = (cut) => cut?.imageUrl || cut?.src || '';
const isMenOnly = (genders) => Array.isArray(genders) && genders.length > 0 && genders.every((g) => g === 'men');
const validAxisValue = (values, value) => values.some((v) => v.value === value);

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
  return { category, gender, axes, source, version: 1 };
}

function extractCuts(envelope) {
  if (Array.isArray(envelope)) return envelope;
  if (Array.isArray(envelope?.cuts)) return envelope.cuts;
  if (Array.isArray(envelope?.data?.cuts)) return envelope.data.cuts;
  return [];
}

function MannequinLoading({ progress }) {
  const stage = progress < 30 ? '의류 정보 분석 중' : progress < 60 ? '핏과 실루엣 정리 중'
    : progress < 80 ? '마네킹 생성 중' : '결과를 확인하는 중';
  return (
    <div className="wizard">
      <PageHead title="마네킹컷을 만들고 있어요" sub="AI가 상품의 핏과 실루엣을 기준 마네킹에 입혀보고 있어요." />
      <div className="surface mq-loading">
        <div className="mq-loading-progress"><ProgressBar value={progress} label={stage} /></div>
        <div className="mq-loading-preview">
          <div className="mq-loading-frame">
            <div className="busy-tile">
              <Icon name="loader" size={22} className="spin" />작업 중
            </div>
          </div>
          <div className="mq-loading-caption">마네킹 생성 중</div>
        </div>
      </div>
    </div>
  );
}

function AxisPills({ axis, values, selectedValue, autoValue, showAutoBadge, onChange }) {
  const options = [{ value: null, label: '사진 그대로' }, ...values];
  return (
    <div className="fit-axis">
      <div className="fit-axis-head">
        <label className="lbl">{AXIS_LABELS[axis] || axis}</label>
      </div>
      <div className="fit-pill-row">
        {options.map((option) => {
          const selected = selectedValue === option.value;
          const auto = showAutoBadge && option.value != null && option.value === autoValue;
          return (
            <button
              type="button"
              key={option.value ?? 'origin'}
              className={`chip fit-pill${selected ? ' on' : ''}${auto ? ' has-ai' : ''}`}
              onClick={() => onChange(axis, option.value)}
            >
              <span>{option.label}</span>
              {auto && <span className="fit-ai-badge">✦ AI 추정</span>}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function FitProfilePanel({ product, analysis, draft, onDraftChange, busy, progress, onRegenerate }) {
  const axisDefs = useMemo(() => axesFor(draft?.category, draft?.gender), [draft?.category, draft?.gender]);
  const autoValues = useMemo(() => autoAxisValues(axisDefs, analysis), [axisDefs, analysis]);
  const entries = Object.entries(axisDefs);

  if (!draft) {
    return (
      <div className="surface inspector fit-profile-panel">
        <div className="sec-title">핏 프로필</div>
        <p className="hint fit-empty">분석 정보를 불러오고 있어요.</p>
      </div>
    );
  }

  const changeAxis = (axis, value) => {
    onDraftChange({
      ...draft,
      axes: { ...draft.axes, [axis]: value },
      source: 'seller',
    });
  };
  const visibleEntries = entries.filter(([axis]) => !(draft.category === 'pants' && axis === 'length'));
  const pantsLength = entries.find(([axis]) => axis === 'length');
  const showAutoBadge = draft.source === 'auto';

  return (
    <div className="surface inspector fit-profile-panel">
      <div className="sec-title">핏 프로필</div>
      <div className="fit-profile-meta">
        <span>{CATEGORY_LABELS[draft.category] || product?.clothingType || '상품'}</span>
        <span>{GENDER_LABELS[draft.gender]}</span>
      </div>

      <div className="fit-axis-list">
        {visibleEntries.map(([axis, values]) => (
          <AxisPills
            key={axis}
            axis={axis}
            values={values}
            selectedValue={draft.axes?.[axis] ?? null}
            autoValue={autoValues[axis]}
            showAutoBadge={showAutoBadge}
            onChange={changeAxis}
          />
        ))}
        {pantsLength && (
          <details className="insp-extra fit-length-details">
            <summary><Icon name="chevDown" size={15} />기장</summary>
            <AxisPills
              axis="length"
              values={pantsLength[1]}
              selectedValue={draft.axes?.length ?? null}
              autoValue={autoValues.length}
              showAutoBadge={showAutoBadge}
              onChange={changeAxis}
            />
          </details>
        )}
      </div>

      {busy && (
        <div className="fit-regenerate-progress">
          <ProgressBar value={progress} label="마네킹 생성 중" />
        </div>
      )}
      <Button
        variant="primary"
        block
        className="btn-glowring fit-regenerate-btn"
        disabled={busy || !entries.length}
        onClick={onRegenerate}
      >
        다시 생성 · {CREDIT_COSTS.mannequinGenerate} 크레딧
      </Button>
    </div>
  );
}

function SelectedCutCard({ selected }) {
  return (
    <div className="mq-selected-card">
      <div className="mq-selected-image">
        {selected ? (
          <>
            <img src={cutImage(selected)} alt={`마네킹 버전 ${selected.version}`} />
            <span className="pill pill-ink mq-selected-flag">선택됨</span>
          </>
        ) : (
          <div className="busy-tile">마네킹컷이 아직 없어요</div>
        )}
      </div>
      <div className="mq-selected-meta">
        <div className="sec-title">마네킹컷 선택</div>
        <div className="sec-sub">선택한 컷이 실제 착용컷 생성의 기준이 돼요.</div>
        {selected && <div className="mq-version-label">버전 {selected.version}</div>}
      </div>
    </div>
  );
}

function VersionHistory({ cuts, selectedId, onSelect }) {
  return (
    <div className="mq-history-block">
      <div className="mq-history-head">
        <div className="sec-title">버전 히스토리</div>
        <div className="sec-sub">새로 생성한 컷은 여기에 추가돼요.</div>
      </div>
      <div className="mq-version-strip">
        {cuts.map((cut) => (
          <button
            type="button"
            key={cut.id}
            className={`mq-version-card${cut.id === selectedId ? ' on' : ''}`}
            onClick={() => onSelect(cut.id)}
          >
            <img src={cutImage(cut)} alt={`버전 ${cut.version}`} />
            <span className="mq-version-chip">v{cut.version}</span>
          </button>
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
      <div className="sec-title">상세페이지 구성</div>
      <div className="cm-mini-name">
        <Icon name="layout" size={15} />{cur.label}
      </div>
      <div className="md">{cur.desc}</div>
      <div className="mode-flow">{cur.flow.map((f, i) => <span className="flow-pill" key={i}>{f}</span>)}</div>
      {cur.count && <div className="cm-count">예상 {cur.count}컷</div>}
      <Button variant={picking ? 'primary' : 'ghost'} block icon={picking ? 'check' : 'layout'} onClick={onToggle} className="compose-mini-btn">
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
                  {disabled && <div className="hint mode-disabled-hint">색상 2개 이상부터</div>}
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
  const [busy, setBusy] = useState(false);
  const [product, setProduct] = useState(null);
  const [analysis, setAnalysis] = useState(null);
  const [fitProfileDraft, setFitProfileDraft] = useState(null);
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
  const syncCredits = useAppStore((s) => s.syncCredits);
  const doneBlocked = useDoneGuard();   // 생성 완료 후 초안 재진입 제한 (PRD §10.17)

  useEffect(() => {
    window.scrollTo({ top: 0 });
    document.querySelector('.app-main')?.scrollTo({ top: 0 });
    let cancelled = false;
    (async () => {
      await useAppStore.getState().loadProject();
      if (cancelled) return;
      const pid = useAppStore.getState().projectId;
      const [nextProduct, nextAnalysis, nextCatalogs] = await Promise.all([
        api.getProduct(pid),
        api.getAnalysis(pid),
        api.getCatalogs(),
      ]);
      if (cancelled) return;
      setProduct(nextProduct);
      setAnalysis(nextAnalysis);
      setCatalogs(nextCatalogs);
      setColorCount((nextProduct?.colors || []).length || 1);
      setFitProfileDraft(createFitProfileDraft(nextProduct, nextAnalysis));

      let list = await api.getMannequins(pid);
      if (cancelled) return;
      if (!list.length) {
        const { data, credits } = await api.generateMannequins(pid, { onProgress: setProgress });
        list = extractCuts(data);
        syncCredits(credits);
      }
      if (cancelled) return;
      setCuts(list);
      const selectedCut = list.find((cut) => cut.isSelected) || list[0];
      if (selectedCut && useAppStore.getState().selectedMannequinId !== selectedCut.id) {
        selectMannequin(selectedCut.id);
      }
      setPhase('ready');
    })().catch((err) => {
      if (!cancelled) toast.push(err?.message || '마네킹 정보를 불러오지 못했어요. 다시 시도해 주세요.', { icon: 'alertTri' });
    });
    return () => { cancelled = true; };
  }, []);

  const selected = cuts.find((c) => c.id === selectedId) || cuts.find((c) => c.isSelected) || cuts[0];
  const selectedCutId = selected?.id || selectedId;

  const chooseCut = (cutId) => {
    setCuts((prev) => prev.map((cut) => ({ ...cut, isSelected: cut.id === cutId })));
    selectMannequin(cutId);
  };

  const regenerate = async () => {
    if (!fitProfileDraft || busy) return;
    setBusy(true);
    setProgress(0);
    try {
      const { data, credits } = await api.regenerateMannequin(projectId, {
        fitProfile: fitProfileDraft,
        onProgress: setProgress,
      });
      const nextCuts = extractCuts(data);
      setCuts(nextCuts);
      const nextSelected = nextCuts.find((cut) => cut.isSelected) || nextCuts.at(-1);
      if (nextSelected) selectMannequin(nextSelected.id);
      setAnalysis((prev) => ({ ...(prev || {}), fitProfile: fitProfileDraft }));
      syncCredits(credits);
      toast.push('새 마네킹 버전을 추가했어요', { icon: 'refresh' });
    } catch (err) {
      toast.push(err?.message || '마네킹 재생성에 실패했어요. 다시 시도해 주세요.', { icon: 'alertTri' });
    } finally {
      setBusy(false);
    }
  };

  if (phase === 'loading') return <>{doneBlocked && <DoneGuardModal />}<MannequinLoading progress={progress} /></>;

  const modes = catalogs?.composeModes || [];
  const pickMode = (v) => { setComposeMode(v); setPicking(false); };
  const mainPanel = (
    <div className="surface mq-main-surface">
      <SelectedCutCard selected={selected} />
      <VersionHistory cuts={cuts} selectedId={selectedCutId} onSelect={chooseCut} />
    </div>
  );
  const rightCol = (
    <div className="mq-side">
      <FitProfilePanel
        product={product}
        analysis={analysis}
        draft={fitProfileDraft}
        onDraftChange={setFitProfileDraft}
        busy={busy}
        progress={progress}
        onRegenerate={() => setConfirmRegen(true)}
      />
      <ComposeModeMini modes={modes} mode={composeMode} colorCount={colorCount} picking={picking} onToggle={() => setPicking((p) => !p)} onPick={pickMode} />
    </div>
  );

  return (
    <div className="wizard wide">
      {doneBlocked && <DoneGuardModal />}
      <PageHead title="마네킹컷을 확인하고 핏 프로필을 조정해주세요" sub="프로필 수정은 무료이며, 다시 생성하면 새 버전이 히스토리에 추가돼요." />
      <div className="mannequin-layout">{mainPanel}{rightCol}</div>
      <WizardCTA>
        <Button variant="primary" size="lg" iconRight="arrowRight" onClick={() => navigate('/create/storyboard')}>상세페이지 초안 만들기</Button>
      </WizardCTA>

      {confirmRegen && (
        <Modal onClose={() => setConfirmRegen(false)}>
          <h3>새 마네킹 버전을 생성할까요?</h3>
          <p>현재 핏 프로필로 새 버전을 히스토리에 추가해요. 기존 버전은 그대로 보관됩니다.</p>
          <div className="modal-actions">
            <Button variant="ghost" onClick={() => setConfirmRegen(false)}>취소</Button>
            <Button variant="primary" disabled={busy} onClick={() => { setConfirmRegen(false); regenerate(); }}>
              생성하기 · {CREDIT_COSTS.mannequinGenerate} 크레딧
            </Button>
          </div>
        </Modal>
      )}
    </div>
  );
}

export default Mannequin;
