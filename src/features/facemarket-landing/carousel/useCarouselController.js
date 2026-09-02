/* =============================================================
   facemarket-landing/carousel/useCarouselController.js
   포인터 드래그·스와이프·키보드를 연속 목표값(target)으로 바꾼다.
   spotlight 프로토타입(useCarouselController.ts) 이식 — 상수 그대로.

   원본과 다른 점 하나: 드래그로 끝난 포인터가 카드의 click 까지 발화시켜
   엉뚱한 카드로 점프하던 걸 consumeDragClick 으로 막는다.

   자동 회전(2026-09-02 사용자 지시: "평소에는 계속 천천히 돌아가는데 클릭해서 움직이면
   꺼지고, 안 만지면 5초쯤 뒤에 다시"):
     · 목표값을 100ms 마다 0.0125칸씩 민다(한 장에 8초). 매 프레임(60Hz) setState 하면
       카드 14장이 그만큼 재렌더되므로(파일 머리말의 그 이유) 10Hz 로만 밀고, 사이는
       스테이지의 감쇠(λ=9, 시정수 111ms)가 메운다 — 화면에는 끊김 없이 흐르는 것으로 보인다.
       정지 상태가 없다: 감쇠의 정상 지연이 0.014칸이라 SETTLE_EPSILON(1e-4)에 영원히 못 닿아
       rAF 루프도 잠들지 않는다(그게 의도다 — 계속 도는 게 요구사항이다). 속도를 더 낮출 거면
       그 지연(속도 × 0.111)이 1e-4 위에 남는지 확인해라 — 밑으로 내려가면 툭툭 끊긴다.
     · 멈추는 조건 넷: 사용자 조작(suspendAutoplay — 3초 뒤 자동 재개) / 스테이지 안에
       포커스가 있음 / 캐러셀이 화면 밖 / '동작 줄이기'. 뒤 셋은 5초 타이머가 아니라
       조건이 풀릴 때 곧바로 재개한다.
     · 포커스로 멈추는 건 **키보드로 들어온 포커스뿐**이다. 마우스로 카드를 클릭해도 그
       버튼은 포커스를 받는데, 그것까지 세면 클릭 뒤 3초가 지나도 영영 안 돈다 — 실제로 그
       증상("안 움직이는데")이 났다. 마지막 입력이 키보드였는지는 우리가 직접 센다
       (keyboardModality) — :focus-visible 은 브라우저마다 답이 갈려 못 믿는다.
       마우스 조작으로 인한 정지는 3초 타이머가 맡는다.
     · 포커스를 왜 세느냐 — 자동 회전이 activeIndex 를 바꾸면 스테이지의 포커스 추종
       이펙트가 키보드 사용자의 포커스를 다른 카드로 끌고 간다. 탭으로 들어와 있는 동안은
       돌지 않는다.
     · '동작 줄이기'에서 아예 안 도는 건 접근성 요구다(WCAG 2.2.2 의 자동 재생 모션).
       속도를 늦추는 걸로 대신하지 마라.

   불변식(깨지면 랜딩이 커서를 따라 도는 유령 캐러셀이 된다):
   pointer.current.id 로 무장하는 곳은 onPointerDown 한 곳이지만, 무장을 푸는 길은
   반드시 여럿이어야 한다. 캡처가 pointerdown 이 아니라 가로 8px 확정 시점에 걸리기
   때문에(그 이유는 onPointerDown 주석) '스테이지 핸들러에 닿는 pointerup' 하나만
   믿을 수 없다. 지금 탈출구는 셋이고, 셋 다 resetPointer 로 모인다.
     ① onPointerMove 의 사장 상태 가드(마우스 + buttons === 0)
     ② window 의 pointerup/pointercancel — 스테이지 밖 릴리스
     ③ .stage 의 onLostPointerCapture — 캡처 상실
   하나라도 지우기 전에 "그 경로로 무장이 남지 않는가"를 먼저 답해야 한다.
   ============================================================= */
import { useCallback, useEffect, useRef, useState } from 'react';
import { modulo, snapTarget, targetForIndex } from './carouselMath.js';
import { usePrefersReducedMotion } from './usePrefersReducedMotion.js';

const DRAG_PIXELS_PER_ITEM = 170;
const HORIZONTAL_INTENT_PIXELS = 8;
// 0 나눗셈과 단발 노이즈만 막는 하한. 원본은 1/60 이었는데, 그러면 120Hz 기기
// (pointermove 간격 ≈8.3ms)의 플릭 속도가 정확히 절반으로 측정돼 같은 손동작이
// 60Hz 의 절반만 넘어간다. 실제 dt 를 쓰되 하한만 240Hz 프레임 간격으로 낮춘다.
const MIN_DELTA_SECONDS = 1 / 240;
// 마지막 움직임 뒤 이만큼 지나서 손을 뗐으면 "끌다가 멈춰서 위치를 맞춘" 것으로 본다.
const STALE_VELOCITY_MS = 100;
// 드래그 표식의 수명. 드래그 끝에 따라오는 compat click 한 번만 막으면 되고,
// 그 click 은 마우스면 같은 틱, 터치여도 수백 ms 안에 온다. 이 창을 넘기면 표식은
// 스스로 무효가 된다 — 안 그러면 (click 이 없는 pointercancel 이나 스테이지 여백에서
// 손을 뗀 경우) 표식이 계속 남아 다음 카드 활성화를 통째로 삼킨다.
const DRAG_CLICK_WINDOW_MS = 500;

/* 자동 회전 속도 — 카드 한 장에 8초. 4초로 시작해 사용자가 "0.5배속"으로 절반 낮춘 값이다.
   한 번 3초로 올렸다가 "갑자기 너무 빨라졌다"고 되돌아왔다 — 이 값은 이제 만지지 마라.
   (그때의 "3초"는 속도가 아니라 아래 재개 대기 시간을 가리킨 말이었다.)
   틱 간격은 10Hz — 이보다 촘촘하면 재렌더가 늘고, 성기면(예: 500ms) 감쇠가 틱 사이에
   수렴을 마쳐 흐르는 게 아니라 툭툭 끊겨 보인다(시정수 111ms 의 5배 = 555ms 가 경계). */
const AUTOPLAY_SECONDS_PER_ITEM = 8;
const AUTOPLAY_ITEMS_PER_SECOND = 1 / AUTOPLAY_SECONDS_PER_ITEM;
const AUTOPLAY_TICK_MS = 100;
/* 조작 뒤 재개까지. 처음엔 5초였는데 사용자가 3초로 줄였다. */
const AUTOPLAY_RESUME_MS = 3000;

const now = () =>
  (typeof performance !== 'undefined' && typeof performance.now === 'function'
    ? performance.now()
    : Date.now());

export function useCarouselController(itemCount, initialIndex = 0) {
  const pointer = useRef({
    id: -1,
    startX: 0,
    startTarget: initialIndex,
    lastX: 0,
    lastTime: 0,
    velocity: 0,
    hasHorizontalIntent: false,
  });
  // 방금 끝난 드래그의 시각(0 = 없음). click 핸들러가 읽고 지운다.
  const dragEndedAt = useRef(0);
  /* target 은 반드시 React state 로 남는다. CarouselStage 의 rAF 루프는 도착하면 스스로
     멈추고 `useEffect(..., [controller.target, ...])` 로만 다시 깨어나므로, target 을
     ref 로 돌리면 그 wake 이펙트가 영영 발화하지 않아 캐러셀이 통째로 안 움직인다.
     드래그 중 재렌더를 줄이려면 이동을 알리는 tick state 나 subscribe 를 따로 내보내야
     한다 — 그때까지 이 useState 는 유지한다. */
  const [target, setTarget] = useState(initialIndex);
  const [isDragging, setDragging] = useState(false);
  const activeIndex = itemCount > 0 ? modulo(Math.round(target), itemCount) : 0;

  /* 자동 회전 게이트 넷. autoplayOn 만 타이머로 되돌아오고(조작 뒤 5초), 나머지 셋은
     조건이 풀리는 즉시 재개한다. */
  const [autoplayOn, setAutoplayOn] = useState(true);
  const [focusHeld, setFocusHeld] = useState(false);
  const [inView, setInView] = useState(true);
  const reducedMotion = usePrefersReducedMotion();
  const resumeTimer = useRef(0);

  /* 조작이 있었다 — 자동 회전을 끄고 3초 뒤 재개를 예약한다. 조작이 이어지면 그때마다
     타이머를 새로 잡으므로 "마지막 조작으로부터 3초"가 된다.
     드래그 중 pointermove 마다 부르지는 않는다: pointerdown 에서 한 번 끄고, 손을 뗄 때
     (releasePointer) 다시 불러 거기서부터 3초를 센다. */
  const suspendAutoplay = useCallback(() => {
    setAutoplayOn(false);
    clearTimeout(resumeTimer.current);
    resumeTimer.current = setTimeout(() => setAutoplayOn(true), AUTOPLAY_RESUME_MS);
  }, []);

  useEffect(() => () => clearTimeout(resumeTimer.current), []);

  const goBy = useCallback((delta) => {
    suspendAutoplay();
    setTarget((current) => current + delta);
  }, [suspendAutoplay]);

  const goTo = useCallback(
    (index) => {
      suspendAutoplay();
      setTarget((current) => targetForIndex(current, index, itemCount));
    },
    [itemCount, suspendAutoplay],
  );

  const handleKeyDown = useCallback(
    (event) => {
      if (event.key === 'ArrowLeft') { event.preventDefault(); goBy(-1); }
      if (event.key === 'ArrowRight') { event.preventDefault(); goBy(1); }
    },
    [goBy],
  );

  /* 목표값을 조금씩 민다. 여기서 goBy 를 쓰면 안 된다 — goBy 는 suspendAutoplay 를 부르므로
     자동 회전이 자기 자신을 매 틱 꺼 버린다.
     document.hidden 을 보는 이유: 백그라운드 탭에서는 setInterval 이 1Hz 로 눌리는데 그동안
     목표값만 쌓이면 탭으로 돌아온 순간 그만큼을 한 번에 훑고 지나간다(rAF 는 멈춰 있어서
     화면은 그 사이 아무것도 안 그렸다). 안 밀면 돌아왔을 때 있던 자리에서 이어진다. */
  useEffect(() => {
    if (!autoplayOn || focusHeld || !inView || reducedMotion || itemCount < 2) return undefined;

    const step = (AUTOPLAY_ITEMS_PER_SECOND * AUTOPLAY_TICK_MS) / 1000;
    const id = setInterval(() => {
      if (typeof document !== 'undefined' && document.hidden) return;
      setTarget((current) => current + step);
    }, AUTOPLAY_TICK_MS);
    return () => clearInterval(id);
  }, [autoplayOn, focusHeld, inView, reducedMotion, itemCount]);

  /* 마지막 입력이 키보드였나. 포커스가 스테이지로 들어올 때 그게 '탭으로 들어온 것'인지
     '마우스로 카드를 누른 것'인지 가르는 데 쓴다.

     :focus-visible 로 가르지 않는 이유: 그건 브라우저 휴리스틱이라 같은 동작에도 답이
     갈린다 — 헤드리스 크롬에서는 스크립트 `focus()` 에도 true 가 나왔다(실측). 이 판정이
     틀리는 쪽으로 기울면 마우스 사용자에게 자동 회전이 영영 안 돌아온다. 실제로 그
     증상("안 움직이는데")이 났고, 그래서 판정을 우리가 직접 한다.
     캡처 단계로 듣는다 — 카드가 stopPropagation 을 하지 않지만, 나중에 누가 걸어도
     이 판정만은 놓치지 않게. */
  const keyboardModality = useRef(false);
  useEffect(() => {
    const markKeyboard = () => { keyboardModality.current = true; };
    const markPointer = () => { keyboardModality.current = false; };
    window.addEventListener('keydown', markKeyboard, true);
    window.addEventListener('pointerdown', markPointer, true);
    return () => {
      window.removeEventListener('keydown', markKeyboard, true);
      window.removeEventListener('pointerdown', markPointer, true);
    };
  }, []);

  /* 스테이지 안으로 **키보드** 포커스가 들어오면 멈춘다(머리말 참고). 마우스로 누른 경우는
     그 pointerdown 이 이미 modality 를 false 로 돌려놨으므로 여기서 붙잡지 않는다 —
     그쪽 정지는 3초 타이머(suspendAutoplay)가 맡는다.
     blur 는 캡처 단계에서 받되 스테이지 **안에서 안으로** 옮겨 다니는 경우(카드 → 옆 카드)는
     나간 게 아니다. */
  const onFocusCapture = useCallback(() => {
    if (keyboardModality.current) setFocusHeld(true);
  }, []);
  const onBlurCapture = useCallback((event) => {
    if (event.currentTarget.contains(event.relatedTarget)) return;
    setFocusHeld(false);
  }, []);

  const resetPointer = useCallback(() => {
    pointer.current.id = -1;
    pointer.current.velocity = 0;
    pointer.current.hasHorizontalIntent = false;
  }, []);

  const onPointerDown = useCallback(
    (event) => {
      // 누른 순간 자동 회전을 끈다. 카드를 집으려는 손 밑에서 카드가 계속 흐르면
      // 드래그 시작점과 목표가 어긋난다.
      suspendAutoplay();
      // 새 포인터가 시작하면 지난 드래그 흔적을 지운다 — 스테이지 밖에서 손을 뗀 뒤
      // 다음에 진짜로 누른 클릭이 삼켜지지 않게.
      dragEndedAt.current = 0;
      pointer.current = {
        id: event.pointerId,
        startX: event.clientX,
        startTarget: target,
        lastX: event.clientX,
        lastTime: event.timeStamp,
        velocity: 0,
        hasHorizontalIntent: false,
      };
      // 여기서 setPointerCapture 를 걸면 안 된다. 캡처가 걸린 뒤엔 pointerup 이
      // 캡처 대상(.stage)으로 리타깃되고, click 은 pointerdown/pointerup 타깃의
      // 최근접 공통 조상에 발화하므로 공통 조상이 .stage 가 된다 → 자식인 카드
      // button 의 onClick(goTo)이 영영 안 불린다. 마우스만 죽고 터치 탭은 살아서
      // QA 에서 놓치기 쉽다. 캡처는 가로 드래그가 확정되는 onPointerMove 로 미룬다.
    },
    [target, suspendAutoplay],
  );

  const onPointerMove = useCallback((event) => {
    if (event.pointerId !== pointer.current.id) return;

    /* 탈출구 ① — 사장된 무장 상태를 여기서 끊는다.
       마우스의 pointerId 는 1 로 고정이라, 어떤 이유로든 pointerup 을 놓치면(스테이지
       밖 릴리스·창 밖 릴리스·앱 전환) 다음번 '버튼 안 누른 맨 호버'가 그 id 와 그대로
       매칭돼 캐러셀이 커서를 따라 돌고 커서가 grabbing 으로 굳는다. 호버에는 buttons
       비트가 없으니 그걸로 판별한다.
       마우스에만 적용하는 이유: 터치는 접촉마다 pointerId 가 새로 발급돼 사장된 무장이
       스스로 낫는다. 펜 호버도 buttons 가 0 이라 pointerType 을 반드시 같이 본다
       (안 보면 펜을 들었다 놓는 정상 동작이 드래그 취소로 오인된다).
       ※ 테스트에서 드래그를 흉내 낼 때 pointermove 에 buttons: 1 을 반드시 넣어라 —
         합성 이벤트의 buttons 기본값은 0 이라 여기서 걸러진다.
       여기서 releasePointerCapture 를 부르지 않는 것도 의도다: buttons 가 0 이라는 건
       UA 가 릴리스를 이미 처리했다는 뜻이고 그 순간 캡처는 암묵 해제된다. 굳이 부르면
       lostpointercapture(탈출구 ③)가 다시 releasePointer 를 태워 snapTarget 이 두 번
       먹는다 — 굳이 넣겠다면 반드시 resetPointer() 뒤에 놓아라. */
    if (event.pointerType === 'mouse' && event.buttons === 0) {
      if (pointer.current.hasHorizontalIntent) {
        // 진짜 드래그 도중에 릴리스를 놓친 경우. 손은 이미 멈춰 있었으니 관성 없이(0)
        // 가장 가까운 칸에 붙여 마무리한다 — releasePointer 의 STALE_VELOCITY_MS 분기와
        // 같은 판단이다. 표식(dragEndedAt)은 세우지 않는다: 그 click 은 이미 스테이지
        // 밖에서 지나갔거나 아예 없었고, 여기서 세우면 다음 카드 클릭을 삼킨다.
        setTarget((current) => snapTarget(current, 0));
        setDragging(false);
      }
      resetPointer();
      return;
    }

    const deltaX = event.clientX - pointer.current.startX;
    // 세로 스크롤 의도를 뺏지 않으려고 8px 넘게 가로로 움직여야 드래그로 친다.
    if (!pointer.current.hasHorizontalIntent) {
      if (Math.abs(deltaX) < HORIZONTAL_INTENT_PIXELS) return;
      pointer.current.hasHorizontalIntent = true;
      // 드래그가 확정된 지금 캡처를 건다. 단순 클릭은 여기까지 못 오므로 캡처가 아예
      // 없어 click 이 카드에 정상 전달되고, 드래그일 때만 스테이지 밖 추적이 유지된다.
      try {
        event.currentTarget.setPointerCapture(event.pointerId);
      } catch {
        // 포인터가 이미 끝났거나 엘리먼트가 사라진 경우. 캡처 없이도 드래그는 굴러간다.
      }
      setDragging(true);
    }

    const deltaSeconds = Math.max((event.timeStamp - pointer.current.lastTime) / 1000, MIN_DELTA_SECONDS);
    pointer.current.velocity =
      -(event.clientX - pointer.current.lastX) / DRAG_PIXELS_PER_ITEM / deltaSeconds;
    pointer.current.lastX = event.clientX;
    pointer.current.lastTime = event.timeStamp;
    setTarget(pointer.current.startTarget - deltaX / DRAG_PIXELS_PER_ITEM);
  }, [resetPointer]);

  const releasePointer = useCallback(
    (event) => {
      /* 이 함수는 세 곳에서 들어온다 — .stage 의 onPointerUp/onPointerCancel(React
         합성), window 의 pointerup/pointercancel(네이티브), .stage 의
         onLostPointerCapture. 같은 릴리스가 둘 이상 도착해도 안전한 건 오직 이 대조
         한 줄 덕분이다: 먼저 온 쪽이 resetPointer 로 id 를 -1 로 돌려놓으므로 뒤따르는
         쪽은 여기서 끝난다. 이 줄을 지우면 한 번의 릴리스가 snapTarget 을 두 번 먹인다.
         네이티브 이벤트로도 들어오므로 아래에서 currentTarget 을 만질 때는 DOM 메서드가
         없는 window 일 수 있다고 보고 옵셔널로 부른다. */
      if (event.pointerId !== pointer.current.id) return;

      // 마지막 move 이후 시간이 뜬 채로 뗐으면 손이 멈춰 있던 것이다. velocity 는
      // move 에서만 갱신되므로 그대로 쓰면 멈춰서 맞춰 놓은 위치가 관성으로 밀려난다.
      const idleMs = event.timeStamp - pointer.current.lastTime;
      const releaseVelocity = idleMs > STALE_VELOCITY_MS ? 0 : pointer.current.velocity;
      // pointercancel 은 뒤따르는 click 이 없다 — 표식을 세우면 아무도 소비하지 않고
      // 남아서 다음 활성화를 삼킨다. 취소된 제스처엔 표식을 세우지 않는다.
      dragEndedAt.current =
        event.type !== 'pointercancel' && pointer.current.hasHorizontalIntent ? now() : 0;
      if (event.currentTarget?.hasPointerCapture?.(event.pointerId)) {
        event.currentTarget.releasePointerCapture(event.pointerId);
      }
      // 누적(target)은 여기서 자르지 않는다. CarouselStage 의 렌더 위치는 target 을
      // 감쇠로 쫓아가는 연속값이라, target 만 705 → 5 로 한 번에 접히면 그 사이 700칸을
      // 실제로 훑어 지나간다(= 50바퀴 회전). 화면상 "같은 위치"인 건 layout 뿐이고
      // 애니메이션은 아니다. 자세한 사유는 carouselMath.js 하단 주석 참고.
      setTarget((current) => snapTarget(current, releaseVelocity));
      setDragging(false);
      resetPointer();
      // 손을 뗀 시점부터 5초를 다시 센다.
      suspendAutoplay();
    },
    [resetPointer, suspendAutoplay],
  );

  /* 탈출구 ② — 스테이지 밖에서 뗀 손도 같은 정리 경로(releasePointer)로 흘린다.
     캡처를 가로 8px 확정 시점까지 미룬 뒤로(위 onPointerDown 주석의 근거 — 되돌리면
     데스크톱 카드 클릭이 죽는다) 8px 미만으로 끝난 제스처에는 캡처가 아예 없다. 그런
     pointerup 이 스테이지 밖에서 일어나면 이벤트 경로에 .stage 가 없어(.gallery 는
     .stage 의 부모다) React 핸들러가 영영 안 불리고 무장이 남는다.

     리스너를 무장할 때만 붙였다 떼지 않고 상시로 두는 이유: releasePointer 는 첫 줄에서
     pointerId 를 대조해 무장 중이 아니면 즉시 빠져나오므로 비용이 사실상 없고, 반대로
     '무장할 때 붙이고 해제할 때 뗀다'로 만들면 그 해제를 놓치는 순간 리스너가 남는
     같은 종류의 구멍이 하나 더 생긴다. 탈출구를 세는 코드는 탈출구가 하나여야 한다.
     스테이지 안에서 뗀 경우는 React 핸들러(루트 컨테이너)가 window 보다 먼저 처리하고
     여기는 id 가 -1 이라 no-op 이 된다. */
  useEffect(() => {
    const onWindowRelease = (event) => releasePointer(event);
    window.addEventListener('pointerup', onWindowRelease);
    window.addEventListener('pointercancel', onWindowRelease);
    return () => {
      window.removeEventListener('pointerup', onWindowRelease);
      window.removeEventListener('pointercancel', onWindowRelease);
    };
  }, [releasePointer]);

  // 드래그 끝의 click 인지 확인하고 표식을 지운다. true 면 클릭을 무시해야 한다.
  // 계약: 호출부(CarouselStage 의 카드 onClick)는 click 이벤트를 반드시 넘긴다.
  // 그래야 키보드 활성화(Enter/Space)를 detail === 0 으로 걸러 항상 통과시킬 수 있다
  // — 포인터에서 유래하지 않은 활성화는 드래그와 무관하기 때문. 인자 없이 부르면
  // 드래그 직후 DRAG_CLICK_WINDOW_MS 안의 Enter 가 표식에 걸려 조용히 삼켜진다.
  const consumeDragClick = useCallback((event) => {
    if (event && event.detail === 0) return false;
    const wasDragged = dragEndedAt.current > 0 && now() - dragEndedAt.current < DRAG_CLICK_WINDOW_MS;
    dragEndedAt.current = 0;
    return wasDragged;
  }, []);

  return {
    target,
    activeIndex,
    isDragging,
    bind: {
      onPointerDown,
      onPointerMove,
      onPointerUp: releasePointer,
      onPointerCancel: releasePointer,
      /* 탈출구 ③ — 캡처를 잃는 것도 제스처의 끝이다(엘리먼트가 사라지거나 브라우저가
         캡처를 회수한 경우). 정상 릴리스에서는 pointerup 이 먼저 처리해 id 를 -1 로
         돌려놓으므로 여기는 no-op 이 되고, releasePointer 안의 releasePointerCapture 도
         hasPointerCapture 가 false 라 건너뛴다 — 이중 실행이 아니다. */
      onLostPointerCapture: releasePointer,
      onFocusCapture,
      onBlurCapture,
    },
    goBy,
    goTo,
    handleKeyDown,
    consumeDragClick,
    /* 캐러셀이 화면에 있는지 — 스테이지가 IntersectionObserver 로 알려준다. 컨트롤러는
       DOM 노드를 모르니 관측은 스테이지가 하고 판단만 여기서 한다. useState 의 setter 라
       참조가 고정이므로 이펙트 의존성에 넣어도 안전하다. */
    setInView,
  };
}
