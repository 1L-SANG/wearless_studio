import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as smoothProgress from '../../src/lib/smoothProgress.js';
const {
  EXPECTED_MS, PROGRESS_CEILING, advanceProgress, creepAge, creepTarget,
  initialProgressState, nextDisplayProgress, analysisWaitProgress,
} = smoothProgress;

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

/* ── 분석 대기: 마운트부터 하나의 시간 모델 ─────────────────────────────── */

const STEPS = 5;
const STEP_MS = 2000;

function runAnalysis({ ms, resultAtMs = Infinity }) {
  let result = null;
  const samples = [];
  for (let t = 0; t <= ms; t += FRAME) {
    if (result === null && t >= resultAtMs) {
      result = { at: resultAtMs, value: analysisWaitProgress({ elapsedMs: resultAtMs }) };
    }
    const value = analysisWaitProgress({
      elapsedMs: t, resultAtMs: result?.at ?? null, resultValue: result?.value ?? 0,
    });
    samples.push({ t, value, index: Math.floor(value / (100 / STEPS)) });
    if (value === 100) break;
  }
  return samples;
}

const biggestFrameJump = (samples) => samples
  .slice(1)
  .reduce((worst, s, i) => Math.max(worst, s.value - samples[i].value), 0);

test('분석 대기 0~8초는 모든 단계 안에서 10%/초이고 체크는 2초 간격이다', () => {
  const samples = runAnalysis({ ms: 8000 });
  assert.equal(samples[0].value, 0);
  assert.equal(samples.at(-1).value, 80);
  for (let step = 0; step < 4; step += 1) {
    const inside = samples.filter(({ t }) => t > step * STEP_MS && t < (step + 1) * STEP_MS);
    for (let i = 1; i < inside.length; i += 1) {
      const speed = (inside[i].value - inside[i - 1].value) / FRAME * 1000;
      assert.ok(Math.abs(speed - 10) <= 0.1, `${step}단계 속도 ${speed}%/초`);
    }
    const completed = samples.find(({ index }) => index >= step + 1);
    assert.ok(Math.abs(completed.t - (step + 1) * STEP_MS) <= FRAME);
  }
});

test('80% 뒤 0.3초 평균 속도는 직전 속도의 85% 이상이다', () => {
  const samples = runAnalysis({ ms: 8320 });
  const before = samples.findLast(({ t }) => t <= 7700);
  const boundary = samples.find(({ t }) => t === 8000);
  const after = samples.findLast(({ t }) => t <= 8300);
  const incoming = (boundary.value - before.value) / (boundary.t - before.t);
  const outgoing = (after.value - boundary.value) / (after.t - boundary.t);
  assert.ok(outgoing >= incoming * 0.85, `경계 속도 비율 ${outgoing / incoming}`);
});

test('25초까지 결과가 없어도 99% 미만에서 단조 증가하고 2초 창마다 움직인다', () => {
  const samples = runAnalysis({ ms: 25000, resultAtMs: 25000 });
  assert.ok(samples.every(({ value }) => value < PROGRESS_CEILING));
  for (let i = 1; i < samples.length; i += 1) {
    assert.ok(samples[i].value > samples[i - 1].value, `${samples[i].t}ms에서 정지 또는 후퇴`);
  }
  for (const { t, value } of samples.filter(({ t }) => t >= 2000)) {
    assert.ok(value > valueAt(samples, t - 2000), `${t}ms까지 2초간 제자리`);
  }
  assert.ok(longestStallMs(samples) <= MAX_STALL_MS);
});

for (const [resultAtMs, resultValue, v0, vf] of [
  [3000, 30, 0.01, 0.025], [6000, 60, 0.01, 0.025],
  [7900, 79, 0.01, 0.0175], [8000, 80, 0.01, 1 / 60],
  [9000, 86.55172413793103, 0.004292508917954816, 0.01120689655172414],
  [11000, 91.63265306122449, 0.0015035401915868389, 0.01],
  [15000, 94.9438202247191, 0.0004557505365484156, 0.01],
  [25000, 97.08994708994709, 0.0001010610005319, 0.01],
]) {
  test(`결과 ${resultAtMs}ms 도착: 같은 속도에서 0.5초간 자연스럽게 가속해 마무리한다`, () => {
    assert.ok(Math.abs(analysisWaitProgress({ elapsedMs: resultAtMs }) - resultValue) < 1e-9);
    assert.equal(analysisWaitProgress({ elapsedMs: resultAtMs, resultAtMs, resultValue }), resultValue);
    const finishAt = (ageMs) => analysisWaitProgress({
      elapsedMs: resultAtMs + ageMs, resultAtMs, resultValue,
    });
    const incoming = (resultValue - analysisWaitProgress({ elapsedMs: resultAtMs - FRAME })) / FRAME * 1000;
    const outgoing = (finishAt(FRAME) - resultValue) / FRAME * 1000;
    assert.ok(Math.abs(outgoing - incoming) <= 0.6, `결과 순간 속도 차이 ${outgoing - incoming}%/초`);

    let previousAge = 0;
    let previousValue = resultValue;
    let previousSpeed = outgoing;
    for (const ageMs of [...Array.from({ length: 31 }, (_, i) => (i + 1) * FRAME), 500]) {
      const value = finishAt(ageMs);
      const speed = (value - previousValue) / (ageMs - previousAge) * 1000;
      assert.ok(speed + 1e-9 >= previousSpeed, `${ageMs}ms 가속 중 속도 감소: ${previousSpeed}에서 ${speed}`);
      previousAge = ageMs;
      previousValue = value;
      previousSpeed = speed;
    }
    // 적분한 거리도 확인해 출발 속도나 가속 모양이 달라지는 것을 잡는다.
    assert.ok(Math.abs(finishAt(250) - (resultValue + v0 * 203.125 + vf * 46.875)) < 1e-9);
    assert.ok(Math.abs(finishAt(500) - (resultValue + (v0 + vf) * 250)) < 1e-9);
    const cruiseSpeed = (finishAt(500 + FRAME) - finishAt(500)) / FRAME * 1000;
    assert.ok(Math.abs(cruiseSpeed - vf * 1000) <= 0.1, `가속 뒤 속도 ${cruiseSpeed}%/초`);

    const deadline = resultAtMs + 500 + (100 - resultValue - (v0 + vf) * 250) / vf;
    const samples = runAnalysis({ ms: deadline + FRAME, resultAtMs });
    const completed = samples.at(-1);
    assert.equal(completed.value, 100);
    assert.ok(completed.t >= deadline - 1e-9 && completed.t <= deadline + FRAME);
    assert.ok(biggestFrameJump(samples) < 0.6);
    let maxFinishSpeed = 0;
    for (let i = 1; i < samples.length; i += 1) {
      const previous = samples[i - 1];
      const current = samples[i];
      const delta = current.value - previous.value;
      assert.ok(delta >= 0, `${current.t}ms에서 후퇴`);
      if (previous.t >= resultAtMs) {
        const speed = delta / FRAME * 1000;
        maxFinishSpeed = Math.max(maxFinishSpeed, speed);
        assert.ok(speed <= 25 + 1e-9, `${current.t}ms에서 마무리 속도 초과`);
        if (previous.t >= resultAtMs + 500 && current.value < 100) {
          assert.ok(Math.abs(speed - vf * 1000) <= 0.1, `${current.t}ms에서 목표 속도와 다름: ${speed}`);
        }
      }
      for (let step = 0; step < STEPS; step += 1) {
        assert.equal(current.index > step, current.value >= (step + 1) * 20,
          `${current.t}ms에서 ${step}단계 체크가 바와 어긋남`);
      }
      assert.equal(current.index === STEPS, current.value === 100);
    }
    assert.ok(longestStallMs(samples) <= MAX_STALL_MS);
    if (resultAtMs === 9000 || resultAtMs === 11000) {
      assert.ok(maxFinishSpeed <= 13 + 1e-9, `보통 마무리 속도 ${maxFinishSpeed}%/초`);
    }
  });
}

test('마무리는 기록된 표시값을 앵커로 쓰고 결과 시각 0도 처리한다', () => {
  assert.equal(analysisWaitProgress({ elapsedMs: 11000, resultAtMs: 11000, resultValue: 90.5 }), 90.5);
  assert.ok(Math.abs(analysisWaitProgress({ elapsedMs: 11016, resultAtMs: 11000, resultValue: 90.5 })
    - 90.52419362176694) < 1e-9);
  assert.ok(Math.abs(analysisWaitProgress({ elapsedMs: 16, resultAtMs: 0, resultValue: 0 })
    - 0.16024182784) < 1e-9);
});

const analysisSource = readFileSync(new URL('../../src/features/analysis/AnalysisForm.jsx', import.meta.url), 'utf8');
const progressSource = analysisSource.slice(analysisSource.indexOf('export function AnalysisProgress'),
  analysisSource.indexOf('export function AnalysisSkeleton'));

test('분석 컴포넌트는 마운트 시계와 최초 결과 앵커를 rAF에 넘기고 체크를 바에서 계산한다', () => {
  assert.match(progressSource, /startedAtRef = useRef\(null\)/);
  assert.match(progressSource, /startedAtRef\.current \?\?= performance\.now\(\)/);
  assert.match(progressSource, /if \(!done \|\| resultRef\.current !== null\) return/);
  assert.match(progressSource, /value: analysisWaitProgress\(\{ fastEntry: isAnalysisFastEntryEnabled\(\), elapsedMs \}\)/);
  assert.match(progressSource, /elapsedMs: performance\.now\(\) - startedAtRef\.current/);
  assert.match(progressSource, /resultAtMs: resultRef\.current\?\.at \?\? null/);
  assert.match(progressSource, /resultValue: resultRef\.current\?\.value \?\? 0/);
  assert.match(progressSource, /setBarPercent\(value\)/);
  assert.match(progressSource, /cancelAnimationFrame\(raf\)/);
  assert.match(progressSource, /doneCount = Math\.floor\(barPercent \/ \(100 \/ ANALYZE_STEPS\.length\)\)/);
  assert.match(progressSource, /const t = setTimeout\(\(\) => setSlow\(true\), SLOW_NOTICE_MS\)/);
  assert.match(progressSource, /if \(done\) \{ setSlow\(false\)/);
});

test('100%가 된 뒤 240ms 전환은 재실행해도 최신 콜백을 한 번만 호출한다', () => {
  const body = progressSource.match(/useEffect\(\(\) => \{(\s*if \(barPercent < 100[\s\S]*?)\n  \}, \[barPercent\]\);/)?.[1];
  assert.ok(body, '100% 완료 effect가 있어야 한다');
  const finish = new Function('barPercent', 'finishedRef', 'onFinishedRef', 'setTimeout', 'clearTimeout', body);
  const timers = new Map();
  let timerId = 0;
  const schedule = (callback, delay) => { timers.set(++timerId, { callback, delay }); return timerId; };
  const cancel = (id) => timers.delete(id);
  const finishedRef = { current: false };
  let oldCalls = 0, latestCalls = 0;
  const onFinishedRef = { current: () => { oldCalls++; } };
  const effect = (value) => finish(value, finishedRef, onFinishedRef, schedule, cancel);
  effect(99);
  assert.equal(timers.size, 0);
  const cleanup = effect(100);
  assert.equal(timers.size, 1);
  assert.equal([...timers.values()][0].delay, 240);
  assert.equal(oldCalls, 0);
  cleanup();
  assert.equal(timers.size, 0);
  effect(100); // 개발 모드의 effect 정리 후 재실행
  onFinishedRef.current = () => { latestCalls++; };
  for (const { callback } of timers.values()) {
    callback();
    callback();
  }
  timers.clear();
  effect(100);
  assert.equal(timers.size, 0);
  assert.equal(oldCalls, 0);
  assert.equal(latestCalls, 1);
});

/* ── 숨김/재표시 (Codex 리뷰 2026-08-15 Major 1) ───────────────────────────
   리본은 화면을 옮기면 null 을 그린다. 그동안 프레임 루프는 멈춰야 하지만(헛 리렌더 방지)
   진행값은 살아 있어야 한다. 훅의 상태 보존을 리듀서 수준에서 재현해 검증한다. */
test('숨겨 있는 동안 루프가 멈춰도 값과 앵커는 살아남는다', () => {
  const expectedMs = DETAIL;
  let state = initialProgressState(0);
  // 0~20초: 보이는 상태로 진행(서버는 계속 0)
  for (let t = 0; t <= 20000; t += FRAME) {
    state = advanceProgress(state, { serverProgress: 0, now: t, expectedMs });
  }
  const beforeHide = state.displayed;
  assert.ok(beforeHide > 5, `전제 확인: 숨기기 전에 이미 차 있어야 한다 (${beforeHide})`);

  // 20~50초: 숨김 — advanceProgress 를 호출하지 않는다(루프 정지). 상태는 그대로 보관.
  const hidden = { ...state };

  // 50초에 다시 표시 — 보관한 상태에서 이어간다
  let resumed = advanceProgress(hidden, { serverProgress: 0, now: 50000, expectedMs });
  assert.ok(resumed.displayed >= beforeHide,
    `재표시 때 후퇴했다: ${beforeHide} → ${resumed.displayed}`);
  // 앵커가 절대시각이라 숨은 30초분의 creep 도 반영돼야 한다
  assert.ok(resumed.displayed > beforeHide,
    `숨은 동안의 진행이 사라졌다: ${beforeHide} → ${resumed.displayed}`);

  for (let t = 50000 + FRAME; t <= 60000; t += FRAME) {
    resumed = advanceProgress(resumed, { serverProgress: 0, now: t, expectedMs });
  }
  assert.ok(resumed.displayed < PROGRESS_CEILING);
});

test('상태를 버리고 다시 시작하면(새 잡) 0 부터다', () => {
  let state = initialProgressState(0);
  for (let t = 0; t <= 20000; t += FRAME) {
    state = advanceProgress(state, { serverProgress: 0, now: t, expectedMs: DETAIL });
  }
  assert.ok(state.displayed > 5);
  // 새 잡 = initialProgressState 로 갈아끼움
  const fresh = advanceProgress(initialProgressState(0), {
    serverProgress: 0, now: 20000, expectedMs: DETAIL,
  });
  assert.equal(fresh.displayed, 0, '새 잡은 0 에서 출발해야 한다');
});


test('빠른 분석 곡선은 5.6초에 80%를 지나고 6.7초와 긴 대기에서도 100%를 넘지 않는다', () => {
  assert.equal(analysisWaitProgress({ elapsedMs: 5600, fastEntry: true }), 80);
  assert.ok(analysisWaitProgress({ elapsedMs: 6700, fastEntry: true }) > 80);
  assert.ok(analysisWaitProgress({ elapsedMs: 30000, fastEntry: true }) < 99);
  assert.equal(analysisWaitProgress({ elapsedMs: 5600, fastEntry: false }), 56);
});
