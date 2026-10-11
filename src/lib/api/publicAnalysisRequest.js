import { selectPublicAnalysisPhotos } from '../publicAnalysisPhotos.js';
import { fetchPhotoBytes } from '../photoBytes.js';

export function analysisPhotoKey(product) {
  const colors = product?.colors || [];
  const base = colors.find((color) => color.isBase) || colors[0];
  return JSON.stringify(selectPublicAnalysisPhotos(base?.images || []).map((photo) => [photo.slot, photo.id, photo.src]));
}

export async function preparePublicAnalysis(product, { fetchBlob = fetchPhotoBytes, subtle = globalThis.crypto.subtle, signal } = {}) {
  const colors = product?.colors || [];
  const base = colors.find((color) => color.isBase) || colors[0];
  const selected = selectPublicAnalysisPhotos(base?.images || []);
  if (!selected.length) throw new Error('분석할 상품 사진을 먼저 올려주세요.');
  const photos = await Promise.all(selected.map(async (photo, index) => ({ ...photo,
    slot: photo.slot || (index === 0 ? 'Front' : 'Detail'), blob: await fetchBlob(photo.src, { signal }) })));
  const sources = await Promise.all(photos.map(async (photo) => {
    const hash = await subtle.digest('SHA-256', await photo.blob.arrayBuffer());
    return [photo.slot, Array.from(new Uint8Array(hash), (byte) => byte.toString(16).padStart(2, '0')).join('')];
  }));
  const hash = await subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify(sources)));
  return { photos, photoSignature: Array.from(new Uint8Array(hash), (byte) => byte.toString(16).padStart(2, '0')).join('') };
}

export function publicAnalysisForm(product, photos, fields = {}) {
  const form = new FormData();
  for (const [index, photo] of photos.entries()) {
    form.append('images', photo.blob, photo.name || photo.filename || `product-${index + 1}`);
    form.append('slots', photo.slot);
  }
  form.append('productContext', JSON.stringify({ name: product?.name || '', clothingType: product?.clothingType || null,
    colors: (product?.colors || []).map((color) => ({ id: color.id, name: color.name || '', swatchId: color.swatchId || null })) }));
  for (const [key, value] of Object.entries(fields)) if (value != null) form.append(key, String(value));
  return form;
}
