export function isGifImage(image) {
  return Boolean(image?.src && image.kind === 'gif' && image.posterSrc && image.gifMeta);
}

// These fields travel together. Nulls explicitly clear old GIF data on replacement.
export function gifImageFields(image) {
  return isGifImage(image)
    ? { kind: 'gif', posterSrc: image.posterSrc, gifMeta: { ...image.gifMeta } }
    : { kind: null, posterSrc: null, gifMeta: null };
}

export function gifStillPatch(image) {
  return isGifImage(image)
    ? { src: image.posterSrc, kind: null, posterSrc: null, gifMeta: null }
    : {};
}

export function gifSummary(image) {
  const meta = image?.gifMeta || image || {};
  return `${meta.width}×${meta.height} · ${meta.frames}장 · ${((meta.bytes || 0) / 1_000_000).toFixed(1)}MB`;
}

export function collectGifImages(blocks = []) {
  const seen = new Set();
  return blocks.flatMap((block) => (block.elements || []).filter((image) => {
    if (image.type !== 'image' || image.hidden || !isGifImage(image) || seen.has(image.src)) return false;
    seen.add(image.src);
    return true;
  }));
}

export function wardrobePhotoGroups(wardrobe = {}, colors = []) {
  const seen = new Set();
  return Object.entries(wardrobe).flatMap(([id, images]) => {
    const photos = (images || []).filter((image) => {
      if (image.loading || !image.id || !image.src || image.kind === 'gif' || seen.has(image.id)) return false;
      seen.add(image.id);
      return true;
    }).sort((a, b) => Number(b.cutType === 'styling') - Number(a.cutType === 'styling'));
    return photos.length ? [{ id, color: colors.find((color) => color.id === id)?.hex, label: colors.find((color) => color.id === id)?.label || (id === 'misc' ? '기타' : id), photos }] : [];
  });
}
