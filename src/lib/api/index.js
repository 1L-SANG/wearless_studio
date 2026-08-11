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
import { analyzePublicDraft } from './publicAnalysis.js';

const mode = import.meta.env.VITE_API_MODE ?? 'mock';
export const isMockMode = mode !== 'http';

// 서버 대응이 없는 순수 클라 함수 — http 모드에서도 mock 로 유지한다.
// getCatalogs: 정적 UI 옵션 데이터. pickAnyImage/download: 클라 헬퍼.
// (draftWashCare 는 서버 wash-care:draft, regenerateMannequin 은 서버 mannequins:regenerate 로 실배선됨 → httpAdapter 담당.)
const CLIENT_ONLY = ['getCatalogs', 'pickAnyImage', 'download'];

// 제품 결정: 입력·분석은 로그인 없이 공개하고, 로그인은 마네킹 단계부터 요구한다.
// 공개 흐름은 서버 projectId가 없으므로 로컬 draft 기능은 mock에 위임한다. 단 분석만은
// 로컬 상품 사진을 multipart로 공개 서버에 보내 진짜 AI 결과를 받는다.
const PUBLIC_INPUT = [
  'getProduct',
  'uploadProductPhotos',
  'saveProduct',
  'analyzeProduct',
  'getAnalysis',
  'saveAnalysis',
  'uploadPhoto',
  'addCustomMatchItem',
  'removeCustomMatchItem',
  'refreshMatchClothing',
];

function buildHttpApi() {
  const api = { ...httpAdapter };
  for (const name of PUBLIC_INPUT) {
    if (name === 'analyzeProduct') {
      api[name] = async (projectId, options) => (
        projectId == null
          ? analyzePublicDraft(options?.product || await mockAdapter.getProduct(projectId), options, {
            remote: httpAdapter, local: mockAdapter,
          })
          : httpAdapter.analyzeProduct(projectId, options)
      );
      continue;
    }
    api[name] = (projectId, ...args) => (
      projectId == null
        ? mockAdapter[name](projectId, ...args)
        : httpAdapter[name](projectId, ...args)
    );
  }
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
