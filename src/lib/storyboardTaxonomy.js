/* =============================================================
   콘티보드 사용자 분류의 단일 소스.

   사용자는 상세페이지의 세 섹션과 생성예시·세부 옵션만 다룬다:
   핵심 장점 → 핏·코디 → 제품 확인.

   contentRole은 AI가 사진의 설명 목적을 정할 때만 쓰는 내부값이다.
   cutType은 사용자가 인스펙터에서 고르는 촬영 방식이며, 내부 역할은
   현재 섹션·카드 위치·선택한 cutType/shot에 맞춰 자동으로 정한다.
   ============================================================= */

export const STORYBOARD_TAXONOMY_VERSION = 2;

export const SECTION_ROLES = Object.freeze({
  BENEFIT: 'benefit',
  FIT: 'fit',
  PRODUCT: 'product',
});

export const SECTION_ROLE_OPTIONS = Object.freeze([
  { value: SECTION_ROLES.BENEFIT, label: '핵심 장점' },
  { value: SECTION_ROLES.FIT, label: '핏·코디' },
  { value: SECTION_ROLES.PRODUCT, label: '제품 확인' },
]);

export const SECTION_TITLES = Object.freeze(Object.fromEntries(
  SECTION_ROLE_OPTIONS.map((option) => [option.value, option.label]),
));

const CUT_TYPE_OPTIONS_BY_SECTION = Object.freeze({
  [SECTION_ROLES.BENEFIT]: Object.freeze([
    Object.freeze({ value: 'styling', label: '스타일링컷' }),
    Object.freeze({ value: 'horizon', label: '호리존컷' }),
  ]),
  [SECTION_ROLES.FIT]: Object.freeze([
    Object.freeze({ value: 'styling', label: '스타일링컷' }),
    Object.freeze({ value: 'horizon', label: '호리존컷' }),
    Object.freeze({ value: 'mirror', label: '거울샷' }),
  ]),
  [SECTION_ROLES.PRODUCT]: Object.freeze([
    Object.freeze({ value: 'product', label: '제품컷' }),
  ]),
});

export const cutTypeOptionsForSection = (sectionRole) => (
  CUT_TYPE_OPTIONS_BY_SECTION[sectionRole] || Object.freeze([])
);

export const CONTENT_ROLES = Object.freeze({
  HERO: 'hero',
  BENEFIT: 'benefit',
  COORDINATION: 'coordination',
  FIT: 'fit',
  REAL_WEAR: 'realWear',
  PRODUCT_OVERVIEW: 'productOverview',
  DETAIL: 'detail',
  CUSTOM: 'custom',
});

export const CONTENT_TEMPLATES = Object.freeze([
  {
    value: CONTENT_ROLES.HERO,
    label: '첫 장면',
    description: '상품의 분위기와 매력을 한눈에 보여줘요.',
    sectionRole: SECTION_ROLES.BENEFIT,
    cutType: 'styling', direction: 'front', shot: 'full', faceExposure: 'same',
  },
  {
    value: CONTENT_ROLES.BENEFIT,
    label: '핵심 장점',
    description: '옷의 눈에 띄는 장점을 중간 거리에서 보여줘요.',
    sectionRole: SECTION_ROLES.BENEFIT,
    cutType: 'horizon', direction: 'front', shot: 'medium', faceExposure: 'same',
  },
  {
    value: CONTENT_ROLES.COORDINATION,
    label: '코디 활용',
    description: '다른 옷과 자연스럽게 입은 모습을 보여줘요.',
    sectionRole: SECTION_ROLES.FIT,
    cutType: 'styling', direction: 'front', shot: 'full', faceExposure: 'same',
  },
  {
    value: CONTENT_ROLES.FIT,
    label: '핏 확인',
    description: '깨끗한 배경에서 옷의 핏과 실루엣을 확인해요.',
    sectionRole: SECTION_ROLES.FIT,
    cutType: 'horizon', direction: 'front', shot: 'full', faceExposure: 'same',
  },
  {
    value: CONTENT_ROLES.REAL_WEAR,
    label: '실제 착용 느낌',
    description: '거울 앞에서 자연스럽게 입은 느낌을 보여줘요.',
    sectionRole: SECTION_ROLES.FIT,
    cutType: 'mirror', direction: null, shot: 'full', faceExposure: 'hide',
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
    description: '업로드한 디테일 사진에서 확인되는 부분만 가까이 보여줘요.',
    sectionRole: SECTION_ROLES.PRODUCT,
    cutType: 'product', direction: 'front', shot: 'detail', faceExposure: null,
    requiresDetailImage: true,
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
const FIT_ROLE_BY_CUT_TYPE = Object.freeze({
  styling: CONTENT_ROLES.COORDINATION,
  horizon: CONTENT_ROLES.FIT,
  mirror: CONTENT_ROLES.REAL_WEAR,
});

export const contentTemplate = (role) => TEMPLATE_BY_ROLE.get(role) || TEMPLATE_BY_ROLE.get(CONTENT_ROLES.CUSTOM);
export const contentTitle = (role) => contentTemplate(role).label;
export const sectionTitle = (role) => SECTION_TITLES[role] || '구성';
export const isSectionRole = (role) => VALID_SECTION_ROLES.has(role);
export const isContentRole = (role) => VALID_CONTENT_ROLES.has(role);
export const sectionRoleForContentRole = (role) => contentTemplate(role).sectionRole;
export const defaultContentRoleForSection = (sectionRole) =>
  contentTemplatesForSection(sectionRole, { hasDetailImage: false })[0]?.value || CONTENT_ROLES.CUSTOM;

export function poseExampleDirectionCompatible(example, { cutType, direction }) {
  if (!example || !['styling', 'horizon', 'mirror'].includes(cutType)) return false;
  if (cutType === 'mirror' || example.cutType === 'mirror') {
    return cutType === 'mirror' && example.cutType === 'mirror';
  }
  return ['styling', 'horizon'].includes(example.cutType)
    && ['front', 'back', 'side'].includes(example.direction)
    && example.direction === direction;
}

export function contentTemplatesForSection(sectionRole, { hasDetailImage = true } = {}) {
  return CONTENT_TEMPLATES.filter((template) => template.sectionRole === sectionRole
    && (!template.requiresDetailImage || hasDetailImage));
}

export function allAiContentTemplates({ hasDetailImage = true, includeHero = true } = {}) {
  return CONTENT_TEMPLATES.filter((template) => template.cutType
    && (includeHero || template.value !== CONTENT_ROLES.HERO)
    && (!template.requiresDetailImage || hasDetailImage));
}

export function inferSectionRole(block) {
  if (isSectionRole(block?.sectionRole)) return block.sectionRole;
  // EditorBlock은 같은 값을 `kind`에 저장한다.
  if (isSectionRole(block?.kind)) return block.kind;
  if (block?.cutType === 'product') return SECTION_ROLES.PRODUCT;
  if (['styling', 'horizon', 'mirror'].includes(block?.cutType)) return SECTION_ROLES.FIT;
  return null;
}

export function inferContentRole(block) {
  if (isContentRole(block?.contentRole)) return block.contentRole;
  if (block?.source === 'mine') return CONTENT_ROLES.CUSTOM;
  if (block?.cutType === 'mirror') return CONTENT_ROLES.REAL_WEAR;
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
      contentRole: CONTENT_ROLES.CUSTOM, title: template.label, exampleId: null,
    };
  }
  const worn = template.cutType === 'styling' || template.cutType === 'horizon' || template.cutType === 'mirror';
  return {
    source: 'ai',
    contentRole: template.value,
    title: template.label,
    cutType: template.cutType,
    direction: template.direction,
    shot: template.shot,
    faceExposure: template.faceExposure,
    pose: 'auto',
    poseLabel: 'AI 자동',
    angle: 'same',
    exampleId: null,
    outerClosureState: clothingType === 'outer' && worn
      ? (block?.outerClosureState || 'open') : null,
    ...(template.cutType === 'product' ? { matchIds: [] } : {}),
  };
}

/* 저장본을 읽을 때 사진 목적과 비노출 생성 레시피가 어긋나지 않게 맞춘다.
   방향·샷이 새 목적에서도 유효하면 사용자의 기존 선택을 보존하고, 유효하지
   않은 값만 목적의 기본값으로 되돌린다. */
export function normalizedRecipePatch(block, role, { hasDetailImage = null } = {}) {
  if (block?.source === 'mine') {
    return { contentRole: CONTENT_ROLES.CUSTOM, title: '내 이미지', cutType: null };
  }

  let nextRole = isContentRole(role) ? role : inferContentRole(block);
  if ([CONTENT_ROLES.COORDINATION, CONTENT_ROLES.FIT, CONTENT_ROLES.REAL_WEAR].includes(nextRole)
    && FIT_ROLE_BY_CUT_TYPE[block?.cutType]) {
    nextRole = FIT_ROLE_BY_CUT_TYPE[block.cutType];
  }
  if ([CONTENT_ROLES.PRODUCT_OVERVIEW, CONTENT_ROLES.DETAIL].includes(nextRole)
    && block?.cutType === 'product') {
    nextRole = block?.shot === 'detail' ? CONTENT_ROLES.DETAIL : CONTENT_ROLES.PRODUCT_OVERVIEW;
  }
  if (nextRole === CONTENT_ROLES.DETAIL
    && hasDetailImage === false) {
    nextRole = CONTENT_ROLES.PRODUCT_OVERVIEW;
  }

  const template = contentTemplate(nextRole);
  if (!template.cutType) {
    if (block?.cutType === 'mirror') {
      return {
        contentRole: nextRole, title: template.label, cutType: 'mirror', direction: null,
        shot: WORN_SHOTS.has(block?.shot) ? block.shot : 'full',
      };
    }
    if (block?.cutType === 'styling' || block?.cutType === 'horizon') {
      return {
        contentRole: nextRole, title: template.label, cutType: block.cutType,
        direction: WORN_DIRECTIONS.has(block?.direction) ? block.direction : 'front',
        shot: WORN_SHOTS.has(block?.shot) ? block.shot : 'full',
      };
    }
    return { contentRole: nextRole, title: template.label };
  }

  const cutType = [CONTENT_ROLES.HERO, CONTENT_ROLES.BENEFIT].includes(nextRole)
    && ['styling', 'horizon'].includes(block?.cutType)
    ? block.cutType : template.cutType;
  let direction = template.direction;
  let shot = template.shot;
  if (cutType === 'mirror') {
    direction = null;
    if (WORN_SHOTS.has(block?.shot)) shot = block.shot;
  } else if (cutType === 'product') {
    if (PRODUCT_DIRECTIONS.has(block?.direction)) direction = block.direction;
    if (nextRole === CONTENT_ROLES.DETAIL) shot = 'detail';
    else if (block?.shot === 'flatlay') shot = 'ghost';
    else if (PRODUCT_OVERVIEW_SHOTS.has(block?.shot)) shot = block.shot;
  } else {
    if (WORN_DIRECTIONS.has(block?.direction)) direction = block.direction;
    if (WORN_SHOTS.has(block?.shot)) shot = block.shot;
  }

  return {
    contentRole: nextRole,
    title: template.label,
    cutType,
    direction,
    shot,
    ...(cutType === 'mirror' && !['show', 'hide'].includes(block?.faceExposure)
      ? { faceExposure: 'hide' } : {}),
    ...(['styling', 'horizon'].includes(cutType)
      && !['same', 'show', 'hide'].includes(block?.faceExposure)
      ? { faceExposure: 'same' } : {}),
  };
}

/* contentRole은 사용자 입력값이 아니다. 기본 구성에서 받은 역할은 보존하되,
   잘못되거나 비어 있는 AI 카드는 현재 섹션의 안전한 기본 역할로 복구하고
   핵심 장점의 첫 AI 카드만 hero가 되도록 카드 순서에 맞춰 자동 배정한다.

   역할이 바뀌어 생성 레시피까지 달라질 때는 이전 생성예시를 그대로 쓰면
   조건이 어긋나므로 선택을 해제하고, 예시 적용 전 썸네일이 있으면 복원한다. */
export function assignInternalContentRoles(blocks) {
  if (!Array.isArray(blocks)) return blocks;
  let heroAssigned = false;
  let changed = false;
  const next = blocks.map((block) => {
    if (!block || block.source === 'mine') return block;

    const sectionRole = inferSectionRole(block) || SECTION_ROLES.BENEFIT;
    const currentRole = isContentRole(block.contentRole) ? block.contentRole : CONTENT_ROLES.CUSTOM;
    const currentRoleSection = sectionRoleForContentRole(currentRole);
    let role = currentRole;

    if (role === CONTENT_ROLES.CUSTOM || currentRoleSection !== sectionRole) {
      role = defaultContentRoleForSection(sectionRole);
    }
    if (sectionRole === SECTION_ROLES.BENEFIT) {
      if (!heroAssigned) {
        role = CONTENT_ROLES.HERO;
        heroAssigned = true;
      } else if (role === CONTENT_ROLES.HERO) {
        role = CONTENT_ROLES.BENEFIT;
      }
    }

    const recipePatch = normalizedRecipePatch(block, role);
    const recipeChanged = block.cutType !== recipePatch.cutType
      || block.direction !== recipePatch.direction
      || block.shot !== recipePatch.shot;
    const roleChanged = block.contentRole !== recipePatch.contentRole
      || block.title !== recipePatch.title
      || block.sectionRole !== sectionRole
      || block.taxonomyVersion !== STORYBOARD_TAXONOMY_VERSION;
    const productStateChanged = recipePatch.cutType === 'product'
      && ((block.matchIds || []).length > 0
        || block.outerClosureState != null
        || block.faceExposure != null);
    if (!recipeChanged && !roleChanged && !productStateChanged) return block;

    changed = true;
    return {
      ...block,
      ...recipePatch,
      sectionRole,
      taxonomyVersion: STORYBOARD_TAXONOMY_VERSION,
      ...(recipePatch.cutType === 'product'
        ? { matchIds: [], outerClosureState: null, faceExposure: null } : {}),
      ...(recipeChanged ? {
        exampleId: null,
        thumb: block.baseThumb || block.thumb,
        baseThumb: null,
      } : {}),
    };
  });
  return changed ? next : blocks;
}

export function hasDetailSource(product) {
  return (product?.colors || []).some((color) => (color?.images || []).some((image) => image?.slot === 'Detail'));
}

/* 불러온 EditorBlock의 이미지와 순서는 건드리지 않고 v2 표시 역할만 정규화한다. */
export function normalizeEditorBlockRole(block) {
  if (!block || block.auto || ['twocol', 'threecol', 'grid2x2', 'colorcmp', 'size', 'care', 'ai-notice'].includes(block.kind)) return block;
  const contentRole = inferContentRole(block);
  const sectionRole = inferSectionRole(block) || SECTION_ROLES.FIT;
  const name = block.name === '내 이미지' ? '내 이미지'
    : contentRole === CONTENT_ROLES.CUSTOM ? (block.name || contentTitle(contentRole))
      : contentTitle(contentRole);
  if (block.kind === sectionRole && block.contentRole === contentRole && block.name === name) return block;
  return { ...block, name, kind: sectionRole, contentRole };
}
