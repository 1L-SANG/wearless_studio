import policy from '../../server/app/data/horizon_background_policy.json' with { type: 'json' };
import { storyboardSpaceSetById } from './storyboardSpaceSetCatalog.js';
import { horizonBackgroundMode, updateHorizonGroupBackground } from './horizonBackground.js';

export const HORIZON_COLOR_EVIDENCE_UNAVAILABLE = '사진에서 옷 색을 확인하지 못해 기존 배경을 사용해요.';
export const HORIZON_BACKGROUND_UNAVAILABLE = '이 의류색은 배경 톤을 안정적으로 맞추기 어려워 기존 배경을 사용해요.';

// 옷 색 측정은 콘티보드를 떠날 때 필요한 색만 한다. 그래서 측정 전(근거 없음, 다른 옷 종류로 잰 근거,
// 행이 없는 색, 정책 버전 차이)은 'pending'으로 선택을 허용하고, 실제로 잰 결과가 실패일 때만 'unavailable'.
export function horizonBackgroundAvailability(members, product, evidence, setId) {
  const set = storyboardSpaceSetById(setId);
  const horizon = (members || []).filter(block => block.cutType === 'horizon' && block.source !== 'mine');
  if (!set?.setType.startsWith('horizon') || !horizon.length) {
    return { state: 'unavailable', available: false, reason: '이 세트는 기존 배경을 사용해요.' };
  }
  const pending = { state: 'pending', available: true, reason: null };
  if (evidence?.version !== 1 || !product?.clothingType
    || evidence.clothingType !== product.clothingType || !Array.isArray(evidence.colors)) return pending;
  const colors = product.colors || [];
  const base = colors.find(color => color.isBase) || colors[0];
  const measurements = [];
  for (const block of horizon) {
    const id = block.colorId ?? base?.id;
    const measurement = evidence.colors.find(color => String(color.colorId) === String(id));
    if (!measurement || measurement.backgroundPolicyVersion !== policy.version) return pending;
    measurements.push(measurement);
  }
  if (measurements.some(color => color.status !== 'ready')) {
    return { state: 'unavailable', available: false, reason: HORIZON_COLOR_EVIDENCE_UNAVAILABLE };
  }
  if (measurements.some(color => color.backgroundStatus !== 'ready')) {
    return { state: 'unavailable', available: false, reason: HORIZON_BACKGROUND_UNAVAILABLE };
  }
  return { state: 'ready', available: true, reason: null };
}

export function effectiveHorizonBackgroundMode(block, availability) {
  return horizonBackgroundMode(block) === 'garment-tone'
    && (availability?.state === 'pending' || availability?.state === 'ready')
    ? 'garment-tone' : 'reference';
}

export function reconcileHorizonGroupBackground(blocks, groupId, product, evidence, setId) {
  const members = (blocks || []).filter(block => block.spaceGroupId === groupId);
  return horizonBackgroundAvailability(members, product, evidence, setId).state === 'unavailable'
    ? updateHorizonGroupBackground(blocks, groupId, 'reference') : blocks;
}
