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
