import asyncio
from types import SimpleNamespace

import pytest

from app.agents import gemini_image


def settings():
    return SimpleNamespace(
        gemini_api_key="test-key",
        vertex_project=None,
        vertex_location="global",
    )


def test_body_interleaves_role_labels_and_sets_system_instruction():
    client = gemini_image.GeminiImageClient(settings())
    images = [
        gemini_image.InlineImage("image/png", b"first"),
        gemini_image.InlineImage("image/png", b"second"),
    ]
    body = client._body(
        "case prompt",
        images,
        "1K",
        0.2,
        "3:4",
        system_instruction="system contract",
        image_labels=["FACE AUTHORITY", "ART-DIRECTION AUTHORITY"],
        final_instruction="generate now",
    )

    parts = body["contents"][0]["parts"]
    assert body["systemInstruction"] == {"parts": [{"text": "system contract"}]}
    assert body["generationConfig"]["temperature"] == 0.2
    assert [parts[index]["text"] for index in (0, 1, 3, 5)] == [
        "case prompt",
        "FACE AUTHORITY",
        "ART-DIRECTION AUTHORITY",
        "generate now",
    ]
    assert all("inline_data" in parts[index] for index in (2, 4))


def test_body_rejects_image_label_count_mismatch():
    client = gemini_image.GeminiImageClient(settings())
    with pytest.raises(ValueError, match="one label per image"):
        client._body(
            "prompt",
            [gemini_image.InlineImage("image/png", b"image")],
            "1K",
            0.2,
            image_labels=[],
        )


def test_http_429_is_structured_as_non_retryable_quota_signal(monkeypatch):
    class Response:
        status_code = 429
        text = '{"error":{"status":"RESOURCE_EXHAUSTED","message":"daily quota exceeded"}}'

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr(gemini_image.httpx, "AsyncClient", Client)
    client = gemini_image.GeminiImageClient(settings())
    with pytest.raises(gemini_image.GeminiError) as raised:
        asyncio.run(
            client.generate_content_image(
                "gemini-3-pro-image",
                "prompt",
                [gemini_image.InlineImage("image/png", b"image")],
                "1K",
            )
        )

    assert raised.value.status_code == 429
    assert raised.value.quota_exhausted is True
    assert raised.value.retryable is False
