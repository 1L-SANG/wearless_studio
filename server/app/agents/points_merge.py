"""서버 생성 특징의 실행별 증표와 AG-01·AG-08 문구 합치기 판정."""

from dataclasses import replace
import hmac
import json
from pathlib import Path
import re
import time

from . import vision_llm

POINTS_PURPOSE = "analysis_points_v1"
FEATURE_PURPOSE = "feature_points_v1"
_TTL = 86400
_TICKET_KEYS = {"purpose", "version", "runId", "points", "issuedAt", "expiresAt", "signature"}
_PROMPT_FILE = Path(__file__).resolve().parents[2] / "prompts" / "points_merge_v1.txt"
_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["decisions"], "properties": {
    "decisions": {"type": "array", "items": {"type": "object", "additionalProperties": False,
        "required": ["late", "action", "early"], "properties": {
            "late": {"type": "integer"}, "action": {"type": "string", "enum": ["refine", "new"]},
            "early": {"type": "integer"}}}}}}


class PointsTicketError(ValueError):
    pass


class PointsMergeError(ValueError):
    pass


def valid_run_id(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{8,64}", value) is not None


def _valid_points(value):
    return (isinstance(value, list) and len(value) <= 8
            and all(isinstance(point, str) and 0 < len(point) <= 80 for point in value))


def _signature(payload, secret):
    if not isinstance(secret, str) or len(secret) < 16:
        raise PointsTicketError("points_ticket_secret_unavailable")
    # 숫자는 정수 시각·버전만 허용해 브라우저 JSON 왕복의 1.0/1 차이를 만들지 않는다.
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":"), allow_nan=False).encode()
    return hmac.new(secret.encode(), canonical, "sha256").hexdigest()


def issue_ticket(purpose, run_id, points, secret, *, now=None):
    issued = int(time.time()) if now is None else now
    if (purpose not in (POINTS_PURPOSE, FEATURE_PURPOSE) or not valid_run_id(run_id)
            or not _valid_points(points) or type(issued) is not int):
        raise PointsTicketError("points_ticket_invalid")
    payload = {"purpose": purpose, "version": 1, "runId": run_id, "points": list(points),
               "issuedAt": issued, "expiresAt": issued + _TTL}
    return {**payload, "signature": _signature(payload, secret)}


def verify_ticket(value, secret, purpose, run_id, *, now=None):
    current = int(time.time()) if now is None else now
    if not isinstance(value, dict) or set(value) != _TICKET_KEYS:
        raise PointsTicketError("points_ticket_invalid")
    if (purpose not in (POINTS_PURPOSE, FEATURE_PURPOSE) or value["purpose"] != purpose
            or type(value["version"]) is not int or value["version"] != 1
            or not valid_run_id(run_id) or value["runId"] != run_id
            or not _valid_points(value["points"])
            or type(value["issuedAt"]) is not int or type(value["expiresAt"]) is not int
            or value["expiresAt"] - value["issuedAt"] != _TTL
            or value["issuedAt"] > current + 60 or value["expiresAt"] < current):
        raise PointsTicketError("points_ticket_expired_or_invalid")
    signature = value["signature"]
    if not isinstance(signature, str) or not re.fullmatch(r"[a-f0-9]{64}", signature):
        raise PointsTicketError("points_ticket_signature_invalid")
    try:
        expected = _signature({k: v for k, v in value.items() if k != "signature"}, secret)
    except (TypeError, ValueError) as exc:
        raise PointsTicketError("points_ticket_invalid") from exc
    if not hmac.compare_digest(signature, expected):
        raise PointsTicketError("points_ticket_signature_invalid")
    return list(value["points"])


def _data_lines(prefix, points):
    # 서명된 문구도 지시가 아닌 데이터다. JSON 문자열로 감싸고 구분자 탈출을 막는다.
    return "\n".join(
        f"{prefix}{i}: " + json.dumps(point, ensure_ascii=False).replace("<", r"\u003c").replace(">", r"\u003e")
        for i, point in enumerate(points)
    )


def _validate_decisions(raw, early_count, late_count):
    if not isinstance(raw, dict) or set(raw) != {"decisions"} or not isinstance(raw["decisions"], list):
        raise PointsMergeError("invalid_decisions")
    decisions, seen = [], set()
    for row in raw["decisions"]:
        if not isinstance(row, dict) or set(row) != {"late", "action", "early"}:
            raise PointsMergeError("invalid_decisions")
        late, action, early = row["late"], row["action"], row["early"]
        if (type(late) is not int or not 0 <= late < late_count or late in seen
                or action not in ("refine", "new")):
            raise PointsMergeError("invalid_decisions")
        if action == "refine" and (type(early) is not int or not 0 <= early < early_count):
            raise PointsMergeError("invalid_decisions")
        if action == "new" and early is not None and type(early) is not int:
            raise PointsMergeError("invalid_decisions")
        seen.add(late)
        decisions.append({"late": late, "action": action, "early": early if action == "refine" else None})
    if len(seen) != late_count:
        raise PointsMergeError("invalid_decisions")
    return decisions


async def merge_points(settings, early, late):
    if not late:
        return []
    if not early:
        return [{"late": i, "action": "new", "early": None} for i in range(len(late))]
    prompt = _PROMPT_FILE.read_text(encoding="utf-8")
    prompt = prompt.format(early=_data_lines("E", early), late=_data_lines("L", late))
    merge_settings = replace(settings, analysis_model_order="gemini", analysis_timeout_seconds=8)
    try:
        raw, _provider = await vision_llm.analyze_with_fallback(
            merge_settings, prompt, [], _SCHEMA, thinking_level="low",
            models={"gemini": settings.model_text_gemini_merge},
        )
    except Exception:
        raise PointsMergeError("model_failed") from None
    return _validate_decisions(raw, len(early), len(late))
