/* =============================================================
   features/library — 보관함 (PRD §4)
   Ported verbatim from reference/prototype/features/library.jsx.
   Only change: ES imports; onNew/onOpen wired to router + store.
   ============================================================= */
import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api/index.js';
import { useAppStore } from '@/store/useAppStore.js';
import { Button, Skeleton, EmptyState, ErrorState, useToast } from '@/components/ui.jsx';

import { isMobileDevice } from '@/lib/deviceClass.js';
import { useAuth } from '@/features/auth/AuthProvider.jsx';
import { authorizeFlowContinuation } from '@/lib/flowSession.js';

const STAGES = { storyboard: '콘티', mannequin: '마네킹', editor: '에디터' };
const stageOf = (step) => ['generating', 'failed'].includes(step) ? 'editor' : step;
const resumePath = (project) => project.status === 'done'
  || ['pending', 'running', 'error', 'cancelled', 'done'].includes(project.latestJobs?.detailPage?.status)
  ? `/editor/${project.id}` : project.selectedMannequinId ? '/create/mannequin' : '/create/storyboard';

export function Library() {
  const navigate = useNavigate();
  const { push } = useToast();
  const { session } = useAuth();
  const openingRef = useRef(false);
  const entryToken = useRef(0);
  const userRef = useRef(session?.user?.id || null);
  userRef.current = session?.user?.id || null;
  useEffect(() => () => { entryToken.current += 1; openingRef.current = false; }, []);
  const [openingId, setOpeningId] = useState(null);
  const adoptProject = useAppStore((s) => s.adoptProject);
  // 서버 project(보관함 행)는 AI 분석 시작 때 생성 — '새 상세페이지' 클릭만으로 빈 행 생성 방지.
  // (과거엔 진행 중 마네킹 job 이 있으면 강제로 그 화면으로 보냈다 — 콘티가 마네킹보다 먼저
  // 오는 지금은 생성이 도는 동안 사용자가 있어야 할 곳도 콘티라, '새로 만들기' 를 다른 작업의
  // 진행 중 job 이 가로챌 이유가 없다.)
  const onNew = () => { entryToken.current += 1; navigate('/create/input'); };
  const onOpen = (it) => {
    if (isMobileDevice()) {
      push('완성본은 PC에서 열어 주세요');
      return;
    }
    entryToken.current += 1;
    adoptProject(it.id);   // 보관함 선택은 해당 project 를 명시적으로 이어서 여는 경로다.
    navigate(`/editor/${it.id}`);
  };
  const onResume = async (item) => {
    if (isMobileDevice()) { push('PC에서 열어 주세요'); return; }
    if (openingRef.current) return;
    const token = ++entryToken.current;
    const userId = userRef.current;
    const { projectGeneration, userId: flowUserId } = useAppStore.getState();
    const isCurrent = () => token === entryToken.current && userId === userRef.current
      && projectGeneration === useAppStore.getState().projectGeneration
      && flowUserId === useAppStore.getState().userId;
    openingRef.current = true;
    setOpeningId(item.id);
    try {
      const project = await api.getProject(item.id);
      if (!isCurrent()) return;
      if (project?.id !== item.id) throw new Error('만들던 상품을 찾지 못했어요.');
      const path = resumePath(project);
      adoptProject(item.id, { serverSnapshot: project });
      const store = useAppStore.getState();
      store.confirmProductInfo(item.id);
      authorizeFlowContinuation(item.id, path);
      store.markFlowUsed(userId);
      store.setResumePath(path);
      navigate(path);
    } catch (error) {
      if (isCurrent()) push(error?.message || '만들던 상품을 열지 못했어요.');
    } finally {
      if (token === entryToken.current) {
        openingRef.current = false;
        setOpeningId(null);
      }
    }
  };

  // 서버 상태는 TanStack Query 캐시로 (frontend_state_model §8-7).
  // staleTime 0 → 보관함 진입(마운트)마다 refetch. 생성/편집/완료가 다른 화면에서
  // 일어나므로 전역 staleTime(60s) 캐시를 쓰면 생성분 빠진 stale 목록이 보인다.
  // 캐시는 즉시 렌더하고 백그라운드로 갱신 → 항상 최신 + 깜빡임 없음.
  const { data: items = [], isLoading, isError, refetch } = useQuery({
    queryKey: ['library'],
    queryFn: () => api.getLibrary({}),
    staleTime: 0,
  });
  const phase = isLoading ? 'loading' : isError ? 'error' : 'ready';
  const { data: inProgress = [], isLoading: progressLoading, isError: progressError, refetch: refetchProgress } = useQuery({
    queryKey: ['library', 'in_progress'],
    queryFn: () => api.getInProgressLibrary(),
    staleTime: 0,
  });

  const [selectedTab, setSelectedTab] = useState(null);
  const [selectedStage, setSelectedStage] = useState('all');
  const hasProgress = inProgress.length > 0;
  const activeTab = hasProgress ? selectedTab || 'progress' : 'done';
  const stageCounts = Object.fromEntries(Object.keys(STAGES).map((stage) =>
    [stage, inProgress.filter((item) => stageOf(item.step) === stage).length]));
  const activeStage = stageCounts[selectedStage] ? selectedStage : 'all';
  const visibleProgress = inProgress.filter((item) => activeStage === 'all' || stageOf(item.step) === activeStage);
  const onTabKeyDown = (event) => {
    const index = activeTab === 'progress' ? 0 : 1;
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? 1
      : ['ArrowLeft', 'ArrowRight'].includes(event.key) ? 1 - index : null;
    if (next === null) return;
    event.preventDefault();
    setSelectedTab(next === 0 ? 'progress' : 'done');
    event.currentTarget.parentElement.querySelectorAll('[role="tab"]')[next].focus();
  };

  const statusPill = (s) => s === 'done' ? <span className="pill pill-soft st"><span className="dot dot-done" />완료</span>
    : s === 'generating' ? <span className="pill pill-soft st"><span className="dot dot-busy" />생성 중</span>
    : <span className="pill pill-soft st">초안</span>;

  // 표시 파생 — 저장값은 ISO updatedAt (계약 ProjectSummary, §7-12 해소)
  const timeAgo = (it) => {
    if (it.status === 'generating') return '진행 중';
    const m = Math.max(1, Math.round((Date.now() - new Date(it.updatedAt).getTime()) / 60000));
    if (m < 60) return `${m}분 전`;
    const h = Math.round(m / 60);
    if (h < 24) return `${h}시간 전`;
    const d = Math.round(h / 24);
    return d === 1 ? '어제' : `${d}일 전`;
  };

  return (
    <div className="wizard wide library">
      <div className="lib-heading">
        <div><h1>보관함</h1>
          <p className="sec-sub">지금까지 만든 상세페이지를 모아봤어요.</p></div>
      </div>

      {progressError && <div className="sec-head"><p className="sec-sub">만드는 중인 상품을 불러오지 못했어요.</p>
        <Button variant="ghost" size="sm" onClick={refetchProgress}>다시 불러오기</Button></div>}
      {hasProgress && (
        <div className="lib-tabs" role="tablist" aria-label="보관함">
          {[['progress', '진행 중', inProgress.length], ['done', '완료', items.length]].map(([tab, label, count]) => (
            <button type="button" key={tab} id={`library-tab-${tab}`} role="tab"
              className="lib-tab" aria-selected={activeTab === tab} aria-controls={`library-panel-${tab}`}
              tabIndex={activeTab === tab ? 0 : -1} onClick={() => setSelectedTab(tab)} onKeyDown={onTabKeyDown}>
              {label} {count}
            </button>
          ))}
        </div>
      )}
      {hasProgress && activeTab === 'progress' && (
        <section className="field-row" id="library-panel-progress" role="tabpanel" aria-labelledby="library-tab-progress">
          <div className="lib-filters" aria-label="제작 단계">
            {['all', ...Object.keys(STAGES).filter((stage) => stageCounts[stage] > 0)].map((stage) => (
              <button type="button" key={stage} className={`lib-filter stage-${stage}`}
                aria-pressed={activeStage === stage} onClick={() => setSelectedStage(stage)}>
                {stage !== 'all' && <span className="lib-stage-dot" aria-hidden="true" />}
                {stage === 'all' ? '전체' : STAGES[stage]} {stage === 'all' ? inProgress.length : stageCounts[stage]}
              </button>
            ))}
          </div>
          <div className="lib-grid">
            {visibleProgress.slice(0, 20).map((item) => (
              <div className="lib-card" key={item.id} role="button" tabIndex={0}
                aria-label={`${item.title || '새 상품'}, ${STAGES[stageOf(item.step)]} 단계`}
                aria-busy={openingId === item.id} aria-disabled={openingId !== null}
                onClick={() => onResume(item)} onKeyDown={(event) => {
                  if (event.key !== 'Enter' && event.key !== ' ') return;
                  event.preventDefault();
                  void onResume(item);
                }}>
                <div className="lib-cover"><img src={item.cover || undefined} alt={item.title || '새 상품'} />
                  <div className="st"><span className={`pill lib-stage stage-${stageOf(item.step)}`}>{STAGES[stageOf(item.step)]} 단계</span></div></div>
                <div className="lib-info"><div className="t handoff-title">{item.title || '새 상품'}</div>
                  <div className="m"><span>{timeAgo(item)}</span></div></div>
              </div>
            ))}
          </div>
        </section>
      )}

      {activeTab === 'done' && <section id="library-panel-done" role={hasProgress ? 'tabpanel' : undefined}
        aria-labelledby={hasProgress ? 'library-tab-done' : undefined}>
      {phase === 'loading' && (
        <div className="lib-grid">{Array.from({ length: 8 }).map((_, i) => <div key={i}><Skeleton h={240} r={12} /></div>)}</div>
      )}
      {phase === 'error' && <div className="surface"><ErrorState desc="보관함을 불러오지 못했어요." onRetry={refetch} /></div>}
      {phase === 'ready' && items.length === 0 && inProgress.length > 0 && (
        <p className="sec-sub">완성된 상세페이지가 여기에 모여요.</p>
      )}
      {phase === 'ready' && items.length === 0 && inProgress.length === 0 && !progressLoading && (
        <div className="surface"><EmptyState icon="library" title="아직 만든 상세페이지가 없어요"
          desc="상품 사진 몇 장이면 첫 상세페이지를 만들 수 있어요." action={<Button variant="primary" icon="plus" onClick={onNew}>첫 상세페이지 만들기</Button>} /></div>
      )}
      {phase === 'ready' && items.length > 0 && (
        <div className="lib-grid field-row">
          {items.map((it) => (
            <div className="lib-card" key={it.id} onClick={() => onOpen(it)}>
              <div className="lib-cover"><img src={it.cover || undefined} alt={it.title} /><div className="st">{statusPill(it.status)}</div></div>
              <div className="lib-info"><div className="t">{it.title}</div>
                <div className="m"><span>블록 {it.blockCount}</span><span>·</span><span>{timeAgo(it)}</span></div></div>
            </div>
          ))}
        </div>
      )}
      </section>}
    </div>
  );
}

export default Library;
