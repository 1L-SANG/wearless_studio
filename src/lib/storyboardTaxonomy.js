/* =============================================================
   콘티보드 사용자 분류의 단일 소스.

   사용자는 상세페이지에서 사진이 맡는 역할을 고른다:
   핵심 장점 → 핏·코디 → 제품 확인.

   cutType은 AI가 사진을 그릴 때만 쓰는 기술 레시피다. 화면에서는
   노출하지 않고 contentRole 선택에서 자동으로 정한다.
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

export const contentTemplate = (role) => TEMPLATE_BY_ROLE.get(role) || TEMPLATE_BY_ROLE.get(CONTENT_ROLES.CUSTOM);
export const contentTitle = (role) => contentTemplate(role).label;
export const sectionTitle = (role) => SECTION_TITLES[role] || '구성';
export const isSectionRole = (role) => VALID_SECTION_ROLES.has(role);
export const isContentRole = (role) => VALID_CONTENT_ROLES.has(role);
export const sectionRoleForContentRole = (role) => contentTemplate(role).sectionRole;
export const defaultContentRoleForSection = (sectionRole) =>
  contentTemplatesForSection(sectionRole, { hasDetailImage: false })[0]?.value || CONTENT_ROLES.CUSTOM;

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

  let direction = template.direction;
  let shot = template.shot;
  if (template.cutType === 'mirror') {
    direction = null;
    if (WORN_SHOTS.has(block?.shot)) shot = block.shot;
  } else if (template.cutType === 'product') {
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
    cutType: template.cutType,
    direction,
    shot,
    ...(template.cutType === 'mirror' && !['show', 'hide'].includes(block?.faceExposure)
      ? { faceExposure: 'hide' } : {}),
  };
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
