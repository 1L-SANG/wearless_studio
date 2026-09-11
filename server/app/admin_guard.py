"""관리자 권한 게이트와 감사 원장 기록 — 콘솔의 모든 쓰기가 지나는 문.

`repo.is_admin` 을 직접 부르는 곳은 이 파일 하나여야 한다. 예전에는 같은 판정이 6군데에
흩어져 있어 에러 코드·문구가 제각각이었고, 새 라우트를 추가할 때 가드를 빼먹어도 아무도
몰랐다(테스트가 그걸 못 본다).

2026-09-11 부터 두 번째 관문이 붙었다 — **기기**. 관리자 계정이어도 승인된 기기의 토큰
(`X-Admin-Device`)이 없으면 막는다. 설계: docs/superpowers/specs/2026-09-11-admin-device-gate-design.md.
모드는 settings.admin_device_gate(off/shadow/enforce). require_admin 이 request 를 받는
이유가 이것이다 — 호출부 24곳이 전부 request 를 넘기는지는 test_admin_guard_adoption 이 센다.

write_audit 은 conn.commit() 을 하지 않는다 — 호출자(라우트)의 트랜잭션 안에서 조치와
함께 커밋돼야 한다. 따로 커밋하면 조치는 실패하고 기록만 남는 경우가 생긴다.
"""
import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request
from psycopg.types.json import Json

from . import repo

logger = logging.getLogger(__name__)

DEVICE_HEADER = "X-Admin-Device"
# last_seen_at 은 표시용이다. 요청마다 쓰면 읽기 라우트가 전부 쓰기가 된다.
DEVICE_TOUCH_INTERVAL = timedelta(seconds=60)
DEVICE_MESSAGES = {
    "device_missing": "등록된 기기에서만 쓸 수 있어요.",
    # 남의 토큰도 같은 문구 — 그 토큰이 존재한다는 사실을 알려 주지 않는다.
    "device_unknown": "등록된 기기에서만 쓸 수 있어요.",
    "device_pending": "이 기기는 아직 승인 대기 중이에요.",
    "device_revoked": "이 기기는 회수됐어요. 다시 등록해 주세요.",
}


@dataclass(frozen=True)
class DeviceVerdict:
    code: str | None            # None = 통과
    device: dict | None = None
    touched: bool = False       # last_seen_at 을 방금 갱신했는지 — require_admin 이 이걸 보고 커밋한다.

    @property
    def ok(self) -> bool:
        return self.code is None


def forbidden() -> HTTPException:
    return HTTPException(
        status_code=403, detail={"code": "forbidden", "message": "관리자만 가능해요."}
    )


def device_forbidden(code: str) -> HTTPException:
    return HTTPException(status_code=403, detail={"code": code, "message": DEVICE_MESSAGES[code]})


def hash_device_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def device_token_from(request: Request) -> str | None:
    return request.headers.get(DEVICE_HEADER)


async def require_admin_identity(conn, user_id: str) -> None:
    """관리자인지만 본다 — 기기는 안 본다.

    기기 등록(`POST /admin/devices/register`)·상태 조회(`GET /admin/devices/me`) 두 라우트
    전용이다. 그 둘은 아직 기기가 없는 관리자가 불러야 하는 라우트라 기기를 요구할 수 없다.
    다른 곳에서 쓰지 마라 — test_admin_guard_adoption 이 사용처가 정확히 둘인지 센다."""
    if not await repo.is_admin(conn, user_id):
        raise forbidden()


async def check_device(
    conn, user_id: str, token: str | None, *, now: datetime | None = None
) -> DeviceVerdict:
    """토큰 → 판정. 라우트가 아니라 순수 판정이라 request 없이 테스트한다."""
    token = (token or "").strip()
    if not token:
        return DeviceVerdict("device_missing")
    device = await repo.find_admin_device_by_hash(conn, hash_device_token(token))
    if device is None or device.get("user_id") != user_id:
        return DeviceVerdict("device_unknown")
    status = device.get("status")
    if status == "pending":
        return DeviceVerdict("device_pending", device)
    if status == "revoked":
        return DeviceVerdict("device_revoked", device)
    if status != "approved":
        return DeviceVerdict("device_unknown", device)
    now = now or datetime.now(timezone.utc)
    seen = device.get("last_seen_at")
    touched = False
    if seen is None or now - seen >= DEVICE_TOUCH_INTERVAL:
        await repo.touch_admin_device(conn, device["id"])
        touched = True
    return DeviceVerdict(None, device, touched=touched)


async def require_admin(conn, user_id: str, request: Request) -> None:
    await require_admin_identity(conn, user_id)
    # 기본값 shadow — settings 에 속성이 아예 없는(테스트 더블 등) 드문 경우도 "검사하되
    # 막지 않는다" 쪽으로 떨어져야 한다. off 로 떨어지면 그 경로는 기기 게이트가 있는 줄도
    # 모르고 조용히 뚫린다.
    mode = getattr(request.app.state.settings, "admin_device_gate", "shadow")
    if mode == "off":
        return
    try:
        verdict = await check_device(conn, user_id, device_token_from(request))
    except Exception:
        if mode == "enforce":
            raise  # enforce 는 닫힘 — 조회가 안 되면 그냥 막는다.
        # shadow: admin_devices 조회 자체가 죽어도(예: 마이그레이션이 아직 안 붙은 첫
        # 배포라 테이블이 없음 — 2026-08-29 CI 옛-DB 사고와 같은 결) 막지 않는다. 가드는
        # 라우트의 첫 문장이라 이 시점엔 트랜잭션에 아직 아무 것도 없다 — 롤백해도 잃을 게
        # 없고, 안 하면 이 커넥션이 INERROR 로 남아 라우트 자신의 쿼리마저 "current
        # transaction is aborted" 로 죽는다.
        logger.exception(
            "admin_device_gate %s check failed user=%s path=%s", mode, user_id, request.url.path,
        )
        await conn.rollback()
        return
    if verdict.touched:
        # last_seen_at 터치는 가드가 바로 커밋한다 — psycopg_pool 은 반환되는 INTRANS
        # 커넥션을 롤백하므로, 커밋 없이는 읽기 전용 라우트의 touch 가 항상 유실돼 '마지막
        # 사용' 이 쓰기 요청 때만 움직이게 된다. 가드는 모든 라우트에서 첫 문장이라 이 커밋이
        # 확정하는 건 touch 하나뿐이다.
        await conn.commit()
    if verdict.ok:
        return
    if mode == "enforce":
        raise device_forbidden(verdict.code)
    # shadow — 막지 않고 남긴다. enforce 로 올리기 전에 누가 잠길지 이 로그로 안다.
    logger.warning(
        "admin_device_gate shadow reject user=%s code=%s path=%s",
        user_id, verdict.code, request.url.path,
    )


async def is_admin_user(conn, user_id: str) -> bool:
    """예외 대신 판정만 필요한 호출자(cutover 는 자체 예외 타입을 쓴다).
    기기 게이트는 안 탄다 — 라우트가 아니라 request 가 없다."""
    return await repo.is_admin(conn, user_id)


async def write_audit(
    conn,
    *,
    actor_user_id: str,
    action: str,
    target_type: str,
    target_id: str | None = None,
    before: dict | None = None,
    after: dict | None = None,
    note: str | None = None,
) -> None:
    async with conn.cursor() as cur:
        await cur.execute(
            "insert into admin_audit_log "
            "(actor_user_id, action, target_type, target_id, before, after, note) "
            "values (%s, %s, %s, %s, %s, %s, %s)",
            (
                actor_user_id, action, target_type, target_id,
                Json(before or {}), Json(after or {}), note,
            ),
        )
