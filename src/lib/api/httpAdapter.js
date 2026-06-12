/* =============================================================
   httpAdapter — FastAPI 실서버 구현 (plan §8, Phase 1+에서 함수
   단위로 채운다). 여기 구현된 함수만 http 모드에서 mock 을 대체하고,
   나머지는 mock 이 계속 담당한다 (부분 스왑).
   시그니처·반환 형태는 mock/api.js(계약 §6)와 동일해야 한다.
   ============================================================= */
const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '';

// 공용 fetch 헬퍼 — Phase 1에서 Authorization: Bearer 주입 (plan §9)
export async function http(path, { method = 'GET', body } = {}) {
  const res = await fetch(`${BASE_URL}${path}`, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`API ${res.status} ${path}`);
  return res.json();
}

export const httpAdapter = {
  // 전환 예시 (Phase 1): async getAccount() { return http('/me'); },
};
