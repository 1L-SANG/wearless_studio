"""모델 정지·해제 — 사유 필수, 그리고 콘솔이 verified 를 창조하지 못하는지.

되돌아가면: 관리자가 손으로 '검증됨' 배지를 붙일 수 있게 된다. 그 순간 배지는
생체등록을 통과했다는 뜻이 아니라 누군가 눌렀다는 뜻이 되어, 라이선스 신뢰의 근거가 없다.

리뷰 후속(finding 1·2): 이중 정지가 복원 체인을 깨는 것, 그리고 동시 요청이 감사 원장의
before 값을 거짓으로 만드는 것 — 둘 다 같은 가드 UPDATE 로 고정한다.
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
    # 가드 UPDATE 가 성공했다는 신호(딕셔너리 행)까지 스크립트로 넣어야 한다 — 가드 없이는
    # 아무 값이나 상관없었지만, 이제 UPDATE 의 반환행을 실제로 읽는다.
    conn = FakeConn([{"status": "verified"}, {"ok": 1}])
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


def test_suspend_rejects_an_already_suspended_model():
    """finding 1: 이미 정지된 모델을 또 정지시키면 감사 원장의 before.status 가 'suspended'
    로 덮여 써진다 — 해제할 때 복원해야 할 진짜 이전 상태(예: verified)가 사라진다."""
    conn = FakeConn([{"status": "suspended"}])
    with pytest.raises(Exception) as exc:
        asyncio.run(facemarket_admin.suspend_model(
            conn, model_id="m1", actor="admin-1", reason="중복 정지 시도",
        ))
    assert exc.value.detail["code"] == "already_suspended"
    assert exc.value.status_code == 409
    # 거절이 읽기 단계에서 끝나야 한다 — 여기까지 왔으면 UPDATE 를 시도조차 안 했어야 한다.
    updates = [p for sql, p in conn.executed if sql.startswith("update fm_models")]
    assert not updates, "이미 정지된 모델인데 UPDATE 를 시도했다"


def test_suspending_twice_in_a_row_rejects_the_second_call():
    """finding 1 을 문자 그대로: 같은 모델을 연달아 두 번 정지시키면 두 번째 호출이 거절된다.

    1차 호출 끝의 None 은 write_audit 자신의 INSERT 가 큐에서 한 자리를 가져가는 몫이다
    (안 넣으면 2차 호출의 진짜 행이 한 칸 밀려 엉뚱한 결과가 나온다).
    """
    conn = FakeConn([
        {"status": "verified"}, {"ok": 1}, None,  # 1차 호출: 조회 → 성공적으로 정지(+감사 소비분)
        {"status": "suspended"},                   # 2차 호출: 조회하니 이미 정지 상태
    ])
    asyncio.run(facemarket_admin.suspend_model(
        conn, model_id="m1", actor="admin-1", reason="R1",
    ))
    with pytest.raises(Exception) as exc:
        asyncio.run(facemarket_admin.suspend_model(
            conn, model_id="m1", actor="admin-1", reason="R2",
        ))
    assert exc.value.detail["code"] == "already_suspended"


def test_suspend_rejects_when_status_changes_between_read_and_write():
    """finding 2: 읽은 직후 다른 요청이 상태를 바꾸면(동시 정지) 가드 UPDATE 가 0-row 가
    되어 충돌로 걸린다 — 조용히 성공한 것처럼 보이면 감사 원장의 before 값이 거짓말을 한다."""
    conn = FakeConn([{"status": "verified"}, None])  # UPDATE 가 아무 행도 못 돌려준다 = 0-row
    with pytest.raises(Exception) as exc:
        asyncio.run(facemarket_admin.suspend_model(
            conn, model_id="m1", actor="admin-1", reason="본인 요청",
        ))
    assert exc.value.detail["code"] == "already_suspended"
    assert exc.value.status_code == 409
    # 0-row 를 성공으로 착각해 감사 기록을 남기면 안 된다.
    audit = [p for sql, p in conn.executed if sql.startswith("insert into admin_audit_log")]
    assert not audit, "충돌인데 감사 기록을 남겼다"


def test_unsuspend_restores_the_status_recorded_at_suspension():
    # 1) 현재 상태 조회 → suspended, 2) 마지막 suspend 기록 → verified, 3) 가드 UPDATE 성공
    conn = FakeConn([{"status": "suspended"}, {"prev": "verified"}, {"ok": 1}])
    asyncio.run(facemarket_admin.unsuspend_model(conn, model_id="m1", actor="admin-1"))
    updates = [p for sql, p in conn.executed if sql.startswith("update fm_models")]
    assert updates and updates[0][0] == "verified"


def test_unsuspend_falls_back_to_pending_without_audit_history():
    conn = FakeConn([{"status": "suspended"}, None, {"ok": 1}])
    asyncio.run(facemarket_admin.unsuspend_model(conn, model_id="m1", actor="admin-1"))
    updates = [p for sql, p in conn.executed if sql.startswith("update fm_models")]
    assert updates and updates[0][0] == "pending"


def test_unsuspend_never_restores_a_status_outside_the_schema():
    """원장 값이 오염됐어도 스키마 밖 상태를 쓰지 않는다(check 제약 위반 → 500)."""
    conn = FakeConn([{"status": "suspended"}, {"prev": "superadmin"}, {"ok": 1}])
    asyncio.run(facemarket_admin.unsuspend_model(conn, model_id="m1", actor="admin-1"))
    updates = [p for sql, p in conn.executed if sql.startswith("update fm_models")]
    assert updates and updates[0][0] == "pending"


def test_unsuspend_rejects_a_model_that_is_not_suspended():
    conn = FakeConn([{"status": "verified"}])
    with pytest.raises(Exception) as exc:
        asyncio.run(facemarket_admin.unsuspend_model(conn, model_id="m1", actor="admin-1"))
    assert exc.value.detail["code"] == "not_suspended"


def test_unsuspend_rejects_when_status_changes_between_read_and_write():
    """finding 2: 확인 시점엔 suspended 였지만 가드 UPDATE 순간엔 이미 다른 요청이 먼저
    해제해 버렸으면(동시 해제) 0-row 로 걸린다 — 조용한 이중 성공을 만들지 않는다."""
    conn = FakeConn([{"status": "suspended"}, {"prev": "verified"}, None])
    with pytest.raises(Exception) as exc:
        asyncio.run(facemarket_admin.unsuspend_model(conn, model_id="m1", actor="admin-1"))
    assert exc.value.detail["code"] == "not_suspended"
    audit = [p for sql, p in conn.executed if sql.startswith("insert into admin_audit_log")]
    assert not audit, "충돌인데 감사 기록을 남겼다"


def test_restore_chain_survives_suspend_unsuspend_suspend_unsuspend_cycle():
    """finding 1 회귀 방지: 정상적인 정지→해제→정지→해제 반복에서 verified 배지가 매번
    정확히 복원되는지 — 가드가 정상 사이클까지 막아버리면 안 된다.

    호출마다 None 자리가 하나씩 끼어 있다 — write_audit 도 자기 커서로 INSERT 를 한 번
    더 실행해서 큐에서 한 자리를 가져간다(읽지는 않지만 소비는 한다). 안 넣으면 다음
    호출의 진짜 행이 한 칸씩 밀린다.
    """
    conn = FakeConn([
        {"status": "verified"}, {"ok": 1}, None,                       # 1차 정지(+감사 소비분)
        {"status": "suspended"}, {"prev": "verified"}, {"ok": 1}, None,  # 1차 해제(+감사 소비분)
        {"status": "verified"}, {"ok": 1}, None,                       # 2차 정지(+감사 소비분)
        {"status": "suspended"}, {"prev": "verified"}, {"ok": 1},      # 2차 해제
    ])
    r1 = asyncio.run(facemarket_admin.suspend_model(
        conn, model_id="m1", actor="admin-1", reason="R1",
    ))
    r2 = asyncio.run(facemarket_admin.unsuspend_model(conn, model_id="m1", actor="admin-1"))
    r3 = asyncio.run(facemarket_admin.suspend_model(
        conn, model_id="m1", actor="admin-1", reason="R2",
    ))
    r4 = asyncio.run(facemarket_admin.unsuspend_model(conn, model_id="m1", actor="admin-1"))

    assert (r1["status"], r2["status"], r3["status"], r4["status"]) == (
        "suspended", "verified", "suspended", "verified",
    )
    # 두 번째 정지의 before 도 여전히 verified 여야 한다 — 'suspended' 로 오염되면 안 된다.
    suspend_audits = [
        p for sql, p in conn.executed
        if sql.startswith("insert into admin_audit_log") and p[1] == "model.suspend"
    ]
    assert len(suspend_audits) == 2
    assert suspend_audits[1][4].obj == {"status": "verified"}, "두 번째 정지의 이전 상태가 오염됐다"
