import { selectPublicAnalysisPhotos } from './publicAnalysisPhotos.js';

// Display/ranking only. Generation resolves this ID against the server-owned contract.
export const isProductDetail = (block) => block?.source !== 'mine'
  && block?.cutType === 'product' && block?.shot === 'detail';

export const isSourceBasedDetailRecipe = ({ cutType, shot, clothingType } = {}) => (
  cutType === 'product' && shot === 'detail'
  && ['top', 'bottom', 'outer', 'dress'].includes(clothingType)
);

export function selectableDetailTargets(recommendations) {
  if (recommendations?.version !== 1 || recommendations.status !== 'ready') return [];
  return (Array.isArray(recommendations.candidates) ? recommendations.candidates : [])
    .filter((candidate) => candidate?.id && candidate.informationGroup
      && candidate.photoUse === 'standalone' && candidate.visibility === 'clear'
      && ['front', 'back'].includes(candidate.direction));
}

// Small, explicit vocabulary for display-text ranking, never target eligibility.
// Generic families include their concrete forms; concrete cues stay separate so
// a collar cannot satisfy a ribbed neckline, nor a zipper satisfy a button claim.
const DETAIL_SUBJECT_CUES = [
  ['목선', '넥라인', '네크라인', '라운드넥', '브이넥', 'v넥', '카라', '칼라'],
  ['카라', '칼라'], ['라운드넥', '둥근목'], ['브이넥', 'v넥'],
  ['여밈', '앞섶', '앞섬', '플라켓', '지퍼', '집업', '단추', '버튼'],
  ['지퍼', '집업'], ['단추', '버튼'],
  ['주머니', '포켓'], ['허리', '웨이스트'],
  ['소매', '소맷', '슬리브', '커프스'], ['소맷단', '소매끝', '커프스'],
  ['밑단', '헴'], ['가슴', '흉부'], ['등판', '뒷판'],
  ['레이스'], ['리본'], ['골지', '리브'],
  ['원단', '소재', '패브릭', '골지', '리브', '니트', '직조'],
  ['봉제', '스티치'], ['주름', '플리츠'], ['라벨', '케어택'],
];
const normalizeDetailText = (value) => String(value || '').replace(/[\s\p{P}\p{S}]+/gu, '').toLowerCase();
const subjectCues = (text) => {
  const cueText = text.replaceAll('슬리브', '소매'); // sleeve must not match rib (리브).
  return DETAIL_SUBJECT_CUES
    .map((aliases, index) => aliases.some((alias) => cueText.includes(alias)) ? index : null)
    .filter((index) => index !== null);
};

function detailEmphasisPriority(candidate, highlights) {
  const facts = [candidate.label, ...(candidate.featurePoints || [])]
    .map(normalizeDetailText).filter((text) => text.length > 1);
  const observedCues = new Set(facts.flatMap(subjectCues));
  return highlights.filter((highlight) => {
    const cues = subjectCues(highlight);
    // All named subject cues must be present in this candidate's observed text.
    if (cues.some((cue) => !observedCues.has(cue))) return false;
    return facts.some((fact) => highlight.includes(fact) || fact.includes(highlight))
      || cues.length > 0;
  }).length;
}

export function recommendedDetailTargets(recommendations, mode, sellingPoints = []) {
  const highlights = [...new Set(sellingPoints.map(normalizeDetailText))].filter((point) => point.length > 1);
  const ranked = [...selectableDetailTargets(recommendations)]
    .sort((a, b) => detailEmphasisPriority(b, highlights) - detailEmphasisPriority(a, highlights) || a.rank - b.rank);
  const groups = new Set();
  const ids = new Set();
  return ranked.filter((candidate) => {
    if (groups.has(candidate.informationGroup) || ids.has(candidate.id)) return false;
    groups.add(candidate.informationGroup);
    ids.add(candidate.id);
    return true;
  }).slice(0, mode === 'extended' ? 3 : 2);
}

export function detailTargetById(recommendations, id) {
  return (recommendations?.candidates || []).find((candidate) => candidate.id === id) || null;
}

export function detailTargetPatch(candidate) {
  return candidate ? {
    detailTargetId: candidate.id, detailTargetOrigin: 'user', direction: candidate.direction,
  } : { detailTargetId: null, detailTargetOrigin: 'user' };
}

export function preserveDetailTargetBinding(block, changes) {
  if (!block?.detailTargetId || 'detailTargetId' in changes) return changes;
  if ((changes.colorId != null && changes.colorId !== block.colorId)
    || (changes.direction != null && changes.direction !== block.direction)
    || (changes.shot != null && changes.shot !== 'detail')
    || (changes.cutType != null && changes.cutType !== 'product')
    || changes.source === 'mine') {
    return { ...changes, detailTargetId: null, detailTargetOrigin: 'user' };
  }
  return changes;
}

export function detailTargetPresentation(block, product, recommendations) {
  if (!isProductDetail(block)) return null;
  const target = detailTargetById(recommendations, block.detailTargetId);
  const colors = product?.colors || [];
  const base = colors.find((color) => color.isBase) || colors[0];
  const sourceColor = target ? base : colors.find((color) => color.id === block.colorId) || base;
  const slot = target?.sourceSlot || (block.direction === 'back' ? 'Back' : 'Front');
  const indexedImage = target ? selectPublicAnalysisPhotos(base?.images)[target.sourceIndex] : null;
  const overview = sourceColor?.images?.find((item) => item.slot === (block.direction === 'back' ? 'Back' : 'Front'));
  const image = (overview?.src ? overview : null) || (indexedImage?.slot === slot ? indexedImage : null)
    || sourceColor?.images?.find((item) => item.slot === slot);
  return { src: image?.src || null, label: target?.label || (block.detailTargetId ? '선택한 디테일' : '원본에서 디테일 촬영'), target };
}

export function detailRecommendationMessage(recommendations) {
  return recommendations?.status === 'ready'
    ? '단독 사진으로 보여줄 디테일을 찾지 못했어요. 필요하면 컷을 직접 추가할 수 있어요.'
    : '디테일 추천을 준비하지 못했어요. 필요하면 원본을 바탕으로 컷을 직접 추가할 수 있어요.';
}
