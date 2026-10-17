"""fal 동기 HTTP 경계. 입력 정규화, 제한된 재시도, 기존 실비 원장을 담당한다."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import time
from dataclasses import dataclass

import httpx
from PIL import Image, ImageOps

from app import image_usage
from app.services.sam_client import SamUnavailable

log = logging.getLogger("wearless.fal")
SAM_MODEL = "fal-ai/sam-3/image"
QWEN_MODEL = "fal-ai/qwen-image-edit-2511"
BIREFNET_MODEL = "fal-ai/birefnet/v2"
TIMEOUT_S = 60.0
MAX_PX = 2048


@dataclass(frozen=True)
class PreparedImage:
    image: Image.Image
    url: str


def image_input_url(encoded: bytes, mime: str) -> str:
    """data URI 대체 경로를 넣을 유일한 경계. Claude가 실연동 확인.

    data URI 거부 시 검증된 업로드 URL 방식으로 이 함수만 교체한다.
    실연동 확인 전에는 원본을 다른 호스트로 업로드하는 폴백을 실행하지 않는다.
    """
    return f"data:{mime};base64,{base64.b64encode(encoded).decode('ascii')}"


def prepare_image(data: bytes) -> PreparedImage:
    with Image.open(io.BytesIO(data)) as opened:
        upright = ImageOps.exif_transpose(opened)
        has_alpha = "A" in upright.getbands() or "transparency" in upright.info
        image = upright.convert("RGBA" if has_alpha else "RGB")
        image.thumbnail((MAX_PX, MAX_PX), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    if has_alpha:
        image.save(out, "PNG")
        mime = "image/png"
    else:
        image.save(out, "JPEG", quality=92)
        mime = "image/jpeg"
    return PreparedImage(image, image_input_url(out.getvalue(), mime))


def configured(settings) -> bool:
    return bool((getattr(settings, "fal_key", None) or "").strip())


async def run_model(settings, model: str, payload: dict) -> dict:
    if not configured(settings):
        raise SamUnavailable("fal is not configured (FAL_KEY)")
    # 두 모델의 결과를 fal CDN에 저장하지 않고 data URI로 직접 받는다.
    payload = {**payload, "sync_mode": True}
    size = payload.get("image_size") or {}
    width, height = size.get("width", 0), size.get("height", 0)
    megapixels = width * height / 1_000_000
    estimate = (0.005 if model == SAM_MODEL else 0.0006 if model == BIREFNET_MODEL
                else megapixels * 0.03)
    headers = {"Authorization": f"Key {settings.fal_key.strip()}"}
    async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
        for attempt in range(2):
            started = time.monotonic()
            status = None
            try:
                response = await client.post(f"https://fal.run/{model}",
                                             headers=headers, json=payload)
                status = response.status_code
            except httpx.HTTPError:
                # 예외 문자열에는 요청 URL·본문·키가 섞일 수 있으므로 전달하지 않는다.
                raise SamUnavailable("fal request unavailable") from None
            finally:
                latency_ms = int((time.monotonic() - started) * 1000)
                log.info(json.dumps({"provider": "fal", "model": model,
                    "latency_ms": latency_ms, "estimated_cost_usd": estimate,
                    "status": status, "attempt": attempt + 1}))
            if (status == 429 or 500 <= status < 600) and attempt == 0:
                await asyncio.sleep(1.5)
                continue
            if not 200 <= status < 300:
                raise SamUnavailable(f"fal responded {status}")
            # POST 성공 시 기록한다. 후속 다운로드·마스크 판정 실패도 실비를 숨기지 않는다.
            image_usage.record(model=model, image_size=f"{width}x{height}",
                usage={"provider": "fal", "megapixels": megapixels},
                latency_ms=latency_ms)
            try:
                body = response.json()
            except ValueError:
                raise SamUnavailable("fal returned non-JSON response") from None
            if not isinstance(body, dict):
                raise SamUnavailable("fal returned invalid response")
            return body
    raise SamUnavailable("fal request unavailable")  # pragma: no cover


async def fetch_image(url: str) -> bytes:
    if url.startswith("data:"):
        try:
            header, encoded = url.split(",", 1)
            if not header.endswith(";base64") or not encoded:
                raise ValueError
            return base64.b64decode(encoded, validate=True)
        except ValueError:
            raise SamUnavailable("fal returned invalid image data URI") from None
    # 출력 호스트에 fal 키를 전달하지 않는다. URL도 로그에 남기지 않는다.
    async with httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=True) as client:
        for attempt in range(2):
            try:
                response = await client.get(url)
            except httpx.HTTPError:
                raise SamUnavailable("fal image download unavailable") from None
            status = response.status_code
            if (status == 429 or 500 <= status < 600) and attempt == 0:
                await asyncio.sleep(1.5)
                continue
            if not 200 <= status < 300:
                raise SamUnavailable(f"fal image download responded {status}")
            return response.content
    raise SamUnavailable("fal image download unavailable")  # pragma: no cover
