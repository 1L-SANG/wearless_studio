import asyncio

import pytest

from app import facemarket_notify
from conftest import make_settings


@pytest.mark.parametrize(
    ("identity_method", "method_label", "next_step"),
    [
        ("mid", "모바일 신분증", "사진 18장을 확인해 주세요."),
        ("simple_auth", "간편인증", "신원 확인을 승인한 뒤 사진 18장을 확인해 주세요."),
    ],
)
@pytest.mark.parametrize(
    ("display_name", "safe_name"),
    [("<모델&이름>", "&lt;모델&amp;이름&gt;"), ("", "-"), (None, "-"),
     ("홍", "*"), (" 홍 ", "*")],
)
def test_enrollment_completed_slack_body(
    monkeypatch, identity_method, method_label, next_step, display_name, safe_name
):
    sent = []

    class FakeAsyncClient:
        def __init__(self, *, timeout):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, url, *, json):
            sent.append((url, json))
            return type("Response", (), {"status_code": 200})()

    monkeypatch.setattr(facemarket_notify.httpx, "AsyncClient", FakeAsyncClient)
    settings = make_settings(fm_slack_webhook_url="https://hooks.example/facemarket")
    delivered = asyncio.run(facemarket_notify.notify_slack_enrollment_completed(
        settings, display_name=display_name, identity_method=identity_method,
        admin_link="https://admin.wearless.kr/review",
    ))

    assert delivered is True
    assert sent == [("https://hooks.example/facemarket", {"text": (
        f":camera_with_flash: 2차 등록 완료 · 이름: {safe_name} · 인증: {method_label}\n"
        f"{next_step}\n"
        "<https://admin.wearless.kr/review|관리자 등록 심사 열기>"
    )})]
    assert "—" not in sent[0][1]["text"]


def test_enrollment_completed_slack_skips_delivery_without_webhook(monkeypatch):
    clients = []

    class ForbiddenAsyncClient:
        def __init__(self, **_kwargs):
            clients.append(True)
            raise AssertionError("webhook 설정이 없으면 HTTP 클라이언트를 만들면 안 됩니다")

    monkeypatch.setattr(facemarket_notify.httpx, "AsyncClient", ForbiddenAsyncClient)
    delivered = asyncio.run(facemarket_notify.notify_slack_enrollment_completed(
        make_settings(fm_slack_webhook_url=None), display_name="모델",
        identity_method="mid", admin_link="https://admin.wearless.kr/review",
    ))
    assert delivered is False
    assert clients == []


def test_license_revoked_slack_skips_delivery_without_webhook(monkeypatch):
    class ForbiddenAsyncClient:
        def __init__(self, **_kwargs):
            raise AssertionError("webhook 설정이 없으면 HTTP 클라이언트를 만들면 안 됩니다")

    monkeypatch.setattr(facemarket_notify.httpx, "AsyncClient", ForbiddenAsyncClient)
    settings = make_settings(
        facemarket_enabled=True,
        fm_ci_pepper="pep",
        fm_slack_webhook_url=None,
    )

    delivered = asyncio.run(
        facemarket_notify.notify_slack_license_revoked(
            settings,
            model_id="model-1",
            display_name="테스트 모델",
            revoked_on="2026-09-26",
            purge_due_on="2026-10-26",
            other_active_licenses=0,
            admin_link="https://admin.wearless.kr/models",
        )
    )
    assert delivered is False


@pytest.mark.parametrize(
    ("other_active_licenses", "warning_line"),
    [
        (0, None),
        (2, "주의: 이 모델에게 아직 유효한 라이선스가 2건 있어요. 파기 전에 확인하세요."),
    ],
)
def test_license_revoked_slack_body_and_conditional_warning(
    monkeypatch, other_active_licenses, warning_line
):
    sent = {}

    class FakeResponse:
        status_code = 200

    class FakeAsyncClient:
        def __init__(self, *, timeout):
            sent["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, url, *, json):
            sent.update(url=url, payload=json)
            return FakeResponse()

    monkeypatch.setattr(facemarket_notify.httpx, "AsyncClient", FakeAsyncClient)
    settings = make_settings(
        facemarket_enabled=True,
        fm_ci_pepper="pep",
        fm_slack_webhook_url="https://hooks.example/facemarket",
    )

    delivered = asyncio.run(
        facemarket_notify.notify_slack_license_revoked(
            settings,
            model_id="model-1",
            display_name="<모델&이름>",
            revoked_on="2026-09-26",
            purge_due_on="2026-10-26",
            other_active_licenses=other_active_licenses,
            admin_link="https://admin.wearless.kr/models",
        )
    )
    assert delivered is True

    lines = [
        ":warning: 모델 라이선스 해지 · 모델: &lt;모델&amp;이름&gt; · ID model-1",
        "해지일 2026-09-26 · 파기 기한 2026-10-26(30일)",
        (
            "원본 사진·특징정보·얼굴 참조 자산(학습 가중치·GPU 서버 사본·개발자 PC 학습 "
            "사본 포함)·테스트컷을 지우고, 백업은 90일 안에 지운 뒤 모델에게 알려야 해요."
        ),
    ]
    if warning_line:
        lines.append(warning_line)
    lines.append("<https://admin.wearless.kr/models|관리자 모델 콘솔 열기>")

    assert sent["url"] == "https://hooks.example/facemarket"
    assert sent["payload"] == {"text": "\n".join(lines)}


@pytest.mark.parametrize("status_code", [302, 503])
def test_license_revoked_slack_rejection_is_reported_for_retry(monkeypatch, status_code):
    class FakeResponse:
        pass

    FakeResponse.status_code = status_code

    class FakeAsyncClient:
        def __init__(self, *, timeout):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, url, *, json):
            return FakeResponse()

    monkeypatch.setattr(facemarket_notify.httpx, "AsyncClient", FakeAsyncClient)
    settings = make_settings(fm_slack_webhook_url="https://hooks.example/facemarket")
    delivered = asyncio.run(
        facemarket_notify.notify_slack_license_revoked(
            settings, model_id="model-1", display_name="홍*동",
            revoked_on="2026-09-25", purge_due_on="2026-10-25",
            other_active_licenses=0, admin_link="https://admin.wearless.kr/models",
        )
    )
    assert delivered is False
