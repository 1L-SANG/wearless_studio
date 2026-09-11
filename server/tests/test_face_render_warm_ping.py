"""미리 켜기 — 셀러가 FaceMarket 모델을 고른 순간을 수요로 읽는다.

콜드스타트를 첫 컷 앞에서 빼려는 것이다. 신호 구조는 SAM 선례 그대로(DemandSnapshot.last_upload_at):
"곧 필요해진다"를 미리 알리는 자리에 워밍 핑을 넣는다. api 태스크가 여러 개라 메모리로는 못 하고
(reconciler 는 다른 태스크에서 돈다) DB 테이블이 필요하다.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.services import sam_autoscale
from app.services.face_autoscale import face_demand_snapshot

NOW = datetime(2026, 9, 11, 3, 0, tzinfo=timezone.utc)


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


def _snapshot(rows):
    return asyncio.run(face_demand_snapshot(_Conn(rows)))


def test_warm_ping_becomes_demand():
    ping = NOW - timedelta(minutes=2)
    snap = _snapshot([
        {"t": "fm_model_loras"},
        {"active_face_jobs": 0, "last_face_finished_at": None},
        {"t": "fm_face_warm_pings"},
        {"at": ping},
    ])
    assert snap.last_upload_at == ping
    # 활성 잡이 0이어도 유휴 창 안이면 파드를 켠다 — 그게 미리 켜기의 전부다.
    assert sam_autoscale.want_running(snap, idle_minutes=10, now=NOW) is True


def test_old_ping_does_not_keep_the_pod_up():
    snap = _snapshot([
        {"t": "fm_model_loras"},
        {"active_face_jobs": 0, "last_face_finished_at": None},
        {"t": "fm_face_warm_pings"},
        {"at": NOW - timedelta(minutes=45)},
    ])
    assert sam_autoscale.want_running(snap, idle_minutes=10, now=NOW) is False


def test_missing_table_means_no_ping_signal():
    """마이그 미적용 환경 — 핑 테이블을 안 본다(기존 동작 그대로)."""
    snap = _snapshot([
        {"t": "fm_model_loras"},
        {"active_face_jobs": 0, "last_face_finished_at": None},
        {"t": None},
    ])
    assert snap.last_upload_at is None
    assert sam_autoscale.want_running(snap, idle_minutes=10, now=NOW) is False


def test_active_jobs_still_win_regardless_of_pings():
    snap = _snapshot([
        {"t": "fm_model_loras"},
        {"active_face_jobs": 2, "last_face_finished_at": None},
        {"t": "fm_face_warm_pings"},
        {"at": None},
    ])
    assert snap.active_sam_jobs == 2
    assert sam_autoscale.want_running(snap, idle_minutes=10, now=NOW) is True


# ── 라우트 계약(소스 고정) ──
def test_warm_route_rules():
    """켜진 LoRA 가 있는 REAL 모델만·셀러당 60초 1회·그 외 204."""
    import pathlib

    from app import facemarket

    text = pathlib.Path(facemarket.__file__).read_text(encoding="utf-8")
    assert '"/face-render/warm"' in text
    assert "FACE_WARM_PING_WINDOW_SECONDS = 60" in text
    # 실존 모델이 아니면 기록하지 않는다
    assert "if not is_real_model_id(model_id):" in text
    # 켜진 LoRA 가 없으면 파드를 켤 이유가 없다
    assert "from fm_model_loras where model_id = %s and enabled and status = 'ready' " in text
    # 플래그가 꺼져 있으면 라우트 자체가 404
    assert 'raise _err("not_found", "사용할 수 없습니다.", status=404)' in text


def test_status_route_reports_eta_from_the_measured_cold_start():
    from app.services import face_autoscale

    assert isinstance(face_autoscale.COLD_START_ETA_MINUTES, int)
    assert 1 <= face_autoscale.COLD_START_ETA_MINUTES <= 10
