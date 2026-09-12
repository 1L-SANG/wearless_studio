/* =============================================================
   lib/api/facemarket — FaceMarket 전용 API (셀러 스튜디오 api 경계와 분리).
   프로덕션은 실서버 전용이고 명시적인 개발 mock 모드만 로컬 자료를 쓴다. http() 헬퍼를 재사용해 Supabase 세션 Bearer 를 주입한다.
   verifyIdentity: CX 표준인증창(ENT_MID) 성공 token만 백엔드로 — 원문 신원은
   서버가 CX trans 에서 직접 받는다(클라→서버 PII 신뢰 금지).
   ============================================================= */
import { http } from '@/lib/api/httpAdapter.js';
import { supabase } from '@/lib/supabase.js';
import { DEVICE_HEADER, readDeviceToken } from '../adminDevice.js';

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

export function createEnrollment({ documentVersion, deviceId }) {
  return http('/v1/facemarket/enrollments', {
    method: 'POST',
    body: {
      biometricConsent: { accepted: true, documentVersion },
      termsConsent: { accepted: true, documentVersion },
      deviceId,
    },
  });
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

export async function deleteStagedApplicationPhoto(kind = 'profile') {
  if (MOCK) return (await mockApi()).deleteStagedApplicationPhoto(kind);
  return checkedJson(await _authFetch(
    `/v1/facemarket/applications/photo-staging/${encodeURIComponent(kind)}`,
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
  const res = await _authFetch(
    `/v1/facemarket/admin/applications/${encodeURIComponent(applicationId)}/profile-image?kind=${encodeURIComponent(kind)}`,
  );
  if (!res.ok) throw new Error('사진을 불러오지 못했어요.');
  const blob = await res.blob();
  return URL.createObjectURL(blob);
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

export function adminAdvancePayoutConfirmation(confirmationId, action) {
  return http(`/v1/facemarket/admin/payout-confirmations/${encodeURIComponent(confirmationId)}/${encodeURIComponent(action)}`, { method: 'POST' });
}

export function adminRevealPayoutConfirmation(confirmationId) {
  return http(`/v1/facemarket/admin/payout-confirmations/${encodeURIComponent(confirmationId)}/account`);
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

export async function adminUploadModelTestCuts(modelId, files, kind) {
  const form = new FormData();
  form.append('kind', kind);
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

export function adminSendModelTestCuts(modelId) {
  return http(`/v1/facemarket/admin/models/${encodeURIComponent(modelId)}/send-test-cuts`, {
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
// → { paymentId, txHash, chainId, totalAmount, modelAmount, platformAmount, opsAmount, vcId, chainStatus }
// (70/20/10 = 모델/플랫폼/운영). 정산 미기록(비 FaceMarket 잡·체인 지연 등)이면 404 → http() 가 throw.
export function getJobSettlement(jobId) {
  return http(`/v1/facemarket/jobs/${jobId}/settlement`);
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
