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

async function checkedJson(res, fallback = '요청을 처리하지 못했어요. 잠시 후 다시 시도해 주세요.') {
  if (res.ok) return res.status === 204 ? null : res.json();
  let message = fallback;
  let code;
  let reasons;
  try {
    const payload = await res.json();
    message = payload?.error?.message || message;
    code = payload?.error?.code;
    reasons = payload?.error?.reasons;
  } catch { /* 비 JSON 응답 — 일반화된 카피 유지 */ }
  const error = new Error(message);
  error.status = res.status;
  if (code) error.code = code;
  if (Array.isArray(reasons)) error.reasons = reasons;
  throw error;
}

// POST /v1/facemarket/identity/verify → { verified, modelId, status, nameMasked }.
// 실패 시 http() 가 서버 에러봉투의 한국어 message 를 throw(409 재사용·400 CI누락 등).
export function verifyIdentity(token) {
  return http('/v1/facemarket/identity/verify', { method: 'POST', body: { token } });
}

// POST /v1/facemarket/enrollments/{id}/identity — 등록 스코프 CI 게이트. CX 표준인증창
// 성공 token 만 전달(원문 신원은 서버가 CX trans 에서 직접 받는다). 성공 시 photos_pending 전이.
export function createIdentity(enrollmentId, { token }) {
  return http(`/v1/facemarket/enrollments/${encodeURIComponent(enrollmentId)}/identity`, {
    method: 'POST', body: { token },
  });
}

// GET /v1/facemarket/models — 검증 모델 카탈로그(셀러용). [FM-13 팀원 계약]
// → [{ id, displayName, status, coverImageUrl, createdAt }] (PII·ci_hash 없음).
export function listModels() {
  return http('/v1/facemarket/models');
}

// GET /v1/facemarket/models/me — 로그인 사용자 본인 소유 모델(마이페이지). 동일 shape.
// 카드에 assetsReady(그리드 자산 빌드 완료 → 셀러 선택 가능) 포함.
export function listMyModels() {
  return http('/v1/facemarket/models/me');
}

export function createEnrollment({ documentVersion, deviceId }) {
  return http('/v1/facemarket/enrollments', {
    method: 'POST',
    body: {
      biometricConsent: { accepted: true, documentVersion },
      deviceId,
    },
  });
}

export function getCurrentEnrollment() {
  return http('/v1/facemarket/enrollments/current');
}

export function getEnrollment(id, { signal } = {}) {
  return http(`/v1/facemarket/enrollments/${encodeURIComponent(id)}`, { signal });
}

export async function uploadEnrollmentPhoto({ enrollmentId, angle, fileBlob, filename }) {
  const form = new FormData();
  form.append('angle', angle);
  form.append('photo', fileBlob, filename || 'face');
  return checkedJson(await _authFetch(
    `/v1/facemarket/enrollments/${encodeURIComponent(enrollmentId)}/photos`,
    { method: 'POST', body: form },
  ), '얼굴 사진 업로드에 실패했어요. 잠시 후 다시 시도해 주세요.');
}

export async function deleteEnrollmentPhoto(enrollmentId, angle) {
  return checkedJson(await _authFetch(
    `/v1/facemarket/enrollments/${encodeURIComponent(enrollmentId)}/photos/${encodeURIComponent(angle)}`,
    { method: 'DELETE' },
  ), '얼굴 사진 삭제에 실패했어요.');
}

// image: 대표(커버) 이미지 — 셀러 카탈로그 카드에 노출. 응답에 저장된 key 는 실리지 않으므로
// 201 자체를 성공 신호로 신뢰한다(별도로 읽어오지 않는다).
export async function uploadProfileImage({ enrollmentId, fileBlob, filename }) {
  const form = new FormData();
  form.append('image', fileBlob, filename || 'cover');
  return checkedJson(await _authFetch(
    `/v1/facemarket/enrollments/${encodeURIComponent(enrollmentId)}/profile-image`,
    { method: 'POST', body: form },
  ), '대표 이미지 업로드에 실패했어요. 잠시 후 다시 시도해 주세요.');
}

export function createLivenessSession(enrollmentId, nonce) {
  return http(`/v1/facemarket/enrollments/${encodeURIComponent(enrollmentId)}/liveness-session`, {
    method: 'POST', body: { nonce },
  });
}

// idPhotoHex: OACX RESULT-step 신분증 사진(data.dlphotoimage) — 위젯 콜백에서 받은 HEX
// 그대로 전달(재인코딩 금지). 서버가 hex-decode+SFace 1:1 매치에 쓰고, 매칭 후 폐기한다.
// token 은 더 이상 여기서 전달하지 않는다 — CI 게이트는 identity 단계(createIdentity)에서 끝난다.
export function completeEnrollment(enrollmentId, { sessionId, idPhotoHex }) {
  return http(`/v1/facemarket/enrollments/${encodeURIComponent(enrollmentId)}/complete`, {
    method: 'POST', body: { sessionId, idPhotoHex },
  });
}

export function cancelEnrollment(enrollmentId) {
  return http(`/v1/facemarket/enrollments/${encodeURIComponent(enrollmentId)}/cancel`, { method: 'POST' });
}

export function createLicense({
  enrollmentId, allowedUse = [], forbiddenUse = [], unitPrice = 10000, validDays = 365,
}) {
  return http('/v1/facemarket/licenses', {
    method: 'POST',
    body: { enrollmentId, allowedUse, forbiddenUse, unitPrice, validDays },
  });
}

// GET /v1/facemarket/licenses — 내 라이선스 목록. [{ id, faceImageUri, allowedUse, ... }].
export function listLicenses() {
  return http('/v1/facemarket/licenses');
}

// POST /v1/facemarket/licenses/{id}/revoke (소유자 스코프) — 라이선스를 해지한다.
// 갱신된 LicenseCard(status:'revoked') 반환. 해지 즉시 얼굴 게이트와 생성 verify 게이트가
// 이 모델을 차단한다(재생성 시 409 license_revoked). 멱등 — 이미 해지된 라이선스도 안전.
export function revokeLicense(id) {
  return http(`/v1/facemarket/licenses/${id}/revoke`, { method: 'POST' });
}

// GET /v1/facemarket/jobs/{jobId}/settlement — 생성 잡의 온체인 정산 영수증(payment_id=job:{jobId}).
// → { paymentId, txHash, chainId, totalAmount, modelAmount, platformAmount, opsAmount, vcId, chainStatus }
// (70/20/10 = 모델/플랫폼/운영). 정산 미기록(비 FaceMarket 잡·체인 지연 등)이면 404 → http() 가 throw.
export function getJobSettlement(jobId) {
  return http(`/v1/facemarket/jobs/${jobId}/settlement`);
}

// GET /v1/facemarket/verify/{id} — QR 공개 검증. **무인증**(심사위원·구매자가 스캔).
// http() 는 세션이 없으면 요청 전에 throw 하므로(httpAdapter) 여기선 쓸 수 없다 — 생 fetch.
// 응답은 서버 화이트리스트(PublicVerifyResult) 그대로:
//   { valid, status, allowedUse, forbiddenUse, unitPrice, validUntil, vcId, model:{ nameMasked, age } }
// 얼굴·digest·CI·생년월일·user_id·model_id 는 서버가 애초에 싣지 않는다(무인증 = 노출 시 영구 유출).
// 해지가 즉시 반영돼야 하므로 캐시 금지(서버 Cache-Control: no-store + 요청 측 cache:'no-store').
export async function verifyLicensePublic(licenseId) {
  const res = await fetch(`${BASE_URL}/v1/facemarket/verify/${encodeURIComponent(licenseId)}`, {
    headers: { Accept: 'application/json' },
    cache: 'no-store',
  });
  if (!res.ok) {
    let message = res.status === 404
      ? '찾을 수 없는 라이선스예요.'
      : '라이선스를 확인하지 못했어요. 잠시 후 다시 시도해 주세요.';
    try { const p = await res.json(); if (p?.error?.message) message = p.error.message; } catch { /* 비 JSON */ }
    const err = new Error(message);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

// 게이트 얼굴 이미지 → objectURL. <img> 는 Bearer 를 못 보내므로 fetch+blob 로 인증해 받는다.
// 호출부는 표시 후 URL.revokeObjectURL 로 해제할 것.
export async function fetchLicenseFaceUrl(faceImageUri) {
  const res = await _authFetch(faceImageUri);
  if (!res.ok) throw new Error('얼굴 이미지를 불러오지 못했어요.');
  return URL.createObjectURL(await res.blob());
}
