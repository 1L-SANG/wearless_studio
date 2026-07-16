/* =============================================================
   lib/api/personalization — 개인화(사용자 얼굴·신체) 모델 API.
   계약: docs/personalization/api-spec.md. facemarket.js 와 동일 패턴 —
   http() 는 JSON 전용이라 멀티파트(얼굴 업로드)·바이너리(게이트 파일)·
   204 No Content(슬롯 삭제)는 직접 fetch 한다. 얼굴 바이트는 절대
   <img src> 공개 URL 로 노출하지 않는다 — Bearer fetch + objectURL 만
   (api-spec §1.4 하드 룰). 응답은 서버 화이트리스트 필드만 그대로 통과
   (r2_key 등 내부 필드는 서버가 애초에 주지 않는다).
   ============================================================= */
import { http } from '@/lib/api/httpAdapter.js';
import { supabase } from '@/lib/supabase.js';

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '';

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

// 서버 에러봉투 { error: { code, message, reasons? } } (api-spec §1.3) 를 파싱해 status·code·
// reasons 를 실은 Error 를 throw. http() 와 동일 계약이지만 multipart/204 요청은 http() 를
// 못 쓰므로(JSON-only) 여기서 직접 구현 — facemarket.createLicense 의 인라인 파싱과 동일 패턴.
async function _throwEnvelope(res, fallback) {
  let message = fallback;
  let code;
  let reasons;
  try {
    const payload = await res.json();
    if (payload?.error?.message) message = payload.error.message;
    if (payload?.error?.code) code = payload.error.code;
    if (Array.isArray(payload?.error?.reasons)) reasons = payload.error.reasons;
  } catch { /* 비 JSON 응답 — 기본 메시지 유지 */ }
  const err = new Error(message);
  err.status = res.status;
  if (code) err.code = code;
  if (reasons) err.reasons = reasons;
  throw err;
}

// ---- 본인확인(성인 인증, T2-1) — FaceMarket 인증창으로 흡수됨 -------------
// 본인확인 화면이 /model/register 하나로 통합됐다 — CX 인증 성공 시 서버(POST
// /v1/facemarket/identity/verify → facemarket.verifyIdentity)가 FaceMarket 모델 등록과
// 개인화 성인 인증(is_adult)을 함께 기록한다. 이 POST /v1/personalization/identity:verify
// 라우트는 서버엔 당분간 남아있지만, 프론트는 더 이상 별도 호출하지 않는다(중복 인증창 제거).

// ---- 동의 (api-spec §3.1) --------------------------------------------------
export function getConsents() {
  return http('/v1/personalization/consents');
}
// items: [{ type, docVersion }]. 이미 granted 인 항목 재제출은 서버가 멱등 처리.
export function submitConsents(items) {
  return http('/v1/personalization/consents', { method: 'POST', body: { items } });
}
// service_use·cross_border_transfer 철회는 서버가 즉시 전체 캐스케이드 파기로 이어간다(§3.1) —
// 응답에 purgeJobId 가 실릴 수 있다.
export function withdrawConsent(type) {
  return http(`/v1/personalization/consents/${type}:withdraw`, { method: 'POST' });
}

// ---- 얼굴 사진 (api-spec §3.2) — multipart 직접 수신, 비공개 버킷, 동기 QC ---
// 통과 201 { angle, qcStatus:'passed', qcReasons:[], imageUri, byteSize, uploadedAt }.
// (imageDigest 는 생체 파생 고정 식별자라 서버가 응답에 싣지 않는다 — api-spec §1.4.)
// 불합격 400 { error:{ code:'face_quality', message, reasons:[qc_reason,...] } } — reasons 를 그대로
// 실어 던진다. 화면은 사유코드별 재업로드 카피(occlusion·low_resolution·multiple_faces·angle_mismatch)를
// reasons 로 매핑해 보여준다.
export async function uploadFacePhoto({ angle, fileBlob, filename }) {
  const fd = new FormData();
  fd.append('photo', fileBlob, filename || 'photo');
  fd.append('angle', angle);
  const res = await _authFetch('/v1/personalization/face-photos', { method: 'POST', body: fd });
  if (!res.ok) await _throwEnvelope(res, '얼굴 사진 업로드에 실패했어요. 잠시 후 다시 시도해 주세요.');
  return res.json();
}

// [{ angle, qcStatus:'none'|'passed', qcReasons:[], imageUri, uploadedAt }], complete:boolean.
export function listFacePhotos() {
  return http('/v1/personalization/face-photos');
}

// 게이트 얼굴 바이트 → objectURL(Bearer 필수, §1.4). 호출부는 표시 후 URL.revokeObjectURL 로 해제할 것.
export async function fetchFacePhotoUrl(imageUri) {
  const res = await _authFetch(imageUri);
  if (!res.ok) throw new Error('얼굴 사진을 불러오지 못했어요.');
  return URL.createObjectURL(await res.blob());
}

// 슬롯 삭제 — 204 No Content(빈 슬롯 삭제도 204, 멱등). http() 의 res.json() 파싱을 못 쓴다.
export async function deleteFacePhoto(angle) {
  const res = await _authFetch(`/v1/personalization/face-photos/${angle}`, { method: 'DELETE' });
  if (!res.ok) await _throwEnvelope(res, '얼굴 사진 삭제에 실패했어요.');
}

// ---- 신체 프로필 (api-spec §3.3) ------------------------------------------
// body: { heightCm, weightKg, bodyType, bodyTypeCustom, gender } — 전체 교체(REPLACE).
export function putBodyProfile(body) {
  return http('/v1/personalization/profile/body', { method: 'PUT', body });
}
// { id, status, body:{…|null}, photos:[…], consents:[…], createdAt, updatedAt }.
export function getProfile() {
  return http('/v1/personalization/profile');
}

// ---- 상태 (api-spec §3.4) — 온보딩 체크리스트/생성 게이트 단일 소스 --------
// { status, canGenerate, blockers:[{code,detail}], purgeJobId }. 프로필 없음/purged 도 200.
export function getStatus() {
  return http('/v1/personalization/status');
}

// ---- 삭제·철회 (api-spec §3.5) — 전체 캐스케이드 파기 ----------------------
// 202 { purgeJobId, status:'purging' }. 진행 상태는 기존 GET /v1/jobs/{id} 재사용.
export function withdrawAll() {
  return http('/v1/personalization:withdraw', { method: 'POST' });
}

// ---- 생성 (api-spec §4, 엔진-의존 — 계약 골격만) ---------------------------
// job 폴링 — httpAdapter.pollJob 미러(기존 job 패턴 재사용, plan §7). status: pending|running|done|error.
async function _pollJob(jobId, { onProgress, intervalMs = 1200, timeoutMs = 300000 } = {}) {
  const start = Date.now();
  let last = -1;
  for (;;) {
    const job = await http(`/v1/jobs/${jobId}`);
    if (typeof job.progress === 'number' && job.progress !== last) {
      last = job.progress;
      onProgress && onProgress(job.progress);
    }
    if (job.status === 'done') { onProgress && onProgress(100); return job.result; }
    if (job.status === 'error') throw new Error(job.errorMessage || '생성에 실패했어요.');
    if (Date.now() - start > timeoutMs) throw new Error('생성이 지연되고 있어요. 잠시 후 다시 확인해 주세요.');
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}

// 202 { jobId } → 폴링 → 완료 result. 결과 shape 은 §4 가 TBD 로 남긴 계약이라, 게이트 URI 배열
// ({ results: [uri,...] })을 가정하고 화면은 방어적으로 읽는다. 서버 게이트 실패(403 consent_required/
// minor_blocked · 409 profile_not_ready/purge_in_progress · 402 insufficient_credits)는 http() 가
// status·code 를 실어 그대로 throw.
export async function startGeneration({ productImageAssetIds, projectId, options } = {}, { onProgress } = {}) {
  const { jobId } = await http('/v1/personalization/generations', {
    method: 'POST',
    body: { productImageAssetIds, projectId: projectId ?? null, options: options ?? {} },
  });
  const result = await _pollJob(jobId, { onProgress });
  return { jobId, result };
}

// 개인화 생성 결과 게이트 바이트 → objectURL(§1.4). uri 는 서버가 준 상대경로(게이트 URI) 그대로 사용.
export async function fetchGenerationResultUrl(uri) {
  const res = await _authFetch(uri);
  if (!res.ok) throw new Error('생성 결과 이미지를 불러오지 못했어요.');
  return URL.createObjectURL(await res.blob());
}
