"""Reconciler — 60초마다 want 를 계산해 ECS 와 맞춘다(양방향). 훅은 지름길일 뿐이다."""

import asyncio
from datetime import datetime, timedelta, timezone

from app.services.sam_autoscale import DemandSnapshot, EcsTarget, ServiceState
from app.workers import sam_autoscaler as mod
from app.workers.sam_autoscaler import SamAutoscaler
from conftest import make_settings

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
T = EcsTarget(cluster_arn="c", service_arn="s")


class _Adapter:
    def __init__(self, state, target=T, enabled=True):
        self.enabled = enabled
        self._target = target
        self.state = state
        self.set_calls = []
        self.notices = []
        self.forgot = 0

    async def discover(self):
        return self._target

    def forget_target(self):
        self.forgot += 1

    async def describe(self, target):
        return self.state

    async def set_desired(self, target, count):
        self.set_calls.append(count)
        self.state = ServiceState(desired=count, running=self.state.running,
                                  pending=self.state.pending,
                                  oldest_started_at=self.state.oldest_started_at)

    async def notify(self, subject, body):
        self.notices.append(subject)


class _Repo:
    def __init__(self, snap, lock=True):
        self.snap = snap
        self.lock = lock

    async def sam_demand_snapshot(self, conn, kinds):
        return self.snap

    async def try_advisory_lock(self, conn, key):
        return self.lock


def _busy():
    return DemandSnapshot(active_sam_jobs=1, last_sam_finished_at=None, last_upload_at=None)


def _quiet():
    return DemandSnapshot(active_sam_jobs=0, last_sam_finished_at=NOW - timedelta(hours=2),
                          last_upload_at=NOW - timedelta(hours=2))


def _scaler(adapter, **over):
    values = {"sam_autoscale": "on"}
    values.update(over)
    s = make_settings(**values)
    app = type("A", (), {"state": type("S", (), {"settings": s, "pool": None})()})()
    sc = SamAutoscaler(app, adapter)
    sc._now = lambda: NOW
    return sc


def _run(sc, repo):
    return asyncio.run(sc.reconcile_once(repo, None))


# ── 기본 방향 ──

def test_it_scales_up_when_demand_exists_and_service_is_down():
    a = _Adapter(ServiceState(0, 0, 0, None))
    assert _run(_scaler(a), _Repo(_busy())) == "up"
    assert a.set_calls == [1]


def test_it_scales_down_when_quiet_and_service_is_up():
    a = _Adapter(ServiceState(1, 1, 0, NOW - timedelta(minutes=40)))
    assert _run(_scaler(a), _Repo(_quiet())) == "down"
    assert a.set_calls == [0]


def test_it_does_nothing_when_already_correct():
    a = _Adapter(ServiceState(1, 1, 0, NOW))
    assert _run(_scaler(a), _Repo(_busy())) == "noop"
    assert a.set_calls == []


def test_it_repairs_a_failed_hook_by_scaling_up_itself():
    """훅이 실패해 0 으로 남아 있어도 reconciler 가 올린다 — 훅은 지름길일 뿐이다."""
    a = _Adapter(ServiceState(0, 0, 0, None))
    snap = DemandSnapshot(active_sam_jobs=0, last_sam_finished_at=None,
                          last_upload_at=NOW - timedelta(minutes=1))
    assert _run(_scaler(a), _Repo(snap)) == "up"


# ── 켜는 중 보호 ──

def test_it_never_scales_down_while_a_task_is_starting():
    """켜는 중에 내리면 콜드스타트를 버린다 — 다음 주기에 다시 본다.

    실측(2026-08-21): desired=1 요청 후 **첫 13~19초는 pending=0 running=0** 이다
    (PROVISIONING 전). pending>0 만 보면 그 창에서 내려버린다. 판정은 desired>0 and running==0.
    """
    for state in (ServiceState(1, 0, 0, None),     # 요청 직후 — pending 아직 0
                  ServiceState(1, 0, 1, None)):    # PROVISIONING~ACTIVATING
        a = _Adapter(state)
        assert _run(_scaler(a), _Repo(_quiet())) == "skip"
        assert a.set_calls == []


# ── 동시성·탐색 ──

def test_it_skips_when_another_process_holds_the_lock():
    a = _Adapter(ServiceState(0, 0, 0, None))
    assert _run(_scaler(a), _Repo(_busy(), lock=False)) == "skip"
    assert a.set_calls == []


def test_it_disables_itself_when_discovery_fails_and_alerts_once():
    a = _Adapter(ServiceState(0, 0, 0, None), target=None)
    sc = _scaler(a)
    assert _run(sc, _Repo(_busy())) == "skip"
    assert _run(sc, _Repo(_busy())) == "skip"
    assert a.notices == ["sam2 autoscale: service not found"]


def test_service_not_found_on_describe_forgets_the_cached_target():
    """스택 재생성 — 캐시된 ARN 이 죽었으면 다음 주기에 한 번 더 찾는다."""
    class _Gone(_Adapter):
        async def describe(self, target):
            raise RuntimeError("ServiceNotFoundException: Service not found.")
    a = _Gone(ServiceState(0, 0, 0, None))
    assert _run(_scaler(a), _Repo(_busy())) == "skip"
    assert a.forgot == 1


# ── 알림 ──

def test_it_alerts_once_per_long_run():
    old = NOW - timedelta(hours=mod.LONG_RUN_ALERT_HOURS, minutes=1)
    a = _Adapter(ServiceState(1, 1, 0, old))
    sc = _scaler(a)
    _run(sc, _Repo(_busy()))
    _run(sc, _Repo(_busy()))
    assert a.notices == ["sam2 autoscale: running over 3h"]


def test_long_run_alert_is_keyed_by_task_start_so_a_redeploy_alerts_again():
    """래치를 '내가 내렸는가'로 풀면 외부 재배포로 태스크가 바뀐 새 가동의 알림이 묻힌다."""
    first = NOW - timedelta(hours=mod.LONG_RUN_ALERT_HOURS, minutes=1)
    a = _Adapter(ServiceState(1, 1, 0, first))
    sc = _scaler(a)
    _run(sc, _Repo(_busy()))                                      # 알림 1 (first)
    a.state = ServiceState(1, 1, 0, first - timedelta(hours=5))    # 재배포 → 다른 startedAt
    _run(sc, _Repo(_busy()))                                      # 알림 2 (new run)
    assert a.notices.count("sam2 autoscale: running over 3h") == 2


def test_long_run_does_not_alert_when_demand_is_gone():
    """3시간 넘었어도 수요가 없으면 어차피 이번 주기에 내린다 — 알림은 소음이다."""
    old = NOW - timedelta(hours=mod.LONG_RUN_ALERT_HOURS, minutes=1)
    a = _Adapter(ServiceState(1, 1, 0, old))
    assert _run(_scaler(a), _Repo(_quiet())) == "down"
    assert a.notices == []


def test_scale_failure_alerts_and_does_not_raise():
    class _Boom(_Adapter):
        async def set_desired(self, target, count):
            raise RuntimeError("AccessDenied")
    a = _Boom(ServiceState(0, 0, 0, None))
    assert _run(_scaler(a), _Repo(_busy())) == "skip"
    assert a.notices == ["sam2 autoscale: scale to 1 failed"]


def test_scale_failure_alert_is_debounced_for_ten_minutes():
    class _Boom(_Adapter):
        async def set_desired(self, target, count):
            raise RuntimeError("AccessDenied")
    a = _Boom(ServiceState(0, 0, 0, None))
    sc = _scaler(a)
    for _ in range(3):                                            # 60초 간격 3회 → 알림 1회
        _run(sc, _Repo(_busy()))
    assert a.notices == ["sam2 autoscale: scale to 1 failed"]


def test_alert_failure_never_breaks_the_reconcile():
    class _Deaf(_Adapter):
        async def notify(self, subject, body):
            raise RuntimeError("sns down")
        async def set_desired(self, target, count):
            raise RuntimeError("AccessDenied")
    a = _Deaf(ServiceState(0, 0, 0, None))
    assert _run(_scaler(a), _Repo(_busy())) == "skip"             # 예외 없음


# ── prewarm 훅 ──

def test_prewarm_scales_up_only_when_down_and_debounces():
    a = _Adapter(ServiceState(0, 0, 0, None))
    sc = _scaler(a)
    asyncio.run(sc.prewarm())
    asyncio.run(sc.prewarm())
    asyncio.run(sc.prewarm())
    assert a.set_calls == [1], "60초 안 반복 호출은 AWS 를 한 번만 부른다"


def test_prewarm_is_a_noop_when_already_up():
    a = _Adapter(ServiceState(1, 1, 0, NOW))
    asyncio.run(_scaler(a).prewarm())
    assert a.set_calls == []


def test_prewarm_swallows_aws_errors():
    class _Boom(_Adapter):
        async def describe(self, target):
            raise RuntimeError("throttled")
    asyncio.run(_scaler(_Boom(ServiceState(0, 0, 0, None))).prewarm())   # 예외 없음


def test_prewarm_soon_keeps_a_reference_until_done():
    """라우트용 fire-and-forget — 참조를 set 에 들고 있어야 GC 에 안 먹힌다."""
    a = _Adapter(ServiceState(0, 0, 0, None))
    sc = _scaler(a)

    async def _go():
        sc.prewarm_soon()
        assert len(sc._inflight) == 1
        await asyncio.gather(*sc._inflight)
    asyncio.run(_go())
    assert a.set_calls == [1]
    assert sc._inflight == set()


def test_disabled_scaler_is_inert():
    a = _Adapter(ServiceState(0, 0, 0, None), enabled=False)
    sc = _scaler(a, sam_autoscale="off")
    assert _run(sc, _Repo(_busy())) == "skip"
    asyncio.run(sc.prewarm())
    sc.prewarm_soon()
    assert a.set_calls == []
    assert sc._inflight == set()
