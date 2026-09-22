"""카카오 OIDC 인가코드 교환 — HTTP 계약 회귀.

여기서 지키는 것:
  ① 응답에는 **id_token 만** 담긴다(카카오 access_token·refresh_token 유출 금지).
  ② code·client_secret·토큰이 **로그에 한 글자도** 남지 않는다.
  ③ redirect_uri 는 서버 화이트리스트를 지난다(임의 URI 를 우리 키로 교환해 주지 않는다).
  ④ 키 미설정은 라우트를 숨기는 게 아니라 503 으로 말한다.
  ⑤ 카카오의 확정 거절은 4xx 로 내려간다(5xx 는 Slack 알림에 물려 있다).
  ⑥ client_secret 은 설정됐을 때만 전송한다(PKCE 만 쓰는 구성도 그대로 동작해야 한다).
"""

import logging

import httpx
import pytest
from fastapi.testclient import TestClient

import app.kakao_oidc as kakao
from app.main import create_app
from conftest import make_settings

CALLBACK = "https://facemarket.wearless.kr/auth/kakao/callback"
ID_TOKEN = "header.payload.signature"


def build_client(**overrides) -> TestClient:
    settings = make_settings(
        kakao_rest_api_key="rest-key",
        kakao_auth_base="https://kauth.test",
        **overrides,
    )
    return TestClient(create_app(settings))


@pytest.fixture()
def stub(monkeypatch):
    """_transport_for 를 MockTransport 로 갈아끼운다(네트워크 없이 계약만 검증)."""

    def install(handler):
        monkeypatch.setattr(kakao, "_transport_for", lambda s: httpx.MockTransport(handler))

    return install


def _form(request: httpx.Request) -> dict[str, str]:
    return dict(pair.split("=", 1) for pair in request.content.decode().split("&") if pair)


def test_exchange_returns_only_the_id_token(stub):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["form"] = _form(request)
        return httpx.Response(200, json={
            "id_token": ID_TOKEN,
            # 카카오는 언제나 이 둘을 같이 준다. 브라우저로 새어 나가면 그 순간부터
            # 카카오 API 를 사용자 대신 부를 수 있는 자격이 된다.
            "access_token": "kakao-access-token",
            "refresh_token": "kakao-refresh-token",
            "token_type": "bearer",
        })

    stub(handler)
    res = build_client().post("/v1/auth/kakao/token", json={
        "code": "auth-code-1", "redirectUri": CALLBACK, "codeVerifier": "v" * 64,
    })
    assert res.status_code == 200
    assert res.json() == {"id_token": ID_TOKEN}
    assert res.headers["cache-control"] == "no-store"
    assert seen["url"] == "https://kauth.test/oauth/token"
    assert seen["form"]["grant_type"] == "authorization_code"
    assert seen["form"]["client_id"] == "rest-key"
    assert seen["form"]["code"] == "auth-code-1"
    assert seen["form"]["code_verifier"] == "v" * 64


def test_client_secret_is_sent_only_when_configured(stub):
    forms = []
    stub(lambda request: (forms.append(_form(request)), httpx.Response(200, json={"id_token": ID_TOKEN}))[1])

    # 시크릿 미설정(= PKCE 만 쓰는 구성) — client_secret 키 자체가 없어야 한다.
    build_client().post("/v1/auth/kakao/token",
                        json={"code": "c", "redirectUri": CALLBACK})
    assert "client_secret" not in forms[-1]

    build_client(kakao_client_secret="shhh").post(
        "/v1/auth/kakao/token", json={"code": "c", "redirectUri": CALLBACK})
    assert forms[-1]["client_secret"] == "shhh"


def test_rejects_redirect_uri_outside_the_whitelist(stub):
    called = []
    stub(lambda request: (called.append(1), httpx.Response(200, json={"id_token": ID_TOKEN}))[1])
    res = build_client().post("/v1/auth/kakao/token", json={
        "code": "auth-code-1", "redirectUri": "https://evil.example/auth/kakao/callback",
    })
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "invalid_redirect_uri"
    # 화이트리스트 밖이면 **카카오를 부르지도 않는다** — 우리 키로 남의 코드를 교환해 주는
    # 통로가 되면 안 된다.
    assert called == []


def test_extra_redirect_uris_from_env_are_allowed(stub):
    stub(lambda request: httpx.Response(200, json={"id_token": ID_TOKEN}))
    extra = "http://localhost:5174/auth/kakao/callback"
    client = build_client(kakao_extra_redirect_uris=(extra,))
    assert client.post("/v1/auth/kakao/token",
                       json={"code": "c", "redirectUri": extra}).status_code == 200
    # 프로덕션 기본 목록은 env 추가와 무관하게 그대로 산다.
    assert client.post("/v1/auth/kakao/token",
                       json={"code": "c", "redirectUri": CALLBACK}).status_code == 200


def test_missing_keys_answer_503_instead_of_hiding_the_route():
    """플래그로 라우트를 숨기면 프론트가 404 를 '미배포'와 구분하지 못한다(main.py 관례)."""
    settings = make_settings(kakao_auth_base="https://kauth.test")
    res = TestClient(create_app(settings)).post(
        "/v1/auth/kakao/token", json={"code": "c", "redirectUri": CALLBACK})
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "kakao_login_not_configured"


def test_kakao_rejection_stays_4xx_and_does_not_leak_kakao_wording(stub):
    """만료·재사용된 인가코드는 사용자 원인이다. 5xx 로 올리면 알림 채널이 죽는다."""
    stub(lambda request: httpx.Response(400, json={
        "error": "invalid_grant", "error_description": "authorization code not found",
    }))
    res = build_client().post("/v1/auth/kakao/token",
                              json={"code": "used", "redirectUri": CALLBACK})
    assert res.status_code == 400
    body = res.json()["error"]
    assert body["code"] == "kakao_token_exchange_failed"
    assert "authorization code not found" not in body["message"]


def test_kakao_5xx_and_transport_failure_are_retryable_503(stub):
    stub(lambda request: httpx.Response(502, json={"error": "server_error"}))
    res = build_client().post("/v1/auth/kakao/token",
                              json={"code": "c", "redirectUri": CALLBACK})
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "kakao_unreachable"

    def boom(request):
        raise httpx.ConnectError("boom")

    stub(boom)
    res = build_client().post("/v1/auth/kakao/token",
                              json={"code": "c", "redirectUri": CALLBACK})
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "kakao_unreachable"


def test_missing_id_token_is_our_bug_not_the_users(stub):
    """id_token 이 없다 = scope 에 openid 가 빠졌다. 우리 배선 사고라 알림에 걸려야 한다."""
    stub(lambda request: httpx.Response(200, json={"access_token": "a"}))
    res = build_client().post("/v1/auth/kakao/token",
                              json={"code": "c", "redirectUri": CALLBACK})
    assert res.status_code == 502
    assert res.json()["error"]["code"] == "kakao_id_token_missing"


def test_secrets_never_reach_the_logs(stub):
    """code·client_secret·토큰은 어느 레벨에서도 안 찍는다(cx_identity 와 같은 규율).

    caplog 를 안 쓰는 이유: main._configure_logging() 이 **root 핸들러를 통째로 교체**하므로
    create_app 뒤에는 caplog 의 핸들러가 떨어져 나가 기록이 비어 버린다(= 무엇을 찍든
    통과하는 가짜 초록불). 그래서 앱을 만든 **뒤에** 직접 핸들러를 건다.
    """

    def handler(request):
        if "reject" in request.content.decode():
            return httpx.Response(401, json={"error": "invalid_client"})
        return httpx.Response(200, json={"id_token": ID_TOKEN, "access_token": "kakao-access"})

    stub(handler)
    client = build_client(kakao_client_secret="super-secret-value")
    records: list[logging.LogRecord] = []

    class Collect(logging.Handler):
        def emit(self, record):
            records.append(record)

    sink = Collect(level=0)
    root = logging.getLogger()
    previous_level = root.level
    root.addHandler(sink)
    root.setLevel(0)
    try:
        client.post("/v1/auth/kakao/token",
                    json={"code": "top-secret-code", "redirectUri": CALLBACK})
        client.post("/v1/auth/kakao/token",
                    json={"code": "reject-me", "redirectUri": CALLBACK})
        client.post("/v1/auth/kakao/token",
                    json={"code": "top-secret-code", "redirectUri": "https://evil.example/cb"})
    finally:
        root.removeHandler(sink)
        root.setLevel(previous_level)

    assert records, "로그가 한 줄도 안 잡혔다 — 이 테스트는 가짜 초록불이 되기 쉽다"
    blob = "\n".join(record.getMessage() for record in records)
    for forbidden in ("top-secret-code", "reject-me", "super-secret-value",
                      "kakao-access", ID_TOKEN, "evil.example"):
        assert forbidden not in blob, f"로그에 민감값이 남았다: {forbidden}"
    # 진단에 필요한 것(status·카카오 error code)은 남아야 한다.
    assert "invalid_client" in blob


def test_authorization_code_is_never_a_query_parameter():
    """GET 쿼리로 받으면 http_error_log 미들웨어가 실패마다 path 에 code 를 찍는다."""
    client = build_client()
    assert client.get("/v1/auth/kakao/token?code=leaky").status_code == 405


def test_rate_limiter_is_swappable_and_closes_the_route():
    class DenyLimiter:
        def allow(self, key, **kwargs):
            return False

    client = build_client()
    client.app.state.kakao_login_limiter = DenyLimiter()
    res = client.post("/v1/auth/kakao/token", json={"code": "c", "redirectUri": CALLBACK})
    assert res.status_code == 429
    assert res.json()["error"]["code"] == "rate_limited"


def test_rate_limiter_failure_does_not_block_login(stub):
    """리미터 장애가 로그인 자체를 막으면 안 된다(public_routes 와 같은 fail-open)."""
    stub(lambda request: httpx.Response(200, json={"id_token": ID_TOKEN}))

    class BrokenLimiter:
        def allow(self, key, **kwargs):
            raise RuntimeError("limiter down")

    client = build_client()
    client.app.state.kakao_login_limiter = BrokenLimiter()
    res = client.post("/v1/auth/kakao/token", json={"code": "c", "redirectUri": CALLBACK})
    assert res.status_code == 200


def test_whitelist_covers_every_production_host():
    """카카오 콘솔에 등록하는 Redirect URI 목록과 짝이다. 하나 빠지면 그 도메인만 죽는다."""
    settings = make_settings()
    allowed = kakao.allowed_redirect_uris(settings)
    for host in ("ai.wearless.kr", "facemarket.wearless.kr", "admin.wearless.kr"):
        assert f"https://{host}/auth/kakao/callback" in allowed, host
    assert "http://localhost:5173/auth/kakao/callback" in allowed
    # 127.0.0.1 은 넣지 않는다 — AppProviders 가 루프백을 localhost 로 정규화하므로
    # 그 표기로만 등록해야 카카오의 '정확 일치' 검사를 통과한다.
    assert not any("127.0.0.1" in uri for uri in allowed)
