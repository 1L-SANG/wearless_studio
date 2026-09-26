/* =============================================================
   lib/detailPageJobPoll — 상세페이지 생성 잡 폴링 루프(의존성 주입 · node 테스트 가능).

   2026-09-26 규칙: **서버 잡이 살아 있는 동안 화면은 포기하지 않는다.**
   예전에는 시작 15분에 화면이 'error'를 띄우고 폴링을 멈췄다. 실측(잡 2240c252, 20컷)은
   15분 7초에 끝났다 — 화면이 7초 먼저 포기해, 서버가 19컷을 저장했는데도 셀러는
   "예상보다 오래 걸린다" 배너와 빈 캔버스만 봤다. 셀러는 서버가 살아 있는지 알 방법이
   없으므로(오너: "알 수 있는 방법이 없는데 중간에 멈추면 어떡해"), 판단은 서버에 맡긴다:
     · pending/running → 계속 본다. 15분이 지나면 주기만 5초로 늦춘다(slow 표식).
     · done / error / cancelled → 그 결과로 끝낸다(error 는 서버 errorMessage 그대로).
     · 잡이 없다(404 등 일시 장애가 아닌 오류) → 던진다 — 호출부가 실패로 보여 준다.
   죽은 잡은 서버 lease 회수(recover_stale_leases, 900초)가 error 로 종결하므로
   무한 대기가 되지 않는다.
   ============================================================= */

export const DETAIL_JOB_POLL_MS = 1200;
export const DETAIL_JOB_SLOW_POLL_MS = 5000;
// 정상 실측 242~285초 + 서버 lease 복구 900초 — 이 뒤로는 느린 주기로 계속 본다(포기 아님).
export const DETAIL_JOB_SLOW_AFTER_MS = 900000;

const TERMINAL = new Set(['done', 'error', 'cancelled']);
// 다시 물으면 달라질 수 있는 실패 — DB 풀·게이트웨이 일시 장애, 브라우저 연결 끊김.
// 생성 실패가 아니다: 서버의 기존 jobId 를 그대로 두고 재조회만 늦춘다(POST 재호출 없음 →
// 중복 생성·중복 과금 없음).
const TRANSIENT_STATUS = new Set([502, 503, 504]);
const TRANSIENT_CODES = new Set(['api_network', 'browser_offline', 'auth_session_network']);

export function isTransientPollError(error) {
  if (!error) return false;
  return TRANSIENT_STATUS.has(error.status) || TRANSIENT_CODES.has(error.code);
}

export function detailJobPollDelay(elapsedMs) {
  return elapsedMs > DETAIL_JOB_SLOW_AFTER_MS ? DETAIL_JOB_SLOW_POLL_MS : DETAIL_JOB_POLL_MS;
}

const defaultSleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * 잡이 종결될 때까지 폴링한다. 반환: 종결 잡(status done|error|cancelled) 또는
 * isAlive()가 false 가 되면 null(재시작·새 제작이 이 루프를 무효화함).
 * @param {object} o
 * @param {string} o.jobId
 * @param {(id: string) => Promise<object>} o.getJob
 * @param {(id: string, after: number) => Promise<{events?: object[]}>} [o.getJobEvents]
 *        이벤트는 보조 신호 — 일시 실패해도 잡 폴링은 계속(다음 턴에 after 재시도)
 * @param {number} o.startedAt  느린 주기 전환 기준 시각(ms)
 * @param {() => boolean} [o.isAlive]
 * @param {(events: object[]) => void} [o.onEvents]
 * @param {(job: object, info: {slow: boolean}) => void} [o.onJob]  매 조회(종결 포함)마다
 * @param {() => number} [o.now]
 * @param {(ms: number) => Promise<void>} [o.sleep]
 */
export async function pollDetailPageJob({
  jobId, getJob, getJobEvents, startedAt,
  isAlive = () => true, onEvents, onJob, now = Date.now, sleep = defaultSleep,
}) {
  let after = 0;
  let transientFailures = 0;
  for (;;) {
    if (!isAlive()) return null;
    let job;
    let ev;
    try {
      [job, ev] = await Promise.all([
        getJob(jobId),
        getJobEvents
          ? getJobEvents(jobId, after).catch(() => ({ events: [] }))
          : Promise.resolve({ events: [] }),
      ]);
      transientFailures = 0;
    } catch (e) {
      if (!isTransientPollError(e)) throw e;
      transientFailures += 1;
      await sleep(Math.min(DETAIL_JOB_SLOW_POLL_MS, DETAIL_JOB_POLL_MS * (2 ** Math.min(transientFailures - 1, 2))));
      continue;
    }
    if (!isAlive()) return null;
    const events = ev?.events || [];
    if (events.length) {
      after = events[events.length - 1].id;
      onEvents?.(events);
    }
    const elapsed = now() - (startedAt || now());
    onJob?.(job, { slow: elapsed > DETAIL_JOB_SLOW_AFTER_MS });
    if (TERMINAL.has(job?.status)) return job;
    await sleep(detailJobPollDelay(elapsed));
  }
}
