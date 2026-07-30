"""서버사이드 Gemini 이미지 클라이언트 (spike/spike.js callGemini 이식).

[프롬프트, base 이미지, 상품 이미지...] → generateContent → 가장 큰 image part 채택.
인증: VERTEX_PROJECT 있으면 Vertex aiplatform, 없으면 AI Studio generativelanguage.
async httpx로 호출해 이벤트 루프를 막지 않는다 (§5).
"""

import base64
import time
from dataclasses import dataclass

import httpx

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
    """A provider error with enough structure for callers to retry safely."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
        quota_exhausted: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable
        self.quota_exhausted = quota_exhausted


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

    def _body(
        self,
        prompt: str,
        images: list[InlineImage],
        image_size: str,
        temperature: float | None,
        aspect_ratio: str | None = None,
        *,
        system_instruction: str | None = None,
        image_labels: list[str] | None = None,
        final_instruction: str | None = None,
    ) -> dict:
        if image_labels is not None and len(image_labels) != len(images):
            raise ValueError("image_labels must have exactly one label per image")
        image_cfg: dict = {"imageSize": image_size}
        if aspect_ratio:
            image_cfg["aspectRatio"] = aspect_ratio
        gen: dict = {
            "responseModalities": ["TEXT", "IMAGE"],
            "imageConfig": image_cfg,
        }
        if temperature is not None:
            gen["temperature"] = temperature
        parts: list[dict] = [{"text": prompt}]
        for index, image in enumerate(images):
            if image_labels is not None:
                label = image_labels[index].strip()
                if not label:
                    raise ValueError("image labels must not be blank")
                parts.append({"text": label})
            parts.append(
                {
                    "inline_data": {
                        "mime_type": image.mime,
                        "data": base64.b64encode(image.data).decode(),
                    }
                }
            )
        if final_instruction:
            parts.append({"text": final_instruction})
        body = {
            "contents": [
                {
                    "role": "user",
                    "parts": parts,
                }
            ],
            "generationConfig": gen,
        }
        if system_instruction:
            body["systemInstruction"] = {"parts": [{"text": system_instruction}]}
        return body

    async def generate_content_image(
        self,
        model: str,
        prompt: str,
        images: list[InlineImage],
        image_size: str,
        temperature: float | None = None,
        aspect_ratio: str | None = None,
        timeout: float = 180.0,
        *,
        system_instruction: str | None = None,
        image_labels: list[str] | None = None,
        final_instruction: str | None = None,
    ) -> GeminiImageResult:
        if not self._key:
            raise GeminiError("GEMINI_API_KEY 미설정")
        body = self._body(
            prompt,
            images,
            image_size,
            temperature,
            aspect_ratio,
            system_instruction=system_instruction,
            image_labels=image_labels,
            final_instruction=final_instruction,
        )
        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                res = await client.post(
                    self._endpoint(model), json=body, headers={"x-goog-api-key": self._key}
                )
        except httpx.RequestError as exc:
            raise GeminiError(
                f"Gemini request failed: {type(exc).__name__}: {exc}",
                retryable=True,
            ) from exc
        latency_ms = int((time.perf_counter() - t0) * 1000)
        if res.status_code != 200:
            error_text = res.text[:500]
            normalized = error_text.lower()
            quota_exhausted = res.status_code == 429 or any(
                marker in normalized
                for marker in (
                    "resource_exhausted",
                    "quota exceeded",
                    "generate_requests_per_model_per_day",
                )
            )
            raise GeminiError(
                f"Gemini {res.status_code}: {error_text}",
                status_code=res.status_code,
                retryable=res.status_code in {408, 500, 502, 503, 504},
                quota_exhausted=quota_exhausted,
            )
        data = res.json()
        parts = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts")) or []
        image_parts = [p for p in parts if (p.get("inlineData") or {}).get("data")]
        if not image_parts:
            text = " ".join(p.get("text", "") for p in parts).strip()[:300]
            raise GeminiError(
                f"응답에 이미지 없음. 텍스트: {text or '(없음)'}",
                retryable=True,
            )
        # 가장 큰 image part 채택 (4K 응답은 프리뷰+본체 2개일 수 있음 — spike 노트)
        best = max(image_parts, key=lambda p: len(p["inlineData"]["data"]))
        return GeminiImageResult(
            image=base64.b64decode(best["inlineData"]["data"]),
            mime=best["inlineData"].get("mimeType") or "image/png",
            latency_ms=latency_ms,
            usage=data.get("usageMetadata"),
        )
