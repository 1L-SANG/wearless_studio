import { FOUNDING_FILLED, FOUNDING_TOTAL } from '../data/foundingModels.js';
import s from '../FacemarketLanding.module.css';

const remainingCount = FOUNDING_TOTAL - FOUNDING_FILLED.length;

export function FoundingSection({ onPrimary }) {
  return (
    <section aria-labelledby="fm-founding-title" className={s.foundingSection}>
      <div className={s.foundingHead}>
        <div>
          <h2 className={s.foundingTitle} id="fm-founding-title">
            첫 <strong>파운딩 모델 {FOUNDING_TOTAL}명</strong>을 모집하고 있어요
          </h2>
          <p className={s.foundingSub}>지원 이후, 등록까지 완료된 모델 기준입니다.</p>
        </div>
        <p className={s.foundingRemaining}>
          <strong>{remainingCount}자리</strong>
          남았어요
        </p>
      </div>

      <div className={s.foundingSlots}>
        {FOUNDING_FILLED.map((src, index) => (
          <div className={`${s.foundingSlot} ${s.foundingSlotFilled}`} key={src}>
            <img loading="lazy" src={src} alt={`등록 완료 모델 ${index + 1}`} />
            <span>등록 완료</span>
          </div>
        ))}
        {Array.from({ length: remainingCount }, (_, index) => {
          const slotNumber = FOUNDING_FILLED.length + index + 1;
          return index === 0 ? (
            <button
              className={`${s.foundingSlot} ${s.foundingSlotOpen}`}
              key={slotNumber}
              onClick={onPrimary}
              type="button"
            >
              지원하기
            </button>
          ) : (
            <div className={s.foundingSlot} key={slotNumber}>{slotNumber}</div>
          );
        })}
      </div>
    </section>
  );
}
