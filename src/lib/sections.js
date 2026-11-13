import { validateStoryboardV4 } from './pageTemplates/validateStoryboard.js';
import { inheritSimilarCutRecipes } from './storyboardSimilarCuts.js';
import { referenceScopeForBlock } from './referenceMode.js';
/* =============================================================
   lib/sections — 콘티보드 섹션 파생 유틸 (2026-07 역할 중심 개편)
   섹션은 별도 엔티티가 아니라 블록 필드(sectionId/Title/Layout)의
   "연속 run"으로만 존재한다 — 저장 계약(blocks: list)은 그대로.
   v4 섹션은 템플릿 인스턴스이며 sectionRole로 묶거나 재정렬하지 않는다.
   ============================================================= */
import { assignInternalContentRoles, productPresentationOf } from './storyboardTaxonomy.js';
import { templateSectionName } from './pageTemplates/slots.js';
import { pageTemplatesCatalog } from './pageTemplates/catalog.js';

export const titleForKey = (key) => pageTemplatesCatalog.kinds[key]?.name || '섹션';

export function ensureSections(blocks) {
  if (!Array.isArray(blocks)) return blocks;
  validateStoryboardV4(blocks);
  const out = assignInternalContentRoles(blocks).map(raw => {
    const b = { ...raw, sectionLayout: 'stack' };
    for (const key of Object.keys(b)) {
      if (/^(hook|layoutRow|colorway)/.test(key) || ['pose', 'poseLabel', 'poseThumb', 'kind'].includes(key)) delete b[key];
    }
    if (!productPresentationOf(b)) delete b.productPresentation;
    if (b.source === 'ai' && (Object.hasOwn(b, 'refScope') || Object.hasOwn(b, 'ref_scope') || b.spaceGroupId)) b.refScope = referenceScopeForBlock(b);
    delete b.ref_scope;
    return b;
  });
  return inheritSimilarCutRecipes(out);
}

/* deriveSections(blocks) — 연속 run 파생. 메타는 run 첫 블록 기준.
   반환: [{ id, title, layout, custom, samePlace, start, items:[{ b, i }] }] */
export function deriveSections(blocks) {
  const secs = [];
  let cur = null;
  (blocks || []).forEach((b, i) => {
    if (!cur || cur.id !== (b.sectionId || '_none')) {
      cur = {
        id: b.sectionId || '_none',
        title: templateSectionName(b),
        pageSectionKey: b.pageSectionKey, kind: b.pageSectionKind, usage: b.pageSectionUsage,
        designId: b.pageDesignId,
        role: b.sectionRole || null,
        layout: b.sectionLayout || 'stack',
        custom: !!b.sectionCustom,
        samePlace: !!b.spaceGroupId,
        spaceGroupId: b.spaceGroupId || null,
        start: i, items: [],
      };
      secs.push(cur);
    }
    cur.custom = cur.custom || !!b.sectionCustom;
    cur.samePlace = cur.samePlace && !!b.spaceGroupId; // 전원이 같은 공간일 때만 배지
    cur.items.push({ b, i });
  });
  return secs.map(section => {
    const photo = section.items.find(({ b }) => b.source === 'ai')?.b;
    return { ...section, role: photo?.cutType === 'horizon' ? 'studio' : photo?.cutType === 'styling' ? 'styling' : photo?.cutType === 'product' ? 'product' : null };
  });
}

/* adoptSection(blocks, movedId|movedIds) — 이동한 블록(들)이 이웃 run 안/경계에 놓였을 때
   그 run 의 섹션을 채택 + 대상 섹션을 '직접 구성'(sectionCustom) 처리.
   반환: 새 배열 (변경 없으면 동일 참조 유지 아님 — 호출부에서 setBlocks 로 사용) */
export function adoptSection(blocks, movedId, targetSid) {
  const ids = new Set(Array.isArray(movedId) ? movedId : [movedId]);
  const moved = blocks.filter(block => ids.has(block.id));
  if (!moved.length || moved.some(block => block.source === 'text')) return blocks;
  // Page order and section membership belong to the template. Only reorder inside a run.
  if (targetSid && moved.some(block => block.sectionId !== targetSid)) return blocks;
  return blocks;
}

// v4 has a single stack run per template instance; designs own visual rows.
export const normalizeBoard = ensureSections;
