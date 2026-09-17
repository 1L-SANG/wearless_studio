/* 지원 절차는 /apply 와 서버에 구현돼 있다. */
import { Link } from 'react-router-dom';
import { ArrowRight } from 'lucide-react';
import { APPLY_TIME_MINUTES, REGISTRATION_TIME_MINUTES } from '../data/landingTiming.js';
import { REVIEW_SLA_LABEL } from '../facemarketTerms.js';
import { SHOOTING_TIME_MINUTES } from '../../model/registerSlots.js';
import s from '../FacemarketLanding.module.css';

export function HowItWorksSection() {
  return (
    <section aria-labelledby="fm-how-title" className={s.howSection} id="how">
      <p className={s.howEyebrow}>지원부터 공개까지</p>
      <h2 className={s.howTitle} id="fm-how-title">사진 18장으로 내 모델을 만들어요</h2>
      <ol className={s.stepGrid}>
        <li className={s.stepItem}>
          <span className={s.stepNumber}>01</span>
          <h3>지원</h3>
          <span className={s.stepTime}>지원서 약 {APPLY_TIME_MINUTES}분</span>
          <p>기본 정보와 심사용 프로필을 제출해요. 모델 등록에는 별도로 사진 18장이 필요해요.</p>
        </li>
        <li className={s.stepItem}>
          <span className={s.stepNumber}>02</span>
          <h3>검토</h3>
          <span className={s.stepTime}>{REVIEW_SLA_LABEL}</span>
          <p>지원 결과를 이메일로 알려드려요. 승인되면 본인확인과 사진 등록을 시작해요.</p>
        </li>
        <li className={s.stepItem}>
          <span className={s.stepNumber}>03</span>
          <h3>등록</h3>
          <span className={s.stepTime}>촬영 약 {SHOOTING_TIME_MINUTES}분</span>
          <p>본인확인 후 사진 18장을 올리고, 사용 조건을 정해 증서를 발급받아요. 촬영 외 절차는 약 {REGISTRATION_TIME_MINUTES}분이며, 간편인증은 담당자 확인 시간이 더해져요.</p>
          <Link className={s.stepGuideLink} to="/photo-guide">촬영 가이드 보기 <ArrowRight size={16} aria-hidden="true" /></Link>
        </li>
      </ol>
    </section>
  );
}
