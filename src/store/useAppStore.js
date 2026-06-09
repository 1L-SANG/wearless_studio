/* =============================================================
   store/useAppStore.js — global UI / flow state (Zustand).
   Holds only what must survive across routes: account (+credits),
   catalogs, and the create-flow selections (product, analysis,
   mannequin choice, compose mode, storyboard, copywriting).
   Screen-local state (form drafts, hover, loading phase, expanded
   panels) stays in React local state per the agreed convention.
   ============================================================= */
import { create } from 'zustand';
import { api } from '@/lib/api/index.js';

const initialFlow = {
  product: null,
  analysis: null,
  mannequins: [],
  selectedMannequinId: null,
  composeMode: 'basic',
  adjustCount: 0,
  storyboard: [],
  copywriting: true,
};

export const useAppStore = create((set, get) => ({
  /* ---- account / catalogs (loaded once) ---- */
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

  /* ---- credits ---- */
  spendCredits(n = 0) {
    set((s) => (s.account ? { account: { ...s.account, credits: Math.max(0, s.account.credits - n) } } : {}));
  },

  /* ---- create flow ---- */
  ...initialFlow,

  setProduct: (product) => set({ product }),
  patchProduct: (patch) => set((s) => ({ product: { ...(s.product || {}), ...patch } })),

  setAnalysis: (analysis) => set({ analysis }),
  patchAnalysis: (patch) => set((s) => ({ analysis: { ...(s.analysis || {}), ...patch } })),

  setMannequins: (mannequins) => set({
    mannequins,
    selectedMannequinId: (mannequins.find((m) => m.selected) || mannequins[0])?.id ?? null,
  }),
  selectMannequin: (id) => set((s) => ({
    selectedMannequinId: id,
    mannequins: s.mannequins.map((m) => ({ ...m, selected: m.id === id })),
  })),
  incAdjust: () => set((s) => ({ adjustCount: s.adjustCount + 1 })),
  setComposeMode: (composeMode) => set({ composeMode }),

  setStoryboard: (storyboard) => set({ storyboard }),
  setCopywriting: (copywriting) => set({ copywriting }),

  /* ---- start a brand-new creation ---- */
  // clear client flow state AND re-seed the mock draft so prior-session
  // mannequin variants / saved storyboard / edited analysis don't leak.
  resetFlow: () => { api.resetDraft(); set({ ...initialFlow }); },
}));

export default useAppStore;
