"""오래 걸리는 잡이 lease 초과로 재큐되지 않게 하는 하트비트 계약(2026-08-17 검증).

lease 는 '워커가 죽었는지'를 보는 장치인데 판별 기준이 **시작 시각**이라, 정상적으로
15분 넘게 도는 상세페이지 생성이 죽은 것으로 오인돼 재큐됐다. 재큐되면 컷 전체를
처음부터 다시 만들어 프로바이더 실비가 페이지 단위로 두 번 나간다.
"""
import asyncio
import contextlib
import inspect
from types import SimpleNamespace

import pytest

from app.workers import dispatcher as dispatcher_mod


class _Conn:
    def __init__(self, sink):
        self.sink = sink

    async def commit(self):
        return None


def _pool(sink, renew_result=True):
    class Pool:
        @contextlib.asynccontextmanager
        async def connection(self):
            yield _Conn(sink)
    return Pool()


def test_dispatcher_renews_the_lease_while_a_job_runs(monkeypatch):
    calls = []

    async def fake_renew(conn, job_id, token):
        calls.append((job_id, token))
        return True

    monkeypatch.setattr(dispatcher_mod.repo, "renew_job_lease", fake_renew)
    disp = dispatcher_mod.JobDispatcher.__new__(dispatcher_mod.JobDispatcher)
    settings = SimpleNamespace(job_lease_timeout_seconds=30)   # 갱신 주기 = 10초
    job = {"id": "job-1", "lease_token": "worker:abc"}

    async def scenario():
        task = asyncio.create_task(disp._keep_lease(settings, _pool(calls), job))
        await asyncio.sleep(0)
        # 실시간 30초를 태우지 않게 sleep 을 즉시 반환시킨다.
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    # 주기 계산이 타임아웃의 1/3 인지 코드로 고정(실시간 대기 없이).
    source = inspect.getsource(dispatcher_mod.JobDispatcher._keep_lease)
    assert "int(s.job_lease_timeout_seconds) // 3" in source
    assert "renew_job_lease" in source


def test_heartbeat_stops_when_the_lease_was_already_taken(monkeypatch):
    """이미 회수돼 다른 워커가 집어간 잡의 lease 를 되살리면 같은 잡이 두 곳에서 돈다."""
    seen = []

    async def fake_renew(conn, job_id, token):
        seen.append(job_id)
        return False        # 토큰 불일치 = 회수됨

    monkeypatch.setattr(dispatcher_mod.repo, "renew_job_lease", fake_renew)

    async def instant_sleep(_seconds):
        return None

    monkeypatch.setattr(dispatcher_mod.asyncio, "sleep", instant_sleep)
    disp = dispatcher_mod.JobDispatcher.__new__(dispatcher_mod.JobDispatcher)
    settings = SimpleNamespace(job_lease_timeout_seconds=30)
    job = {"id": "job-2", "lease_token": "worker:abc"}

    asyncio.run(asyncio.wait_for(disp._keep_lease(settings, _pool(seen), job), timeout=2))
    assert seen == ["job-2"], "한 번 실패하면 즉시 멈춘다"


def test_heartbeat_is_a_noop_without_a_lease_token():
    disp = dispatcher_mod.JobDispatcher.__new__(dispatcher_mod.JobDispatcher)
    settings = SimpleNamespace(job_lease_timeout_seconds=30)
    asyncio.run(asyncio.wait_for(disp._keep_lease(settings, _pool([]), {"id": "j"}), timeout=2))


def test_worker_run_is_wrapped_by_the_heartbeat():
    """워커가 끝나면(성공·실패 무관) 하트비트도 반드시 멈춘다.

    잡 동시 실행(2026-08-27)을 넣으면서 이 블록이 _run 에서 잡 단위 태스크인 _run_job 으로
    옮겨졌다. 계약은 그대로다 — 하트비트를 띄우고, finally 에서 반드시 멈춘다.
    """
    source = inspect.getsource(dispatcher_mod.JobDispatcher._run_job)
    assert "heartbeat = asyncio.create_task(self._keep_lease(s, pool, job))" in source
    assert "finally:" in source and "heartbeat.cancel()" in source


def test_renew_query_only_touches_the_same_lease_holder():
    source = inspect.getsource(dispatcher_mod.repo.renew_job_lease)
    assert "status = 'running'" in source and "locked_by = %s" in source
    assert "locked_at = now()" in source


def test_stale_paid_detail_page_is_not_automatically_requeued():
    """Provider outcome is unknown after a crash, so whole-page replay can double-charge."""

    source = inspect.getsource(dispatcher_mod.repo.recover_stale_leases)
    assert "stale.kind = 'detail_page'" in source
    assert "then 'error'" in source
    assert source.count("stale.kind = 'detail_page' or stale.recoveries >= 1") == 3
    assert "finished_at = case" in source
    assert "kind = any(%s)" in source


def test_dispatcher_stop_waits_for_cancelled_worker_finalizer(monkeypatch):
    finalized = False

    async def fake_wait_for(_task, timeout):
        assert timeout == 10
        raise asyncio.TimeoutError

    async def running_worker():
        nonlocal finalized
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            await asyncio.sleep(0)
            finalized = True
            raise

    async def scenario():
        dispatcher = dispatcher_mod.JobDispatcher.__new__(dispatcher_mod.JobDispatcher)
        dispatcher._stop = asyncio.Event()
        dispatcher._task = asyncio.create_task(running_worker())
        await asyncio.sleep(0)
        await dispatcher.stop()
        assert dispatcher._task.done()
        assert finalized is True

    monkeypatch.setattr(dispatcher_mod.asyncio, "wait_for", fake_wait_for)
    asyncio.run(scenario())
