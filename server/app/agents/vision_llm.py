"""AG-01 vision LLM 클라이언트 — 구조화 JSON 추출 + GPT↔Gemini 순차 폴백.

- **이미지는 bytes(InlineImage)** — R2 URL 아님. 서명 URL 만료·provider 발산 회피
  (`mannequin_job.py:173` 와 동일한 유일 검증 경로).
- **provider 호출은 httpx 직접** — OpenAI SDK 미설치. `gemini_image.py` 와 같은 패턴이라
  신규 의존성 0 + 단위테스트에서 `_call_gpt`/`_call_gemini` 를 목킹하기 쉽다.
- **구조화 출력**: GPT = `response_format` json_schema(strict), Gemini = `responseSchema`
  (+ `responseMimeType: application/json`). 계약 §6.2 "text tier는 JSON schema 강제".
- **폴백**: `ANALYSIS_MODEL_ORDER`(기본 `gpt,gemini`) 순서로 시도, 1차 실패/비순응/타임아웃 →
  다음 provider. 키 미설정 provider 는 순서에서 skip. 기본 순서 = 계약(GPT-first, ai_agent_modules §1).
"""

import base64
import json
import logging

import httpx

from ..config import Settings
from .gemini_image import InlineImage

logger = logging.getLogger("wearless.vision_llm")

_OPENAI_URL = "https://api.openai.com/v1/chat/completions"
_GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class VisionError(RuntimeError):
    """분석 LLM 호출/파싱 실패 — 워커·라우트가 한국어 error 봉투로 매핑."""


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _envelope_json(res, provider: str) -> dict:
    """응답 엔벨로프 파싱 → VisionError 로 변환. 200+비JSON(프록시 HTML 등)도 폴백 대상이 되게
    (analyze_with_fallback 이 VisionError 를 잡는다 — raw JSONDecodeError 는 폴백을 우회한다)."""
    try:
        return res.json()
    except ValueError as e:  # json.JSONDecodeError ⊂ ValueError
        raise VisionError(f"{provider} 응답 파싱 실패: {e}") from e


def _parse_json(text: str, provider: str) -> dict:
    if not text or not text.strip():
        raise VisionError(f"{provider} 응답이 비어 있어요.")
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError) as e:
        raise VisionError(f"{provider} JSON 파싱 실패: {e}") from e
    if not isinstance(parsed, dict):
        raise VisionError(f"{provider} 응답이 객체가 아니에요.")
    return parsed


async def _call_gpt(settings: Settings, model: str, prompt: str,
                    images: list[InlineImage], schema: dict, timeout: float) -> dict:
    """OpenAI chat/completions — Structured Outputs(strict json_schema). content 는 문자열 JSON."""
    if not settings.openai_api_key:
        raise VisionError("OPENAI_API_KEY 미설정")
    content = [{"type": "text", "text": prompt}]
    for im in images:
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:{im.mime};base64,{_b64(im.data)}"}})
    body = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "product_analysis", "strict": True, "schema": schema},
        },
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        res = await client.post(
            _OPENAI_URL, json=body,
            headers={"Authorization": f"Bearer {settings.openai_api_key}"})
    if res.status_code != 200:
        raise VisionError(f"OpenAI {res.status_code}: {res.text[:300]}")
    data = _envelope_json(res, "OpenAI")
    msg = ((data.get("choices") or [{}])[0].get("message") or {})
    return _parse_json(msg.get("content") or "", "OpenAI")


def _to_gemini_schema(node: dict) -> dict:
    """JSON-Schema(소문자 type) → Gemini responseSchema(대문자 TYPE + nullable).

    Gemini `Schema` proto 는 additionalProperties·strict 를 모르고 type 을 대문자 enum 으로,
    nullable 을 별도 키로 받는다. `["string","null"]` 형태를 nullable=true 로 접는다."""
    _TYPE = {"object": "OBJECT", "array": "ARRAY", "string": "STRING",
             "number": "NUMBER", "integer": "INTEGER", "boolean": "BOOLEAN"}
    t = node.get("type")
    nullable = False
    if isinstance(t, list):  # ["string","null"] → STRING + nullable
        nullable = "null" in t
        non_null = [x for x in t if x != "null"]
        t = non_null[0] if non_null else "string"
    out: dict = {"type": _TYPE.get(t, "STRING")}
    if nullable:
        out["nullable"] = True
    if node.get("enum"):
        out["enum"] = node["enum"]
    if node.get("description"):
        out["description"] = node["description"]
    if t == "object":
        props = node.get("properties") or {}
        out["properties"] = {k: _to_gemini_schema(v) for k, v in props.items()}
        if node.get("required"):
            out["required"] = node["required"]
    elif t == "array" and node.get("items"):
        out["items"] = _to_gemini_schema(node["items"])
    return out


async def _call_gemini(settings: Settings, model: str, prompt: str,
                       images: list[InlineImage], schema: dict, timeout: float) -> dict:
    """Gemini generateContent — responseSchema + responseMimeType json. 텍스트 파트 합쳐 파싱."""
    if not settings.gemini_api_key:
        raise VisionError("GEMINI_API_KEY 미설정")
    parts: list = [{"text": prompt}]
    for im in images:
        parts.append({"inline_data": {"mime_type": im.mime, "data": _b64(im.data)}})
    body = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": _to_gemini_schema(schema),
        },
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        res = await client.post(
            _GEMINI_URL.format(model=model), json=body,
            headers={"x-goog-api-key": settings.gemini_api_key})
    if res.status_code != 200:
        raise VisionError(f"Gemini {res.status_code}: {res.text[:300]}")
    data = _envelope_json(res, "Gemini")
    parts_out = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts")) or []
    text = "".join(p.get("text", "") for p in parts_out)
    return _parse_json(text, "Gemini")


# provider 이름 → (호출 함수, 모델 selector, 키 selector). ANALYSIS_MODEL_ORDER 가 순서를 정한다.
_PROVIDERS = {
    "gpt": (_call_gpt, lambda s: s.model_text, lambda s: s.openai_api_key),
    "gemini": (_call_gemini, lambda s: s.model_text_gemini, lambda s: s.gemini_api_key),
}


def _order(settings: Settings) -> list[str]:
    names = [p.strip().lower() for p in (settings.analysis_model_order or "").split(",") if p.strip()]
    return [n for n in names if n in _PROVIDERS] or ["gpt", "gemini"]


async def analyze_with_fallback(
    settings: Settings, prompt: str, images: list[InlineImage], schema: dict,
) -> tuple[dict, str]:
    """순서대로 provider 시도 → (파싱된 raw dict, 사용한 provider). 전부 실패 시 VisionError.

    키 미설정 provider 는 skip. 각 provider 는 timeout(analysis_timeout_seconds) 상한;
    실패/비순응/타임아웃이면 다음으로 폴백. `images` 는 bytes(InlineImage)."""
    timeout = settings.analysis_timeout_seconds
    attempts: list[str] = []
    last_error: Exception | None = None
    for name in _order(settings):
        call, model_of, key_of = _PROVIDERS[name]
        if not key_of(settings):
            attempts.append(f"{name}:no_key")
            continue
        try:
            raw = await call(settings, model_of(settings), prompt, images, schema, timeout)
            if attempts:
                logger.info("vision_llm fallback used", extra={"provider": name, "prior": attempts})
            return raw, name
        except (VisionError, httpx.HTTPError) as e:
            last_error = e
            attempts.append(f"{name}:err")
            logger.warning("vision_llm provider failed: %s (%s)", name, str(e)[:200])
            continue
    if last_error is None:  # 시도할 provider 자체가 없었음(키 전무)
        raise VisionError("분석 AI 키가 설정되지 않았어요. 관리자에게 문의해 주세요.")
    raise VisionError("상품 분석에 실패했어요. 잠시 후 다시 시도해 주세요.")


async def complete_json(settings: Settings, prompt: str, schema: dict) -> tuple[dict, str]:
    """텍스트 전용 구조화 호출(이미지 없음) — AG-02 카피·AG-03 카피검수 등 text tier 재사용.
    `analyze_with_fallback` 을 images=[] 로 호출한다(프로바이더 content/parts 에 이미지 파트만 빠짐)."""
    return await analyze_with_fallback(settings, prompt, [], schema)
