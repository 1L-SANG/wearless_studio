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
  /** 상품 분석의 마지막 "결과 대기" 단계 — 앞 4단계 10초를 뺀 꼬리(실측 12~22초 기준). */
  analysisWait: 8000,
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
const timeOr = (value, fallback) => (value == null ? fallback : value);

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

/* ── 단계형 진행 (분석 대기) ────────────────────────────────────────────────
   위의 잡 진행바와는 다른 모델이다. 분석은 단계마다 예정 시간이 이미 정해져 있어
   (AnalysisForm.STEP_DUR) 서버를 추측할 필요가 없다.

   여기에 점근 곡선을 쓰면 안 된다. 곡선은 뒤로 갈수록 느려지는데 단계는 20% 씩 균등하게
   올라오니, 단계가 끝날 때마다 생기는 따라잡기 폭이 점점 커진다(실측 +1.4 → +7 → +12.6
   → +18.3). 바가 뒤에서 갑자기 빨라지는 것처럼 읽힌다 — 오너 피드백 2026-08-14.

   그래서 단계마다 같은 몫(1/n)을 그 단계의 예정 시간 동안 **선형으로** 채운다.
   칸 경계에서 다음 칸의 시작과 정확히 만나므로 단계가 바뀌어도 튀지 않는다. */
export function steppedProgress({
  stepIndex, stepCount, stepElapsedMs, plannedMs, waitExpectedMs,
  ceiling = PROGRESS_CEILING,
}) {
  const count = Math.max(0, Number(stepCount) || 0);
  if (count <= 0) return 0;
  if (stepIndex >= count) return 100;          // 전 단계 완료
  const segment = 100 / count;
  const start = Math.max(0, Number(stepIndex) || 0) * segment;
  const elapsed = Math.max(0, Number(stepElapsedMs) || 0);

  // 예정 시간이 없는 단계 = 결과가 도착해야 끝나는 대기. 칸을 넘지 않으면서 계속 기어간다.
  if (!(Number(plannedMs) > 0)) {
    return Math.min(ceiling, creepTarget({
      base: start, ageMs: elapsed, expectedMs: waitExpectedMs, ceiling,
    }));
  }
  const planned = Number(plannedMs);
  return Math.min(start + segment, start + segment * (elapsed / planned));
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
