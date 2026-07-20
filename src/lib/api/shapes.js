/* =============================================================
   lib/api/shapes — 클라 소유 기본 shape (계약 §6).
   httpAdapter 가 mock db.js 에 의존하지 않도록, AI 분석이 산출하지 못하는
   필드(models·selectedModelId·측정 구조 등)의 편집용 기본값을 여기 둔다.
   AI 산출 필드는 analyzeProduct 콜러가 덮어쓴다.
   ============================================================= */

// 인물모델은 더 이상 정적 시드가 아니다 — FaceMarket 검증 모델 카탈로그(GET /v1/facemarket/models,
// listModels())를 AnalysisForm 이 런타임에 불러온다. 기본 shape 은 빈 목록 + 미선택으로 둔다.
// selectedModelId 는 셀러가 라이선스 활성 모델을 고르면 실 fm_models.id(UUID)로 채워지고,
// saveAnalysis 가 서버에 지속해 생성 게이트가 서버측에서 라이선스를 해석한다.

// 실측 템플릿 — key 는 영문 토큰(계약 §4). value 는 AI 미산출 → null(사용자 직접 입력, PRD §6.5).
const MEASUREMENT_TEMPLATE = [
  { key: 'totalLength', value: null, unit: 'cm' },
  { key: 'shoulderWidth', value: null, unit: 'cm' },
  { key: 'chestWidth', value: null, unit: 'cm' },
  { key: 'sleeveLength', value: null, unit: 'cm' },
];

// 기본/확장 콘티 — http getStoryboard 가 저장 콘티 없을 때 시드한다.
// mock buildStoryboard와 같은 역할 중심 블록 shape을 만든다.
import { uid } from '@/lib/ids.js';
import { Placeholder as P } from '@/mock/placeholders.js';
import { ensureSections } from '@/lib/sections.js';
import {
  CONTENT_ROLES,
  SECTION_ROLES,
  STORYBOARD_TAXONOMY_VERSION,
  contentTitle,
  hasDetailSource,
} from '@/lib/storyboardTaxonomy.js';

const sb = (sectionRole, contentRole, cutType, direction, shot, colorId, extra) => ({
  id: uid('blk'), sectionRole, contentRole, taxonomyVersion: STORYBOARD_TAXONOMY_VERSION,
  title: contentTitle(contentRole), source: 'ai', cutType, direction, shot, colorId,
  pose: 'auto', matchIds: [], faceExposure: 'same', angle: 'same', refImages: [],
  thumb: P.photo(contentRole + shot, cutType === 'product' ? 'product' : cutType === 'horizon' ? 'horizon' : 'styling', 240, 320),
  poseThumb: P.pose('stand'), poseLabel: 'AI 자동',
  ...(extra || {}),
});

export function defaultStoryboard(colors, mode = 'basic') {
  if (mode !== 'basic' && mode !== 'extended') throw new Error('invalid_compose_mode');
  const list = Array.isArray(colors) && colors.length ? colors : [{ id: 'col1', isBase: true }];
  const base = (list.find((c) => c.isBase) || list[0]).id;
  const hasDetail = hasDetailSource({ colors: list });
  const detailColor = list.find((color) => (color.images || []).some((image) => image.slot === 'Detail'))?.id || base;
  const spacePair = (colorId) => {
    const spaceGroupId = uid('sg');
    return [
      sb(SECTION_ROLES.FIT, CONTENT_ROLES.COORDINATION, 'styling', 'front', 'full', colorId, { spaceGroupId, spaceVariation: 'subtle' }),
      sb(SECTION_ROLES.FIT, CONTENT_ROLES.COORDINATION, 'styling', 'side', 'medium', colorId, { spaceGroupId, spaceVariation: 'subtle' }),
    ];
  };
  const blocks = [
    sb(SECTION_ROLES.BENEFIT, CONTENT_ROLES.HERO, 'styling', 'front', 'full', base),
    sb(SECTION_ROLES.BENEFIT, CONTENT_ROLES.BENEFIT, 'horizon', 'front', 'medium', base),
  ];
  if (mode === 'extended') {
    list.slice(0, 4).forEach((color, colorIndex) => {
      blocks.push(
        ...spacePair(color.id),
        sb(SECTION_ROLES.FIT, CONTENT_ROLES.FIT, 'horizon', 'front', 'medium', color.id),
        sb(SECTION_ROLES.FIT, CONTENT_ROLES.FIT, 'horizon', 'back', 'full', color.id),
        sb(SECTION_ROLES.FIT, CONTENT_ROLES.FIT, 'horizon', 'front', 'medium', color.id),
      );
      if (colorIndex === 0) blocks.push(
        sb(SECTION_ROLES.FIT, CONTENT_ROLES.COORDINATION, 'styling', 'front', 'medium', color.id),
        sb(SECTION_ROLES.FIT, CONTENT_ROLES.FIT, 'horizon', 'side', 'full', color.id),
        sb(SECTION_ROLES.FIT, CONTENT_ROLES.FIT, 'horizon', 'front', 'full', color.id),
      );
    });
    blocks.push(
      sb(SECTION_ROLES.FIT, CONTENT_ROLES.REAL_WEAR, 'mirror', null, 'full', base, { faceExposure: 'hide' }),
      sb(SECTION_ROLES.PRODUCT, CONTENT_ROLES.PRODUCT_OVERVIEW, 'product', 'front', 'ghost', base),
      sb(SECTION_ROLES.PRODUCT, CONTENT_ROLES.PRODUCT_OVERVIEW, 'product', 'back', 'ghost', base),
    );
    if (hasDetail) blocks.push(sb(SECTION_ROLES.PRODUCT, CONTENT_ROLES.DETAIL, 'product', 'front', 'detail', detailColor));
  } else {
    blocks.push(
      ...spacePair(base),
      sb(SECTION_ROLES.FIT, CONTENT_ROLES.COORDINATION, 'styling', 'front', 'medium', base),
      sb(SECTION_ROLES.FIT, CONTENT_ROLES.FIT, 'horizon', 'front', 'medium', base),
      sb(SECTION_ROLES.FIT, CONTENT_ROLES.FIT, 'horizon', 'side', 'full', base),
      sb(SECTION_ROLES.FIT, CONTENT_ROLES.FIT, 'horizon', 'back', 'full', base),
      sb(SECTION_ROLES.FIT, CONTENT_ROLES.FIT, 'horizon', 'front', 'medium', base),
      sb(SECTION_ROLES.FIT, CONTENT_ROLES.REAL_WEAR, 'mirror', null, 'full', base, { faceExposure: 'hide' }),
      sb(SECTION_ROLES.PRODUCT, CONTENT_ROLES.PRODUCT_OVERVIEW, 'product', 'front', 'ghost', base),
      hasDetail
        ? sb(SECTION_ROLES.PRODUCT, CONTENT_ROLES.DETAIL, 'product', 'front', 'detail', detailColor)
        : sb(SECTION_ROLES.PRODUCT, CONTENT_ROLES.PRODUCT_OVERVIEW, 'product', 'back', 'ghost', base),
    );
  }
  return ensureSections(blocks);
}

/* id·썸네일처럼 시드할 때마다 바뀌는 표시 필드를 빼고, 사용자가
   바꿀 수 있는 의미/생성/배치 필드만 비교한다. HTTP에서 사진 양을
   바꾸었을 때 '손대지 않은 기본 콘티'만 새 모드로 교체하기 위한 지문이다. */
function storyboardTemplateFingerprint(blocks) {
  const spaceIds = new Map();
  const rowIds = new Map();
  const ordinal = (map, value) => {
    if (!value) return null;
    if (!map.has(value)) map.set(value, map.size + 1);
    return map.get(value);
  };
  return JSON.stringify((blocks || []).map((block) => ({
    taxonomyVersion: block.taxonomyVersion,
    sectionRole: block.sectionRole,
    contentRole: block.contentRole,
    source: block.source,
    cutType: block.cutType ?? null,
    direction: block.direction ?? null,
    shot: block.shot ?? null,
    colorId: block.colorId ?? null,
    colorIds: block.colorIds || [],
    pose: block.pose ?? null,
    matchIds: block.matchIds || [],
    faceExposure: block.faceExposure ?? null,
    angle: block.angle ?? null,
    outerClosureState: block.outerClosureState ?? null,
    exampleId: block.exampleId ?? null,
    refScope: block.refScope ?? null,
    refImages: block.refImages || [],
    refAssetIds: block.refAssetIds || [],
    ownImages: block.ownImages || [],
    spaceGroup: ordinal(spaceIds, block.spaceGroupId),
    spaceVariation: block.spaceVariation ?? null,
    sectionLayout: block.sectionLayout || 'stack',
    sectionCustom: !!block.sectionCustom,
    layoutRow: ordinal(rowIds, block.layoutRowId),
    layoutRowVersion: block.layoutRowVersion ?? null,
  })));
}

export function isDefaultStoryboardForMode(blocks, colors, mode) {
  if (!Array.isArray(blocks) || !blocks.length) return false;
  // v2 계약을 충족하지 않는 보드는 기본 시드로 간주해 교체하지 않는다.
  if (blocks.some((block) => block.taxonomyVersion !== STORYBOARD_TAXONOMY_VERSION)) return false;
  return storyboardTemplateFingerprint(blocks)
    === storyboardTemplateFingerprint(defaultStoryboard(colors, mode));
}

// analyzeProduct 의 shape 뼈대 — AnalysisForm 이 무가드로 읽는 필드 전부 포함(계약 §6).
// AI 산출 필드(clothingType/materials/styleTags 등)는 콜러가 덮어쓴다.
export function defaultAnalysisShape() {
  return {
    clothingType: null, subCategory: null, targetGenders: [],
    fit: null, suggestedName: '',
    materials: [], sellingPoints: [], aiSuggestedPoints: [],
    styleTags: [], swatchSuggestions: [],
    selectedModelId: null, models: [],
    matchClothing: [],
    washCare: '', locked: false, measurementsUnknown: false,
    measurements: MEASUREMENT_TEMPLATE.map((m) => ({ ...m })),
    fitProfile: null,
  };
}
