/* =============================================================
   facemarket-landing/LandingShell.jsx
   랜딩 네 페이지(홈·라이선싱·모델 등록·모델 정보)가 공유하는 껍데기.
   앱 크롬(ChromeLayout) 밖 독립 surface 다 — TopNav 의 크레딧 배지·플로우 스테퍼는
   셀러 스튜디오 물건이고, 이 상단바는 랜딩 전용 내비게이션이다.

   여기 모아 둔 것: 페이지별 head 교체, 등록 상태 → CTA 문구 조회, 인증 부트스트랩
   중 눌린 CTA 의 보류·소비. 네 페이지가 같은 상단바와 같은 CTA 를 쓰므로 이 상태를
   페이지마다 복제하면 라우트를 옮길 때마다 조회가 다시 돌고 보류함이 초기화된다.

   children 은 렌더 프롭이다 — 섹션들이 CTA(문구·핸들러)를 받아야 하는데, prop 을
   cloneElement 로 몰래 주입하면 각 페이지가 무엇을 받는지 호출부에서 안 보인다.
   ============================================================= */
import { useCallback, useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '@/features/auth/AuthProvider.jsx';
import { IS_FACEMARKET } from '@/lib/host.js';
import { getCurrentEnrollment, listMyModels } from '@/lib/api/facemarket.js';
import { LandingHeader } from './LandingHeader.jsx';
import { registerCta } from './registerCta.js';
import { FooterSection } from './sections/FooterSection.jsx';
import s from './FacemarketLanding.module.css';

/* index.html 의 head 는 셀러 스튜디오("Wearless — AI 상세페이지 스튜디오") 것이다.
   랜딩은 SPA 라우트라 정적 head 를 도메인별로 나눌 수 없어서 마운트 때 갈아 끼운다.
   주의: JS 를 실행하지 않는 크롤러의 공유 미리보기(og:*)는 이걸로 안 바뀐다 —
   그건 정적 head 를 호스트별로 나눠야 하는 별건이다. */
function applyHead(title, description) {
  const previousTitle = document.title;
  document.title = title;

  let tag = document.querySelector('meta[name="description"]');
  const created = !tag;
  if (created) {
    tag = document.createElement('meta');
    tag.setAttribute('name', 'description');
    document.head.appendChild(tag);
  }
  const previousDescription = created ? null : tag.getAttribute('content');
  tag.setAttribute('content', description);

  return () => {
    document.title = previousTitle;
    if (created) tag.remove();
    else if (previousDescription === null) tag.removeAttribute('content');
    else tag.setAttribute('content', previousDescription);
  };
}

export function LandingShell({ title, description, children }) {
  const { session, loading, openLogin } = useAuth();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const [cta, setCta] = useState(() => registerCta(null, null));

  // 라우터는 전환할 때 스크롤을 건드리지 않는다. 문서가 그대로라 홈에서 캐러셀까지
  // 내려간 뒤 '모델 정보'를 누르면 새 페이지가 중간부터 보인다 — 새 페이지처럼 보여야
  // 하는 전환이라 맨 위에서 시작해야 한다.
  // 뒤로가기의 스크롤 복원까지 뺏지 않으려고 history.scrollRestoration 은 건드리지 않는다.
  // 항상 즉시 이동이다 — 페이지가 바뀌는 전환을 부드럽게 굴리면 새 페이지가 스크롤되며
  // 나타나고, 거리가 길면 '동작 줄이기'를 켠 사람이 정확히 막으려는 모션이 된다.
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [pathname]);

  useEffect(() => {
    const restoreHead = applyHead(title, description);
    // facemarket 도메인에서는 되돌리지 않는다. 되돌리면 랜딩을 떠나는 순간 탭 제목이
    // index.html 의 정적 <title>('Wearless — AI 상세페이지 스튜디오')로 바뀌고, 이 도메인의
    // 나머지 화면 전체가 새로고침 전까지 그 상태로 남는다 — 생체정보를 넘기는 등록 7단계
    // 내내 탭에 셀러 제품 이름이 뜨고, 그 상태로 북마크하면 셀러 제목으로 저장된다.
    // 랜딩 네 페이지 사이를 오갈 때도 다음 페이지가 곧바로 자기 제목을 덮으므로,
    // 복원을 건너뛴다고 해서 제목이 낡은 채 남지 않는다.
    // ai 도메인에서 이 셸을 재사용하게 되면 그땐 복원이 옳으므로 분기로 남겨 둔다.
    if (IS_FACEMARKET) return undefined;
    return restoreHead;
  }, [title, description]);

  // 세션 객체가 아니라 사용자 신원으로 조회를 묶는다. session 은 토큰 갱신
  // (TOKEN_REFRESHED, 기본 1시간)마다 새 객체로 바뀌는데, 같은 사람이면 CTA 도 같다.
  const userId = session?.user?.id ?? null;

  // 등록 상태로 CTA 문구를 바꾼다. 조회는 렌더를 막지 않는다 —
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
    // (스크린리더 랜드마크 점프가 안 된다).
    <div className={s.shell} id="top">
      {/* 맨 위 안내 띠 — 왼쪽 '**모델을 위한** 안내 페이지입니다.', 오른쪽 셀러용 스튜디오 링크
          (문구는 2026-09-02 사용자 지시). 셀러가 facemarket 도메인에 잘못 들어와도 첫 줄에서
          갈라진다. 랜딩 네 페이지(이 셸)에만 있고 /model/* 은 없다 — 거긴 이미 모델이 서 있는
          화면이다. 링크는 푸터와 같은 ai.wearless.kr, 같은 탭이다.
          띄어쓰기는 지시받은 문구에서 두 군데 고쳤다: '안내페이지 입니다' → '안내 페이지입니다',
          '만들러가기' → '만들러 가기'(보조용언은 띄고 서술격 조사는 붙인다). 브랜드는
          'Wearless' 로 대문자 유지 — 푸터·로고와 같은 표기여야 한다. */}
      <div className={s.topNotice}>
        <div className={s.topNoticeInner}>
          <span>
            <strong>모델을 위한</strong> 안내 페이지입니다.
          </span>
          <a className={s.topNoticeLink} href="https://ai.wearless.kr">
            셀러이신가요? 상세페이지 만들러 가기 Wearless →
          </a>
        </div>
      </div>
      <LandingHeader onPrimary={onPrimary} primaryLabel={primaryLabel} />
      <main>{children({ ctaLabel: primaryLabel, onPrimary })}</main>
      <FooterSection />
    </div>
  );
}
