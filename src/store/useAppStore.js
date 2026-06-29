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
import { clearDraft } from '@/lib/draftStore.js';

const initialFlow = {
  projectId: null,
  // 서버 project(보관함 행) 생성 완료 여부. '상세페이지 제작' 진입만으로 빈 프로젝트가
  // 생기지 않도록, createProject 는 입력 진입이 아니라 AI 분석 시작(ensureProject) 때 1회 호출한다.
  projectPersisted: false,
  selectedMannequinId: null,
  composeMode: 'basic',
  copywriting: true,
  adjustCount: 0,
};

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
  // 명시적 '새 제작' 횟수 — ProductInput 을 이 값으로 key 해서, 같은 /create/input 라우트에서
  // 새 제작해도 컴포넌트를 remount(폼·복원상태 초기화)한다. loadProject·retry 의 projectId
  // 변경에는 바뀌지 않아 일반 흐름엔 영향 없음.
  projectGeneration: 0,

  /** 새 제작 진입 — 서버 project 생성은 보류한다(AI 분석 시 ensureProject 가 생성).
     '상세페이지 제작'/'새 상세페이지' 클릭만으로 보관함에 빈 프로젝트가 생기던 버그 방지.
     여기선 로컬 플로우만 초기화: 미동기화 draft 폐기(묵은 입력 복원 방지) + projectGeneration
     을 올려 ProductInput 을 remount(폼 초기화)한다. */
  async beginProject() {
    await clearDraft().catch(() => {});
    // http: 서버 POST 이연(빈 보관함 행 방지) — projectId 없이 시작, 생성은 ensureProject.
    // mock: createProject 가 reseedDraft 로 DB.product/analysis 를 깨끗한 시드로 되돌린다.
    // 안 하면 이전 세션 변형(clothingType/measurements 등)이 새 제작 입력에 유입된다(코드리뷰 반영).
    const mode = import.meta.env.VITE_API_MODE ?? 'mock';
    if (mode === 'http') {
      set({ ...initialFlow, projectGeneration: get().projectGeneration + 1 });
    } else {
      const project = await api.createProject();
      set({ ...initialFlow, projectId: project.id, projectPersisted: true, projectGeneration: get().projectGeneration + 1 });
    }
  },
  /** 서버 project(보관함 행)를 필요 시 1회 생성하고 projectId 를 반환 — AI 분석 시작 시 호출.
     이미 이 플로우에서 생성했으면(persisted) 재사용해 보관함 행 중복 생성을 막는다. */
  async ensureProject() {
    if (get().projectPersisted && get().projectId) return get().projectId;
    const project = await api.createProject();
    set({ projectId: project.id, projectPersisted: true });
    return project.id;
  },
  /** 새로고침 등으로 스토어가 비었을 때 서버의 project 에서 선택값 복원. */
  async loadProject() {
    if (get().projectId) return get().projectId;
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
  setProjectId(projectId) { set({ projectId }); },

  selectMannequin(id) {
    set({ selectedMannequinId: id });
    api.patchProject(get().projectId, { selectedMannequinId: id });
  },
  setComposeMode(composeMode) {
    set({ composeMode });
    api.patchProject(get().projectId, { composeMode });
  },
  setCopywriting(copywriting) {
    set({ copywriting });
    api.patchProject(get().projectId, { copywriting });
  },
  /** 서버 응답(조정/재생성 결과) 반영용 — 화면이 임의 계산해 넣지 않는다. */
  setAdjustCount(adjustCount) { set({ adjustCount }); },
}));

export default useAppStore;
