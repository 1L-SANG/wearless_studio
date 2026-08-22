// Cloudflare on-the-fly image resize.
// 기본값: prod 빌드는 on(Vercel env 없이도 동작 — CF Transformations·Cache Rule 배포 완료, prod 검증됨),
// dev 는 off(로컬 API base=localhost 는 CF가 소스를 못 가져와 403). VITE_IMG_RESIZE 로 명시 오버라이드 가능.
const ENV = import.meta.env ?? {};
const CDN = ENV.VITE_IMG_CDN_BASE ?? 'https://images.wearless.kr';
const ON  = (ENV.VITE_IMG_RESIZE ?? (ENV.PROD ? 'on' : 'off')) === 'on';
const API_ASSET_CAPABILITY_RE = /^(?:https?:\/\/[^/]+)?\/v1\/assets\/[^/?#]+\/(?:file|bytes)(?:[?#]|$)/;

// Wrap an absolute asset URL with a Cloudflare /cdn-cgi/image resize transform.
// Only touches absolute http(s) URLs when the flag is on and a CDN base is set; otherwise returns url unchanged.
export function thumbUrlForConfig(
  url,
  width,
  { quality = 80, fit = 'cover' } = {},
  { enabled, cdn },
) {
  if (
    !enabled
    || !cdn
    || !url
    || API_ASSET_CAPABILITY_RE.test(url)
    || !/^https?:\/\//.test(url)
  ) return url;
  return `${cdn}/cdn-cgi/image/width=${width},quality=${quality},format=auto,fit=${fit}/${url}`;
}

export function thumbUrl(url, width, options) {
  return thumbUrlForConfig(url, width, options, { enabled: ON, cdn: CDN });
}
