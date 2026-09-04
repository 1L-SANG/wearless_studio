"""모델 정지·해제 — 사유 필수, 그리고 콘솔이 verified 를 창조하지 못하는지.

되돌아가면: 관리자가 손으로 '검증됨' 배지를 붙일 수 있게 된다. 그 순간 배지는
생체등록을 통과했다는 뜻이 아니라 누군가 눌렀다는 뜻이 되어, 라이선스 신뢰의 근거가 없다.
"""
import asyncio
import contextlib

import pytest

from app import facemarket_admin


class FakeCursor:
    def __init__(self, store, rows):
        self.store, self.rows, self._row = store, rows, None

    async def execute(self, sql, params=None):
        self.store.append((" ".join(sql.split()), params))
        self._row = self.rows.pop(0) if self.rows else None

    async def fetchone(self):
        return self._row if isinstance(self._row, dict) else None

    async def fetchall(self):
        return self._row if isinstance(self._row, list) else []


class FakeConn:
    def __init__(self, rows):
        self.executed, self.rows, self.commits = [], list(rows), 0

    def cursor(self):
        @contextlib.asynccontextmanager
        async def _cm():
            yield FakeCursor(self.executed, self.rows)

        return _cm()

    async def commit(self):
        self.commits += 1


def test_suspend_requires_a_reason():
    with pytest.raises(Exception) as exc:
        asyncio.run(facemarket_admin.suspend_model(
            FakeConn([]), model_id="m1", actor="admin-1", reason="   ",
        ))
    assert exc.value.detail["code"] == "reason_required"


def test_suspend_records_previous_status_in_audit():
    conn = FakeConn([{"status": "verified"}, None])
    asyncio.run(facemarket_admin.suspend_model(
        conn, model_id="m1", actor="admin-1", reason="본인 요청",
    ))
    audit = [p for sql, p in conn.executed if sql.startswith("insert into admin_audit_log")]
    assert audit, "감사 기록이 없다"
    params = audit[0]
    assert params[1] == "model.suspend"
    assert params[4].obj == {"status": "verified"}, "정지 직전 상태가 안 남으면 복원할 수 없다"
    assert params[6] == "본인 요청"


def test_suspend_404_for_unknown_model():
    with pytest.raises(Exception) as exc:
        asyncio.run(facemarket_admin.suspend_model(
            FakeConn([None]), model_id="nope", actor="admin-1", reason="x",
        ))
    assert exc.value.status_code == 404


def test_unsuspend_restores_the_status_recorded_at_suspension():
    # 1) 현재 상태 조회 → suspended, 2) 마지막 suspend 기록 → verified
    conn = FakeConn([{"status": "suspended"}, {"prev": "verified"}, None])
    asyncio.run(facemarket_admin.unsuspend_model(conn, model_id="m1", actor="admin-1"))
    updates = [p for sql, p in conn.executed if sql.startswith("update fm_models")]
    assert updates and updates[0][0] == "verified"


def test_unsuspend_falls_back_to_pending_without_audit_history():
    conn = FakeConn([{"status": "suspended"}, None, None])
    asyncio.run(facemarket_admin.unsuspend_model(conn, model_id="m1", actor="admin-1"))
    updates = [p for sql, p in conn.executed if sql.startswith("update fm_models")]
    assert updates and updates[0][0] == "pending"


def test_unsuspend_never_restores_a_status_outside_the_schema():
    """원장 값이 오염됐어도 스키마 밖 상태를 쓰지 않는다(check 제약 위반 → 500)."""
    conn = FakeConn([{"status": "suspended"}, {"prev": "superadmin"}, None])
    asyncio.run(facemarket_admin.unsuspend_model(conn, model_id="m1", actor="admin-1"))
    updates = [p for sql, p in conn.executed if sql.startswith("update fm_models")]
    assert updates and updates[0][0] == "pending"


def test_unsuspend_rejects_a_model_that_is_not_suspended():
    conn = FakeConn([{"status": "verified"}])
    with pytest.raises(Exception) as exc:
        asyncio.run(facemarket_admin.unsuspend_model(conn, model_id="m1", actor="admin-1"))
    assert exc.value.detail["code"] == "not_suspended"
