import test from 'node:test';
import assert from 'node:assert/strict';
import {
  EXPECTED_MS, PROGRESS_CEILING, advanceProgress, creepAge, creepTarget,
  initialProgressState, nextDisplayProgress, steppedProgress,
} from '../../src/lib/smoothProgress.js';

const DETAIL = EXPECTED_MS.detailPage;
const FRAME = 16;

/** 프레임 루프를 흉내낸다. serverAt(ms) → 그 시점부터의 서버 보고값. */
function run({ ms, expectedMs = DETAIL, serverAt = () => 0, done = () => false, startedAt = 0 }) {
  let state = initialProgressState(startedAt);
  const samples = [];
  for (let t = 0; t <= ms; t += FRAME) {
    state = advanceProgress(state, {
      serverProgress: serverAt(t), now: t, expectedMs, done: done(t),
    });
    samples.push({ t, value: state.displayed });
  }
  return { state, samples };
}

/* 연속으로 제자리인 가장 긴 구간(ms). "바가 멈춘 것처럼 보이는가" 를 직접 재는 잣대다.
   앵커가 옮겨가는 순간처럼 한 프레임 같은 값이 나오는 건 곡선이 정확히 이어졌다는 뜻이라
   문제가 아니고, 사람 눈에 걸리는 건 수백 ms 이상 붙박이인 구간이다. */
function longestStallMs(samples) {
  let worst = 0;
  let runStart = null;
  for (let i = 1; i < samples.length; i += 1) {
    if (samples[i].value <= samples[i - 1].value) {
      if (runStart === null) runStart = samples[i - 1].t;
      worst = Math.max(worst, samples[i].t - runStart);
    } else {
      runStart = null;
    }
  }
  return worst;
}

const MAX_STALL_MS = 100;   // 이보다 길게 붙박이면 "멈췄다" 로 읽힌다

test('앵커에서 기어오르되 천장에 닿지는 않는다', () => {
  let prev = 0;
  for (const age of [1000, 10000, 60000, DETAIL, DETAIL * 3, DETAIL * 10]) {
    const now = creepTarget({ base: 0, ageMs: age, expectedMs: DETAIL });
    assert.ok(now > prev, `${age}ms 지점에서 멈추면 안 된다 (${prev} → ${now})`);
    assert.ok(now < PROGRESS_CEILING, '완료 신호 없이 천장에 닿으면 안 된다');
    prev = now;
  }
});

test('creepAge 는 creepTarget 의 역함수다 (앵커 이동이 곡선을 끊지 않는 근거)', () => {
  for (const age of [500, 5000, 50000, 200000]) {
    const value = creepTarget({ base: 30, ageMs: age, expectedMs: DETAIL });
    const back = creepAge({ base: 30, value, expectedMs: DETAIL });
    assert.ok(Math.abs(back - age) < 1, `${age}ms → ${value}% → ${back}ms`);
  }
});

test('표시값은 절대 후퇴하지 않는다', () => {
  assert.equal(nextDisplayProgress({ displayed: 62, target: 40, dtMs: FRAME }), 62);
  assert.equal(nextDisplayProgress({ displayed: 62, target: 62, dtMs: FRAME }), 62);
});

test('서버가 크게 앞서면 한 프레임에 튀지 않고 부드럽게 따라잡는다', () => {
  const oneFrame = nextDisplayProgress({ displayed: 47, target: 70, dtMs: FRAME, catchUpMs: 600 });
  assert.ok(oneFrame > 47 && oneFrame < 49, `한 프레임 이동이 과하다: ${oneFrame}`);

  let displayed = 47;
  for (let ms = 0; ms < 4000; ms += FRAME) {
    displayed = nextDisplayProgress({ displayed, target: 70, dtMs: FRAME, catchUpMs: 600 });
  }
  assert.equal(displayed, 70, '몇 초 안에는 목표에 붙어야 한다');
});

test('서버가 40%에서 60초 침묵해도 매 프레임 앞으로 간다', () => {
  // 컷 한 장이 도는 실제 구간. 첫 판이 여기서 통째로 멈췄다.
  const { state, samples } = run({ ms: 60000, serverAt: () => 40 });
  assert.ok(state.displayed > 40, `60초 뒤에도 서버값에 머물러 있다: ${state.displayed}`);
  assert.ok(state.displayed < PROGRESS_CEILING);
  assert.ok(longestStallMs(samples) <= MAX_STALL_MS, `${longestStallMs(samples)}ms 동안 붙박이`);
});

test('서버 보고가 하나도 없어도 계속 차오른다', () => {
  const { state, samples } = run({ ms: 120000, serverAt: () => 0 });
  assert.ok(state.displayed > 0);
  assert.ok(state.displayed < PROGRESS_CEILING);
  assert.ok(longestStallMs(samples) <= MAX_STALL_MS, `${longestStallMs(samples)}ms 동안 붙박이`);
});

/** t 이하의 마지막 표본 — 프레임 간격이 16ms 라 임의 시각과 딱 맞지 않는다. */
const valueAt = (samples, t) => samples.filter((s) => s.t <= t).pop().value;

test('서버가 뒤늦게 앞서 보고해도 바가 멈추지 않는다 (앵커 이동)', () => {
  // 150초간 조용하다 40% 가 도착 — 그 사이 기어온 표시값이 이미 40 을 넘겨 있다.
  const { samples } = run({ ms: 260000, serverAt: (t) => (t < 150000 ? 0 : 40) });
  const atReport = valueAt(samples, 150000);
  assert.ok(atReport > 40, `전제 확인: 보고 시점 표시값이 서버값보다 앞서야 한다 (${atReport})`);
  assert.ok(longestStallMs(samples) <= MAX_STALL_MS,
    `뒤늦은 보고 뒤 ${longestStallMs(samples)}ms 붙박이 — 앵커 이동이 곡선을 끊었다`);
});

test('서버가 앞서면 즉시 그 위로 올라탄다', () => {
  const { samples } = run({ ms: 20000, serverAt: (t) => (t < 5000 ? 0 : 70) });
  const before = valueAt(samples, 5000 - FRAME);
  const after = valueAt(samples, 10000);
  assert.ok(before < 20, `전제 확인: 보고 전엔 아직 낮아야 한다 (${before})`);
  assert.ok(after > 69, `5초 뒤 서버값 70 을 따라잡아야 한다 (${after})`);
});

test('완료 전에는 100을 보여주지 않고, 완료 신호에만 100이 된다', () => {
  const long = run({ ms: DETAIL * 4, serverAt: () => 100 });
  assert.ok(long.state.displayed <= PROGRESS_CEILING, `완료 전 ${long.state.displayed}%`);

  const finished = run({ ms: 8000, serverAt: () => 60, done: (t) => t > 4000 });
  assert.equal(finished.state.displayed, 100);
});

test('새로고침 복원 — startedAt 이 있으면 처음부터 다시 기지 않는다', () => {
  // 3분 전에 시작된 잡을 복원한 직후(서버 첫 응답 전)
  const restored = advanceProgress(initialProgressState(-180000), {
    serverProgress: 0, now: 0, expectedMs: DETAIL,
  });
  assert.ok(restored.displayed === 0, '첫 프레임은 dt=0 이라 제자리');
  const second = advanceProgress(restored, { serverProgress: 0, now: FRAME, expectedMs: DETAIL });
  assert.ok(second.displayed > 0, '이미 3분 경과한 잡이면 0 에서 출발하지 않는다');
});

/* ── 단계형 진행 (분석 대기) ─────────────────────────────────────────────── */

const STEPS = 5;
const STEP_DUR = Array.from({ length: STEPS }, () => 2500);   // AnalysisForm 과 같은 값
const FAST_DUR = 320;

/* 분석 화면 타임라인을 프레임 단위로 재현한다 — AnalysisProgress 의 단계 진행 규칙
   (마지막 단계는 결과가 와야 넘어가고, 결과가 오면 남은 단계를 320ms 로 훑는다) 그대로. */
function runAnalysis({ ms, resultAtMs = Infinity }) {
  let index = 0;
  let stepAt = 0;
  let peak = 0;
  const samples = [];
  for (let t = 0; t <= ms; t += FRAME) {
    const done = t >= resultAtMs;
    // 예정 시간이 없는 단계 = 결과를 기다리는 마지막 단계
    const plannedMs = (!done && index === STEPS - 1) ? null
      : (done ? FAST_DUR : STEP_DUR[index]);
    if (index < STEPS && plannedMs !== null && t - stepAt >= plannedMs) {
      index += 1;
      stepAt = t;
      continue;   // 단계가 넘어간 프레임은 다음 루프에서 새 칸으로 계산
    }
    peak = Math.max(peak, steppedProgress({
      stepIndex: index, stepCount: STEPS, stepElapsedMs: t - stepAt,
      plannedMs, waitExpectedMs: EXPECTED_MS.analysisWait,
    }));
    samples.push({ t, value: peak, index });
  }
  return samples;
}

test('단계마다 같은 몫(20%)을 차지한다', () => {
  for (let i = 0; i < STEPS; i += 1) {
    const startOfStep = steppedProgress({
      stepIndex: i, stepCount: STEPS, stepElapsedMs: 0, plannedMs: STEP_DUR[i],
    });
    assert.equal(startOfStep, i * 20, `${i}단계는 ${i * 20}% 에서 시작해야 한다`);
  }
});

test('한 단계 안에서는 선형으로 찬다 (뒤에서 빨라지지 않는다)', () => {
  const at = (frac) => steppedProgress({
    stepIndex: 1, stepCount: STEPS, stepElapsedMs: STEP_DUR[1] * frac, plannedMs: STEP_DUR[1],
  });
  const quarter = at(0.25) - at(0);
  const mid = at(0.75) - at(0.5);
  assert.ok(Math.abs(quarter - mid) < 1e-9, `구간마다 이동량이 달라진다: ${quarter} vs ${mid}`);
  assert.equal(at(1), 40, '단계가 끝나면 다음 칸 시작점과 정확히 만난다');
});

test('칸 경계에서 튀지 않는다 (앞 칸 끝 = 다음 칸 시작)', () => {
  for (let i = 0; i < STEPS - 1; i += 1) {
    const end = steppedProgress({
      stepIndex: i, stepCount: STEPS, stepElapsedMs: STEP_DUR[i], plannedMs: STEP_DUR[i],
    });
    const next = steppedProgress({
      stepIndex: i + 1, stepCount: STEPS, stepElapsedMs: 0, plannedMs: STEP_DUR[i + 1],
    });
    assert.equal(end, next, `${i}→${i + 1} 경계가 어긋난다`);
  }
});

test('단계가 넘어갈 때 따라잡기 점프가 없다 — 오너 피드백의 핵심', () => {
  const samples = runAnalysis({ ms: 10000 });
  let biggestJump = 0;
  for (let i = 1; i < samples.length; i += 1) {
    biggestJump = Math.max(biggestJump, samples[i].value - samples[i - 1].value);
  }
  // 한 프레임(16ms)에 이동할 수 있는 최대치는 가장 짧은 단계 기준 20% × 16/2200 ≈ 0.15%
  assert.ok(biggestJump < 0.3, `프레임 하나에 ${biggestJump.toFixed(2)}% 점프 — 계단이 남아 있다`);
});

test('앞 4단계 속도가 전부 같다 (2.5초 균등 — 오너 결정)', () => {
  const samples = runAnalysis({ ms: 10000 });
  const speedOver = (fromMs, toMs) => {
    const a = samples.filter((s) => s.t <= fromMs).pop().value;
    const b = samples.filter((s) => s.t <= toMs).pop().value;
    return ((b - a) / (toMs - fromMs)) * 1000;   // %/초
  };
  // 경계 프레임의 양자화 오차를 피해 각 단계 안쪽만 잰다.
  const speeds = [0, 1, 2, 3].map((i) => speedOver(i * 2500 + 200, i * 2500 + 2300));
  const fastest = Math.max(...speeds);
  const slowest = Math.min(...speeds);
  assert.ok(fastest - slowest < 0.1,
    `단계별 속도가 갈린다: ${speeds.map((s) => s.toFixed(2)).join(' / ')} %/s`);
  assert.ok(Math.abs(slowest - 8) < 0.1, `20% ÷ 2.5초 = 8%/s 여야 한다: ${slowest.toFixed(2)}`);
});

test('결과가 늦으면 마지막 칸 안에서 천천히 계속 기어간다', () => {
  const samples = runAnalysis({ ms: 40000 });
  const at10s = samples.filter((s) => s.t <= 10000).pop().value;
  const at25s = samples.filter((s) => s.t <= 25000).pop().value;
  // 16ms 프레임이 10000ms 에 정확히 떨어지지 않아 한 프레임분(≈0.15%) 못 미칠 수 있다.
  assert.ok(at10s > 79.5, `4단계까지 끝나면 80% 언저리여야 한다: ${at10s}`);
  assert.ok(at25s > at10s, '결과를 기다리는 동안 멈춰 있다');
  assert.ok(at25s < PROGRESS_CEILING, '완료 전에 천장에 닿으면 안 된다');
  assert.ok(longestStallMs(samples) <= MAX_STALL_MS, `${longestStallMs(samples)}ms 붙박이`);
});

test('전 단계 완료면 100%', () => {
  assert.equal(steppedProgress({
    stepIndex: STEPS, stepCount: STEPS, stepElapsedMs: 0, plannedMs: 320,
  }), 100);
});
