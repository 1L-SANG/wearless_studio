"""텍스트/이미지 임베딩 — retrieval_upgrade_prd 벡터 검색 기반.

text: OpenAI embeddings API (settings.embed_text_model, 예: text-embedding-3-small).
image: Google Vertex 멀티모달 임베딩 (settings.embed_image_model, 예: multimodalembedding@001).
async httpx로 호출해 이벤트 루프를 막지 않는다 (§5, gemini_image.py와 동일 패턴).

인젝션 안전: 여기서 다루는 texts는 검색 쿼리 원문 그대로 임베딩될 뿐이며,
어떤 경우에도 모델 instruction으로 실행되지 않는다 — 이 모듈은 벡터만 계산한다.
"""

import base64
import logging
import time

import httpx

from ..config import Settings

logger = logging.getLogger(__name__)


class EmbeddingError(RuntimeError):
    pass


async def embed_text(settings: Settings, texts: list[str]) -> list[list[float]]:
    """OpenAI embeddings API로 texts를 벡터로 변환. 입력 순서를 보존해 반환."""
    if not settings.openai_api_key:
        raise EmbeddingError("OPENAI_API_KEY 미설정")

    model = settings.embed_text_model
    t0 = time.monotonic()
    async with httpx.AsyncClient(timeout=60.0) as client:
        res = await client.post(
            "https://api.openai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            json={"model": model, "input": texts},
        )
    latency_ms = int((time.monotonic() - t0) * 1000)
    if res.status_code != 200:
        raise EmbeddingError(f"OpenAI embeddings {res.status_code}: {res.text[:500]}")

    data = res.json()
    items = data.get("data") or []
    if len(items) != len(texts):
        raise EmbeddingError(
            f"응답 개수 불일치: 요청 {len(texts)} vs 응답 {len(items)}"
        )
    # index로 정렬해 입력 순서를 보존 (API가 순서를 보장해도 방어적으로)
    items = sorted(items, key=lambda it: it.get("index", 0))
    vectors = [it["embedding"] for it in items]

    dims = {len(v) for v in vectors}
    if len(dims) > 1:
        raise EmbeddingError(f"임베딩 차원 불일치: {sorted(dims)}")
    dim = dims.pop() if dims else 0

    logger.info(
        "embedding_call",
        extra={
            "kind": "text",
            "model": model,
            "dim": dim,
            "count": len(vectors),
            "latency_ms": latency_ms,
        },
    )
    return vectors


async def embed_image(
    settings: Settings,
    image: bytes,
    *,
    access_token: str | None = None,
) -> list[float]:
    """Google Vertex 멀티모달 임베딩으로 image를 벡터로 변환.

    Vertex는 GCP 액세스 토큰(서비스계정/ADC)이 필요하며 AI Studio 키(gemini_api_key)로는
    호출할 수 없다. google-auth 의존성을 추가하지 않기 위해 토큰은 주입식으로 받는다 —
    access_token 인자로 넘기거나, 없으면 에러. 실제 서비스계정/ADC 연동은 후속 ops 작업.
    """
    if not settings.vertex_project:
        raise EmbeddingError("VERTEX_PROJECT 미설정")
    if not access_token:
        raise EmbeddingError("Vertex 인증 미설정 — GCP 서비스계정/ADC 필요")

    model = settings.embed_image_model
    location = settings.vertex_location
    host = "aiplatform.googleapis.com" if location == "global" else f"{location}-aiplatform.googleapis.com"
    url = (
        f"https://{host}/v1/projects/{settings.vertex_project}/locations/{location}"
        f"/publishers/google/models/{model}:predict"
    )
    body = {
        "instances": [
            {"image": {"bytesBase64Encoded": base64.b64encode(image).decode()}}
        ]
    }

    t0 = time.monotonic()
    async with httpx.AsyncClient(timeout=60.0) as client:
        res = await client.post(
            url, json=body, headers={"Authorization": f"Bearer {access_token}"}
        )
    latency_ms = int((time.monotonic() - t0) * 1000)
    if res.status_code != 200:
        raise EmbeddingError(f"Vertex embeddings {res.status_code}: {res.text[:500]}")

    data = res.json()
    predictions = data.get("predictions") or []
    if not predictions:
        raise EmbeddingError("Vertex 응답에 predictions 없음")
    vector = predictions[0].get("imageEmbedding")
    if not vector:
        raise EmbeddingError("Vertex 응답에 imageEmbedding 없음")

    logger.info(
        "embedding_call",
        extra={
            "kind": "image",
            "model": model,
            "dim": len(vector),
            "count": 1,
            "latency_ms": latency_ms,
        },
    )
    return vector
