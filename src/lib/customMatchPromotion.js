import { matchingIdsForColor } from './colorwayMatching.js';

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
  if (!customDraft?.uploads?.length) return { promoted: false, attempted: false };
  try {
    // Promise.all 은 결과 배열을 입력 map 순서로 돌려주므로, 업로드 완료 순서와 무관하게
    // set_custom_match_source_order 가 읽는 assetIds 는 셀러가 올린 사진 순서를 유지한다.
    const assetIds = await Promise.all(customDraft.uploads.map(async (up) => {
      const uploaded = await api.uploadPhoto(projectId, {
        filename: up.filename, mime: up.mime, blob: up.blob,
        purpose: 'custom_match_source',
      });
      return uploaded.assetId;
    }));
    const result = await api.addCustomMatchItem(projectId, { assetIds });
    // draft 에서 선택돼 있었으면 승격본(새 서버 id)도 선택 상태로 저장한다 —
    // 아니면 "선택했는데 확정하니 풀려 있음"이 된다.
    //
    // **패치는 선택 델타만** 보낸다. analysis 전체를 patch 로 넘기면 서버 어댑터가
    // 이걸 '추천 갱신'으로 보고 matchClothing 을 통째 머지하는데, 승격 payload 에는
    // clothingType 이 빠져 있어 보완 타입이 bottom 으로 굳는다 — 하의 상품에서는 방금
    // 승격한 내 옷까지 selected:false 로 저장된다(리뷰 확정 결함).
    const promoted = (result?.analysis?.matchClothing || []).find((m) => m.isCustom);
    if (customDraft.selected && promoted) {
      await api.saveAnalysis(projectId, {
        matchClothing: [{ id: promoted.id, selected: true, selOrder: 1 }],
      });
    }
    return { promoted: true, attempted: true, itemId: promoted?.id ?? null };
  } catch (err) {
    if (err?.status !== 409) {
      console.warn('custom match promotion failed (확정은 유지)', err);
    }
    // 409(이미 등록됨)는 재시도 합류라 실패가 아니다 — 경고를 띄우지 않는다.
    return { promoted: err?.status === 409, attempted: true, error: err };
  } finally {
    // 성공·실패·409 어느 경로든 이 draft 의 승격 키는 소비됐다. 남겨 두면 같은 탭에서
    // 다음 프로젝트를 만들 때 이전 프로젝트의 blob 이 다시 등록된다(리뷰 확정 결함 —
    // 커스텀 슬롯까지 점유해 진짜 내 옷 추가가 409 로 막힌다).
    api.clearCustomMatchDraft?.();
  }
}

// 같은 SPA 안에서 입력 화면이 시작한 승격 프라미스를 콘티보드가 직접 구독한다.
// 서버 폴링을 새로 만들지 않고, 완료된 task 도 현재 세션 동안 남겨 콘티가 조금 늦게
// 마운트되어도 성공/실패 결과를 놓치지 않게 한다.
const promotionTasks = new Map();

export function startCustomMatchPromotion(api, projectId, customDraft, { onSettled } = {}) {
  if (!projectId || !customDraft?.uploads?.length) return null;
  const current = promotionTasks.get(projectId);
  if (current?.status === 'pending') return current;

  const task = { status: 'pending', promise: null };
  task.promise = promoteCustomMatch(api, projectId, customDraft)
    .finally(() => onSettled?.(projectId))
    .then((result) => {
      task.status = 'settled';
      task.result = result;
      return result;
    }, (error) => {
      task.status = 'settled';
      task.error = error;
      throw error;
    });
  promotionTasks.set(projectId, task);
  return task;
}

export function getCustomMatchPromotionTask(projectId) {
  return projectId ? promotionTasks.get(projectId) ?? null : null;
}

// 테스트와 새 프로젝트 경계에서만 쓰는 명시적 정리 함수. 진행 중 요청을 취소하지는 않는다.
export function clearCustomMatchPromotionTask(projectId) {
  if (projectId) promotionTasks.delete(projectId);
}

// 승격 전에 만들어진 기본 콘티는 이전 추천 의류 id를 들고 있다. 셀러가 인스펙터에서 직접
// 고른 컷은 보존하고, 자동 배정 컷만 최신 analysis 선택으로 다시 맞춘다.
export function applyPromotedMatchSelection(blocks, colors, matchClothing) {
  if (!Array.isArray(blocks)) return blocks;
  const colorList = Array.isArray(colors) ? colors : [];
  const baseColor = colorList.find((color) => color.isBase) || colorList[0] || null;
  const colorById = new Map(colorList.map((color) => [color.id, color]));
  let changed = false;
  const next = blocks.map((block) => {
    if (!['styling', 'horizon', 'mirror'].includes(block.cutType)
      || block.source === 'mine' || block.matchIdsOrigin === 'user') return block;
    const color = colorById.get(block.colorId) || baseColor;
    const matchIds = matchingIdsForColor(color, matchClothing, {
      preferMain: Boolean(color && baseColor && color.id === baseColor.id),
    });
    const current = Array.isArray(block.matchIds) ? block.matchIds : [];
    if (current.length === matchIds.length && current.every((id, index) => id === matchIds[index])) {
      return block;
    }
    changed = true;
    return { ...block, matchIds };
  });
  return changed ? next : blocks;
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
