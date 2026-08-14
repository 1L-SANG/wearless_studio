/* =============================================================
   customMatchPromotion — draft(비프로젝트) 단계에서 추가한 "내 옷"을
   확정 승격 시 실서버에 등록한다.

   입력 페이지는 로그인 전이라 내 옷이 로컬 blob 카드로만 존재한다. 확정 때 사진·상품·
   분석만 승격하고 이걸 빼먹으면 서버 asset 이 없어 누끼(matching_cutout)·flat-lay 가
   돌 대상 자체가 없다(2026-08-14 QA 실측: custom-match-item 호출 0건, 원본 썸네일 고착).

   실패는 fail-open — 내 옷 없이도 프로젝트는 유효하고 분석 화면에서 다시 추가할 수 있다.
   409(이미 있음)는 재시도 합류이므로 조용히 무시한다.
   ============================================================= */

export async function promoteCustomMatch(api, projectId, customDraft) {
  if (!customDraft?.uploads?.length) return { promoted: false };
  try {
    const assetIds = [];
    for (const up of customDraft.uploads) {
      const uploaded = await api.uploadPhoto(projectId, {
        filename: up.filename, mime: up.mime, blob: up.blob,
        purpose: 'custom_match_source',
      });
      assetIds.push(uploaded.assetId);
    }
    const result = await api.addCustomMatchItem(projectId, { assetIds });
    // draft 에서 선택돼 있었으면 승격본(새 서버 id)도 선택 상태로 저장한다 —
    // 아니면 "선택했는데 확정하니 풀려 있음"이 된다.
    const promoted = (result?.analysis?.matchClothing || []).find((m) => m.isCustom);
    if (customDraft.selected && promoted) {
      await api.saveAnalysis(projectId, {
        ...result.analysis,
        matchClothing: result.analysis.matchClothing.map((m) => (
          m.id === promoted.id
            ? { ...m, selected: true, selOrder: 1 }
            : { ...m, selected: false, selOrder: undefined }
        )),
      });
    }
    return { promoted: true, itemId: promoted?.id ?? null };
  } catch (err) {
    if (err?.status !== 409) {
      console.warn('custom match promotion failed (확정은 유지)', err);
    }
    return { promoted: false, error: err };
  }
}

// analysis payload 승격 전에 로컬 커스텀 항목을 걷어낸다 — objectURL·로컬 id 가
// 서버 payload 에 박히면 죽은 링크가 된다. 정식 항목은 서버 등록이 다시 넣는다.
export function stripLocalCustomMatch(analysis) {
  if (!analysis || !Array.isArray(analysis.matchClothing)) return analysis;
  return {
    ...analysis,
    matchClothing: analysis.matchClothing.filter((m) => !m.isCustom),
  };
}
