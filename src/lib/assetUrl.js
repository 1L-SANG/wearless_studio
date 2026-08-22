/* =============================================================
   lib/assetUrl — 에셋 URL 호스트 정규화(공용).

   서버는 에셋을 안정 앱 URL `/v1/assets/{id}/file`(상대경로)로 준다. 프론트는 다른
   도메인(Vercel)에서 서빙되므로 <img src> 로 쓰려면 API 도메인 프리픽스가 필요하다.

   문제: 옛 로컬 빌드(VITE_API_BASE_URL=http://localhost:8081 등)가 절대화한 URL 을
   editor_blocks·storyboard·`ew-draft-*`(에디터 편집 버퍼) 에 그대로 저장하면, 이후
   올바른 빌드에서도 그 절대 URL(잘못된 호스트)이 그대로 남아 이미지 CDN 이 403 을 낸다.

   - rebaseAssetUrls: 상대경로든 잘못된 호스트의 절대 URL 이든 현재 BASE_URL 로 재기준화(읽기 자가치유).
   - relativizeAssetUrls: 저장 직전 호스트를 벗겨 경로만 남긴다(재발 차단 — 저장소에 호스트를 안 박음).

   `/v1/assets/` 는 언제나 API 만 서빙하므로 호스트 교체는 안전하다. R2 공개 URL·
   personalization·blob 등 비에셋 문자열은 건드리지 않는다.
   ============================================================= */

const BASE_URL = import.meta.env?.VITE_API_BASE_URL ?? '';
export const ASSET_CACHE_VERSION = '2';

// `http(s)://<host>/v1/assets/...` → 캡처그룹 1 = `/v1/assets/...`
const ASSET_HOST_RE = /^https?:\/\/[^/]+(\/v1\/assets\/.*)$/;
const ASSET_CAPABILITY_RE = /^(.*\/v1\/assets\/[^/?#]+\/(?:file|bytes))(\?[^#]*)?(#.*)?$/;

/** 현재 클라이언트가 과거 immutable capability cache를 재사용하지 않도록 버전을 올린다. */
export function versionAssetCapabilityUrl(value) {
  const match = ASSET_CAPABILITY_RE.exec(value);
  if (!match) return value;
  const params = new URLSearchParams((match[2] || '').slice(1));
  params.set('e', ASSET_CACHE_VERSION);
  return `${match[1]}?${params.toString()}${match[3] || ''}`;
}

function rebaseOne(s) {
  if (s.startsWith('/v1/assets/')) return versionAssetCapabilityUrl(`${BASE_URL}${s}`);
  const m = ASSET_HOST_RE.exec(s);
  return m ? versionAssetCapabilityUrl(`${BASE_URL}${m[1]}`) : s;
}

function relativizeOne(s) {
  const m = ASSET_HOST_RE.exec(s);
  return versionAssetCapabilityUrl(m ? m[1] : s);
}

function deepMap(v, fn) {
  if (typeof v === 'string') return fn(v);
  if (Array.isArray(v)) return v.map((x) => deepMap(x, fn));
  if (v && typeof v === 'object') {
    const out = {};
    for (const k of Object.keys(v)) out[k] = deepMap(v[k], fn);
    return out;
  }
  return v;
}

/** 에셋 URL(상대경로 또는 잘못된 호스트의 절대 URL)을 현재 BASE_URL 로 재기준화. */
export const rebaseAssetUrls = (v) => deepMap(v, rebaseOne);

/** 새 클라이언트가 직접 만드는 file capability도 공용 버전 계약을 통과시킨다. */
export const assetFileUrl = (assetId) => rebaseOne(`/v1/assets/${assetId}/file`);

/** 에셋 URL 의 호스트를 벗겨 상대경로로 되돌린다(저장 전 정규화). */
export const relativizeAssetUrls = (v) => deepMap(v, relativizeOne);
