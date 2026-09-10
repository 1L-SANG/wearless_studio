"""얼굴 패스 GPU 온디맨드 — RunPod 어댑터와 수요 판정.

판정(want_count)·reconciler 는 sam2 것을 그대로 쓴다(그쪽 테스트가 본다).
여기서 고정하는 건 **RunPod 쪽 차이**와 **수요를 어디서 세는가** 둘이다.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.services import sam_autoscale
from app.services.face_autoscale import (
    FACE_KINDS,
    USER_AGENT,
    RunpodAutoscaleAdapter,
    RunpodTarget,
    face_demand_snapshot,
)
from conftest import make_settings

POD = "pod-abc123"


def _settings(**kw):
    base = {"gemini_api_key": "x", "r2_bucket": "b"}
    base.update(kw)
    return make_settings(**base)


class _FakeRes:
    def __init__(self, payload):
        self._payload = payload
        self.content = b"{}"

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, pod=None):
        self.pod = pod or {"desiredStatus": "EXITED"}
        self.gets, self.posts = [], []

    def get(self, path):
        self.gets.append(path)
        return _FakeRes(self.pod)

    def post(self, path):
        self.posts.append(path)
        return _FakeRes({})


# ── 설정 ──
def test_default_is_off_and_makes_no_client():
    s = _settings()
    assert s.face_autoscale == "off"
    assert s.face_autoscale_idle_minutes == 30
    adapter = RunpodAutoscaleAdapter(s)
    assert adapter.enabled is False
    assert asyncio.run(adapter.discover()) is None
    assert adapter._client is None


def test_enabled_without_pod_or_key_stays_disabled_instead_of_guessing():
    # 이름으로 파드를 고르지 않는다 — 계정의 다른 파드를 끌 수 있다.
    adapter = RunpodAutoscaleAdapter(_settings(face_autoscale="on"))
    assert adapter.enabled is True
    assert asyncio.run(adapter.discover()) is None


def test_discover_uses_configured_pod_id():
    adapter = RunpodAutoscaleAdapter(
        _settings(face_autoscale="on", face_runpod_pod_id=POD, face_runpod_api_key="k"),
        client=_FakeClient())
    assert asyncio.run(adapter.discover()) == RunpodTarget(POD)


def test_http_client_carries_auth_and_user_agent():
    """UA 없는 요청이 거부된 실측이 있다(2026-09-08 워치독 stop 실패)."""
    adapter = RunpodAutoscaleAdapter(
        _settings(face_autoscale="on", face_runpod_pod_id=POD, face_runpod_api_key="k"))
    client = adapter._http()
    try:
        assert client.headers["user-agent"] == USER_AGENT
        assert client.headers["authorization"] == "Bearer k"
        assert str(client.base_url).startswith("https://rest.runpod.io")
    finally:
        client.close()


# ── 상태 매핑 ──
@pytest.mark.parametrize("status,desired,running,pending", [
    ("RUNNING", 1, 1, 0),
    ("STARTING", 1, 0, 1),
    ("EXITED", 0, 0, 0),
    ("TERMINATED", 0, 0, 0),
])
def test_describe_maps_pod_status(status, desired, running, pending):
    client = _FakeClient({"desiredStatus": status, "lastStartedAt": "2026-09-10T01:00:00Z"})
    adapter = RunpodAutoscaleAdapter(
        _settings(face_autoscale="on", face_runpod_pod_id=POD, face_runpod_api_key="k"),
        client=client)
    state = asyncio.run(adapter.describe(RunpodTarget(POD)))
    assert (state.desired, state.running, state.pending) == (desired, running, pending)
    assert client.gets == [f"/pods/{POD}"]
    if running:
        assert state.oldest_started_at == datetime(2026, 9, 10, 1, 0, tzinfo=timezone.utc)
    else:
        assert state.oldest_started_at is None


@pytest.mark.parametrize("count,action", [(1, "start"), (2, "start"), (0, "stop")])
def test_set_desired_starts_and_stops(count, action):
    client = _FakeClient()
    adapter = RunpodAutoscaleAdapter(
        _settings(face_autoscale="on", face_runpod_pod_id=POD, face_runpod_api_key="k"),
        client=client)
    asyncio.run(adapter.set_desired(RunpodTarget(POD), count))
    assert client.posts == [f"/pods/{POD}/{action}"]


# ── 수요 ──
class _Cur:
    def __init__(self, rows):
        self._rows = list(rows)
        self.sql = []

    async def execute(self, sql, params=None):
        self.sql.append(" ".join(sql.split()))

    async def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Conn:
    def __init__(self, rows):
        self.cur = _Cur(rows)

    def cursor(self):
        return self.cur


def test_demand_is_zero_without_the_table():
    """마이그 미적용 환경 — 잡 테이블을 아예 안 본다(얼굴 패스가 걸릴 수 없다)."""
    conn = _Conn([{"t": None}])
    snap = asyncio.run(face_demand_snapshot(conn))
    assert (snap.active_sam_jobs, snap.last_sam_finished_at, snap.last_upload_at) == (0, None, None)
    assert len(conn.cur.sql) == 1 and "to_regclass" in conn.cur.sql[0]


def test_demand_counts_only_enabled_lora_owners_cuts():
    finished = datetime(2026, 9, 10, 3, 0, tzinfo=timezone.utc)
    conn = _Conn([{"t": "fm_model_loras"},
                  {"active_face_jobs": 2, "last_face_finished_at": finished}])
    snap = asyncio.run(face_demand_snapshot(conn))
    assert snap.active_sam_jobs == 2
    assert snap.last_sam_finished_at == finished
    sql = conn.cur.sql[1]
    assert "where enabled and status = 'ready'" in sql
    assert "payload -> '_facemarket' ->> 'modelId' in (select id from enabled)" in sql
    # 착장 컷을 만드는 잡만. 마네킹·매칭은 얼굴 패스 경로가 아니다.
    assert FACE_KINDS == ("editor_image", "detail_page")


def test_want_count_reuses_sam_rule():
    """수요 0 + 유휴 창 밖 → 0대, 창 안 → 1대. sam 과 같은 함수를 쓴다는 사실을 고정."""
    now = datetime(2026, 9, 10, 4, 0, tzinfo=timezone.utc)
    idle_in = sam_autoscale.DemandSnapshot(0, now - timedelta(minutes=5), None)
    idle_out = sam_autoscale.DemandSnapshot(0, now - timedelta(minutes=90), None)
    assert sam_autoscale.want_count(idle_in, idle_minutes=30, now=now) == 1
    assert sam_autoscale.want_count(idle_out, idle_minutes=30, now=now) == 0
    assert sam_autoscale.want_count(
        sam_autoscale.DemandSnapshot(3, None, None), idle_minutes=30, now=now) == 1
