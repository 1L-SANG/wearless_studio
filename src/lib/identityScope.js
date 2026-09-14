import SCOPES from '../data/identityScopes.json' with { type: 'json' };
import { inferSectionRole } from './storyboardTaxonomy.js';

/**
 * 컷을 어떤 모델로 만들 수 있는가 — **판정 규칙은 서버에 있다**.
 * 이 파일은 서버(server/app/agents/identity_scope.py)가 내보낸 규칙 데이터를 평가만 한다.
 * 규칙과 대조 케이스는 server/scripts/gen_identity_scopes.py 가 만들고, 평가 결과가
 * 서버와 갈라지면 tests/frontend/identity-scope.test.mjs 가 깨진다.
 */
export const IDENTITY_SCOPES = Object.freeze({ VIRTUAL: 'virtual', REAL: 'real', BOTH: 'both' });

const RULES = SCOPES.rules || [];
const SPACE_SETS = SCOPES.spaceSets || {};
export const IDENTITY_SCOPE_CASES = SCOPES.cases || [];

const listHas = (list, value) => !Array.isArray(list) || list.includes(String(value ?? ''));

function matches(when, block) {
  if (!when) return false;
  if (when.sectionNotIn) {
    // 섹션을 못 알아내면 막지 않는다 — 서버(identity_scope.studio_only_block)와 같은 규칙.
    const section = inferSectionRole(block);
    if (!section || when.sectionNotIn.includes(section)) return false;
    return true;
  }
  const spaceSetId = block.spaceSetId || block.setId
    || (block.spaceGroupId ? String(block.spaceGroupId) : null);
  if (when.cutType && String(block.cutType || '') !== when.cutType) return false;
  if (when.direction && String(block.direction || '') !== when.direction) return false;
  if (when.shot && !when.shot.includes(String(block.shot || ''))) return false;
  if (when.refScope && String(block.refScope || '') !== when.refScope) return false;
  if (when.pose && String(block.pose || '') !== when.pose) return false;
  if (when.noSpaceSet && spaceSetId) return false;
  if (when.spaceSetIn) {
    // 세트 id 는 그룹 id 안에 들어 있다(ssg1__<setId>__sg_x) — 부분 일치로 본다.
    const raw = String(spaceSetId || '');
    if (!raw || !when.spaceSetIn.some((id) => raw.includes(id))) {
      if (!listHas(when.exampleIn, block.exampleId)) return false;
      if (!raw) return false;
    }
  }
  if (when.exampleIn && !when.spaceSetIn && !listHas(when.exampleIn, block.exampleId)) return false;
  return true;
}

/** 막힌 이유(코드·문구). 서버가 규칙과 함께 내보낸 값을 그대로 쓴다 — 문구를 프런트에서 짓지 않는다. */
export function rejectionOfBlock(block, identityKind) {
  if (!block || typeof block !== 'object') return null;
  for (const rule of RULES) {
    if (!matches(rule.when, block)) continue;
    if (scopeAllows(rule.scope, identityKind)) return null;
    return rule.code ? { code: rule.code, message: rule.message } : null;
  }
  return null;
}

/** 이 섹션에 이 모델의 컷을 넣을 수 있는가 — 막혔으면 {code,message}. */
export function rejectionOfSection(sectionRole, identityKind) {
  return rejectionOfBlock({ sectionRole }, identityKind);
}

/** 이 모델로 실제 생성될 블록만 남긴다 — 서버 예약 필터(routes.generate_detail_page)와 같은 규칙. */
export function blocksForModel(blocks, identityKind) {
  if (!identityKind) return blocks || [];
  return (blocks || []).filter(
    (block) => block?.source === 'mine' || blockAllowedForModel(block, identityKind),
  );
}

/** 이 컷의 범위. 규칙에 안 걸리면 both(막지 않는다). */
export function scopeOfBlock(block) {
  if (!block || typeof block !== 'object') return IDENTITY_SCOPES.BOTH;
  for (const rule of RULES) {
    if (matches(rule.when, block)) return rule.scope;
  }
  return IDENTITY_SCOPES.BOTH;
}

export function scopeOfSpaceSet(setId) {
  return SPACE_SETS[String(setId || '')] || IDENTITY_SCOPES.BOTH;
}

/** 선택한 모델 종류(real|virtual). */
export function identityKindOf(isRealModel) {
  return isRealModel ? IDENTITY_SCOPES.REAL : IDENTITY_SCOPES.VIRTUAL;
}

export function scopeAllows(scope, identityKind) {
  const value = scope || IDENTITY_SCOPES.BOTH;
  if (identityKind === IDENTITY_SCOPES.REAL) {
    return value === IDENTITY_SCOPES.REAL || value === IDENTITY_SCOPES.BOTH;
  }
  if (identityKind === IDENTITY_SCOPES.VIRTUAL) {
    return value === IDENTITY_SCOPES.VIRTUAL || value === IDENTITY_SCOPES.BOTH;
  }
  return true;              // 모델 종류를 모르면 아무것도 막지 않는다
}

export function blockAllowedForModel(block, identityKind) {
  return scopeAllows(scopeOfBlock(block), identityKind);
}

/** 고를 수 있는 예시만 남긴다 — 컷 모양이 판정에 들어가므로 블록 맥락을 함께 받는다. */
export function filterExamplesForModel(examples, identityKind, blockShape = null) {
  if (!identityKind) return examples || [];
  return (examples || []).filter((item) => blockAllowedForModel(
    { ...(blockShape || {}), exampleId: item?.id }, identityKind,
  ));
}

/** 고를 수 있는 공간 세트만 남긴다. */
export function filterSpaceSetsForModel(sets, identityKind) {
  if (!identityKind) return sets || [];
  return (sets || []).filter((set) => scopeAllows(scopeOfSpaceSet(set?.id), identityKind));
}
