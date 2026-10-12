export const GIF_SOFT_BYTES = 1_500_000;
export const GIF_HARD_BYTES = 4_700_000;

export const GIF_PRESETS = [
  { id: 'ably', label: '에이블리 5:6', width: 640, height: 768 },
  { id: 'cafe24', label: '카페24 3:4', width: 800, height: 1067 },
  { id: 'square', label: '정사각 1:1', width: 800, height: 800 },
];

export function planLadder(mode) {
  if (mode === 'photo') return [
    { colors: 256, scale: 1 },
    { colors: 192, scale: 1 },
    { colors: 128, scale: 1 },
    { colors: 256, scale: 0.8 },
    { colors: 128, scale: 0.8 },
    { colors: 128, scale: 0.64 },
  ];
  if (mode === 'video') return [
    { colors: 256, scale: 1, fps: 10 },
    { colors: 256, scale: 1, fps: 8 },
    { colors: 128, scale: 1, fps: 8 },
    { colors: 128, scale: 1, fps: 6 },
    { colors: 128, scale: 0.8, fps: 8 },
    { colors: 128, scale: 1, fps: 8, duration: 2 },
  ];
  throw new Error('Unknown GIF mode');
}

export function shouldStopLadder(mode, stepIndex, bytes) {
  const steps = planLadder(mode);
  if (!Number.isInteger(stepIndex) || !steps[stepIndex] || !Number.isFinite(bytes) || bytes < 0) {
    throw new Error('Invalid GIF ladder result');
  }
  if (stepIndex === steps.length - 1 || bytes <= GIF_SOFT_BYTES) return true;
  // Lower resolution (or a shorter video) is only needed for the hard cap.
  return stepIndex >= (mode === 'photo' ? 2 : 3) && bytes <= GIF_HARD_BYTES;
}

export function presetSize(id, scale = 1) {
  const preset = GIF_PRESETS.find((item) => item.id === id);
  if (!preset || !Number.isFinite(scale) || scale <= 0 || scale > 1) throw new Error('Invalid GIF preset size');
  return { width: Math.max(1, Math.round(preset.width * scale)), height: Math.max(1, Math.round(preset.height * scale)) };
}

export function coverCrop(sourceWidth, sourceHeight, width, height) {
  if (![sourceWidth, sourceHeight, width, height].every((value) => Number.isFinite(value) && value > 0)) {
    throw new Error('Invalid GIF crop size');
  }
  const ratio = Math.max(width / sourceWidth, height / sourceHeight);
  const sw = width / ratio, sh = height / ratio;
  return { sx: (sourceWidth - sw) / 2, sy: (sourceHeight - sh) / 2, sw, sh, width, height };
}
