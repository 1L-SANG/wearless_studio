import { loadAndDecodeImage } from '../../lib/imagePrewarm.js';

export const INITIAL_REVEAL_TIMEOUT_MS = 2_500;
export const INITIAL_REVEAL_SECTION_LIMIT = 3;
export const INITIAL_REVEAL_IMAGE_LIMIT = 12;
const SECTION_PREVIEW_LIMIT = 3;

/** 접힌 보드가 실제로 그리는 순서대로 첫 화면 썸네일 URL만 모은다. */
export function collectInitialRevealThumbnailUrls(renderedSections, resolveUrl, {
  sectionLimit = INITIAL_REVEAL_SECTION_LIMIT,
  imageLimit = INITIAL_REVEAL_IMAGE_LIMIT,
} = {}) {
  if (!Array.isArray(renderedSections) || typeof resolveUrl !== 'function') return [];

  const urls = [];
  const seen = new Set();
  for (const section of renderedSections.slice(0, sectionLimit)) {
    for (const item of (section?.items || []).slice(0, SECTION_PREVIEW_LIMIT)) {
      const url = resolveUrl(item?.block ?? item);
      if (typeof url !== 'string' || !url || seen.has(url)) continue;
      seen.add(url);
      urls.push(url);
      if (urls.length >= imageLimit) return urls;
    }
  }
  return urls;
}

/** 모든 이미지의 성공/실패 또는 상한 시간 중 먼저 끝나는 쪽에서 reveal을 해제한다. */
export async function waitForInitialReveal(urls, {
  loadImage = loadAndDecodeImage,
  timeoutMs = INITIAL_REVEAL_TIMEOUT_MS,
  setTimeoutFn = setTimeout,
  clearTimeoutFn = clearTimeout,
} = {}) {
  const queue = [...new Set((urls || []).filter((url) => typeof url === 'string' && url))];
  if (!queue.length) return { reason: 'empty', results: [] };

  let timeoutId;
  const settled = Promise.allSettled(queue.map((url) => (
    Promise.resolve().then(() => loadImage(url))
  ))).then((results) => ({ reason: 'settled', results }));
  const timedOut = new Promise((resolve) => {
    timeoutId = setTimeoutFn(() => resolve({ reason: 'timeout', results: [] }), timeoutMs);
  });
  const result = await Promise.race([settled, timedOut]);
  if (result.reason === 'settled') clearTimeoutFn(timeoutId);
  return result;
}
