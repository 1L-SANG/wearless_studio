/* 진행바 표시값 계산 (순수 함수) — 잉크+쉬인 스킨과 한 쌍.
   시안: mockups/progressbar-gallery.html ⑤ "잉크 + 쉬인"

   왜 필요한가. 서버 progress 는 1.2초 폴링으로 계단처럼 들어오는데, 컷 한 장이 도는
   수십 초 동안은 같은 값만 반복된다(useAppStore.startDetailPageGeneration). 그 값을
   그대로 폭에 꽂으면 바가 완전히 정지해 "실패했나" 로 읽힌다 — 마네킹 대기화면이
   진행바를 아예 걷어내야 했던 사고와 같은 뿌리다(Mannequin.jsx MannequinLoading 주석).

   설계. 표시값은 "마지막 서버 보고(base)" 를 앵커로 잡고, 그 뒤 흐른 시간만큼
   남은 구간(base→천장)을 지수적으로 기어오른다.
     · 서버 보고가 오면  → 앵커가 그 값으로 올라가고 바가 즉시 그 위로 따라붙는다.
     · 서버가 조용하면   → 앵커 시각으로부터의 경과시간이 바를 계속 밀어 올린다.
   앵커를 절대 시작시각이 아니라 **마지막 보고 시점**에 두는 게 핵심이다. 절대시각
   기준이면 서버가 일정보다 앞서 보고한 순간 시간 곡선이 뒤처져서, 그 뒤로는 곡선이
   서버값을 따라잡을 때까지 바가 통째로 멈춘다(이 파일의 첫 판이 그랬고 테스트가 잡았다).

   완료 신호 전에는 PROGRESS_CEILING 을 넘지 않는다. 100% 를 먼저 보여주고 기다리게
   만드는 건 진행바가 할 수 있는 가장 나쁜 거짓말이라서. */

/** 완료(done) 전에 표시할 수 있는 최대치. 100 은 오직 완료 신호로만 도달한다. */
export const PROGRESS_CEILING = 99;

/** 기어오름 시상수 = expectedMs × 이 값. 클수록 느리고 완만하게 기어간다. */
const CREEP_RATIO = 0.8;

/* 예상 소요시간 — 전부 코드에 근거가 남아 있는 실측값이다.
   임의로 늘리면 바가 실제보다 느리게 기고, 줄이면 일찍 천장에 붙어 멈춘 것처럼 보인다. */
export const EXPECTED_MS = {
  /** 상세페이지 생성 — 실측 242~285초(useAppStore 폴링 주석). */
  detailPage: 270000,
  /** 마네킹컷 생성 — 대기화면이 40초에 장기 대기 안내를 띄우는 기준(Mannequin.jsx). */
  mannequin: 45000,
  /** 그 밖의 이미지 잡 — 근거 없는 곳의 보수적 기본값. */
  default: 60000,
};

const clampPercent = (v) => Math.max(0, Math.min(100, Number(v) || 0));

const creepTau = (expectedMs) => Math.max(1, (Number(expectedMs) || 1) * CREEP_RATIO);

/** 앵커(base)에서 ageMs 만큼 기어오른 값. 천장에 점근할 뿐 닿지는 않는다. */
export function creepTarget({ base, ageMs, expectedMs, ceiling = PROGRESS_CEILING }) {
  const from = clampPercent(base);
  if (from >= ceiling) return from;
  const age = Math.max(0, Number(ageMs) || 0);
  return from + (ceiling - from) * (1 - Math.exp(-age / creepTau(expectedMs)));
}

/** creepTarget 의 역함수 — value 가 base 기준 곡선 위 몇 ms 지점인지. 앵커를 옮길 때 쓴다. */
export function creepAge({ base, value, expectedMs, ceiling = PROGRESS_CEILING }) {
  const from = clampPercent(base);
  if (from >= ceiling) return 0;
  const frac = (Math.min(Number(value) || 0, ceiling) - from) / (ceiling - from);
  if (!(frac > 0)) return 0;
  return -Math.log(1 - Math.min(frac, 0.999999)) * creepTau(expectedMs);
}

/* 한 프레임 이동. 목표까지 지수적으로 접근해서 서버가 크게 앞서도 뚝 끊기지 않고 따라붙는다.
   목표가 코앞(snapAt 이내)이면 그냥 붙인다 — 조용한 구간에서 영원히 뒤처지지 않도록. */
export function nextDisplayProgress({
  displayed, target, dtMs, catchUpMs = 600, snapAt = 0.35,
}) {
  const from = Math.max(0, Number(displayed) || 0);
  const to = Number(target) || 0;
  if (to <= from) return from;                       // 절대 후퇴하지 않는다
  const dt = Math.max(0, Number(dtMs) || 0);
  const eased = from + (to - from) * (1 - Math.exp(-dt / Math.max(1, catchUpMs)));
  return to - eased < snapAt ? to : eased;
}

/* 시각 필드는 "아직 없음" 을 null 로 둔다 — 0 을 falsy 로 삼키면 시각 0(테스트·가짜 타이머)이
   매 프레임 "미설정" 으로 읽혀 dt 가 늘 0 이 되고 바가 통째로 멈춘다. */
export const timeOr = (value, fallback) => (value == null ? fallback : value);

/** @param startedAt 시작 시각. 서버 첫 보고 전까지의 앵커로 쓰인다(새로고침 복원용). */
export function initialProgressState(startedAt = 0) {
  const started = Number(startedAt);
  return {
    base: 0,
    baseAt: Number.isFinite(started) && started !== 0 ? started : null,
    displayed: 0,
    at: null,
  };
}

/* 분석 대기: 오너 9/15·10/10 지적대로 초반 등속, 늦은 결과는 완만한 꼬리로 기다린다.
   9/15~10/10 약 20건의 서버 실측은 중앙 7.4초, 상위 25% 10초·10% 13초, 최대 16초.
   임시저장 0.5~1초를 더해 8초에 80%로 잡는다. 9/15 "뒤에서 쫙", 10/10 "자연스럽게"
   지적에 맞춰 결과 순간 속도를 이어 0.5초간 가속하고, 기본의 2.5배로 제한한다. */
const ANALYSIS_RATE = 10 / 1000;          // %/ms, 20%씩 2초
const ANALYSIS_FINISH_MAX_RATE = 25 / 1000;
const ANALYSIS_FINISH_TARGET_MS = 1200;
const ANALYSIS_FINISH_RAMP_MS = 500;
const ANALYSIS_LINEAR_END = 80;
const ANALYSIS_LINEAR_MS = ANALYSIS_LINEAR_END / ANALYSIS_RATE;
const ANALYSIS_TAIL = PROGRESS_CEILING - ANALYSIS_LINEAR_END;
// k를 남은 폭/기본 속도로 두면 80% 경계에서도 같은 속도로 출발한다.
const ANALYSIS_TAIL_K = ANALYSIS_TAIL / ANALYSIS_RATE;

/** elapsedMs 는 마운트부터의 시간. 결과가 오면 그 시각과 표시값을 고정해 이어간다. */
export function analysisWaitProgress({ elapsedMs, resultAtMs = null, resultValue = 0 }) {
  const elapsed = Math.max(0, elapsedMs);
  if (resultAtMs !== null && elapsed >= resultAtMs) {
    const v0 = resultAtMs <= ANALYSIS_LINEAR_MS
      ? ANALYSIS_RATE
      : ANALYSIS_TAIL * ANALYSIS_TAIL_K / (resultAtMs - ANALYSIS_LINEAR_MS + ANALYSIS_TAIL_K) ** 2;
    const vf = Math.max(ANALYSIS_RATE,
      Math.min(ANALYSIS_FINISH_MAX_RATE, (100 - resultValue) / ANALYSIS_FINISH_TARGET_MS));
    const age = elapsed - resultAtMs;
    const ramp = ANALYSIS_FINISH_RAMP_MS;
    if (age <= ramp) {
      const u = age / ramp;
      return Math.min(100, resultValue + v0 * age + (vf - v0) * ramp * (u ** 3 - u ** 4 / 2));
    }
    return Math.min(100, resultValue + v0 * ramp + (vf - v0) * ramp / 2 + vf * (age - ramp));
  }
  if (elapsed <= ANALYSIS_LINEAR_MS) return elapsed * ANALYSIS_RATE;

  const age = elapsed - ANALYSIS_LINEAR_MS;
  return ANALYSIS_LINEAR_END + ANALYSIS_TAIL * age / (age + ANALYSIS_TAIL_K);
}

/* 한 프레임 진행 — 상태를 받아 다음 상태를 돌려주는 순수 리듀서. 훅은 이걸 rAF 로 돌리기만 한다. */
export function advanceProgress(state, {
  serverProgress, now, expectedMs, done = false,
  ceiling = PROGRESS_CEILING, catchUpMs = 600,
}) {
  const prev = state || initialProgressState();
  const at = timeOr(prev.at, now);
  let base = prev.base;
  let baseAt = timeOr(prev.baseAt, now);

  const server = clampPercent(serverProgress);
  if (server > base) {
    // 실제 진행이 도착했다 — 앵커를 그 값으로 올린다. 다만 이미 그보다 앞서 기어와
    // 있었다면 앵커 시각을 현재 위치만큼 뒤로 밀어 곡선을 잇는다. 그러지 않으면
    // 새 곡선이 표시값을 따라잡을 때까지 바가 멈춘다.
    const ahead = prev.displayed > server
      ? creepAge({ base: server, value: prev.displayed, expectedMs, ceiling })
      : 0;
    base = server;
    baseAt = now - ahead;
  }

  const target = done
    ? 100
    : Math.min(ceiling, creepTarget({ base, ageMs: now - baseAt, expectedMs, ceiling }));
  const displayed = nextDisplayProgress({
    displayed: prev.displayed, target, dtMs: now - at, catchUpMs,
  });
  return { base, baseAt, displayed, at: now };
}
