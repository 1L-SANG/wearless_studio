/* =============================================================
   lib/api/facemarket — FaceMarket 전용 API (셀러 스튜디오 api 경계와 분리).
   http-only(실서버 필수). http() 헬퍼를 재사용해 Supabase 세션 Bearer 를 주입한다.
   verifyIdentity: CX 표준인증창(ENT_MID) 성공 token만 백엔드로 — 원문 신원은
   서버가 CX trans 에서 직접 받는다(클라→서버 PII 신뢰 금지).
   ============================================================= */
import { http } from '@/lib/api/httpAdapter.js';
import { supabase } from '@/lib/supabase.js';

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '';

// http() 는 JSON 전용이라 멀티파트(얼굴 업로드)·바이너리(게이트 얼굴)는 직접 fetch 한다.
// Supabase Bearer 를 동일하게 주입하고, 에러봉투의 한국어 message 를 throw.
async function _bearer() {
  const { data } = await supabase.auth.getSession();
  return data.session?.access_token;
}

async function _authFetch(path, opts = {}) {
  const token = await _bearer();
  return fetch(`${BASE_URL}${path}`, {
    ...opts,
    headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(opts.headers || {}) },
  });
}

// POST /v1/facemarket/identity/verify → { verified, modelId, status, nameMasked }.
// 실패 시 http() 가 서버 에러봉투의 한국어 message 를 throw(409 재사용·400 CI누락 등).
export function verifyIdentity(token) {
  return http('/v1/facemarket/identity/verify', { method: 'POST', body: { token } });
}

// GET /v1/facemarket/models — 검증 모델 카탈로그(셀러용). [FM-13 팀원 계약]
// → [{ id, displayName, status, coverImageUrl, createdAt }] (PII·ci_hash 없음).
export function listModels() {
  return http('/v1/facemarket/models');
}

// GET /v1/facemarket/models/me — 로그인 사용자 본인 소유 모델(마이페이지). 동일 shape.
export function listMyModels() {
  return http('/v1/facemarket/models/me');
}

// POST /v1/facemarket/licenses (멀티파트) — 얼굴 + 라이선스 조건 → LicenseCard.
// 얼굴 바이트는 비공개 R2 로만 가고, 응답엔 게이트 URL(faceImageUri)만 실린다.
// Content-Type 은 브라우저가 multipart boundary 로 자동 설정(수동 지정 금지).
export async function createLicense({
  faceBlob, filename, allowedUse = [], forbiddenUse = [], unitPrice = 10000, validDays = 365,
}) {
  const fd = new FormData();
  fd.append('face', faceBlob, filename || 'face');
  allowedUse.forEach((v) => fd.append('allowed_use', v));
  forbiddenUse.forEach((v) => fd.append('forbidden_use', v));
  fd.append('unit_price', String(unitPrice));
  fd.append('valid_days', String(validDays));

  const res = await _authFetch('/v1/facemarket/licenses', { method: 'POST', body: fd });
  if (!res.ok) {
    let message = '라이선스 등록에 실패했어요. 잠시 후 다시 시도해 주세요.';
    try { const p = await res.json(); if (p?.error?.message) message = p.error.message; } catch { /* 비 JSON */ }
    throw new Error(message);
  }
  return res.json();
}

// GET /v1/facemarket/licenses — 내 라이선스 목록. [{ id, faceImageUri, allowedUse, ... }].
export function listLicenses() {
  return http('/v1/facemarket/licenses');
}

// 게이트 얼굴 이미지 → objectURL. <img> 는 Bearer 를 못 보내므로 fetch+blob 로 인증해 받는다.
// 호출부는 표시 후 URL.revokeObjectURL 로 해제할 것.
export async function fetchLicenseFaceUrl(faceImageUri) {
  const res = await _authFetch(faceImageUri);
  if (!res.ok) throw new Error('얼굴 이미지를 불러오지 못했어요.');
  return URL.createObjectURL(await res.blob());
}
