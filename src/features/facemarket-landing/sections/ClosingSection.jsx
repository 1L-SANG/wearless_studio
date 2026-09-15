import { APPLY_TIME_MINUTES } from '../data/landingTiming.js';
import s from '../FacemarketLanding.module.css';

export function ClosingSection({ onPrimary, primaryLabel }) {
  return (
    <section aria-labelledby="fm-closing-title" className={s.closingSection}>
      <h2 className={`${s.heroTitle} ${s.closingTitle}`} id="fm-closing-title" lang="en">
        create your own <em>online model</em>
      </h2>
      {onPrimary ? (
        <button className={`${s.heroCta} ${s.closingCta}`} onClick={onPrimary} type="button">
          {primaryLabel}
        </button>
      ) : null}
      <p className={s.closingCaption}>지원 {APPLY_TIME_MINUTES}분, 지금은 발급료 무료</p>
    </section>
  );
}
