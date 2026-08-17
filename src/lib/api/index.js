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
import { withoutDemoPhotos } from './guestProductTemplate.js';

const mode = import.meta.env.VITE_API_MODE ?? 'mock';
export const isMockMode = mode !== 'http';

// 서버 대응이 없는 순수 클라 함수 — http 모드에서도 mock 로 유지한다.
// getCatalogs: 정적 UI 옵션 데이터. download: 클라 헬퍼.
// (draftWashCare 는 서버 wash-care:draft, regenerateMannequin 은 서버 mannequins:regenerate 로 실배선됨 → httpAdapter 담당.)
// getCustomMatchDraft/clearCustomMatchDraft: draft 단계 내 옷 blob 접근자·소거자 —
// 확정 승격(draftSync)이 실서버 등록에 쓰고, 끝나면 반드시 비운다(탭 내 다음 프로젝트 오염 방지).
// resetInputDraft: 게스트 구간 로컬 저장소(mock 싱글톤) 비우기 — 새 제작이 http 모드에서도
// 불러야 한다(직전 제작의 분석이 다음 분석 응답에 섞이는 것을 막는다).
const CLIENT_ONLY = ['getCatalogs', 'download', 'getCustomMatchDraft', 'clearCustomMatchDraft',
  'resetInputDraft'];

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
    if (name === 'getProduct') {
      // 게스트 구간의 상품 템플릿에서 데모 사진을 잘라낸다. mock DB 의 시드 사진은 SVG
      // 플레이스홀더라, 실제 흐름이 그걸 셀러 사진으로 들고 가면 임시저장·복원을 거쳐
      // 확정 업로드에서 서버 400(지원하지 않는 이미지 형식)으로 막힌다(2026-08-17 사고).
      api[name] = async (projectId, ...args) => (
        projectId == null
          ? withoutDemoPhotos(await mockAdapter.getProduct(projectId, ...args))
          : httpAdapter.getProduct(projectId, ...args)
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
