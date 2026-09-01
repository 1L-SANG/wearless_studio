/* =============================================================
   facemarket-landing/FacemarketLanding.jsx
   facemarket.wearless.kr 랜딩. 앱 크롬(ChromeLayout) 밖 독립 surface 다 —
   TopNav 의 크레딧 배지·플로우 스테퍼는 셀러 스튜디오 물건이고, 이 페이지의
   상단바는 섹션 앵커라 성격이 다르다.
   ============================================================= */
import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '@/features/auth/AuthProvider.jsx';
import { IS_FACEMARKET } from '@/lib/host.js';
import { getCurrentEnrollment, listMyModels } from '@/lib/api/facemarket.js';
import { LandingHeader } from './LandingHeader.jsx';
import { registerCta } from './registerCta.js';
import { HeroSection } from './sections/HeroSection.jsx';
import { GallerySection } from './sections/GallerySection.jsx';
import { LicensingSection } from './sections/LicensingSection.jsx';
import { RegisterSection } from './sections/RegisterSection.jsx';
import { ModelInfoSection } from './sections/ModelInfoSection.jsx';
import { FooterSection } from './sections/FooterSection.jsx';
import s from './FacemarketLanding.module.css';

/* index.html 의 head 는 셀러 스튜디오("Wearless — AI 상세페이지 스튜디오") 것이다.
   랜딩은 SPA 라우트라 정적 head 를 도메인별로 나눌 수 없어서 마운트 때 갈아 끼운다.
   언마운트 복원은 **호스트로 가른다** — 되돌릴지 말지는 아래 effect 가 정한다(근거는 거기).
   주의: JS 를 실행하지 않는 크롤러의 공유 미리보기(og:*)는 이걸로 안 바뀐다 —
   그건 정적 head 를 호스트별로 나눠야 하는 별건이다. */
const LANDING_TITLE = 'FaceMarket — 내 얼굴을 라이선스로';
/* 이 설명은 LicensingSection 과 같은 눈금이어야 한다. "쓰인 만큼 정산받는다"·"어디에
   쓰였는지 확인한다"는 지급(payout) 코드도 모델용 사용 내역 화면도 없는 지금 지키지
   못하는 약속이라(LicensingSection.jsx 헤더 주석 참고) 쓰지 않는다. 여기 남은 세 가지
   —조건 선택·QR 공개 검증·해지—는 전부 실동작하는 것만 골랐다.

   '해지'라고 쓴다. 제품 화면이 그 단어를 쓰고(ModelLicense.jsx·PublicVerify.jsx),
   랜딩의 다른 문자열도 전부 '해지'로 맞춰져 있다 — '폐기'로 되돌리지 마라. 목적어도
   생략하지 마라: '언제든 해지'만 쓰면 등록 자체를 지울 수 있다고 읽히는데, 같은 페이지
   ModelInfoSection 이 '등록 자체를 지우는 화면은 아직 없습니다'라고 정반대를 말한다.
   이 문자열은 검색 스니펫으로 나가므로 본문보다 먼저 읽힌다. */
const LANDING_DESCRIPTION =
  '얼굴을 등록하고, 어떤 품목에 얼마 동안 쓸 수 있는지 직접 정해 라이선스로 발급합니다. '
  + '조건은 QR 로 누구나 확인할 수 있고, 발급한 라이선스는 언제든 해지할 수 있어요.';

/* 원래 값으로 되돌리는 함수를 돌려준다 — 없던 태그는 지우고, 있던 값은 복원한다. */
function applyLandingHead() {
  const previousTitle = document.title;
  document.title = LANDING_TITLE;

  let tag = document.querySelector('meta[name="description"]');
  const created = !tag;
  if (created) {
    tag = document.createElement('meta');
    tag.setAttribute('name', 'description');
    document.head.appendChild(tag);
  }
  const previousDescription = created ? null : tag.getAttribute('content');
  tag.setAttribute('content', LANDING_DESCRIPTION);

  return () => {
    document.title = previousTitle;
    if (created) tag.remove();
    else if (previousDescription === null) tag.removeAttribute('content');
    else tag.setAttribute('content', previousDescription);
  };
}

export function FacemarketLanding() {
  const { session, loading, openLogin } = useAuth();
  const navigate = useNavigate();
  const [cta, setCta] = useState(() => registerCta(null, null));

  // 세션 객체가 아니라 사용자 신원으로 조회를 묶는다. session 은 토큰 갱신
  // (TOKEN_REFRESHED, 기본 1시간)마다 새 객체로 바뀌는데, 같은 사람이면 CTA 도 같다.
  const userId = session?.user?.id ?? null;

  useEffect(() => {
    const restoreHead = applyLandingHead();
    // facemarket 도메인에서는 되돌리지 않는다. 되돌리면 랜딩을 떠나는 순간 탭 제목이
    // index.html 의 정적 <title>('Wearless — AI 상세페이지 스튜디오')로 바뀌고, 이 도메인의
    // 나머지 화면 전체가 새로고침 전까지 그 상태로 남는다 — 생체정보를 넘기는 등록 7단계
    // 내내 탭에 셀러 제품 이름이 뜨고, 그 상태로 북마크하면 셀러 제목으로 저장된다.
    // 위 머리말이 복원의 근거로 든 '같은 문서에서 셀러 화면으로 이동할 수 있다'는 이 도메인에선
    // 사실상 성립하지 않는다: catch-all 이 '/' 로 돌려보내고(App.jsx) TopNav 도 셀러 탭을
    // 숨긴다(shell.jsx) — 주소창에 직접 치는 경우에만 도달한다. 그 예외를 위해 흔한 경로를
    // 망가뜨리는 거래는 맞지 않는다. (호스트별 정적 head 분리가 근본 해결이고 이건 그 전 조치.)
    // ai 도메인에서 이 컴포넌트를 재사용하게 되면 그땐 복원이 옳으므로 분기로 남겨 둔다.
    if (IS_FACEMARKET) return undefined;
    return restoreHead;
  }, []);

  // 등록 상태로 CTA 문구를 바꾼다. 조회는 랜딩 렌더를 막지 않는다 —
  // 기본 문구로 먼저 그리고, 결과가 오면 교체한다. 실패하면 기본 문구로 남는다.
  useEffect(() => {
    if (!userId) { setCta(registerCta(null, null)); return undefined; }

    let alive = true;
    void (async () => {
      const [models, enrollment] = await Promise.all([
        listMyModels().catch(() => null),
        getCurrentEnrollment().catch(() => null),
      ]);
      if (!alive) return;
      setCta(registerCta(models?.[0] || null, enrollment));
    })();
    return () => { alive = false; };
  }, [userId]);

  const runPrimary = useCallback(() => {
    if (session) navigate(cta.to);
    else openLogin('/model/register');
  }, [session, cta.to, navigate, openLogin]);

  // 부트스트랩 중 눌린 클릭을 담아 두는 보류함. FacemarketRoot 는 복귀 플래그가 없는
  // 평범한 방문이면 loading 중에도 랜딩을 그대로 그리므로, 이 창은 실제로 사용자에게 보인다.
  // ref 가 아니라 state 인 게 중요하다 — ref 면 재렌더가 없어 눌린 사실이 화면에 하나도
  // 안 남는다(라벨·스피너·disabled 어느 것도 안 바뀐다). 느린 회선에서는 "버튼이 고장 났나"
  // 하고 연타하다 몇 초 뒤 스크롤 위치와 무관하게 모달이 떠오르는 게 실제 증상이었다.
  const [pendingPrimary, setPendingPrimary] = useState(false);

  // 눌린 사실은 라벨로만 돌려준다. disabled 는 쓰지 않는다 — LandingHeader.jsx 머리말과
  // 같은 이유로, 버튼을 잠그면 클릭이 아예 안 들어와 보류함 자체가 죽는다.
  const primaryLabel = pendingPrimary ? '확인 중이에요…' : cta.label;

  const onPrimary = () => {
    // 부트스트랩 중에는 session=null 이 '비로그인'이 아니라 '아직 모름'이다. 이때
    // 분기를 태우면 이미 로그인한 사람에게 로그인 모달이 뜨고(세션이 뒤늦게 도착하면
    // Login.jsx 가 스스로 닫아 주지만, 그전까지는 맥락 없는 모달이다), openLogin 이
    // 복귀 플래그까지 심어 다음 '/' 진입을 엉뚱한 곳으로 보낸다. 플래그를 심는 것까지
    // 막아야 하므로 loading 게이트는 그대로 둔다.
    // RequireAuth·RootRedirect 가 loading 을 다루는 것과 같은 이유다.
    // 다만 조용히 return 하면 랜딩의 유일한 전환 버튼이 '눌러도 아무 일 없는 버튼'이
    // 된다. 분기는 미루되 의도는 버리지 않는다 — loading 이 내려가면 한 번만 실행한다.
    if (loading) { setPendingPrimary(true); return; }
    runPrimary();
  };

  // 보류된 클릭 소비. 연타해도 boolean 하나라 이동·모달은 한 번만 일어난다
  // (소비하며 false 로 내리고, 그 재렌더에서는 이 이펙트가 곧바로 빠져나온다).
  useEffect(() => {
    if (loading || !pendingPrimary) return;
    setPendingPrimary(false);
    runPrimary();
  }, [loading, pendingPrimary, runPrimary]);

  return (
    // header/footer 는 <main> 밖에 둔다. HTML-AAM 상 main·section·article 안에
    // 중첩된 header/footer 는 banner·contentinfo 랜드마크를 잃고 generic 이 된다
    // (스크린리더 랜드마크 점프가 안 된다). 셸 클래스는 그대로라 레이아웃은 안 바뀐다.
    <div className={s.shell} id="top">
      <LandingHeader onPrimary={onPrimary} primaryLabel={primaryLabel} />
      <main>
        <HeroSection onPrimary={onPrimary} primaryLabel={primaryLabel} />
        <GallerySection />
        <LicensingSection />
        <RegisterSection ctaLabel={primaryLabel} onPrimary={onPrimary} />
        {/* 마지막 섹션에도 CTA 를 준다. ModelInfoSection 은 프라이버시 규칙과 '그만두고
            싶을 때'로 끝나는데, 그 뒤가 CTA 없는 푸터라 끝까지 읽은 사람이 전환하려면
            스크롤을 되돌려 올라가야 했다 — 페이지의 마지막 인상이 '지급 미구현·삭제
            미구현'인 채로. 고지 문장은 그대로 두고 누를 자리만 그 뒤에 놓는다.
            prop 이름은 RegisterSection 과 같은 둘(ctaLabel·onPrimary)이다 — 새 이름을
            만들면 같은 버튼이 파일마다 다른 계약을 갖는다. */}
        <ModelInfoSection ctaLabel={primaryLabel} onPrimary={onPrimary} />
      </main>
      <FooterSection />
    </div>
  );
}
