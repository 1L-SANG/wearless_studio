import { selectPublicAnalysisPhotos } from './publicAnalysisPhotos.js';

export function photoCollectionFailure(product, { failed = 0, failedIds = [], failures = [] } = {}) {
  if (!failed) return null;
  const colors = product?.colors || [];
  const base = colors.find((color) => color.isBase) || colors[0];
  const sources = new Set(selectPublicAnalysisPhotos(base?.images).map((image) => image.id));
  return {
    failed,
    failedIds,
    ...(failures.find((failure) => failure.error)?.error ? { error: failures.find((failure) => failure.error).error } : {}),
    removed: failures.length === failed && failures.every((failure) => failure.status === 404),
    canOmit: failedIds.length === failed && !failedIds.some((id) => sources.has(id)),
  };
}

// 제외 동의도 다시 수집한다. 새 실패가 생기면 칸 삭제와 승격 전에 다시 멈춘다.
export async function confirmDraftPhotos({ product, omitIds = [], onProductChange, save, onFailure, confirm }) {
  if (omitIds.length) {
    const failure = photoCollectionFailure(product, { failed: omitIds.length, failedIds: omitIds });
    if (!failure.canOmit) {
      onFailure(failure);
      return;
    }
    const omitted = new Set(omitIds);
    product = {
      ...product,
      colors: (product.colors || []).map((color) => ({
        ...color,
        images: (color.images || []).filter((image) => !omitted.has(image.id)),
      })),
    };
    onProductChange(product);
  }
  const failure = photoCollectionFailure(product, await save(product));
  if (failure) {
    onFailure(failure);
    return;
  }
  return confirm();
}
