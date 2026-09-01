/* 캐러셀 섹션. 이미지는 전부 가상 모델이고, 그 사실이 화면에 상시 보인다 —
   고지가 없으면 이미 등록된 실존 모델 목록으로 읽힌다. */
import { Icon } from '@/components/ui.jsx';
import { CarouselStage } from '../carousel/CarouselStage.jsx';
import { useCarouselController } from '../carousel/useCarouselController.js';
import { LANDING_MODELS } from '../data/landingModels.js';
import s from '../FacemarketLanding.module.css';

export function GallerySection() {
  // 첫 화면은 01번 카드가 가운데다. 무한 루프라 어느 인덱스로 시작해도 좌우에 이웃이
  // 보이므로, 카드 배지·점 번호와 어긋나지 않는 0 으로 연다.
  const controller = useCarouselController(LANDING_MODELS.length, 0);

  return (
    <section aria-label="예시 이미지 갤러리" className={s.gallery} id="gallery">
      {/* 고지는 스테이지보다 **위**다. 아래에 두면 모바일에서 스테이지 높이가
          clamp(24rem, 78vh…)이라 사진 14장만 화면에 들어오고 고지는 뷰포트 밖으로 밀린다.
          여백도 그 배치에 맞춰 .galleryNotice 안에서 아래로 잡혀 있다(margin: 0 0 0.9rem). */}
      <p className={s.galleryNotice}>
        <Icon name="info" size={14} stroke={2} />
        아래 이미지는 전부 가상 모델 예시입니다. 실제 등록된 모델이 아닙니다.
      </p>

      <CarouselStage controller={controller} items={LANDING_MODELS} />

      <div className={s.galleryBar}>
        <button
          aria-label="이전 이미지"
          className={s.galleryArrow}
          onClick={() => controller.goBy(-1)}
          type="button"
        >
          <Icon name="chevLeft" size={20} stroke={2} />
        </button>

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

        <button
          aria-label="다음 이미지"
          className={s.galleryArrow}
          onClick={() => controller.goBy(1)}
          type="button"
        >
          <Icon name="chevRight" size={20} stroke={2} />
        </button>
      </div>
    </section>
  );
}
