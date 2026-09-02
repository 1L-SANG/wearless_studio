/* 첫 화면 위쪽 — 원본 spotlight 의 Hero(kicker + 두 줄 세리프 제목) 그대로다.
   여기엔 제목까지만 온다. 리드문과 CTA 는 IntroSection 으로 내려갔다 — 원본 첫 화면은
   상단바·kicker·제목·캐러셀·메타 바가 한 뷰포트에 딱 들어차는 구성이고, 그 사이에
   문단과 버튼을 끼우면 캐러셀이 밀려 내려가 메타 바가 화면 밖으로 잘린다(실제로 그랬다).

   대형 세리프는 Cormorant(라틴 전용)라 영문이고, 한글 리드문은 Pretendard 다. */
import s from '../FacemarketLanding.module.css';

export function HeroSection() {
  return (
    <section aria-labelledby="fm-hero-title" className={s.hero}>
      {/* 원본 .kicker — 양옆 헤어라인 사이의 라틴 대문자. 넓은 자간은 라틴 대문자에서만
          에디토리얼로 읽힌다(한글에 걸면 음절이 흩어진다 — .eyebrow 주석 참고). */}
      <p className={s.kicker}>
        <span aria-hidden="true" />
        FACEMARKET
        <span aria-hidden="true" />
      </p>
      {/* 원본 "Selected works, / framed in <em>light</em>" 의 리듬 — 두 줄, 마지막 단어만
          이탤릭. 문구는 그대로 'Your face, your terms.' 이고 줄바꿈·강조만 조판이다.
          <br> 로 줄을 못박는 이유: 폭에 따라 한 줄로 붙거나 세 줄로 흩어지면 첫 화면의
          세로 예산(뷰포트 = 상단바 + 히어로 + 스테이지 + 메타 바)이 흔들린다. */}
      <h1 className={s.heroTitle} id="fm-hero-title">
        Your face,
        <br />
        your <em>terms</em>.
      </h1>
    </section>
  );
}
