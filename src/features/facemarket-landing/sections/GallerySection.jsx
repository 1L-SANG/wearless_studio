/* 캐러셀 + 메타 바. 이미지는 전부 가상 모델이고, 그 사실이 화면에 상시 보인다 —
   고지가 없으면 이미 등록된 실존 모델 목록으로 읽힌다.

   래퍼 없이 프래그먼트로 돌려준다. 스테이지와 메타 바는 FacemarketLanding.jsx 의
   첫 화면 그리드(.screen — 행: 히어로 / 스테이지 / 메타 바)의 **직접 자식**이어야
   스테이지가 1fr 행을 채우고 메타 바가 그 밑에 붙는다. 컨트롤러는 둘이 같이 써야
   하므로 이 컴포넌트가 만든다. */
import { Icon } from '@/components/ui.jsx';
import { CarouselStage } from '../carousel/CarouselStage.jsx';
import { useCarouselController } from '../carousel/useCarouselController.js';
import { LANDING_MODELS } from '../data/landingModels.js';
import s from '../FacemarketLanding.module.css';

export function GallerySection() {
  // 첫 화면은 01번 카드가 가운데다. 무한 루프라 어느 인덱스로 시작해도 좌우에 이웃이
  // 보이므로, 카드 배지·점 번호와 어긋나지 않는 0 으로 연다.
  const controller = useCarouselController(LANDING_MODELS.length, 0);

  // 두 자리 고정. 원본이 '04 / 07' 처럼 앞을 0 으로 채워 자릿수가 흔들리지 않게 했고,
  // 여기서도 1↔10 을 오갈 때 큰 숫자의 폭이 바뀌면 옆 요소가 밀린다.

  return (
    <>
      <CarouselStage controller={controller} items={LANDING_MODELS} />

      {/* 원본 spotlight 의 ArtworkMeta + GalleryControls 를 옮긴 것이다:
          가운데 위에 조작 힌트, 아래 줄에 좌: 큰 인덱스 / 가운데: 점 / 우: 화살표.
          원본에서 힌트와 점 사이에 있던 작품명·연도·카테고리·평점 줄은 **뺐다** — 카드가
          가상 모델이라 채울 실명·실적이 없고, 지어내면 실재하는 모델 정보로 읽힌다.
          그 자리에는 가상 모델 고지가 선다. 사용자가 카드에 무엇을 붙일지 정하면 그때
          되살린다. */}
      <section aria-label="예시 이미지 안내" className={s.galleryMeta}>
        <div className={s.galleryLines}>
          {/* 조작 힌트(DRAG · SWIPE · ARROW KEYS)·큰 인덱스(01/14)·점 내비·좌우 화살표 버튼은
              2026-09-03 오너 지시로 차례로 전부 제거됐다. 드래그·스와이프·키보드 조작 자체는
              useCarouselController 에 그대로 살아 있다(버튼만 없다). */}
          {/* 고지는 스테이지 **아래**, 원본의 작품명 자리다. 예전엔 스테이지 위에 뒀다
              (폰에서 스테이지가 78vh 라 아래 두면 첫 화면 밖으로 밀린다는 이유) — 지금은
              사진 한 장 한 장에 '예시' 배지(CarouselStage 의 .badgeNotice)가 박혀 있어
              사진과 같은 화면에 고지가 있다는 조건은 그쪽이 지키고, 이 줄은 문장으로
              한 번 더 못박는 역할이다. */}
          <p className={s.galleryNotice}>
            <Icon name="info" size={14} stroke={2} />
            위 이미지는 전부 가상 모델 예시입니다. 실제 등록된 모델이 아닙니다.
          </p>
          {/* 신뢰 pill 3개 — 화살표 버튼이 서던 자리(2026-09-03 저녁 오너 지시: 제목 위 → 여기).
              보호 장치를 사람 말로: 문구는 blueprint §7·§9 의 실제 장치(사용 원장·규칙 게이트·
              철회)와 1:1 이라 과장이 없다. C2PA·블록체인은 첫 항목의 작은 글씨로만. */}
          <ul aria-label="보호 장치" className={s.trustPills}>
            <li>위조 불가 사용 기록 <small>C2PA · 블록체인</small></li>
            <li>정해진 범위 외 사용 불가</li>
            <li>언제든지 라이선스 철회 가능</li>
          </ul>
        </div>

        {/* 큰 인덱스는 aria-hidden 이다(숫자 두 덩이로 쪼개져 있어 그대로 읽히면 어수선하다).
            대신 위치 변화를 여기서 한 문장으로 알린다 — 드래그·키보드로 옮겨도 스크린리더
            사용자가 현재 위치를 안다. */}
        <p className={s.srOnly} aria-live="polite" aria-atomic="true">
          {`${LANDING_MODELS.length}장 중 ${controller.activeIndex + 1}번째 이미지`}
        </p>
      </section>
    </>
  );
}
