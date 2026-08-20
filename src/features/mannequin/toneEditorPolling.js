// 마스크 상태 폴링은 UI와 분리해 네트워크 실패·타이머 동작을 브라우저 없이 검증한다.
export const POLL_MS = 4_000;
// 최악 활주로: SAM 4회 × 90초 + 서버 백오프(15+60+120초) + 폴 정렬 여유 24초
// = 579초. 145폴 × 4초 = 580초로 마지막 세대가 끝날 때까지 상태 조회를 유지한다.
export const POLL_LIMIT = 145;
// 세 번까지는 일시적인 조회 장애로 보고 계속한다. 네 번째 연속 실패에서만 중단한다.
export const MAX_CONSECUTIVE_FAILURES = 3;

export function startToneEditorPolling({
  fetchState,
  onState,
  onFailed,
  schedule = setTimeout,
  cancelSchedule = clearTimeout,
  pollMs = POLL_MS,
  pollLimit = POLL_LIMIT,
}) {
  let active = true;
  let attempts = 0;
  let consecutiveFailures = 0;
  let timer;

  const queueNext = (tick) => {
    if (active && attempts < pollLimit) timer = schedule(tick, pollMs);
  };

  const tick = async () => {
    attempts += 1;
    try {
      const next = await fetchState();
      if (!active) return;
      consecutiveFailures = 0;
      onState(next);
      if (next?.status === 'processing') queueNext(tick);
    } catch {
      if (!active) return;
      consecutiveFailures += 1;
      if (consecutiveFailures > MAX_CONSECUTIVE_FAILURES) {
        onFailed();
        return;
      }
      queueNext(tick);
    }
  };

  tick();
  return () => {
    active = false;
    cancelSchedule(timer);
  };
}
