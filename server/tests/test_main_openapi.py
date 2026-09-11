"""API 스키마 노출과 관리자 기기 헤더의 CORS 허용 — 앱 팩토리 계약.

되돌아가면: prod 의 /openapi.json 이 209KB 전체 라우트·모델을 아무에게나 준다(2026-09-11
실측). CORS 에 헤더가 빠지면 admin 콘솔의 모든 요청이 preflight 에서 죽는데, 로그인까지는
되니 "서버에 연결하지 못했어요" 로만 보인다(2026-09-04 CORS_ORIGINS 사고와 같은 모양).
"""
from fastapi.testclient import TestClient

from app.main import create_app
from conftest import make_settings


def test_openapi_and_docs_are_closed_outside_dev():
    client = TestClient(create_app(make_settings(app_env="prod")))
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404


def test_openapi_and_docs_stay_open_in_dev():
    client = TestClient(create_app(make_settings(app_env="dev")))
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/docs").status_code == 200


def test_cors_preflight_allows_the_admin_device_header():
    client = TestClient(create_app(make_settings(cors_origins=["https://admin.wearless.kr"])))
    res = client.options(
        "/v1/facemarket/admin/overview",
        headers={
            "Origin": "https://admin.wearless.kr",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization,x-admin-device",
        },
    )
    assert res.status_code == 200
    allowed = res.headers.get("access-control-allow-headers", "").lower()
    assert "x-admin-device" in allowed
