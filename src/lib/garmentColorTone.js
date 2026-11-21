/* =============================================================
   lib/garmentColorTone — 코디보드 "내 옷 색 맞추기"의 색 연산 garment-color-v1.

   정본: documents/garment_color_target_spec.md §2. 서버(파이썬)가 같은 식으로 목표 색을
   계산하므로 상수나 순서를 바꾸면 server/tests/fixtures/garment_color_tone_js_reference.json
   을 다시 만들고 서버 대조 시험을 함께 고친다.

   모든 연산은 선형광에서 픽셀마다 곱하거나(따뜻함, 밝기) 그 픽셀 자신의 휘도 둘레로 채도를
   조절한다. 그래서 같은 값을 걸어도 빛을 많이 받은 곳은 밝은 채로, 그늘은 그늘인 채로 남는다.
   ============================================================= */

export const GARMENT_TONE_VERSION = 'garment-color-v1';
export const SLIDER_RANGE = 100;
export const SLIDER_SNAP = 3;
export const WARM_END = 0.10;
export const CHROMA_SPAN = 0.40;
export const EXPOSURE_SPAN_EV = 0.50;
export const ZERO_SLIDERS = Object.freeze({ chroma: 0, lightness: 0, warmth: 0 });
export const SLIDER_KEYS = ['chroma', 'lightness', 'warmth'];

const LR = 0.2126, LG = 0.7152, LB = 0.0722;

const SRGB_TO_LINEAR = new Float64Array(256);
for (let i = 0; i < 256; i += 1) {
  const c = i / 255;
  SRGB_TO_LINEAR[i] = c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}
export const srgb8ToLinear = (v) => SRGB_TO_LINEAR[Math.max(0, Math.min(255, Math.round(v)))];
export const linearToSrgb8 = (c) => {
  const v = Math.min(1, Math.max(0, c));
  return (v <= 0.0031308 ? v * 12.92 : 1.055 * v ** (1 / 2.4) - 0.055) * 255;
};

export function clampSlider(value) {
  const v = Math.round(Number(value) || 0);
  if (Math.abs(v) <= SLIDER_SNAP) return 0;
  return Math.max(-SLIDER_RANGE, Math.min(SLIDER_RANGE, v));
}

export function normalizeSliders(sliders) {
  return Object.fromEntries(SLIDER_KEYS.map((key) => [key, clampSlider(sliders?.[key])]));
}

export const isZeroSliders = (sliders) => SLIDER_KEYS.every((key) => !Number(sliders?.[key]));
export const sameSliders = (a, b) => SLIDER_KEYS.every((key) => (Number(a?.[key]) || 0) === (Number(b?.[key]) || 0));

/** 선형 sRGB 한 픽셀에 값 p를 건다. 결과는 자르지 않은 선형값이다(sRGB로 돌릴 때만 0~1로 자른다). */
export function toneLinear(r, g, b, p) {
  const t = ((Number(p?.warmth) || 0) / SLIDER_RANGE) * WARM_END;
  const gr = 1 + t, gb = 1 - t, n = 1 / (LR * gr + LG + LB * gb);
  r *= gr * n; g *= n; b *= gb * n;
  const f = 1 + ((Number(p?.chroma) || 0) / SLIDER_RANGE) * CHROMA_SPAN;
  const y = LR * r + LG * g + LB * b;
  r = y + f * (r - y); g = y + f * (g - y); b = y + f * (b - y);
  const lo = Math.min(r, g, b), hi = Math.max(r, g, b);
  if (lo < 0 || hi > 1) {
    let k = 1;
    if (lo < 0) k = Math.min(k, y / (y - lo));
    if (hi > 1) k = Math.min(k, (1 - y) / (hi - y));
    if (!(k >= 0)) k = 0;
    r = y + (r - y) * k; g = y + (g - y) * k; b = y + (b - y) * k;
  }
  const gain = 2 ** (((Number(p?.lightness) || 0) / SLIDER_RANGE) * EXPOSURE_SPAN_EV);
  return [r * gain, g * gain, b * gain];
}

/** sRGB 8비트 색 하나에 값 p를 건 sRGB 8비트 색(실수). */
export function toneSrgb8(rgb, p) {
  const [r, g, b] = toneLinear(srgb8ToLinear(rgb[0]), srgb8ToLinear(rgb[1]), srgb8ToLinear(rgb[2]), p);
  return [linearToSrgb8(r), linearToSrgb8(g), linearToSrgb8(b)];
}

export function hexOf(rgb) {
  return `#${rgb.map((v) => Math.round(Math.min(255, Math.max(0, v))).toString(16).padStart(2, '0')).join('')}`;
}

const LINEAR_LUT_SIZE = 4096;
const LINEAR_TO_SRGB8 = new Uint8ClampedArray(LINEAR_LUT_SIZE + 1);
for (let i = 0; i <= LINEAR_LUT_SIZE; i += 1) LINEAR_TO_SRGB8[i] = Math.round(linearToSrgb8(i / LINEAR_LUT_SIZE));
const lutSrgb8 = (c) => LINEAR_TO_SRGB8[c <= 0 ? 0 : c >= 1 ? LINEAR_LUT_SIZE : Math.round(c * LINEAR_LUT_SIZE)];

/**
 * 슬라이더를 끌 때마다 다시 칠하는 미리보기. 원본을 한 번 선형광으로 풀어 두고 같은 식(toneLinear)을
 * 픽셀마다 건다. 저장값과의 차이는 표시용 반올림(8비트 1단계 이내)뿐이다.
 */
export function createTonePreview(imageData) {
  const { data, width, height } = imageData;
  const opaque = [];
  for (let i = 0; i < data.length; i += 4) if (data[i + 3]) opaque.push(i);
  const index = Uint32Array.from(opaque);
  const linear = new Float32Array(index.length * 3);
  index.forEach((p, k) => {
    linear[k * 3] = SRGB_TO_LINEAR[data[p]];
    linear[k * 3 + 1] = SRGB_TO_LINEAR[data[p + 1]];
    linear[k * 3 + 2] = SRGB_TO_LINEAR[data[p + 2]];
  });
  return {
    width, height,
    render(sliders, out) {
      out.data.set(data);
      if (isZeroSliders(sliders)) return out;
      for (let k = 0; k < index.length; k += 1) {
        const [r, g, b] = toneLinear(linear[k * 3], linear[k * 3 + 1], linear[k * 3 + 2], sliders);
        const p = index[k];
        out.data[p] = lutSrgb8(r); out.data[p + 1] = lutSrgb8(g); out.data[p + 2] = lutSrgb8(b);
      }
      return out;
    },
  };
}

/** 누끼 PNG의 불투명 픽셀(알파 200 이상) 채널별 중앙값. 서버의 원본 색 S8과 같은 규칙(명세 §3). */
export function opaqueMedianSrgb8(imageData) {
  const hist = [new Uint32Array(256), new Uint32Array(256), new Uint32Array(256)];
  let count = 0;
  const { data } = imageData;
  for (let i = 0; i < data.length; i += 4) {
    if (data[i + 3] < 200) continue;
    hist[0][data[i]] += 1; hist[1][data[i + 1]] += 1; hist[2][data[i + 2]] += 1; count += 1;
  }
  if (!count) return null;
  return hist.map((channel) => {
    const half = (count - 1) / 2;
    let seen = 0, low = null;
    for (let v = 0; v < 256; v += 1) {
      seen += channel[v];
      if (low === null && seen > Math.floor(half)) low = v;
      if (seen > Math.ceil(half)) return (low + v) / 2;
    }
    return low ?? 0;
  });
}

/** 손잡이를 끝에서 끝까지 움직일 때 이 색이 되는 띠(나머지 두 값은 지금 그대로). */
export function sliderTrack(sourceSrgb, sliders, key, stops = [-100, -50, 0, 50, 100]) {
  if (!sourceSrgb) return null;
  return `linear-gradient(90deg, ${stops.map((value) => hexOf(toneSrgb8(sourceSrgb, { ...sliders, [key]: value }))).join(', ')})`;
}
