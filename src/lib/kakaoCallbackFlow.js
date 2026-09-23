/* =============================================================
   lib/kakaoCallbackFlow — 카카오 OIDC 콜백의 판단 전부.

   화면(features/auth/KakaoCallback.jsx)에서 떼어 낸 이유는 하나다: **테스트하려고.**
   이 레포의 프론트 러너는 node:test 이고, 순수 모듈은 그냥 import 해서 단위 테스트할 수
   있다(host-route-boundary 와 같은 선례). React·vite SSR 하네스를 세우지 않고도
   'state 가 안 맞으면 교환을 시도조차 하지 않는다' 같은 계약을 못 박을 수 있다.

   그래서 http·signInWithKakaoIdToken 을 **인자로 받는다** — 이 파일은 supabase 도
   httpAdapter 도 import 하지 않는다. 화면이 useAuth() 에서 받아 넘긴다(화면 컴포넌트가
   supabase 를 직접 부르지 않는다는 이 레포의 인증 경계도 그대로 지켜진다).
   ============================================================= */
/* 상대경로로 import 한다 — node:test 가 이 모듈을 `@/` 별칭 해석 없이 그대로 import 해서
   단위 테스트하기 때문이다(vite 만 그 별칭을 안다). */
import { clearSignupConsent } from './signupConsent.js';
import { consumeKakaoAuthRequest, readKakaoCallbackParams } from './kakaoOidc.js';

export function kakaoFailure(code, message) {
  /* 실패한 카카오 시도가 들고 있던 '가입 동의 표시'를 여기서 버린다.
     그 표시(wl_signupConsent)는 소셜 버튼을 누르기 **전에** 심고 SignupCompletion 이
     로그인 후 서버에 기록한다. 전체 페이지 이동으로 돌아오는 이 경로는 LoginGate 가
     언마운트된 뒤라 그쪽의 롤백(pageshow bfcache)이 안 돈다 — 남겨 두면 30분 TTL 안에
     **다음 로그인, 심지어 다른 계정**으로 동의가 새어 들어간다.
     성공 경로에서는 지우지 않는다(그 표시를 쓰라고 심은 것이다). */
  clearSignupConsent();
  return { ok: false, code, message };
}

/**
 * 콜백 URL 을 읽고, 교환하고, 세션을 만든다.
 *
 * @param {object} deps
 * @param {string} deps.search  window.location.search
 * @param {Function} deps.http  httpAdapter 의 http(path, options)
 * @param {Function} deps.signInWithKakaoIdToken  useAuth() 가 주는 함수
 * @returns {Promise<{ok: true} | {ok: false, code: string, message: string}>}
 */
export async function runKakaoCallbackFlow({ search, http, signInWithKakaoIdToken }) {
  const params = readKakaoCallbackParams(search);

  if (params.error) {
    // 사용자가 카카오 동의 화면에서 취소하면 여기로 온다(access_denied).
    return kakaoFailure(
      params.error,
      params.error === 'access_denied'
        ? '카카오 로그인을 취소했어요. 다시 시도하시려면 아래 버튼을 눌러 주세요.'
        : '카카오 로그인을 완료하지 못했어요. 잠시 후 다시 시도해 주세요.',
    );
  }
  if (!params.code || !params.state) {
    return kakaoFailure('missing_code', '카카오 로그인 정보를 찾지 못했어요. 처음부터 다시 시도해 주세요.');
  }

  /* state 불일치 = 우리가 시작한 로그인이 아니거나, 탭이 바뀌었거나, 저장소가 비워졌다.
     대조 없이 교환하면 공격자가 자기 인가코드를 피해자 브라우저에 밀어 넣어
     **피해자를 공격자 계정으로 로그인시키는** CSRF 가 된다. 교환을 시도조차 하지 않는다. */
  const request = consumeKakaoAuthRequest(params.state);
  if (!request) {
    return kakaoFailure('state_mismatch', '로그인 요청이 만료됐어요. 처음부터 다시 시도해 주세요.');
  }

  let payload;
  try {
    // 공개(무인증) 라우트다 — 서버 쪽에 require_user 가 없다. 인가코드는 **본문**으로만
    // 보낸다: 쿼리로 보내면 서버의 http_error_log 미들웨어가 실패마다 path 를 찍으면서
    // code 를 CloudWatch 에 통째로 남긴다.
    payload = await http('/v1/auth/kakao/token', {
      method: 'POST',
      requireAuth: false,
      body: {
        code: params.code,
        redirectUri: request.redirectUri,
        ...(request.codeVerifier ? { codeVerifier: request.codeVerifier } : {}),
      },
    });
  } catch (error) {
    // 서버가 준 한국어 message 를 그대로 보여준다(에러 봉투 계약 §6).
    return kakaoFailure(error?.code || 'exchange_failed',
      error?.message || '카카오 로그인을 완료하지 못했어요. 잠시 후 다시 시도해 주세요.');
  }
  if (!payload?.id_token) {
    return kakaoFailure('id_token_missing', '카카오 로그인 응답이 올바르지 않아요. 잠시 후 다시 시도해 주세요.');
  }

  /* 여기 넘기는 nonce 는 **원본**이다 — 카카오 인가 요청에 실린 건 그 값의 sha256 해시고,
     GoTrue 가 우리가 준 원본을 해시해서 id_token 의 클레임과 맞춘다(kakaoOidc.js sha256Hex).
     해시를 못 만든 브라우저에서는 저장된 nonce 가 null 이고 인가 요청에도 nonce 가 없었다 —
     그때는 **보내지 않는다**(둘 중 하나만 있으면 GoTrue 가 거절한다). */
  const { error } = await signInWithKakaoIdToken({
    token: payload.id_token,
    ...(request.nonce ? { nonce: request.nonce } : {}),
  });
  if (error) {
    /* 여기서 실패하는 가장 흔한 원인은 **Supabase 대시보드의 Kakao provider 가 꺼졌거나
       client_id 가 우리 VITE_KAKAO_REST_API_KEY 와 달라진 것**이다 — GoTrue 가 id_token 의
       aud 를 그 값과 대조한다(token_oidc.go). 둘을 맞추기 전에는 다시 눌러도 같은 결과다.
       토큰은 로그에 싣지 않는다 — 그 자체가 로그인 자격이다. */
    console.error('[auth] 카카오 id_token 로그인 실패', error?.message || error);
    return kakaoFailure('id_token_rejected', '카카오 계정으로 로그인하지 못했어요. 잠시 후 다시 시도해 주세요.');
  }
  return { ok: true };
}
