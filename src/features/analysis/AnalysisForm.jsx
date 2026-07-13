/* =============================================================
   features/analysis — ② AI 분석·세부 확인 (PRD §6)
   Ported verbatim from reference/prototype/features/analysis.jsx.
   Only change: ES imports + exports (was window globals). Markup,
   classNames, inline styles unchanged.
   ============================================================= */
import { useState, useEffect, useCallback, useRef } from 'react';
import { api } from '@/lib/api/index.js';
import { listModels } from '@/lib/api/facemarket.js';
import { useAppStore } from '@/store/useAppStore.js';
import { Icon, Chips, Button, Skeleton, ErrorState, useToast } from '@/components/ui.jsx';
import { PageHead, WizardCTA } from '@/features/shell/shell.jsx';
import { axesFor, fitProfileCategory } from '@/lib/fitAxes.js';

// 글자 폭 추정(em) — 한글 ≈1em, 그 외 ≈0.55em. '직접 입력' pill이 다른 칩과 같은 크기로
// 시작해 내용 길이만큼만 유동 확장되게 하는 계산 (2026-07-13 사용자 피드백).
const chWidth = (s) => [...s].reduce((n, ch) => n + (/[가-힣]/.test(ch) ? 1 : 0.55), 0).toFixed(1);
import { CREDIT_COSTS } from '@/lib/limits.js';

export const isMatchRecommendationPatch = (patch) => ['clothingType', 'targetGenders', 'styleTags'].some((key) => key in patch);

// 남성 단독일 때만 'men' — mannequin.py select_base_gender 와 동일 규칙 (핏 프로필 성별 키)
const genderOf = (genders) => {
  const g = (genders || []).map((x) => String(x).toLowerCase());
  return g.length && g.every((x) => ['men', 'male', '남성', '남'].includes(x)) ? 'men' : 'women';
};

// ── 분석 대기 연출 (A안 · 단계 체크리스트 — 2026-07-13 확정, mockups/analysis-waiting-concepts.html) ──
// 자연 페이스 합 ~9초 = prod 실측 분석(8~12초)의 앞쪽에 맞춤. 결과(done)가 먼저 오면 잔여 단계를
// 고속(180ms) 완주한 뒤 onFinished — 애니메이션 끝과 화면 전환이 맞물린다. 늦으면 마지막 단계
// 스피너로 은은히 대기(멈춘 느낌 방지). 퍼센트 숫자 금지(마네킹 대기화면과 동일 결정).
const ANALYZE_STEPS = ['사진 확인', '종류·핏 판별', '소재 추정', '특징 발굴', '매칭 의류 선정'];
const STEP_DUR = [1200, 2200, 1600, 2200, 1800];
const FAST_DUR = 180;

export function AnalysisProgress({ photoSrc, done, onFinished }) {
  const [doneCount, setDoneCount] = useState(0);   // 완료된 단계 수 (0..5)
  const finishedRef = useRef(false);
  const onFinishedRef = useRef(onFinished);
  onFinishedRef.current = onFinished;

  useEffect(() => {
    if (doneCount >= ANALYZE_STEPS.length) {       // 전 단계 완료 → 살짝 여운 후 전환
      if (finishedRef.current) return;
      finishedRef.current = true;
      const t = setTimeout(() => onFinishedRef.current?.(), 400);
      return () => clearTimeout(t);
    }
    // 마지막 단계는 결과가 도착해야만 완료 — 그 전엔 스피너 유지 (분석 최대시간 커버)
    if (!done && doneCount === ANALYZE_STEPS.length - 1) return;
    const t = setTimeout(() => setDoneCount((n) => n + 1), done ? FAST_DUR : STEP_DUR[doneCount]);
    return () => clearTimeout(t);
  }, [doneCount, done]);

  return (
    <div className="ap-stage surface">
      {photoSrc && <img className="ap-photo" src={photoSrc} alt="" />}
      <div className="ap-body">
        <div className="ap-title">AI가 상품을 분석하고 있어요</div>
        {ANALYZE_STEPS.map((s, i) => (
          <div key={s} className={`ap-step${i < doneCount ? ' done' : i === doneCount ? ' run' : ''}`}>
            <span className="ap-dot" />{s}
          </div>
        ))}
        <div className="ap-bar"><i style={{ width: `${(doneCount / ANALYZE_STEPS.length) * 100}%` }} /></div>
      </div>
    </div>
  );
}

export function AnalysisSkeleton() {
  const chipRow = (ws) => <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>{ws.map((w, i) => <Skeleton key={i} w={w} h={34} r={9999} />)}</div>;
  const fieldRow = (ws, i) => (
    <div className="field-row" key={i}>
      <Skeleton w={52} h={13} r={4} />
      {chipRow(ws)}
    </div>
  );
  const secHead = (tw, sw) => (
    <div className="sec-head"><div>
      <Skeleton w={tw} h={18} r={5} />
      <Skeleton w={sw} h={13} r={4} style={{ marginTop: 9 }} />
    </div></div>
  );
  const cards = (n, ws) => (
    <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
      {Array.from({ length: n }).map((_, i) => (
        <div key={i} style={{ width: 110 }}>
          <Skeleton h={130} r={8} />
          <Skeleton w={ws} h={12} r={4} style={{ marginTop: 8 }} />
        </div>
      ))}
    </div>
  );
  return (
    <div className="af-skeleton" aria-busy="true">
      {/* 진행 헤더는 AnalysisProgress(단계 체크리스트)가 담당 — 여기는 폼 뼈대만 */}
      <div className="merged-card" style={{ marginTop: 16 }}>
        {/* 기본 정보 */}
        <div className="surface">
          <Skeleton w={70} h={18} r={5} style={{ marginBottom: 20 }} />
          <div className="basic-fields">
            {[[64, 64, 72, 56], [60, 68, 56, 64], [52, 52], [64, 52, 72, 60]].map((ws, i) => fieldRow(ws, i))}
          </div>
        </div>
        {/* 소재 */}
        <div className="surface">
          {secHead(50, 200)}
          {chipRow([96, 112, 80])}
        </div>
        {/* 실측 정보 */}
        <div className="surface">
          {secHead(70, 220)}
          <div className="measure-grid">
            {[0, 1, 2, 3].map((i) => (
              <div className="measure-cell" key={i}>
                <Skeleton w={48} h={12} r={4} />
                <Skeleton h={44} r={8} />
              </div>
            ))}
          </div>
        </div>
        {/* 강조하고 싶은 특징 */}
        <div className="surface">
          {secHead(118, 230)}
          {chipRow([90, 72, 124, 96, 108])}
        </div>
        {/* 모델 선택 */}
        <div className="surface">
          <Skeleton w={70} h={18} r={5} />
          <Skeleton w={200} h={13} r={4} style={{ margin: '9px 0 16px' }} />
          {cards(3, 56)}
        </div>
        {/* 매칭 의류 */}
        <div className="surface">
          <Skeleton w={70} h={18} r={5} />
          <Skeleton w={220} h={13} r={4} style={{ margin: '9px 0 16px' }} />
          {cards(4, 64)}
        </div>
      </div>
    </div>
  );
}

export function AnalysisForm({ inline, analysis, catalogs, onChange, onNext }) {
  const a = analysis;
  const toast = useToast();
  const [washing, setWashing] = useState(false);
  const [spDraft, setSpDraft] = useState('');
  const [ccDraft, setCcDraft] = useState(a.customCategory || '');   // 직접 입력 pill (blur 커밋)
  useEffect(() => { setCcDraft(a.customCategory || ''); }, [a.customCategory]);
  const [spAdding, setSpAdding] = useState(false);
  const [editMatIdx, setEditMatIdx] = useState(null);
  const matTotal = (a.materials || []).reduce((s, m) => s + (Number(m.ratio) || 0), 0);
  const matOver = matTotal > 100;
  // 인물 모델 카탈로그 — FaceMarket 검증 모델(GET /v1/facemarket/models, listModels()).
  // 정적 시드를 버리고 런타임 로드한다. 라이선스가 활성인(hasActiveLicense) 모델만 선택 가능.
  const [models, setModels] = useState([]);
  const [modelsLoading, setModelsLoading] = useState(true);
  useEffect(() => {
    let alive = true;
    listModels()
      .then((list) => { if (alive) setModels(Array.isArray(list) ? list : []); })
      .catch(() => { if (alive) setModels([]); })   // 카탈로그 실패는 비치명 — 빈 그리드로 안내만
      .finally(() => { if (alive) setModelsLoading(false); });
    return () => { alive = false; };
  }, []);

  // AI 추천 특징은 일단 강제로 칩에 채워둔다 (사용자가 지우면 빠짐). 최대 5개.
  useEffect(() => {
    const ai = a.aiSuggestedPoints || [];
    const missing = ai.filter((p) => !a.sellingPoints.includes(p));
    if (missing.length) onChange({ sellingPoints: [...a.sellingPoints, ...missing].slice(0, 5) });
  }, []);

  // 카탈로그 로드 후 선택값이 없거나 더 이상 라이선스 활성 모델이 아니면 첫 라이선스 활성 모델로 자동 선택.
  // (구 정적 selectedModelId 'mA' 등도 여기서 실 fm_models.id(UUID)로 교체 → 생성 게이트가 해석 가능.)
  useEffect(() => {
    const licensable = models.filter((m) => m.hasActiveLicense);
    if (licensable.length && !licensable.some((m) => m.id === a.selectedModelId)) {
      onChange({ selectedModelId: licensable[0].id });
    }
  }, [models]);
  const aiSet = new Set(a.aiSuggestedPoints || []);

  const commitSp = () => {
    const t = spDraft.trim();
    if (!t) { setSpAdding(false); setSpDraft(''); return; }
    onChange({ sellingPoints: [...a.sellingPoints, t] });
    setSpDraft(''); setSpAdding(false);
  };

  const subCats = catalogs.subCategories[a.clothingType] || [];
  const selMatch = (a.matchClothing || []).filter((c) => c.selected).sort((x, y) => (x.selOrder || 0) - (y.selOrder || 0));
  const mainMatchId = selMatch[0]?.id;
  const subMatchId = selMatch[1]?.id;
  const toggleMatch = (id) => {
    const cur = a.matchClothing;
    const item = cur.find((c) => c.id === id);
    if (item.selected) {
      onChange({ matchClothing: cur.map((c) => c.id === id ? { ...c, selected: false, selOrder: undefined } : c) });
    } else {
      if (selMatch.length >= 2) { toast.push('매칭 의류는 최대 2개까지 선택할 수 있어요'); return; }
      const maxOrder = Math.max(0, ...cur.map((c) => c.selOrder || 0));
      onChange({ matchClothing: cur.map((c) => c.id === id ? { ...c, selected: true, selOrder: maxOrder + 1 } : c) });
    }
  };
  const setMat = (i, patch) => onChange({ materials: a.materials.map((m, j) => j === i ? { ...m, ...patch } : m) });
  const draftWash = async () => {
    setWashing(true);
    try {
      const t = await api.draftWashCare(useAppStore.getState().projectId);
      onChange({ washCare: t });
      toast.push('AI 초안을 채웠어요 · 실제 케어라벨과 확인해주세요', { icon: 'sparkles' });
    } catch (e) {
      toast.push(e.message || '세탁 초안 생성에 실패했어요', { icon: 'alert' });
    } finally { setWashing(false); }
  };
  // ── 핏 = fitProfile.axes.fit 의 셀러 편집기 (spec §1 — '핏' 개념이 두 번 보이지 않게 단일화) ──
  // 값 세트는 카테고리×성별로 fitAxes 에서 파생 (여성 상의 = 타이트~오버 5단 등). 원피스는 핏 축 없음 → 행 숨김.
  const fitOptsOf = (draft) => {
    const cat = fitProfileCategory(draft.clothingType, draft.subCategory) || 'top';
    const values = axesFor(cat, genderOf(draft.targetGenders)).fit || [];
    return { cat, opts: values.map(({ value, label }) => ({ value, label })) };
  };
  // patch 적용 후의 핏·fitProfile 을 함께 산출. 카테고리·성별 변경으로 기존 값이 무효면 regular(없으면 첫 값)로 방어 리셋.
  const withFitProfile = (patch, source) => {
    const next = { ...a, ...patch };
    const { cat, opts } = fitOptsOf(next);
    let fit = 'fit' in patch ? patch.fit : next.fit;
    let src = source;
    if (!opts.length) fit = null; // 원피스 등 핏 축 없는 카테고리
    else if (!opts.some((o) => o.value === fit)) { fit = opts.some((o) => o.value === 'regular') ? 'regular' : opts[0].value; src = 'auto'; }
    const prev = next.fitProfile;
    const axes = prev?.category === cat ? { ...(prev.axes || {}) } : {}; // 카테고리 바뀌면 타 축(컷·기장 등) 무효 → 리셋
    if (fit === null) delete axes.fit; else axes.fit = fit;
    return { ...patch, fit, fitProfile: {
      category: cat, gender: genderOf(next.targetGenders), axes,
      source: src ?? prev?.source ?? 'auto', version: 1,
    } };
  };
  // subCategory 는 영문 토큰, 실측 key 는 MeasurementKey — 라벨은 catalogs 에서 파생 (계약 §4)
  const changeType = (t) => onChange(withFitProfile({ clothingType: t, subCategory: (catalogs.subCategories[t] || [])[0]?.value ?? null,
    measurements: (catalogs.measurementSchema[t] || []).map((k) => ({ key: k, value: null, unit: 'cm' })) }));
  const setMeasure = (key, value) => onChange({ measurements: (a.measurements || []).map((m) => m.key === key ? { ...m, value: value === '' ? null : Number(value) } : m) });
  const typeLabel = catalogs.clothingTypes.find((t) => t.value === a.clothingType)?.label;
  const fitOpts = fitOptsOf(a).opts;

  const sections = (
    <>
      {/* 1. basic info */}
      <div className="surface">
        <div className="sec-title" style={{ marginBottom: 20 }}>기본 정보</div>
        <div className="basic-fields">
          <div className="field-row"><label className="lbl">의류 종류</label>
            <Chips options={catalogs.clothingTypes} value={a.clothingType} onChange={changeType} /></div>
          {/* 세부 카테고리 — enum 칩 + 같은 줄 끝의 '직접 입력' pill (2026-07-13 사용자 결정:
              별도 줄이 아니라 칩처럼). enum 선택 ↔ 직접 입력은 배타: 칩을 고르면 custom을
              비우고, custom을 쓰면 칩 해제. AI 추측(customCategory)이 있으면 pill에 채워짐.
              저장은 blur/Enter 커밋, key로 분석 갱신 시 리셋(소재 인라인 편집 관례). */}
          <div className="field-row"><label className="lbl">세부 카테고리</label>
            <Chips options={subCats} value={a.subCategory}
              onChange={(v) => onChange(withFitProfile({ subCategory: v, customCategory: null }))}
              trailing={
                <input
                  className={`chip chip-input${a.customCategory && !a.subCategory ? ' on' : ''}`}
                  value={ccDraft} maxLength={20} placeholder="직접 입력"
                  style={{ width: `calc(${chWidth(ccDraft || '직접 입력')}em + 32px)` }}
                  onChange={(e) => setCcDraft(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur(); }}
                  onBlur={() => {
                    const v = ccDraft.trim();
                    if (v === (a.customCategory || '')) return;
                    onChange(withFitProfile(v ? { customCategory: v, subCategory: null } : { customCategory: null }));
                  }} />
              } /></div>
          <div className="field-row"><label className="lbl">대상 성별</label>
            <Chips options={catalogs.genders} value={a.targetGenders?.[0] || null} onChange={(v) => onChange(withFitProfile({ targetGenders: v ? [v] : [] }))} /></div>
          {fitOpts.length > 0 && (
            <div className="field-row"><label className="lbl">핏</label>
              <Chips options={fitOpts} value={a.fit} onChange={(v) => onChange(withFitProfile({ fit: v }, 'seller'))} /></div>
          )}
        </div>
      </div>

      {/* 2. materials */}
      <div className="surface">
        <div className="sec-head"><div><div className="sec-title">소재</div><div className="sec-sub">혼용률을 입력해주세요. 합계 100%를 권장해요.</div></div></div>
        <div className="material-chips">
            {a.materials.map((m, i) => (
              editMatIdx === i ? (
                <span className="mat-chip draft editing" key={i}
                  onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget)) { setEditMatIdx(null); if (!(m.name || '').trim() && !m.ratio) onChange({ materials: a.materials.filter((_, j) => j !== i) }); } }}>
                  <input className="mc-name" autoFocus value={m.name}
                    onChange={(e) => setMat(i, { name: e.target.value })}
                    onKeyDown={(e) => { if (e.key === 'Enter') setEditMatIdx(null); }} />
                  <span className="mc-div" />
                  <input className="mc-ratio" type="number" inputMode="numeric" min="0" max="100" value={m.ratio || ''}
                    onChange={(e) => setMat(i, { ratio: Number(e.target.value.replace(/[^0-9]/g, '').slice(0, 3)) || 0 })}
                    onKeyDown={(e) => { if (e.key === 'Enter') setEditMatIdx(null); }} /><span className="mc-pct">%</span>
                  <button className="mc-x" onMouseDown={(e) => e.preventDefault()} onClick={() => { setEditMatIdx(null); onChange({ materials: a.materials.filter((_, j) => j !== i) }); }}><Icon name="x" size={12} /></button>
                </span>
              ) : (
                <span className="mat-chip done" key={i} role="button" tabIndex={0} title="클릭해서 수정"
                  onClick={() => setEditMatIdx(i)} onKeyDown={(e) => { if (e.key === 'Enter') setEditMatIdx(i); }}>
                  <span className="mc-text">{m.name || '소재'}</span>
                  <span className="mc-div" />
                  <span className="mc-val">{m.ratio || 0}%</span>
                  <button className="mc-x" onClick={(e) => { e.stopPropagation(); onChange({ materials: a.materials.filter((_, j) => j !== i) }); }}><Icon name="x" size={12} /></button>
                </span>
              )
            ))}
            <button className="mat-add" onClick={() => { onChange({ materials: [...a.materials, { name: '', ratio: 0 }] }); setEditMatIdx(a.materials.length); }}>
              <Icon name="plus" size={14} />소재 추가
            </button>
          </div>
          {matOver && <p className="mat-warn"><Icon name="alertTri" size={14} />혼용률 합계가 100%를 넘었어요 (현재 {matTotal}%). 다시 확인해주세요.</p>}
      </div>

      {/* 3. measurements */}
      <div className="surface">
        <div style={{ marginBottom: 16 }}>
          <div className="sec-title">실측 정보</div>
          <div className="sec-sub">실측정보를 입력하면 상품의 사실성이 더욱 향상돼요. · {typeLabel}</div>
        </div>
        {!a.measurementsUnknown && (
          <div className="measure-grid">
            {(a.measurements || []).map((m) => (
              <div className="measure-cell" key={m.key}>
                <label className="lbl" style={{ fontWeight: 400, color: 'var(--fg-2)', fontSize: 12.5 }}>{(catalogs.measurementLabels || {})[m.key] || m.key}</label>
                <div className="mfield"><input type="number" placeholder="0" value={m.value ?? ''} onChange={(e) => setMeasure(m.key, e.target.value)} /><span className="u">cm</span></div>
              </div>
            ))}
          </div>
        )}
        <label className={`check-row${a.measurementsUnknown ? ' on' : ''}`} style={{ marginTop: a.measurementsUnknown ? 0 : 16 }}>
          <input type="checkbox" checked={a.measurementsUnknown} onChange={(e) => onChange({ measurementsUnknown: e.target.checked })} />
          <span className="check-box"><Icon name="check" size={12} /></span>
          실측 모름
        </label>
      </div>

      {/* 4. selling points — chips */}
      <div className="surface">
        <div className="sec-head"><div><div className="sec-title">강조하고 싶은 특징</div>
          <div className="sec-sub">상세페이지에서 가장 강조될 핵심 포인트예요. 최대 5개까지 넣을 수 있어요.</div></div>
          <span className="pill pill-soft">{a.sellingPoints.length}/5개</span></div>
        <div className="sp-chipwrap">
          {a.sellingPoints.map((p, i) => (
            <span className={`sp-chip${aiSet.has(p) ? ' ai' : ''}`} key={i}>
              {aiSet.has(p) && <span className="sp-ai-tag">AI 제안</span>}
              {p}
              <button className="sp-chip-x" onClick={() => onChange({ sellingPoints: a.sellingPoints.filter((_, j) => j !== i), aiSuggestedPoints: (a.aiSuggestedPoints || []).filter((x) => x !== p) })}><Icon name="x" size={12} /></button>
            </span>
          ))}
          {a.sellingPoints.length < 5 && (
            spAdding ? (
              <span className="sp-chip draft">
                <input className="sp-draft-input" autoFocus placeholder="특징 입력 후 Enter" value={spDraft}
                  onChange={(e) => setSpDraft(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') commitSp(); else if (e.key === 'Escape') { setSpAdding(false); setSpDraft(''); } }}
                  onBlur={commitSp} />
              </span>
            ) : (
              <button className="sp-add" onClick={() => setSpAdding(true)}><Icon name="plus" size={14} />추가하기</button>
            )
          )}
        </div>
      </div>

      {/* 5. model select — FaceMarket 검증 모델 카탈로그(라이선스 활성 모델만 선택 가능) */}
      <div className="surface">
        <div className="sec-title" style={{ marginBottom: 6 }}>모델 선택</div>
        <div className="sec-sub" style={{ marginBottom: 16 }}>검증된 얼굴 라이선스 모델이에요 · 라이선스가 활성인 모델만 선택할 수 있어요.</div>
        {modelsLoading ? (
          <div className="hint">검증 모델을 불러오는 중이에요…</div>
        ) : models.length === 0 ? (
          <div className="hint">아직 등록된 검증 모델이 없어요.</div>
        ) : (
          <div className="model-grid">
            {models.map((m) => {
              const selectable = !!m.hasActiveLicense;
              const on = a.selectedModelId === m.id;
              return (
                <div key={m.id}
                  className={`model-card fm-model${on ? ' on' : ''}${selectable ? '' : ' disabled'}`}
                  onClick={() => selectable && onChange({ selectedModelId: m.id })}
                  title={selectable ? m.displayName : '활성 라이선스가 없어 선택할 수 없어요'}>
                  {m.coverImageUrl
                    ? <img src={m.coverImageUrl} alt={m.displayName} />
                    : <div className="fm-empty"><Icon name="person" size={24} /></div>}
                  {m.status === 'verified' && <span className="fm-verified"><Icon name="check" size={11} />검증</span>}
                  <div className="fm-meta">
                    <div className="fm-name">{m.displayName}{on && <Icon name="check" size={13} className="star" />}</div>
                    <div className="fm-price">
                      {selectable && m.unitPrice != null
                        ? `₩${Number(m.unitPrice).toLocaleString('ko-KR')}/건`
                        : '라이선스 없음'}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* 6. match clothing — full width */}
      <div className="surface">
        <div className="sec-title" style={{ marginBottom: 6 }}>매칭 의류</div>
        <div className="sec-sub" style={{ marginBottom: 16 }}>스타일링컷 생성에 쓰여요 · 메인 최대 2개</div>
        <div className="model-grid">
          {a.matchClothing.map((m) => (
            <div key={m.id} className={`model-card${m.selected ? ' on' : ''}`} style={{ width: 110 }}
              onClick={() => toggleMatch(m.id)}>
              <img src={m.thumb} alt={m.name} style={{ height: 130 }} />
              {m.id === mainMatchId && <span className="match-role main">메인</span>}
              {m.id === subMatchId && <span className="match-role sub">서브</span>}
              <div className="nm">{m.name}{m.selected && <Icon name="check" size={13} className="star" />}</div>
            </div>
          ))}
        </div>
      </div>
    </>
  );

  // 마네킹 최초 생성은 다음 페이지 진입 시 자동 차감 — 차감 직전 마지막 행동인 이 버튼에 예고 (PRD §7.7)
  const cta = <Button variant="primary" size="lg" iconRight="arrowRight" onClick={onNext}>의류정보 확정 완료 · {CREDIT_COSTS.mannequinGenerate} 크레딧</Button>;

  if (inline) {
    return (
      <>
        <div className="af-inline-head"><div><div className="af-head-title">AI가 분석한 정보예요</div><div className="hint" style={{ marginTop: 2 }}>틀린 부분이 있으면 직접 수정해주세요.</div></div></div>
        <div className="af-body af-cards merged-card">{sections}</div>
        <WizardCTA>{cta}</WizardCTA>
      </>
    );
  }

  return (
    <div className="wizard">
      <PageHead title="AI가 상품 정보를 분석했어요" sub="틀린 부분이 있으면 직접 수정해주세요." />
      <div className="af-body af-cards merged-card">{sections}</div>
      <WizardCTA>{cta}</WizardCTA>
    </div>
  );
}

export function Analysis({ onNext }) {
  const [phase, setPhase] = useState('loading');
  const [analysis, setAnalysis] = useState(null);
  const [catalogs, setCatalogs] = useState(null);

  const run = useCallback(() => {
    setPhase('loading');
    // 시그니처 통일 — analyzeProduct 는 projectId 를 받는다(http 모드 job 시작 대상). mock 은 무시.
    Promise.all([api.analyzeProduct(useAppStore.getState().projectId, {}), api.getCatalogs()])
      .then(([a, c]) => { setAnalysis(a); setCatalogs(c); setPhase('ready'); })
      .catch(() => setPhase('error'));
  }, []);
  useEffect(() => { run(); }, [run]);

  if (phase === 'loading') return <div className="wizard"><PageHead title="AI가 상품 정보를 분석했어요" sub="틀린 부분이 있으면 직접 수정해주세요." /><AnalysisSkeleton /></div>;
  if (phase === 'error') return <div className="wizard narrow"><div className="surface"><ErrorState desc="분석 서버에 일시적인 문제가 발생했어요." onRetry={run} /></div></div>;

  return <AnalysisForm analysis={analysis} catalogs={catalogs}
    onChange={(patch) => {
      const refreshMatch = isMatchRecommendationPatch(patch);
      setAnalysis((a) => ({ ...a, ...patch }));
      api.saveAnalysis(null, patch).then((saved) => {
        if (refreshMatch) setAnalysis((a) => ({ ...a, matchClothing: saved.matchClothing }));
      });
    }} onNext={onNext} />;
}
