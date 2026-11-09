/* =============================================================
   콘티보드 사용자 분류의 단일 소스.

   섹션은 템플릿 인스턴스다. sectionRole은 생성과 에디터가 읽는 촬영 묶음이다.

   contentRole은 AI가 사진의 설명 목적을 정할 때만 쓰는 내부값이다.
   cutType은 사용자가 인스펙터에서 고르는 촬영 방식이며, 내부 역할은
   현재 섹션·카드 위치·선택한 cutType/shot에 맞춰 자동으로 정한다.
   ============================================================= */

import { photoSlotForBlock } from './pageTemplates/slots.js';

export const STORYBOARD_TAXONOMY_VERSION = 4;

export const SECTION_ROLES = Object.freeze({
  HOOKING: 'hooking',
  STYLING: 'styling',
  STUDIO: 'studio',
  PRODUCT: 'product',
});

export const SECTION_ROLE_OPTIONS = Object.freeze([
  { value: SECTION_ROLES.HOOKING, label: '후킹' },
  { value: SECTION_ROLES.STYLING, label: '스타일링' },
  { value: SECTION_ROLES.STUDIO, label: '스튜디오' },
  { value: SECTION_ROLES.PRODUCT, label: '의류 확인' },
]);

export const SECTION_TITLES = Object.freeze(Object.fromEntries(
  SECTION_ROLE_OPTIONS.map((option) => [option.value, option.label]),
));

const CUT_TYPE_OPTIONS = Object.freeze([
  Object.freeze({ value: 'styling', label: '스타일링컷' }),
  Object.freeze({ value: 'horizon', label: '호리존컷' }),
  Object.freeze({ value: 'product', label: '제품컷' }),
]);

export const isSupportedCutType = (cutType) => CUT_TYPE_OPTIONS.some((option) => option.value === cutType);

export function filterSupportedStoryboardBlocks(blocks) {
  if (!Array.isArray(blocks)) return blocks;
  const unsupported = (block) => block && block.source === 'ai'
    && block.cutType != null && !isSupportedCutType(block.cutType);
  const ignored = blocks.filter(unsupported);
  if (!ignored.length) return blocks;
  const ignoredIds = new Set(ignored.map((block) => block.id).filter(Boolean));
  return blocks.filter((block) => !unsupported(block) && !ignoredIds.has(block?.similarSourceBlockId));
}

const CUT_TYPE_OPTIONS_BY_SECTION = Object.freeze({
  [SECTION_ROLES.HOOKING]: CUT_TYPE_OPTIONS,
  [SECTION_ROLES.STYLING]: CUT_TYPE_OPTIONS,
  [SECTION_ROLES.STUDIO]: CUT_TYPE_OPTIONS,
  [SECTION_ROLES.PRODUCT]: CUT_TYPE_OPTIONS,
});

export const allowedCutTypeOptionsForSection = (blockOrRole) => {
  if (typeof blockOrRole !== 'object') return CUT_TYPE_OPTIONS;
  if (blockOrRole?.source === 'text') return [];
  const slot = photoSlotForBlock(blockOrRole);
  return slot ? CUT_TYPE_OPTIONS.filter(option => slot.recipe.allowedCutTypes.includes(option.value)) : CUT_TYPE_OPTIONS;
};

export const cutTypeOptionsForSection = (sectionRole) => (
  allowedCutTypeOptionsForSection(sectionRole)
);

// 섹션 개념이 없는 화면(에디터 '새 이미지 추가')용 — 섹션별 목록의 중복 없는 합집합.
export const ALL_CUT_TYPE_OPTIONS = Object.freeze([...new Map(
  Object.keys(CUT_TYPE_OPTIONS_BY_SECTION).flatMap(cutTypeOptionsForSection)
    .map((option) => [option.value, option]),
).values()]);

export const PRODUCT_PRESENTATION_OPTIONS = Object.freeze([
  { value: 'ghost', label: '고스트컷' },
  { value: 'flat', label: '평면컷' },
  { value: 'natural_light', label: '자연광컷' },
]);

export function productPresentationOf(block) {
  return block?.source !== 'mine' && block?.cutType === 'product' && block?.shot === 'ghost'
    && PRODUCT_PRESENTATION_OPTIONS.some((option) => option.value === block.productPresentation)
    ? block.productPresentation : null;
}

export function normalizeProductPresentation(block) {
  if (!block || !Object.hasOwn(block, 'productPresentation') || productPresentationOf(block)) return block;
  const { productPresentation: _presentation, ...clean } = block;
  return clean;
}

export const CONTENT_ROLES = Object.freeze({
  HERO: 'hero',
  BENEFIT: 'benefit',
  COORDINATION: 'coordination',
  FIT: 'fit',
  PRODUCT_OVERVIEW: 'productOverview',
  DETAIL: 'detail',
  CUSTOM: 'custom',
});

export const CONTENT_TEMPLATES = Object.freeze([
  {
    value: CONTENT_ROLES.HERO,
    label: '첫 장면',
    description: '상품의 분위기와 매력을 한눈에 보여줘요.',
    sectionRole: SECTION_ROLES.HOOKING,
    cutType: 'styling', direction: 'front', shot: 'full', faceExposure: 'same',
  },
  {
    value: CONTENT_ROLES.BENEFIT,
    label: '핵심 장점',
    description: '옷의 눈에 띄는 장점을 중간 거리에서 보여줘요.',
    sectionRole: SECTION_ROLES.HOOKING,
    cutType: 'horizon', direction: 'front', shot: 'medium', faceExposure: 'same',
  },
  {
    value: CONTENT_ROLES.COORDINATION,
    label: '코디 활용',
    description: '다른 옷과 자연스럽게 입은 모습을 보여줘요.',
    sectionRole: SECTION_ROLES.STYLING,
    cutType: 'styling', direction: 'front', shot: 'full', faceExposure: 'same',
  },
  {
    value: CONTENT_ROLES.FIT,
    label: '핏 확인',
    description: '깨끗한 배경에서 옷의 핏과 실루엣을 확인해요.',
    sectionRole: SECTION_ROLES.STUDIO,
    cutType: 'horizon', direction: 'front', shot: 'full', faceExposure: 'same',
  },
  {
    value: CONTENT_ROLES.PRODUCT_OVERVIEW,
    label: '제품 전체',
    description: '사람 없이 옷의 전체 모양을 또렷하게 보여줘요.',
    sectionRole: SECTION_ROLES.PRODUCT,
    cutType: 'product', direction: 'front', shot: 'ghost', faceExposure: null,
  },
  {
    value: CONTENT_ROLES.DETAIL,
    label: '디테일',
    // 2026-08-07 개편: 디테일 사진이 없어도 앞뒤 원본의 확인 가능한 부분을 확대해 생성한다
    // (서버 구조 확대 모드) — requiresDetailImage 게이트 폐기, 디테일 컷 상시 제공.
    description: '디테일 사진이 있으면 그대로, 없으면 앞뒤 사진에서 보이는 부분을 확대해요.',
    sectionRole: SECTION_ROLES.PRODUCT,
    cutType: 'product', direction: 'front', shot: 'detail', faceExposure: null,
  },
  {
    value: CONTENT_ROLES.CUSTOM,
    label: '직접 구성',
    description: '정해진 목적에 맞지 않는 사진을 직접 구성해요.',
    sectionRole: null,
    cutType: null, direction: null, shot: null, faceExposure: null,
  },
]);

const TEMPLATE_BY_ROLE = new Map(CONTENT_TEMPLATES.map((template) => [template.value, template]));
const VALID_SECTION_ROLES = new Set(SECTION_ROLE_OPTIONS.map((option) => option.value));
const VALID_CONTENT_ROLES = new Set(CONTENT_TEMPLATES.map((template) => template.value));
const WORN_DIRECTIONS = new Set(['front', 'back', 'side']);
const WORN_SHOTS = new Set(['full', 'medium']);
const PRODUCT_DIRECTIONS = new Set(['front', 'back']);
const PRODUCT_OVERVIEW_SHOTS = new Set(['ghost']);
const WORN_ROLE_BY_CUT_TYPE = Object.freeze({
  styling: CONTENT_ROLES.COORDINATION,
  horizon: CONTENT_ROLES.FIT,
});

export const contentTemplate = (role) => TEMPLATE_BY_ROLE.get(role) || TEMPLATE_BY_ROLE.get(CONTENT_ROLES.CUSTOM);
export const contentTitle = (role) => contentTemplate(role).label;
export const sectionTitle = (role) => SECTION_TITLES[role] || '구성';
export const isSectionRole = (role) => VALID_SECTION_ROLES.has(role);
export const isContentRole = (role) => VALID_CONTENT_ROLES.has(role);
export const sectionRoleForContentRole = (role) => contentTemplate(role).sectionRole;
export const defaultContentRoleForSection = (sectionRole) =>
  contentTemplatesForSection(sectionRole)[0]?.value || CONTENT_ROLES.CUSTOM;

/* side 의 하위 갈래. 미기재는 옆모습 — 사선(threeQuarter)은 2026-09-21에 생긴 값이고
   그 전에 발행된 side 예시는 전수조사 결과 전부 완전 옆모습이다. 서버
   cut_generator._side_style_of 와 같은 기본값을 쓴다. */
const sideStyleOf = (source) => (
  (source && source.sideStyle) === 'threeQuarter' ? 'threeQuarter' : 'profile'
);

/* 예시와 카드가 **같은 그림**(방향 가족)인가 — 정면 / 사선 / 옆모습 / 뒷면.
   side 는 direction 만으로 같은 그림이 아니다 — 사선과 90도 옆모습이 같은 'side' 다.
   갈라 보지 않으면 사선 카드가 완전 옆모습 사진의 포즈를 물려받는다.
   방향이 없는 예시(direction=null — 제품·옛 항목)는 어느 가족에도 안 속한다. */
export function exampleDirectionFamilyMatches(example, { direction, sideStyle = null }) {
  if (!example || !direction) return false;
  if (!['front', 'back', 'side'].includes(example.direction)) return false;
  if (example.direction !== direction) return false;
  return direction !== 'side' || sideStyleOf(example) === sideStyleOf({ sideStyle });
}

export function poseExampleDirectionCompatible(example, { cutType, direction, sideStyle = null }) {
  if (!example || !['styling', 'horizon'].includes(cutType)) return false;
  if (!['styling', 'horizon'].includes(example.cutType)) return false;
  return exampleDirectionFamilyMatches(example, { direction, sideStyle });
}

/* 디테일 역할도 항상 포함 — 2026-08-07 개편(구조 확대 모드)으로 디테일 사진 유무는
   생성 방식만 가르고 제공 여부를 가르지 않는다. 호출부가 남긴 { hasDetailImage } 옵션은
   무시된다(호환 목적, 점진 제거). */
export function contentTemplatesForSection(sectionRole) {
  return CONTENT_TEMPLATES.filter((template) => template.sectionRole === sectionRole);
}

export function inferSectionRole(block) {
  if (isSectionRole(block?.sectionRole)) return block.sectionRole;
  // taxonomy v2 저장본은 읽는 즉시 공식 네 섹션으로 승격한다.
  if (block?.sectionRole === 'benefit') return SECTION_ROLES.HOOKING;
  if (block?.sectionRole === 'fit') {
    return block?.cutType === 'horizon' || block?.contentRole === CONTENT_ROLES.FIT
      ? SECTION_ROLES.STUDIO : SECTION_ROLES.STYLING;
  }
  // EditorBlock은 같은 값을 `kind`에 저장한다.
  if (isSectionRole(block?.kind)) return block.kind;
  if (block?.kind === 'benefit') return SECTION_ROLES.HOOKING;
  if (block?.kind === 'fit') {
    return block?.cutType === 'horizon' || block?.contentRole === CONTENT_ROLES.FIT
      ? SECTION_ROLES.STUDIO : SECTION_ROLES.STYLING;
  }
  if (block?.cutType === 'product') return SECTION_ROLES.PRODUCT;
  if (block?.cutType === 'horizon') return SECTION_ROLES.STUDIO;
  if (block?.cutType === 'styling') return SECTION_ROLES.STYLING;
  return null;
}

export function inferContentRole(block) {
  if (block?.source === 'text') return null;
  if (isContentRole(block?.contentRole)) return block.contentRole;
  if (block?.source === 'mine') return CONTENT_ROLES.CUSTOM;
  if (block?.cutType === 'product') return block?.shot === 'detail'
    ? CONTENT_ROLES.DETAIL : CONTENT_ROLES.PRODUCT_OVERVIEW;
  if (block?.cutType === 'horizon') return CONTENT_ROLES.FIT;
  if (block?.cutType === 'styling') return CONTENT_ROLES.COORDINATION;
  return CONTENT_ROLES.CUSTOM;
}

export function blockPatchForContentRole(block, role, { clothingType = 'top' } = {}) {
  const template = contentTemplate(role);
  if (!template.cutType) {
    return {
      contentRole: CONTENT_ROLES.CUSTOM, title: template.label, exampleId: null, exampleSelectionOrigin: null,
      ...(block ? { thumb: block.baseThumb || block.thumb, baseThumb: null } : {}),
    };
  }
  const worn = template.cutType === 'styling' || template.cutType === 'horizon';
  return {
    source: 'ai',
    contentRole: template.value,
    title: template.label,
    cutType: template.cutType,
    direction: template.direction,
    shot: template.shot,
    faceExposure: template.faceExposure,
    angle: 'same',
    exampleId: null,
    exampleSelectionOrigin: null,
    ...(block ? { thumb: block.baseThumb || block.thumb, baseThumb: null } : {}),
    outerClosureState: clothingType === 'outer' && worn
      ? (block?.outerClosureState || 'open') : null,
    ...(template.cutType === 'product' ? { matchIds: [] } : {}),
  };
}

/* 저장본을 읽을 때 사진 목적과 비노출 생성 레시피가 어긋나지 않게 맞춘다.
   방향·샷이 새 목적에서도 유효하면 사용자의 기존 선택을 보존하고, 유효하지
   않은 값만 목적의 기본값으로 되돌린다. */
export function normalizedRecipePatch(block, role) {
  if (block?.source === 'text') return { cutType: null, contentRole: null, sectionRole: null };
  if (block?.source === 'mine') return { contentRole: CONTENT_ROLES.CUSTOM, title: '내 이미지', cutType: null };
  const slot = photoSlotForBlock(block, { includeReuse: true });
  const recipe = slot?.recipe;
  let cutType = block?.cutType;
  if (recipe && !recipe.allowedCutTypes.includes(cutType)) cutType = recipe.cutType;
  if (!isSupportedCutType(cutType)) cutType = contentTemplate(role).cutType;
  if (!cutType) return { contentRole: role || CONTENT_ROLES.CUSTOM, title: contentTitle(role) };
  const worn = cutType !== 'product';
  let shot = block?.shot;
  if (recipe && !recipe.allowedShots.includes(shot)) shot = recipe.shot;
  if (!(worn ? WORN_SHOTS : new Set(['ghost', 'detail'])).has(shot)) shot = worn ? 'full' : 'ghost';
  const nextRole = [CONTENT_ROLES.HERO, CONTENT_ROLES.BENEFIT].includes(role) && worn ? role
    : slot?.contentRole && (!recipe || recipe.allowedCutTypes.includes(cutType)) ? slot.contentRole
      : cutType === 'product' ? (shot === 'detail' ? CONTENT_ROLES.DETAIL : CONTENT_ROLES.PRODUCT_OVERVIEW)
        : WORN_ROLE_BY_CUT_TYPE[cutType];
  return {
    cutType, shot, contentRole: nextRole, title: contentTitle(nextRole),
    ...(Object.hasOwn(block || {}, 'productPresentation')
      ? { productPresentation: productPresentationOf({ ...block, cutType, shot }) } : {}),
    direction: (worn ? WORN_DIRECTIONS : PRODUCT_DIRECTIONS).has(block?.direction)
      ? block.direction : recipe?.direction || 'front',
    ...(!worn ? { matchIds: [], outerClosureState: null, faceExposure: null }
      : !['same', 'show', 'hide'].includes(block?.faceExposure) ? { faceExposure: recipe?.faceExposure || 'same' } : {}),
  };
}

export function assignInternalContentRoles(blocks) {
  if (!Array.isArray(blocks)) return blocks;
  const supportedBlocks = filterSupportedStoryboardBlocks(blocks);
  let heroAssigned = false;
  let changed = supportedBlocks !== blocks;
  const next = supportedBlocks.map(raw => {
    const block = normalizeProductPresentation(raw);
    if (block !== raw) changed = true;
    if (!block) return block;
    if (block.source === 'text') {
      if (block.cutType === null && block.contentRole === null && block.sectionRole === null) return block;
      changed = true;
      return { ...block, cutType: null, contentRole: null, sectionRole: null };
    }
    let role = block.source === 'mine' ? CONTENT_ROLES.CUSTOM
      : photoSlotForBlock(block)?.contentRole || inferContentRole({ ...block, contentRole: null });
    const patch = normalizedRecipePatch(block, role);
    if (block.source === 'ai' && block.pageSectionKind === 'first'
      && ['styling', 'horizon'].includes(patch.cutType)) {
      patch.contentRole = !heroAssigned && !block.similarSourceBlockId ? CONTENT_ROLES.HERO : CONTENT_ROLES.BENEFIT;
      patch.title = contentTitle(patch.contentRole);
      if (patch.contentRole === CONTENT_ROLES.HERO) heroAssigned = true;
    }
    const sectionRole = sectionRoleForContentRole(patch.contentRole);
    const recipeChanged = block.cutType !== patch.cutType
      || block.direction !== patch.direction || block.shot !== patch.shot;
    const next = normalizeProductPresentation({ ...block, ...patch, sectionRole, taxonomyVersion: STORYBOARD_TAXONOMY_VERSION,
      ...(recipeChanged ? { exampleId: null, exampleSelectionOrigin: null,
        thumb: block.baseThumb || block.thumb, baseThumb: null, previewThumb: null } : {}),
    });
    if (JSON.stringify(next) === JSON.stringify(block)) return block;
    changed = true;
    return next;
  });
  return changed ? next : blocks;
}

export function hasDetailSource(product) {
  return (product?.colors || []).some((color) => (color?.images || []).some((image) => image?.slot === 'Detail'));
}

/* 불러온 EditorBlock의 이미지와 순서는 건드리지 않고 v2 표시 역할만 정규화한다.
   kind='info'(내용 추가 블록, PRD §10.14)는 섹션 역할 추론 대상이 아니다 —
   여기서 통과시키지 않으면 재로드 때 kind가 'fit'으로 덮여 정보 블록이 깨진다. */
export function normalizeEditorBlockRole(block) {
  if (!block || block.pageSectionKey || block.auto || block.kind === 'info' || ['twocol', 'threecol', 'grid2x2', 'colorcmp', 'size', 'care', 'ai-notice'].includes(block.kind)) return block;
  const contentRole = inferContentRole(block);
  const sectionRole = inferSectionRole(block) || SECTION_ROLES.STYLING;
  const name = block.name === '내 이미지' ? '내 이미지'
    : contentRole === CONTENT_ROLES.CUSTOM ? (block.name || contentTitle(contentRole))
      : contentTitle(contentRole);
  if (block.kind === sectionRole && block.contentRole === contentRole && block.name === name) return block;
  return { ...block, name, kind: sectionRole, contentRole };
}
