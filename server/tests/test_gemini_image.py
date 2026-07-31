import asyncio
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
