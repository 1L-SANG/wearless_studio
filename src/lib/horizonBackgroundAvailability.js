import policy from '../../server/app/data/horizon_background_policy.json' with { type: 'json' };

export const HORIZON_COLOR_EVIDENCE_UNAVAILABLE = '사진에서 의류색을 충분히 확인하지 못했어요. 기존 배경을 사용해 주세요.';
export const HORIZON_BACKGROUND_UNAVAILABLE = '이 의류색은 배경 톤을 안정적으로 맞추기 어려워 기존 배경을 사용해요.';

export function horizonBackgroundAvailability(members, product, evidence, setId) {
  if (!policy.allowedSetIds?.includes(setId)) return { available: false, reason: '이 세트는 기존 배경을 사용해요.' };
  const unavailable = { available: false, reason: HORIZON_COLOR_EVIDENCE_UNAVAILABLE };
  const horizon = (members || []).filter(block => block.cutType === 'horizon' && block.source !== 'mine');
  if (!horizon.length || evidence?.version !== 1 || !product?.clothingType
    || evidence.clothingType !== product.clothingType || !Array.isArray(evidence.colors)) return unavailable;
  const colors = product.colors || [];
  const base = colors.find(color => color.isBase) || colors[0];
  const measurements = [];
  for (const block of horizon) {
    const id = block.colorId ?? base?.id;
    const measurement = evidence.colors.find(color => String(color.colorId) === String(id));
    if (id == null || !colors.some(color => String(color.id) === String(id))
      || measurement?.status !== 'ready') return unavailable;
    measurements.push(measurement);
  }
  if (measurements.some(color => color.backgroundStatus !== 'ready' || color.backgroundPolicyVersion !== policy.version)) {
    return { available: false, reason: HORIZON_BACKGROUND_UNAVAILABLE };
  }
  return { available: true, reason: null };
}
