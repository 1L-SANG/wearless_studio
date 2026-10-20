const ALPHA_THRESHOLD = 8;
const MEASUREMENT_MAX_SIDE = 256;
const MEASUREMENT_TIMEOUT_MS = 1500;
const measurements = new Map();

/** Canvas decodes opaque formats such as JPEG with alpha 255. */
export function alphaBoundsFromPixels(data, width, height) {
  let left = width, top = height, right = -1, bottom = -1;
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    if (data[(y * width + x) * 4 + 3] < ALPHA_THRESHOLD) continue;
    left = Math.min(left, x); top = Math.min(top, y);
    right = Math.max(right, x); bottom = Math.max(bottom, y);
  }
  return right < left ? null : {
    x: left / width, y: top / height, w: (right - left + 1) / width, h: (bottom - top + 1) / height,
  };
}

/** Keep CORS reads out of the long-lived cache used by ordinary display imgs. */
export function imageBoundsRequest(src, baseUrl = globalThis.location?.href || globalThis.document?.baseURI) {
  try {
    const base = new URL(baseUrl), url = new URL(src, base);
    if (['http:', 'https:'].includes(url.protocol) && url.origin !== base.origin) {
      url.search += `${url.search ? '&' : ''}bbox=1`;
      return { src: url.href, crossOrigin: 'anonymous' };
    }
  } catch { /* Invalid addresses fail quietly in the image loader. */ }
  return { src };
}

export function imageBoundsAspectRatio(measurement, fallback) {
  return measurement ? measurement.width * measurement.bounds.w / (measurement.height * measurement.bounds.h) : fallback;
}

export function croppedImageStyle(bounds) {
  if (!bounds) return undefined;
  return { width: `${100 / bounds.w}%`, height: `${100 / bounds.h}%`,
    left: `${-100 * bounds.x / bounds.w}%`, top: `${-100 * bounds.y / bounds.h}%` };
}

/** undefined means pending/unrequested, null means permanently use contain. */
export function getOutfitImageBounds(src) {
  return measurements.get(src)?.result;
}

/** Cache the pending promise too: a shoe's two copies still make one read. */
export function loadOutfitImageBounds(src) {
  if (measurements.has(src)) return measurements.get(src).promise;
  const entry = { result: undefined };
  entry.promise = new Promise((resolve) => {
    let img, timer, settled = false;
    const finish = (result) => {
      if (settled) return;
      settled = true;
      entry.result = result;
      clearTimeout(timer);
      if (img) { img.onload = null; img.onerror = null; }
      resolve(result);
    };
    timer = setTimeout(() => finish(null), MEASUREMENT_TIMEOUT_MS);
    try {
      img = new Image();
      img.onerror = () => finish(null);
      img.onload = async () => {
        try {
          await img.decode?.();
          if (settled) return;
          const width = img.naturalWidth, height = img.naturalHeight;
          if (!(width > 0 && height > 0)) return finish(null);
          const scale = Math.min(1, MEASUREMENT_MAX_SIDE / Math.max(width, height));
          const canvas = document.createElement('canvas');
          canvas.width = Math.max(1, Math.round(width * scale));
          canvas.height = Math.max(1, Math.round(height * scale));
          const context = canvas.getContext('2d', { willReadFrequently: true });
          context.drawImage(img, 0, 0, canvas.width, canvas.height);
          const bounds = alphaBoundsFromPixels(context.getImageData(0, 0, canvas.width, canvas.height).data, canvas.width, canvas.height);
          finish(bounds ? { width, height, bounds } : null);
        } catch { finish(null); }
      };
      const request = imageBoundsRequest(src);
      if (request.crossOrigin) img.crossOrigin = request.crossOrigin;
      img.src = request.src;
    } catch { finish(null); }
  });
  measurements.set(src, entry);
  return entry.promise;
}
