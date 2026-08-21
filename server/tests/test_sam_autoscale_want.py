"""지금 sam2 가 몇 대여야 하는가 — 세 조건 중 하나라도 참이면 1, 전부 거짓이면 0.

조건 2(마지막 SAM 잡 종료 30분 이내)가 없으면 "어제 만든 컷을 오늘 열어 색감 조정" 경로가
안 잡힌다: 그 잡은 1초 만에 unavailable 로 끝나 60초 뒤 reconciler 가 볼 땐 pending 도
running 도 아니다.
"""

from datetime import datetime, timedelta, timezone

from app.services import sam_autoscale
from app.services.sam_autoscale import DemandSnapshot, want_running

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)


def _ago(minutes):
    return NOW - timedelta(minutes=minutes)


def test_sam_kinds_are_the_three_sam_jobs():
    assert set(sam_autoscale.SAM_KINDS) == {
        "sam_preprocess", "matching_cutout", "editor_garment_mask"}


def test_active_job_wants_running_regardless_of_timestamps():
    snap = DemandSnapshot(active_sam_jobs=1, last_sam_finished_at=_ago(999),
                          last_upload_at=_ago(999))
    assert want_running(snap, idle_minutes=30, now=NOW) is True


def test_recent_sam_finish_wants_running():
    snap = DemandSnapshot(active_sam_jobs=0, last_sam_finished_at=_ago(29), last_upload_at=None)
    assert want_running(snap, idle_minutes=30, now=NOW) is True


def test_recent_upload_wants_running():
    snap = DemandSnapshot(active_sam_jobs=0, last_sam_finished_at=None, last_upload_at=_ago(29))
    assert want_running(snap, idle_minutes=30, now=NOW) is True


def test_all_quiet_wants_stopped():
    snap = DemandSnapshot(active_sam_jobs=0, last_sam_finished_at=_ago(31),
                          last_upload_at=_ago(31))
    assert want_running(snap, idle_minutes=30, now=NOW) is False


def test_boundary_is_strictly_less_than_idle():
    snap = DemandSnapshot(active_sam_jobs=0, last_sam_finished_at=_ago(30), last_upload_at=None)
    assert want_running(snap, idle_minutes=30, now=NOW) is False


def test_nothing_ever_happened_wants_stopped():
    snap = DemandSnapshot(active_sam_jobs=0, last_sam_finished_at=None, last_upload_at=None)
    assert want_running(snap, idle_minutes=30, now=NOW) is False


def test_naive_timestamps_are_treated_as_utc():
    snap = DemandSnapshot(active_sam_jobs=0,
                          last_sam_finished_at=NOW.replace(tzinfo=None) - timedelta(minutes=5),
                          last_upload_at=None)
    assert want_running(snap, idle_minutes=30, now=NOW) is True


def test_idle_minutes_is_honoured():
    snap = DemandSnapshot(active_sam_jobs=0, last_sam_finished_at=_ago(40), last_upload_at=None)
    assert want_running(snap, idle_minutes=30, now=NOW) is False
    assert want_running(snap, idle_minutes=60, now=NOW) is True
