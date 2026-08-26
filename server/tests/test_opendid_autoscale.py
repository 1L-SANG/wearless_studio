"""opendid(fm-holder) scale-to-zero — sam reconciler/어댑터를 service·demand·lock_key 만 바꿔
재사용한다. 여기서 고정하는 건 (1) opendid 수요 스냅샷 매핑, (2) 어댑터가 opendid 태그로 찾는지,
(3) 재사용한 reconciler 가 opendid 수요로 양방향 스케일하는지다. want_running 자체는 sam 쪽에서 검증됨."""

import asyncio
from datetime import datetime, timedelta, timezone

from app import repo as repo_mod
from app.services.sam_autoscale import DemandSnapshot, EcsTarget, ServiceState, SamAutoscaleAdapter
from app.workers.sam_autoscaler import SamAutoscaler
from conftest import make_settings

NOW = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
T = EcsTarget(cluster_arn="c", service_arn="s")


# ── (1) 수요 스냅샷 매핑 (fake conn) ──────────────────────────────────────────

class _Cur:
    def __init__(self, row):
        self._row = row

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def execute(self, sql, params=()):
        self._sql = sql

    async def fetchone(self):
        return self._row


class _Conn:
    def __init__(self, row):
        self._row = row

    def cursor(self):
        return _Cur(self._row)


def test_opendid_demand_snapshot_maps_pending_enrollments_to_active():
    row = {"active": 2, "last_finished": NOW - timedelta(minutes=5),
           "last_activity": NOW - timedelta(minutes=1)}
    snap = asyncio.run(repo_mod.opendid_demand_snapshot(_Conn(row)))
    assert snap.active_sam_jobs == 2
    assert snap.last_sam_finished_at == row["last_finished"]
    assert snap.last_upload_at == row["last_activity"]


def test_opendid_demand_snapshot_queries_holder_stage_states():
    captured = {}

    class _CaptureCur(_Cur):
        async def execute(self, sql, params=()):
            captured["sql"] = sql

    class _CaptureConn:
        def cursor(self):
            return _CaptureCur({"active": 0, "last_finished": None, "last_activity": None})

    snap = asyncio.run(repo_mod.opendid_demand_snapshot(_CaptureConn()))
    assert snap.active_sam_jobs == 0
    # 수요 = holder 를 부르는 등록 단계. 이 두 상태가 active 계수의 근거여야 한다.
    assert "license_pending" in captured["sql"]
    assert "vc_pending" in captured["sql"]
    assert "fm_biometric_enrollments" in captured["sql"]


# ── (2) 어댑터가 opendid 태그로 찾는지 ────────────────────────────────────────

def _tags(service):
    return [{"key": "copilot-application", "value": "wearless"},
            {"key": "copilot-environment", "value": "prod"},
            {"key": "copilot-service", "value": service}]


class _Ecs:
    def __init__(self):
        self.updated = []

    def list_clusters(self):
        return {"clusterArns": ["cl"]}

    def list_services(self, cluster):
        return {"serviceArns": ["sam2-arn", "opendid-arn"]}

    def describe_services(self, cluster, services, include=()):
        by = {"sam2-arn": ("sam2", "sam2-arn"), "opendid-arn": ("opendid", "opendid-arn")}
        return {"services": [{"serviceArn": by[a][1], "tags": _tags(by[a][0])}
                             for a in services if a in by]}


def test_adapter_discovers_by_opendid_service_tag():
    s = make_settings(opendid_autoscale="on")
    adapter = SamAutoscaleAdapter(s, service="opendid", enabled_attr="opendid_autoscale",
                                  ecs=_Ecs(), sns=object())
    target = asyncio.run(adapter.discover())
    assert target is not None and target.service_arn == "opendid-arn"


# ── (3) 재사용 reconciler 가 opendid 수요로 스케일 ────────────────────────────

class _Adapter:
    def __init__(self, state, enabled=True):
        self.enabled = enabled
        self.state = state
        self.set_calls = []

    async def discover(self):
        return T

    def forget_target(self):
        pass

    async def describe(self, target):
        return self.state

    async def set_desired(self, target, count):
        self.set_calls.append(count)
        self.state = ServiceState(desired=count, running=self.state.running,
                                  pending=self.state.pending,
                                  oldest_started_at=self.state.oldest_started_at)

    async def notify(self, subject, body):
        pass


class _Repo:
    def __init__(self, snap, lock=True):
        self.snap = snap
        self.lock = lock
        self.lock_keys = []

    async def opendid_demand_snapshot(self, conn):
        return self.snap

    async def sam_demand_snapshot(self, conn, kinds):  # 안 불려야 한다(opendid demand 를 써야 함)
        raise AssertionError("opendid reconciler must not call sam_demand_snapshot")

    async def try_advisory_lock(self, conn, key):
        self.lock_keys.append(key)
        return self.lock


def _opendid_scaler(adapter):
    s = make_settings(opendid_autoscale="on", opendid_autoscale_idle_minutes=30)
    app = type("A", (), {"state": type("S", (), {"settings": s, "pool": None})()})()
    sc = SamAutoscaler(app, adapter,
                       demand_fn=lambda repo, conn: repo.opendid_demand_snapshot(conn),
                       idle_attr="opendid_autoscale_idle_minutes",
                       name="opendid", lock_key="opendid_autoscaler")
    sc._now = lambda: NOW
    return sc


def test_opendid_scales_up_on_vc_pending_demand():
    a = _Adapter(ServiceState(0, 0, 0, None))
    repo = _Repo(DemandSnapshot(active_sam_jobs=1, last_sam_finished_at=None, last_upload_at=None))
    assert asyncio.run(_opendid_scaler(a).reconcile_once(repo, None)) == "up"
    assert a.set_calls == [1]
    assert repo.lock_keys == ["opendid_autoscaler"]  # sam 과 다른 락 — 서로 안 막는다


def test_opendid_scales_down_when_no_holder_demand():
    a = _Adapter(ServiceState(1, 1, 0, NOW - timedelta(minutes=40)))
    quiet = DemandSnapshot(active_sam_jobs=0, last_sam_finished_at=NOW - timedelta(hours=2),
                           last_upload_at=NOW - timedelta(hours=2))
    assert asyncio.run(_opendid_scaler(a).reconcile_once(_Repo(quiet), None)) == "down"
    assert a.set_calls == [0]
