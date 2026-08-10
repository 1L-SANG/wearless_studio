"""lease heartbeat — 살아 있는 잡과 죽은 워커를 구분한다.

무엇이 잘못됐었나
-----------------
`locked_at` 은 claim 때 한 번 쓰이고 그 뒤 아무도 손대지 않았다. `recover_stale_leases` 는
오직 `locked_at` 나이로만 회수를 결정하므로, lease 시간을 넘긴 **정상 작업**과 죽은 워커가
구분되지 않았다. 멀쩡히 돌고 있는 잡을 다른 레플리카가 회수해 가고, 한 번 더 시간이 지나면
그 대체 잡이 종결 오류가 된다.

왜 `_emit` 에 얹지 않았나
-------------------------
처음엔 progress 이벤트에 lease_token 을 실어 보냈다. `emit_job_event` 는 워커 여럿과 시험
65곳이 4-인자로 쓰는 **공유 계약**이라 인자 하나에 89개가 깨졌다. 시험 65개를 고쳐 맞추는
것은 잘못된 수정이다 — heartbeat 는 자기 함수를 갖는다.
"""

import inspect

from app import repo
from app.workers import mannequin_job


def test_the_lease_can_be_renewed():
    assert hasattr(repo, "renew_job_lease")
    src = inspect.getsource(repo.renew_job_lease)
    assert "locked_at = now()" in src


def test_the_renewal_is_ownership_conditioned():
    """lease 를 잃은 옛 워커가 남의 잡 lease 를 늘리면 회수가 영영 안 온다."""
    src = inspect.getsource(repo.renew_job_lease)
    assert "locked_by = %s" in src
    assert "status = 'running'" in src


def test_the_renewal_reports_whether_it_applied():
    src = inspect.getsource(repo.renew_job_lease)
    assert "rowcount > 0" in src


def test_the_shared_event_signature_was_not_changed():
    """공유 계약을 건드리지 않는다 — 이 회귀로 89개가 깨졌었다."""
    from app.workers import _common
    params = list(inspect.signature(_common.emit_job_event).parameters)
    assert params == ["pool", "job_id", "event_type", "payload"], params


def test_a_missing_token_is_a_no_op():
    src = inspect.getsource(mannequin_job._heartbeat)
    assert "if not lease_token:" in src
    assert "return" in src


def test_a_heartbeat_failure_never_breaks_generation():
    """heartbeat 실패가 생성을 막으면 본말이 전도된다."""
    src = inspect.getsource(mannequin_job._heartbeat)
    assert "except Exception" in src


def test_the_long_running_worker_actually_sends_them():
    """가장 오래 도는 잡이 heartbeat 를 안 보내면 이 배선은 장식이다."""
    src = inspect.getsource(mannequin_job.run_mannequin_job)
    assert src.count("_heartbeat(pool, job_id, lease_token)") >= 4, src.count(
        "_heartbeat(pool, job_id, lease_token)")
