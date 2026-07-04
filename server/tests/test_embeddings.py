"""embeddings 서비스 단위테스트 — 실제 네트워크 없이 httpx.MockTransport로 격리.

text: OpenAI 응답 모양 캔, 순서 보존·차원 검증·Authorization 헤더 확인.
image: Vertex 인증(access_token) 부재/vertex_project 부재 가드, 응답 파싱 확인.
suite에 pytest-asyncio가 없으므로(conftest 확인) asyncio.run()으로 코루틴 실행.
"""

import asyncio
import json

import httpx
import pytest

from app.config import Settings
from app.services.embeddings import EmbeddingError, embed_image, embed_text


def make_settings(**overrides) -> Settings:
    base = dict(
        app_env="prod",
        supabase_url="https://example.supabase.co",
        jwks_url="https://example.supabase.co/auth/v1/.well-known/jwks.json",
        jwt_audience="authenticated",
        cors_origins=["http://localhost:5173"],
        database_url=None,
        r2_account_id=None,
        r2_access_key_id=None,
        r2_secret_access_key=None,
        r2_bucket=None,
        r2_endpoint=None,
        r2_public_base=None,
        openai_api_key="sk-test-key",
        embed_text_model="text-embedding-3-small",
        embed_image_model="multimodalembedding@001",
        vertex_project="proj-123",
        vertex_location="us-central1",
    )
    base.update(overrides)
    return Settings(**base)


# ---------- embed_text ----------


def test_embed_text_returns_vectors_in_order_and_sends_auth_header(monkeypatch):
    settings = make_settings()
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.4, 0.5, 0.6]},
                    {"index": 0, "embedding": [0.1, 0.2, 0.3]},
                ],
                "model": "text-embedding-3-small",
            },
        )

    transport = httpx.MockTransport(handler)
    orig_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["transport"] = transport
        orig_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    vectors = asyncio.run(embed_text(settings, ["hello", "world"]))

    assert vectors == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]  # 입력 순서 보존
    assert captured["headers"]["authorization"] == "Bearer sk-test-key"
    assert captured["body"] == {
        "model": "text-embedding-3-small",
        "input": ["hello", "world"],
    }


def test_embed_text_raises_on_dimension_mismatch(monkeypatch):
    settings = make_settings()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 0, "embedding": [0.1, 0.2, 0.3]},
                    {"index": 1, "embedding": [0.4, 0.5]},
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    orig_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["transport"] = transport
        orig_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    with pytest.raises(EmbeddingError):
        asyncio.run(embed_text(settings, ["a", "b"]))


def test_embed_text_raises_without_api_key():
    settings = make_settings(openai_api_key=None)
    with pytest.raises(EmbeddingError, match="OPENAI_API_KEY"):
        asyncio.run(embed_text(settings, ["hello"]))


# ---------- embed_image ----------


def test_embed_image_raises_without_access_token():
    settings = make_settings()
    with pytest.raises(EmbeddingError, match="Vertex 인증"):
        asyncio.run(embed_image(settings, b"fake-image-bytes"))


def test_embed_image_raises_without_vertex_project():
    settings = make_settings(vertex_project=None)
    with pytest.raises(EmbeddingError, match="VERTEX_PROJECT"):
        asyncio.run(embed_image(settings, b"fake-image-bytes", access_token="token-abc"))


def test_embed_image_parses_response_with_access_token(monkeypatch):
    settings = make_settings()
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"predictions": [{"imageEmbedding": [0.7, 0.8, 0.9, 1.0]}]},
        )

    transport = httpx.MockTransport(handler)
    orig_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["transport"] = transport
        orig_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    vector = asyncio.run(embed_image(settings, b"fake-image-bytes", access_token="token-abc"))

    assert vector == [0.7, 0.8, 0.9, 1.0]
    assert captured["headers"]["authorization"] == "Bearer token-abc"
    assert "proj-123" in captured["url"]
    assert "multimodalembedding@001" in captured["url"]
    assert "bytesBase64Encoded" in captured["body"]["instances"][0]["image"]
