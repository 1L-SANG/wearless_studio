import { GIF_PRESETS, cropLossInfo, cropLossRatio, presetSize } from './gifLadder.js';

export function recommendCropPreset(sizes, current) {
  // Wait for all dimensions so a partial selection cannot skew the recommendation.
  if (!sizes.length || sizes.some(size => !size)) return current;
  const loss = id => {
    const target = presetSize(id, 1, sizes[0]);
    return sizes.reduce((sum, size) => sum + cropLossRatio(size.width, size.height, target.width, target.height), 0);
  };
  let best = current, minimum = loss(current);
  for (const option of GIF_PRESETS) {
    const total = loss(option.id);
    if (total < minimum - 1e-10) { best = option.id; minimum = total; }
  }
  return best;
}

export function cropWarningText(sizes, preset) {
  if (!sizes.length || (preset === 'original' && !sizes[0])) return '';
  const target = presetSize(preset, 1, sizes[0]);
  const reasons = new Map();
  sizes.forEach((size, index) => {
    if (!size) return;
    const { ratio, axis } = cropLossInfo(size.width, size.height, target.width, target.height);
    if (ratio <= 0.25) return;
    const reason = axis === 'y' ? '틀보다 세로가 길어 위아래가 잘려요'
      : size.width > size.height && target.width < target.height
        ? '가로 사진이라 세로 틀에 맞추면 양옆이 잘려요'
        : '틀보다 가로가 넓어 양옆이 잘려요';
    reasons.set(reason, [...(reasons.get(reason) || []), `${index + 1}번`]);
  });
  if (!reasons.size) return '';
  const explanation = [...reasons].map(([reason, numbers]) => `${numbers.join(', ')}은 ${reason}.`).join(' ');
  const recommended = recommendCropPreset(sizes, preset);
  if (recommended === preset) return `${explanation} 빼 주세요.`;
  const label = GIF_PRESETS.find(option => option.id === recommended).label;
  return `${explanation} 빼거나, 규격을 ${label}${recommended === 'ably' ? '으로' : '로'} 바꿔 보세요.`;
}
