/* =============================================================
   httpAdapter — FastAPI 실서버 구현 (plan §8, Phase 1+에서 함수
   단위로 채운다). 여기 구현된 함수만 http 모드에서 mock 을 대체하고,
   나머지는 mock 이 계속 담당한다 (부분 스왑).
   시그니처·반환 형태는 mock/api.js(계약 §6)와 동일해야 한다.
   ============================================================= */
import { supabase } from '@/lib/supabase.js';

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '';

// 공용 fetch 헬퍼 — Supabase 세션의 access_token 을 Bearer 로 주입 (plan §9).
// 에러 봉투 { error: { code, message } } 의 한국어 message 를 그대로 throw (계약 §6).
export async function http(path, { method = 'GET', body } = {}) {
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;

  const res = await fetch(`${BASE_URL}${path}`, {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (!res.ok) {
    // 계약 §6: 사용자에게 그대로 보여줄 한국어 message. envelope 없으면 한국어 기본값.
    let message = '요청을 처리하지 못했어요. 잠시 후 다시 시도해 주세요.';
    try {
      const payload = await res.json();
      if (payload?.error?.message) message = payload.error.message;
    } catch { /* 비 JSON 응답 — 기본 메시지 유지 */ }
    console.error(`API ${res.status} ${path}`); // 기술 세부는 콘솔로만
    throw new Error(message);
  }
  return res.json();
}

export const httpAdapter = {
  // Phase 1-B 읽기·CRUD 스왑 (계약 §6 시그니처 동일). 미구현 함수는 mock 폴백.
  // getProject 는 store 가 projectId 없이 호출(api.getProject()) → 시그니처 정리 후
  // 플로우 단계에서 스왑. 지금 스왑하면 깨지므로 mock 유지.
  async getAccount() {
    return http('/v1/me/account');
  },
  async getLibrary() {
    // mock 의 { forceEmpty, forceError } 옵션은 실서버에선 무의미 — 무시.
    return http('/v1/projects?view=library');
  },
  async createProject() {
    return http('/v1/projects', { method: 'POST' });
  },
  async patchProject(projectId, patch) {
    return http(`/v1/projects/${projectId}`, { method: 'PATCH', body: patch });
  },
  // 크레딧 표시 페이지 (계약 §6) — 조회 전용. 구매·환불 UI는 PG 단계.
  async getPricingPlans() {
    return http('/v1/pricing-plans');
  },
  async getCreditHistory() {
    return http('/v1/credits/history');
  },
  async getCreditSources() {
    return http('/v1/credits/sources');
  },
};
