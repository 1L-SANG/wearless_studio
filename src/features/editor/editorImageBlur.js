// Presence, including zero, is the entire backdrop contract. No second marker
// is stored on editor elements and ordinary photos never acquire a default.
export function hasBackdropBlur(element) {
  return element?.type === 'image' && Number.isFinite(element.blur) && element.blur >= 0 && element.blur <= 100;
}

export function validateEditorImageBlur(blocks) {
  for (const block of blocks || []) for (const element of Array.isArray(block?.elements) ? block.elements : []) {
    if (element && Object.hasOwn(element, 'blur') && !hasBackdropBlur(element)) {
      throw Object.assign(new Error('이미지 흐림은 0부터 100 사이의 숫자여야 해요.'), { status: 400, code: 'editor_blur_invalid' });
    }
  }
}

export function scaledBlurRadius(blur, ratio) {
  return Math.max(0, Number.isFinite(blur) ? blur : 0) * Math.max(0, Number.isFinite(ratio) ? ratio : 1);
}

function boxRadii(sigma) {
  const ideal = Math.sqrt(4 * sigma * sigma + 1);
  let lower = Math.floor(ideal);
  if (lower % 2 === 0) lower--;
  const upper = lower + 2;
  const count = Math.round((12 * sigma * sigma - 3 * lower * lower - 12 * lower - 9) / (-4 * lower - 4));
  return Array.from({ length: 3 }, (_, index) => ((index < count ? lower : upper) - 1) / 2);
}

// Sliding box sums clamp at both edges. Cost is linear in pixels, not radius.
function boxPass(source, target, width, height, radius, vertical) {
  const length = vertical ? height : width;
  const lines = vertical ? width : height;
  const stride = vertical ? width : 1;
  const divisor = radius * 2 + 1;
  for (let line = 0; line < lines; line++) {
    const first = vertical ? line : line * width;
    let sum = source[first] * (radius + 1);
    for (let offset = 1; offset <= radius; offset++) sum += source[first + Math.min(offset, length - 1) * stride];
    for (let position = 0; position < length; position++) {
      target[first + position * stride] = sum / divisor;
      sum += source[first + Math.min(position + radius + 1, length - 1) * stride]
        - source[first + Math.max(position - radius, 0) * stride];
    }
  }
}

// Three box passes approximate a Gaussian without browser filters. Work in
// premultiplied alpha so transparent source RGB never creates a dark fringe.
export function blurPixels(pixels, width, height, sigma) {
  if (!Number.isInteger(width) || !Number.isInteger(height) || width < 1 || height < 1 || pixels.length !== width * height * 4) {
    throw new Error('흐림을 적용할 이미지 크기를 확인하지 못했어요.');
  }
  if (!Number.isFinite(sigma) || sigma <= 0) return new Uint8ClampedArray(pixels);
  const size = width * height;
  const source = new Float32Array(size), scratch = new Float32Array(size), alpha = new Float32Array(size);
  const output = new Uint8ClampedArray(pixels.length);
  const radii = boxRadii(sigma);
  // One channel at a time keeps working buffers at 12 bytes/pixel, instead of
  // two full float RGBA images (32 bytes/pixel).
  for (const channel of [3, 0, 1, 2]) {
    for (let index = 0; index < size; index++) {
      source[index] = channel === 3 ? pixels[index * 4 + 3] : pixels[index * 4 + channel] * pixels[index * 4 + 3] / 255;
    }
    for (const radius of radii) {
      boxPass(source, scratch, width, height, radius, false);
      boxPass(scratch, source, width, height, radius, true);
    }
    if (channel === 3) alpha.set(source);
    for (let index = 0; index < size; index++) {
      output[index * 4 + channel] = channel === 3 ? source[index] : alpha[index] > 0 ? source[index] * 255 / alpha[index] : 0;
    }
  }
  return output;
}

// The center stays exactly the original crop. Only the outer 1px strips and
// corner pixels stretch into the overscan; the final frame clips them away.
export function backdropEdgeTiles(width, height, blur) {
  const pad = Math.ceil(blur * 3) + 1;
  const axes = size => [
    { at: -pad, size: pad, source: 0, span: Math.min(1, size) },
    { at: 0, size, source: 0, span: size },
    { at: size, size: pad, source: Math.max(0, size - 1), span: Math.min(1, size) },
  ];
  return axes(height).flatMap((y, row) => axes(width).map((x, column) => ({
    key: `${row}-${column}`, x: x.at, y: y.at, width: x.size, height: y.size,
    viewBox: `${x.source} ${y.source} ${x.span} ${y.span}`,
  })));
}
