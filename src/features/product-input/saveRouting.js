import { invalidateStoryboardEntryPrefetch } from '../storyboard/storyboardEntryPrefetch.js';
import { isPlaceholderPhotoSrc } from '../../lib/imageTranscode.js';

const analysisEditSaveBarriers = new Map();

// 라우트 이탈 cleanup은 저장을 시작할 수는 있어도 await할 수 없다. 같은 탭에서 바로 콘티가
// 열리면 그 프로젝트의 마지막 분석 저장만 기다리게 해 PATCH보다 GET이 먼저 끝나는 레이스를 막는다.
export function registerAnalysisEditSave(projectId, promise) {
  if (!projectId || !promise?.then) return promise;
  let tracked;
  tracked = Promise.resolve(promise)
    .catch(() => {})
    .finally(() => {
      if (analysisEditSaveBarriers.get(projectId) === tracked) {
        analysisEditSaveBarriers.delete(projectId);
      }
    });
  analysisEditSaveBarriers.set(projectId, tracked);
  return promise;
}

export async function waitForAnalysisEditSave(projectId) {
  const pending = projectId ? analysisEditSaveBarriers.get(projectId) : null;
  if (pending) await pending;
}

// 콘티 시드(httpAdapter.getStoryboard → shapes.defaultStoryboard)가 실제로 읽는 필드.
// 이 중 하나라도 이 화면에서 다시 저장되면, 이미 데워둔 콘티 프리페치는 스테일해진다.
const STORYBOARD_SEED_PATCH_KEYS = new Set(['colors', 'clothingType', 'targetGenders', 'matchClothing']);

const PRODUCT_PATCH_KEYS = new Set([
  'name',
  'clothingType',
  'colors',
  'measurements',
  'measurementsUnknown',
  'uploadComplete',
]);

const PRODUCT_PATCH_NON_NULL_KEYS = new Set([
  'name',
  'colors',
  'measurements',
  'measurementsUnknown',
  'uploadComplete',
]);

export function splitAnalysisEditPatch(patch) {
  const productPatch = {};
  const analysisPatch = {};
  Object.entries(patch || {}).forEach(([key, value]) => {
    if (PRODUCT_PATCH_KEYS.has(key)) {
      if (!(PRODUCT_PATCH_NON_NULL_KEYS.has(key) && value == null)) {
        productPatch[key] = value;
      }
      return;
    }
    analysisPatch[key] = value;
  });
  return { productPatch, analysisPatch };
}

export function hasPatchFields(patch) {
  return !!patch && Object.keys(patch).length > 0;
}

export function mergeProductOwnedAnalysisFields(analysis, product) {
  if (!analysis) return analysis;
  return {
    ...analysis,
    clothingType: product?.clothingType ?? analysis.clothingType ?? null,
    measurements: Array.isArray(product?.measurements) ? product.measurements : (analysis.measurements || []),
    measurementsUnknown: typeof product?.measurementsUnknown === 'boolean'
      ? product.measurementsUnknown
      : !!analysis.measurementsUnknown,
  };
}

export function mergeLatestFailedAnalysisPatch(currentFailedPatch, failedPatch, latestPatch) {
  return {
    ...(currentFailedPatch || {}),
    ...(failedPatch || {}),
    ...(latestPatch || {}),
  };
}

const COLOR_METADATA_KEYS = ['name', 'swatchId', 'isBase', 'isMain', 'monotone'];

export function mergeColorMetadataWithPersistedImages(persistedColors, editedColors) {
  const persistedById = new Map((persistedColors || []).map((color) => [color.id, color]));
  return (editedColors || []).map((edited) => {
    const persisted = persistedById.get(edited.id);
    const merged = persisted ? { ...persisted } : { id: edited.id, images: [] };
    COLOR_METADATA_KEYS.forEach((field) => {
      if (Object.prototype.hasOwnProperty.call(edited, field)) merged[field] = edited[field];
    });
    // 분석 후 사진 편집은 별도 업로드 전의 blob/local id일 수 있다. 색상명 저장이 그 임시값을
    // 서버의 asset 목록으로 승격하지 않도록 마지막 서버 저장본의 images만 유지한다.
    // 단 올릴 수 없는 src(=목 데모의 SVG 플레이스홀더)는 저장본에 있었더라도 실어 보내지
    // 않는다 — 그게 실려 나가면 복원된 화면의 상품 사진이 되고 확정 업로드가 서버에
    // 거부된다(2026-08-17 사고).
    merged.images = (persisted?.images || []).filter((image) => !isPlaceholderPhotoSrc(image?.src));
    return merged;
  });
}

export function createTrailingPatchScheduler({
  commit,
  delayMs = 1500,
  setTimer = setTimeout,
  clearTimer = clearTimeout,
}) {
  let pendingPatch = null;
  let timer = null;

  const commitPending = () => {
    if (!pendingPatch) return false;
    const patch = pendingPatch;
    pendingPatch = null;
    commit(patch);
    return true;
  };

  return {
    schedule(patch) {
      pendingPatch = { ...(pendingPatch || {}), ...(patch || {}) };
      if (timer != null) clearTimer(timer);
      timer = setTimer(() => {
        timer = null;
        commitPending();
      }, delayMs);
    },
    flush() {
      if (timer != null) clearTimer(timer);
      timer = null;
      return commitPending();
    },
  };
}

export async function persistAnalysisEdit(api, projectId, patch) {
  const { productPatch } = splitAnalysisEditPatch(patch);
  const saved = {};
  // sbSaveNow(storyboardPersistence.js)와 같은 원칙 — 저장이 착지하기 전에 먼저 무효화해,
  // 진행 중인 저장과 레이스하는 프리페치가 곧 스테일해질 값을 캐시해 버리지 않게 한다.
  if (projectId && Object.keys(patch || {}).some((key) => STORYBOARD_SEED_PATCH_KEYS.has(key))) {
    invalidateStoryboardEntryPrefetch(projectId);
  }
  if (projectId && hasPatchFields(productPatch)) {
    saved.product = await api.saveProduct(projectId, productPatch);
  }
  // AnalysisForm의 현재 저장 shape에는 추천 갱신 컨텍스트(clothingType)와 실측 표시값도 들어 있다.
  // 서버 Product를 먼저 갱신해 생성의 정본을 맞춘 뒤, 기존 analysis 저장 계약은 유지한다.
  if (hasPatchFields(patch)) {
    saved.analysis = await api.saveAnalysis(projectId, patch);
  }
  return saved;
}
