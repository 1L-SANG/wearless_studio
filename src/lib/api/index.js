/* =============================================================
   lib/api — the API boundary screens import from.
   mockAdapter/httpAdapter 병렬 구조 (plan §8) — VITE_API_MODE 로 선택.
   - mock (기본): 전부 mock.
   - http: httpAdapter 구현분은 실서버. 서버 엔드포인트가 없는 순수 클라 함수만
     화이트리스트(CLIENT_ONLY)로 mock 유지. 그 외 미구현 함수는 조용한 mock 폴백 대신
     호출 즉시 throw — 과거 poison(조용한 폴백이 가짜 데이터를 실서버 요청에 흘려 404) 재발 방지.
   ============================================================= */
import { mockAdapter } from './mockAdapter.js';
import { httpAdapter } from './httpAdapter.js';

const mode = import.meta.env.VITE_API_MODE ?? 'mock';

// 서버 대응이 없는 순수 클라 함수 — http 모드에서도 mock 로 유지한다.
// getCatalogs: 정적 UI 옵션 데이터. pickAnyImage/download: 클라 헬퍼.
// (draftWashCare 는 서버 wash-care:draft, regenerateMannequin 은 서버 mannequins:regenerate 로 실배선됨 → httpAdapter 담당.)
const CLIENT_ONLY = ['getCatalogs', 'pickAnyImage', 'download'];

function buildHttpApi() {
  const api = { ...httpAdapter };
  for (const name of CLIENT_ONLY) {
    if (mockAdapter[name]) api[name] = mockAdapter[name];
  }
  // 미구현 가드 — mock 에만 있고 http·화이트리스트에 없는 함수는 호출 즉시 throw(조용한 폴백 금지).
  for (const name of Object.keys(mockAdapter)) {
    if (name in api) continue;
    api[name] = () => {
      throw new Error(`[api] '${name}' 는 http 모드에서 아직 구현되지 않았어요.`);
    };
  }
  return api;
}

export const api = mode === 'http' ? buildHttpApi() : mockAdapter;
export default api;
