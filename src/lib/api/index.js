/* =============================================================
   lib/api — the API boundary screens import from.
   Today it re-exports the mock layer. When the real backend lands,
   swap this single module to call fetch()/Supabase while keeping
   the same function signatures + shapes (handoff/contracts/api.js).
   TanStack Query is intentionally NOT introduced yet — it arrives
   with the backend/Supabase integration step.
   ============================================================= */
export { api, default } from '@/mock/api.js';
