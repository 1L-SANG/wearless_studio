/* =============================================================
   features/editor/reviewGate.js — AI 편집 결과 사용 승인 게이트 (Phase 3 P0-C)

   wardrobe 이미지가 **상세페이지 데이터로 들어가는 모든 경로**가 지나는 한 곳.
   경로마다 정책을 복제하면 다음에 생기는 경로 하나가 조용히 우회로가 된다.

   React 밖의 순수 상태기계로 둔 이유: 이 정책의 버그는 "문자열이 있다/없다"가 아니라
   **전이**에서 난다(승인 실패했는데 반영됨, 승인 한 번에 두 번 반영됨, 닫힌 폼에 반영됨).
   그런 건 실제로 돌려 봐야 잡힌다.

   continuation(= 사용 목적)은 호출자가 만든다. 게이트는 그게 무엇인지 모른 채 보관만
   하고, 승인이 **기록된 뒤에** 정확히 한 번 실행한다. 게이트가 목적을 추측하면
   "캔버스에 넣기"와 "3번 사진 슬롯에 넣기"를 구분할 수 없다.
   ============================================================= */

/** 지금 이 이미지를 바로 써도 되는가. accepted 만 통과한다 — 거절을 뒤집으려면 새 승인. */
export function needsReviewBeforeUse(image) {
  return !!image?.needsReview && image?.reviewDecision !== 'accepted';
}

/**
 * @param record  (image, decision, reason) => Promise<boolean>  서버 기록. false = 실패
 * @param onChange (state|null) => void   UI 로 내보내는 상태 { image, busy }
 */
export function createReviewGate({ record, onChange = () => {} }) {
  let pending = null;      // { image, use }
  let busy = false;

  const emit = () => onChange(pending ? { image: pending.image, busy } : null);
  const clear = () => { const p = pending; pending = null; busy = false; emit(); return p; };

  const settle = async (decision, reason) => {
    if (!pending || busy) return false;      // 진행 중 중복 클릭은 무시
    busy = true; emit();
    const target = pending;
    let ok = false;
    try {
      ok = await record(target.image, decision, reason);
    } finally {
      // 요청 중 close() 로 사라졌다면 늦게 온 응답이 상태를 되살리지 않게 한다.
      if (pending === target) { busy = false; emit(); }
    }
    if (!ok || pending !== target) return false;
    clear();                                 // continuation 을 먼저 회수 → 두 번 실행 불가
    if (decision === 'accepted') target.use(target.image);
    return true;
  };

  return {
    /** 바로 쓸 수 있으면 즉시 실행. 아니면 검수를 열고 목적을 보관. → 검수가 열렸는가 */
    request(image, use) {
      if (!needsReviewBeforeUse(image)) { use(image); return false; }
      pending = { image, use }; busy = false; emit();
      return true;
    },
    /** 승인 기록이 성공한 **뒤에만** continuation 실행. */
    accept(reason) { return settle('accepted', reason); },
    /** 거절도 기록이다. continuation 은 실행하지 않고 폐기한다(이미지는 지우지 않는다). */
    reject(reason) { return settle('rejected', reason); },
    /** 닫기 = 취소. 기록 중에는 닫히지 않는다(승인만 남고 반영이 안 되는 상태 방지). */
    close() { if (busy) return false; clear(); return true; },
    get pendingImage() { return pending ? pending.image : null; },
    get isBusy() { return busy; },
  };
}

/**
 * 폼 안에서 만든 continuation 의 수명 관리.
 *
 * 검수는 비동기라 그 사이에 폼이 닫히거나(unmount) 대상 슬롯이 바뀔 수 있다. 그때
 * 승인이 성공했다고 해서 이미 없어진 대상에 값을 쓰면 안 된다 — 사용자가 보는 화면과
 * 저장되는 데이터가 어긋난다.
 */
export function createContinuationSlot() {
  let token = null;
  let alive = true;
  return {
    /** 요청 시점의 대상을 고정한 표를 발급한다. 새 요청은 앞의 표를 무효화한다. */
    claim(target) { token = { target }; return token; },
    /** 표가 아직 유효할 때만 1회 실행. → 실행했는가 */
    run(claimed, apply) {
      if (!alive || token !== claimed) return false;
      token = null;                          // 1회성 — 중복 호출은 no-op
      apply(claimed.target);
      return true;
    },
    /** 폼 unmount. 이후의 어떤 continuation 도 적용되지 않는다. */
    dispose() { alive = false; token = null; },
    get pendingTarget() { return token ? token.target : null; },
  };
}
