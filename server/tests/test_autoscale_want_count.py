"""지금 워커가 몇 **대** 여야 하는가 — want_running(bool) 의 대수 확장.

SamAutoscaler 는 sam2·opendid·detail-worker 셋이 공유한다. 대수 산출을 넣으면서 앞의 둘의
동작이 바뀌면 안 된다. 그래서 기본값(per_task_capacity=1, max_tasks=1)에서는 want_running
과 **완전히 같은 결과**여야 한다는 것을 먼저 못 박는다.

detail-worker 만 태스크당 잡 N개를 처리하므로 대기 잡 수를 N 으로 나눠 대수를 정한다.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.services.sam_autoscale import DemandSnapshot, want_count, want_running

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)


def _snap(active=0, finished_min_ago=None, upload_min_ago=None):
    def ago(m):
        return None if m is None else NOW - timedelta(minutes=m)
    return DemandSnapshot(
        active_sam_jobs=active,
        last_sam_finished_at=ago(finished_min_ago),
        last_upload_at=ago(upload_min_ago),
    )


# ── 기존 동작 보존 (sam2·opendid 회귀 방지) ──────────────────────────────────

@pytest.mark.parametrize("snap", [
    _snap(active=0),
    _snap(active=1),
    _snap(active=7),
    _snap(active=0, finished_min_ago=5),
    _snap(active=0, finished_min_ago=90),
    _snap(active=0, upload_min_ago=5),
    _snap(active=0, upload_min_ago=90),
])
def test_default_capacity_matches_want_running(snap):
    """기본값에서는 want_running 이 True 면 1, False 면 0 — 한 치도 다르지 않다."""
    expected = 1 if want_running(snap, idle_minutes=30, now=NOW) else 0
    assert want_count(snap, idle_minutes=30, now=NOW) == expected


def test_zero_when_idle_regardless_of_capacity():
    """수요가 없으면 capacity·max 가 아무리 커도 0 대다 — scale-to-zero 를 깨지 않는다."""
    idle = _snap(active=0, finished_min_ago=90)
    assert want_count(idle, idle_minutes=30, now=NOW,
                      per_task_capacity=3, max_tasks=5) == 0


# ── 대수 산출 ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("active,capacity,expected", [
    (1, 3, 1),    # 잡 1개는 태스크 1대로 충분
    (3, 3, 1),    # 정확히 한 대분
    (4, 3, 2),    # 넘치면 한 대 더
    (6, 3, 2),
    (7, 3, 3),
    (1, 1, 1),
    (5, 1, 5),
])
def test_tasks_scale_with_backlog(active, capacity, expected):
    assert want_count(_snap(active=active), idle_minutes=30, now=NOW,
                      per_task_capacity=capacity, max_tasks=99) == expected


def test_never_exceeds_max_tasks():
    """대기 잡이 아무리 많아도 상한을 넘지 않는다 — 비용이 무한히 늘지 않게."""
    assert want_count(_snap(active=100), idle_minutes=30, now=NOW,
                      per_task_capacity=3, max_tasks=2) == 2


def test_idle_window_keeps_one_task_even_without_active_jobs():
    """활성 잡이 없어도 유휴 창 안이면 1대는 남긴다 — 콜드스타트 재부담을 피한다."""
    assert want_count(_snap(active=0, finished_min_ago=5), idle_minutes=30, now=NOW,
                      per_task_capacity=3, max_tasks=2) == 1


def test_capacity_zero_is_treated_as_one():
    """capacity 가 0/음수로 잘못 들어와도 0 으로 나누지 않는다 — 설정 오타 방어."""
    assert want_count(_snap(active=2), idle_minutes=30, now=NOW,
                      per_task_capacity=0, max_tasks=5) == 2
