/* =============================================================
   lib/generationCutCount — 크레딧 견적용 "실제 생성 수" (ADR-0011)

   생성 계약이 완전히 같은 복제 컷은 서버가 1장만 생성해 결과를 복사한다
   (detail_page_job._duplicate_source_indexes). 크레딧 표시·사전 차단도 같은
   수를 봐야 한다 — 복제가 많은 보드에서 "크레딧 부족"으로 잘못 막히거나
   (마네킹 CTA), 목 모드가 과다 청구하는 것을 막는다(Codex PR#142 리뷰).

   아우터 열림은 서버가 생성 직전에 맞추는 값으로 비교한다. 저장 블록은
   바꾸지 않으며 확정 GPT 요청과 세트 멤버, 비슷한 컷의 예외를 유지한다.
   ============================================================= */

import scopes from '../data/identityScopes.json' with { type: 'json' };

const CLOSURES = ['closed', 'openToChest', 'partial', 'openToWaist', 'open'];
const WORN_CUTS = new Set(['styling', 'horizon', 'mirror']);
const EXCLUDED_EXAMPLES = new Set(scopes.confirmedGptExcludedExampleIds || []);

function closureRange(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)
      || !['version', 'kind', 'range', 'secondary', 'evidenceSlots', 'note'].every(key => key in value)
      || value.version !== 1) return 'graded';
  const ranges = { zipper: ['graded'], unknown: ['graded'], belt: ['binary'], none: [null],
    button: ['graded', 'binary'], snap: ['graded', 'binary'] };
  if (!Object.hasOwn(ranges, value.kind) || !ranges[value.kind].includes(value.range)) return 'graded';
  if (value.secondary !== null && (!['zipper', 'button', 'snap', 'belt'].includes(value.secondary)
      || value.secondary === value.kind || ['none', 'unknown'].includes(value.kind))) return 'graded';
  if (!Array.isArray(value.evidenceSlots)
      || value.evidenceSlots.some(slot => !['Front', 'Back', 'Detail', 'BackDetail'].includes(slot))
      || (value.kind === 'unknown' && value.evidenceSlots.length)) return 'graded';
  if (value.note !== null && (typeof value.note !== 'string' || value.note.length > 80)) return 'graded';
  return value.kind === 'none' ? 'none' : value.range;
}

function confirmedProfileRequested(block) {
  return block.cutType === 'styling' && (block.direction || 'front') === 'front'
    && (!block.shot || ['full', 'medium'].includes(block.shot))
    && (!block.refScope || block.refScope === 'all') && !block.spaceGroupId
    && block._referenceDirectionCompatible !== false && Boolean(block.exampleId)
    && !EXCLUDED_EXAMPLES.has(block.exampleId);
}

function effectiveClosure(block, clothingType, frontClosure) {
  if (clothingType == null) return block.outerClosureState ?? null;
  if (clothingType !== 'outer' || !WORN_CUTS.has(block.cutType)) return null;
  const state = CLOSURES.includes(block.outerClosureState) ? block.outerClosureState : 'open';
  if (confirmedProfileRequested(block)) return state;
  const range = closureRange(frontClosure);
  if (range === 'none') return 'open';
  if (range === 'binary') return CLOSURES.indexOf(state) <= 2 ? 'closed' : 'open';
  return state;
}

export function uniqueGenerationCutCount(blocks, { clothingType, frontClosure } = {}) {
  const seen = new Set();
  let count = 0;
  for (const block of (Array.isArray(blocks) ? blocks : [])) {
    if (!block || block.source === 'mine') continue;
    if (block.spaceGroupId || block.similarSourceBlockId) { count += 1; continue; }
    const key = JSON.stringify({
      cutType: block.cutType ?? null,
      shot: block.shot ?? null,
      detailTargetId: block.detailTargetId ?? null,
      direction: block.direction ?? null,
      colorId: block.colorId ?? null,
      colorIds: block.colorIds || [],
      exampleId: block.exampleId ?? null,
      pose: block.pose ?? 'auto',
      refScope: block.refScope ?? 'all',
      matchIds: [...(block.matchIds || [])].sort(),
      faceExposure: block.faceExposure ?? null,
      angle: block.angle ?? null,
      outerClosureState: effectiveClosure(block, clothingType, frontClosure),
      refAssetIds: block.refAssetIds || [],
      refImages: block.refImages || [],
      contentRole: block.contentRole ?? null,
      sectionRole: block.sectionRole ?? null,
    });
    if (seen.has(key)) continue;
    seen.add(key);
    count += 1;
  }
  return count;
}
