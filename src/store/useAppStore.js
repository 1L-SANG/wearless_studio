/* =============================================================
   store/useAppStore.js — global CLIENT state only (Zustand).
   상태 3계층 (documents/frontend_state_model.md §1, ADR-0002):
   ① 서버 상태(product/analysis/mannequins/storyboard/editorBlocks…)
      는 여기 두지 않는다 — 화면이 lib/api 로 직접 읽고 쓴다.
   ② 라우트를 넘어 살아야 하는 것만 이 스토어에 둔다: account/catalogs
      전역 캐시(Query 도입 전까지), projectId, 플로우 선택값.
   ③ 화면 안에서만 쓰는 상태(폼 draft, hover, 패널, 에디터 히스토리)는
      각 컴포넌트의 React 로컬 상태.
   플로우 선택값은 project 필드의 작업 사본 — 변경 시 patchProject 로
   서버에 동기화한다 (계약 §2).
   ============================================================= */
import { create } from 'zustand';
import { api } from '@/lib/api/index.js';
import { resetAnalysisCache } from '@/lib/api/httpAdapter.js';
import { clearDraft } from '@/lib/draftStore.js';
import { clearFlowSession, markProductInfoConfirmed, readFlowSession } from '@/lib/flowSession.js';
import { clearDetailPageJobMarker, loadDetailPageJobMarker, saveDetailPageJobMarker } from '@/lib/detailPageJobPersistence.js';
import {
  adoptGenerationRelevantEdits,
  clearGenerationRelevantEdits as clearGenerationRelevantEditsSession,
  markGenerationRelevantEdits as markGenerationRelevantEditsSession,
  readGenerationRelevantEdits,
} from '@/features/mannequin/generationRelevantEditsSession.js';

const mode = import.meta.env.VITE_API_MODE ?? 'mock';
const normalizeComposeMode = (value) => value === 'extended' ? 'extended' : 'basic';

// http 모드에서만 flow(projectId·resumePath·선택값)를 localStorage 에 영속한다.
// 목적: 상세페이지 제작 중 다른 페이지로 이탈했다 돌아오거나 cold reload 해도 진행 중 프로젝트를
// '이어서' 재개할 수 있게 한다(과거 http loadProject 가 null 을 반환해 재개 자체가 불가였음).
// mock 은 localStorage 대신 같은 탭의 flowSession project 표식만 복원한다
// (모드 간 stale id 교차 오염 방지 + F5 분석 합류 데모 유지).
const FLOW_KEY = 'wl_flow';
function loadPersistedFlow() {
  if (mode !== 'http') {
    const saved = readFlowSession();
    if (!saved?.projectId) return {};
    return {
      projectId: saved.projectId,
      projectPersisted: true,
      resumePath: saved.path && saved.path !== '/create/input' ? saved.path : null,
      productInfoConfirmed: saved.productInfoConfirmed === true,
    };
  }
  try {
    const raw = localStorage.getItem(FLOW_KEY);
    if (!raw) return {};
    const saved = JSON.parse(raw);
    if ('composeMode' in saved) saved.composeMode = normalizeComposeMode(saved.composeMode);
    return saved;
  } catch { return {}; }
}
function persistFlow(s) {
  if (mode !== 'http') return;
  try {
    localStorage.setItem(FLOW_KEY, JSON.stringify({
      projectId: s.projectId,
      projectPersisted: s.projectPersisted,
      resumePath: s.resumePath,
      selectedMannequinId: s.selectedMannequinId,
      composeMode: s.composeMode,
      copywriting: s.copywriting,
      adjustCount: s.adjustCount,
      productInfoConfirmed: s.productInfoConfirmed,
    }));
  } catch { /* localStorage 불가(사생활 모드 등) — 영속 생략, 세션 내 동작은 유지 */ }
}

const initialFlow = {
  projectId: null,
  // 서버 project(보관함 행) 생성 완료 여부. 확정 전 분석은 공개 경로라 project 없이 진행하고,
  // 의류정보 확정 승격이 끝난 뒤에만 true가 된다.
  projectPersisted: false,
  selectedMannequinId: null,
  composeMode: 'basic',
  copywriting: true,
  adjustCount: 0,
  // 진행 중 상세페이지 제작에서 마지막으로 머문 create/editor 경로 — '이어서 작업' 재개 목표.
  resumePath: null,
  productInfoConfirmed: false,
};

// 재생성 트리거 판정(순수 로직)은 src/lib/generationRelevance.js 로 분리 — node --test 로
// 직접 단위 테스트하기 위해 이 스토어(Vite 전용 @/ 임포트·import.meta.env 의존)에서 뺐다.
export { isGenerationRelevantAnalysisPatch } from '@/lib/generationRelevance.js';

const initialMannequinJob = () => ({
  status: 'idle', // idle | running | error
  projectId: null,
  progress: 0,
  errorMessage: '',
});

/* 상세페이지 생성 잡 — 에디터 대기 화면의 단일 소스(editor_wait_dev_spec §3).
   mannequinJob 과 달리 폴링 수명도 store 가 소유한다: 화면을 떠나도 추적이 살아있어야
   전역 리본·"창 닫아도 계속"이 말이 아니라 사실이 된다. */
const initialDetailPageJob = () => ({
  status: 'idle',   // idle | running | done | blocked | error
  jobId: null,
  projectId: null,
  progress: 0,
  phase: null,      // inputs_loaded | copy | cut | assemble
  cutsDone: 0,
  cutsTotal: 0,
  cuts: {},         // sourceBlockId → { url, width, height } (previewUrl=1h presigned)
  live: [],         // 지금 생성 중인 sourceBlockId[] (cut_start~종결 사이)
  failedCuts: [],   // 실패 컷 sourceBlockId[] (빈 슬롯·미차감)
  copy: {},         // sourceBlockId → texts[{role,text}] (AG-03 통과본)
  errorMessage: '',
  startedAt: 0,
});

function restoredDetailPageJob(projectId) {
  if (mode !== 'http') return initialDetailPageJob();
  const saved = loadDetailPageJobMarker();
  if (!saved || saved.projectId !== projectId) return initialDetailPageJob();
  return { ...initialDetailPageJob(), ...saved, status: 'running' };
}

// 재시작·새 제작 시 이전 폴링 루프를 무효화하는 세대 토큰(모듈 스코프).
let detailJobSeq = 0;
let detailJobLoopProjectId = null;

/* 완료 브라우저 알림 — 폴링과 같은 곳(store)에서 발화해야 셀러가 다른 화면·다른 탭에
   가 있어도 울린다(대기 화면 컴포넌트 수명에 묶으면 이탈 시 무음 — codex 리뷰 F7).
   권한 요청은 대기 화면의 [완료되면 알림 받기] 버튼이 담당, 여기는 발화만. */
function notifyDetailPageDone() {
  try {
    if (typeof Notification !== 'undefined' && Notification.permission === 'granted' && document.hidden) {
      new Notification('상세페이지가 완성됐어요', { body: '돌아와서 순서와 카피를 다듬어 보세요.' });
    }
  } catch { /* 알림 실패는 생성 결과에 영향 없음 */ }
}

/* 이벤트 → 잡 상태 반영 (순수 함수). 이벤트는 append-only 원장이라 재수신(after=0 재생)에도
   같은 결과로 수렴한다 — 새로고침 복원의 근거. */
function applyDetailJobEvents(job, events) {
  const next = { ...job, cuts: { ...job.cuts }, copy: { ...job.copy } };
  const live = new Set(next.live);
  const failed = new Set(next.failedCuts);
  for (const e of events) {
    const p = e?.payload || {};
    if (e?.type === 'progress') {
      next.progress = Math.max(next.progress, p.progress || 0);
      if (p.phase) next.phase = p.phase;
      if (p.phase === 'cut') {
        next.cutsDone = p.done ?? next.cutsDone;
        next.cutsTotal = p.total ?? next.cutsTotal;
      }
    } else if (e?.type === 'step' && p.blockId) {
      if (p.status === 'cut_start') live.add(p.blockId);
      if (p.status === 'cut_done') {
        live.delete(p.blockId);
        next.cuts[p.blockId] = { url: p.previewUrl, width: p.width, height: p.height };
      }
      if (p.status === 'cut_passthrough') {
        live.delete(p.blockId);
        // 셀러 원본 재사용 — asset 행이 이미 있어 안정 /file 경로가 즉시 유효
        next.cuts[p.blockId] = { url: p.assetId ? `/v1/assets/${p.assetId}/file` : null };
      }
      if (p.status === 'cut_failed') { live.delete(p.blockId); failed.add(p.blockId); }
      if (p.status === 'copy_ready') next.copy[p.blockId] = p.texts || [];
    }
  }
  next.live = [...live];
  next.failedCuts = [...failed];
  return next;
}

// 레거시/후속 화면의 ensureProject 동시 호출 합류용 — 확정 승격은 draftSyncSingleFlight가 맡는다.
// createProject 를 중복 호출(보관함 행 중복 생성)하지 않게 한다(코드리뷰 반영).
let ensureProjectInflight = null;

// http 모드에서 loadPersistedFlow() 가 복원한 projectId 를 세션당 1회만 서버 유효성 확인한다.
// StrictMode·여러 화면 가드의 동시 호출은 같은 Promise 에 합류시켜 검증 중인 id를 정상으로 오인하지 않는다.
let flowValidated = false;
let flowValidationInflight = null;
// 사진 양 선택 PATCH를 직렬화한다. 사용자가 빠르게 바꾸고 다음으로 가도
// 오래 걸린 이전 요청이 나중에 도착해 최신 선택을 덮지 않게 한다.
let composeModePatchChain = Promise.resolve();

const persistedFlow = loadPersistedFlow();

export const useAppStore = create((set, get) => ({
  /* ---- account / catalogs (서버 상태의 전역 캐시 — loaded once) ---- */
  account: null,
  catalogs: null,
  accountLoaded: false,
  catalogsLoaded: false,

  async loadAccount() {
    if (get().accountLoaded) return get().account;
    const account = await api.getAccount();
    set({ account, accountLoaded: true });
    return account;
  },
  async loadCatalogs() {
    if (get().catalogsLoaded) return get().catalogs;
    const catalogs = await api.getCatalogs();
    set({ catalogs, catalogsLoaded: true });
    return catalogs;
  },

  /* ---- credits ----
     단일 표시 소스 (frontend_state_model.md §6). 차감은 서버(mock api)
     책임 — 크레딧 봉투 응답의 잔액을 그대로 반영한다. 선차감 금지. */
  syncCredits(credits) {
    if (typeof credits !== 'number') return;
    set((s) => (s.account ? { account: { ...s.account, credits } } : {}));
  },

  /* ---- current project + flow selections ---- */
  ...initialFlow,
  ...persistedFlow,   // http: 이탈/새로고침 전 진행 프로젝트 복원 → '이어서' 재개
  mannequinJob: initialMannequinJob(),
  detailPageJob: restoredDetailPageJob(persistedFlow.projectId),
  // 명시적 '새 제작' 횟수 — ProductInput 을 이 값으로 key 해서, 같은 /create/input 라우트에서
  // 새 제작해도 컴포넌트를 remount(폼·복원상태 초기화)한다. loadProject·retry 의 projectId
  // 변경에는 바뀌지 않아 일반 흐름엔 영향 없음.
  projectGeneration: 0,
  generationRelevantEditsDirty: readGenerationRelevantEdits(persistedFlow.projectId),

  /** 새 제작 진입 — 서버 project 생성은 의류정보 확정까지 보류한다.
     '상세페이지 제작'/'새 상세페이지' 클릭만으로 보관함에 빈 프로젝트가 생기던 버그 방지.
     여기선 로컬 플로우만 초기화: 미동기화 draft 폐기(묵은 입력 복원 방지) + projectGeneration
     을 올려 ProductInput 을 remount(폼 초기화)한다. */
  async beginProject() {
    ensureProjectInflight = null;   // 새 제작 시작 — 이전 플로우의 in-flight 생성과 분리
    detailJobSeq += 1;              // 이전 프로젝트의 상세페이지 폴링 루프 무효화(codex F5 —
                                    // stale 루프가 새 플로우의 detailPageJob 을 덮지 않게)
    detailJobLoopProjectId = null;
    clearDetailPageJobMarker();
    resetAnalysisCache();           // 이전 프로젝트의 analysis/매칭 캐시 해제 (F1)
    clearFlowSession();
    await clearDraft().catch(() => {});
    // mock도 실제 흐름처럼 project 없이 시작하되, 이전 데모 입력 데이터만 깨끗하게 재시드한다.
    if (mode !== 'http') await api.resetInputDraft();
    set({
      ...initialFlow,
      mannequinJob: initialMannequinJob(),
      detailPageJob: initialDetailPageJob(),
      projectGeneration: get().projectGeneration + 1,
      generationRelevantEditsDirty: false,
    });
    persistFlow(get());   // 새 제작 시작 — 영속 flow 초기화(stale projectId 미복원)
  },
  /** 서버 project(보관함 행)를 필요 시 1회 생성하고 projectId 를 반환 — 레거시 후속 경로용.
     이미 이 플로우에서 생성했으면(persisted) 재사용해 보관함 행 중복 생성을 막는다. */
  async ensureProject() {
    if (get().projectPersisted && get().projectId) return get().projectId;
    // 동시 호출(버튼 더블클릭·중복 submit·네트워크 지연 중 재시도)을 한 번의 createProject 로
    // 합류시킨다. projectPersisted 가 true 로 세팅되기 전 두 번째 호출이 들어와도 같은 promise 를
    // 공유하므로 서버 행이 중복 생성되지 않는다(성공·실패 모두 finally 에서 in-flight 해제).
    if (ensureProjectInflight) return ensureProjectInflight;
    ensureProjectInflight = (async () => {
      try {
        const project = await api.createProject();
        const preserveDirty = get().projectId === null && get().generationRelevantEditsDirty;
        const generationRelevantEditsDirty = adoptGenerationRelevantEdits(project.id, { preserveDirty });
        set({ projectId: project.id, projectPersisted: true, generationRelevantEditsDirty });
        persistFlow(get());   // 서버 project 생성 — 재개 대상으로 영속
        return project.id;
      } finally {
        ensureProjectInflight = null;
      }
    })();
    return ensureProjectInflight;
  },
  /** 스토어가 비었을 때 projectId·선택값 복원 시도. 복원 불가면 null 반환(화면이 입력으로 리다이렉트).
     http: 서버엔 '현재 프로젝트' 개념이 없다(projectId 원천은 스토어뿐, 플로우 라우트에 URL 파라미터 없음).
       콜드 새로고침/직접 URL 진입이면 복원할 게 없으므로 null — getProject 를 argless 로 호출하지 않는다
       (과거 mock getProject 가 가짜 단일 project 를 스토어에 심어 upload-url 이 404 나던 poison 근원).
     mock: 단일 시드 프로젝트를 복원해 dev 새로고침 흐름을 유지. */
  async loadProject() {
    const pid = get().projectId;
    if (pid) {
      // http: loadPersistedFlow() 가 복원한 projectId 를 세션당 1회 서버 유효성 확인한다. 확정 404만
      // 초기화하고, 인증·네트워크·서버 일시 장애에는 진행 정보를 보존한다.
      if (mode !== 'http' || flowValidated) return pid;
      if (flowValidationInflight?.projectId === pid) return flowValidationInflight.promise;
      let validationPromise;
      validationPromise = (async () => {
        try {
          const p = await api.getProject(pid);
          if (get().projectId !== pid) return get().projectId;
          if (p && p.id) { flowValidated = true; return pid; }
        } catch (error) {
          if (get().projectId !== pid) return get().projectId;
          if (error?.status !== 404) return pid;
        }
        flowValidated = true;
        clearGenerationRelevantEditsSession(pid);
        set({ ...initialFlow, generationRelevantEditsDirty: false });
        clearDetailPageJobMarker();
        persistFlow(get());
        return null;
      })().finally(() => {
        if (flowValidationInflight?.promise === validationPromise) flowValidationInflight = null;
      });
      flowValidationInflight = { projectId: pid, promise: validationPromise };
      return validationPromise;
    }
    flowValidated = true;   // 복원할 id 없음 — 이후 재검증 불필요(새 id 는 생성 시점에 신뢰)
    if (mode !== 'mock') return null;
    const p = await api.getProject();
    set({
      projectId: p.id,
      projectPersisted: true,   // 기존 project 복원 — 이미 보관함에 존재
      selectedMannequinId: p.selectedMannequinId,
      composeMode: normalizeComposeMode(p.composeMode),
      copywriting: p.copywriting,
      adjustCount: p.adjustCount,
      generationRelevantEditsDirty: readGenerationRelevantEdits(p.id),
    });
    return p.id;
  },
  /** 백엔드 sync(비로그인 draft) 결과의 projectId 반영 — 로그인 복귀 후 RootRedirect 가 호출. */
  setProjectId(projectId) {
    const sameProject = get().projectId === projectId;
    const generationRelevantEditsDirty = get().projectId === projectId
      ? get().generationRelevantEditsDirty
      : readGenerationRelevantEdits(projectId);
    set({
      projectId,
      generationRelevantEditsDirty,
      productInfoConfirmed: sameProject ? get().productInfoConfirmed : false,
    });
    persistFlow(get());
  },
  /** 로그인 복귀 draft sync 등에서 서버 project 를 현재 진행 프로젝트로 채택(영속 포함).
     preserveGenerationDirty: 서버 project 가 아직 없던(projectId===null) 지금까지의 작업이
     막 서버 신원을 얻을 뿐인 경로에서만 true 로 넘긴다 — 게스트가 분석을 편집(플래그 true)한 뒤
     세션이 생겨 draft sync 로 처음 project 를 갖는 경우가 그렇다. 그건 '다른 작업으로 전환'이
     아니라 같은 작업의 연속이라 재생성 신호를 지우면 안 된다. 보관함에서 다른 project 를 열거나
     /editor/:id 로 직접 들어오는 경로는 실제로 '다른 작업'을 여는 것이므로 이 옵션을 넘기지
     않는다. 기본값(false)은 대상 project의 sessionStorage 신호만 읽어, 이전 project의 신호가
     새지 않게 한다. 이미 다른 project 로 작업 중이면 true 를 넘겨도 보존하지 않는다. */
  adoptProject(projectId, { preserveGenerationDirty = false } = {}) {
    // 다른 프로젝트 채택 = 프로젝트 경계 전환 — 이전 상세페이지 폴링 루프를 무효화해
    // stale 루프가 초기화된 슬라이스를 나중에 덮지 않게 한다(codex F5).
    if (get().projectId !== projectId) {
      detailJobSeq += 1;
      detailJobLoopProjectId = null;
      clearDetailPageJobMarker();
    }
    const current = get();
    if (current.projectId === projectId) {
      set({ projectPersisted: true });
      persistFlow(get());
      return;
    }
    const sameWorkContinuation = preserveGenerationDirty && current.projectId === null;
    const preserveDirty = sameWorkContinuation && current.generationRelevantEditsDirty;
    const generationRelevantEditsDirty = adoptGenerationRelevantEdits(projectId, { preserveDirty });
    set({
      ...initialFlow,
      projectId,
      projectPersisted: true,
      mannequinJob: initialMannequinJob(),
      detailPageJob: initialDetailPageJob(),
      generationRelevantEditsDirty,
      // 같은 작업이 서버 신원을 얻는 경로면 비로그인 때 고른 사진 양도 이어간다 —
      // initialFlow 스프레드가 basic 으로 되돌리면 게스트의 선택이 로그인 순간 조용히 사라진다.
      composeMode: sameWorkContinuation ? current.composeMode : initialFlow.composeMode,
    });
    persistFlow(get());
  },
  /** 상세페이지 제작 플로우에서 현재 머문 경로 기록 — '이어서 작업' 재개 목표(ResumeTracker 가 호출). */
  setResumePath(resumePath) {
    if (get().resumePath === resumePath) return;
    set({ resumePath });
    persistFlow(get());
  },

  confirmProductInfo(projectId = get().projectId) {
    if (!projectId || projectId !== get().projectId) return false;
    markProductInfoConfirmed(projectId);
    set({ productInfoConfirmed: true, resumePath: '/create/storyboard' });
    persistFlow(get());
    return true;
  },

  markGenerationRelevantEdits() {
    set({ generationRelevantEditsDirty: markGenerationRelevantEditsSession(get().projectId) });
  },
  clearGenerationRelevantEdits(projectId = get().projectId, expectedRevision) {
    const cleared = clearGenerationRelevantEditsSession(projectId, expectedRevision);
    if (cleared && get().projectId === projectId) set({ generationRelevantEditsDirty: false });
    return cleared;
  },

  selectMannequin(id) {
    set({ selectedMannequinId: id });
    persistFlow(get());
    api.patchProject(get().projectId, { selectedMannequinId: id });
  },
  setComposeMode(composeMode) {
    composeMode = normalizeComposeMode(composeMode);
    set({ composeMode });
    persistFlow(get());
    const projectId = get().projectId;
    // 비로그인 분석(projectId 없음)에서도 사진 양 칩이 눌린다(분석 페이지는 공개) — 이때
    // PATCH 를 쏘면 /v1/projects/null 로 나가 에러가 된다. 로컬 선택만 저장하고, 서버 반영은
    // 로그인 후 adoptProject 가 같은 작업을 채택할 때 이어서 한다.
    if (!projectId || !get().productInfoConfirmed) return composeModePatchChain;
    composeModePatchChain = composeModePatchChain
      .catch(() => {})
      .then(() => api.patchProject(projectId, { composeMode }));
    return composeModePatchChain;
  },
  restoreComposeMode(composeMode) {
    set({ composeMode: normalizeComposeMode(composeMode) });
    persistFlow(get());
  },
  setCopywriting(copywriting) {
    set({ copywriting });
    persistFlow(get());
    api.patchProject(get().projectId, { copywriting });
  },
  /** 서버 응답(조정/재생성 결과) 반영용 — 화면이 임의 계산해 넣지 않는다. */
  setAdjustCount(adjustCount) { set({ adjustCount }); persistFlow(get()); },
  setMannequinJob(patch) {
    set((s) => ({ mannequinJob: { ...s.mannequinJob, ...patch } }));
  },

  /* ---- 상세페이지 생성 잡 (editor_wait_dev_spec §3) ----
     시작·폴링·이벤트 소비를 store 가 소유한다. 대기 화면(Generating)은 구독만 하고,
     셀러가 다른 화면으로 떠나도 루프는 계속 돌아 전역 리본이 진행을 보여준다. */
  async startDetailPageGeneration(projectId) {
    const cur = get().detailPageJob;
    // 같은 프로젝트 생성이 이미 돌고 있으면 합류(마운트 중복·StrictMode 이중 실행 가드)
    if (cur.status === 'running' && cur.projectId === projectId
        && detailJobLoopProjectId === projectId) return;
    const seq = ++detailJobSeq;
    const alive = () => detailJobSeq === seq;
    const patch = (p) => {
      if (!alive()) return;
      set((s) => ({ detailPageJob: { ...s.detailPageJob, ...p } }));
      if (get().detailPageJob.status === 'running') saveDetailPageJobMarker(get().detailPageJob);
    };
    const recovering = cur.projectId === projectId && Boolean(cur.jobId)
      && (cur.status === 'running' || cur.status === 'error');
    const running = recovering
      ? { ...cur, status: 'running', errorMessage: '' }
      : { ...initialDetailPageJob(), status: 'running', projectId, startedAt: Date.now() };
    detailJobLoopProjectId = projectId;
    set({ detailPageJob: running });
    saveDetailPageJobMarker(running);
    try {
      // 새로고침 복원은 저장된 jobId를 직접 다시 폴링한다. jobId가 브라우저에 기록되기 전
      // 새로고침된 아주 짧은 구간만 POST를 재호출하며, 서버가 활성 잡에 멱등 합류시킨다.
      const res = running.jobId ? { jobId: running.jobId } : await api.startDetailPage(projectId);
      if (!alive()) return;
      if (res.legacy) {
        // mock — 이벤트 배관 없이 기존 완주 흐름(진행바만). editor_wait_dev_spec §1 비범위.
        const out = await api.generateDetailPage(projectId, {
          onProgress: (p) => patch({ progress: p }),
        });
        if (!alive()) return;
        get().syncCredits(out.credits);
        patch({ status: 'done', jobId: out.jobId || null, progress: 100 });
        clearDetailPageJobMarker();
        notifyDetailPageDone();
        return;
      }
      if (res.data) {   // 완료 재호출(멱등) — 새 잡 없음
        get().syncCredits(res.credits);
        patch({ status: 'done', progress: 100 });
        clearDetailPageJobMarker();
        return;   // 재호출은 즉시 완료 — 새 생성이 아니므로 알림 없음
      }
      patch({ jobId: res.jobId });
      let after = 0;
      // 15분 — 정상 실측 242~285초 + 서버 lease 복구(900초) 동안 화면이 먼저 포기하지 않게
      // (httpAdapter.generateDetailPage 의 timeoutMs 와 같은 근거).
      const deadline = (running.startedAt || Date.now()) + 900000;
      for (;;) {
        if (!alive()) return;
        const [job, ev] = await Promise.all([
          api.getJob(res.jobId),
          // 이벤트는 보조 신호 — 일시 실패해도 잡 폴링은 계속(다음 턴에 after 재시도)
          api.getJobEvents(res.jobId, after).catch(() => ({ events: [] })),
        ]);
        if (!alive()) return;
        const events = ev?.events || [];
        if (events.length) {
          after = events[events.length - 1].id;
          set((s) => ({ detailPageJob: applyDetailJobEvents(s.detailPageJob, events) }));
        }
        if (typeof job.progress === 'number') {
          patch({ progress: Math.max(get().detailPageJob.progress, job.progress) });
        }
        if (job.status === 'done') {
          get().syncCredits(job.result?.credits);
          patch({ status: 'done', progress: 100 });
          clearDetailPageJobMarker();
          notifyDetailPageDone();
          return;
        }
        if (job.status === 'error') {
          patch({ status: 'error', errorMessage: job.errorMessage || '상세페이지 생성에 실패했어요.' });
          return;
        }
        if (Date.now() > deadline) {
          // 타임아웃은 실패가 아니다 — 화면이 기다리기를 그만둔 것(서버 잡은 계속 돈다)
          patch({ status: 'error', errorMessage: '생성이 예상보다 오래 걸리고 있어요. 잠시 후 다시 확인해 주세요.' });
          return;
        }
        await new Promise((r) => setTimeout(r, 1200));
      }
    } catch (e) {
      if (!alive()) return;
      // 장면⑤ — 얼굴 라이선스 차단(409): 블로킹 패널로 명확히 멈춘다(재생성 재차단 신호)
      if (e?.status === 409) {
        patch({ status: 'blocked', errorMessage: e.message || '이 모델의 얼굴 라이선스를 사용할 수 없어요.' });
        return;
      }
      patch({ status: 'error', errorMessage: e?.message || '상세페이지 생성에 실패했어요.' });
    } finally {
      if (alive()) detailJobLoopProjectId = null;
    }
  },
  /** 재시도·이탈 정리용 — 진행 중 루프를 무효화하고 초기 상태로. */
  resetDetailPageJob() {
    detailJobSeq += 1;
    detailJobLoopProjectId = null;
    clearDetailPageJobMarker();
    set({ detailPageJob: initialDetailPageJob() });
  },
}));

export default useAppStore;
