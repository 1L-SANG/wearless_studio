function isBoostColor(r, g, b) {
  return (r > 95 && g > 40 && b > 20 && r > g && r > b && Math.abs(r - g) > 15 && r - b > 15)
    || (r > 110 && r - g > 45 && r - b > 35 && g < 140);
}

export function colorBoost(rgba) {
  let extra = 0;
  for (let at = 0; at < rgba.length; at += 4) {
    if (isBoostColor(rgba[at], rgba[at + 1], rgba[at + 2])) extra += 12;
  }
  const sample = new Uint8Array(rgba.length + extra);
  sample.set(rgba);
  let end = rgba.length;
  for (let at = 0; at < rgba.length; at += 4) {
    if (!isBoostColor(rgba[at], rgba[at + 1], rgba[at + 2])) continue;
    for (let copy = 0; copy < 3; copy += 1) {
      sample.set(rgba.subarray(at, at + 4), end);
      end += 4;
    }
  }
  return sample;
}

export function ditherFrame(rgba, width, height, palette) {
  if (rgba.length !== width * height * 4 || !palette.length || palette.length > 256) {
    throw new Error('Invalid GIF palette frame');
  }
  const indexed = new Uint8Array(width * height);
  // Cache nearest colors in the same rgb565 space as the quantizer.
  const lookup = new Uint8Array(65536), seen = new Uint8Array(65536);
  let row = new Float32Array((width + 2) * 3), next = new Float32Array(row.length);
  const clamp = (value) => Math.max(0, Math.min(255, value));
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const pixel = y * width + x, at = pixel * 4, pos = (x + 1) * 3;
      const rgb = [clamp(rgba[at] + row[pos]), clamp(rgba[at + 1] + row[pos + 1]), clamp(rgba[at + 2] + row[pos + 2])];
      const key = ((rgb[0] >> 3) << 11) | ((rgb[1] >> 2) << 5) | (rgb[2] >> 3);
      if (!seen[key]) {
        const r = (key >> 11) * 255 / 31, g = ((key >> 5) & 63) * 255 / 63, b = (key & 31) * 255 / 31;
        let best = Infinity;
        for (let i = 0; i < palette.length; i += 1) {
          const color = palette[i];
          const distance = (r - color[0]) ** 2 + (g - color[1]) ** 2 + (b - color[2]) ** 2;
          if (distance < best) { best = distance; lookup[key] = i; }
        }
        seen[key] = 1;
      }
      const index = lookup[key];
      indexed[pixel] = index;
      for (let ch = 0; ch < 3; ch += 1) {
        const error = rgb[ch] - palette[index][ch];
        row[pos + 3 + ch] += error * 7 / 16;
        next[pos - 3 + ch] += error * 3 / 16;
        next[pos + ch] += error * 5 / 16;
        next[pos + 3 + ch] += error / 16;
      }
    }
    [row, next] = [next, row];
    next.fill(0);
  }
  return indexed;
}
