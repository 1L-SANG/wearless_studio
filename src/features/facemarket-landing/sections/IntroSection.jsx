import { Icon } from '@/components/ui.jsx';
import { pricingLine } from '../../../lib/facemarketPricing.js';
import s from '../FacemarketLanding.module.css';

export function IntroSection({ onPrimary, primaryLabel }) {
  return <section aria-label="소개" className={s.intro}>
    <p className={s.introLead}>얼굴을 등록하고, 어떤 품목에 얼마 동안 쓸 수 있는지 직접 정해요. 정한 조건은 누구나 확인할 수 있는 라이선스로 남아요.</p>
    <p className={s.sectionLead}>이용 가격은 표준가로 같아요. {pricingLine()}이고, 이 금액의 70%가 모델의 몫이에요.</p>
    <button className={s.heroCta} onClick={onPrimary} type="button">{primaryLabel}<Icon name="arrowRight" size={18} stroke={2} /></button>
  </section>;
}
