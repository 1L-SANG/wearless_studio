/* =============================================================
   lib/detailPageJobFailure — 상세페이지 잡의 부분 실패(incomplete_cuts) 해석(순수 함수, 2026-09-28).

   오너 결정 B(#420): 한 컷이라도 실패하면 페이지를 내보내지 않고 예약 전액을 푼다 — 그대로다.
   다만 20장 중 1장이 실패했는데 "이미지를 모두 만들지 못했어요"로 보이면 거짓이다. 서버는 이제
   실패 잡의 result 에 몇 장 중 무엇이 실패·완성·보관됐는지 싣는다(블록 id·개수뿐, 저장소 키 없음).
   완성 컷은 체크포인트(#394)로 24시간 보관되고, 같은 콘티로 새 잡을 돌리면 그대로 싣는다.

   여기서 하는 일:
     · incompleteCutsOf(job)       서버 잡 → 부분 실패 요약(없으면 null)
     · incompleteFailurePatch(job) store 실패 종결에 덧씌울 값 — 완성 컷은 '완성됐어요' 표식만
       (주소 없음: 1h 서명 주소의 출력 객체는 서버가 이미 지웠다. 그림은 미리보기 라우트가
       보관본에서 요청마다 소유·라이선스를 확인해 준다), 실패 컷은 실패 표식
     · retryOfIncomplete(job)      [실패한 컷만 다시 만들기]가 새 잡에 넘길 비교 기준
     · retryReuseHint(job)         다시 시도 중 "보관된 N장은 그대로 써요"(콘티가 바뀌었으면 사실대로)
   ============================================================= */

export const INCOMPLETE_CUTS = 'incomplete_cuts';

const ids = (value) => (Array.isArray(value) ? value.map((v) => String(v)) : []);

/** 보관 기한이 지났는가 — 서버가 준 retainedUntil(보관본이 적어도 남아 있는 시각) 기준. 없으면 false. */
export function retentionExpired(retainedUntil, now = Date.now()) {
  if (!retainedUntil) return false;
  const until = Date.parse(retainedUntil);
  return Number.isFinite(until) && now >= until;
}

/** 서버 잡(GET /v1/jobs/{id}) → 부분 실패 요약. 부분 실패가 아니면 null. */
export function incompleteCutsOf(job, now = Date.now()) {
  const result = job?.result;
  if (job?.status !== 'error' || result?.errorCode !== INCOMPLETE_CUTS) return null;
  const failed = ids(result.failedBlockIds);
  const completed = ids(result.completedBlockIds);
  const expired = retentionExpired(result.retainedUntil, now);
  // 기한이 지난 보관본은 없는 것으로 센다 — "보관돼 있어요"라고 말하지 않는다.
  const retained = expired ? [] : ids(result.retainedBlockIds).filter((id) => completed.includes(id));
  const requested = Number.isFinite(result.requestedCuts)
    ? result.requestedCuts : failed.length + completed.length;
  return {
    requested,
    failed,
    completed,
    retained,
    expired,
    retainedUntil: result.retainedUntil || null,
    storyboardDigest: result.storyboardDigest || null,
  };
}

/** store 실패 종결에 덧씌울 값. 부분 실패가 아니면 {incomplete: null}(=오늘처럼 전부 비운다). */
export function incompleteFailurePatch(job, now = Date.now()) {
  const incomplete = incompleteCutsOf(job, now);
  if (!incomplete) return { incomplete: null };
  return {
    incomplete,
    // 완성 컷 자리는 '완성됐어요' 표식만 남긴다 — 주소(url)는 싣지 않는다.
    cuts: Object.fromEntries(incomplete.completed.map((id) => [id, { url: null, done: true }])),
    failedCuts: incomplete.failed,
  };
}

/** 셀러에게 보일 부분 실패 문구. 서버 문구가 정본이고, 보관 기한이 지났으면 그 사실로 바꾼다. */
export function incompleteMessage(incomplete, serverMessage) {
  if (!incomplete) return serverMessage || '';
  if (incomplete.expired) {
    return `${incomplete.requested}장 중 ${incomplete.failed.length}장을 만들지 못했어요. `
      + '완성된 컷의 보관 기간(24시간)이 지나 다시 만들면 모든 컷을 새로 만들어요. 이번 시도는 크레딧이 차감되지 않았어요.';
  }
  return serverMessage || '';
}

/** "19장 완성 · 1장 미완성" */
export function incompleteSummary(incomplete) {
  if (!incomplete) return '';
  return `${incomplete.completed.length}장 완성 · ${incomplete.failed.length}장 미완성`;
}

/** [실패한 컷만 다시 만들기] — 새 잡이 앞선 실패와 같은 콘티로 도는지 대조할 기준. */
export function retryOfIncomplete(job) {
  const incomplete = job?.incomplete;
  if (!incomplete) return null;
  return {
    jobId: job.jobId || null,
    retained: incomplete.retained.length,
    failed: incomplete.failed.length,
    storyboardDigest: incomplete.storyboardDigest,
  };
}

/** 다시 시도 중 진행 문구 옆 한 줄. 보관본이 없으면 ''.
    · 콘티 지문이 다르면(셀러가 콘티를 고침) 보관본을 못 쓸 수 있다고 사실대로 말한다.
    · 컷 단계가 끝나면 서버가 실제로 이어 쓴 수(cut_checkpoint_reused)로 말한다. */
export function retryReuseHint(job) {
  const retryOf = job?.retryOf;
  if (!retryOf || !retryOf.retained) return '';
  if (job.storyboardDigest && retryOf.storyboardDigest
      && job.storyboardDigest !== retryOf.storyboardDigest) {
    return '콘티가 바뀌어 보관된 컷을 그대로 쓰지 못할 수 있어요';
  }
  const reused = (job.reusedCuts || []).length;
  const cutsFinished = job.phase === 'assemble'
    || (job.cutsTotal > 0 && job.cutsDone >= job.cutsTotal);
  // 복제 컷은 원본 1장만 이어 쓰므로 이벤트 수는 블록 수보다 적을 수 있다 — 수 대신 사실만 말한다.
  if (cutsFinished) return reused ? '보관된 컷을 그대로 썼어요' : '보관된 컷을 쓰지 못해 새로 만들었어요';
  return `보관된 ${retryOf.retained}장은 그대로 써요`;
}
