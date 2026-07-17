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

const mode = import.meta.env.VITE_API_MODE ?? 'mock';

// http 모드에서만 flow(projectId·resumePath·선택값)를 localStorage 에 영속한다.
// 목적: 상세페이지 제작 중 다른 페이지로 이탈했다 돌아오거나 cold reload 해도 진행 중 프로젝트를
// '이어서' 재개할 수 있게 한다(과거 http loadProject 가 null 을 반환해 재개 자체가 불가였음).
// mock 은 자체 시드 복원(api.getProject)이 있어 영속하지 않는다(모드 간 stale id 교차 오염 방지).
const FLOW_KEY = 'wl_flow';
function loadPersistedFlow() {
  if (mode !== 'http') return {};
  try {
    const raw = localStorage.getItem(FLOW_KEY);
    return raw ? JSON.parse(raw) : {};
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
    }));
  } catch { /* localStorage 불가(사생활 모드 등) — 영속 생략, 세션 내 동작은 유지 */ }
}

const initialFlow = {
  projectId: null,
  // 서버 project(보관함 행) 생성 완료 여부. '상세페이지 제작' 진입만으로 빈 프로젝트가
  // 생기지 않도록, createProject 는 입력 진입이 아니라 AI 분석 시작(ensureProject) 때 1회 호출한다.
  projectPersisted: false,
  selectedMannequinId: null,
  composeMode: 'basic',
  copywriting: true,
  adjustCount: 0,
  // 진행 중 상세페이지 제작에서 마지막으로 머문 create/editor 경로 — '이어서 작업' 재개 목표.
  resumePath: null,
};

// 분석 확정 후 이미 생성된 마네킹을 다시 만들지 판단하는 탭 세션 전용 신호.
// flow 영속 대상에 넣지 않는다: 새 브라우저 세션의 복원은 명시적 이어서일 뿐, 과거 탭의
// 미확정 편집 의도까지 재생성 트리거로 복원하면 안 된다.
const generationRelevantAnalysisKeys = new Set([
  'matchClothing',
  'clothingType',
  'subCategory',
  'customCategory',
  'targetGenders',
  'fit',
  'fitProfile',
]);

export function isGenerationRelevantAnalysisPatch(patch) {
  return !!patch && Object.keys(patch).some((key) => generationRelevantAnalysisKeys.has(key));
}

const initialMannequinJob = () => ({
  status: 'idle', // idle | running | error
  projectId: null,
  progress: 0,
  errorMessage: '',
});

// ensureProject 동시 호출 합류용 — in-flight Promise 를 모듈 스코프에 보관해 더블클릭/재시도가
// createProject 를 중복 호출(보관함 행 중복 생성)하지 않게 한다(코드리뷰 반영).
let ensureProjectInflight = null;

// http 모드에서 loadPersistedFlow() 가 복원한 projectId 를 세션당 1회만 서버 유효성 확인한다.
// StrictMode·여러 화면 가드의 동시 호출은 같은 Promise 에 합류시켜 검증 중인 id를 정상으로 오인하지 않는다.
let flowValidated = false;
let flowValidationInflight = null;

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
  ...loadPersistedFlow(),   // http: 이탈/새로고침 전 진행 프로젝트 복원 → '이어서' 재개
  mannequinJob: initialMannequinJob(),
  // 명시적 '새 제작' 횟수 — ProductInput 을 이 값으로 key 해서, 같은 /create/input 라우트에서
  // 새 제작해도 컴포넌트를 remount(폼·복원상태 초기화)한다. loadProject·retry 의 projectId
  // 변경에는 바뀌지 않아 일반 흐름엔 영향 없음.
  projectGeneration: 0,
  generationRelevantEditsDirty: false,

  /** 새 제작 진입 — 서버 project 생성은 보류한다(AI 분석 시 ensureProject 가 생성).
     '상세페이지 제작'/'새 상세페이지' 클릭만으로 보관함에 빈 프로젝트가 생기던 버그 방지.
     여기선 로컬 플로우만 초기화: 미동기화 draft 폐기(묵은 입력 복원 방지) + projectGeneration
     을 올려 ProductInput 을 remount(폼 초기화)한다. */
  async beginProject() {
    ensureProjectInflight = null;   // 새 제작 시작 — 이전 플로우의 in-flight 생성과 분리
    resetAnalysisCache();           // 이전 프로젝트의 analysis/매칭 캐시 해제 (F1)
    await clearDraft().catch(() => {});
    // http: 서버 POST 이연(빈 보관함 행 방지) — projectId 없이 시작, 생성은 ensureProject.
    // mock: createProject 가 reseedDraft 로 DB.product/analysis 를 깨끗한 시드로 되돌린다.
    // 안 하면 이전 세션 변형(clothingType/measurements 등)이 새 제작 입력에 유입된다(코드리뷰 반영).
    if (mode === 'http') {
      set({
        ...initialFlow,
        mannequinJob: initialMannequinJob(),
        projectGeneration: get().projectGeneration + 1,
        generationRelevantEditsDirty: false,
      });
    } else {
      const project = await api.createProject();
      set({
        ...initialFlow,
        mannequinJob: initialMannequinJob(),
        projectId: project.id,
        projectPersisted: true,
        projectGeneration: get().projectGeneration + 1,
        generationRelevantEditsDirty: false,
      });
    }
    persistFlow(get());   // 새 제작 시작 — 영속 flow 초기화(stale projectId 미복원)
  },
  /** 서버 project(보관함 행)를 필요 시 1회 생성하고 projectId 를 반환 — AI 분석 시작 시 호출.
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
        set({ projectId: project.id, projectPersisted: true });
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
        set({ ...initialFlow, generationRelevantEditsDirty: false });
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
      composeMode: p.composeMode,
      copywriting: p.copywriting,
      adjustCount: p.adjustCount,
    });
    return p.id;
  },
  /** 백엔드 sync(비로그인 draft) 결과의 projectId 반영 — 로그인 복귀 후 RootRedirect 가 호출. */
  setProjectId(projectId) { set({ projectId }); persistFlow(get()); },
  /** 로그인 복귀 draft sync 등에서 서버 project 를 현재 진행 프로젝트로 채택(영속 포함). */
  adoptProject(projectId) {
    set((s) => (s.projectId === projectId
      ? { projectPersisted: true }
      : {
        ...initialFlow,
        projectId,
        projectPersisted: true,
        mannequinJob: initialMannequinJob(),
        generationRelevantEditsDirty: false,
      }));
    persistFlow(get());
  },
  /** 상세페이지 제작 플로우에서 현재 머문 경로 기록 — '이어서 작업' 재개 목표(ResumeTracker 가 호출). */
  setResumePath(resumePath) {
    if (get().resumePath === resumePath) return;
    set({ resumePath });
    persistFlow(get());
  },

  markGenerationRelevantEdits() { set({ generationRelevantEditsDirty: true }); },
  clearGenerationRelevantEdits() { set({ generationRelevantEditsDirty: false }); },

  selectMannequin(id) {
    set({ selectedMannequinId: id });
    persistFlow(get());
    api.patchProject(get().projectId, { selectedMannequinId: id });
  },
  setComposeMode(composeMode) {
    set({ composeMode });
    persistFlow(get());
    api.patchProject(get().projectId, { composeMode });
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
}));

export default useAppStore;
