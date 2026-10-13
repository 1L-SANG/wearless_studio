export const GIF_SOFT_BYTES = 1_500_000;
export const GIF_HARD_BYTES = 4_700_000;
export const GIF_PHOTO_DELAY_MS = 1500;

export const GIF_PRESETS = [
  { id: 'original', label: '원본 비율' },
  { id: 'ably', label: '5:6', width: 640, height: 768 },
  { id: 'cafe24', label: '3:4', width: 800, height: 1067 },
  { id: 'square', label: '1:1', width: 800, height: 800 },
];

export function planLadder(mode) {
  if (mode === 'attachment') return [
    { colors: 256, scale: 1, fps: 10 },
    { colors: 192, scale: 1, fps: 10 },
    { colors: 256, scale: 1, fps: 8 },
    { colors: 128, scale: 1, fps: 8 },
    { colors: 128, scale: 0.8, fps: 8 },
    { colors: 128, scale: 0.8, fps: 6 },
    { colors: 128, scale: 0.64, fps: 8, duration: 2 },
    { colors: 128, scale: 0.64, fps: 6, duration: 2 },
  ];
  if (mode === 'photo') return [
    { colors: 256, scale: 1 },
    { colors: 192, scale: 1 },
    { colors: 256, scale: 0.8 },
    { colors: 128, scale: 0.8 },
    { colors: 128, scale: 0.64 },
  ];
  throw new Error('Unknown GIF mode');
}

export function shouldStopLadder(mode, stepIndex, bytes) {
  const steps = planLadder(mode);
  if (!Number.isInteger(stepIndex) || !steps[stepIndex] || !Number.isFinite(bytes) || bytes < 0) {
    throw new Error('Invalid GIF ladder result');
  }
  if (stepIndex === steps.length - 1 || bytes <= GIF_SOFT_BYTES) return true;
  // After the first two soft attempts, reduce quality only for the hard cap.
  return stepIndex >= 2 && bytes <= GIF_HARD_BYTES;
}

export function presetSize(id, scale = 1, sourceSize) {
  const preset = GIF_PRESETS.find((item) => item.id === id);
  if (!preset || !Number.isFinite(scale) || scale <= 0 || scale > 1) throw new Error('Invalid GIF preset size');
  if (id === 'original') {
    if (![sourceSize?.width, sourceSize?.height].every((v) => Number.isFinite(v) && v > 0)) throw new Error('Invalid GIF source size');
    const height = Math.min(1200, Math.max(2, Math.round(640 * sourceSize.height / sourceSize.width / 2) * 2));
    return { width: Math.round(640 * scale), height: Math.max(2, Math.round(height * scale / 2) * 2) };
  }
  return { width: Math.max(1, Math.round(preset.width * scale)), height: Math.max(1, Math.round(preset.height * scale)) };
}

export function coverCrop(sourceWidth, sourceHeight, width, height, { anchorY = 0.5 } = {}) {
  if (![sourceWidth, sourceHeight, width, height].every((value) => Number.isFinite(value) && value > 0)) {
    throw new Error('Invalid GIF crop size');
  }
  const ratio = Math.max(width / sourceWidth, height / sourceHeight);
  const sw = width / ratio, sh = height / ratio;
  return { sx: (sourceWidth - sw) / 2, sy: (sourceHeight - sh) * Math.max(0, Math.min(1, anchorY)), sw, sh, width, height };
}

export function cropLossRatio(sourceW, sourceH, width, height) {
  const { sw, sh } = coverCrop(sourceW, sourceH, width, height);
  return Math.max(0, Math.min(1, 1 - sw * sh / (sourceW * sourceH)));
}
