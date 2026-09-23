import { Link } from 'react-router-dom';
import { FACEMARKET_PRICING } from '@/lib/facemarketPricing.js';
import {
  LICENSE_ISSUE_FEE_KRW,
  MIN_PAYOUT_KRW,
  MODEL_SHARE,
  SETTLEMENT_DAY,
  formatKrw,
} from '../facemarketTerms.js';
import s from '../FacemarketLanding.module.css';

const sharePercent = MODEL_SHARE * 100;
const perCutShare = FACEMARKET_PRICING.perCut * MODEL_SHARE;

export function FaqSection() {
  return (
    <section aria-labelledby="fm-faq-title" className={s.faqSection} id="faq">
      <div className={s.faqHead}>
        <h2 id="fm-faq-title">자주 하는 질문들</h2>
        <p>
          계약 조건은{' '}
          <Link to="/license-agreement">초상 라이선스 계약서</Link>와{' '}
          <Link to="/biometric-consent">동의서</Link> 원문에서 그대로 읽을 수 있어요.
        </p>
      </div>

      <div className={s.faqGrid}>
        <article className={s.faqEntry}>
          <h3>제가 돈 쓰는 부분은 없나요?</h3>
          <p className={s.faqAnswerText}><strong>없어요.</strong> 지원과 등록은 무료이고, 증서 발급료 {formatKrw(LICENSE_ISSUE_FEE_KRW)}도 지금은 무료예요. 화면에 보이는 {formatKrw(FACEMARKET_PRICING.perCut)}과 {formatKrw(FACEMARKET_PRICING.monthly)}은 셀러가 내는 금액이에요.</p>
        </article>
        <article className={s.faqEntry}>
          <h3>얼마를, 언제 받나요?</h3>
          <p className={s.faqAnswerText}>셀러가 낸 금액의 <strong>{sharePercent}%</strong>예요. 한 건이면 {formatKrw(perCutShare)}이고, 쌓인 몫이 {formatKrw(MIN_PAYOUT_KRW)}을 넘으면 매월 {SETTLEMENT_DAY}일에 등록한 계좌로 보내드려요.</p>
        </article>
        <article className={s.faqEntry}>
          <h3>모델 경력이 없어도 되나요?</h3>
          <p className={s.faqAnswerText}>네. 경력, 포트폴리오, SNS 는 선택이에요. 만 19세 이상이고, 소속 에이전시가 없고, 본인이 직접 지원하면 돼요.</p>
        </article>
        <article className={s.faqEntry}>
          <h3>내 얼굴은 어디에 쓰이나요?</h3>
          <p className={s.faqAnswerText}>스튜디오 배경의 의류 착용컷과 그 상품의 상세페이지에만요. 광고, 인쇄물, 영상, 다른 사람과의 합성에는 쓰이지 않아요.</p>
        </article>
        <article className={s.faqEntry}>
          <h3>등록할 때 무엇이 필요한가요?</h3>
          <p className={s.faqAnswerText}>본인확인과 등록 사진 <strong>18장</strong>이 필요해요. 밝은 야외에서 도와줄 사람 1명과 함께, 그늘 9장과 햇빛에서 방향을 바꾸며 3장씩 찍어요. 촬영은 약 15분이에요. <Link to="/photo-guide">촬영 가이드 보기</Link></p>
        </article>
        <article className={s.faqEntry}>
          <h3>조건은 나중에 바꿀 수 있나요?</h3>
          <p className={s.faqAnswerText}>마이페이지에서 언제든요. 바뀐 조건은 그다음 사용 건부터 적용되고, 이미 만들어진 건은 그대로예요.</p>
        </article>
        <article className={s.faqEntry}>
          <h3>협찬을 꼭 해야 하나요?</h3>
          <p className={s.faqAnswerText}>아니요. 등록할 때 켠 사람만 요청을 받아요. 언제든 끌 수 있고, 옷을 받는 협찬이에요. 협찬 요청 기능은 준비 중이에요. 지금은 참여 설정만 저장해요.</p>
        </article>
        <article className={s.faqEntry}>
          <h3>팔로워가 적어도 되나요?</h3>
          <p className={s.faqAnswerText}>수 제한 없이 참여할 수 있어요. 셀러가 계정과 스타일을 보고 제안해요.</p>
        </article>
      </div>
    </section>
  );
}
