"""켜지는 중 무한 대기 방지 — 유예를 넘겨도 안 뜨면 내린다.

reconciler 규칙 "running==0 이면 켜는 중이라 건드리지 않는다" 는 ECS 전제였다. ECS 는 태스크가
못 뜨면 스스로 재시도하지만 RunPod 파드는 켠 채로 아무것도 안 할 수 있다(컨테이너가 죽어도
desiredStatus 는 RUNNING — 2026-09-10 실측). 그러면 수요가 사라져도 영원히 안 꺼지고 요금만 나간다.

유예가 설정되지 않은 서비스(sam2·opendid·detail-worker)는 동작이 한 줄도 바뀌지 않아야 한다.
"""

import asyncio
import types
from dataclasses import replace

import pytest

from app.services.sam_autoscale import DemandSnapshot, ServiceState
from app.workers.sam_autoscaler import SamAutoscaler
from conftest import make_settings

TARGET = object()


class _Adapter:
    enabled = True

    def __init__(self, state):
        self.state = state
        self.scaled = []
        self.alerts = []

    async def discover(self):
        return TARGET

    def forget_target(self):
        return None

    async def describe(self, target):
        return self.state

    async def set_desired(self, target, count):
        self.scaled.append(count)

    async def notify(self, subject, body):
        self.alerts.append(subject)


class _Repo:
    async def try_advisory_lock(self, conn, key):
        return True


def _app(**over):
    base = {"gemini_api_key": "x", "r2_bucket": "b"}
    base.update(over)
    return types.SimpleNamespace(state=types.SimpleNamespace(settings=make_settings(**base)))


def _scaler(app, adapter, *, grace_attr="face_autoscale_start_grace_minutes", demand=None):
    snap = demand or DemandSnapshot(0, None, None)   # 기본 = 수요 없음
    return SamAutoscaler(app, adapter, demand_fn=lambda repo, conn: _wrap(snap),
                         idle_attr="face_autoscale_idle_minutes", name="face-render",
                         lock_key="face_autoscaler", start_grace_attr=grace_attr)


async def _wrap(v):
    return v


def _run(scaler):
    return asyncio.run(scaler.reconcile_once(_Repo(), None))


def _clock(scaler, seconds):
    """단조시계를 앞으로 감는다 — 테스트가 실제로 기다리지 않게."""
    if scaler._starting_since is not None:
        scaler._starting_since -= seconds


def test_starting_pod_is_left_alone_inside_the_grace():
    adapter = _Adapter(ServiceState(desired=1, running=0, pending=1, oldest_started_at=None))
    scaler = _scaler(_app(face_autoscale_start_grace_minutes=8), adapter)
    assert _run(scaler) == "skip"          # 아직 유예 안(콜드스타트를 버리지 않는다)
    _clock(scaler, 7 * 60)
    assert _run(scaler) == "skip"
    assert adapter.scaled == [] and adapter.alerts == []


def test_stalled_start_is_scaled_down_and_alerted():
    adapter = _Adapter(ServiceState(desired=1, running=0, pending=1, oldest_started_at=None))
    scaler = _scaler(_app(face_autoscale_start_grace_minutes=8), adapter)
    assert _run(scaler) == "skip"
    _clock(scaler, 9 * 60)                 # 유예 초과
    assert _run(scaler) == "down"
    assert adapter.scaled == [0]
    assert adapter.alerts and "never became healthy" in adapter.alerts[0]


def test_healthy_pod_resets_the_start_clock():
    state = ServiceState(desired=1, running=0, pending=1, oldest_started_at=None)
    adapter = _Adapter(state)
    scaler = _scaler(_app(face_autoscale_start_grace_minutes=8), adapter)
    _run(scaler)
    _clock(scaler, 9 * 60)
    adapter.state = ServiceState(desired=1, running=1, pending=0, oldest_started_at=None)
    assert _run(scaler) == "down"          # 떴고 수요는 없다 → 정상 종료 경로
    assert scaler._starting_since is None
    assert adapter.alerts == []            # 기동 실패 알림은 없다


def test_demand_present_keeps_the_pod_even_when_stalled():
    """수요가 있으면 죽이지 않는다 — 내려도 곧바로 다시 켜야 해서 무의미하다."""
    adapter = _Adapter(ServiceState(desired=1, running=0, pending=1, oldest_started_at=None))
    scaler = _scaler(_app(face_autoscale_start_grace_minutes=8), adapter,
                     demand=DemandSnapshot(3, None, None))
    _run(scaler)
    _clock(scaler, 30 * 60)
    assert _run(scaler) == "noop"
    assert adapter.scaled == []


@pytest.mark.parametrize("grace_attr,minutes", [(None, 8), ("face_autoscale_start_grace_minutes", 0)])
def test_services_without_a_grace_behave_exactly_as_before(grace_attr, minutes):
    """sam2·opendid·detail-worker(유예 미설정) 회귀 고정 — 몇 시간이 지나도 skip."""
    adapter = _Adapter(ServiceState(desired=1, running=0, pending=1, oldest_started_at=None))
    scaler = _scaler(_app(face_autoscale_start_grace_minutes=minutes), adapter, grace_attr=grace_attr)
    assert _run(scaler) == "skip"
    _clock(scaler, 6 * 3600)
    assert _run(scaler) == "skip"
    assert adapter.scaled == [] and adapter.alerts == []


def test_grace_default_and_env_name():
    s = make_settings(gemini_api_key="x", r2_bucket="b")
    assert s.face_autoscale_start_grace_minutes == 8
    assert replace(s, face_autoscale_start_grace_minutes=14).face_autoscale_start_grace_minutes == 14
