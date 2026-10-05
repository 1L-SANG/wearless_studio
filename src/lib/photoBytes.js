const ASSET_FILE_URL = /^(https?:\/\/[^/?#]+)?(\/v1\/assets\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\/file(\?[^#]*)?(#.*)?$/i;

export function photoBytesUrl(src) {
  return typeof src === 'string'
    ? src.replace(ASSET_FILE_URL, '$1$2/bytes$3$4')
    : src;
}

export function isRemoteAssetPhoto(src) {
  return photoBytesUrl(src) !== src;
}

export async function fetchPhotoBytes(src, options) {
  const response = await fetch(photoBytesUrl(src), options);
  if (!response.ok) throw new Error(`사진을 불러오지 못했어요. (${response.status})`);
  const mime = (response.headers.get('content-type') || '').split(';')[0].trim().toLowerCase();
  if (!/^image\/[^\s/]+$/.test(mime)) throw new Error('사진 응답 형식이 올바르지 않아요.');
  const blob = await response.blob();
  if (!blob.size) throw new Error('사진 응답이 비어 있어요.');
  return blob.type === mime ? blob : blob.slice(0, blob.size, mime);
}
