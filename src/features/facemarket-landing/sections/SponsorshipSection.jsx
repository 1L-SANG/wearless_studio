import { FACEMARKET_PRICING } from '@/lib/facemarketPricing.js';
import { MODEL_SHARE, formatKrw } from '../facemarketTerms.js';
import s from './SponsorshipSection.module.css';

export function SponsorshipSection() {
  return (
    <section aria-labelledby="fm-sponsorship-title" className={s.section} id="sponsorship">
      <h2 className={s.title} id="fm-sponsorship-title">두 가지 일이 진행돼요</h2>
      <div className={s.cards}>
        <article className={s.card}>
          <h3>AI 의류 상세페이지 <span className={s.scope}>스튜디오컷 한정</span></h3>
          <p>본인확인과 사진 18장으로 한 번 등록하면, 셀러가 내 얼굴로 스튜디오컷 세트(3~5장)를 만들 때마다 <strong>{formatKrw(FACEMARKET_PRICING.perCut)}</strong>의 매출이 발생해요. 모델료는 <strong>{MODEL_SHARE * 100}%</strong> 정산.</p>
          <ul className={s.benefits}>
            <li>촬영장에 안 가요</li>
            <li>어떤 옷에 쓸지 내가 정해요</li>
          </ul>
        </article>
        <article className={s.card}>
          <h3>의류 협찬 <span className={s.optional}>(선택)</span></h3>
          <p>셀러가 보내준 옷을 입고 사진을 찍어서 피드에 올리면 돼요. 옷은 내 것. 등록할 때 켜 둔 사람만 요청을 받고, 언제든 끌 수 있어요.</p>
          <div className={s.example} aria-label="협찬 요청 예시">
            <div className={s.exampleHead}><span>요청 예시 · ○○몰</span><strong>니트 1벌</strong></div>
            <ul className={s.conditions}>
              <li><span>옷 받은 뒤 3일 이내 피드 1회</span><span>30일 유지</span></li>
              <li><span>내 허용 품목 안에서만</span><span>현금 없음</span></li>
            </ul>
            <p className={s.sequence}>요청이 오면, 옷을 받은 뒤 게시해요.</p>
          </div>
          <p className={s.launchNote}>협찬 요청 기능은 준비 중이에요. 지금은 참여 설정만 저장해요.</p>
        </article>
      </div>
    </section>
  );
}
