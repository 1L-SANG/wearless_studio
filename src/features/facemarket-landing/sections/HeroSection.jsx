/* 히어로 — 모델 관점 한 문장. 셀러·브랜드 관점 문구는 여기 들어오지 않는다.
   대형 세리프는 Cormorant(라틴 전용)라 영문이고, 한글 리드문은 Pretendard 다. */
import { Icon } from '@/components/ui.jsx';
import s from '../FacemarketLanding.module.css';

export function HeroSection({ onPrimary, primaryLabel }) {
  return (
    <section className={s.hero}>
      <p className={s.eyebrow}>FACEMARKET</p>
      <h1 className={s.heroTitle}>Your face, your terms.</h1>
      {/* 리드는 "절차 한 줄 + 편익 한 줄"이다. 아래 두 문장은 라운드마다 반대로
          왕복한 자리라 근거를 박아 둔다.

          (1) '어떤 품목에' 로 되돌리지 마라 — '어떤 옷에' 로 좁히면 실제보다 좁게
              말하는 게 된다. 허용 품목 11개(brandUseCategories.js ALLOWED)에
              '잡화·액세서리'와 '뷰티·화장품'이 들어 있고 서버도 같은 튜플로 강제한다
              (facemarket.py `_clean_uses`). 같은 페이지의 description·라이선싱 카드도
              이미 '품목'이다.
          (2) 두 번째 문장(편익)을 지우지 마라 — 3라운드 최종 판정이 "편익 문장이 한 줄도
              없어 페이지의 목적이 사라졌다"였다. 다만 편익은 코드가 지키는 것만 적는다.
              허용 품목·건당 단가·유효기간은 전부 모델이 발급 폼에서 직접 넣는 값이고
              (ModelLicense.jsx TermsStep 의 allowed/unitPrice/validDays), 공개 검증은
              인증 없이 열린다(GET /v1/facemarket/verify/{id}).
          (3) 여기에 '어디에 쓰였는지 기록이 남는다'는 쓰지 마라. 온체인 정산은
              best-effort 라 체인 미설정·게이트웨이 장애면 행이 아예 안 생긴다
              (LicensingSection.jsx '체인 기록' 칸 주석 참고). 히어로는 예외를 달 자리가
              없어서, 무조건 참인 것만 올린다. */}
      <p className={s.heroLead}>
        얼굴을 등록하고, 어떤 품목에 건당 얼마로 얼마 동안 쓸 수 있는지 직접 정합니다.
        조건을 내미는 쪽이 브랜드가 아니라 본인이고, 정한 조건은 누구나 확인할 수 있는
        라이선스로 남습니다.
      </p>
      <button className={s.heroCta} onClick={onPrimary} type="button">
        {primaryLabel}
        <Icon name="arrowRight" size={18} stroke={2} />
      </button>
    </section>
  );
}
