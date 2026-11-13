const DEFAULT_SOURCE_ASPECT = 3 / 4;
const CROP_ASPECT = 4 / 5;
const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
const positive = value => Number.isFinite(value) && value > 0;
const knownAspect = image => image?.dimensionsKnown !== false && positive(image?.width) && positive(image?.height)
  ? image.width / image.height : null;

// Source dimensions are transient input, never new storyboard fields. An AI
// wear cut has no relationship to the product Front photo's pixel dimensions.
export function wornCropSourceAspect(source, { cutResults = [], product = {} } = {}) {
  const ownUrl = source?.source === 'mine' ? source.ownImages?.[0] : null;
  const cut = cutResults.find(result => result.blockId === source?.id
    && (!ownUrl || result.imageUrl === ownUrl) && knownAspect(result));
  if (cut) return knownAspect(cut);
  if (ownUrl) {
    const image = (product.colors || []).flatMap(color => color.images || [])
      .find(image => image.src === ownUrl && knownAspect(image));
    if (image) return knownAspect(image);
  }
  return DEFAULT_SOURCE_ASPECT;
}

// Keep the subject box's width and center, extending/cropping vertically to
// make its pixel ratio 4:5. Very wide sources shrink the box only as required
// to fit the original. Clamping never changes the result's aspect ratio.
export function wornCropRegion(region, sourceAspect = DEFAULT_SOURCE_ASPECT) {
  const aspect = positive(sourceAspect) ? sourceAspect : DEFAULT_SOURCE_ASPECT;
  const [x, y, width, height] = Array.isArray(region) ? region : [];
  const sourceWidth = positive(width) ? width : .3;
  const sourceHeight = positive(height) ? height : .3;
  const centerX = Number.isFinite(x) ? x + sourceWidth / 2 : .5;
  const centerY = Number.isFinite(y) ? y + sourceHeight / 2 : .5;
  const w = Math.min(sourceWidth, 1, CROP_ASPECT / aspect);
  const h = w * aspect / CROP_ASPECT;
  return [clamp(centerX - w / 2, 0, 1 - w), clamp(centerY - h / 2, 0, 1 - h), w, h];
}

export function sameWornCropSourceImage(before, after) {
  if (!before) return true; // A restored snapshot already owns its saved ROI.
  return before.source === after.source
    && (after.source !== 'mine' || before.ownImages?.[0] === after.ownImages?.[0]);
}
