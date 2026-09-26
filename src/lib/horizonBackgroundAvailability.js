import policy from '../../server/app/data/horizon_background_policy.json' with { type: 'json' };
import { storyboardSpaceSetById } from './storyboardSpaceSetCatalog.js';
import { horizonBackgroundMode, updateHorizonGroupBackground } from './horizonBackground.js';

export const HORIZON_COLOR_EVIDENCE_UNAVAILABLE = '사진에서 옷 색을 확인하지 못해 기존 배경을 사용해요.';
export const HORIZON_BACKGROUND_UNAVAILABLE = '이 의류색은 배경 톤을 안정적으로 맞추기 어려워 기존 배경을 사용해요.';
export const HORIZON_COLOR_PHOTO_MISSING = '이 색상은 앞면이나 뒷면 사진이 없어 기존 배경을 사용해요.';

// 서버는 앞면이나 뒷면 사진이 있는 색만 잰다(슬롯 없는 사진은 앞면으로 본다, cut_generator.color_images).
// 사진이 없는 색은 측정 결과가 영영 생기지 않으니 '대기'가 아니라 바로 '불가'로 알린다.
const hasMeasurablePhoto = color => (color?.images || []).some(image => !image?.slot || image.slot === 'Front' || image.slot === 'Back');

// 옷 색 측정은 콘티보드를 떠날 때 필요한 색만 한다. 그래서 측정 전(근거 없음, 다른 옷 종류로 잰 근거,
// 행이 없는 색, 정책 버전 차이)은 'pending'으로 선택을 허용하고, 실제로 잰 결과가 실패일 때만 'unavailable'.
export function horizonBackgroundAvailability(members, product, evidence, setId) {
  const set = storyboardSpaceSetById(setId);
  const horizon = (members || []).filter(block => block.cutType === 'horizon' && block.source !== 'mine');
  if (!set?.setType.startsWith('horizon') || !horizon.length) {
    return { state: 'unavailable', available: false, reason: '이 세트는 기존 배경을 사용해요.' };
  }
  const pending = { state: 'pending', available: true, reason: null };
  const colors = product?.colors || [];
  const base = colors.find(color => color.isBase) || colors[0];
  // 상품을 아직 못 읽었으면 판단을 미룬다. 섣불리 '불가'로 보면 저장된 선택이 기존 배경으로 되돌려진다.
  if (!colors.length) return pending;
  if (horizon.some(block => !hasMeasurablePhoto(colors.find(color => String(color.id) === String(block.colorId ?? base?.id))))) {
    return { state: 'unavailable', available: false, reason: HORIZON_COLOR_PHOTO_MISSING };
  }
  if (evidence?.version !== 1 || !product?.clothingType
    || evidence.clothingType !== product.clothingType || !Array.isArray(evidence.colors)) return pending;
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
