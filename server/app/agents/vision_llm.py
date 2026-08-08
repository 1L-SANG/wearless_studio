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

#: 실패 부류 — 대응이 서로 다른 것만 나눈다. 값은 저장·이벤트에 실리므로 안정 문자열이다.
CATEGORY_TIMEOUT = "timeout"                  # 재시도가 의미 있음
CATEGORY_RATE_LIMIT = "rate_limit"            # 물러섰다 재시도
CATEGORY_PROVIDER_UNAVAILABLE = "provider_unavailable"   # 5xx — provider 쪽 문제
CATEGORY_PROVIDER_REJECTED = "provider_rejected"         # 4xx — 요청·키·모델 문제
CATEGORY_MALFORMED = "response_malformed"     # 200 인데 JSON/스키마가 아님
CATEGORY_EMPTY = "response_empty"             # 200 인데 본문 없음(안전필터 등)
CATEGORY_NO_KEY = "no_key"
CATEGORY_TRANSPORT = "transport_error"        # 연결 자체가 실패
CATEGORY_PROVIDER_ERROR = "provider_error"    # vision 계층 실패인데 status 가 없음
CATEGORY_UNEXPECTED = "unexpected_error"


class VisionError(RuntimeError):
    """분석 LLM 호출/파싱 실패 — 워커·라우트가 한국어 error 봉투로 매핑.

    메시지 **원문은 여전히 어디에도 전파하지 않는다** — provider 오류 본문에는 요청 URL 과
    쿼리(키 포함 가능)가 들어간다. 대신 대응을 가르는 세 값만 구조화해서 들고 다닌다:
    어느 provider 인가, HTTP status 는 무엇인가, 어떤 부류의 실패인가.

    이게 없어서 2026-08-04~07 사이 landmark 실패 9건 중 단 한 건도 분류하지 못했다.
    DB 에 남은 것은 `기하 추출 실패: VisionError` 뿐이었고, status·body·category 는 전부
    실패 시점에 존재했는데 세 계층이 차례로 버렸다.
    """

    def __init__(self, message, *, provider=None, status=None, category=None):
        super().__init__(message)
        self.provider = provider
        self.status = status
        self.category = category


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _envelope_json(res, provider: str) -> dict:
    """응답 엔벨로프 파싱 → VisionError 로 변환. 200+비JSON(프록시 HTML 등)도 폴백 대상이 되게
    (analyze_with_fallback 이 VisionError 를 잡는다 — raw JSONDecodeError 는 폴백을 우회한다)."""
    try:
        return res.json()
    except ValueError as e:  # json.JSONDecodeError ⊂ ValueError
        raise VisionError(f"{provider} 응답 파싱 실패: {e}", provider=provider,
                          status=res.status_code, category=CATEGORY_MALFORMED) from e


def _parse_json(text: str, provider: str) -> dict:
    if not text or not text.strip():
        raise VisionError(f"{provider} 응답이 비어 있어요.", provider=provider,
                          category=CATEGORY_EMPTY)
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError) as e:
        raise VisionError(f"{provider} JSON 파싱 실패: {e}", provider=provider,
                          category=CATEGORY_MALFORMED) from e
    if not isinstance(parsed, dict):
        raise VisionError(f"{provider} 응답이 객체가 아니에요.", provider=provider,
                          category=CATEGORY_MALFORMED)
    return parsed


async def _call_gpt(settings: Settings, model: str, prompt: str,
                    images: list[InlineImage], schema: dict, timeout: float,
                    thinking_level: str | None = None) -> dict:  # thinking_level: Gemini 전용(GPT 미사용)
    """OpenAI chat/completions — Structured Outputs(strict json_schema). content 는 문자열 JSON."""
    if not settings.openai_api_key:
        raise VisionError("OPENAI_API_KEY 미설정", provider="gpt",
                          category=CATEGORY_NO_KEY)
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
        raise VisionError(f"OpenAI {res.status_code}: {res.text[:300]}",
                          provider="gpt", status=res.status_code)
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
                       images: list[InlineImage], schema: dict, timeout: float,
                       thinking_level: str | None = None) -> dict:
    """Gemini generateContent — responseSchema + responseMimeType json. 텍스트 파트 합쳐 파싱."""
    if not settings.gemini_api_key:
        raise VisionError("GEMINI_API_KEY 미설정", provider="gemini",
                          category=CATEGORY_NO_KEY)
    parts: list = [{"text": prompt}]
    for im in images:
        parts.append({"inline_data": {"mime_type": im.mime, "data": _b64(im.data)}})
    gen: dict = {
        "responseMimeType": "application/json",
        "responseSchema": _to_gemini_schema(schema),
    }
    # 분류·추출 작업엔 low 로 충분 — 미지정 시 모델 기본(깊은 추론)이 수 초를 낭비한다
    # (2026-07-07 속도 개선, 실측: gemini-3.5-flash v1beta 가 thinkingLevel 수용 확인).
    # 콜별 오버라이드(thinking_level 인자) > 전역 설정 — AG-08 특징 발굴은 medium (후보 선별).
    level = thinking_level or settings.analysis_thinking_level
    if level != "off":
        gen["thinkingConfig"] = {"thinkingLevel": level}
    body = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": gen,
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        res = await client.post(
            _GEMINI_URL.format(model=model), json=body,
            headers={"x-goog-api-key": settings.gemini_api_key})
    if res.status_code != 200:
        raise VisionError(f"Gemini {res.status_code}: {res.text[:300]}",
                          provider="gemini", status=res.status_code)
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


def _failure_category(exc: BaseException) -> str:
    """실패 분류 — 원문 없이 대응을 가르는 최소 정보만.

    status 가 있으면 그것으로 가른다: 429 는 물러섰다 재시도, 5xx 는 provider 쪽,
    4xx 는 우리 요청·키·모델 쪽이다. 이전에는 셋이 전부 `provider_error` 로 뭉개져
    "다시 시도하면 되는가"조차 답할 수 없었다.
    """
    name = type(exc).__name__
    if "Timeout" in name:
        return CATEGORY_TIMEOUT
    if isinstance(exc, VisionError):
        if exc.category:
            return exc.category
        status = exc.status
        if isinstance(status, int):
            if status == 429:
                return CATEGORY_RATE_LIMIT
            if 500 <= status < 600:
                return CATEGORY_PROVIDER_UNAVAILABLE
            if 400 <= status < 500:
                return CATEGORY_PROVIDER_REJECTED
        # status 가 없는 VisionError 는 여전히 vision 계층의 실패다(다른 agent 들이
        # 스키마 위반에 이 형태로 raise 한다). 기존 계약대로 provider_error 로 남긴다 —
        # 새 status 분류는 그 위를 **세분화**할 뿐 기존 의미를 뺏지 않는다.
        return CATEGORY_PROVIDER_ERROR
    if isinstance(exc, httpx.HTTPError):
        return CATEGORY_TRANSPORT
    return CATEGORY_UNEXPECTED


def failure_summary(exc: BaseException) -> str:
    """저장·이벤트에 실어도 안전한 한 줄. provider·status·category 만 — 원문·URL 없음."""
    parts = [type(exc).__name__]
    provider = getattr(exc, "provider", None)
    status = getattr(exc, "status", None)
    if provider:
        parts.append(str(provider))
    if isinstance(status, int):
        parts.append(str(status))
    parts.append(_failure_category(exc))
    return " ".join(parts)


async def analyze_with_fallback(
    settings: Settings, prompt: str, images: list[InlineImage], schema: dict,
    thinking_level: str | None = None,
) -> tuple[dict, str]:
    """순서대로 provider 시도 → (파싱된 raw dict, 사용한 provider). 전부 실패 시 VisionError.

    키 미설정 provider 는 skip. 각 provider 는 timeout(analysis_timeout_seconds) 상한;
    실패/비순응/타임아웃이면 다음으로 폴백. `images` 는 bytes(InlineImage).
    thinking_level 은 콜별 오버라이드(미지정 시 settings.analysis_thinking_level)."""
    timeout = settings.analysis_timeout_seconds
    attempts: list[str] = []
    last_error: Exception | None = None
    for name in _order(settings):
        call, model_of, key_of = _PROVIDERS[name]
        if not key_of(settings):
            attempts.append(f"{name}:no_key")
            continue
        try:
            raw = await call(settings, model_of(settings), prompt, images, schema, timeout,
                             thinking_level=thinking_level)
            if attempts:
                logger.info("vision_llm fallback used", extra={"provider": name, "prior": attempts})
            return raw, name
        except (VisionError, httpx.HTTPError) as e:
            last_error = e
            attempts.append(f"{name}:err")
            # 원문을 남기지 않는다: provider 오류 메시지에는 요청 URL·쿼리(키 포함 가능)와
            # 응답 본문 300자가 들어간다(_call_gpt·_call_gemini 참조). 운영에 필요한 것은
            # "어느 provider 가 어떤 종류로 실패했는가"까지다.
            logger.warning("vision_llm provider failed",
                           extra={"provider": name, "error_type": type(e).__name__,
                                  "category": _failure_category(e)})
            continue
    if last_error is None:  # 시도할 provider 자체가 없었음(키 전무)
        raise VisionError("분석 AI 키가 설정되지 않았어요. 관리자에게 문의해 주세요.",
                          category=CATEGORY_NO_KEY)
    # 사용자 문구는 그대로. 달라진 것은 **왜 실패했는지가 같이 나간다**는 점이다 —
    # 원문·URL 은 여전히 붙이지 않고(키가 실린다), `from` 으로 원 예외를 체인하지도
    # 않는다(트레이스백이 그 원문을 출력할 수 있다). provider·status·category 세 값만
    # 넘긴다. 이 셋이 없어서 landmark 실패 9건이 전부 분류 불능이었다.
    raise VisionError("상품 분석에 실패했어요. 잠시 후 다시 시도해 주세요.",
                      provider=getattr(last_error, "provider", None),
                      status=getattr(last_error, "status", None),
                      category=_failure_category(last_error))


async def complete_json(settings: Settings, prompt: str, schema: dict) -> tuple[dict, str]:
    """텍스트 전용 구조화 호출(이미지 없음) — AG-02 카피·AG-03 카피검수 등 text tier 재사용.
    `analyze_with_fallback` 을 images=[] 로 호출한다(프로바이더 content/parts 에 이미지 파트만 빠짐)."""
    return await analyze_with_fallback(settings, prompt, [], schema)
