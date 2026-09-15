/* 지원 절차는 /apply 와 서버에 구현돼 있다. */
import { APPLY_TIME_MINUTES, REGISTRATION_TIME_MINUTES } from '../data/landingTiming.js';
import { REVIEW_SLA_LABEL } from '../facemarketTerms.js';
import s from '../FacemarketLanding.module.css';

export function HowItWorksSection() {
  return (
    <section aria-labelledby="fm-how-title" className={s.howSection} id="how">
      <p className={s.howEyebrow}>지원부터 등록까지</p>
      <h2 className={s.howTitle} id="fm-how-title">셀카 한 장으로 시작해요</h2>
      <ol className={s.stepGrid}>
        <li className={s.stepItem}>
          <span className={s.stepNumber}>01</span>
          <h3>지원</h3>
          <span className={s.stepTime}>{APPLY_TIME_MINUTES}분</span>
          <p>이름, 생년월일, 연락처와 셀카 한 장이면 돼요. 경력이나 포트폴리오는 없어도 괜찮아요.</p>
        </li>
        <li className={s.stepItem}>
          <span className={s.stepNumber}>02</span>
          <h3>검토</h3>
          <span className={s.stepTime}>{REVIEW_SLA_LABEL}</span>
          <p>사람이 직접 보고 이메일로 결과를 알려드려요. 마이페이지에서도 지금 상태를 볼 수 있어요.</p>
        </li>
        <li className={s.stepItem}>
          <span className={s.stepNumber}>03</span>
          <h3>등록</h3>
          <span className={s.stepTime}>{REGISTRATION_TIME_MINUTES}분</span>
          <p>모바일 신분증으로 본인확인을 하고, 정면과 45도, 측면 사진을 올리고, 사용 조건을 정하면 라이선스 증서가 발급돼요.</p>
        </li>
      </ol>
      <p className={s.stepFoot}><strong>승인은 그대로 남아요.</strong> 등록을 하다 멈춰도 지원서를 다시 낼 필요 없이 이어서 하면 돼요.</p>
    </section>
  );
}
