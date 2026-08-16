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

// 기본/확장 콘티 — http getStoryboard 가 저장 콘티 없을 때 시드한다.
// mock buildStoryboard와 같은 역할 중심 블록 shape을 만든다.
import { uid } from '../ids.js';
import { Placeholder as P } from '../../mock/placeholders.js';
import { ensureSections } from '../sections.js';
import { exampleSelectionFingerprintFields } from '../generationExamples.js';
import { genderForClothingType } from '../productGender.js';
import { spaceSetGroupId } from '../storyboardSpaceSetCatalog.js';
import { entryStylingMembers, pickEntrySets } from '../storyboardEntryPlacement.js';
import { applyHookStyle } from '../storyboardHookFrame.js';
import { createMeasurementFields } from '../measurementSchema.js';
import { matchingIdsForColor } from '../colorwayMatching.js';
import {
  CONTENT_ROLES,
  SECTION_ROLES,
  STORYBOARD_TAXONOMY_VERSION,
  contentTitle,
} from '../storyboardTaxonomy.js';

const sb = (sectionRole, contentRole, cutType, direction, shot, colorId, extra) => ({
  id: uid('blk'), sectionRole, contentRole, taxonomyVersion: STORYBOARD_TAXONOMY_VERSION,
  title: contentTitle(contentRole), source: 'ai', cutType, direction, shot, colorId,
  pose: 'auto', matchIds: [], faceExposure: 'same', angle: 'same', refImages: [],
  thumb: P.photo(contentRole + shot, cutType === 'product' ? 'product' : cutType === 'horizon' ? 'horizon' : 'styling', 240, 320),
  poseThumb: P.pose('stand'), poseLabel: 'AI 자동',
  ...(extra || {}),
});

function setMemberBlocks(set, colorId, sectionRole, contentRole) {
  if (!set) return [];
  const groupId = spaceSetGroupId(set.id, uid('sg'));
  const members = set.setType === 'styling' ? entryStylingMembers(set) : set.members;
  return members.map((member) => sb(
    sectionRole,
    contentRole,
    member.cutType,
    member.direction,
    member.shot,
    colorId,
    {
      spaceGroupId: groupId,
      spaceVariation: set.spaceVariation,
      spaceSetMemberOrder: member.order,
      refScope: 'pose',
      exampleId: member.exampleId,
      exampleSelectionOrigin: 'auto',
      setSelectionOrigin: 'auto',
      thumb: member.thumb,
    },
  ));
}

function stylingFallback(colorId, clothingType) {
  return [
    sb(
      SECTION_ROLES.STYLING,
      CONTENT_ROLES.COORDINATION,
      'styling',
      clothingType === 'bottom' ? 'back' : 'front',
      'full',
      colorId,
    ),
    sb(SECTION_ROLES.STYLING, CONTENT_ROLES.COORDINATION, 'styling', 'front', 'medium', colorId),
  ];
}

function horizonRotationFallback(colorId) {
  return ['front', 'side', 'back'].map((direction) => sb(
    SECTION_ROLES.STUDIO,
    CONTENT_ROLES.FIT,
    'horizon',
    direction,
    'full',
    colorId,
  ));
}

export function defaultStoryboard(colors, mode = 'basic', context = {}) {
  if (mode !== 'basic' && mode !== 'extended') throw new Error('invalid_compose_mode');
  const list = Array.isArray(colors) && colors.length ? colors : [{ id: 'col1', isBase: true }];
  const base = (list.find((c) => c.isBase) || list[0]).id;
  // 기본 구성 디테일 블록은 앞면 방향 — 앞면 디테일(Detail) 보유 색을 우선, 없으면 기준색
  // (서버가 원본 구조 확대로 폴백, 2026-08-07 개편).
  const detailColor = list.find((color) => (color.images || []).some((image) => image.slot === 'Detail'))?.id || base;
  const clothingType = context.clothingType || 'top';
  const matchClothing = context.matchClothing || [];
  const colorById = new Map(list.map((color) => [color.id, color]));
  const matchIdsFor = (colorId) => matchingIdsForColor(
    colorById.get(colorId),
    matchClothing,
    { preferMain: colorId === base },
  );
  // 서버(select_base_gender)와 동일 의미론: 남성 단독일 때만 men, 혼합·미상은 women.
  const gender = genderForClothingType(clothingType, context.targetGenders);
  const { stylingSets, rotationSet, sequenceSet } = pickEntrySets({
    gender,
    clothingType,
    projectId: context.projectId,
    stylingCount: mode === 'extended' ? 3 : 2,
  });
  // 후킹 첫 화면 스타일(2026-08-14 확정, 디폴트 = 시그니처 컷): 후킹 섹션은 스타일이
  // 필요로 하는 컷만 둔다 — 시그니처 = 확대 미디움샷 1컷(구 2컷 배치 폐기, 오너 확정).
  // 구(2컷) 기본 시드는 지문이 어긋나 "편집본"으로 남는다 — 2026-08-07 개편과 같은
  // 선례(기존 프로젝트 마이그레이션 없음, 구 오프닝 행만 pair 승격).
  const hookFrameId = `hookframe__${uid('hf')}`;
  const blocks = [
    sb(SECTION_ROLES.HOOKING, CONTENT_ROLES.HERO, 'horizon', 'front', 'medium', base, {
      hookFrameId, hookStyle: 'signature', hookFrameVersion: 1, hookTitleOverlay: true, hookSlotRole: 'signature',
    }),
  ];

  // 스타일링 섹션은 **장소 세트만** 자동 배치한다 — 세트에 안 묶인 낱장 컷을 하나 얹던
  // 규칙은 폐기(2026-08-16 오너: "공간세트 2개 말고 컷 하나가 더 붙는 것 없애줘").
  // 낱장 스타일링컷·거울샷은 셀러가 '컷 추가'로 직접 넣는 수동 선택지로만 남는다.
  for (const set of stylingSets) {
    blocks.push(...(set
      ? setMemberBlocks(set, base, SECTION_ROLES.STYLING, CONTENT_ROLES.COORDINATION)
      : stylingFallback(base, clothingType)));
  }

  if (mode === 'extended') {
    const horizonSet = sequenceSet || rotationSet;
    blocks.push(...(horizonSet
      ? setMemberBlocks(horizonSet, base, SECTION_ROLES.STUDIO, CONTENT_ROLES.FIT)
      : horizonRotationFallback(base)));

    const additionalColors = list.filter((color) => color.id !== base).slice(0, 3);
    for (const color of additionalColors) {
      const colorwayGroupId = `colorway__${color.id}`;
      const layoutRowId = `row__colorway__${color.id}`;
      blocks.push(
        sb(SECTION_ROLES.STUDIO, CONTENT_ROLES.FIT, 'horizon', 'front', 'full', color.id, {
          colorwayGroupId,
          colorwayPairVersion: 1,
          sectionLayout: 'twoColumn',
          layoutRowId,
          layoutRowVersion: 1,
        }),
        sb(SECTION_ROLES.STUDIO, CONTENT_ROLES.FIT, 'horizon', 'front', 'medium', color.id, {
          colorwayGroupId,
          colorwayPairVersion: 1,
          sectionLayout: 'twoColumn',
          layoutRowId,
          layoutRowVersion: 1,
        }),
      );
    }

    for (const color of additionalColors) {
      blocks.push(sb(
        SECTION_ROLES.PRODUCT,
        CONTENT_ROLES.PRODUCT_OVERVIEW,
        'product',
        'front',
        'ghost',
        color.id,
      ));
    }
    blocks.push(
      sb(SECTION_ROLES.PRODUCT, CONTENT_ROLES.PRODUCT_OVERVIEW, 'product', 'front', 'ghost', base),
      sb(SECTION_ROLES.PRODUCT, CONTENT_ROLES.PRODUCT_OVERVIEW, 'product', 'back', 'ghost', base),
    );
    blocks.push(
      sb(SECTION_ROLES.PRODUCT, CONTENT_ROLES.DETAIL, 'product', 'front', 'detail', detailColor),
      sb(SECTION_ROLES.PRODUCT, CONTENT_ROLES.DETAIL, 'product', 'front', 'detail', detailColor),
    );
  } else {
    blocks.push(...(rotationSet
      ? setMemberBlocks(rotationSet, base, SECTION_ROLES.STUDIO, CONTENT_ROLES.FIT)
      : horizonRotationFallback(base)));
    blocks.push(
      sb(SECTION_ROLES.PRODUCT, CONTENT_ROLES.PRODUCT_OVERVIEW, 'product', 'front', 'ghost', base),
    );
    for (const color of list.slice(1, 4)) {
      blocks.push(sb(
        SECTION_ROLES.PRODUCT,
        CONTENT_ROLES.PRODUCT_OVERVIEW,
        'product',
        'front',
        'ghost',
        color.id,
      ));
    }
    blocks.push(sb(SECTION_ROLES.PRODUCT, CONTENT_ROLES.DETAIL, 'product', 'front', 'detail', detailColor));
  }
  return ensureSections(blocks.map((block) => (
    ['styling', 'horizon', 'mirror'].includes(block.cutType)
      ? { ...block, matchIds: matchIdsFor(block.colorId) }
      : block
  )));
}

/* id·썸네일처럼 시드할 때마다 바뀌는 표시 필드를 빼고, 사용자가
   바꿀 수 있는 의미/생성/배치 필드만 비교한다. HTTP에서 사진 양을
   바꾸었을 때 '손대지 않은 기본 콘티'만 새 모드로 교체하기 위한 지문이다. */
function storyboardTemplateFingerprint(blocks) {
  const spaceIds = new Map();
  const rowIds = new Map();
  const colorwayIds = new Map();
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
    ...exampleSelectionFingerprintFields(block),
    refImages: block.refImages || [],
    refAssetIds: block.refAssetIds || [],
    ownImages: block.ownImages || [],
    spaceGroup: ordinal(spaceIds, block.spaceGroupId),
    spaceVariation: block.spaceVariation ?? null,
    sectionLayout: block.sectionLayout || 'stack',
    sectionCustom: !!block.sectionCustom,
    layoutRow: ordinal(rowIds, block.layoutRowId),
    layoutRowVersion: block.layoutRowVersion ?? null,
    colorwayGroup: ordinal(colorwayIds, block.colorwayGroupId),
    colorwayPairVersion: block.colorwayPairVersion ?? null,
  })));
}

/* 시드 보드에 두 컷 구성을 적용한다 — 후킹 시드가 시그니처 1컷이라 오른칸은 여기서
   만든다. 이 팩토리는 UI 전환(applyHookStyleChoice의 createBlock)과 지문 필드
   (storyboardTemplateFingerprint)가 일치해야 한다 — 어긋나면 pair 로 바꾼 기본 보드가
   사진 양 변경 재시드에서 "편집본"으로 오판된다. */
export function applySeededHookStyle(seeded, style, colors) {
  if (style !== 'pair') return seeded;
  const template = seeded.find((block) => (
    block.sectionRole === SECTION_ROLES.HOOKING && block.source !== 'mine'
  ));
  if (!template) return seeded;
  const createBlock = (slot) => ({
    ...sb(SECTION_ROLES.HOOKING, CONTENT_ROLES.BENEFIT, slot.cutType, 'front', slot.shot,
      slot.colorId || template.colorId),
    matchIds: [...(template.matchIds || [])],
    poseThumb: template.poseThumb,
    thumb: template.thumb,
  });
  return applyHookStyle(seeded, 'pair', { colors: colors || [], createBlock });
}

export function isDefaultStoryboardForMode(blocks, colors, mode, product = {}) {
  if (!Array.isArray(blocks) || !blocks.length) return false;
  // 현재 역할 분류 계약을 충족하지 않는 보드는 기본 시드로 간주해 교체하지 않는다.
  if (blocks.some((block) => block.taxonomyVersion !== STORYBOARD_TAXONOMY_VERSION)) return false;
  const seeded = defaultStoryboard(colors, mode, product);
  const fingerprint = storyboardTemplateFingerprint(blocks);
  // 2026-08-07 개편 전 기본 시드(디테일 없음→ghost 대체)와 구 오프닝 행 시드는 지문이
  // 어긋나 "편집본"으로 남는다 — 기존 프로젝트가 전부 테스트용이라 마이그레이션하지
  // 않기로 한 선례(오너 결정)를 따르고, 구 오프닝 행은 진입 승격(adoptHookFrame)이 흡수한다.
  // 두 번째 지문: 첫 화면 스타일만 두 컷 구성으로 바꾼 기본 보드도 기본 시드로 인정 —
  // 사진 양 변경 시 재시드가 스타일 선택을 존중하며 계속 동작하게 한다.
  return fingerprint === storyboardTemplateFingerprint(seeded)
    || fingerprint === storyboardTemplateFingerprint(applySeededHookStyle(seeded, 'pair', colors));
}

// analyzeProduct 의 shape 뼈대 — AnalysisForm 이 무가드로 읽는 필드 전부 포함(계약 §6).
// AI 산출 필드(clothingType/materials/styleTags 등)는 콜러가 덮어쓴다.
export function defaultAnalysisShape(clothingType = 'top') {
  return {
    clothingType: null, subCategory: null, targetGenders: [],
    // enum 밖 의류의 자유 명칭(AG-01 추측 + 셀러 주관식 수정, 계약 §3.2). mock/db.js 에는
    // 있는데 여기 없어서 http 경로에서만 유실됐다 — AI 가 "후드 집업" 을 추측해도 폼에 안 떴다.
    customCategory: null,
    fit: null, suggestedName: '',
    materials: [], sellingPoints: [], aiSuggestedPoints: [],
    styleTags: [], swatchSuggestions: [],
    selectedModelId: null, models: [],
    matchClothing: [],
    // AG-01 파생(셀러 미편집). 저장이 REPLACE 라 shape 에 없으면 한 번의 저장으로 사라지고,
    // 그러면 거울 셀카 원본의 반전된 로고가 그대로 생성 컷에 남는다.
    sourceMirrored: false,
    washCare: '', locked: false, measurementsUnknown: false,
    measurements: createMeasurementFields(clothingType),
    fitProfile: null,
  };
}
