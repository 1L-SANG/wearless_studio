import asyncio

import pytest

from app import facemarket_notify
from conftest import make_settings


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

    asyncio.run(
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

    asyncio.run(
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
