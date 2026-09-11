# server/app/facemarket_admin_devices.py
"""관리자 콘솔 기기 등록·승인·회수.

설계: docs/superpowers/specs/2026-09-11-admin-device-gate-design.md §5.3
판정 자체는 admin_guard.check_device 에 있다. 여기는 그 판정이 볼 행을 만들고 바꾸는 라우트다.

register·me 두 라우트만 기기 없이 열린다(require_admin_identity) — 아직 기기가 없는
관리자가 부르는 라우트라서다. 나머지는 다른 관리자 라우트와 똑같이 require_admin 을 탄다.
"""
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import Field

from . import admin_guard, facemarket_notify
from .auth import require_user
from .db import get_conn
from .models import CamelModel

router = APIRouter(prefix="/v1/facemarket/admin/devices", tags=["FaceMarket admin devices"])

LABEL_MAX = 60
UNKNOWN_LABEL = "알 수 없는 기기"


def _err(code: str, message: str, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


class RegisterRequest(CamelModel):
    label: str | None = Field(default=None, max_length=200)
    user_agent: str | None = Field(default=None, max_length=512)


def label_from_user_agent(ua: str | None) -> str:
    """UA → "macOS · Chrome" 류. 정확할 필요 없다 — 목록에서 자기 기기를 알아보는 용도.
    순서가 중요하다: iPhone UA 에 'Mac OS X' 가, Android UA 에 'Linux' 가 들어 있다."""
    ua = ua or ""
    if "iPhone" in ua:
        os_name = "iPhone"
    elif "iPad" in ua:
        os_name = "iPad"
    elif "Android" in ua:
        os_name = "Android"
    elif "Mac OS X" in ua or "Macintosh" in ua:
        os_name = "macOS"
    elif "Windows" in ua:
        os_name = "Windows"
    elif "Linux" in ua:
        os_name = "Linux"
    else:
        os_name = None
    if "Edg/" in ua:
        browser = "Edge"
    elif "Firefox/" in ua:
        browser = "Firefox"
    elif "Chrome/" in ua or "CriOS/" in ua:
        browser = "Chrome"
    elif "Safari/" in ua:
        browser = "Safari"
    else:
        browser = None
    parts = [p for p in (os_name, browser) if p]
    return " · ".join(parts) if parts else UNKNOWN_LABEL


def _clean_label(label: str | None, user_agent: str | None) -> str:
    text = " ".join((label or "").split())
    return text[:LABEL_MAX] if text else label_from_user_agent(user_agent)


# ---------- SQL ----------

COUNT_PENDING_SQL = (
    "select count(*)::int as count from admin_devices "
    "where user_id = %s and status = 'pending'"
)
INSERT_SQL = (
    "insert into admin_devices (user_id, token_hash, label, user_agent) "
    "values (%s, %s, %s, %s) returning id::text as id"
)
USER_EMAIL_SQL = "select email from auth.users where id = %s"
LIST_SQL = """
select d.id::text as id, d.user_id::text as user_id, u.email as user_email, d.label, d.status,
       d.created_at, d.last_seen_at, d.approved_at, d.revoked_at,
       a.email as approved_by_email, d.token_hash
from admin_devices d
left join auth.users u on u.id = d.user_id
left join auth.users a on a.id = d.approved_by
order by (d.status = 'pending') desc, coalesce(d.last_seen_at, d.created_at) desc
"""
LOCK_SQL = (
    "select id::text as id, user_id::text as user_id, label, status, token_hash "
    "from admin_devices where id = %s for update"
)
APPROVE_SQL = (
    "update admin_devices set status = 'approved', approved_by = %s, approved_at = now() "
    "where id = %s"
)
REVOKE_SQL = (
    "update admin_devices set status = 'revoked', revoked_by = %s, revoked_at = now() "
    "where id = %s"
)
REVOKE_ALL_FOR_USER_SQL = (
    "update admin_devices set status = 'revoked', revoked_by = %s, revoked_at = now() "
    "where user_id = %s and status in ('pending', 'approved') returning id::text as id"
)


# ---------- 순수 함수 (FakeConn 으로 테스트) ----------

async def register_device(
    conn, *, user_id: str, label: str | None, user_agent: str | None, max_pending: int
) -> dict:
    clean = _clean_label(label, user_agent)
    async with conn.cursor() as cur:
        await cur.execute(COUNT_PENDING_SQL, (user_id,))
        row = await cur.fetchone()
        if (row or {}).get("count", 0) >= max_pending:
            raise _err(
                "too_many_pending",
                "승인 대기 중인 기기가 너무 많아요. 다른 관리자에게 기존 요청을 정리해 달라고 하세요.",
                status=429,
            )
        token = secrets.token_urlsafe(32)
        await cur.execute(
            INSERT_SQL, (user_id, admin_guard.hash_device_token(token), clean, user_agent)
        )
        inserted = await cur.fetchone()
    # 토큰은 이 반환값 한 번뿐이다. DB 에는 해시만 있어 다시 만들 수 없다.
    return {"deviceId": inserted["id"], "token": token, "status": "pending", "label": clean}


async def device_status(conn, *, user_id: str, token: str | None) -> dict:
    """대기 화면이 폴링하는 값. 403 을 내지 않고 상태를 말한다. last_seen 은 안 찍는다 —
    화면을 열어 둔 것은 사용이 아니다."""
    token = (token or "").strip()
    if not token:
        return {"status": "unknown"}
    device = await admin_guard.repo.find_admin_device_by_hash(conn, admin_guard.hash_device_token(token))
    if device is None or device.get("user_id") != user_id:
        return {"status": "unknown"}
    return {"status": device["status"], "deviceId": device["id"], "label": device.get("label")}


def _iso(value) -> str | None:
    return value.isoformat() if value else None


async def list_devices(conn, *, current_token_hash: str | None) -> dict:
    async with conn.cursor() as cur:
        await cur.execute(LIST_SQL)
        rows = await cur.fetchall() or []
    return {
        "items": [
            {
                "id": r["id"], "userId": r["user_id"], "userEmail": r.get("user_email"),
                "label": r["label"], "status": r["status"],
                "createdAt": _iso(r.get("created_at")), "lastSeenAt": _iso(r.get("last_seen_at")),
                "approvedAt": _iso(r.get("approved_at")), "revokedAt": _iso(r.get("revoked_at")),
                "approvedByEmail": r.get("approved_by_email"),
                "isCurrent": bool(current_token_hash) and r.get("token_hash") == current_token_hash,
            }
            for r in rows
        ]
    }


async def _lock_device(cur, device_id: str) -> dict:
    await cur.execute(LOCK_SQL, (device_id,))
    row = await cur.fetchone()
    if row is None:
        raise _err("device_not_found", "기기를 찾을 수 없어요.", status=404)
    return row


async def approve_device(conn, *, device_id: str, actor: str) -> dict:
    async with conn.cursor() as cur:
        device = await _lock_device(cur, device_id)
        if device["status"] != "pending":
            raise _err("device_not_pending", "승인 대기 중인 기기가 아니에요.", status=409)
        await cur.execute(APPROVE_SQL, (actor, device_id))
    await admin_guard.write_audit(
        conn, actor_user_id=actor, action="device.approve", target_type="admin_device",
        target_id=device_id, before={"status": "pending"}, after={"status": "approved"},
        note=f"{device['label']} · owner {device['user_id']}",
    )
    return {"deviceId": device_id, "status": "approved"}


async def revoke_device(
    conn, *, device_id: str, actor: str, current_token_hash: str | None
) -> dict:
    async with conn.cursor() as cur:
        device = await _lock_device(cur, device_id)
        if device["status"] == "revoked":
            raise _err("device_already_revoked", "이미 회수된 기기예요.", status=409)
        # 지금 이 요청을 보낸 기기를 회수하면 그 자리에서 잠긴다 — set_role 의 자기 강등 금지와 같은 결.
        if current_token_hash and device.get("token_hash") == current_token_hash:
            raise _err(
                "cannot_revoke_current",
                "지금 쓰는 기기는 회수할 수 없어요 — 다른 기기에서 회수해 주세요.",
            )
        previous = device["status"]
        await cur.execute(REVOKE_SQL, (actor, device_id))
    await admin_guard.write_audit(
        conn, actor_user_id=actor, action="device.revoke", target_type="admin_device",
        target_id=device_id, before={"status": previous}, after={"status": "revoked"},
        note=f"{device['label']} · owner {device['user_id']}",
    )
    return {"deviceId": device_id, "status": "revoked"}


async def revoke_devices_for_user(conn, *, user_id: str, actor: str) -> int:
    """관리자 강등 때 set_role 이 부른다. 안 하면 재승격 시 옛 승인 기기가 그대로 살아난다."""
    async with conn.cursor() as cur:
        await cur.execute(REVOKE_ALL_FOR_USER_SQL, (actor, user_id))
        rows = await cur.fetchall() or []
    return len(rows)


# ---------- 라우트 ----------

def _current_hash(request: Request) -> str | None:
    token = (admin_guard.device_token_from(request) or "").strip()
    return admin_guard.hash_device_token(token) if token else None


@router.post("/register")
async def admin_register_device(
    request: Request, body: RegisterRequest, user_id: str = Depends(require_user)
):
    settings = request.app.state.settings
    async with get_conn(request) as conn:
        await admin_guard.require_admin_identity(conn, user_id)
        result = await register_device(
            conn, user_id=user_id, label=body.label,
            user_agent=body.user_agent or request.headers.get("user-agent"),
            max_pending=settings.admin_device_max_pending_per_user,
        )
        async with conn.cursor() as cur:
            await cur.execute(USER_EMAIL_SQL, (user_id,))
            email_row = await cur.fetchone()
        await conn.commit()
    # 커밋 뒤에 알린다 — 알림이 실패해도 등록은 남고, 등록이 실패하면 알림도 없다.
    await facemarket_notify.notify_slack_admin_device_requested(
        settings, email=(email_row or {}).get("email"), label=result["label"],
    )
    return JSONResponse(status_code=201, content={**result, "gate": settings.admin_device_gate})


@router.get("/me")
async def admin_device_me(request: Request, user_id: str = Depends(require_user)):
    settings = request.app.state.settings
    async with get_conn(request) as conn:
        await admin_guard.require_admin_identity(conn, user_id)
        # off 는 조회를 하지 않는다(§5.2) — 프런트가 off 에서 /me 성공에 의존하므로
        # admin_devices 테이블이 아직 없는 첫 배포에서도(마이그레이션 미적용) 살아야 한다.
        if settings.admin_device_gate == "off":
            result = {"status": "unknown"}
        else:
            result = await device_status(conn, user_id=user_id, token=admin_guard.device_token_from(request))
    return JSONResponse({**result, "gate": settings.admin_device_gate})


@router.get("")
async def admin_list_devices(request: Request, user_id: str = Depends(require_user)):
    async with get_conn(request) as conn:
        await admin_guard.require_admin(conn, user_id, request)
        return JSONResponse(await list_devices(conn, current_token_hash=_current_hash(request)))


@router.post("/{device_id}/approve")
async def admin_approve_device(
    request: Request, device_id: str, user_id: str = Depends(require_user)
):
    async with get_conn(request) as conn:
        await admin_guard.require_admin(conn, user_id, request)
        result = await approve_device(conn, device_id=device_id, actor=user_id)
        await conn.commit()
    return JSONResponse(result)


@router.post("/{device_id}/revoke")
async def admin_revoke_device(
    request: Request, device_id: str, user_id: str = Depends(require_user)
):
    async with get_conn(request) as conn:
        await admin_guard.require_admin(conn, user_id, request)
        result = await revoke_device(
            conn, device_id=device_id, actor=user_id, current_token_hash=_current_hash(request),
        )
        await conn.commit()
    return JSONResponse(result)
