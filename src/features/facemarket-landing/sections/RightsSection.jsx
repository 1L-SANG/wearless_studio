import { FACEMARKET_PRICING } from '@/lib/facemarketPricing.js';
import { FACE_WITHDRAWAL_PURGE_DAYS } from '../data/landingTiming.js';
import { MIN_PAYOUT_KRW, MODEL_SHARE, SETTLEMENT_DAY, formatKrw } from '../facemarketTerms.js';
import { DEMO_CUTS } from '../data/demoCuts.js';
import s from '../FacemarketLanding.module.css';

const sharePercent = MODEL_SHARE * 100;
const perCutShare = FACEMARKET_PRICING.perCut * MODEL_SHARE;

export function RightsSection() {
  return (
    <section aria-label="초상 원칙" className={s.rightsSection}>
      <div className={s.rightsGrid}>
      <ol className={s.rightsList}>
        <li>
          <h3>내 얼굴을 <strong>어떤 옷에 쓸지</strong>, 내가 정해요.</h3>
          <p>일반 의류는 기본으로 허용되고, 속옷이나 수영복 같은 품목은 내가 켜기 전엔 절대 쓰이지 않아요. 정한 범위 밖의 요청은 시스템이 막아요.</p>
        </li>
        <li>
          <h3>쓰일 때마다 결제 금액의 <strong>{sharePercent}%</strong>가 내 몫이에요.</h3>
          <p>셀러가 {formatKrw(FACEMARKET_PRICING.perCut)}을 내면 {formatKrw(perCutShare)}이 내 몫으로 쌓여요. {formatKrw(MIN_PAYOUT_KRW)}이 넘으면 매월 {SETTLEMENT_DAY}일에 보내드려요.</p>
        </li>
        <li>
          <h3>어디에 쓰였는지 <strong>전부</strong> 볼 수 있어요.</h3>
          <p>어느 셀러가 언제 어떤 상품에 썼는지, 완성된 착용컷과 라이선스 번호까지 마이페이지에서 확인해요.</p>
        </li>
        <li>
          <h3>착용컷마다 <strong>위조할 수 없는 기록</strong>이 남아요.</h3>
          <p>만들어진 이미지에는 출처 서명(C2PA)이 들어가고, 정산 내역은 블록체인에 기록돼요. 누구나 라이선스 번호로 진짜인지 확인할 수 있어요.</p>
        </li>
        <li>
          <h3>언제든 <strong>철회</strong>할 수 있어요.</h3>
          <p>이유 없이, 위약금 없이요. 철회하면 새 사용이 바로 멈추고, 얼굴 정보는 {FACE_WITHDRAWAL_PURGE_DAYS}일 안에 지워져요.</p>
        </li>
      </ol>
      {/* 실제 모델 착용컷 2장(오너 제공, 2026-09-14). 원칙 옆에 붙여 두면 "이 조건 아래에서
          이런 컷이 나온다" 로 읽힌다. 설명 문구는 오너 지시로 두지 않는다. */}
      <figure className={s.rightsCuts}>
        <div className={s.rightsCutRow}>
          {DEMO_CUTS.map((src, index) => (
            <div className={s.rightsCut} key={src}>
              <img loading="lazy" src={src} alt={`FaceMarket이 만든 착용컷 ${index + 1}`} />
            </div>
          ))}
        </div>
        <figcaption className={s.rightsCutsNote}>실제 모델의 얼굴로 FaceMarket이 만든 착용컷이에요.</figcaption>
      </figure>
      </div>
    </section>
  );
}
