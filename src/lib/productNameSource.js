const sources = new Set(['seller', 'ai', 'unknown']);
const origin = value => sources.has(value) ? value : 'unknown';

export function sellerProductName(name) {
  return { name, nameSource: String(name || '').trim() ? 'seller' : 'unknown' };
}

export function productNameAfterAnalysis(product, suggestedName) {
  const current = String(product?.name || '').trim();
  if (current && current !== '새 상품') return { name: current, nameSource: origin(product.nameSource) };
  return { name: String(suggestedName || '').trim() || '새 상품', nameSource: 'ai' };
}

// Mirrors the product API boundary: metadata-only saves cannot turn an old AI
// name into a seller claim, and an unmarked changed name remains unknown.
export function productNamePatch(current, patch) {
  const name = Object.hasOwn(patch, 'name') ? patch.name : current.name;
  const nameSource = Object.hasOwn(patch, 'nameSource') ? origin(patch.nameSource)
    : name === current.name ? origin(current.nameSource) : 'unknown';
  return { name, nameSource };
}
