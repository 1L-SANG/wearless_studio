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
          <p>셀러가 보내준 옷을 직접 입고 찍어서, 내 인스타그램 피드에 올려요. 참여 여부는 등록할 때 정하고 언제든 끌 수 있어요.</p>
          <div className={s.example} aria-label="의류 협찬 진행 예시">
            <div className={s.exampleTopline}><span>협찬 진행 예시</span><span>실제 모집 상품 아님</span></div>
            <div className={s.exampleMain}>
              <div className={s.exampleMedia}>
                <img
                  alt="모델이 입지 않은 아이보리 니트의 협찬 예시 상품 이미지"
                  className={s.exampleImage}
                  loading="lazy"
                  src="/assets/sponsorship-knit-ivory-ghost.webp"
                />
              </div>
              <div className={s.exampleDetails}>
                <span className={s.exampleBadge}>의류 협찬</span>
                <strong className={s.exampleProduct}>아이보리 니트 1벌</strong>
                <div className={s.examplePrice} aria-label="예시 상품 가격 49,900원, 협찬으로 받으면 무료">
                  <del>49,900원</del>
                  <strong>무료</strong>
                </div>
                <dl className={s.exampleTerms}>
                  <div><dt>올릴 콘텐츠</dt><dd>착용 사진 인스타그램 피드 1회</dd></div>
                  <div><dt>게시 기한</dt><dd>수령 후 3일 이내</dd></div>
                  <div><dt>게시 유지</dt><dd>30일</dd></div>
                </dl>
              </div>
            </div>
            <p className={s.exampleFootnote}>내가 허용한 옷 종류 안에서만 협찬받아요. 게시물의 광고 재사용은 별도 동의 후 진행돼요.</p>
          </div>
          <p className={s.launchNote}>협찬 요청 기능은 준비 중이에요. 지금은 참여 설정만 저장해요.</p>
        </article>
      </div>
    </section>
  );
}
