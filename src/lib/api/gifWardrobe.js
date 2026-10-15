import { getUploadValidationError } from '../imageTranscode.js';
import { GIF_HARD_BYTES } from '../gif/gifLadder.js';
import { isGifImage } from '../gif/gifMedia.js';

export async function uploadWardrobeGif(projectId, result, uploadPhoto, options = {}) {
  const { blob, jpgBlob, width, height, frames, delayMs } = result || {};
  if (blob?.size > GIF_HARD_BYTES) throw new Error('GIF는 5MB 이하만 저장할 수 있어요');
  if (blob?.type !== 'image/gif' || jpgBlob?.type !== 'image/jpeg'
    || getUploadValidationError(blob) || getUploadValidationError(jpgBlob)
    || ![width, height, frames, delayMs].every((value) => Number.isFinite(value) && value > 0)) {
    throw new Error('GIF와 첫 컷을 다시 만들어 주세요');
  }
  const gif = await uploadPhoto(projectId, { filename: 'thumbnail.gif', mime: blob.type, blob }, options);
  const poster = await uploadPhoto(projectId, { filename: 'thumbnail.jpg', mime: jpgBlob.type, blob: jpgBlob }, options);
  if (options.signal?.aborted) throw new DOMException('Aborted', 'AbortError');
  return { id: gif.assetId, src: gif.url, posterSrc: poster.url, kind: 'gif',
    gifMeta: { frames, delayMs, width, height, bytes: blob.size }, width, height,
    userUploaded: true, wardrobeGroup: 'misc' };
}

const storageKey = (projectId) => `wearless.editor.gifs.v1:${projectId}`;
const browserStorage = () => typeof localStorage === 'undefined' ? null : localStorage;

export function readWardrobeGifs(projectId, storage = browserStorage()) {
  try {
    const items = JSON.parse(storage?.getItem(storageKey(projectId)) || '[]');
    return Array.isArray(items) ? items.filter(isGifImage) : [];
  } catch { return []; }
}

export function writeWardrobeGifs(projectId, items, storage = browserStorage()) {
  if (!storage) throw new Error('이 브라우저에 GIF 목록을 저장할 수 없어요');
  try { storage.setItem(storageKey(projectId), JSON.stringify(items)); }
  catch { throw new Error('GIF 목록을 저장하지 못했어요. 브라우저 저장 공간을 확인해 주세요'); }
}

export function mergeWardrobeGifs(wardrobe, gifs) {
  if (!gifs.length) return wardrobe;
  const sources = new Set(gifs.map((image) => image.src));
  const next = Object.fromEntries(Object.entries(wardrobe || {}).map(([group, images]) =>
    [group, images.filter((image) => !sources.has(image.src))]));
  next.misc = [...gifs, ...(next.misc || [])];
  return next;
}
