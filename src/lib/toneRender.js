/* =============================================================
   lib/toneRender — 마네킹컷의 **의류 픽셀만** 색감·밝기 조정한다.

   프리뷰와 최종 렌더가 **같은 함수**를 쓴다. 미리보기용 근사와 저장용 정식 구현을 따로 두면
   셀러가 본 것과 저장된 것이 달라지고, 그 차이는 슬라이더를 만질 때가 아니라 다운로드한
   뒤에야 드러난다. 다른 건 해상도뿐이다 — 드래그 중엔 화면 크기 버퍼, 적용 시엔 원본 크기.

   마스크 밖은 **원본 바이트 그대로**다. 반올림 오차조차 남기지 않는다: 마네킹·피부·배경이
   1 LSB 라도 움직이면 그건 "옷만 바뀐다"는 약속을 깬 것이다.
   ============================================================= */

// 슬라이더 범위와 내부 파라미터 (계약: 색감 ±100 → ×0.0~2.0, 밝기 ±100 → ∓1.0 EV).
// ±30/±20 · 스팬 0.30 시절엔 체감 차이가 안 났다(QA 2026-08-13) — 포토샵 관례로 맞춘다:
// 색감 -100 = 완전 흑백, +100 = 채도 2배, 밝기 ±100 = 빛의 양 절반~2배.
export const SATURATION_RANGE = 100;
export const EXPOSURE_RANGE = 100;
const SATURATION_SPAN = 1.0;
const EXPOSURE_SPAN_EV = 1.0;

// Rec.709 휘도. 채도를 이 축 둘레로만 움직여야 색상(hue)이 보존된다 — 채널을 직접
// 스케일하면 빨강이 주황으로 돈다.
const LUMA_R = 0.2126, LUMA_G = 0.7152, LUMA_B = 0.0722;

export const clampSaturation = (v) => Math.max(-SATURATION_RANGE, Math.min(SATURATION_RANGE, Math.round(Number(v) || 0)));
export const clampExposure = (v) => Math.max(-EXPOSURE_RANGE, Math.min(EXPOSURE_RANGE, Math.round(Number(v) || 0)));

export function toneParams(saturation, exposure) {
  return {
    factor: 1 + (clampSaturation(saturation) / SATURATION_RANGE) * SATURATION_SPAN,
    ev: (clampExposure(exposure) / EXPOSURE_RANGE) * EXPOSURE_SPAN_EV,
  };
}

export const isNeutral = (saturation, exposure) => clampSaturation(saturation) === 0 && clampExposure(exposure) === 0;

/* sRGB ↔ 선형광. 노출은 빛의 양을 곱하는 연산이라 감마 위에서 곱하면 어두운 쪽이 과하게
   뜬다. 256개 값뿐이므로 순방향은 표로 잡아 픽셀당 pow 를 없앤다. */
const SRGB_TO_LINEAR = new Float64Array(256);
for (let i = 0; i < 256; i += 1) {
  const c = i / 255;
  SRGB_TO_LINEAR[i] = c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}
const linearToSrgb = (c) => (c <= 0.0031308 ? c * 12.92 : 1.055 * c ** (1 / 2.4) - 0.055);

/* 색역 밖으로 밀린 색을 채널 클립으로 막으면 **색상이 돈다**(가장 채도 높은 채널만 잘려
   나가기 때문). 대신 휘도를 유지한 채 채도를 필요한 만큼만 되돌린다 — 밝기는 그대로, 색은
   덜 진해질 뿐 다른 색이 되지 않는다. 계약 §26(색 정체성 보존)이 요구하는 동작이다. */
function gamutSafe(r, g, b, y) {
  const lo = Math.min(r, g, b);
  const hi = Math.max(r, g, b);
  if (lo >= 0 && hi <= 1) return [r, g, b];
  let t = 1;
  if (lo < 0) t = Math.min(t, y / (y - lo));
  if (hi > 1) t = Math.min(t, (1 - y) / (hi - y));
  if (!(t >= 0)) t = 0;
  return [y + (r - y) * t, y + (g - y) * t, y + (b - y) * t];
}

/**
 * 원본 RGBA + 마스크(0..255) → 조정된 RGBA. 원본 버퍼는 건드리지 않는다.
 *
 * @param {Uint8ClampedArray} src   원본 RGBA
 * @param {Uint8Array|Uint8ClampedArray} maskAlpha  픽셀당 0..255 (마스크 PNG 의 밝기)
 * @param {Uint8ClampedArray} out   결과를 쓸 버퍼(길이 동일). src 와 같은 버퍼여도 된다.
 */
export function applyTone(src, maskAlpha, out, saturation, exposure) {
  const { factor, ev } = toneParams(saturation, exposure);
  const gain = 2 ** ev;
  const neutral = factor === 1 && gain === 1;
  const n = maskAlpha.length;

  for (let i = 0, p = 0; i < n; i += 1, p += 4) {
    const a = maskAlpha[i];
    if (a === 0 || neutral) {
      // 마스크 밖 = 원본 그대로. 왕복 변환을 아예 태우지 않는다.
      out[p] = src[p]; out[p + 1] = src[p + 1]; out[p + 2] = src[p + 2]; out[p + 3] = src[p + 3];
      continue;
    }
    const lr = SRGB_TO_LINEAR[src[p]];
    const lg = SRGB_TO_LINEAR[src[p + 1]];
    const lb = SRGB_TO_LINEAR[src[p + 2]];
    const y = LUMA_R * lr + LUMA_G * lg + LUMA_B * lb;

    let r = y + factor * (lr - y);
    let g = y + factor * (lg - y);
    let b = y + factor * (lb - y);
    [r, g, b] = gamutSafe(r, g, b, y);
    r *= gain; g *= gain; b *= gain;

    const sr = linearToSrgb(Math.min(1, Math.max(0, r))) * 255;
    const sg = linearToSrgb(Math.min(1, Math.max(0, g))) * 255;
    const sb = linearToSrgb(Math.min(1, Math.max(0, b))) * 255;

    if (a === 255) {
      // Uint8ClampedArray 는 대입할 때 이미 반올림한다. 여기서 +0.5 를 더하면 이중
      // 반올림이 되어 무채색 픽셀이 128 → 129 로 밀린다(측정된 버그).
      out[p] = sr; out[p + 1] = sg; out[p + 2] = sb;
    } else {
      // 경계의 반투명 구간만 섞는다.
      const w = a / 255, iw = 1 - w;
      out[p] = src[p] * iw + sr * w;
      out[p + 1] = src[p + 1] * iw + sg * w;
      out[p + 2] = src[p + 2] * iw + sb * w;
    }
    out[p + 3] = src[p + 3];
  }
  return out;
}

/** 마스크 이미지의 R 채널만 뽑아 픽셀당 0..255 알파로. (마스크는 회색조 PNG) */
export function maskAlphaFrom(imageData) {
  const { data, width, height } = imageData;
  const alpha = new Uint8Array(width * height);
  for (let i = 0, p = 0; i < alpha.length; i += 1, p += 4) alpha[i] = data[p];
  return alpha;
}
