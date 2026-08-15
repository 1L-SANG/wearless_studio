/* =============================================================
   lib/generationCutCount — 크레딧 견적용 "실제 생성 수" (ADR-0011)

   생성 계약이 완전히 같은 복제 컷은 서버가 1장만 생성해 결과를 복사한다
   (detail_page_job._duplicate_source_indexes). 크레딧 표시·사전 차단도 같은
   수를 봐야 한다 — 복제가 많은 보드에서 "크레딧 부족"으로 잘못 막히거나
   (마네킹 CTA), 목 모드가 과다 청구하는 것을 막는다(Codex PR#142 리뷰).

   키는 서버 정규화 스펙의 근사다: 여기 비교 필드가 서버보다 엄격해도
   '컷 복제' 직후의 완전 동일 복제는 확실히 접히고, 필드가 하나라도 다른
   컷은 서버도 따로 생성하므로 어긋나지 않는다. 세트 멤버는 접지 않는다.
   ============================================================= */

export function uniqueGenerationCutCount(blocks) {
  const seen = new Set();
  let count = 0;
  for (const block of (Array.isArray(blocks) ? blocks : [])) {
    if (!block || block.source === 'mine') continue;
    if (block.spaceGroupId) { count += 1; continue; }
    const key = JSON.stringify({
      cutType: block.cutType ?? null,
      shot: block.shot ?? null,
      direction: block.direction ?? null,
      colorId: block.colorId ?? null,
      colorIds: block.colorIds || [],
      exampleId: block.exampleId ?? null,
      pose: block.pose ?? 'auto',
      refScope: block.refScope ?? 'all',
      matchIds: [...(block.matchIds || [])].sort(),
      faceExposure: block.faceExposure ?? null,
      angle: block.angle ?? null,
      outerClosureState: block.outerClosureState ?? null,
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
