/* =============================================================
   lib/api — the API boundary screens import from.
   mockAdapter/httpAdapter 병렬 구조 (plan §8) — VITE_API_MODE 로 선택.
   - mock (기본): 전부 mock
   - http: httpAdapter 에 구현된 함수만 실서버, 나머지는 mock 폴백
     (함수 단위 부분 스왑 — Phase 7 mock 제거 전까지 유지)
   TanStack Query 는 Phase 1 에서 도입 (03 §3, frontend_state_model §8-7).
   ============================================================= */
import { mockAdapter } from './mockAdapter.js';
import { httpAdapter } from './httpAdapter.js';

const mode = import.meta.env.VITE_API_MODE ?? 'mock';

export const api = mode === 'http' ? { ...mockAdapter, ...httpAdapter } : mockAdapter;
export default api;
