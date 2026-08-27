"""디스패처가 잡을 N개까지 동시에 돌린다 — 동시 사용자 상한을 푸는 계약.

이 파일이 고정하는 것은 "몇 배 빨라지는가"가 아니라 **동시성을 켰을 때 깨지면 안 되는
것들**이다. 프로덕션 실측(2026-08-27)에서 워커 태스크는 CPU 3~16%, 메모리 21% 로 놀고
있는데 잡을 하나씩만 처리해 동시 사용자가 4명에서 막혀 있었다.

동시 실행이 새로 만드는 위험 셋을 각각 테스트로 못 박는다.
  1. 비용 귀속 — image_usage 는 ContextVar 라, 스코프를 루프에서 잡으면 병렬 잡끼리 덮어쓴다
  2. 예외 격리 — 지금은 워커 예외가 루프까지 올라가 디스패처 한 바퀴가 통째로 날아간다
                 (2026-08-26 프로덕션에서 UniqueViolation 3회로 실제 발생)
  3. 드레인    — 종료 시 실행 중인 잡을 기다려야 한다. 안 기다리면 배포마다 잡이 죽는다
                 (2026-08-27 실측: 배포가 겹쳐 8컷 만든 잡이 죽고 $1.24 가 날아갔다)
"""
import asyncio
import contextlib
import types

import pytest

from app import image_usage
from app.workers.dispatcher import JobDispatcher


class _Conn:
    async def commit(self):
        return None

    async def rollback(self):
        return None


class _Pool:
    @contextlib.asynccontextmanager
    async def connection(self):
        yield _Conn()


def _app(settings=None):
    s = types.SimpleNamespace(
        job_worker_id="test-worker",
        job_poll_interval_seconds=0.01,
        job_lease_timeout_seconds=900,
        facemarket_enabled=False,
    )
    for k, v in (settings or {}).items():
        setattr(s, k, v)
    return types.SimpleNamespace(state=types.SimpleNamespace(settings=s, pool=_Pool()))


def _queue(n, kind="mannequin"):
    """claim_next_job 대역이 순서대로 내줄 잡 n개. 소진되면 None(=큐 빔)."""
    return [
        {"id": f"job-{i}", "kind": kind, "user_id": f"u-{i}", "lease_token": f"t-{i}"}
        for i in range(n)
    ]


def _wire(monkeypatch, jobs, worker):
    """claim/lease/sweep 을 대역으로 바꾸고 worker 하나만 남긴다."""
    from app.workers import dispatcher as mod

    pending = list(jobs)

    async def fake_claim(conn, kinds, worker_id):
        return pending.pop(0) if pending else None

    async def fake_recover(conn, timeout, kinds):
        return []

    async def fake_renew(conn, job_id, token):
        return True

    monkeypatch.setattr(mod.repo, "claim_next_job", fake_claim)
    monkeypatch.setattr(mod.repo, "recover_stale_leases", fake_recover)
    monkeypatch.setattr(mod.repo, "renew_job_lease", fake_renew)
    monkeypatch.setattr(mod.repo, "list_unsettled_errored_jobs",
                        lambda conn: _empty())
    monkeypatch.setitem(mod._WORKERS, "mannequin", worker)
    return pending


async def _empty():
    return []


async def _drain(dispatcher, done_flag, timeout=5.0):
    """잡이 다 끝날 때까지 기다렸다가 디스패처를 멈춘다."""
    try:
        await asyncio.wait_for(done_flag.wait(), timeout=timeout)
    finally:
        await dispatcher.stop()


# ── 1. 동시 실행 수가 N 을 넘지 않는다 ────────────────────────────────────────

def test_dispatcher_runs_jobs_concurrently_up_to_limit(monkeypatch):
    """N=3 이면 잡 5개를 던져도 동시 실행은 3을 넘지 않고, 5개 전부 완주한다."""
    N = 3
    live = 0
    peak = 0
    finished = []
    all_done = asyncio.Event()

    async def worker(app, job):
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        try:
            await asyncio.sleep(0.05)
        finally:
            live -= 1
        finished.append(job["id"])
        if len(finished) == 5:
            all_done.set()

    async def run():
        _wire(monkeypatch, _queue(5), worker)
        app = _app({"job_concurrency": N})
        d = JobDispatcher(app, kinds=("mannequin",))
        await d.start()
        await _drain(d, all_done)

    asyncio.run(run())
    assert len(finished) == 5, f"완주 못 함: {finished}"
    assert peak > 1, "동시 실행이 전혀 안 일어났다 — 여전히 직렬이다"
    assert peak <= N, f"동시 실행 {peak} 이 상한 {N} 을 넘었다"


def test_dispatcher_defaults_to_serial(monkeypatch):
    """기본값은 1 — 설정을 안 켜면 지금 동작(직렬) 그대로다."""
    live = 0
    peak = 0
    finished = []
    all_done = asyncio.Event()

    async def worker(app, job):
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        try:
            await asyncio.sleep(0.02)
        finally:
            live -= 1
        finished.append(job["id"])
        if len(finished) == 3:
            all_done.set()

    async def run():
        _wire(monkeypatch, _queue(3), worker)
        d = JobDispatcher(_app(), kinds=("mannequin",))   # job_concurrency 미설정
        await d.start()
        await _drain(d, all_done)

    asyncio.run(run())
    assert peak == 1, f"기본값인데 동시 실행 {peak} — 배포만 해도 동작이 바뀐다"
    assert len(finished) == 3


# ── 2. 한 잡이 터져도 나머지가 산다 ──────────────────────────────────────────

def test_worker_exception_does_not_kill_the_loop(monkeypatch):
    """워커 하나가 예외를 던져도 디스패처가 남은 잡을 계속 처리한다.

    회귀 대상(2026-08-26 프로덕션): sam_preprocess 의 UniqueViolation 이 루프의 except 까지
    올라가 디스패처가 한 바퀴를 통째로 건너뛰었다. 동시 실행에서는 그 피해가 N 배가 된다.
    """
    finished = []
    all_done = asyncio.Event()

    async def worker(app, job):
        if job["id"] == "job-1":
            raise RuntimeError("boom")
        finished.append(job["id"])
        if len(finished) == 3:
            all_done.set()

    async def run():
        _wire(monkeypatch, _queue(4), worker)
        d = JobDispatcher(_app({"job_concurrency": 2}), kinds=("mannequin",))
        await d.start()
        await _drain(d, all_done)

    asyncio.run(run())
    assert set(finished) == {"job-0", "job-2", "job-3"}, (
        f"터진 잡 하나가 나머지를 막았다: {finished}")


# ── 3. 비용 귀속이 잡끼리 안 섞인다 ──────────────────────────────────────────

def test_image_usage_scope_is_isolated_per_job(monkeypatch):
    """동시에 도는 잡 둘이 서로의 job_id 로 비용을 기록하지 않는다.

    image_usage._ctx 는 ContextVar 다. 지금처럼 루프에서 job_scope 를 잡으면 두 잡이
    같은 컨텍스트를 덮어써 실비가 엉뚱한 잡에 붙는다 — 잡별 원가 집계가 조용히 깨진다.
    """
    seen = {}
    started = asyncio.Event()
    all_done = asyncio.Event()
    finished = []

    async def worker(app, job):
        if job["id"] == "job-0":
            started.set()
            await asyncio.sleep(0.05)          # job-1 이 그 사이에 스코프를 잡게 둔다
        else:
            await started.wait()
        seen[job["id"]] = image_usage.current_job_id()
        finished.append(job["id"])
        if len(finished) == 2:
            all_done.set()

    async def run():
        _wire(monkeypatch, _queue(2), worker)
        d = JobDispatcher(_app({"job_concurrency": 2}), kinds=("mannequin",))
        await d.start()
        await _drain(d, all_done)

    asyncio.run(run())
    assert seen == {"job-0": "job-0", "job-1": "job-1"}, (
        f"비용 귀속이 섞였다: {seen}")


# ── 4. 종료가 실행 중인 잡을 기다린다 ────────────────────────────────────────

def test_stop_drains_running_jobs(monkeypatch):
    """stop() 은 실행 중인 잡이 끝날 때까지 기다린다.

    회귀 대상(2026-08-27 실측): 배포가 겹쳐 컷 8장을 만든 잡이 중간에 죽고 $1.24 가
    날아갔다. 드레인이 없으면 배포마다 이 일이 반복되고, 동시 실행에서는 N 배가 된다.
    """
    completed = []
    started = asyncio.Event()

    async def worker(app, job):
        started.set()
        await asyncio.sleep(0.15)
        completed.append(job["id"])

    async def run():
        _wire(monkeypatch, _queue(1), worker)
        d = JobDispatcher(_app({"job_concurrency": 2}), kinds=("mannequin",))
        await d.start()
        await asyncio.wait_for(started.wait(), timeout=2.0)
        await d.stop()

    asyncio.run(run())
    assert completed == ["job-0"], "stop() 이 실행 중인 잡을 버리고 끝났다"
