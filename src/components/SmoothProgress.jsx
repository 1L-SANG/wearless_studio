/* 진행바를 "멈추지 않게" 그리는 배선 — 계산은 src/lib/smoothProgress.js 가 한다.
   서버 보고(계단)와 경과시간(연속)을 합친 값을 requestAnimationFrame 으로 흘려보낸다. */
import { useEffect, useRef, useState } from 'react';
import {
  EXPECTED_MS, advanceProgress, initialProgressState, steppedProgress, timeOr,
} from '@/lib/smoothProgress.js';

/* 이 폭 이상 움직였을 때만 리렌더한다. 280px 바에서 0.14px — 눈에는 연속으로 보이면서
   초당 60번이 아니라 6번 정도만 렌더한다. */
const PUSH_STEP = 0.05;

/**
 * @param serverProgress 서버가 보고한 진행률(0~100). 후퇴하지 않는 바닥으로만 쓰인다.
 * @param active         진행 중일 때만 true. false 로 내려가면 0 으로 되돌린다.
 * @param done           완료 신호. 이때만 100 에 도달한다.
 * @param startedAt      시작 시각(ms). 없으면 훅이 활성화된 순간을 쓴다(새로고침 복원용).
 * @param expectedMs     예상 소요시간. EXPECTED_MS 참고.
 */
export function useSmoothProgress(serverProgress, {
  active = true,
  done = false,
  startedAt = 0,
  expectedMs = EXPECTED_MS.default,
  catchUpMs = 600,
} = {}) {
  const [displayed, setDisplayed] = useState(0);
  // 프레임 루프가 항상 최신 인자를 읽게 한다 — 인자가 바뀔 때마다 rAF 를 끊지 않기 위해.
  const argsRef = useRef(null);
  argsRef.current = { serverProgress, done, startedAt, expectedMs, catchUpMs };

  useEffect(() => {
    if (!active) { setDisplayed(0); return undefined; }
    let raf = 0;
    let pushed = -1;    // 마지막으로 렌더에 올린 값
    // 시작 시각이 주어졌고 과거이면 그걸 앵커로 — 새로고침으로 돌아와도 0 에서 다시 기지 않는다.
    const given = Number(argsRef.current.startedAt) || 0;
    let state = initialProgressState(given > 0 && given <= Date.now() ? given : 0);

    const tick = () => {
      const a = argsRef.current;
      state = advanceProgress(state, {
        serverProgress: a.serverProgress,
        now: Date.now(),
        expectedMs: a.expectedMs,
        done: a.done,
        catchUpMs: a.catchUpMs,
      });
      if (state.displayed - pushed >= PUSH_STEP || (state.displayed === 100 && pushed !== 100)) {
        pushed = state.displayed;
        setDisplayed(state.displayed);
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [active]);

  return Math.min(100, Math.max(0, displayed));
}

/* 단계마다 예정 시간이 정해진 진행(분석 대기)용. 단계별로 균등한 몫을 선형으로 채우므로
   따라잡기 이징이 필요 없다 — 계산이 이미 연속이라 그대로 흘려보내면 된다.
 * @param stepIndex     지금 진행 중인 단계(0부터)
 * @param stepStartedAt 그 단계가 시작된 시각(ms)
 * @param plannedMs     그 단계의 예정 시간. null 이면 "결과 대기"로 보고 천천히 기어간다.
 */
export function useSteppedProgress({
  stepIndex, stepCount, stepStartedAt, plannedMs,
  waitExpectedMs = EXPECTED_MS.analysisWait,
}) {
  const [displayed, setDisplayed] = useState(0);
  const argsRef = useRef(null);
  argsRef.current = { stepIndex, stepCount, stepStartedAt, plannedMs, waitExpectedMs };

  useEffect(() => {
    let raf = 0;
    let pushed = -1;
    let peak = 0;      // 절대 후퇴하지 않는다
    const tick = () => {
      const a = argsRef.current;
      const now = Date.now();
      peak = Math.max(peak, steppedProgress({
        stepIndex: a.stepIndex,
        stepCount: a.stepCount,
        // timeOr — 시각 0 을 falsy 로 삼키면 dt 가 늘 0 이 되어 바가 통째로 멈춘다.
        stepElapsedMs: now - timeOr(a.stepStartedAt, now),
        plannedMs: a.plannedMs,
        waitExpectedMs: a.waitExpectedMs,
      }));
      if (peak - pushed >= PUSH_STEP || (peak === 100 && pushed !== 100)) {
        pushed = peak;
        setDisplayed(peak);
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);

  return displayed;
}

/* 큰 화면(에디터) 안에 박히는 진행바용 — 훅을 이 작은 컴포넌트가 소유해서
   초당 여러 번의 리렌더가 바깥 트리로 번지지 않게 격리한다. */
export function SmoothProgressTrack({
  value, active = true, done = false, startedAt = 0, expectedMs, catchUpMs,
  className, fillClassName, tag: Tag = 'div', fillTag: Fill = 'i',
}) {
  const pct = useSmoothProgress(value, { active, done, startedAt, expectedMs, catchUpMs });
  return (
    <Tag className={className} aria-hidden="true">
      <Fill className={fillClassName} style={{ width: `${pct}%` }} />
    </Tag>
  );
}
