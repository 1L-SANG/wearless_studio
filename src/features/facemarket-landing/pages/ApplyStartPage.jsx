import { Link } from 'react-router-dom';
import { Icon } from '@/components/ui.jsx';
import { FACEMARKET_PRICING } from '@/lib/facemarketPricing.js';
import { LandingShell } from '../LandingShell.jsx';
import { MODEL_SHARE, MIN_PAYOUT_KRW, SETTLEMENT_DAY, formatKrw } from '../facemarketTerms.js';
import { APPLY_START_FAQ } from '../applyStartFaq.js';
import s from './ApplyStartPage.module.css';

const FACES = ['/models/women/w1.webp', '/models/men/m1.webp', '/models/women/w2.webp', '/models/men/m3.webp'];
const QUALIFICATIONS = ['만 19세 이상이어야 해요', '소속 에이전시가 없어야 해요', '본인이 직접 지원해야 하며, 이미지 권리를 갖고 있어야 해요'];
const sharePercent = MODEL_SHARE * 100;
const perCutShare = FACEMARKET_PRICING.perCut * MODEL_SHARE;

export function ApplyStartPage() {
  return (
    <LandingShell title="모델 지원 | FaceMarket" description="사진 한 장이면 시작해요. 경력 없이도 지원해요." variant="apply">
      {() => (
        <div className={s.wrap}>
          <div className={s.dashboard}>
            <section className={s.intro} aria-labelledby="apply-title">
              <p className={s.eyebrow}>FaceMarket에서 모델로 시작해요</p>
              <h1 id="apply-title">모델 지원</h1>
              <p className={s.lead}>
                내가 등록해 둔 얼굴을 이용해 셀러가 AI로 의류컷을 만들 수 있어요<br />
                셀러 기준 1번 이용 시 <b>{formatKrw(FACEMARKET_PRICING.perCut)}</b>, 1개월 이용 시 <b>{formatKrw(FACEMARKET_PRICING.monthly)}</b> ({FACEMARKET_PRICING.monthlyCap}회 제한)
              </p>
              <Link className={s.primary} to="/model/apply">지원서 쓰기</Link>
              <div className={s.faces} aria-label="가상 모델 프로필 사진">
                {FACES.map((src, index) => <img key={src} src={src} alt={`가상 모델 프로필 예시 ${index + 1}`} decoding="async" />)}
              </div>
              <p className={s.caption}>가상 모델 사진이에요</p>
              <div className={s.footnote}><span>사진 한 장이면 시작해요</span><span>경력 없이도 지원해요</span></div>
            </section>
            <div className={s.side}>
              <section className={s.benefits} aria-labelledby="apply-benefits">
                <h2 id="apply-benefits">지금 시작하면</h2>
                <ol>
                  <li><span>01</span><p>증서 발급료, <b className={s.emphasis}>무료</b>예요</p></li>
                  <li><span>02</span><p><b>3분</b>이면 제출 가능해요</p></li>
                  <li><span>03</span><p>24시간 안에 승인 여부를 알려드려요</p></li>
                </ol>
              </section>
              <section className={s.earnings} aria-labelledby="apply-earnings">
                <h2 id="apply-earnings">등록하면 이런 게 달라져요</h2>
                <ul>
                  <li>
                    10명의 셀러가 1번씩만 사용해도 약 <b className={s.emphasis}>{formatKrw(Math.round(perCutShare * 10 / 10_000) * 10_000)}</b>이 자동입금돼요.<br />
                    셀러가 결제한 금액의 {sharePercent}%를 정산해드려요{' '}
                    <span className={s.info} tabIndex={0} aria-label="정산 금액 안내" aria-describedby="apply-settlement-tooltip">
                      <Icon name="info" size={16} />
                      <span className={s.tooltip} id="apply-settlement-tooltip" role="tooltip">1건 기준 {formatKrw(FACEMARKET_PRICING.perCut)}의 {sharePercent}%, {formatKrw(perCutShare)}</span>
                    </span>
                  </li>
                  <li>허용, 금지 카테고리 설정이 가능해요</li>
                  <li>내 얼굴이 어디 쓰였는지 전부 추적이 가능해요</li>
                </ul>
                <p className={s.fine}>쌓인 몫이 {formatKrw(MIN_PAYOUT_KRW)}을 넘으면 매월 {SETTLEMENT_DAY}일에 보내드려요</p>
              </section>
              <section className={s.qualifications} aria-labelledby="apply-qualifications">
                <h2 id="apply-qualifications">지원 자격</h2>
                <ul>{QUALIFICATIONS.map((text) => <li key={text}><Icon name="check" size={16} /><span>{text}</span></li>)}</ul>
              </section>
            </div>
          </div>
          <section className={s.faq} aria-labelledby="apply-faq">
            <h2 id="apply-faq">자주 묻는 질문</h2>
            <div className={s.faqCards}>
              {APPLY_START_FAQ.map(({ q, a }) => (
                <details key={q} className={s.faqCard}>
                  <summary>{q}</summary>
                  <p>{a}</p>
                </details>
              ))}
            </div>
          </section>
        </div>
      )}
    </LandingShell>
  );
}
