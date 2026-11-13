// Match the active connection view used by settings. Never fall back to a
// member name, a mall identifier, or a disconnected shop's saved metadata.
export function selectFrameShopName(connections) {
  const items = Array.isArray(connections) ? connections : connections?.items;
  if (!Array.isArray(items)) return null;
  for (const mall of ['cafe24', 'zigzag']) {
    const item = items.find(value => value?.mall === mall && value.status === 'connected'
      && typeof value.shopName === 'string' && value.shopName.trim());
    if (item) return item.shopName.trim();
  }
  return null;
}
