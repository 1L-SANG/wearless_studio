/* =============================================================
   facemarket-landing/carousel/CarouselStage.jsx
   spotlight 캐러셀의 CSS 3D 재구현. 원본의 Canvas·ArtworkScene·
   ArtworkCardMesh·CssGalleryFallback 네 파일이 여기 하나로 합쳐졌다.

   WebGL 이 필요 없는 이유: 원본 셰이더가 하는 일은 둥근 모서리 마스크와
   opacity 곱하기뿐이고, 그림자는 별도 plane, 라벨은 캔버스에 그린 글자였다.
   각각 border-radius·opacity·box-shadow·진짜 텍스트로 대체된다.

   함정 넷 (전부 감사에서 실제로 터진 것들):
   1) 매 프레임 setState 하면 카드 14장이 60fps 로 재렌더된다. 그래서 위치는
      ref 로 들고 rAF 안에서 style 을 직접 쓴다.
      (단서: 드래그 중에는 컨트롤러가 pointermove 마다 target 을 setState 하므로
       재렌더가 실제로 일어난다. 여기서 할 수 있는 건 ref 콜백을 인덱스별로
       캐시해 14개 ref 재부착만이라도 막는 것뿐 — target 을 ref 로 돌리는 건
       useCarouselController 쪽 일이다.)
   2) perspective 만으로는 브라우저가 카드를 깊이순으로 정렬하지 않는다
      (transform-style: preserve-3d 가 아니면 각 자식이 개별 평탄화된다).
      그래서 z 를 z-index 로 직접 번역한다 — 안 하면 카메라 앞으로 나온 카드가
      뒤 카드에 가린다.
   3) rAF 루프는 목표에 닿으면 스스로 멈춘다. 안 멈추면 아무도 안 만지는 랜딩에서도
      초당 14장×스타일 4개를 영원히 덮어쓴다(캐러셀이 화면 밖으로 스크롤돼도).
      다시 켜는 건 target·드래그·모션설정이 바뀔 때뿐이다.
   4) 포커스를 쥔 카드는 절대 visibility:hidden 으로 만들지 않는다. 숨기는 순간
      브라우저가 포커스를 body 로 회수해 방향키 조작이 통째로 죽는다.
      카드는 로빙 탭인덱스(활성 카드만 tabIndex 0)로 두고 이동할 때 포커스도 옮긴다.
   ============================================================= */
import { useEffect, useRef, useState } from 'react';
import { shortestWrappedOffset } from './carouselMath.js';
import { layoutForOffset, metricsForAspect } from './sceneLayout.js';
import { CAMERA_Z, cardTransform, fillScale } from './cssProjection.js';
import s from './CarouselStage.module.css';

const DAMP_IDLE = 9;
const DAMP_DRAG = 18;
/* 목표와 이만큼 가까워지면 도착으로 친다(단위는 '칸'이라 0.0001칸 ≈ 0.02px).
   지수 감쇠는 목표에 정확히 닿지 않으므로 이 임계가 없으면 '정지' 상태 자체가 없다. */
const SETTLE_EPSILON = 1e-4;
/* 포커스 이동 재시도 상한(프레임). 목적지 카드가 감쇠로 보이게 되기까지 최악이
   폰 버킷(edgeFade 1.9)에서 5칸 점프 ≈ 7프레임이라 넉넉하다. 상한을 두는 건
   목적지가 영영 안 보이는 경우(포커스가 딴 데로 갔거나 레이아웃이 죽은 경우)에
   rAF 를 무한히 돌리지 않기 위해서다. */
const FOCUS_RETRY_FRAMES = 120;

function usePrefersReducedMotion() {
  const [reduced, setReduced] = useState(false);

  useEffect(() => {
    const query = window.matchMedia('(prefers-reduced-motion: reduce)');
    const update = () => setReduced(query.matches);
    update();
    query.addEventListener('change', update);
    return () => query.removeEventListener('change', update);
  }, []);

  return reduced;
}

export function CarouselStage({ items, controller }) {
  const stageRef = useRef(null);
  const cardRefs = useRef([]);
  const cardRefSetters = useRef([]);
  const positionRef = useRef(controller.target);
  const targetRef = useRef(controller.target);
  const draggingRef = useRef(false);
  const reducedRef = useRef(false);
  const wakeRef = useRef(null);
  const [stage, setStage] = useState({ width: 0, height: 0 });
  const reducedMotion = usePrefersReducedMotion();

  // rAF 루프가 읽을 최신값. 렌더마다 갱신하되 루프를 다시 만들지는 않는다.
  targetRef.current = controller.target;
  draggingRef.current = controller.isDragging;
  reducedRef.current = reducedMotion;

  /* 인덱스별 ref 콜백을 캐시한다. 인라인 화살표를 쓰면 렌더마다 새 함수가 되어 React 가
     카드 14개의 ref 를 (null → node) 로 다시 붙인다 — 드래그 중에는 그게 프레임마다다. */
  const cardRef = (index) => {
    if (!cardRefSetters.current[index]) {
      cardRefSetters.current[index] = (node) => { cardRefs.current[index] = node; };
    }
    return cardRefSetters.current[index];
  };

  // 원본이 three viewport 로 읽던 값 — 여기선 스테이지 DOM 의 실제 크기.
  // .stage 높이는 **부모 그리드 행**이 정한다(FacemarketLanding.module.css .screen 의
  // minmax(하한, 1fr) / 폰 clamp). 그 행은 카드 크기와 무관하다 — 스테이지가 overflow:hidden
  // 이라 자동 최소 높이가 0 이고 카드가 position:absolute 라 내재 높이에 안 잡히므로 —
  // 그래서 카드 크기 → 스테이지 높이 → 여기 setState → 카드 크기로 도는 되먹임이 없다.
  // 카드를 정적 그리드 아이템으로 되돌리면 그 되먹임이 되살아나 첫 로드에 높이가 두세 번 튄다.
  useEffect(() => {
    const node = stageRef.current;
    if (!node) return undefined;

    const observer = new ResizeObserver(([entry]) => {
      const box = entry.contentRect;
      setStage({ width: box.width, height: box.height });
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!(stage.height > 0)) return undefined;

    const metrics = metricsForAspect(stage.width / stage.height);
    // 계수는 높이만이 아니라 폭에서도 잰다(cssProjection.js fillScale 머리말) — 데스크톱에서
    // 보이는 5장이 폭을 채운다. perspective 도 같은 k 로 걸어야 원근 불변식이 선다.
    const k = fillScale(stage.width, stage.height, metrics);
    const count = items.length;
    let frame = 0;
    let previous = 0;

    // 애니메이션이 실제로 도는 동안에만 will-change 를 켠다(CSS 가 이 속성을 읽는다).
    // 클래스에 상시로 박아두면 화면에 한 번도 안 나오는 카드까지 합성 레이어를 붙들고 있다.
    const setAnimating = (on) => {
      const node = stageRef.current;
      if (node) node.dataset.animating = on ? 'true' : 'false';
    };

    const paint = () => {
      const focused = document.activeElement;

      for (let index = 0; index < count; index += 1) {
        const card = cardRefs.current[index];
        if (!card) continue;

        const offset = shortestWrappedOffset(index, positionRef.current, count);
        const layout = layoutForOffset(offset, metrics);
        const faded = layout.opacity <= 0.01;
        card.style.transform = cardTransform(layout, k);
        card.style.opacity = String(layout.opacity);
        // 다 사라진 카드도 포커스를 쥐고 있으면 숨기지 않는다 — 숨기면 포커스가 body 로
        // 날아가 방향키가 스테이지에 닿지 않게 된다. 대신 클릭만 받지 않게 한다.
        card.style.visibility = faded && card !== focused ? 'hidden' : 'visible';
        card.style.pointerEvents = faded ? 'none' : '';
        card.style.zIndex = String(1000 + Math.round(layout.z * 100));
      }
    };

    const tick = (now) => {
      // 탭이 백그라운드였다가 돌아오면 delta 가 몇 초씩 튄다. 한 프레임에 다 감쇠하지
      // 않게 상한을 둔다(50ms).
      const delta = previous ? Math.min((now - previous) / 1000, 0.05) : 0;
      previous = now;

      const distance = targetRef.current - positionRef.current;
      // '동작 줄이기'를 켠 사용자에게는 3D 슬라이드를 재생하지 않는다. 감쇠를 빠르게 하는
      // 것만으로는(예전 λ=24) 원근 회전이 여전히 160ms 동안 보인다 — 즉시 확정한다.
      if (reducedRef.current || Math.abs(distance) < SETTLE_EPSILON) {
        positionRef.current = targetRef.current;
      } else {
        const lambda = draggingRef.current ? DAMP_DRAG : DAMP_IDLE;
        positionRef.current += distance * (1 - Math.exp(-lambda * delta));
      }
      paint();

      // 도착했고 드래그 중도 아니면 루프를 끈다. wake() 가 다음 입력에서 다시 켠다.
      // draggingRef 를 조건에 넣어도 루프가 사장되지 않는 근거는 컨트롤러 쪽에 있다:
      // useCarouselController 는 무장을 푸는 탈출구를 셋(호버 가드·window 릴리스·캡처
      // 상실) 갖고 있어 isDragging 이 반드시 false 로 돌아오고, 그 setState 가 아래
      // wake 이펙트(deps 에 controller.isDragging)를 한 번 더 태워 스냅 애니메이션까지
      // 마치고 여기서 멈춘다. 그 탈출구가 하나라도 사라지면 이 루프가 영원히 돈다.
      if (!draggingRef.current && positionRef.current === targetRef.current) {
        frame = 0;
        previous = 0;
        setAnimating(false);
        return;
      }
      frame = requestAnimationFrame(tick);
    };

    const wake = () => {
      if (frame) return;
      previous = 0;   // 멈춰 있던 시간이 첫 delta 로 잡히지 않게
      setAnimating(true);
      frame = requestAnimationFrame(tick);
    };

    wakeRef.current = wake;
    paint();
    wake();
    return () => {
      cancelAnimationFrame(frame);
      frame = 0;
      wakeRef.current = null;
      setAnimating(false);
    };
  }, [items.length, stage.height, stage.width]);

  // 목표·드래그·모션설정이 바뀌면 잠들어 있던 루프를 깨운다. 이 이펙트가 루프 이펙트보다
  // 뒤에 선언돼야 첫 마운트에서 wakeRef 가 이미 채워져 있다.
  useEffect(() => {
    if (wakeRef.current) wakeRef.current();
  }, [controller.target, controller.isDragging, reducedMotion]);

  /* 로빙 탭인덱스의 나머지 절반 — 활성 카드가 바뀌면 포커스도 따라간다.
     화살표 버튼이나 점에서 온 이동이면(포커스가 카드 밖) 포커스를 뺏지 않는다.
     preventScroll 은 필수다: 카드는 transform 으로 화면 밖까지 밀려나 있어서
     기본 focus() 가 페이지를 옆으로 끌고 간다.

     한 번만 시도하면 안 된다. 이 이펙트가 도는 시점의 카드 인라인 스타일은 직전 프레임
     paint() 가 쓴 값이고, 렌더 위치(positionRef)는 아직 새 target 을 못 따라잡았다.
     플릭 한 번이 3~5칸을 넘기면(snapTarget) 목적지 카드는 edgeFade 밖이라 여전히
     visibility:hidden 이고, hidden 요소에 건 focus() 는 아무 일도 하지 않는다.
     그러면 포커스는 opacity 0 인 옛 카드에 남아, Enter 가 방금 넘긴 카드로 되돌린다.
     그래서 감쇠가 목적지를 보이게 만들 때까지 프레임마다 다시 시도한다. */
  useEffect(() => {
    const active = cardRefs.current[controller.activeIndex];
    if (!active) return undefined;

    /* 포커스를 옮겨도 되는 상황인지 — 지금 포커스가 (활성 아닌) 카드 위에 있을 때만.
       재시도 중에 사용자가 캐러셀 밖으로 탭해 나가면 되뺏지 않는다. */
    const shouldMove = () => {
      const focused = document.activeElement;
      return Boolean(focused) && focused !== active && cardRefs.current.includes(focused);
    };
    if (!shouldMove()) return undefined;

    let frame = 0;
    let attempts = 0;
    const attempt = () => {
      frame = 0;
      if (!shouldMove()) return;
      active.focus({ preventScroll: true });
      // hidden 이라 무시됐으면 activeElement 가 안 바뀐다. 다음 프레임에 다시.
      if (document.activeElement === active) return;
      attempts += 1;
      if (attempts >= FOCUS_RETRY_FRAMES) return;
      frame = requestAnimationFrame(attempt);
    };
    attempt();

    return () => {
      if (frame) cancelAnimationFrame(frame);
    };
  }, [controller.activeIndex]);

  const ready = stage.height > 0;
  const metrics = ready ? metricsForAspect(stage.width / stage.height) : null;
  const k = ready ? fillScale(stage.width, stage.height, metrics) : 0;

  return (
    <div
      aria-label="가상 모델 예시 이미지"
      className={s.stage}
      onKeyDown={controller.handleKeyDown}
      ref={stageRef}
      role="region"
      style={{
        cursor: controller.isDragging ? 'grabbing' : 'grab',
        // ready 전에는 perspective 를 걸지 않는다. k=0 이면 perspective:0px 이 되어
        // 원근이 사실상 꺼지고 모든 카드 transform 이 원점으로 붕괴한다.
        // 카메라 거리 × k — 배율 불변식 D/(D−z) = P/(P−z·k) 는 P = D·k 일 때만 성립한다.
        perspective: ready ? `${CAMERA_Z * k}px` : undefined,
      }}
      tabIndex={0}
      {...controller.bind}
    >
      {items.map((item, index) => (
        <button
          aria-current={index === controller.activeIndex ? 'true' : undefined}
          className={s.card}
          key={item.id}
          // event 를 반드시 넘긴다. 컨트롤러는 이 이벤트로 키보드 활성화(Enter/Space,
          // detail === 0)를 가려내 드래그 표식과 무관하게 통과시킨다 — 안 넘기면
          // 드래그 직후 500ms 안의 Enter 가 조용히 삼켜진다.
          onClick={(event) => {
            if (controller.consumeDragClick(event)) return;
            controller.goTo(index);
          }}
          ref={cardRef(index)}
          style={metrics ? { width: `${metrics.cardWidth * k}px`, height: `${metrics.cardHeight * k}px` } : undefined}
          // 활성 카드만 탭 순서에 남긴다. 14장을 전부 탭으로 훑게 두면 포커스가 화면 밖
          // 카드로 흘러가고, 그 카드가 페이드아웃될 때 포커스를 잃는다.
          tabIndex={index === controller.activeIndex ? 0 : -1}
          type="button"
        >
          <img alt={item.alt} className={s.photo} draggable="false" src={item.src} />
          {/* 카드 메타는 번호뿐이다(이름·연도 같은 건 지어내면 실재 정보로 읽힌다).
              '예시'는 메타가 아니라 고지다 — 이미지만 잘려 공유돼도 가상 모델이라는
              사실이 같이 나가야 해서 카드 안에 박는다. */}
          {/* 화면에만 보이면 된다. img 의 alt 가 이미 "가상 모델 예시 이미지 01" 이라
              이걸 읽히면 스크린리더가 "…01 01 예시" 로 더듬는다. */}
          <span aria-hidden="true" className={s.badge}>
            <span className={s.badgeNumber}>{String(index + 1).padStart(2, '0')}</span>
            <span className={s.badgeNotice}>예시</span>
          </span>
        </button>
      ))}
    </div>
  );
}
