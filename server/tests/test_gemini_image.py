import asyncio
import base64
from types import SimpleNamespace

import httpx
import pytest

from app.agents import gemini_image


def settings():
    return SimpleNamespace(
        gemini_api_key="test-key",
        vertex_project=None,
        vertex_location="global",
    )


def test_transport_error_is_wrapped_as_gemini_error(monkeypatch):
    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            raise httpx.ConnectError("offline")

    monkeypatch.setattr(gemini_image.httpx, "AsyncClient", Client)
    client = gemini_image.GeminiImageClient(settings())
    with pytest.raises(gemini_image.GeminiError, match="ConnectError") as raised:
        asyncio.run(
            client.generate_content_image(
                "gemini-3-pro-image",
                "prompt",
                [gemini_image.InlineImage("image/png", b"image")],
                "1K",
            )
        )

    assert isinstance(raised.value.__cause__, httpx.ConnectError)


def _wire_response(monkeypatch, response):
    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            return response

    monkeypatch.setattr(gemini_image.httpx, "AsyncClient", Client)


def test_malformed_200_is_recorded_once_before_raising(monkeypatch):
    class Response:
        status_code = 200
        text = "not-json"

        def json(self):
            raise ValueError("bad json")

    recorded = []
    _wire_response(monkeypatch, Response())
    monkeypatch.setattr(gemini_image.image_usage, "record", lambda **kw: recorded.append(kw))

    with pytest.raises(gemini_image.GeminiError, match="응답 형식 오류"):
        asyncio.run(gemini_image.GeminiImageClient(settings()).generate_content_image(
            "gemini-3-pro-image", "prompt", [], "4K"))

    assert len(recorded) == 1
    assert recorded[0]["usage"] is None
    assert recorded[0]["has_image"] is False


def test_unexpected_candidates_shape_keeps_available_usage(monkeypatch):
    usage = {"promptTokenCount": 7}
    response = SimpleNamespace(
        status_code=200,
        text="ok",
        json=lambda: {"usageMetadata": usage, "candidates": {"bad": "shape"}},
    )
    recorded = []
    _wire_response(monkeypatch, response)
    monkeypatch.setattr(gemini_image.image_usage, "record", lambda **kw: recorded.append(kw))

    with pytest.raises(gemini_image.GeminiError, match="candidates is not a list"):
        asyncio.run(gemini_image.GeminiImageClient(settings()).generate_content_image(
            "gemini-3-pro-image", "prompt", [], "1K"))

    assert len(recorded) == 1
    assert recorded[0]["usage"] == usage and recorded[0]["has_image"] is False


def test_text_only_200_records_usage_without_claiming_an_image(monkeypatch):
    usage = {"promptTokenCount": 10, "candidatesTokenCount": 20}
    response = SimpleNamespace(
        status_code=200,
        text="ok",
        json=lambda: {"usageMetadata": usage, "candidates": [
            {"content": {"parts": [{"text": "no image"}]}}
        ]},
    )
    recorded = []
    _wire_response(monkeypatch, response)
    monkeypatch.setattr(gemini_image.image_usage, "record", lambda **kw: recorded.append(kw))

    with pytest.raises(gemini_image.GeminiError, match="이미지 없음"):
        asyncio.run(gemini_image.GeminiImageClient(settings()).generate_content_image(
            "gemini-3-pro-image", "prompt", [], "1K"))

    assert len(recorded) == 1
    assert recorded[0]["usage"] == usage and recorded[0]["has_image"] is False


def test_image_200_records_exactly_once_and_returns_image(monkeypatch):
    payload = base64.b64encode(b"image-bytes").decode()
    response = SimpleNamespace(
        status_code=200,
        text="ok",
        json=lambda: {"usageMetadata": {"promptTokenCount": 1}, "candidates": [
            {"content": {"parts": [{"inlineData": {
                "data": payload, "mimeType": "image/png"
            }}]}}
        ]},
    )
    recorded = []
    _wire_response(monkeypatch, response)
    monkeypatch.setattr(gemini_image.image_usage, "record", lambda **kw: recorded.append(kw))

    result = asyncio.run(gemini_image.GeminiImageClient(settings()).generate_content_image(
        "gemini-3-pro-image", "prompt", [], "1K"))

    assert result.image == b"image-bytes"
    assert len(recorded) == 1 and recorded[0]["has_image"] is True


def _counting_client(monkeypatch, raise_exc, sleeps):
    calls = {"n": 0}

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            calls["n"] += 1
            raise raise_exc

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(gemini_image.httpx, "AsyncClient", Client)
    monkeypatch.setattr(gemini_image.asyncio, "sleep", fake_sleep)
    return calls


def test_read_timeout_is_not_retried_so_a_charged_image_is_not_paid_for_twice(monkeypatch):
    """읽기 타임아웃 = 요청은 이미 도착했고 답만 늦는 상태.

    다시 보내면 프로바이더가 이미 만든(=과금된) 이미지를 한 장 더 만들고, 그 추가 호출은
    비용 원장(image_usage_events)에도 안 남는다. 그래서 재시도하지 않는다(2026-08-16 리뷰).
    """
    sleeps: list[float] = []
    calls = _counting_client(monkeypatch, httpx.ReadTimeout("slow"), sleeps)
    client = gemini_image.GeminiImageClient(settings())
    with pytest.raises(gemini_image.GeminiError, match="ReadTimeout"):
        asyncio.run(
            client.generate_content_image(
                "gemini-3-pro-image",
                "prompt",
                [gemini_image.InlineImage("image/png", b"image")],
                "1K",
            )
        )

    assert calls["n"] == 1, "한 번만 보낸다 — 재시도하면 같은 컷을 두 번 과금한다"
    assert sleeps == []


def test_connect_error_is_retried_because_the_provider_never_received_the_request(monkeypatch):
    """연결 자체가 안 선 경우는 프로바이더가 요청을 받지도 못했다 — 재시도해도 이중 과금이 없다."""
    sleeps: list[float] = []
    calls = _counting_client(monkeypatch, httpx.ConnectError("offline"), sleeps)
    client = gemini_image.GeminiImageClient(settings())
    with pytest.raises(gemini_image.GeminiError, match="ConnectError"):
        asyncio.run(
            client.generate_content_image(
                "gemini-3-pro-image",
                "prompt",
                [gemini_image.InlineImage("image/png", b"image")],
                "1K",
            )
        )

    assert calls["n"] == 3, "3회까지 시도"
    assert sleeps == [5, 10], "5초 → 10초 백오프"
