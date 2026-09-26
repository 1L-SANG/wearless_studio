import policy from '../../server/app/data/horizon_background_policy.json' with { type: 'json' };
import { storyboardSpaceSetById } from './storyboardSpaceSetCatalog.js';
import { horizonBackgroundMode, updateHorizonGroupBackground } from './horizonBackground.js';

export const HORIZON_COLOR_EVIDENCE_UNAVAILABLE = '의류색 분석 결과가 없어 기존 배경을 사용해요.';
export const HORIZON_BACKGROUND_UNAVAILABLE = '이 의류색은 배경 톤을 안정적으로 맞추기 어려워 기존 배경을 사용해요.';

export function horizonBackgroundAvailability(members, product, evidence, setId) {
  const set = storyboardSpaceSetById(setId);
  if (!set?.setType.startsWith('horizon')) return { available: false, reason: '이 세트는 기존 배경을 사용해요.' };
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

export function effectiveHorizonBackgroundMode(block, availability) {
  return horizonBackgroundMode(block) === 'garment-tone' && availability?.available === true
    ? 'garment-tone' : 'reference';
}

export function reconcileHorizonGroupBackground(blocks, groupId, product, evidence, setId) {
  const members = (blocks || []).filter(block => block.spaceGroupId === groupId);
  return horizonBackgroundAvailability(members, product, evidence, setId).available
    ? blocks : updateHorizonGroupBackground(blocks, groupId, 'reference');
}
