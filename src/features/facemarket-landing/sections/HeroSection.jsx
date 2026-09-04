/* 첫 화면 위쪽 — 원본 spotlight 의 Hero 에서 두 가지가 바뀌었다(2026-09-03 오너 지시).
   (1) kicker(FACEMARKET 라틴 라벨) 제거 — 상단바 워드마크와 중복이라 내렸다.
   (2) 제목 아래 CTA 재도입. 예전에 '문단+버튼을 끼우면 캐러셀이 밀려 메타 바가 잘린다'고
   내렸던 자리지만, 이번엔 문단 없이 버튼 하나이고 kicker 가 빠져 세로 예산이 상쇄된다 —
   부족분은 스테이지 1fr 의 하한+스크롤이 흡수한다(스크린샷 실측으로 확인).
   리드문은 계속 IntroSection 이다.

   제목은 한글이라 Noto Serif KR(facemarket.html 로드)로 그린다 — .heroTitle 주석 참고. */
import { Icon } from '@/components/ui.jsx';
import { APPLY_LABEL } from '../registerCta.js';
import s from '../FacemarketLanding.module.css';

export function HeroSection({ onPrimary, primaryLabel }) {
  const isApply = primaryLabel === APPLY_LABEL;
  return (
    <section aria-labelledby="fm-hero-title" className={s.hero}>
      {/* 제목 뒤에 깔리는 브랜드 오브 = 셀러 앱 배경과 **같은 물건**이다.
          app.css 의 .orb-bg 4겹(l1/l2/l3/hi)을 그대로 쓰고, 여기서는 위치와 크기
          (--orb-shift · --orb-scale)만 갈아끼운다 — 색·블러·회전은 전역 정의가 유일한
          출처이고, tokens.css 의 --glow-* 를 바꾸면 두 도메인이 같이 바뀐다.

          왜 PNG(/assets/brand/orb.png)를 버렸나: 그 파일은 오브를 한 번 굽어 놓은
          래스터라 (1) 전체에 균일한 30% 알파가 깔려 사각형이 비쳤고, (2) 알파를 못 고쳐
          saturate(2.8) 로 억지 보정하다 색이 청록으로 틀어졌으며, (3) 구울 때의 얼룩이
          그대로 남아 빛이 아니라 자국으로 읽혔다. 라이브 오브는 알파도 색도 살아 있다.

          장식이라 접근성 트리에서 뺀다(aria-hidden). 자식 div 에 클래스명이 문자열인
          이유는 전역 클래스(:global)이기 때문이다 — CSS 모듈이 해시를 붙이면 안 된다. */}
      <div aria-hidden="true" className={s.heroOrb}>
        <div className="orb-bg">
          <div className="l1" />
          <div className="l2" />
          <div className="l3" />
          <div className="hi" />
        </div>
      </div>
      {/* 2026-09-03 카피 확정: 오너 원안("안전하게 얼굴 등록하고 / 수익 자동화를
          진행해보세요")에서 동사만 다듬은 것('진행해보세요' → '받아보세요').
          앞선 두 안(직역 "내 얼굴, 내 조건" / 위트 "얼굴이 출근합니다")은 오너 기각 —
          장식 없이 혜택을 그대로 말하는 톤으로 확정. 세리프·이탤릭 강조도 같은 지시로
          내렸다(.heroTitle 주석). <br> 로 줄을 못박는 이유: 폭에 따라 줄 수가 흔들리면
          첫 화면의 세로 예산(뷰포트 = 상단바 + 히어로 + 스테이지 + 메타 바)이 흔들린다. */}
      {/* 2026-09-03 오너 원안: "나를 온라인 모델로 구현해보세요 / 쇼핑몰에서 사용될때마다
          수익이 들어와요 / (C2PA, 블록체인으로 안전한 이미지 사용 보장)". 두 곳만 다듬었다:
          '구현' → '만들어' (개발 어휘라 모델에게 딱딱), 괄호 문장은 '보장'(법적 약속 —
          스크린샷 유출까지는 못 막는다는 내부 결론과 충돌)을 사실 진술('위조할 수 없게
          기록')로 바꾸고 기술명은 뒤에 붙였다. 제목은 한 줄 — <br> 없이도 폭 안에 든다. */}
      {/* 신뢰 pill 3개는 제목 위에 잠깐 있다가(2026-09-03 낮) 같은 날 저녁 오너 지시로
          캐러셀 아래 화살표 자리(GallerySection .trustPills)로 내려갔다 — 히어로는 다시
          제목 + 리드 + 버튼뿐이다. */}
      {/* 2026-09-03 밤 오너 확정 제목 — 영문 한 줄, 소문자 그대로("create your own digital DNA").
          앞선 한글 두 줄("내 얼굴로 만든 온라인 모델, / 내가 정한 조건에서만.")과 토스 원칙 카피
          6안·공감 카피·'나만의' 카피는 전부 기각. digital DNA = 내 얼굴로 만든 온라인 모델이고,
          그 뜻풀이는 아래 리드문이 맡는다(제목만으로는 서비스가 안 보인다). */}
      {/* 2026-09-03 밤 오너 확정 서체 = 16종 비교의 10번(Playfair Display 600 + 'digital DNA' 만
          이탤릭 500), 두 줄로 못박음(<br>). 실화면 A/B(10 vs 11 Instrument Serif)는 끝났고
          빌드 스위치(VITE_FM_TITLE_STYLE)는 지웠다. 비교 시안은
          mockups/facemarket_title_styles_20260903.html. */}
      <h1 className={s.heroTitle} id="fm-hero-title" lang="en">
        create your own
        <br />
        <em>digital DNA</em>
      </h1>
      {/* 2026-09-03 히어로 축소(A안): 요소 6 → 4. 부제는 수익 한 줄만. 안전 기술 문장
          (C2PA·블록체인)은 GallerySection 의 신뢰 스트립으로 내려갔다 — 첫 3초에 읽을 정보가
          아니고, 히어로는 메시지 하나(제목)+행동 하나(버튼)만 맡는다. */}
      {/* 리드문 = 영문 제목의 뜻풀이 + 돈이 들어오는 구조 + 통제권, 두 문장. 2026-09-03 밤 추천안
          (오너 선택 대기 — 후보는 대화 기록). '쇼핑몰 대표가'·'수익' 같은 보도자료 어휘는 뺐다. */}
      {/* 리드문 한 줄(2026-09-03 밤 오너 요청 "한 줄로"). '온라인 모델' 같은 물건 이름은 안 부르고
          일어나는 일로만: 얼굴이 일한다 + 쓰일 때마다 입금. 통제권 문장("어디까지 쓸지는 내가
          정할 수 있어요")은 아래 신뢰 pill(정해진 범위 외 사용 불가·철회 가능)이 맡는다.
          지금 문장은 추천안(오너 선택 대기 — 후보는 대화 기록). */}
      <p className={s.heroLead}>내 얼굴이 쇼핑몰에서 일하고, 쓰일 때마다 입금돼요.</p>
      {/* 헤더 CTA 와 같은 동작(onPrimary — 인증 부트스트랩 중 클릭 보류 로직 포함). 랜딩
          밖(등록 위저드 등)에서 이 섹션을 재사용하면 props 가 없을 수 있어 가드한다.
          얼리버드 혜택은 별도 칩 대신 **버튼 둘레 링 + 버튼 옆 손그림 메모** — 혜택이 행동 곁에
          붙어야 읽는 순서(제목→수익→버튼→이유)가 한 줄로 선다. 둘 다 **신규 지원 상태
          (라벨 = APPLY_LABEL)일 때만** — 이미 등록했거나 심사 중인 사람에게 "지금 지원하면
          발급료 무료"는 틀린 말이다. 라벨을 상태 키로 쓰는 이유: 상태 판정은 LandingShell 이
          registerCta 로 끝냈고 여기서 다시 계산하면 두 벌이 된다. */}
      {/* 버튼 + 그 아래 혜택 캡션, 세로 한 줄(.heroCtaRow). 손그림 화살표 메모 안은 오너가
          하루 써보고 되돌렸다(2026-09-03). */}
      {/* 얼리버드 표현(2026-09-03 오너 선택 10안, 비교 mockups/facemarket_cta_badge_20260903.html):
          버튼 안 'EARLY BIRD' 배지 대신 **버튼 둘레 2px 오브색 링**. 배지는 버튼 안이 두 덩이라
          어수선했고 영문 대문자가 한글 카피와 결이 달랐다. 링은 신규 지원 상태에서만 —
          래퍼 span 을 쓰는 이유는 .heroCta 가 빛 스윕 때문에 overflow:hidden 이라 ::before
          링이 잘리기 때문. */}
      {onPrimary ? (
        <div className={s.heroCtaRow}>
          <span className={isApply ? s.heroCtaRing : undefined}>
            <button className={s.heroCta} onClick={onPrimary} type="button">
              {primaryLabel}
              <Icon name="arrowRight" size={18} stroke={2} />
            </button>
          </span>
          {/* 혜택 캡션은 버튼 **아래** 한 줄(2026-09-03 밤 오너 결정 — 오른쪽 위 손그림 메모는
              한나절 써보고 기각). 표현은 4안 비교의 D(mockups/facemarket_benefit_caption_20260903.html):
              회색 캡션은 약관 문법이라 혜택에 안 맞는다는 오너 지적 → 잉크색 문장 + 정가 취소선 +
              '무료'는 살짝 기울인 액센트 도장. 쇼핑몰 가격표 문법이라 이 사람들에게 익숙하다. */}
          {isApply ? (
            <p className={s.heroCaption}>
              지금 지원하면 발급료 <s>20,000원</s> <b className={s.heroCaptionFree}>무료</b>
            </p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
