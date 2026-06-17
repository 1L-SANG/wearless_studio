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

  /** 새 제작 시작 — 새 project 를 만들고 플로우 선택값을 초기화 (구 resetFlow). */
  async startProject() {
    const project = await api.createProject();
    // 명시적 새 제작 — 미동기화 입력 draft·복원 마커를 폐기해 묵은 입력이 복원되지 않게 한다.
    await clearDraft().catch(() => {});
    sessionStorage.removeItem('wl_recoverDraft');
    set({ ...initialFlow, projectId: project.id, projectGeneration: get().projectGeneration + 1 });
    return project;
  },
  /** 새로고침 등으로 스토어가 비었을 때 서버의 project 에서 선택값 복원. */
  async loadProject() {
    if (get().projectId) return get().projectId;
    const p = await api.getProject();
    set({
      projectId: p.id,
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
