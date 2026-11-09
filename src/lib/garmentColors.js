import { productSourcePhotos, productSourceColorPatch } from './productSourcePhotos.js';
import { preserveDetailTargetBinding } from './detailRecommendations.js';

/** Color names and swatches describe the option; uploaded photos decide availability. */
export function garmentColorOptions(product, colorOpts = [], block = {}) {
  return (product?.colors || []).map((color, index) => {
    const display = colorOpts.find(option => option.id === color.id);
    const photos = productSourcePhotos(product, color.id, block.shot);
    const direction = block.direction === 'back' ? 'back' : 'front';
    const other = direction === 'back' ? 'front' : 'back';
    const isProduct = block.cutType === 'product';
    const source = isProduct ? photos[direction] || photos[other] : photos.front;
    const sourceDirection = source === photos.back ? 'back' : 'front';
    return {
      id: color.id,
      label: display?.label || color.name?.trim() || `색상 ${index + 1}`,
      hex: display?.hex || color.hex || null,
      source,
      sourceLabel: sourceDirection === 'back' ? '내 뒷면 사진' : '내 앞면 사진',
      disabled: !source,
      reason: source ? '' : isProduct ? '원본 사진 필요' : '정면 사진 필요',
      note: isProduct && source && !photos[direction]
        ? `${direction === 'back' ? '뒷면' : '앞면'} 사진이 없어 ${sourceDirection === 'back' ? '뒷면' : '앞면'}을 사용해요.` : '',
    };
  });
}

/** Persist the requested color only, retaining the existing source-face and detail rules. */
export function garmentColorSelectionPatch(block, product, colorId) {
  const option = garmentColorOptions(product, [], block).find(color => color.id === colorId);
  if (!option || option.disabled) return null;
  const changes = preserveDetailTargetBinding(block, { colorId });
  return { ...changes, ...productSourceColorPatch({ ...block, ...changes }, product, colorId) };
}
