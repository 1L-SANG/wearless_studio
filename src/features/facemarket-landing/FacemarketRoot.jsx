/* =============================================================
   facemarket-landing/FacemarketRoot.jsx
   facemarket 도메인의 '/' 진입점. 로그인 복귀 목표가 있으면 그리로 보내고,
   없으면 랜딩을 그린다.

   여기엔 순서 계약이 하나 있다. **인증 부트스트랩이 끝나기 전에는 절대 이동하지
   않는다.** 이유가 두 개다.

   1) OAuth code 를 지키기 위해. supabase 는 detectSessionInUrl:false 라
      `?code=` 교환처는 AuthProvider 한 곳뿐인데(AuthProvider.jsx 의 부트스트랩
      effect), AuthProvider 는 BrowserRouter 의 조상이다(main.jsx). React 의 passive
      effect 는 자식→부모 순이라, 첫 렌더에서 <Navigate> 를 반환하면 그 effect 가
      먼저 돌아 history.replaceState 로 쿼리스트링을 통째로 지운다 — AuthProvider 가
      code 를 읽기도 전에. 그러면 이 도메인에서는 아무도 로그인할 수 없다.
      RootRedirect(App.jsx)가 `if (loading && target !== '/create/input') return;` 로
      지키던 계약이 바로 이것이고, 여기서 같은 계약을 쓴다.

   2) 취소된 로그인의 묵은 플래그로 랜딩을 잠그지 않기 위해. 세션 없이 이동하면
      /model/register 의 RequireAuth 가 FacemarketLoginPrompt 를 띄우고, 그게
      openLogin 으로 플래그를 **다시 심는다**. 그 탭에서 랜딩은 영영 못 본다.
      그래서 소비 조건은 '플래그가 있다'가 아니라 '부트스트랩이 끝났고 세션이 있다'다.
      세션이 없으면(=로그인을 취소했다) 플래그를 버리고 랜딩을 그린다.
   ============================================================= */
import { useEffect, useState } from 'react';
import { Navigate } from 'react-router-dom';
import { forgetPostLogin, readPostLogin, useAuth } from '@/features/auth/AuthProvider.jsx';
import { FacemarketLanding } from './FacemarketLanding.jsx';
import { facemarketRootTarget } from './facemarketRootTarget.js';

/* 키 이름('wl_postLogin')과 저장소 예외 처리는 AuthProvider 가 단독으로 소유한다 —
   여기서 sessionStorage 를 직접 부르면 같은 키의 주인이 둘이 되고, 한쪽만 하드닝된
   '반쪽' 상태가 라운드마다 되살아난다(App.jsx RootRedirect 가 실제로 그랬다: 맨몸 접근이
   쿠키·사이트데이터를 막은 브라우저에서 셀러 도메인 루트를 흰 화면으로 만들었다).
   읽기가 실패하면 null 로 떨어지고 — 랜딩은 그대로 뜬다. 복귀 목표는 편의 기능이다. */
function readReturnTarget() {
  return facemarketRootTarget(readPostLogin());
}

/* 지울 때 값을 대조한다 — 내가 마운트에서 집어온 그 의도만 지운다. 비교는 raw 문자열이
   아니라 facemarketRootTarget() 을 통과시킨 값으로 한다(readReturnTarget 과 같은 렌즈).
   '  /model  ' 처럼 정규화로 달라지는 값을 raw 로 비교하면 자기 플래그를 못 알아본다. */
function forgetReturnTargetIfUnchanged(expected) {
  if (readReturnTarget() !== expected) return;   // 그 사이 새 의도가 심겼다 — 남의 것이다
  forgetPostLogin();
}

export function FacemarketRoot() {
  const { session, loading } = useAuth();

  // 읽기만 렌더에서 한다. StrictMode(dev)는 마운트 렌더를 두 번 돌리고 두 번째 결과를
  // 커밋하므로, 여기서 지우면 두 번째 렌더가 빈 값을 읽어 복귀 목표가 통째로 사라진다.
  const [target] = useState(readReturnTarget);

  const settled = !loading;                              // 부트스트랩(=code 교환) 종료
  const destination = settled && session ? target : null;

  // 지우기는 커밋 뒤에. 부트스트랩이 끝난 뒤에만 지운다 — 그 전에 지우면 code 교환이
  // 늦게 끝나는 사이에 복귀 목표를 잃는다.
  //
  // ⚠︎ 이 조건은 라운드마다 왕복한 자리다. **무조건 지우기로 되돌리지 마라.**
  // 무조건 지우면 자식(FacemarketLanding)의 보류 클릭을 부모(여기)가 잡아먹는다:
  //   1) 플래그 없이 들어온 평범한 방문은 target=null 이라 !settled 중에도 랜딩이 그려진다
  //      (아래 스피너 분기는 target 이 있을 때만 탄다). 그래서 부트스트랩 중에 CTA 가
  //      실제로 보이고 눌린다.
  //   2) 그 클릭은 FacemarketLanding 의 보류함에 담겼다가 loading:true→false 커밋에서
  //      소비돼 openLogin('/model/register') → rememberPostLogin() 으로 플래그를 심는다.
  //   3) React 의 passive effect 는 자식→부모 순이다(이 파일 머리말이 근거로 삼는 바로 그
  //      순서 계약). 즉 자식의 setItem 이 먼저, 부모의 removeItem 이 나중 — 방금 심은
  //      의도가 같은 커밋에서 지워져, 구글 로그인을 마치고 돌아와도 등록 위저드가 아니라
  //      랜딩이 뜬다. 랜딩의 유일한 전환 경로가 통째로 헛돈다.
  // 그래서 두 겹으로 좁힌다.
  //   (a) `!target` 이면 애초에 지울 게 없다 — 평범한 방문에서는 손대지 않는다.
  //   (b) target 이 있어도 지우기 직전 값을 대조한다 — 부트스트랩 도중 새로 심긴 의도는 산다.
  // 원래 목적(취소된 로그인의 묵은 플래그가 랜딩을 잠그는 것 막기)은 그대로다: target 이
  // 있는 진입은 !settled 동안 스피너만 그려 랜딩이 마운트되지 않으므로 (b)가 통과하고,
  // 세션이 없으면(=취소) 그대로 버려진다.
  //
  // 반대 방향(‘소비 안 된 플래그가 남는다’며 조건을 다시 넓히기)도 하지 마라. 랜딩이 떠
  // 있는 동안 CTA 가 새로 심은 플래그는 여기서 안 지워지는 게 **맞다** — 그건 그 사용자가
  // 방금 누른 등록 의도이고, 다음 '/' 진입에서 마운트가 한 번 소비한 뒤(위 useState) 이
  // 이펙트가 곧바로 지운다. 즉 최대 한 번 어긋날 뿐이고, 그 한 번마저 사용자가 누른
  // '모델 등록 시작'과 같은 방향이다. 그걸 없애자고 destination === null 까지 지우게 하면
  // (a)가 무력화돼 위의 경합(부모가 자식의 의도를 잡아먹는 것)이 그대로 되살아난다.
  useEffect(() => {
    if (!settled || !target) return;
    forgetReturnTargetIfUnchanged(target);
  }, [settled, target]);

  // 복귀 목표를 들고 온 진입에서만 잠깐 기다린다. 평범한 방문(플래그 없음)은
  // 인증과 무관하게 랜딩을 즉시 그린다 — 스펙: 루트는 로그인 여부와 무관하게 랜딩.
  if (target && !settled) return <div className="route-loading">불러오는 중이에요</div>;
  if (destination) return <Navigate replace to={destination} />;
  return <FacemarketLanding />;
}
