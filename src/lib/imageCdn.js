// Cloudflare on-the-fly image resize. Feature-flagged: off/unset → returns original URL (zero regression).
const CDN = import.meta.env.VITE_IMG_CDN_BASE ?? '';   // e.g. https://images.wearless.kr
const ON  = import.meta.env.VITE_IMG_RESIZE === 'on';

// Wrap an absolute asset URL with a Cloudflare /cdn-cgi/image resize transform.
// Only touches absolute http(s) URLs when the flag is on and a CDN base is set; otherwise returns url unchanged.
export function thumbUrl(url, width, { quality = 80, fit = 'cover' } = {}) {
  if (!ON || !CDN || !url || !/^https?:\/\//.test(url)) return url;
  return `${CDN}/cdn-cgi/image/width=${width},quality=${quality},format=auto,fit=${fit}/${url}`;
}
