/* =============================================================
   lib/api/facemarket — FaceMarket 전용 API (셀러 스튜디오 api 경계와 분리).
   프로덕션은 실서버 전용이고 명시적인 개발 mock 모드만 로컬 자료를 쓴다. http() 헬퍼를 재사용해 Supabase 세션 Bearer 를 주입한다.
   verifyIdentity: CX 표준인증창(ENT_MID) 성공 token만 백엔드로 — 원문 신원은
   서버가 CX trans 에서 직접 받는다(클라→서버 PII 신뢰 금지).
   ============================================================= */
import { http } from '@/lib/api/httpAdapter.js';
import { supabase } from '@/lib/supabase.js';
// 상대 경로다(‘@/’ 아님): tests/frontend 의 몇몇 vite 하네스가 configFile:false 로 돌아
// '@' 별칭이 없다 — 그 하네스들은 httpAdapter·supabase 만 스텁으로 가로채므로, 여기서
// '@/' 를 쓰면 새 모듈 하나 때문에 통째로 깨진다(같은 파일의 facemarketPricing 선례).
import { DEVICE_HEADER, DEVICE_REJECTED_EVENT, readDeviceToken } from '../adminDevice.js';

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '';
const MOCK = import.meta.env.DEV && import.meta.env.VITE_API_MODE === 'mock';
const mockApi = async () => (await import('../../mock/facemarket.js')).getFacemarketMock();

// http() 는 JSON 전용이라 멀티파트(얼굴 업로드)·바이너리(게이트 얼굴)는 직접 fetch 한다.
// Supabase Bearer 를 동일하게 주입하고, 에러봉투의 한국어 message 를 throw.
async function _bearer() {
  const { data } = await supabase.auth.getSession();
  return data.session?.access_token;
}

async function _authFetch(path, opts = {}) {
  const token = await _bearer();
  // 관리자 기기 토큰 — http()(httpAdapter.js)와 같은 규칙으로 싣는다. 이게 빠지면
  // ADMIN_DEVICE_GATE=enforce 인 프로덕션에서 관리자 화면의 **모든 이미지 fetch 가 403** 이다
  // (admin_guard.device_token_from 은 이 헤더만 본다). 심사 화면은 신분증을 한 장도 못 보고,
  // 실패를 "파기됨"으로 오해하게 만든다(최종리뷰 C4). 스토리지가 오리진으로 갈라져 있어
  // 관리자 문서 밖에서는 토큰 자체가 없으므로 무조건 싣는다(IS_ADMIN 을 보지 않는다).
  const deviceToken = readDeviceToken();
  return fetch(`${BASE_URL}${path}`, {
    ...opts,
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(deviceToken ? { [DEVICE_HEADER]: deviceToken } : {}),
      ...(opts.headers || {}),
    },
  });
}

/* 게이트 라우트에서 바이트를 받아 objectURL 로 만든다(<img src> 로는 못 건다 — 인증
   헤더가 필요하다). 403(기기 게이트 거절)과 404(파기됨/없음)를 반드시 구분한다:
   403 을 "파기됨"으로 그리면 심사자가 존재하는 증거를 없다고 믿는다(최종리뷰 C4).
   기기 거절이면 http() 와 같은 이벤트를 쏴서 RequireDevice 가 복구 화면으로 넘긴다. */
async function _gatedImageUrl(path, fallbackMessage) {
  const res = await _authFetch(path);
  if (!res.ok) {
    let code;
    try {
      code = (await res.json())?.error?.code;
    } catch { /* 비 JSON 응답 */ }
    if (res.status === 403 && typeof code === 'string' && code.startsWith('device_')) {
      try {
        window.dispatchEvent(new CustomEvent(DEVICE_REJECTED_EVENT, { detail: { code } }));
      } catch { /* 비브라우저 환경 */ }
    }
    const error = new Error(fallbackMessage);
    error.status = res.status;
    if (code) error.code = code;
    throw error;
  }
  const blob = await res.blob();
  return URL.createObjectURL(blob);
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

// POST /v1/facemarket/face-render/warm — 얼굴 렌더 파드를 미리 켠다(워밍 핑).
// 셀러가 FaceMarket 모델을 고른 순간 부르면 첫 컷 앞에서 콜드스타트를 뺄 수 있다.
// **실패는 조용히 무시한다** — 생성 흐름에 영향이 0이어야 하는 부가 신호다.
export function warmFaceRender(modelId) {
  if (MOCK || !modelId) return Promise.resolve(null);
  return http('/v1/facemarket/face-render/warm', {
    method: 'POST',
    body: { modelId },
  }).catch(() => null);
}

// GET /v1/facemarket/face-render/status?modelId=… — 이 모델에 얼굴 패스가 걸리는지 + 파드 상태.
// → { ready, enabled, etaMinutes }. modelId 가 없으면 서버가 enabled=false 로 답한다
// (얼굴 패스는 그 모델에 켜진 LoRA 가 있어야 걸린다 — 모델을 모르면 판단할 수 없다).
export function getFaceRenderStatus(modelId, { signal } = {}) {
  if (MOCK) return Promise.resolve({ ready: false, enabled: false, etaMinutes: null });
  const query = modelId ? `?modelId=${encodeURIComponent(modelId)}` : '';
  return http(`/v1/facemarket/face-render/status${query}`, { signal }).catch(() => null);
}

// GET /v1/facemarket/models/me — 로그인 사용자 본인 소유 모델(마이페이지). 동일 shape.
// 카드에 assetsReady(그리드 자산 빌드 완료 → 셀러 선택 가능) 포함.
export function listMyModels() {
  if (MOCK) return Promise.resolve([]);
  return http('/v1/facemarket/models/me');
}

// 모델 리스트(/models) 열람 자격. 등록된 셀러(셀러 약관 동의)와 2차 등록(라이선스 발급)까지 마친 모델,
// 관리자만 allowed 다(2026-09-23 오너 결정, server/app/facemarket_catalog_access.py). 목 모드는 로그인 계정을 모델로 본다.
export function getCatalogAccess() {
  if (MOCK) return Promise.resolve({ allowed: true, role: 'model' });
  return http('/v1/facemarket/catalog-access');
}

export function createEnrollment({ documentVersion, deviceId, identityMethod }) {
  return http('/v1/facemarket/enrollments', {
    method: 'POST',
    body: {
      biometricConsent: { accepted: true, documentVersion },
      termsConsent: { accepted: true, documentVersion },
      deviceId,
      ...(identityMethod ? { identityMethod } : {}),
    },
  });
}

// POST /v1/facemarket/enrollments/{id}/id-document — 간편인증(simple_auth) 경로 전용.
// 사용자가 촬영한 신분증 전체본(마스킹 확인 완료) 업로드. 성공 시 photos_pending 전이
// (이 라우트에 오는 시점엔 본인확인이 이미 끝나 있다 — Task6 순서 뒤집기 이후).
// documentType: v1 은 rrc(주민등록증)만. 얼굴 업로드(uploadEnrollmentPhoto)와 같은 멀티파트 패턴.
export async function uploadIdDocument(enrollmentId, { file, documentType, maskedConfirmed, maskRegion }) {
  const form = new FormData();
  form.append('file', file, file?.name || 'id-document');
  form.append('documentType', documentType);
  form.append('maskedConfirmed', maskedConfirmed ? 'true' : 'false');
  if (maskRegion) form.append('maskRegion', JSON.stringify(maskRegion));
  return checkedJson(await _authFetch(
    `/v1/facemarket/enrollments/${encodeURIComponent(enrollmentId)}/id-document`,
    { method: 'POST', body: form },
  ), '신분증 업로드에 실패했어요. 잠시 후 다시 시도해 주세요.');
}

// 등록 위저드 런타임 설정(라이브니스 필요 여부 등) — 서버 authoritative.
export function getFacemarketConfig() {
  if (MOCK) return Promise.resolve({ livenessRequired: false });
  return http('/v1/facemarket/config');
}

export function getCurrentEnrollment() {
  if (MOCK) return Promise.resolve(null);
  return http('/v1/facemarket/enrollments/current');
}

export function getEnrollment(id, { signal } = {}) {
  return http(`/v1/facemarket/enrollments/${encodeURIComponent(id)}`, { signal });
}

export async function uploadEnrollmentPhoto({ enrollmentId, slot, angle, fileBlob, filename }) {
  const form = new FormData();
  form.append('slot', slot || angle);
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

// ── 모델 지원서(리뉴얼) ─────────────────────────────────────────────────────

// 제출 전 지원 사진 임시 저장 — kind: profile|closeup|waist_up|full_length (슬롯당 1장, 재업로드 시 교체).
export async function stageApplicationPhoto({ kind = 'profile', fileBlob, filename }) {
  if (MOCK) return (await mockApi()).stageApplicationPhoto({ kind, fileBlob, filename });
  const form = new FormData();
  form.append('kind', kind);
  form.append('image', fileBlob, filename || kind);
  return checkedJson(await _authFetch(
    '/v1/facemarket/applications/photo-staging',
    { method: 'POST', body: form },
  ), '사진 업로드에 실패했어요. 잠시 후 다시 시도해 주세요.');
}

export async function deleteStagedApplicationPhoto(kind = 'profile', stageId) {
  if (MOCK) return (await mockApi()).deleteStagedApplicationPhoto(kind, stageId);
  return checkedJson(await _authFetch(
    `/v1/facemarket/applications/photo-staging/${encodeURIComponent(kind)}/${encodeURIComponent(stageId)}`,
    { method: 'DELETE' },
  ), '임시 사진을 지우지 못했어요. 잠시 후 다시 시도해 주세요.');
}

// 지원서 제출. 성공 시 검토 중(auto-approve 면 승인) ApplicationView 반환. 중복이면 409.
export function submitApplication(body) {
  if (MOCK) return mockApi().then((api) => api.submitApplication(body));
  return http('/v1/facemarket/applications', { method: 'POST', body });
}

// 지원서 게이트 활성 여부(신규 진입을 /model/apply 로 보낼지) — 생체 /config 와 독립.
export function getApplicationConfig() {
  if (MOCK) return Promise.resolve({ applicationRequired: true });
  return http('/v1/facemarket/applications/config');
}

// 현재(활성 또는 최근 터미널) 지원서 — 상태 허브·재지원 프리필용. 없으면 404.
export function getCurrentApplication() {
  if (MOCK) return mockApi().then((api) => api.getCurrentApplication());
  return http('/v1/facemarket/applications/current');
}

export function cancelApplication(applicationId) {
  if (MOCK) return mockApi().then((api) => api.cancelApplication(applicationId));
  return http(`/v1/facemarket/applications/${encodeURIComponent(applicationId)}/cancel`, {
    method: 'POST',
  });
}

// ── 관리자: 지원서 검토 ─────────────────────────────────────────────────────
// 서버가 repo.is_admin 을 강제한다(호스트 라우팅은 UX 경계일 뿐, 비관리자는 403).

export function adminListApplications(status) {
  const qs = status ? `?status=${encodeURIComponent(status)}` : '';
  return http(`/v1/facemarket/admin/applications${qs}`);
}

export function adminApproveApplication(applicationId) {
  return http(`/v1/facemarket/admin/applications/${encodeURIComponent(applicationId)}/approve`, {
    method: 'POST',
  });
}

export function adminRejectApplication(applicationId, reason) {
  return http(`/v1/facemarket/admin/applications/${encodeURIComponent(applicationId)}/reject`, {
    method: 'POST', body: { reason },
  });
}

// 결정 메일 재발송(2A '메일 미발송' 복구).
export function adminResendEmail(applicationId) {
  return http(`/v1/facemarket/admin/applications/${encodeURIComponent(applicationId)}/resend-email`, {
    method: 'POST',
  });
}

// 관리자 프로필 사진: 게이트 라우트는 Authorization 헤더가 필요해 <img src> 로 못 건다.
// 바이트를 인증 fetch 로 받아 objectURL 을 만든다(호출자가 revokeObjectURL 로 해제).
export async function adminFetchApplicationPhotoUrl(applicationId, kind = 'profile') {
  return _gatedImageUrl(
    `/v1/facemarket/admin/applications/${encodeURIComponent(applicationId)}/profile-image?kind=${encodeURIComponent(kind)}`,
    '사진을 불러오지 못했어요.',
  );
}

// ── 관리자: 생체 등록 육안 심사(간편인증 review_pending) ────────────────────
// 전부 서버가 admin_guard.require_admin(기기 게이트 포함)을 강제한다.

// review 는 서버(`facemarket_admin_review.py` list_review_queue)에서 `Query(..., ...)` —
// 기본값 없는 필수 파라미터라 adminListApplications 의 옵션-쿼리 패턴(있으면만 붙임)을
// 그대로 베끼면 인자 없이 부르는 순간 422 가 난다. 항상 붙인다 — 호출부가 값을 빠뜨리면
// 여기서 조용히 숨기지 말고 그 즉시 드러나야 한다.
export function adminListEnrollments(review) {
  return http(`/v1/facemarket/admin/enrollments?review=${encodeURIComponent(review)}`);
}

export function adminEnrollmentCard(enrollmentId) {
  return http(`/v1/facemarket/admin/enrollments/${encodeURIComponent(enrollmentId)}`);
}

export function adminApproveEnrollment(enrollmentId) {
  return http(`/v1/facemarket/admin/enrollments/${encodeURIComponent(enrollmentId)}/approve`, {
    method: 'POST',
  });
}

export function adminRejectEnrollment(enrollmentId, reason) {
  return http(`/v1/facemarket/admin/enrollments/${encodeURIComponent(enrollmentId)}/reject`, {
    method: 'POST', body: { reason },
  });
}

// ── 관리자: 학습 전 사진 확인 ───────────────────────────────────────────────
// 심사 큐(review_status)와 다른 축이다 — 표준인증(mid) 등록은 사람 심사를 안 거치지만
// 사진 확인은 똑같이 받는다. status 는 'awaiting' | 'approved'.
export function adminListPhotoReview(status = 'awaiting') {
  return http(`/v1/facemarket/admin/enrollments/photo-review?status=${encodeURIComponent(status)}`);
}

export function adminApproveEnrollmentPhotos(enrollmentId) {
  return http(`/v1/facemarket/admin/enrollments/${encodeURIComponent(enrollmentId)}/photos/approve`, {
    method: 'POST',
  });
}

// slots: [{ slot, reason }] — 칸 이름은 서버가 화이트리스트(PHOTO_SLOTS)로 다시 검사한다.
export function adminRequestEnrollmentReshoot(enrollmentId, slots) {
  return http(`/v1/facemarket/admin/enrollments/${encodeURIComponent(enrollmentId)}/photos/reshoot`, {
    method: 'POST', body: { slots },
  });
}

// 신분증·등록 사진 스트림: 게이트 라우트라 <img src> 로 못 건다(adminFetchApplicationPhotoUrl
// 과 같은 이유). 경로는 호출부가 카드 응답의 `images` 맵에서 그대로 받아 넘긴다 — 프런트가
// `/v1/facemarket/admin/enrollments/{id}/images/{kind}` 를 다시 조립하면, 서버가 라우트
// 프리픽스를 바꿀 때 두 곳을 나란히 고쳐야 한다(fix round 1, minor: 서버가 이미 만들어
// 준 값을 프런트가 버리고 재조립하는 건 드리프트 위험). 응답이 no-store 라 이 objectURL 도
// 캐시가 아니다 — 카드가 닫히면 호출자가 revokeObjectURL 로 즉시 해제해야 한다(생체
// 이미지를 앱 상태에 오래 남기지 않는다).
export async function adminFetchGatedImageUrl(path) {
  return _gatedImageUrl(path, '이미지를 불러오지 못했어요.');
}

/* 지원서 사진 URI 를 카드 응답에서 그대로 받아 쓰는 경로(AdminSubmissionDetails).
   403(기기 게이트 거절)과 404(파기됨)를 반드시 구분해야 하므로 _gatedImageUrl 에 위임한다 —
   `if (!res.ok) throw` 로 뭉개면 심사자가 403 을 "파기됨"으로 읽는다(최종리뷰 C4). */
export async function adminApplicationProfileImage(imageUri) {
  return _gatedImageUrl(imageUri, '사진을 불러오지 못했어요.');
}

// ── 관리자 콘솔: 집계·모델·권한 ─────────────────────────────────────────────
// 전부 서버가 admin_guard.require_admin 을 강제한다(비관리자는 403).

export function adminOverview(days = 30) {
  return http(`/v1/facemarket/admin/overview?days=${encodeURIComponent(days)}`);
}

export function adminListModels({ q, status, limit = 50 } = {}) {
  const params = new URLSearchParams();
  if (q) params.set('q', q);
  if (status) params.set('status', status);
  params.set('limit', String(limit));
  return http(`/v1/facemarket/admin/models?${params.toString()}`);
}

export function adminModelDetail(modelId) {
  return http(`/v1/facemarket/admin/models/${encodeURIComponent(modelId)}`);
}

export function adminSuspendModel(modelId, reason) {
  return http(`/v1/facemarket/admin/models/${encodeURIComponent(modelId)}/suspend`, {
    method: 'POST', body: { reason },
  });
}

export function adminUnsuspendModel(modelId) {
  return http(`/v1/facemarket/admin/models/${encodeURIComponent(modelId)}/unsuspend`, {
    method: 'POST',
  });
}

export function adminListStaff(q) {
  const qs = q ? `?q=${encodeURIComponent(q)}` : '';
  return http(`/v1/facemarket/admin/staff${qs}`);
}

export function adminSetRole(userId, role) {
  return http(`/v1/facemarket/admin/staff/${encodeURIComponent(userId)}/role`, {
    method: 'POST', body: { role },
  });
}

/* 전체 가입자 목록 — 셀러/FaceMarket 출처 필터·이메일/이름 부분일치 검색.
   adminListStaff 와 헷갈리지 마라: 저쪽은 "이미 이메일을 아는 사람" 을 승격하는 도구라
   정확일치만 되고, 이쪽은 명부를 보는 화면이다(서버가 열람을 감사 원장에 남긴다).
   origin 은 seller|facemarket|both|unknown, 안 주면 전체. */
export function adminListUsers({ q, origin, limit = 50, cursor } = {}) {
  const params = new URLSearchParams({ limit: String(limit) });
  if (q) params.set('q', q);
  if (origin) params.set('origin', origin);
  if (cursor) params.set('cursor', cursor);
  return http(`/v1/facemarket/admin/users?${params.toString()}`);
}

export function adminGrantCredits(userId, body, idempotencyKey) {
  return http(`/v1/facemarket/admin/users/${encodeURIComponent(userId)}/credits/grants`, {
    method: 'POST', body,
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
  });
}

/* 계좌이체 신청 확인 — 지시서 docs/superpowers/plans/2026-09-22-bank-transfer-payments.md.
   status: requested(기본)|paid|rejected|canceled|expired|all. 확인은 신청 ID 로 멱등이라
   응답을 못 받았으면 같은 신청에 그대로 다시 눌러도 된다. */
export function adminListBankTransfers({ status = 'requested', limit = 100 } = {}) {
  const params = new URLSearchParams({ status, limit: String(limit) });
  return http(`/v1/facemarket/admin/bank-transfers?${params.toString()}`);
}

export function adminConfirmBankTransfer(requestId, { paidAt, adminNote }) {
  return http(`/v1/facemarket/admin/bank-transfers/${encodeURIComponent(requestId)}/confirm`, {
    method: 'POST', body: { paidAt, adminNote: adminNote || null },
  });
}

export function adminRejectBankTransfer(requestId, reason) {
  return http(`/v1/facemarket/admin/bank-transfers/${encodeURIComponent(requestId)}/reject`, {
    method: 'POST', body: { reason },
  });
}

export function adminListAudit({ limit = 20, targetType, targetId } = {}) {
  const params = new URLSearchParams({ limit: String(limit) });
  if (targetType) params.set('targetType', targetType);
  if (targetId) params.set('targetId', targetId);
  return http(`/v1/facemarket/admin/audit?${params.toString()}`);
}

export function adminListUsageReports({ status, limit = 100, cursor } = {}) {
  const params = new URLSearchParams({ limit: String(limit) });
  if (status) params.set('status', status);
  if (cursor) params.set('cursor', cursor);
  return http(`/v1/facemarket/admin/usage-reports?${params.toString()}`);
}

export function adminUpdateUsageReportStatus(reportId, status) {
  return http(`/v1/facemarket/admin/usage-reports/${encodeURIComponent(reportId)}`, {
    method: 'PATCH', body: { status },
  });
}

export function adminListPayoutStatements({ month }) {
  return http(`/v1/facemarket/admin/payout-statements?month=${encodeURIComponent(month)}`);
}

export function adminSetPayoutStatementStatus(modelId, periodMonth, status, note, expectedConfirmationId) {
  return http(`/v1/facemarket/admin/payout-statements/${encodeURIComponent(modelId)}/${encodeURIComponent(periodMonth)}/status`, {
    method: 'POST', body: { status, ...(note ? { note } : {}), ...(expectedConfirmationId ? { expectedConfirmationId } : {}) },
  });
}

export function adminRevealPayoutAccount(modelId) {
  return http(`/v1/facemarket/admin/models/${encodeURIComponent(modelId)}/payout-account`);
}

export function adminConfirmPayoutStatement(modelId, periodMonth, confirmationId) {
  return http(`/v1/facemarket/admin/payout-statements/${encodeURIComponent(modelId)}/${encodeURIComponent(periodMonth)}/confirm`, {
    method: 'POST', body: { confirmationId },
  });
}

// POST .../simulate — 데모 전용. 서버가 stub 제공자일 때만 동작하고(아니면 409),
// **돈은 움직이지 않는다.** outcome 으로 실패도 고를 수 있다.
export function adminSimulatePayoutConfirmation(confirmationId, outcome) {
  return http(`/v1/facemarket/admin/payout-confirmations/${encodeURIComponent(confirmationId)}/simulate`, {
    method: 'POST', body: { outcome },
  });
}

// 지급 완료(action='paid')는 2026-09-26 부터 실제 이체 기록이 있어야 한다 —
// transfer = { transferReference, amount, transferredOn(YYYY-MM-DD) }. 서버가 금액·날짜를 확인서와 대조한다.
export function adminAdvancePayoutConfirmation(confirmationId, action, transfer) {
  return http(`/v1/facemarket/admin/payout-confirmations/${encodeURIComponent(confirmationId)}/${encodeURIComponent(action)}`, {
    method: 'POST', ...(transfer ? { body: transfer } : {}),
  });
}

// POST .../payout-statements/{YYYY-MM}/run — 월말 정산 실행. 마감된 달의 체인 확정 정산을 모델별
// 지급 확인서(prepared)로 모은다. **돈은 움직이지 않는다**(moneyMoved:false) — 이체는 담당자가
// 은행 앱에서 하고 참조번호를 기록한다. 다시 눌러도 확인서가 늘지 않는다.
export function adminRunMonthlyPayout(periodMonth) {
  return http(`/v1/facemarket/admin/payout-statements/${encodeURIComponent(periodMonth)}/run`, { method: 'POST' });
}

// POST .../payout-statements/{이번 달}/run?interim=true — 중간 정산(2026-09-27). 마감 전인 이번 달의
// 체인 확정 정산 중 아직 어느 확인서에도 없는 것을 **누른 시각(cutoffAt)까지** 모은다. 돈은 움직이지
// 않는다. 월말 정산은 나중에 남은 몫만 모은다(이미 담긴 정산은 다시 못 담는다).
export function adminRunInterimPayout(periodMonth) {
  return http(`/v1/facemarket/admin/payout-statements/${encodeURIComponent(periodMonth)}/run?interim=true`, { method: 'POST' });
}

// ── 정산 체인 대조(2026-09-26) ── 서버가 getSettlement 를 그 자리에서 eth_call 로 읽어 DB 값과
// 칸마다 비교한다 → { verdict:'match'|'mismatch'|'not_found', fields:[{key,label,db,chain,match}],
// checkedAt, chainId, contractAddress, txHash }. 체인을 못 읽으면 502/503 으로 throw(성공 흉내 없음).
export function adminListSettlements({ limit = 100 } = {}) {
  return http(`/v1/facemarket/admin/settlements?limit=${encodeURIComponent(limit)}`);
}

export function adminCheckSettlementOnChain(settlementId) {
  return http(`/v1/facemarket/admin/settlements/${encodeURIComponent(settlementId)}/chain-check`, { suppressErrorLog: true });
}

export function adminRevealPayoutConfirmation(confirmationId) {
  return http(`/v1/facemarket/admin/payout-confirmations/${encodeURIComponent(confirmationId)}/account`);
}

// ── 관리자: 출처 추적(2026-09-26) ─────────────────────────────────────────────
// 쇼핑몰에서 발견한 이미지 → 워터마크 판독 + 지문(pHash) 대조 → 배포본·셀러·모델·라이선스 후보.
// 서버가 관리자·기기 게이트를 판정하고 감사 원장에 남긴다. 이미지는 저장하지 않는다.
export async function adminTraceImage(file) {
  const form = new FormData();
  form.append('image', file, file?.name || 'found-image');
  return checkedJson(await _authFetch('/v1/facemarket/admin/trace', { method: 'POST', body: form }),
    '출처를 추적하지 못했어요. 잠시 후 다시 시도해 주세요.');
}

// ── 관리자: 기기 게이트(설계 2026-09-11-admin-device-gate-design.md §5.3) ────────────
// register·me 는 기기 없이 열린다(아직 기기가 없는 관리자가 부른다). 나머지는 승인 기기 필수.

export function adminRegisterDevice({ label, userAgent } = {}) {
  return http('/v1/facemarket/admin/devices/register', {
    method: 'POST', body: { label: label || null, userAgent: userAgent || null },
  });
}

export function adminDeviceMe() {
  return http('/v1/facemarket/admin/devices/me');
}

export function adminListDevices() {
  return http('/v1/facemarket/admin/devices');
}

export function adminApproveDevice(deviceId) {
  return http(`/v1/facemarket/admin/devices/${encodeURIComponent(deviceId)}/approve`, { method: 'POST' });
}

export function adminRevokeDevice(deviceId) {
  return http(`/v1/facemarket/admin/devices/${encodeURIComponent(deviceId)}/revoke`, { method: 'POST' });
}

// ── 관리자: 모델 테스트컷(콘솔 모델 상세의 하위 리소스) ────────────────────

export function adminModelTestCuts(modelId) {
  return http(`/v1/facemarket/admin/models/${encodeURIComponent(modelId)}/test-cuts`);
}

// 손으로 올리는 경로. 보정(prod|texture|soft50)은 **필수**예요 — 없으면 어느 묶음인지 몰라
// 전송에서 영영 빠져요(서버가 400 으로 막아요).
export async function adminUploadModelTestCuts(modelId, files, kind, skinFinish) {
  const form = new FormData();
  form.append('kind', kind);
  form.append('skin_finish', skinFinish);
  for (const file of files) form.append('images', file, file.name || 'test-cut');
  return checkedJson(await _authFetch(
    `/v1/facemarket/admin/models/${encodeURIComponent(modelId)}/test-cuts`,
    { method: 'POST', body: form },
  ), '테스트컷 업로드에 실패했어요. 잠시 후 다시 시도해 주세요.');
}

export function adminDeleteModelTestCut(modelId, cutId) {
  return http(
    `/v1/facemarket/admin/models/${encodeURIComponent(modelId)}/test-cuts/${encodeURIComponent(cutId)}`,
    { method: 'DELETE' },
  );
}

// 고른 보정 한 묶음(4장)만 등록자에게 보내요. 그 값이 그 사람의 설정이 돼요.
export function adminSendModelTestCuts(modelId, skinFinish) {
  return http(`/v1/facemarket/admin/models/${encodeURIComponent(modelId)}/send-test-cuts`, {
    method: 'POST',
    body: { skinFinish },
  });
}

// 테스트컷 12장 자동 생성(수동 실행·다시 생성). 실제 생성은 서버 큐가 해요.
export function adminBuildModelTestCuts(modelId) {
  return http(`/v1/facemarket/admin/models/${encodeURIComponent(modelId)}/test-cuts/build`, {
    method: 'POST',
  });
}

export async function adminFetchModelTestCutUrl(imageUri) {
  const res = await _authFetch(imageUri);
  if (!res.ok) throw new Error('테스트컷을 불러오지 못했어요.');
  return URL.createObjectURL(await res.blob());
}

// ── 모델 본인: 테스트컷 확인 게이트 ────────────────────────────────────────

export function getMyModelTestCuts() {
  return http('/v1/facemarket/model/test-cuts');
}

export async function fetchMyModelTestCutUrl(imageUri) {
  const res = await _authFetch(imageUri);
  if (!res.ok) throw new Error('테스트컷을 불러오지 못했어요.');
  return URL.createObjectURL(await res.blob());
}

export function confirmMyModelTestCuts({ closeupCutId, fullbodyCutId }) {
  return http('/v1/facemarket/model/test-cuts/confirm', {
    method: 'POST', body: { closeupCutId, fullbodyCutId },
  });
}

export function requestMyModelTestCutRedo(reason) {
  return http('/v1/facemarket/model/test-cuts/redo', {
    method: 'POST', body: reason ? { reason } : {},
  });
}

// POST /v1/facemarket/enrollments/{id}/physique — 체형·키(선택, 비게이팅) 저장. 서버가
// enum·성별 일치를 검증(app.facemarket_physique)하고 갱신된 EnrollmentView 를 돌려준다.
export function submitPhysique({ enrollmentId, heightBucket, bodyType }) {
  return http(`/v1/facemarket/enrollments/${encodeURIComponent(enrollmentId)}/physique`, {
    method: 'POST', body: { heightBucket, bodyType },
  });
}

export function createLivenessSession(enrollmentId, nonce) {
  return http(`/v1/facemarket/enrollments/${encodeURIComponent(enrollmentId)}/liveness-session`, {
    method: 'POST', body: { nonce },
  });
}

// 기본 완료 경로에는 신분증 초상이 포함되지 않아요.
export function completeEnrollment(enrollmentId, { sessionId } = {}, { signal } = {}) {
  return http(`/v1/facemarket/enrollments/${encodeURIComponent(enrollmentId)}/complete`, {
    method: 'POST', body: sessionId ? { sessionId } : {}, signal,
  });
}

export async function fetchEnrollmentPhotoUrl(enrollmentId, slot, { signal } = {}) {
  const res = await _authFetch(`/v1/facemarket/enrollments/${encodeURIComponent(enrollmentId)}/photos/${encodeURIComponent(slot)}`, { signal });
  if (!res.ok) await checkedJson(res, '사진을 불러오지 못했어요.');
  return URL.createObjectURL(await res.blob());
}

export function cancelEnrollment(enrollmentId) {
  return http(`/v1/facemarket/enrollments/${encodeURIComponent(enrollmentId)}/cancel`, { method: 'POST' });
}

export function reopenEnrollmentPhotos(enrollmentId) {
  return http(`/v1/facemarket/enrollments/${encodeURIComponent(enrollmentId)}/reopen-photos`, { method: 'POST' });
}

export function createLicense({
  enrollmentId, allowedUse = [], forbiddenUse = [],
}, { signal } = {}) {
  return http('/v1/facemarket/licenses', {
    method: 'POST',
    body: { enrollmentId, allowedUse, forbiddenUse }, signal,
  });
}

// GET /v1/facemarket/licenses — 내 라이선스 목록. [{ id, faceImageUri, allowedUse, ... }].
export function listLicenses({ includeRevoked = false } = {}) {
  if (MOCK) return Promise.resolve([]);
  return http(`/v1/facemarket/licenses${includeRevoked ? '?includeRevoked=true' : ''}`);
}

// POST /v1/facemarket/licenses/{id}/revoke (소유자 스코프) — 라이선스를 해지한다.
// 갱신된 LicenseCard(status:'revoked') 반환. 해지 즉시 얼굴 게이트와 생성 verify 게이트가
// 이 모델을 차단한다(재생성 시 409 license_revoked). 멱등 — 이미 해지된 라이선스도 안전.
export function revokeLicense(id) {
  return http(`/v1/facemarket/licenses/${id}/revoke`, { method: 'POST' });
}

// GET /v1/facemarket/jobs/{jobId}/settlement — 생성 잡이 속한 상품의 온체인 정산 영수증(payment_id=product:{projectId}:{날짜}, 레거시 job:{jobId}).
// → { paymentId, txHash, chainId, totalAmount, modelAmount, platformAmount, opsAmount, vcId, chainStatus,
//     recordedBlock, createdAt }  (recordedBlock·createdAt 은 2026-09-26 영수증 체인 칸용)
// (70/20/10 = 모델/플랫폼/운영). 정산 미기록(비 FaceMarket 잡·체인 지연 등)이면 404 → http() 가 throw.
export function getJobSettlement(jobId) {
  return http(`/v1/facemarket/jobs/${jobId}/settlement`);
}

// GET /v1/facemarket/settlements/{paymentId}/chain-check — 영수증 [체인에서 확인]. 잡 소유 셀러만.
export function checkSettlementOnChain(paymentId) {
  return http(`/v1/facemarket/settlements/${encodeURIComponent(paymentId)}/chain-check`, { suppressErrorLog: true });
}

// GET /v1/facemarket/model/settlements/{id}/chain-check — 정산 내역 [체인 확인]. 그 얼굴의 모델 본인만.
export function checkModelSettlementOnChain(settlementId) {
  return http(`/v1/facemarket/model/settlements/${encodeURIComponent(settlementId)}/chain-check`, { suppressErrorLog: true });
}

// GET /v1/facemarket/settlements → 로그인 모델 본인의 정산 기록(최신순, 최대 200건).
export function listSettlements() {
  if (MOCK) return Promise.resolve([]);
  return http('/v1/facemarket/settlements');
}

// 전체 기록의 모델 몫 합계 — 최근 200건 목록과 별도로 집계한다.
export function getSettlementSummary() {
  if (MOCK) return Promise.resolve({ monthCount: 0, monthAmount: 0, totalAmount: 0 });
  return http('/v1/facemarket/settlements/summary');
}

export function getPayoutStatements() {
  return http('/v1/facemarket/payout-statements');
}

export function getPublicationPreviewUrl(publicationId) {
  return http(`/v1/facemarket/model/publications/${encodeURIComponent(publicationId)}/preview-url`, { suppressErrorLog: true });
}

// GET /v1/facemarket/model/settlements/{id}/preview-url — 정산 한 건의 그림.
// → { url, expiresIn, source:'publication'|'cut' }. 셀러가 발행(다운로드 공증)한 상세페이지가
// 있으면 그것, 없으면 그 잡이 만든 생성 컷. 발행이 0건이던 운영에서 목록이 전부 빈 그림이었다.
export function getSettlementPreviewUrl(settlementId) {
  return http(`/v1/facemarket/model/settlements/${encodeURIComponent(settlementId)}/preview-url`, { suppressErrorLog: true });
}

// GET /v1/facemarket/models/{id}/usage — 모델 본인의 얼굴 사용 내역.
// → [{ kind:'cut'|'publication', createdAt, imageHashPrefix, chainStatus }]
// 셀러/프로젝트/원본 해시는 응답에 없다(모델에게 필요한 건 횟수·체인 기록 여부뿐).
export function listModelUsage(modelId) {
  return http(`/v1/facemarket/models/${encodeURIComponent(modelId)}/usage`);
}

// GET /v1/facemarket/verify/{id} — QR 공개 검증. **무인증**(심사위원·구매자가 스캔).
// http() 는 세션이 없으면 요청 전에 throw 하므로(httpAdapter) 여기선 쓸 수 없다 — 생 fetch.
// 응답은 서버 화이트리스트(PublicVerifyResult) 그대로:
//   { valid, status, allowedUse, forbiddenUse, unitPrice, validUntil, vcId, model:{ nameMasked, age } }
//   validUntil은 영구 라이선스에서 null일 수 있어요.
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

// GET /v1/facemarket/publications/verify/{id} — 배포본 공개 검증. **무인증**.
// C2PA 매니페스트의 verifyUrl 이 여기로 온다(파일 안에 박혀 배포된 뒤 회수 불가).
// 응답은 서버 화이트리스트(PublicationVerifyResult) 그대로:
//   { valid, status, publishedAt, imageHashPrefix, kind, allowedUse, forbiddenUse,
//     licenseValidUntil, chain, model:{ nameMasked, age } }
// 얼굴·CI·생년월일·user_id·model_id·seller_id·내부 R2 키·전체 image_sha256 은 서버가
// 애초에 싣지 않는다. 해지가 즉시 반영돼야 하므로 캐시 금지(위 verifyLicensePublic 과 동일 패턴).
export async function verifyPublicationPublic(publicationId) {
  const res = await fetch(
    `${BASE_URL}/v1/facemarket/publications/verify/${encodeURIComponent(publicationId)}`,
    { headers: { Accept: 'application/json' }, cache: 'no-store' },
  );
  if (!res.ok) {
    let message = res.status === 404
      ? '찾을 수 없는 기록이에요.'
      : '확인하지 못했어요. 잠시 후 다시 시도해 주세요.';
    try { const p = await res.json(); if (p?.error?.message) message = p.error.message; } catch { /* 비 JSON */ }
    const err = new Error(message);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

// POST /v1/facemarket/publications/presign — 배포본 업로드 URL. 행은 아직 안 만든다.
export function presignPublication({ projectId, kind, byteSize }) {
  return http('/v1/facemarket/publications/presign', {
    method: 'POST', body: { projectId, kind, byteSize },
  });
}

// POST /v1/facemarket/publications/sign — 해시·원장·C2PA 서명. 응답의 publicationId 가 정본.
// projectId·kind 는 uploadToken 안에 서명돼 있다 — 여기서 다시 보내지 않는다.
export function signPublication({ uploadToken }) {
  return http('/v1/facemarket/publications/sign', {
    method: 'POST', body: { uploadToken },
  });
}

// 게이트 얼굴 이미지 → objectURL. <img> 는 Bearer 를 못 보내므로 fetch+blob 로 인증해 받는다.
// 호출부는 표시 후 URL.revokeObjectURL 로 해제할 것.
export async function fetchLicenseFaceUrl(faceImageUri) {
  const res = await _authFetch(faceImageUri);
  if (!res.ok) throw new Error('얼굴 이미지를 불러오지 못했어요.');
  return URL.createObjectURL(await res.blob());
}

export function reportUsage(paymentId, reason) {
  return http(`/v1/facemarket/settlements/${encodeURIComponent(paymentId)}/report`, { method: 'POST', body: { reason } });
}
export function updateLicenseTerms(licenseId, terms) {
  return http(`/v1/facemarket/licenses/${encodeURIComponent(licenseId)}/terms`, { method: 'PATCH', body: terms });
}
export function pauseMyModel(modelId) {
  return http(`/v1/facemarket/models/${encodeURIComponent(modelId)}/pause`, { method: 'POST' });
}
export function resumeMyModel(modelId) {
  return http(`/v1/facemarket/models/${encodeURIComponent(modelId)}/resume`, { method: 'POST' });
}

// 협찬 프로필은 초상 라이선스와 별도로 저장해요. 사용자 ID와 기준일은 서버가 정해요.
export async function updateModelSponsorship(modelId, settings, screen = 'sponsorship_settings') {
  const fields = ['sponsorshipEnabled', 'instagramHandle', 'instagramFollowers', 'sizeTop', 'sizeBottomWaist', 'profileConsent'];
  const body = Object.fromEntries(fields.filter(key => Object.hasOwn(settings, key)).map(key => [key, settings[key]]));
  const updated = await http(`/v1/facemarket/models/${encodeURIComponent(modelId)}/sponsorship`, { method: 'PATCH', body, headers: { 'X-Facemarket-Screen': screen } });
  if (typeof window !== 'undefined') window.dispatchEvent(new CustomEvent('facemarket:sponsorship-changed', { detail: { modelId } }));
  return updated;
}

// GET …/sponsorship/credential — 협찬 동의 증명서(VC) 상태(본인 모델만).
// → { featureEnabled, status: none|waiting_license|pending|active, vcId, issuedAt }.
// featureEnabled=false 면 화면은 지금과 똑같이 아무것도 더 그리지 않아요.
const NO_SPONSORSHIP_CREDENTIAL = { featureEnabled: false, status: 'none', vcId: null, issuedAt: null };
export function getSponsorshipCredential(modelId) {
  if (MOCK || !modelId) return Promise.resolve(NO_SPONSORSHIP_CREDENTIAL);
  return http(`/v1/facemarket/models/${encodeURIComponent(modelId)}/sponsorship/credential`);
}

export function getSponsorshipInterest(modelId) {
  return http(`/v1/facemarket/models/${encodeURIComponent(modelId)}/sponsorship-interest`);
}

export function requestSponsorshipInterest(modelId) {
  return http(`/v1/facemarket/models/${encodeURIComponent(modelId)}/sponsorship-interest`, { method: 'POST', body: {} });
}
