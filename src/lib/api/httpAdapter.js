/* =============================================================
   httpAdapter — FastAPI 실서버 구현 (plan §8, Phase 1+에서 함수
   단위로 채운다). 여기 구현된 함수만 http 모드에서 mock 을 대체하고,
   나머지는 mock 이 계속 담당한다 (부분 스왑).
   시그니처·반환 형태는 mock/api.js(계약 §6)와 동일해야 한다.
   ============================================================= */
import { supabase } from '@/lib/supabase.js';
import { LIMITS } from '@/lib/limits.js';
import { applySeededHookStyle, defaultAnalysisShape, defaultStoryboard, isDefaultStoryboardForMode } from '@/lib/api/shapes.js';
import { normalizeMatchClothingSelection, toMatchItem } from '@/lib/api/matchingItems.js';
import { deriveHookFrame } from '@/lib/storyboardHookFrame.js';
import { selectPublicAnalysisPhotos } from '@/lib/publicAnalysisPhotos.js';
import { normalizeAnalysisFit } from '@/lib/fitAxes.js';

export { toMatchItem } from '@/lib/api/matchingItems.js';

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '';
const LONG_IMAGE_JOB_TIMEOUT_MS = 15 * 60 * 1000;
const DEFAULT_JOB_TIMEOUT_MESSAGE = '작업이 지연되고 있어요. 잠시 후 다시 시도해 주세요.';
const MANNEQUIN_JOB_TIMEOUT_MESSAGE = '마네킹컷 생성이 예상보다 오래 걸리고 있어요. 잠시 후 다시 확인해 주세요.';
const MANNEQUIN_ADJUST_JOB_TIMEOUT_MESSAGE = '마네킹컷 조정이 예상보다 오래 걸리고 있어요. 잠시 후 다시 확인해 주세요.';
const BROWSER_OFFLINE_MESSAGE = '인터넷 연결이 끊겼어요. 연결을 확인해 주세요. 개발자 도구 Network 설정이 Offline이면 No throttling으로 바꿔 주세요.';

const isBrowserOffline = () => globalThis.navigator?.onLine === false;

function networkError(code, message, context, cause) {
  const offline = isBrowserOffline();
  const finalCode = offline ? 'browser_offline' : code;
  const finalMessage = offline ? BROWSER_OFFLINE_MESSAGE : message;
  // presigned URL·Bearer token은 로그에 남기지 않고, 단계·path·origin만 남겨
  // 브라우저가 모든 CORS/연결 실패를 같은 `Failed to fetch`로 숨겨도 구분한다.
  console.error(`[network:${finalCode}]`, { ...context, online: !offline }, cause);
  const error = new Error(finalMessage);
  error.code = finalCode;
  error.cause = cause;
  return error;
}

const browserOrigin = () => globalThis.location?.origin || 'unknown';

// 서버는 에셋 이미지를 안정 앱 URL `/v1/assets/{id}/file`(상대경로)로 반환한다. 프론트는 다른
// 도메인(Vercel)에서 서빙되므로 <img src> 가 그대로 쓰면 프론트 도메인에 붙어 404 가 난다.
// 모든 응답이 지나는 http() 초크포인트에서 재귀로 절대화한다(API 도메인 프리픽스).
// vary 요청이 src 를 서버로 되돌려보내도 워커의 _ASSET_FILE_RE 는 search(비앵커)라 절대 URL 도 파싱된다.
function absolutizeAssetUrls(v) {
  if (typeof v === 'string') {
    return v.startsWith('/v1/assets/') ? `${BASE_URL}${v}` : v;
  }
  if (Array.isArray(v)) return v.map(absolutizeAssetUrls);
  if (v && typeof v === 'object') {
    const out = {};
    for (const k of Object.keys(v)) out[k] = absolutizeAssetUrls(v[k]);
    return out;
  }
  return v;
}

// 공용 fetch 헬퍼 — Supabase 세션의 access_token 을 Bearer 로 주입 (plan §9).
// 에러 봉투 { error: { code, message } } 의 한국어 message 를 그대로 throw (계약 §6).
export async function http(path, {
  method = 'GET', body, signal, keepalive, headers: requestHeaders,
} = {}) {
  let data;
  try {
    ({ data } = await supabase.auth.getSession());
  } catch (cause) {
    throw networkError(
      'auth_session_network',
      '로그인 상태를 확인하지 못했어요. 페이지를 새로고침한 뒤 다시 시도해 주세요.',
      { stage: 'auth_session', path, origin: browserOrigin() },
      cause,
    );
  }
  const token = data?.session?.access_token;
  if (!token) {
    // http 모드에 mock 폴백은 없다 — 무세션이면 전 호출이 401 폭탄이 되므로 요청 전에 명확히 실패시킨다.
    console.error(`API no-session ${path}`);
    throw new Error('로그인이 필요해요. 로그인 후 다시 시도해 주세요.');
  }

  // Chrome DevTools의 Offline 에뮬레이션을 포함해 브라우저가 오프라인이면 요청 자체를 보내지
  // 않는다. 응답이 아예 없을 때 뒤따르는 가짜 CORS 메시지를 서버 장애로 오진하지 않게 한다.
  if (isBrowserOffline()) {
    throw networkError(
      'api_network',
      '서버에 연결하지 못했어요. 페이지를 새로고침한 뒤 다시 시도해 주세요.',
      { stage: 'api', method, path, origin: browserOrigin() },
    );
  }

  let res;
  try {
    res = await fetch(`${BASE_URL}${path}`, {
      method,
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(requestHeaders || {}),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
      keepalive,
    });
  } catch (cause) {
    if (cause?.name === 'AbortError') throw cause;
    throw networkError(
      'api_network',
      '서버에 연결하지 못했어요. 페이지를 새로고침한 뒤 다시 시도해 주세요.',
      { stage: 'api', method, path, origin: browserOrigin() },
      cause,
    );
  }

  if (!res.ok) {
    // 계약 §6: 사용자에게 그대로 보여줄 한국어 message. envelope 없으면 한국어 기본값.
    let message = '요청을 처리하지 못했어요. 잠시 후 다시 시도해 주세요.';
    let code;
    let meta;
    try {
      const payload = await res.json();
      if (payload?.error?.message) message = payload.error.message;
      if (payload?.error?.code) code = payload.error.code;
      if (payload?.error?.meta) meta = payload.error.meta;
    } catch { /* 비 JSON 응답 — 기본 메시지 유지 */ }
    console.error(`API ${res.status} ${path}`); // 기술 세부는 콘솔로만
    // status·code 를 에러에 실어 호출부가 분기할 수 있게 한다(예: 409 라이선스 차단 → 블로킹 패널,
    // 404 무효 상태 vs 일시 장애 구분). message 는 그대로라 기존 catch(e.message) 는 영향 없음(하위호환).
    const err = new Error(message);
    err.status = res.status;
    if (code) err.code = code;
    if (meta) err.meta = meta;
    throw err;
  }
  if (res.status === 204) return null;
  return absolutizeAssetUrls(await res.json());
}

/* 톤 에디터 전용 바이트 취득.

   `/assets/{id}/file` 은 R2 공개 도메인으로 302 를 준다. 캔버스로 픽셀을 **읽으려면**
   그 최종 응답에 CORS 헤더가 있어야 하는데 그건 CDN 설정이라 앱이 보장할 수 없다. 그래서
   편집 소스만 API 가 직접 실어 보내는 라우트를 쓴다 — 여기 CORS 는 우리 것이다.
   에디터를 열 때 원본 1장 + 마스크 1장이고, 슬라이더를 움직이는 동안엔 0장이다. */
export async function httpBlob(path, { signal } = {}) {
  const { data } = await supabase.auth.getSession();
  const token = data?.session?.access_token;
  if (!token) throw new Error('로그인이 필요해요. 로그인 후 다시 시도해 주세요.');
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { Authorization: `Bearer ${token}` },
    signal,
  });
  if (!res.ok) {
    const err = new Error('이미지를 불러오지 못했어요.');
    err.status = res.status;
    throw err;
  }
  return res.blob();
}

// 공개 체험용 multipart 요청. 로그인 사용자는 optional_user가 유효 Bearer를 보고 IP 제한을
// 건너뛸 수 있게 토큰을 선호해서 붙이고, 세션 조회 실패·비로그인은 그대로 공개 경로를 쓴다.
async function publicHttp(path, formData, { signal } = {}) {
  let token;
  try {
    const { data } = await supabase.auth.getSession();
    token = data?.session?.access_token;
  } catch { /* 공개 분석은 인증 bootstrap 실패에도 익명으로 계속 가능 */ }
  let res;
  try {
    res = await fetch(`${BASE_URL}${path}`, {
      method: 'POST',
      headers: token ? { Authorization: `Bearer ${token}` } : undefined,
      body: formData,
      signal,
    });
  } catch (cause) {
    if (cause?.name === 'AbortError') throw cause;
    throw networkError(
      'public_analysis_network',
      '분석 서버에 연결하지 못했어요. 잠시 후 다시 시도해 주세요.',
      { stage: 'public_analysis', method: 'POST', path, origin: browserOrigin() },
      cause,
    );
  }
  if (!res.ok) {
    let message = '상품 분석에 실패했어요. 잠시 후 다시 시도해 주세요.';
    let code;
    try {
      const payload = await res.json();
      message = payload?.error?.message || message;
      code = payload?.error?.code;
    } catch { /* 기본 메시지 유지 */ }
    const error = new Error(message);
    error.status = res.status;
    if (code) error.code = code;
    throw error;
  }
  return res.json();
}

// job 폴링 어댑터 — job형 API(202 {jobId})를 mock 의 onProgress 콜백 계약으로 변환.
// GET /v1/jobs/{id} 를 폴링해 progress 를 전달하고, done 이면 result, error 면 한국어 message throw.
// SSE 대신 폴링(마네킹 경로와 동일 GET 재사용, plan §7). 무과금 분석엔 stall 로직 불필요.
async function pollJob(
  jobId,
  { onProgress, intervalMs = 1200, timeoutMs = 90000, timeoutMessage = DEFAULT_JOB_TIMEOUT_MESSAGE } = {},
) {
  const start = Date.now();
  let last = -1;
  for (;;) {
    const job = await http(`/v1/jobs/${jobId}`);
    if (job.status === 'cancelled') {
      const error = new Error('마네킹컷 생성이 취소됐어요.');
      error.code = 'job_cancelled';
      throw error;
    }
    if (typeof job.progress === 'number' && job.progress !== last) {
      last = job.progress;
      onProgress && onProgress(job.progress);
    }
    if (job.status === 'done') { onProgress && onProgress(100); return job.result; }
    if (job.status === 'error') {
      const error = new Error(job.errorMessage || '작업에 실패했어요.');
      error.code = 'job_failed';
      throw error;
    }
    if (Date.now() - start > timeoutMs) {
      // 타임아웃은 **실패가 아니다** — 화면이 기다리기를 그만둔 것뿐이고 서버 잡은 계속 돈다.
      // 호출부가 "실패 처리"와 구분할 수 있게 code 를 붙인다(2026-08-07: 이 구분이 없어서
      // 정상 진행 중인 생성이 실패 토스트 + 콘티보드 복귀로 처리됐다).
      const err = new Error(timeoutMessage);
      err.code = 'job_timeout';
      throw err;
    }
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}

// 사진 1장 업로드 — presigned URL 3콜(발급→R2 PUT→complete). {assetId, url} 반환.
// 로그인 후 draft 동기화(draftSync)도 이 함수를 재사용 (단일 업로드 계약, 서버 §3).
// 서명 PUT은 Bearer 안 씀(서명 자체가 인증).
export async function uploadPhoto(
  projectId,
  { filename, mime, blob, purpose = 'upload' },
  { signal } = {},
) {
  const { assetId, uploadUrl } = await http('/v1/assets/upload-url', {
    method: 'POST', body: { filename, mime, size: blob.size, projectId, purpose }, signal,
  });
  if (isBrowserOffline()) {
    throw networkError(
      'photo_upload_network',
      '사진 업로드 서버에 연결하지 못했어요. 페이지를 새로고침한 뒤 다시 시도해 주세요.',
      { stage: 'r2_put', origin: browserOrigin() },
    );
  }
  let put;
  try {
    put = await fetch(uploadUrl, {
      method: 'PUT', headers: { 'Content-Type': mime }, body: blob, signal,
    });
  } catch (cause) {
    if (cause?.name === 'AbortError') throw cause;
    throw networkError(
      'photo_upload_network',
      '사진 업로드 서버에 연결하지 못했어요. 페이지를 새로고침한 뒤 다시 시도해 주세요.',
      { stage: 'r2_put', origin: browserOrigin() },
      cause,
    );
  }
  if (!put.ok) {
    const error = new Error('사진 업로드에 실패했어요. 잠시 후 다시 시도해 주세요.');
    error.code = 'photo_upload_failed';
    error.status = put.status;
    throw error;
  }
  await http(`/v1/assets/${assetId}/complete`, {
    method: 'POST', body: { projectId, mime, filename, purpose }, signal,
  });
  // complete 응답의 R2 URL은 배포 설정에 따라 만료되는 서명 URL일 수 있다. 에디터 문서에는
  // 현재 R2 위치로 매번 리다이렉트하는 앱의 안정 에셋 경로를 저장해야 재접속 후에도 보인다.
  return { assetId, url: absolutizeAssetUrls(`/v1/assets/${assetId}/file`) };
}

// 브라우저 이미지 picker의 단일 경로 — 콘티의 내 이미지와 에디터 무드 레퍼런스가
// 모두 실제 업로드를 거쳐 같은 {assetId, url} 계약을 받는다. 취소/빈 선택은 업로드하지 않는다.
async function pickAndUploadImage(projectId) {
  const file = await new Promise((resolve) => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'image/*';
    input.onchange = () => resolve(input.files?.[0] || null);
    input.oncancel = () => resolve(null);
    input.click();
  });
  if (!file) return null;
  const { assetId, url } = await uploadPhoto(projectId, {
    filename: file.name,
    mime: file.type || 'image/jpeg',
    blob: file,
  });
  return { assetId, url };
}

// ---- 매칭 의류 / analysis (US-4) --------------------------------------------
// 서버는 GET /analysis 라우트가 없고 PATCH 가 REPLACE(payload=excluded.payload) 라, 전체 analysis 를
// 이 모듈에 캐시해 delta 를 머지한 full payload 로 저장한다(delta 만 보내면 다른 필드가 유실됨).
// analyzeProduct 가 seed, saveAnalysis 가 갱신. 페이지 세션 동안 유지(하드 새로고침 시 리셋 → 재분석).
// getMatchClothing 도 이 캐시를 읽어 화면 전환(분석→마네킹) 간 매칭 선택을 이월한다.
// projectId 로 스코프 — 보관함에서 다른 프로젝트를 열어도 이전 프로젝트의 매칭이 새지 않게 한다.
let analysisCache = { projectId: null, analysis: null };  // { projectId, analysis }
const cachedAnalysisFor = (projectId) =>
  (analysisCache.projectId === projectId ? analysisCache.analysis : null);

// '새 제작' 진입 시 이전 프로젝트의 analysis 캐시를 비운다(beginProject 가 호출). 프로젝트 스코프가
// 이미 교차 유출을 막지만, 명시적 리셋으로 stale 참조 해제 + defense-in-depth (F1).
export function resetAnalysisCache() {
  analysisCache = { projectId: null, analysis: null };
}

const isMatchRefresh = (patch) =>
  ['clothingType', 'targetGenders', 'styleTags'].some((k) => k in patch);

function mergeAnalysisResult(ai) {
  const base = defaultAnalysisShape(ai.clothingType || 'top');
  return {
    ...base,
    clothingType: ai.clothingType ?? null,
    subCategory: ai.subCategory ?? null,
    targetGenders: ai.targetGenders ?? [],
    fit: ai.fit ?? null,
    materials: (ai.materials && ai.materials.length)
      ? ai.materials
      : [{ name: '면', ratio: 60 }, { name: '폴리에스터', ratio: 40 }],
    aiSuggestedPoints: ai.aiSuggestedPoints ?? [],
    suggestedName: ai.suggestedName ?? base.suggestedName,
    styleTags: ai.styleTags ?? [],
    swatchSuggestions: ai.swatchSuggestions ?? [],
    sourceMirrored: ai.sourceMirrored === true,
    customCategory: ai.customCategory ?? null,
    sellingPoints: [],
    inputConsistency: ai.inputConsistency ?? null,
  };
}

// match-candidates(실 매칭 아이템) 조회 → [{id,name,gender,thumb,imageUrl,thumbnailUrl,selected:false}].
// clothingType 은 필수 쿼리 — analysis 우선, 없으면 서버 product 에서. gender/styleTags 는 반복 파라미터.
async function fetchMatchCandidates(projectId, analysis) {
  const clothingType = analysis?.clothingType
    || (await http(`/v1/projects/${projectId}/product`))?.clothingType || 'top';
  const qs = new URLSearchParams();
  qs.set('clothingType', clothingType);
  // 성별은 화면의 칩(단일 선택)과 같은 값 하나만 보낸다 — 둘을 보내면 서버 필터가 남녀를 모두
  // 통과시켜 "성별 상관없이 다 뜨는" 증상이 된다. 서버 validate 도 단일화하지만, 이미 저장된
  // 옛 분석(성별 2개)까지 화면과 일치시키려면 조회 시점에도 첫 값만 쓴다 (2026-07-31).
  const gender = (analysis?.targetGenders || [])[0];
  if (gender) qs.append('gender', gender);
  (analysis?.styleTags || []).forEach((t) => qs.append('styleTags', t));
  return http(`/v1/projects/${projectId}/analysis/match-candidates?${qs.toString()}`);
}

// 추천 재계산(로그인·서버 project): 이전 선택을 유효 범위에서 유지, 없으면 상위 N 기본 선택(mock 계약 동일).
async function recommendMatchHttp(projectId, analysis, current, { defaultSelection = true } = {}) {
  const items = await fetchMatchCandidates(projectId, analysis);
  const prev = (current || []).filter((m) => m.selected)
    .sort((a, b) => (a.selOrder || 0) - (b.selOrder || 0)).map((m) => m.id);
  const selectable = items.filter((it) => it.isCompatible !== false);
  const valid = prev.filter((id) => selectable.some((it) => it.id === id)).slice(0, LIMITS.matchClothingMax);
  const fallback = defaultSelection
    ? selectable.filter((item) => item.isCustom !== true).slice(0, LIMITS.matchClothingMax)
    : [];
  const chosen = valid.length ? valid : fallback.map((it) => it.id);
  return items.map((it) => {
    const idx = chosen.indexOf(it.id);
    return toMatchItem(it, idx >= 0 ? idx + 1 : null);
  });
}

// 선택 토글 머지 — id 단위 selected/selOrder 반영 후 1..max 재부여(mock 정규화와 동일 규칙).
function mergeMatchSelection(currentMatch, matchPatch, clothingType) {
  const expectedType = clothingType === 'dress'
    ? null
    : (clothingType === 'bottom' ? 'top' : 'bottom');
  const patchById = new Map(matchPatch.map((m) => [m.id, m]));
  const merged = (currentMatch || []).map((m) => {
    const p = patchById.get(m.id);
    if (!p) return m;
    return { ...m, selected: !!p.selected, selOrder: p.selected ? p.selOrder : undefined };
  });
  const ranked = merged.filter((m) => m.selected
      && m.isCompatible !== false
      && expectedType !== null
      && (m.clothingType == null || m.clothingType === expectedType))
    .sort((a, b) => (a.selOrder || 99) - (b.selOrder || 99)).slice(0, LIMITS.matchClothingMax);
  const orderById = new Map(ranked.map((m, i) => [m.id, i + 1]));
  return merged.map((m) => orderById.has(m.id)
    ? { ...m, selected: true, selOrder: orderById.get(m.id) }
    : { ...m, selected: false, selOrder: undefined });
}

export const httpAdapter = {
  uploadPhoto,
  async uploadDraftSlotPhoto(photo, options) {
    return uploadPhoto(null, { ...photo, purpose: 'draft_slot' }, options);
  },
  async getDraftSlot(token, { full = false } = {}) {
    return http(`/v1/draft-slot${full ? '?full=1' : ''}`, {
      headers: token ? { 'X-Draft-Token': token } : undefined,
    });
  },
  async putDraftSlot(body) {
    return http('/v1/draft-slot', { method: 'PUT', body });
  },
  async takeoverDraftSlot() {
    return http('/v1/draft-slot:takeover', { method: 'POST' });
  },
  async deleteDraftSlot(token) {
    return http('/v1/draft-slot', {
      method: 'DELETE',
      headers: token ? { 'X-Draft-Token': token } : undefined,
    });
  },
  async discardDraftSlotPhoto(assetId) {
    return http(`/v1/draft-slot/assets/${assetId}`, { method: 'DELETE' });
  },
  async publicAnalyze(product, { onProgress, signal } = {}) {
    const colors = product?.colors || [];
    const baseColor = colors.find((color) => color.isBase) || colors[0];
    const photos = selectPublicAnalysisPhotos(baseColor?.images || []);
    if (!photos.length) throw new Error('분석할 상품 사진을 먼저 올려주세요.');
    const form = new FormData();
    onProgress?.(10);
    for (const [index, photo] of photos.entries()) {
      const blob = await fetch(photo.src, { signal }).then((response) => response.blob());
      form.append('images', blob, photo.name || `product-${index + 1}`);
      form.append('slots', photo.slot || (index === 0 ? 'Front' : 'Detail'));
    }
    onProgress?.(30);
    const result = await publicHttp('/v1/public/analyze', form, { signal });
    onProgress?.(100);
    return mergeAnalysisResult(result?.data || {});
  },
  // 상품 사진(blob)을 R2에 업로드하고 images[].id 를 **서버 asset id 로 치환**한다.
  // 서버(mannequin.base_color_images·분석 워커)는 colors[].images[].id 를 asset id 로 링크하므로,
  // 로컬 uid('img') 를 그대로 두면 서버가 사진을 못 찾는다(no_product_images). src 도 R2 URL 로 갱신.
  // 이미 업로드된(blob: 아님) 이미지는 건너뛴다. projectId 없는 공개 흐름은 api/index가 mock에 위임한다.
  async uploadProductPhotos(projectId, product) {
    const colors = await Promise.all((product.colors ?? []).map(async (c) => {
      const images = await Promise.all((c.images ?? []).map(async (im) => {
        if (!im.src || !im.src.startsWith('blob:')) return im;
        const blob = await fetch(im.src).then((r) => r.blob());
        // im.type 은 파일 감지 실패 시 'image'(잘못된 MIME)일 수 있다(filesToMetas 폴백) —
        // '/' 가 있는 진짜 MIME 일 때만 쓰고, 아니면 blob.type / jpeg 로. (upload-url 400 방지)
        const mime = (im.type && im.type.includes('/')) ? im.type : (blob.type || 'image/jpeg');
        const { assetId, url } = await uploadPhoto(projectId, {
          filename: im.name || 'photo', mime, blob,
        });
        return { ...im, id: assetId, src: url };
      }));
      return { ...c, images };
    }));
    return { ...product, colors };
  },
  async saveProduct(projectId, patch) {
    // getProduct·마네킹·콘티·에디터가 모두 http 로 스왑됨(US-2~4) → mock 미러 불필요, 서버가 단일 소스.
    return http(`/v1/projects/${projectId}/product`, { method: 'PATCH', body: patch });
  },
  // AG-01 상품 분석 — POST /analyze(job) → 폴링 → analysis payload.
  // 반환 shape 은 계약 §6 와 동일해야 한다 — AnalysisForm 이 a.models/.matchClothing/.sellingPoints 등을
  // 무가드로 읽으므로. AI 가 산출하지 못하는 필드(models·selectedModelId·측정 구조 등)는 클라 소유
  // 기본 shape(shapes.defaultAnalysisShape)를 베이스로 두고 AI 산출 필드만 덮어써 shape 를 보존한다.
  // (과거엔 mock db.js 의 DB.analysis 를 베이스로 썼으나 mock 결합을 끊고 클라 상수로 대체.)
  async analyzeProduct(projectId, { onProgress } = {}) {
    const { jobId } = await http(`/v1/projects/${projectId}/analyze`, { method: 'POST' });
    // 폴링 상한은 provider 순차 폴백 최악경로(2 × analysis_timeout_seconds=60s = 120s)보다 넉넉히
    // 잡는다 — 짧으면 정상 폴백(gpt→gemini)이 완료 전에 실패 토스트가 뜨는데 job은 뒤늦게 성공한다.
    const result = await pollJob(jobId, {
      onProgress,
      timeoutMs: 180000,
      timeoutMessage: '분석이 지연되고 있어요. 잠시 후 다시 시도해 주세요.',
    });
    const ai = (result && result.data) || {};
    const merged = mergeAnalysisResult(ai);
    // 실측은 AI 미산출 → 기본 shape(defaultAnalysisShape)이 이미 value:null (사용자 직접 입력, PRD §6.5).
    // 매칭 의류 후보 시드 — 서버 matching_items 실 후보(top-N 기본 선택, mock 계약 §6 동일 shape).
    // defaultAnalysisShape 는 matchClothing:[] 라 여기서 채우지 않으면 분석 페이지 매칭 그리드가
    // 비어 보인다(과거 mock base 시절엔 가짜 목이 채워줬음). 실패는 비치명 — 빈 목록 유지.
    try {
      merged.matchClothing = await recommendMatchHttp(projectId, merged, []);
    } catch { /* 후보 조회 실패 — 분석 자체는 진행 */ }
    analysisCache = { projectId, analysis: merged };   // US-4: full-payload 머지 + 매칭 선택 이월 seed(프로젝트 스코프)
    return merged;
  },
  // ---- 상세페이지 (PL-4) — 콘티·에디터는 서버 소유. detail_page job 이 저장 콘티를 읽는다 ----
  async getStoryboard(projectId) {
    const [saved, product, project, analysis] = await Promise.all([
      http(`/v1/projects/${projectId}/storyboard`),
      http(`/v1/projects/${projectId}/product`),
      http(`/v1/projects/${projectId}`),
      http(`/v1/projects/${projectId}/analysis`),
    ]);
    const colors = product?.colors || [];
    const mode = project?.composeMode === 'extended' ? 'extended' : 'basic';
    const storyboardContext = {
      projectId,
      clothingType: product?.clothingType || 'top',
      targetGenders: analysis?.targetGenders || [],
      matchClothing: analysis?.matchClothing || [],
    };
    if (Array.isArray(saved) && saved.length) {
      const previousMode = mode === 'extended' ? 'basic' : 'extended';
      // 이전 모드의 기본 시드 그대로일 때만 사진 양 변경을 반영한다.
      // 사용자가 옵션·순서·레이아웃 하나라도 바꾼 콘티는 교체하지 않는다.
      if (!isDefaultStoryboardForMode(saved, colors, previousMode, storyboardContext)) return saved;
      const seeded = defaultStoryboard(colors, mode, storyboardContext);
      // 첫 화면 스타일 선택은 사진 양을 바꿔도 유지한다 — pair 만 기본 지문에 들어올 수
      // 있고(네컷 프레임은 컷이 늘어 애초에 기본이 아님), 그 외는 시드 기본(시그니처).
      return deriveHookFrame(saved)?.style === 'pair'
        ? applySeededHookStyle(seeded, 'pair', colors)
        : seeded;
    }
    // 첫 진입/재시드는 화면의 자동 예시 배정 뒤 한 번만 PUT한다. 첫 화면 스타일은
    // defaultStoryboard 가 시그니처 컷으로 시드한다(2026-08-14 확정).
    return defaultStoryboard(colors, mode, storyboardContext);
  },
  async saveStoryboard(projectId, blocks, options = {}) {
    return http(`/v1/projects/${projectId}/storyboard`, { method: 'PUT', body: blocks, ...options });
  },
  async getEditorBlocks(projectId) {
    return http(`/v1/projects/${projectId}/editor-blocks`);
  },
  async saveEditorBlocks(projectId, blocks) {
    await http(`/v1/projects/${projectId}/editor-blocks`, { method: 'PUT', body: blocks });
  },
  // AG-06 컷 + AG-02/03 카피 → M-02 조립. 완료 재호출은 서버가 기존 결과 반환(무차감).
  async generateDetailPage(projectId, { onProgress } = {}) {
    const res = await http(`/v1/projects/${projectId}/detail-page:generate`, { method: 'POST' });
    if (res.data) return { data: res.data, credits: res.credits };  // 완료 재호출(202 아님) — 새 잡 없음
    const result = await pollJob(res.jobId, {
      onProgress,
      // 15분. 정상 생성 실측이 242~285초인데 상한이 300초였다 — 여유가 15초뿐이라
      // 조금만 느려도 화면이 먼저 포기했다(2026-08-05 실측). 서버 lease 복구가 900초라
      // 그 사이 죽었다 되살아난 잡까지 화면이 지켜볼 수 있게 같은 값으로 맞춘다.
      timeoutMs: 900000,
      timeoutMessage: '상세페이지 생성이 예상보다 오래 걸리고 있어요. 잠시 후 다시 확인해 주세요.',
    });
    // jobId 를 함께 반환 — 완료 후 정산 영수증(GET /jobs/{jobId}/settlement, payment_id=job:{jobId})을 조회한다.
    return { data: result.data, credits: result.credits, jobId: res.jobId };
  },
  /* ---- 에디터 대기 배관 (editor_wait_dev_spec §3) ----
     generateDetailPage(위)는 시작+완주를 한 호출로 묶는다. 대기 화면은 진행 이벤트를
     같이 소비해야 하므로 시작만 하는 start + 잡/이벤트 폴링을 분리한다. 폴링 주기·수명은
     store(startDetailPageGeneration)가 소유 — 화면을 떠나도 생성 추적이 살아있게. */
  async startDetailPage(projectId) {
    const res = await http(`/v1/projects/${projectId}/detail-page:generate`, { method: 'POST' });
    // 완료 재호출(202 아님) — 새 잡 없이 기존 결과 반환(무차감·멱등)
    if (res.data) return { data: res.data, credits: res.credits };
    return { jobId: res.jobId };
  },
  async getJob(jobId) {
    return http(`/v1/jobs/${jobId}`);
  },
  // ?poll=1 — SSE 대신 1회 JSON(EventSource 는 Bearer 헤더 불가). after = 마지막 이벤트 id 커서.
  async getJobEvents(jobId, after = 0) {
    return http(`/v1/jobs/${jobId}/events?poll=1&after=${after}`);
  },
  // 프로젝트 단건 조회 (계약 §6) — {id,status,title,composeMode,copywriting,
  // selectedMannequinId,adjustCount,createdAt,updatedAt}. projectId 필수:
  // store.loadProject 가 argless 로 부르던 과거 경로(mock 가짜 project 오염 → 404)는
  // useAppStore 에서 제거됐다. 방어적으로 pid 없으면 서버 호출 없이 null.
  async getProject(projectId) {
    if (!projectId) return null;
    return http(`/v1/projects/${projectId}`);
  },
  // 상품 조회 (계약 §3.1) — {id,projectId,name,clothingType,colors[],measurements[],
  // measurementsUnknown,uploadComplete}. colors 는 프론트-소유 JSONB(saveProduct 가 저장한 isBase·images shape).
  // projectId 없는 입력단계는 api/index가 mock seed 템플릿에 위임한다.
  async getProduct(projectId) {
    return http(`/v1/projects/${projectId}/product`);
  },
  // 분석 저장 (계약 §3.2) — 서버 PATCH 는 REPLACE 라 캐시에 delta 를 머지한 full payload 를 보낸다
  // (delta 만 보내면 다른 analysis 필드가 유실). 매칭 추천 갱신·선택 토글을 반영해 반환 matchClothing 을
  // 콜러(AnalysisForm)가 읽는다. projectId 없는 공개 분석은 api/index가 mock에 위임한다.
  async saveAnalysis(projectId, patch) {
    const { matchClothing: matchPatch, ...rest } = patch;
    let cached = cachedAnalysisFor(projectId);
    let serverEmpty = false;   // 하이드레이션이 "서버 저장분 없음"을 증명 — delta PATCH 로 유실될 게 없다
    if (!cached && projectId) {   // 하드 새로고침 후에도 persist 되도록 저장분 1회 하이드레이션(getMatchClothing 동일)
      const saved = await http(`/v1/projects/${projectId}/analysis`);
      if (saved && Object.keys(saved).length > 1) {   // {projectId} 만 있으면 미저장 — 스킵
        analysisCache = { projectId, analysis: saved };
        cached = saved;
      } else {
        serverEmpty = true;
      }
    }
    const base = cached ? { ...cached } : {};
    Object.assign(base, rest);
    if (isMatchRefresh(patch)) {
      base.matchClothing = await recommendMatchHttp(projectId, base, base.matchClothing);
    }
    if (Array.isArray(matchPatch)) {
      base.matchClothing = mergeMatchSelection(
        base.matchClothing || [], matchPatch, base.clothingType,
      );
    }
    // 서버 PATCH 는 REPLACE — full base(analyze 가 seed 한 캐시)일 때만 지속한다. 예외: 하이드레이션이
    // 서버 저장분이 비었음을 증명한 경우(serverEmpty)는 유실될 상위 상태가 없으므로 delta 라도 지속한다
    // — 분석 실패로 빈 프로젝트에 재진입해 모델만 고른 케이스(F3)에서 selectedModelId 무음 유실 방지.
    // 캐시도 없고 하이드레이션도 안 뛴(비정상) 상태만 계속 스킵.
    let savedAnalysis = base;
    if (projectId && (cached || serverEmpty)) {
      savedAnalysis = await http(`/v1/projects/${projectId}/analysis`, {
        method: 'PATCH',
        body: base,
      });
    }
    analysisCache = { projectId, analysis: savedAnalysis };
    return savedAnalysis;
  },
  // 저장된 분석 payload 조회 (계약 §3.2) — 하드 새로고침 후 매칭 선택 등 복원용. {projectId, ...payload}.
  async getAnalysis(projectId) {
    const analysis = normalizeAnalysisFit(await http(`/v1/projects/${projectId}/analysis`));
    const normalized = { ...analysis, matchClothing: normalizeMatchClothingSelection(analysis.matchClothing) };
    analysisCache = { projectId, analysis: normalized };
    return normalized;
  },
  // 세탁 관리법 AI 초안 (동기·무과금) — 서버가 상품 종류·소재로 짧은 문구 생성. bare string 반환(mock 동일).
  // projectId 없으면(비로그인) 서버 project 가 없으니 클라 기본 문구로 폴백.
  async draftWashCare(projectId) {
    if (!projectId) return '찬물 단독 손세탁 권장 · 표백제 사용 금지 · 그늘에 뉘어 건조';
    const res = await http(`/v1/projects/${projectId}/wash-care:draft`, { method: 'POST' });
    return res.text;
  },
  // 특징 포인트 설명 AI 초안 (동기·무과금) — 강조특징마다 한 줄. 상세페이지 생성 잡과 같은 경로라
  // 사전 히트는 즉시, 나머지만 LLM 이 쓴다. 서버가 analysis.featureCopy 에 합쳐 저장한다.
  async draftFeatureCopy(projectId) {
    if (!projectId) return [];
    const res = await http(`/v1/projects/${projectId}/feature-copy:draft`, { method: 'POST' });
    return res.items || [];
  },
  // 매칭 후보 (계약 §6) — 같은 프로젝트의 이월 선택(analysisCache)을 우선. 캐시 미스(하드 새로고침)면
  // GET /analysis 로 저장분을 1회 하이드레이션해 선택 복원. 그래도 없으면 서버 후보 + 상위 N 기본선택.
  async getMatchClothing(projectId) {
    let cached = cachedAnalysisFor(projectId);
    if (!cached && projectId) {
      const saved = await http(`/v1/projects/${projectId}/analysis`);
      if (saved && Object.keys(saved).length > 1) {   // {projectId} 만 있으면 미저장 — 스킵
        cached = { ...saved, matchClothing: normalizeMatchClothingSelection(saved.matchClothing) };
        analysisCache = { projectId, analysis: cached };
      }
    }
    if (cached?.matchClothing?.length) return normalizeMatchClothingSelection(cached.matchClothing);
    if (!projectId) return [];
    const items = await fetchMatchCandidates(projectId, cached);
    const defaultIds = items.filter((it) => it.isCompatible !== false && it.isCustom !== true)
      .slice(0, LIMITS.matchClothingMax).map((it) => it.id);
    return items.map((it) => {
      const index = defaultIds.indexOf(it.id);
      return toMatchItem(it, index >= 0 ? index + 1 : null);
    });
  },
  async addCustomMatchItem(projectId, { assetIds }, { signal } = {}) {
    const result = await http(`/v1/projects/${projectId}/analysis/custom-match-item`, {
      method: 'POST', body: { assetIds }, signal,
    });
    if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
    const analysis = {
      ...result.analysis,
      matchClothing: normalizeMatchClothingSelection(result.analysis?.matchClothing),
    };
    analysisCache = { projectId, analysis };
    return { ...result, analysis };
  },
  async removeCustomMatchItem(projectId) {
    await http(`/v1/projects/${projectId}/analysis/custom-match-item`, { method: 'DELETE' });
    const saved = await http(`/v1/projects/${projectId}/analysis`);
    const analysis = {
      ...saved,
      matchClothing: normalizeMatchClothingSelection(saved.matchClothing),
    };
    analysisCache = { projectId, analysis };
    return { analysis };
  },
  // 이 함수가 새로 아는 건 matchClothing 뿐이다. 왕복 두 번(GET /analysis → match-candidates)
  // 뒤에 착지하므로, 서버 응답을 통째로 캐시 베이스로 쓰면 그 사이 저장된 편집이 캐시에서
  // 사라진다 — 그리고 saveAnalysis 는 이 캐시로 full REPLACE payload 를 만들기 때문에
  // 다음 저장이 서버 값을 옛것으로 되돌린다(2026-08-14 재리뷰 I-A). 베이스는 항상
  // "지금 캐시"(없을 때만 서버 응답)로 잡고, 캐시는 왕복이 끝난 뒤에 읽는다.
  async refreshMatchClothing(projectId) {
    const saved = await http(`/v1/projects/${projectId}/analysis`);
    const matchClothing = await recommendMatchHttp(
      projectId, saved, saved.matchClothing || [], { defaultSelection: false },
    );
    const prev = cachedAnalysisFor(projectId);
    const analysis = { ...(prev || saved), matchClothing };
    analysisCache = { projectId, analysis };
    return analysis;
  },
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
  // ---- 토스 추가구매 (WS3) — 금액은 서버가 정한다. 클라이언트는 planCode 만 보낸다. ----
  // checkout 이 돌려준 orderId/amount 를 그대로 결제창에 넘기고, 승인도 그 값으로만 한다.
  async createTossCheckout(planCode) {
    return http('/v1/payments/toss/checkout', { method: 'POST', body: { planCode } });
  },
  async confirmTossPayment({ paymentKey, orderId, amount }) {
    return http('/v1/payments/toss/confirm', {
      method: 'POST', body: { paymentKey, orderId, amount },
    });
  },
  // ---- 마네킹 (PRD §7) — generate/getMannequins/adjust 는 배포된 라우트로 실배선 ----
  // 마네킹 컷 목록 (계약 §6) — [{id,src,candidate,version,baseFit,fitAdjust,lengthAdjust,matchAdjust}].
  async getMannequins(projectId) {
    if (!projectId) return [];
    return http(`/v1/projects/${projectId}/mannequins`);
  },
  getToneEditor(projectId, cutId) {
    return http(`/v1/projects/${projectId}/mannequins/${encodeURIComponent(cutId)}/tone-editor`);
  },
  toneEditorSource(projectId, cutId, opts) {
    return httpBlob(`/v1/projects/${projectId}/mannequins/${encodeURIComponent(cutId)}/tone-editor/source`, opts);
  },
  toneEditorMask(projectId, cutId, opts) {
    return httpBlob(`/v1/projects/${projectId}/mannequins/${encodeURIComponent(cutId)}/tone-editor/mask`, opts);
  },
  applyToneEditor(projectId, cutId, { assetId, saturation, exposure }) {
    return http(`/v1/projects/${projectId}/mannequins/${encodeURIComponent(cutId)}/tone-editor:apply`, {
      method: 'POST', body: { assetId, saturation, exposure },
    });
  },

  // 최초 A/B 후보 생성 — 202{jobId}→폴링, 또는 완료 존재 시 200{data,credits}(무차감 재호출).
  // 크레딧: mannequinGenerate. 진행 중 재호출은 서버가 활성 job 에 합류(1회만 차감).
  //
  // onJobStarted: **서버가 202 로 답해 실제 job 이 생겼을 때만** 1회 호출한다. 200 캐시 경로에선
  // 부르지 않는다. 두 갈래를 구분할 수 있는 곳은 여기뿐이고(반환 형태 {data,credits} 는 동일하고
  // 폴링이 끝난 뒤라 늦다), 호출부는 이 신호로만 "생성이 시작됐다" 를 판단해야 한다 —
  // 시작하지도 않은 생성을 진행 중이라 알리거나(리본) 최초 생성의 소유권을 주장하면
  // (initialGenerationSession 플래그) 유료 재생성 게이트가 조용히 무력화된다.
  async generateMannequins(projectId, { onProgress, onJobStarted } = {}) {
    const res = await http(`/v1/projects/${projectId}/mannequins:generate`, { method: 'POST' });
    if (res.data) return { data: res.data, credits: res.credits };  // 완료 재호출(200 캐시) — job 없음
    onJobStarted?.(res.jobId);
    // 마네킹 A/B 합성은 무거운 image job — 폴링 상한을 넉넉히(짧으면 정상 job 완료 전 실패 토스트).
    const result = await pollJob(res.jobId, {
      onProgress,
      timeoutMs: LONG_IMAGE_JOB_TIMEOUT_MS,
      timeoutMessage: MANNEQUIN_JOB_TIMEOUT_MESSAGE,
    });
    return { data: result.data, credits: result.credits };
  },
  // 진행 중인 마네킹 생성을 취소한다. 서버가 취소된 작업의 예약 크레딧까지 charged 로
  // 확정한 뒤 돌려준 잔액을 호출부가 즉시 store 에 동기화한다. 활성 job 이 없으면 멱등 200.
  async cancelMannequinGeneration(projectId) {
    return http(`/v1/projects/${projectId}/mannequins:cancel`, { method: 'POST' });
  },
  // @deprecated (2026-07) AG-05 폐기 — fitProfile 재생성(regenerateMannequin)으로 통합.
  // 서버 :adjust 는 항상 410 Gone(잡 미생성). 화면 어디서도 호출하지 않으며 계약 §6 잔재로만 남김.
  async adjustMannequin(projectId, { baseId, fitAdjust, lengthAdjust, matchAdjust, onProgress } = {}) {
    const res = await http(`/v1/projects/${projectId}/mannequins:adjust`, {
      method: 'POST', body: { baseId, fitAdjust, lengthAdjust, matchAdjust },
    });
    if (res.data) return { data: res.data, credits: res.credits };
    const result = await pollJob(res.jobId, {
      onProgress,
      timeoutMs: LONG_IMAGE_JOB_TIMEOUT_MS,
      timeoutMessage: MANNEQUIN_ADJUST_JOB_TIMEOUT_MESSAGE,
    });
    return { data: result.data, credits: result.credits };
  },
  // fit-profile 재생성 — 완료 캐시 없이 매 호출이 새 A/B 버전을 만든다(서버 :regenerate, finalize 가 max(version)+1).
  // 크레딧: mannequinGenerate. generate 미러(202 job → 폴링). 재생성은 캐시 200 경로가 없어 항상 job.
  async regenerateMannequin(projectId, { fitProfile, onProgress, idempotencyKey } = {}) {
    const res = await http(`/v1/projects/${projectId}/mannequins:regenerate`, {
      method: 'POST',
      body: { fitProfile },
      headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
    });
    if (res.data) return { data: res.data, credits: res.credits };
    const result = await pollJob(res.jobId, {
      onProgress,
      timeoutMs: LONG_IMAGE_JOB_TIMEOUT_MS,
      timeoutMessage: MANNEQUIN_JOB_TIMEOUT_MESSAGE,
    });
    // 잡 결과 data 는 "이번에 새로 만든 컷"만(finalize candidates) — 계약(mock cutsEnvelope)은
    // 전체 버전 목록이므로 재조회로 정합한다(버전 스트립이 이전 버전 히스토리를 유지).
    const cuts = await http(`/v1/projects/${projectId}/mannequins`);
    return { data: { cuts }, credits: result.credits };
  },
  // 에디터 Wardrobe(의류 탭, 계약 §3.6) — Record<colorId|'misc', WardrobeImage[]>.
  async getWardrobe(projectId) {
    return http(`/v1/projects/${projectId}/wardrobe`);
  },
  // 콘티 '내 이미지' — 파일 선택 → R2 업로드 → {assetId, url}. 취소 시 null.
  async pickAnyImage(projectId) {
    return pickAndUploadImage(projectId);
  },
  // '내 사진' 무드 레퍼런스 — 파일 선택 → R2 업로드 → {assetId, url}. 취소 시 null.
  // 서버 컷 생성이 assetId 로 이미지를 첨부하므로(refAssetIds), objectURL 이 아니라 업로드가 필수.
  async pickRefImage(projectId) {
    return pickAndUploadImage(projectId);
  },
  // AG-06(mode:'new')/AG-07(mode:'vary') — req = NewCutRequest | VaryRequest (계약 §6).
  // 완료 재호출 없음(매 호출이 새 이미지 생성, mock과 동일 계약) — onProgress는 body에서 제외.
  async generateImage(projectId, req = {}) {
    const { onProgress, ...body } = req;
    const res = await http(`/v1/projects/${projectId}/editor:generate-image`, {
      method: 'POST', body,
    });
    if (res.data) return { data: res.data, credits: res.credits };
    const result = await pollJob(res.jobId, { onProgress });
    return { data: result.data, credits: result.credits };
  },
};
