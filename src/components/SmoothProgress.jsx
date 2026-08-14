/* 진행바를 "멈추지 않게" 그리는 배선 — 계산은 src/lib/smoothProgress.js 가 한다.
   서버 보고(계단)와 경과시간(연속)을 합친 값을 requestAnimationFrame 으로 흘려보낸다. */
import { useEffect, useRef, useState } from 'react';
import {
  EXPECTED_MS, advanceProgress, initialProgressState, steppedProgress, timeOr,
} from '@/lib/smoothProgress.js';

/* 이 폭 이상 움직였을 때만 리렌더한다. 280px 바에서 0.14px — 눈에는 연속으로 보인다. */
const PUSH_STEP = 0.05;

/* 프레임 루프 하나로 두 모델(잡 creep · 단계 선형)을 돌린다. 두 훅이 각자 rAF·푸시 게이트를
   복제하던 걸 여기로 모았다.

   running 과 resetKey 를 반드시 구분할 것.
     · running=false  → 루프만 멈추고 값은 ref 에 남는다. 화면에서 잠깐 숨겨졌을 때 쓴다.
     · resetKey 변경  → 다른 잡이므로 0 부터 다시 시작한다.
   이 둘을 한 플래그로 묶으면 화면을 옮겼다 돌아올 때 진행률이 0 으로 후퇴한다
   (Codex 리뷰 2026-08-15 Major 1 — "절대 후퇴하지 않는다" 계약이 화면 전환에 깨졌다). */
function useProgressLoop({ running, resetKey, step, emit }) {
  const stateRef = useRef(null);      // 리듀서 상태 — 숨겨 있는 동안에도 살아남는다
  const stepRef = useRef(step);
  const emitRef = useRef(emit);
  stepRef.current = step;
  emitRef.current = emit;

  useEffect(() => {
    stateRef.current = null;          // 새 잡 — 다음 프레임이 처음부터 다시 만든다
    emitRef.current(0);
  }, [resetKey]);

  useEffect(() => {
    if (!running) return undefined;
    let raf = 0;
    const tick = () => {
      const next = stepRef.current(stateRef.current);
      stateRef.current = next.state;
      emitRef.current(next.value);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [running]);
}

/* 값이 PUSH_STEP 이상 움직였을 때만 setState 로 올리는 emit 을 만든다. */
function usePushedValue() {
  const [displayed, setDisplayed] = useState(0);
  const pushedRef = useRef(-1);
  const emit = (value) => {
    if (value === 0 && pushedRef.current !== 0) { pushedRef.current = 0; setDisplayed(0); return; }
    if (value - pushedRef.current >= PUSH_STEP || (value === 100 && pushedRef.current !== 100)) {
      pushedRef.current = value;
      setDisplayed(value);
    }
  };
  return [displayed, emit];
}

/**
 * @param serverProgress 서버가 보고한 진행률(0~100). 후퇴하지 않는 바닥으로만 쓰인다.
 * @param active   잡이 살아 있는가. false 면 0 으로 초기화한다.
 * @param paused   지금 화면에서 숨겨져 있는가. 루프만 멈추고 값은 보존한다.
 * @param jobKey   잡 신원. 바뀌면 0 부터 다시 시작한다.
 * @param done     완료 신호. 이때만 100 에 도달한다.
 * @param startedAt 시작 시각(ms). 없으면 훅이 활성화된 순간을 쓴다(새로고침 복원용).
 */
export function useSmoothProgress(serverProgress, {
  active = true,
  paused = false,
  jobKey = '',
  done = false,
  startedAt = 0,
  expectedMs = EXPECTED_MS.default,
  catchUpMs = 600,
} = {}) {
  const [displayed, emit] = usePushedValue();
  const argsRef = useRef(null);
  argsRef.current = { serverProgress, done, startedAt, expectedMs, catchUpMs };

  const step = (prev) => {
    const a = argsRef.current;
    // 시작 시각이 주어졌고 과거이면 그걸 앵커로 — 새로고침으로 돌아와도 0 에서 다시 기지 않는다.
    const given = Number(a.startedAt) || 0;
    const now = Date.now();
    const state = advanceProgress(prev || initialProgressState(given > 0 && given <= now ? given : 0), {
      serverProgress: a.serverProgress, now, expectedMs: a.expectedMs, done: a.done, catchUpMs: a.catchUpMs,
    });
    return { state, value: state.displayed };
  };

  useProgressLoop({
    running: active && !paused,
    resetKey: active ? `on:${jobKey}` : 'off',
    step,
    emit,
  });

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
  const [displayed, emit] = usePushedValue();
  const argsRef = useRef(null);
  argsRef.current = { stepIndex, stepCount, stepStartedAt, plannedMs, waitExpectedMs };

  const step = (prev) => {
    const a = argsRef.current;
    const now = Date.now();
    const value = Math.max(prev?.peak || 0, steppedProgress({
      stepIndex: a.stepIndex,
      stepCount: a.stepCount,
      // timeOr — 시각 0 을 falsy 로 삼키면 dt 가 늘 0 이 되어 바가 통째로 멈춘다.
      stepElapsedMs: now - timeOr(a.stepStartedAt, now),
      plannedMs: a.plannedMs,
      waitExpectedMs: a.waitExpectedMs,
    }));
    return { state: { peak: value }, value };   // 절대 후퇴하지 않는다
  };

  useProgressLoop({ running: true, resetKey: 'stepped', step, emit });

  return displayed;
}

/* 큰 화면(에디터·모델 생성) 안에 박히는 진행바용 — 훅을 이 작은 컴포넌트가 소유해서
   초당 여러 번의 리렌더가 바깥 트리로 번지지 않게 격리한다.
   주의: 부모가 조건부로 이 컴포넌트를 감추면 언마운트되어 진행 상태가 사라진다. 숨겼다
   다시 보여야 하는 자리(전역 리본)는 항상 마운트된 컴포넌트에서 훅을 직접 쓰고 paused 를 넘길 것. */
export function SmoothProgressTrack({
  value, active = true, paused = false, jobKey, done = false, startedAt = 0, expectedMs, catchUpMs,
  className, fillClassName, tag: Tag = 'div', fillTag: Fill = 'i',
}) {
  const pct = useSmoothProgress(value, { active, paused, jobKey, done, startedAt, expectedMs, catchUpMs });
  return (
    <Tag className={className} aria-hidden="true">
      <Fill className={fillClassName} style={{ width: `${pct}%` }} />
    </Tag>
  );
}
