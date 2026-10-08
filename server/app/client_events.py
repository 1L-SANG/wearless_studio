"""정제한 화면 오류와 최소 진단 정보를 수신한다."""

import hashlib
import hmac
import json
import math
import re
import secrets
from datetime import datetime, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request
from psycopg.types.json import Json
from starlette.concurrency import run_in_threadpool

from .auth import optional_user
from .client_ip import client_ip
from .db import get_conn
from .request_id import valid_request_id

router = APIRouter(prefix="/v1", tags=["Client events"])
BODY_LIMIT = 32 * 1024
LIMITS = {"ip": 120, "user": 60, "global": 3000}
KST = ZoneInfo("Asia/Seoul")
KINDS = frozenset(("api_error", "ui_error", "runtime_error", "unhandled_rejection",
                   "error_boundary", "network_error", "toast_error", "save_error",
                   "upload_error", "generation_error", "analysis_error"))
WHOLE_SCREEN_KINDS = frozenset(("ui_error", "error_boundary", "runtime_error", "unhandled_rejection"))
STEPS = frozenset(("input", "storyboard", "mannequin", "generating", "editor", "library", "other"))
ACTIONS = frozenset(("save", "upload", "generate", "analyze", "restore", "navigate",
                     "load", "poll", "download", "export", "login", "unknown",
                     "mannequinGenerate", "mannequinAdjust", "detailPageGenerate", "editorImage"))
ROUTE_SEGMENTS = frozenset(("v1", "seller", "projects", "project", "editor", "storyboard",
                            "product-input", "input", "analysis", "analyze", "mannequin",
                            "matching", "library", "assets", "photos", "jobs", "events",
                            "generate", "save", "upload", "complete", "presign", "bytes",
                            "file", "detail-page", "detail-pages", "facemarket", "models",
                            "personalization", "profiles", "me", "account", "draft-slot",
                            "handoff", "mobile", "pricing", "credits", "unknown"))


def _invalid():
    return HTTPException(422, detail={"code": "validation_error", "message": "오류 기록 형식이 올바르지 않아요."})


def _timestamp(value):
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.isoformat() if parsed.tzinfo is not None else None
    except ValueError:
        return None


def _code(value, limit=64):
    if not isinstance(value, str):
        return None
    value = value[:limit]
    return value if re.fullmatch(r"[A-Za-z0-9_.:-]+", value) else None


def _error_code(value):
    return value if isinstance(value, str) and re.fullmatch(r"[a-z][a-z_]{0,31}", value) else "other"


def _integer(value, low, high):
    return value if type(value) is int and low <= value <= high else None


def _number(value, low, high):
    return value if type(value) in (int, float) and low <= value <= high and math.isfinite(value) else None


def _platform(raw):
    result = {}
    for name, pattern in (("family", r"[A-Za-z][A-Za-z0-9 _-]{0,31}"),
                          ("major", r"[A-Za-z0-9][A-Za-z0-9._-]{0,7}")):
        value = raw.get(name)
        if isinstance(value, str) and re.fullmatch(pattern, value):
            result[name] = value
    return result


def _technical_text(value, maximum):
    if not isinstance(value, str):
        return None
    # JSONB에 넣을 수 없는 제어 문자와 단독 surrogate를 먼저 없앤다.
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff]", "", value)

    def clean_url(match):
        address = re.split(r"[?#]", match.group(0), maxsplit=1)[0]
        return re.sub(r"^((?:[A-Za-z][A-Za-z0-9+.-]*:)?//)[^/]*@", r"\1", address)

    value = re.sub(r"(?:(?:https?|wss?|ftp|file|webpack):)?//[^\s<>\"'()]+|/[^\s<>\"'()]+", clean_url, value,
                   flags=re.IGNORECASE)

    def clean_email(match):
        token = match.group(0)
        if "@" not in token:
            return token
        _, _, address = token.partition("@")
        # Firefox의 함수명@URL 프레임은 보존한다. 긴 문자열에서도 토큰당 한 번만 검사한다.
        if "@" not in address and re.match(r"(?:blob:)?(?:[A-Za-z][A-Za-z0-9+.-]*:)?//|/", address):
            return token
        return "[email]"

    value = re.sub(r"\S+", clean_email, value)
    value = re.sub(r"\d{6,}", "[digits]", value)
    # 길이를 먼저 자르면 이메일과 URL이 중간에서 끊겨 정제를 우회할 수 있다.
    return value[:maximum]


def _diagnostics(raw):
    step = raw.get("step")
    result = {"step": step if isinstance(step, str) and step in STEPS else "other",
              "message_code": _error_code(raw.get("message_code"))}
    for name in ("browser", "os"):
        if isinstance(raw.get(name), dict):
            result[name] = _platform(raw[name])
    if isinstance(raw.get("viewport"), dict):
        result["viewport"] = {}
        for name in ("width", "height"):
            value = _integer(raw["viewport"].get(name), 0, 16384)
            if value is not None:
                result["viewport"][name] = value
    if isinstance(raw.get("network"), dict):
        network = raw["network"]
        effective_type = network.get("effective_type")
        result["network"] = {"effective_type": effective_type if isinstance(effective_type, str) and
                             effective_type in ("slow-2g", "2g", "3g", "4g", "unknown") else "unknown"}
        for name, maximum in (("rtt_ms", 60000), ("downlink_mbps", 10000)):
            value = _number(network.get(name), 0, maximum)
            if value is not None:
                result["network"][name] = value
        for name in ("save_data", "online"):
            if type(network.get(name)) is bool:
                result["network"][name] = network[name]
    age = _integer(raw.get("page_age_ms"), 0, 604800000)
    if age is not None:
        result["page_age_ms"] = age
    if isinstance(raw.get("visibility"), str) and raw["visibility"] in ("visible", "hidden"):
        result["visibility"] = raw["visibility"]
    for name, maximum in (("tech_message", 500), ("stack", 2000)):
        if name == "stack" and raw["kind"] not in WHOLE_SCREEN_KINDS:
            continue
        value = _technical_text(raw.get(name), maximum)
        if value is not None:
            result[name] = value
    return result


def normalize_route(value):
    if not isinstance(value, str) or len(value) > 512:
        return "/unknown"
    try:
        path = urlsplit(value).path
    except ValueError:
        return "/unknown"
    segments = []
    for part in path.split("/")[:16]:
        if not part:
            continue
        if part in ROUTE_SEGMENTS:
            segments.append(part)
        elif valid_request_id(part) or part.isdecimal() or part == ":id" or re.fullmatch(r"\{[a-zA-Z_]+\}", part):
            segments.append(":id")
        else:
            segments.append(":segment")
    return "/" + "/".join(segments)


def _breadcrumb(raw):
    if not isinstance(raw, dict):
        return None
    result = {"type": raw.get("type") if raw.get("type") in ("api", "action", "navigation") else "unknown"}
    at = _timestamp(raw.get("at"))
    if at:
        result["at"] = at
    for name, value in (
        ("route", normalize_route(raw.get("route"))),
        ("action", raw.get("action") if isinstance(raw.get("action"), str) and raw.get("action") in ACTIONS else "unknown"),
        ("request_id", valid_request_id(raw.get("request_id"))),
        ("status", _integer(raw.get("status"), 0, 599)),
        ("error_code", _error_code(raw.get("error_code"))),
        ("count", _integer(raw.get("count"), 1, 10000)),
        ("duration_ms", _integer(raw.get("duration_ms"), 0, 3600000)),
    ):
        if value is not None:
            result[name] = value
    if raw.get("method") in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
        result["method"] = raw["method"]
    return result


def sanitize_event(raw):
    if not isinstance(raw, dict):
        raise _invalid()
    event_id = valid_request_id(raw.get("event_id"))
    occurred_at = _timestamp(raw.get("occurred_at"))
    if not event_id or raw.get("schema_version") != 1 or type(raw.get("schema_version")) is not int or not occurred_at:
        raise _invalid()
    if not isinstance(raw.get("kind"), str) or raw["kind"] not in KINDS:
        raise _invalid()
    action = raw.get("action")
    device = raw.get("device")
    result = {
        "event_id": event_id, "schema_version": 1, "occurred_at": occurred_at,
        "kind": raw["kind"], "action": action if isinstance(action, str) and action in ACTIONS else "unknown",
        "route": normalize_route(raw.get("route")), "status": _integer(raw.get("status"), 0, 599),
        "error_code": _error_code(raw.get("error_code")), "request_id": valid_request_id(raw.get("request_id")),
        "project_id": valid_request_id(raw.get("project_id")), "job_id": valid_request_id(raw.get("job_id")),
        "build_sha": _code(raw.get("build_sha")),
        "device": device if isinstance(device, str) and device in ("desktop", "mobile", "tablet") else "unknown",
        "blocking": raw.get("blocking") is True,
    }
    result.update(_diagnostics(raw))
    context = raw.get("context")
    result["context"] = {}
    if isinstance(context, dict):
        for key in ("online", "local_backup"):
            if type(context.get(key)) is bool:
                result["context"][key] = context[key]
        for key, choices in (("visibility", ("visible", "hidden")),
                             ("draft_slot_status", ("owned", "lost", "none", "unknown"))):
            if isinstance(context.get(key), str) and context[key] in choices:
                result["context"][key] = context[key]
        count = _integer(context.get("photo_pending_count"), 0, 1000)
        if count is not None:
            result["context"]["photo_pending_count"] = count
    crumbs = raw.get("breadcrumbs")
    result["breadcrumbs"] = [clean for item in crumbs[-30:] if (clean := _breadcrumb(item))] if isinstance(crumbs, list) else []
    return result


def event_signature(event):
    fields = [event[key] for key in ("kind", "action", "route", "status", "error_code")]
    return hashlib.sha256(json.dumps(fields, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()


async def get_daily_salt(conn, cache, *, now=None):
    day = (now or datetime.now(timezone.utc)).astimezone(KST).date()
    if day in cache:
        return cache[day]
    async with conn.cursor() as cur:
        await cur.execute(
            "insert into client_event_salts (day, salt) values (%s, %s) on conflict (day) do nothing",
            (day, secrets.token_bytes(32)),
        )
        await cur.execute("select salt from client_event_salts where day = %s", (day,))
        salt = bytes((await cur.fetchone())["salt"])
    # 다른 인스턴스도 같은 값을 읽도록 확정한 뒤 오늘 값 하나만 캐시한다.
    await conn.commit()
    cache.clear()
    cache[day] = salt
    return salt


def hash_ip(ip, salt):
    return hmac.new(salt, ip.encode(), hashlib.sha256).hexdigest()


async def read_body(request):
    length = request.headers.get("content-length")
    if length:
        try:
            if int(length) > BODY_LIMIT:
                raise HTTPException(413, detail={"code": "body_too_large", "message": "오류 기록이 너무 커요."})
        except ValueError:
            raise _invalid() from None
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > BODY_LIMIT:
            raise HTTPException(413, detail={"code": "body_too_large", "message": "오류 기록이 너무 커요."})
        data.extend(chunk)
    return bytes(data)


async def consume_limit(conn, scope, key):
    async with conn.cursor() as cur:
        await cur.execute(
            """insert into client_event_rate_limits (scope, key, window_start, request_count)
               values (%s, %s, date_trunc('minute', clock_timestamp()), 1)
               on conflict (scope, key, window_start) do update
                 set request_count = client_event_rate_limits.request_count + 1
                 where client_event_rate_limits.request_count < %s
               returning request_count""", (scope, key, LIMITS[scope]))
        return await cur.fetchone() is not None


async def lock_user_events(conn, user_id):
    async with conn.cursor() as cur:
        await cur.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", ("client-events-user:" + user_id,))


async def delete_user_events(conn, user_id):
    await lock_user_events(conn, user_id)
    async with conn.cursor() as cur:
        await cur.execute("delete from client_error_events where user_id = %s", (user_id,))
        await cur.execute("delete from client_event_rate_limits where scope = 'user' and key = %s", (user_id,))


async def store_event(conn, event, user_id, ip_hmac):
    if user_id:
        from .repo import user_account_purge_closed
        await lock_user_events(conn, user_id)
        if await user_account_purge_closed(conn, user_id):
            return
    # 커서 조회가 진행 중인 INSERT의 커밋을 기다리도록 짧은 공유 잠금을 잡는다.
    async with conn.cursor() as cur:
        await cur.execute("select pg_advisory_xact_lock_shared(%s, %s)", (0x4345, 2))
    columns = ("event_id", "schema_version", "occurred_at", "build_sha", "kind", "route", "action",
               "status", "error_code", "request_id", "project_id", "job_id", "device", "blocking")
    values = tuple(event[key] for key in columns)
    async with conn.cursor() as cur:
        await cur.execute(
            f"insert into client_error_events ({', '.join(columns)}, user_id, ip_hmac, signature, payload) "
            f"values ({', '.join(['%s'] * (len(columns) + 4))}) on conflict (event_id) do nothing",
            (*values, user_id, ip_hmac, event_signature(event), Json(event)))


@router.post("/client-events", status_code=202)
async def collect_event(request: Request):
    # IP와 전체 제한은 본문 파싱과 JWT 조회보다 먼저 커밋한다.
    async with get_conn(request) as conn:
        salt = await get_daily_salt(conn, request.app.state.client_event_salt_cache)
        ip_hmac = hash_ip(client_ip(request), salt)
        allowed = await consume_limit(conn, "ip", ip_hmac)
        if allowed:
            allowed = await consume_limit(conn, "global", "all")
        await conn.commit()
    if not allowed:
        return {"accepted": True}
    data = await read_body(request)
    try:
        raw = json.loads(data)
    except (ValueError, UnicodeError, RecursionError):
        raise _invalid() from None
    event = sanitize_event(raw)
    user_id = valid_request_id(await run_in_threadpool(optional_user, request))
    async with get_conn(request) as conn:
        if user_id:
            await lock_user_events(conn, user_id)
        if user_id is None or await consume_limit(conn, "user", user_id):
            await store_event(conn, event, user_id, ip_hmac)
        await conn.commit()
    return {"accepted": True}
