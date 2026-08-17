"""서버사이드 Gemini 이미지 클라이언트 (spike/spike.js callGemini 이식).

[프롬프트, base 이미지, 상품 이미지...] → generateContent → 가장 큰 image part 채택.
인증: VERTEX_PROJECT 있으면 Vertex aiplatform, 없으면 AI Studio generativelanguage.
async httpx로 호출해 이벤트 루프를 막지 않는다 (§5).
"""

import asyncio
import base64
import binascii
import logging
import time
from dataclasses import dataclass

import httpx

from .. import image_usage
from ..config import Settings

log = logging.getLogger(__name__)


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


def _record_unbilled_failure(model: str, image_size: str, t0: float, reason: str) -> None:
    """응답을 못 받았지만 프로바이더는 이미 그렸을 수 있는 실패를 원장에 남긴다.

    usage 를 못 받았으므로 토큰 수는 알 수 없다(추정 단가만 남는다). 그래도 남겨야
    "그 시각에 과금됐을 수 있는 호출이 몇 건"인지 셀 수 있다 — 안 남기면 이번 재시도
    차단이 실제로 얼마를 아꼈는지 검증할 방법이 없다(2026-08-17 검증).
    """
    try:
        latency_ms = int((time.perf_counter() - t0) * 1000)
        log.warning("image_usage unbilled_failure model=%s size=%s reason=%s latency_ms=%d",
                    model, image_size, reason, latency_ms)
        image_usage.record(model=model, image_size=image_size, usage=None,
                           latency_ms=latency_ms, has_image=False)
    except Exception:  # 계측 실패가 생성 경로를 깨뜨리지 않게
        log.exception("unbilled failure record failed (ignored)")


class GeminiError(RuntimeError):
    """이미지 호출 실패.

    billable=True 는 "프로바이더가 이미 그림을 만들었을 수 있다"는 뜻이다(읽기 타임아웃,
    게이트웨이 502/504). 이런 실패는 **위층에서도 재시도하면 안 된다** — 같은 컷을 한 장
    더 만들고 요금이 두 번 나가는데 추가 호출은 비용 원장에도 안 남는다(2026-08-17 리뷰).
    """

    def __init__(self, message: str, *, billable: bool = False) -> None:
        super().__init__(message)
        self.billable = billable


def _as_openai_png(image: "InlineImage") -> bytes:
    """OpenAI images/edits 입력용 표준화 — RGB PNG 로 통일.

    Gemini 는 관대하지만 OpenAI 는 MPO(확장자만 .jpeg 인 아이폰 다중사진)·팔레트·CMYK 를
    invalid_image_file 로 거부한다. 실측(2026-08-17): 같은 사진이 Gemini 성공 / OpenAI 400.
    이미 표준 PNG 면 재인코딩하지 않는다.
    """
    if image.mime == "image/png":
        return image.data
    try:
        from io import BytesIO

        from PIL import Image as _PILImage

        with _PILImage.open(BytesIO(image.data)) as im:
            buf = BytesIO()
            im.convert("RGB").save(buf, "PNG")
            return buf.getvalue()
    except Exception:  # noqa: BLE001 — 변환 실패 시 원본을 그대로 보내 기존 동작 유지
        return image.data


class GeminiImageClient:
    """앱 1개당 1개. app.state.gemini 에 둔다. settings.gemini_api_key 없으면 생성 안 함."""

    def __init__(self, settings: Settings):
        self._key = settings.gemini_api_key
        # getattr — 테스트·부분 설정 객체가 openai 키를 안 가질 수 있다. 없으면 gpt-image
        # 모델을 호출할 때에만 GeminiError 로 드러난다(기존 gemini 경로는 영향 0).
        self._openai_key = getattr(settings, "openai_api_key", None)
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
        # 모델 id 로 provider 분기. gpt-image* 는 OpenAI images/edits(멀티 레퍼런스), 그 외는 Gemini.
        # 같은 시그니처·같은 GeminiImageResult 반환이라 9개 콜사이트는 무변경이다.
        if model.startswith("gpt-image"):
            return await self._openai_generate(
                model, prompt, images, image_size, aspect_ratio, timeout)
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
                # 연결 자체가 안 선 경우만 재시도한다 — 그건 프로바이더가 요청을 받지도
                # 못했다는 뜻이라 다시 보내도 이미지가 두 번 만들어지지 않는다.
                # 반대로 ReadTimeout 은 "보냈는데 답을 늦게 받는 중"이라, 재시도하면 이미
                # 만들어져(=과금돼) 있는 이미지를 한 장 더 만든다. 그 추가 호출은 원장
                # (image_usage_events)에도 안 남아 비용이 조용히 샌다 — 2026-08-16 리뷰.
                retryable = isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout))
                if attempt == 2 or not retryable:
                    billable = not retryable
                    # 그림이 이미 나왔을 수 있으면 원장에도 남긴다 — 안 남기면 "재시도를 막아
                    # 얼마를 아꼈는지"도, "지금 얼마가 새는지"도 영영 못 잰다(2026-08-17 검증).
                    if billable:
                        _record_unbilled_failure(model, image_size, t0, f"{type(exc).__name__}")
                    raise GeminiError(
                        f"Gemini request failed: {type(exc).__name__}: {exc}",
                        billable=billable,
                    ) from exc
                await asyncio.sleep(5 * (attempt + 1))
                continue
            # 429(스로틀)와 500/503(백엔드가 요청을 거절)만 재시도한다. 502/504 는 게이트웨이가
            # 응답을 못 받은 것이라 모델이 이미 그려(=과금돼) 있을 수 있어 다시 보내지 않는다
            # — 읽기 타임아웃과 같은 사정이다(2026-08-17 리뷰).
            if res.status_code not in (429, 500, 503) or attempt == 2:
                break
            await asyncio.sleep(5 * (attempt + 1))  # 5s → 10s
        latency_ms = int((time.perf_counter() - t0) * 1000)
        if res.status_code != 200:
            billable = res.status_code in (502, 504)
            if billable:
                _record_unbilled_failure(model, image_size, t0, f"http_{res.status_code}")
            raise GeminiError(f"Gemini {res.status_code}: {res.text[:500]}", billable=billable)
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

    #: 기본(1K) 캔버스는 기존 배선을 보존한다. GPT Image 2의 임의 유효 해상도 지원을
    #: 이용하는 4K 2:3만 별도로 승급한다. 2336×3504는 정확히 2:3이고, 양 변이 16의
    #: 배수이며, 8,185,344 픽셀로 API의 8,294,400 픽셀 상한 안에 드는 최대 크기다.
    _OPENAI_SIZE = {"2:3": "1024x1536", "9:16": "1024x1536", "3:4": "1024x1536",
                    "3:2": "1536x1024", "16:9": "1536x1024", "4:3": "1536x1024",
                    "1:1": "1024x1024"}
    _OPENAI_4K_SIZE = {"2:3": "2336x3504", "3:2": "3504x2336"}

    @classmethod
    def _openai_size(cls, image_size: str, aspect_ratio: str | None) -> str:
        ratio = aspect_ratio or ""
        if image_size.upper() == "4K" and ratio in cls._OPENAI_4K_SIZE:
            return cls._OPENAI_4K_SIZE[ratio]
        return cls._OPENAI_SIZE.get(ratio, "1024x1536")

    async def _openai_generate(
        self, model: str, prompt: str, images: list[InlineImage],
        image_size: str, aspect_ratio: str | None, timeout: float,
    ) -> GeminiImageResult:
        """OpenAI images/edits — 멀티 레퍼런스 편집 생성. Gemini 와 동일 반환 계약.

        Gemini generateContent 와 의미가 다르다(캔버스 편집 vs 조건부 생성) — 동등 품질 보장 아님.
        키·엔드포인트·multipart·응답(b64_json) 전부 Gemini 와 다르므로 별도 경로다.
        """
        if not self._openai_key:
            raise GeminiError("OPENAI_API_KEY 미설정")
        size = self._openai_size(image_size, aspect_ratio)
        _ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
        # OpenAI images/edits 는 Gemini 보다 입력 형식에 엄격하다 — 확장자만 .jpeg 인
        # MPO(아이폰 다중사진)나 팔레트/알파 모드가 오면 invalid_image_file 로 거부한다.
        # 셀러 사진은 대부분 휴대폰 촬영본이므로, 보내기 직전에 표준 PNG(RGB)로 통일한다.
        files = [
            ("image[]", (f"ref{i}.png", data, "image/png"))
            for i, data in enumerate(_as_openai_png(im) for im in images)
        ]
        # 선정 실험의 GPT Image 2 레시피: medium + PNG. GPT Image 2는
        # 모든 입력 이미지를 high-fidelity로 처리하므로 input_fidelity는 보내지 않는다.
        data = {
            "model": model,
            "prompt": prompt,
            "size": size,
            "quality": "medium",
            "output_format": "png",
            "n": "1",
        }
        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                res = await client.post(
                    "https://api.openai.com/v1/images/edits",
                    headers={"Authorization": f"Bearer {self._openai_key}"},
                    data=data, files=files)
        except httpx.RequestError as exc:
            # Gemini 경로와 같은 규칙 — 연결이 안 선 경우만 '아직 안 그려졌다'로 본다.
            # 안 맞추면 이미지 모델을 바꾸는 순간 이중 과금 방어가 통째로 사라진다
            # (2026-08-17 검증: 프로바이더별 비대칭).
            billable = not isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout))
            if billable:
                _record_unbilled_failure(model, size, t0, type(exc).__name__)
            raise GeminiError(
                f"OpenAI request failed: {type(exc).__name__}: {exc}", billable=billable) from exc
        latency_ms = int((time.perf_counter() - t0) * 1000)
        if res.status_code != 200:
            billable = res.status_code in (502, 504)
            if billable:
                _record_unbilled_failure(model, size, t0, f"http_{res.status_code}")
            raise GeminiError(f"OpenAI {res.status_code}: {res.text[:500]}", billable=billable)
        payload = None
        usage = None
        img = None
        parse_error = None
        try:
            payload = res.json()
            if not isinstance(payload, dict):
                raise ValueError("response root is not an object")
            usage = payload.get("usage")
            rows = payload.get("data") or []
            if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
                raise ValueError("no image row in response")
            b64 = rows[0].get("b64_json")
            if not isinstance(b64, str) or not b64:
                raise ValueError("no b64_json in response")
            img = base64.b64decode(b64, validate=True)
        except (TypeError, ValueError, KeyError, binascii.Error) as exc:
            parse_error = exc

        # 200이면 이미지 파싱이 실패해도 이미 과금됐을 수 있다. usage를 먼저
        # 기록해야 원장에서 조용히 사라지지 않는다.
        image_usage.record(
            model=model, image_size=size, usage=usage,
            latency_ms=latency_ms, has_image=img is not None,
        )
        if parse_error is not None:
            raise GeminiError(f"OpenAI 200 응답 형식 오류: {parse_error}") from parse_error
        return GeminiImageResult(
            image=img, mime="image/png", latency_ms=latency_ms, usage=usage)
