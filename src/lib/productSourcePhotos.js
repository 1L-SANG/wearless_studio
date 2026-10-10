export function productSourcePhotos(product, colorId, shot = 'ghost') {
  const images = product?.colors?.find((color) => color.id === colorId)?.images || [];
  const first = (slot) => images.find((image) => image.slot === slot && image.src) || null;
  return {
    front: first('Front') || (shot === 'detail' ? first('Detail') : null),
    back: first('Back') || (shot === 'detail' ? first('BackDetail') : null),
  };
}

export function productSourceColorPatch(block, product, colorId) {
  if (block?.cutType !== 'product' || block.detailTargetId) return {};
  const photos = productSourcePhotos(product, colorId, block.shot);
  const direction = block.direction === 'back' ? 'back' : 'front';
  const other = direction === 'back' ? 'front' : 'back';
  return !photos[direction] && photos[other] ? { direction: other } : {};
}
