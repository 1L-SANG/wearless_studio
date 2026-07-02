"""서버사이드 Gemini 텍스트 클라이언트 — 구조화 JSON 출력 전용 (text tier: AG-01 등).

gemini_image.py와 동일한 인증·엔드포인트(AI Studio/Vertex 분기)·httpx 비차단 호출.
차이는 generationConfig(responseJsonSchema + thinkingLevel)와 응답 파싱(JSON 텍스트)뿐.
스키마 준수의 최종 게이트는 호출측 pydantic 검증 (pl1_analysis_agent_spec §3.2 이중 게이트).
"""

import base64
import json
import logging
import time
from dataclasses import dataclass

import httpx

from ..config import Settings
from .gemini_image import InlineImage

log = logging.getLogger("wearless.gemini_text")


@dataclass(frozen=True)
class GeminiJsonResult:
    data: dict  # json.loads 결과 (스키마 검증은 호출측)
    latency_ms: int
    usage: dict | None  # usageMetadata — 토큰 관측 (모듈 §6-5)


class GeminiTextError(RuntimeError):
    pass


def to_openapi_schema(schema: dict) -> dict:
    """responseJsonSchema 미지원(400) 폴백용 변환 (pl1 spec §6.3 — 결정적 규칙):
    ① "type": [X, "null"] → "type": X + "nullable": true ② enum에서 null 제거
    ③ 타입명은 대문자(STRING 등 — REST responseSchema는 OpenAPI proto enum) ④ 나머지 그대로."""
    if isinstance(schema, list):
        return [to_openapi_schema(x) for x in schema]
    if not isinstance(schema, dict):
        return schema
    out: dict = {}
    for k, v in schema.items():
        if k == "type" and isinstance(v, list):
            non_null = [t for t in v if t != "null"]
            out["type"] = (non_null[0] if non_null else "string").upper()
            if "null" in v:
                out["nullable"] = True
        elif k == "type" and isinstance(v, str):
            out["type"] = v.upper()
        elif k == "enum" and isinstance(v, list):
            out["enum"] = [e for e in v if e is not None]
        else:
            out[k] = to_openapi_schema(v)
    return out


class GeminiTextClient:
    """앱 1개당 1개. app.state.gemini_text 에 둔다. gemini_api_key 없으면 생성 안 함."""

    def __init__(self, settings: Settings):
        self._key = settings.gemini_api_key
        self._vertex_project = settings.vertex_project
        self._vertex_location = settings.vertex_location

    def _endpoint(self, model: str) -> str:
        # 키는 URL이 아니라 x-goog-api-key 헤더로 — 로그/에러 유출 방지 (gemini_image와 동일)
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
        system: str,
        user_text: str,
        images: list[InlineImage],
        response_schema: dict,
        thinking_level: str,
        max_output_tokens: int,
        *,
        use_json_schema: bool = True,
        with_thinking: bool = True,
    ) -> dict:
        gen: dict = {
            "responseMimeType": "application/json",
            "maxOutputTokens": max_output_tokens,
            # temperature 미지정 = 기본 1.0 (Gemini 3 권고 — pl1 spec §2.3)
        }
        if use_json_schema:
            gen["responseJsonSchema"] = response_schema
        else:
            gen["responseSchema"] = to_openapi_schema(response_schema)
        if with_thinking:
            gen["thinkingConfig"] = {"thinkingLevel": thinking_level}
        return {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": user_text},
                        *[
                            {"inline_data": {"mime_type": im.mime, "data": base64.b64encode(im.data).decode()}}
                            for im in images
                        ],
                    ],
                }
            ],
            "generationConfig": gen,
        }

    async def generate_json(
        self,
        model: str,
        system: str,
        user_text: str,
        images: list[InlineImage],
        response_schema: dict,
        *,
        thinking_level: str = "low",
        max_output_tokens: int = 2048,
        timeout: float = 60.0,
    ) -> GeminiJsonResult:
        if not self._key:
            raise GeminiTextError("GEMINI_API_KEY 미설정")
        t0 = time.perf_counter()
        use_json_schema, with_thinking = True, True
        async with httpx.AsyncClient(timeout=timeout) as client:
            for _ in range(3):  # 최초 1회 + 필드 폴백 최대 2회 (스키마/thinking — 라이브 A/B 흡수)
                body = self._body(
                    system, user_text, images, response_schema,
                    thinking_level, max_output_tokens,
                    use_json_schema=use_json_schema, with_thinking=with_thinking,
                )
                try:
                    res = await client.post(
                        self._endpoint(model), json=body, headers={"x-goog-api-key": self._key}
                    )
                except httpx.HTTPError as e:
                    # 타임아웃·연결 오류도 GeminiTextError로 — 워커 재시도 대상 (pl1 spec §2.3)
                    raise GeminiTextError(f"Gemini 네트워크 오류: {e!r}") from e
                if res.status_code == 400 and use_json_schema and "responseJsonSchema" in res.text:
                    use_json_schema = False  # 폴백: responseSchema(OpenAPI 서브셋) — §6.3
                    log.warning("responseJsonSchema 미지원 응답 — responseSchema로 폴백 (model=%s)", model)
                    continue
                if res.status_code == 400 and with_thinking and "thinking" in res.text.lower():
                    with_thinking = False  # 폴백: thinkingConfig 미지원 모델/엔드포인트
                    log.warning("thinkingConfig 거부 — thinking 없이 재호출 (model=%s)", model)
                    continue
                break
        latency_ms = int((time.perf_counter() - t0) * 1000)
        if res.status_code != 200:
            raise GeminiTextError(f"Gemini {res.status_code}: {res.text[:500]}")
        try:
            data = res.json()
        except ValueError as e:  # 200이지만 본문이 JSON이 아님 — 재시도 대상 (§2.3)
            raise GeminiTextError(f"응답 본문 JSON 파싱 실패: {res.text[:200]}") from e
        parts = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts")) or []
        text = "".join(p.get("text", "") for p in parts).strip()
        if not text:
            raise GeminiTextError("응답에 텍스트 없음")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as e:
            raise GeminiTextError(f"JSON 파싱 실패: {e} — {text[:200]}")
        if not isinstance(parsed, dict):
            raise GeminiTextError(f"JSON 객체가 아님: {text[:200]}")
        return GeminiJsonResult(data=parsed, latency_ms=latency_ms, usage=data.get("usageMetadata"))
