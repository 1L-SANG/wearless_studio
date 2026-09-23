"""카카오 OpenID Connect 직접 로그인 — 인가코드 → id_token 교환(서버 전용).

**왜 Supabase 의 카카오 OAuth 를 안 쓰고 이 길로 왔나 (지우기 전에 읽어라).**
Supabase GoTrue 는 카카오 authorize 요청의 scope 를 코드에 **하드코딩**해서 붙인다 —
`account_email profile_image profile_nickname`(auth/internal/api/provider/kakao.go 는
append 이지 replace 가 아니다). 클라이언트에서 뺄 수단이 없다.
그런데 우리 카카오 앱의 `account_email` 은 **"권한 없음"** 이다(비즈앱 전환 + 추가기능
심사 3~5영업일 필요). 카카오는 앱에 설정되지 않은 동의항목이 섞인 인가 요청을
**KOE205** 로 거절한다 → 2026-09-22 시점 카카오 로그인이 전원 실패했다.

그래서 카카오만 OAuth provider 경로를 버리고 OIDC 로 간다:
  프론트 → kauth authorize(scope=openid) → **이 라우트**가 code 를 id_token 으로 교환
  → 프론트가 supabase.auth.signInWithIdToken({provider:'kakao', token, nonce}) → 세션.

GoTrue 의 id_token 경로는 그대로 살아 있다(internal/api/token_oidc.go):
`p.Provider == KakaoProvider || p.Issuer == provider.IssuerKakao` 로 분기해
`config.External.Kakao.ClientID` 를 aud 허용값으로 쓴다.
⇒ **Supabase 대시보드의 Kakao provider 는 계속 Enabled 여야 하고 client_id 도 그대로여야
한다. 그리고 여기 KAKAO_REST_API_KEY 는 그 값과 반드시 같은 값이다.** 프론트에서
signInWithOAuth 호출부를 지웠다고 대시보드 provider 를 끄면 aud 대조가 깨져 전부 실패한다.

이 모듈의 불변식:
  ① **code·id_token·access_token·client_secret 은 어느 레벨에서도 로그에 안 찍는다.**
     남기는 건 status 와 카카오가 준 error code 뿐이다(cx_identity.py 와 같은 규율).
  ② 응답에는 **id_token 만** 담는다. 카카오 access_token/refresh_token 은 우리가 쓸 데가
     없고, 브라우저로 내보내면 그 순간부터 카카오 API 를 대신 부를 수 있는 자격이 샌다.
  ③ authorization code 는 **POST JSON 본문**으로 받는다. 쿼리스트링으로 받으면
     main.py 의 http_error_log 미들웨어가 실패마다 `path=` 를 찍으면서 code 를
     CloudWatch 에 통째로 남긴다.
  ④ redirect_uri 는 **서버 화이트리스트**로 검증한다. 클라이언트가 준 값을 그대로
     카카오에 넘기면, 공격자가 자기 사이트를 redirect_uri 로 넣은 인가코드를 우리 키로
     교환하는 통로가 된다.
  ⑤ 키가 없으면 라우트를 숨기지 않고 **503 으로 거절**한다(main.py 581-582 의 결정).
     플래그로 라우트를 숨기면 프론트가 404 를 '미배포'와 구분하지 못한다.
  ⑥ 자동 재시도를 하지 않는다. 인가코드는 **일회용**이라 서버가 알아서 다시 때리면
     두 번째 호출이 확정 실패가 되고, 첫 성공을 덮어쓸 수도 있다.
"""

import logging

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# 공개 라우트의 IP 판정은 **복붙하지 않는다.** ALB append-mode XFF 의 마지막 값을 쓰는
# 규칙이 이 레포에 이미 세 벌 있고(public_routes·facemarket 등) 네 번째를 만들면 한 곳만
# 고쳐지는 날이 온다. 리미터 인스턴스는 main.py 가 app.state.kakao_login_limiter 로 박는다.
from .public_routes import _client_ip

log = logging.getLogger("wearless.kakao_oidc")

router = APIRouter(prefix="/v1/auth", tags=["Auth"])

_TOKEN_PATH = "/oauth/token"

#: 카카오 콘솔에 등록하는 Redirect URI 와 **같은 목록**이어야 한다.
#: 프로덕션 3호스트는 `.wearless.kr` 공유 쿠키를 쓰지만 PKCE verifier·nonce·state 와
#: 복귀 목표(wl_postLogin)는 sessionStorage = 오리진 한정이라, 콜백은 반드시 로그인을
#: 시작한 **그 오리진**으로 돌아와야 한다. 그래서 호스트마다 한 줄씩이다.
#: localhost 는 5173(vite strictPort). 127.0.0.1 은 넣지 않는다 — AppProviders 가
#: 루프백을 localhost 로 정규화하므로 그 표기로 통일한다.
DEFAULT_REDIRECT_URIS = (
    "https://ai.wearless.kr/auth/kakao/callback",
    "https://facemarket.wearless.kr/auth/kakao/callback",
    "https://admin.wearless.kr/auth/kakao/callback",
    "http://localhost:5173/auth/kakao/callback",
)


class KakaoTokenBody(BaseModel):
    """인가코드 교환 요청. 카멜케이스(프론트)와 스네이크케이스를 모두 받는다."""

    code: str = Field(min_length=1, max_length=512)
    redirect_uri: str = Field(alias="redirectUri", min_length=1, max_length=512)
    # PKCE 를 쓰면 함께 온다. client_secret 없이 갈 때의 유일한 방어라 값이 오면 그대로 넘긴다.
    code_verifier: str | None = Field(alias="codeVerifier", default=None,
                                      min_length=43, max_length=128)

    model_config = {"populate_by_name": True}


def _err(code: str, message: str, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def allowed_redirect_uris(settings) -> tuple[str, ...]:
    """화이트리스트 = 기본 목록 + env 추가분(KAKAO_REDIRECT_URIS).

    env 추가분은 병렬 워크트리(5174)나 터널 호스트처럼 **일시적인 QA 주소**를 위한 것이다.
    프로덕션 세 호스트를 여기서 빼지 마라 — 카카오 콘솔과 짝이 어긋나면 로그인이 죽는다.
    """
    return tuple(DEFAULT_REDIRECT_URIS) + tuple(settings.kakao_extra_redirect_uris or ())


def _require_kakao_keys(request: Request) -> tuple[str, str | None]:
    """REST API 키가 없으면 503. 기동을 막지 않는 이유는 모듈 docstring ⑤ 참고."""
    settings = request.app.state.settings
    if not settings.kakao_rest_api_key:
        raise _err("kakao_login_not_configured", "카카오 로그인이 아직 설정되지 않았어요.", 503)
    return settings.kakao_rest_api_key, settings.kakao_client_secret


def _transport_for(settings):
    """테스트가 MockTransport 로 갈아끼우는 이음매. 운영에서는 None(기본 전송).

    toss_billing.py 와 같은 구조다. AsyncClient 를 함수 안에서 인라인으로 만들면 테스트가
    함수 전체를 monkeypatch 할 수밖에 없어 **HTTP 계약 자체**(URL·grant_type·client_secret
    전송 여부)를 한 번도 검증하지 못한다.
    """
    return None


def _client(settings) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=settings.kakao_token_timeout,
                             transport=_transport_for(settings))


def _rate_limit(request: Request) -> None:
    """IP 레이트리밋. 공개(미인증) 라우트라 IP 하나만 근거다.

    리미터 조회 실패는 fail-open 이다(public_routes.py 129-135 와 같은 판단) — 리미터
    장애가 로그인 자체를 막으면 안 된다.
    """
    client_ip = _client_ip(request)
    try:
        limiter = request.app.state.kakao_login_limiter
        allowed = limiter.allow(client_ip)
    except Exception:
        log.exception("kakao login rate limiter unavailable")
        allowed = True
    if not allowed:
        raise _err("rate_limited", "잠시 후 다시 시도해주세요.", 429)


async def _exchange_with_kakao(settings, *, client_id: str, client_secret: str | None,
                               body: KakaoTokenBody) -> dict:
    """kauth.kakao.com/oauth/token 호출. client_secret_post 방식(discovery 실측).

    시크릿·코드는 예외 메시지에도 싣지 않는다 — 여기서 올라간 문자열이 그대로
    사용자 화면과 로그로 간다.
    """
    form = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "redirect_uri": body.redirect_uri,
        "code": body.code,
    }
    # 카카오 콘솔에서 'Client Secret 사용함' 일 때만 필요하다. 안 쓰면 PKCE 가 그 자리를
    # 대신한다(discovery: code_challenge_methods_supported=["S256"]).
    if client_secret:
        form["client_secret"] = client_secret
    if body.code_verifier:
        form["code_verifier"] = body.code_verifier

    try:
        async with _client(settings) as client:
            res = await client.post(f"{settings.kakao_auth_base}{_TOKEN_PATH}", data=form)
    except httpx.HTTPError as e:
        # 전송 실패는 '카카오가 거절했다'가 아니라 '결과를 모른다'다. 사용자에게는 재시도를
        # 권하고, 로그에는 예외 **타입만** 남긴다(URL 에 code 가 붙어 있을 수 있다).
        log.warning("kakao token unreachable: %s", type(e).__name__)
        raise _err("kakao_unreachable",
                   "카카오와 연결하지 못했어요. 잠시 후 다시 시도해 주세요.", 503) from None

    if res.status_code != 200:
        try:
            payload = res.json()
        except Exception:
            payload = {}
        code = str(payload.get("error") or "kakao_token_exchange_failed")
        log.warning("kakao token rejected status=%s error=%s", res.status_code, code)
        if res.status_code >= 500:
            raise _err("kakao_unreachable",
                       "카카오와 연결하지 못했어요. 잠시 후 다시 시도해 주세요.", 503)
        # 4xx 의 대부분은 사용자 원인이다(만료·재사용된 인가코드, 뒤로가기). 5xx 로 올리면
        # Slack 알림 채널이 죽는다(main.py 441-447). 카카오 원문 메시지는 그대로 내보내지
        # 않는다 — 우리 사용자에게 쓰는 말이 아니고, 키 정보가 섞여 들어올 여지도 있다.
        raise _err("kakao_token_exchange_failed",
                   "카카오 로그인을 완료하지 못했어요. 다시 시도해 주세요.", 400)

    return res.json()


@router.post("/kakao/token", summary="카카오 인가코드 → id_token 교환")
async def exchange_kakao_code(request: Request, body: KakaoTokenBody):
    """카카오 인가코드를 id_token 으로 바꿔 준다. **미인증 공개 라우트**다.

    - **Bearer Token**: 불필요(로그인 전에 부르는 라우트다)
    - **응답**: `{"id_token": "..."}` — 그 외 토큰은 담지 않는다(모듈 docstring ②)
    - **에지 케이스**: `503 kakao_login_not_configured`(키 미설정) ·
      `400 invalid_redirect_uri`(화이트리스트 밖) · `400 kakao_token_exchange_failed`
      (만료·재사용 코드) · `503 kakao_unreachable` · `429 rate_limited`
    """
    _rate_limit(request)
    client_id, client_secret = _require_kakao_keys(request)
    settings = request.app.state.settings

    if body.redirect_uri not in allowed_redirect_uris(settings):
        # 값은 로그에 남기지 않는다 — 공격자가 넣은 URL 이 그대로 로그 뷰어에 뜨는 것도,
        # 정상 사용자의 주소가 남는 것도 원치 않는다.
        log.warning("kakao token rejected: redirect_uri not allowed")
        raise _err("invalid_redirect_uri", "카카오 로그인 주소가 올바르지 않아요.", 400)

    payload = await _exchange_with_kakao(
        settings, client_id=client_id, client_secret=client_secret, body=body,
    )
    id_token = payload.get("id_token")
    if not id_token:
        # scope 에 openid 가 빠졌을 때 벌어지는 일이다 = 우리 쪽 배선 사고지 사용자 실수가
        # 아니다. 5xx 로 올려 알림에 걸리게 둔다.
        log.error("kakao token response had no id_token — scope 에 openid 가 있는지 확인")
        raise _err("kakao_id_token_missing",
                   "카카오 로그인 응답이 올바르지 않아요. 잠시 후 다시 시도해 주세요.", 502)

    # no-store: id_token 은 그 자체로 로그인 자격이다. 프록시·브라우저 캐시에 남기지 않는다.
    return JSONResponse({"id_token": id_token}, headers={"Cache-Control": "no-store"})


__all__ = ["router", "allowed_redirect_uris", "DEFAULT_REDIRECT_URIS"]
