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
  const indexLabel = String(controller.activeIndex + 1).padStart(2, '0');
  const totalLabel = String(LANDING_MODELS.length).padStart(2, '0');

  return (
    <>
      <CarouselStage controller={controller} items={LANDING_MODELS} />

      {/* 원본 spotlight 의 ArtworkMeta + GalleryControls 를 옮긴 것이다:
          가운데 위에 조작 힌트, 아래 줄에 좌: 큰 인덱스 / 가운데: 점 / 우: 화살표.
          원본에서 힌트와 점 사이에 있던 작품명·연도·카테고리·평점 줄은 **뺐다** — 카드가
          가상 모델이라 채울 실명·실적이 없고, 지어내면 실재하는 모델 정보로 읽힌다.
          그 자리에는 가상 모델 고지가 선다. 사용자가 카드에 무엇을 붙일지 정하면 그때
          되살린다. */}
      <section aria-label="예시 이미지 갤러리 조작" className={s.galleryMeta}>
        <div className={s.galleryLines}>
          <p className={s.galleryHint} aria-hidden="true">
            DRAG · SWIPE · ARROW KEYS
          </p>
          {/* 고지는 스테이지 **아래**, 원본의 작품명 자리다. 예전엔 스테이지 위에 뒀다
              (폰에서 스테이지가 78vh 라 아래 두면 첫 화면 밖으로 밀린다는 이유) — 지금은
              사진 한 장 한 장에 '예시' 배지(CarouselStage 의 .badgeNotice)가 박혀 있어
              사진과 같은 화면에 고지가 있다는 조건은 그쪽이 지키고, 이 줄은 문장으로
              한 번 더 못박는 역할이다. */}
          <p className={s.galleryNotice}>
            <Icon name="info" size={14} stroke={2} />
            위 이미지는 전부 가상 모델 예시입니다. 실제 등록된 모델이 아닙니다.
          </p>
        </div>

        <div className={s.galleryControls}>
          <p className={s.galleryIndex} aria-hidden="true">
            <span className={s.galleryIndexNow}>{indexLabel}</span>
            <span className={s.galleryIndexTotal}>/ {totalLabel}</span>
          </p>

          {/* 점은 탭이 아니다 — role="tab" 은 자기가 여는 tabpanel 을 가리켜야 하는데 여기엔
              패널이 없고 카드 14장이 한 스테이지 안에서 돌 뿐이다. 그대로 두면 스크린리더가
              "탭 1/14, 선택 안 됨"으로 읽어 없는 구조를 안내한다. 지금 위치는 스테이지의
              카드와 같은 방식(aria-current)으로 알린다. */}
          <div className={s.dots}>
            {LANDING_MODELS.map((item, index) => (
              <button
                aria-current={index === controller.activeIndex ? 'true' : undefined}
                aria-label={`${index + 1}번 이미지 보기`}
                className={index === controller.activeIndex ? s.dotActive : s.dot}
                key={item.id}
                onClick={() => controller.goTo(index)}
                type="button"
              />
            ))}
          </div>

          <div className={s.galleryArrows}>
            <button
              aria-label="이전 이미지"
              className={s.galleryArrow}
              onClick={() => controller.goBy(-1)}
              type="button"
            >
              <Icon name="chevLeft" size={22} stroke={2.2} />
            </button>
            <button
              aria-label="다음 이미지"
              className={s.galleryArrow}
              onClick={() => controller.goBy(1)}
              type="button"
            >
              <Icon name="chevRight" size={22} stroke={2.2} />
            </button>
          </div>
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
