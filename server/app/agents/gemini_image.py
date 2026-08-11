"""서버사이드 Gemini 이미지 클라이언트 (spike/spike.js callGemini 이식).

[프롬프트, base 이미지, 상품 이미지...] → generateContent → 가장 큰 image part 채택.
인증: VERTEX_PROJECT 있으면 Vertex aiplatform, 없으면 AI Studio generativelanguage.
async httpx로 호출해 이벤트 루프를 막지 않는다 (§5).
"""

import asyncio
import base64
import time
from dataclasses import dataclass

import httpx

from .. import image_usage
from ..config import Settings


@dataclass(frozen=True)
class InlineImage:
    mime: str
    data: bytes  # 원본 바이트 (base64 인코딩은 여기서)


@dataclass(frozen=True)
class GeminiImageResult:
    image: bytes
    mime: str
    latency_ms: int
    usage: dict | None


class GeminiError(RuntimeError):
    pass


class GeminiImageClient:
    """앱 1개당 1개. app.state.gemini 에 둔다. settings.gemini_api_key 없으면 생성 안 함."""

    def __init__(self, settings: Settings):
        self._key = settings.gemini_api_key
        self._vertex_project = settings.vertex_project
        self._vertex_location = settings.vertex_location

    def _endpoint(self, model: str) -> str:
        # 키는 URL이 아니라 x-goog-api-key 헤더로 — 로그/에러 유출 방지
        if self._vertex_project:
            loc = self._vertex_location
            host = "aiplatform.googleapis.com" if loc == "global" else f"{loc}-aiplatform.googleapis.com"
            return (
                f"https://{host}/v1/projects/{self._vertex_project}/locations/{loc}"
                f"/publishers/google/models/{model}:generateContent"
            )
        return f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def _body(self, prompt: str, images: list[InlineImage], image_size: str,
              temperature: float | None, aspect_ratio: str | None = None) -> dict:
        image_cfg: dict = {"imageSize": image_size}
        if aspect_ratio:
            image_cfg["aspectRatio"] = aspect_ratio
        gen: dict = {
            "responseModalities": ["TEXT", "IMAGE"],
            "imageConfig": image_cfg,
        }
        if temperature is not None:
            gen["temperature"] = temperature
        return {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": prompt},
                        *[
                            {"inline_data": {"mime_type": im.mime, "data": base64.b64encode(im.data).decode()}}
                            for im in images
                        ],
                    ],
                }
            ],
            "generationConfig": gen,
        }

    async def generate_content_image(
        self,
        model: str,
        prompt: str,
        images: list[InlineImage],
        image_size: str,
        temperature: float | None = None,
        aspect_ratio: str | None = None,
        timeout: float = 180.0,
    ) -> GeminiImageResult:
        if not self._key:
            raise GeminiError("GEMINI_API_KEY 미설정")
        body = self._body(prompt, images, image_size, temperature, aspect_ratio)
        t0 = time.perf_counter()
        # 429(레이트리밋) 백오프 재시도 — 전부-병렬 제출(detail_cut_concurrency=0)의 안전망.
        # 재시도 없이는 스로틀된 컷이 곧장 실패(빈 슬롯·미차감)로 떨어진다. 다른 상태코드는
        # 기존대로 즉시 실패(파라미터 오류 등은 재시도해도 같다).
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    res = await client.post(
                        self._endpoint(model), json=body, headers={"x-goog-api-key": self._key}
                    )
            except httpx.RequestError as exc:
                raise GeminiError(
                    f"Gemini request failed: {type(exc).__name__}: {exc}"
                ) from exc
            if res.status_code != 429 or attempt == 2:
                break
            await asyncio.sleep(5 * (attempt + 1))  # 5s → 10s
        latency_ms = int((time.perf_counter() - t0) * 1000)
        if res.status_code != 200:
            raise GeminiError(f"Gemini {res.status_code}: {res.text[:500]}")
        parse_error = None
        usage = None
        parts = []
        image_parts = []
        try:
            data = res.json()
            if not isinstance(data, dict):
                raise ValueError("response root is not an object")
            usage = data.get("usageMetadata")
            candidates = data.get("candidates") or []
            if not isinstance(candidates, list):
                raise ValueError("candidates is not a list")
            first = candidates[0] if candidates else {}
            if not isinstance(first, dict):
                raise ValueError("candidate is not an object")
            content = first.get("content") or {}
            if not isinstance(content, dict):
                raise ValueError("candidate content is not an object")
            raw_parts = content.get("parts") or []
            if not isinstance(raw_parts, list):
                raise ValueError("candidate parts is not a list")
            parts = [part for part in raw_parts if isinstance(part, dict)]
            image_parts = [
                part for part in parts
                if isinstance(part.get("inlineData"), dict)
                and part["inlineData"].get("data")
            ]
        except (TypeError, ValueError) as exc:
            parse_error = exc
        # 200 이면 이미지가 없어도 요금은 나간다 — 채택 여부·QC 결과와 무관하게 여기서 기록한다.
        image_usage.record(
            model=model, image_size=image_size, usage=usage,
            latency_ms=latency_ms, has_image=bool(image_parts),
        )
        if parse_error is not None:
            raise GeminiError(
                f"Gemini 200 응답 형식 오류: {type(parse_error).__name__}: {parse_error}"
            ) from parse_error
        if not image_parts:
            text = " ".join(str(p.get("text") or "") for p in parts).strip()[:300]
            raise GeminiError(f"응답에 이미지 없음. 텍스트: {text or '(없음)'}")
        # 가장 큰 image part 채택 (4K 응답은 프리뷰+본체 2개일 수 있음 — spike 노트)
        best = max(image_parts, key=lambda p: len(p["inlineData"]["data"]))
        return GeminiImageResult(
            image=base64.b64decode(best["inlineData"]["data"]),
            mime=best["inlineData"].get("mimeType") or "image/png",
            latency_ms=latency_ms,
            usage=usage,
        )
