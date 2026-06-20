"""FIFO 버킷 배분 순수 함수 테스트 (credit_system_design.md §3.3)."""

from app.credits import allocate_fifo


def _b(id, remaining):
    return {"id": id, "remaining_credits": remaining}


def test_single_bucket_covers():
    allocs, uncovered = allocate_fifo([_b("a", 200)], 2)
    assert uncovered == 0
    assert allocs == [{"id": "a", "take": 2}]


def test_subscription_first_then_topup_order_preserved():
    # 호출측이 구독먼저→오래된순으로 정렬해 넘긴다는 전제 — 들어온 순서대로 소진
    buckets = [_b("sub", 1), _b("topup_old", 5), _b("topup_new", 5)]
    allocs, uncovered = allocate_fifo(buckets, 3)
    assert uncovered == 0
    # 구독 1 전부 + 다음(오래된 topup) 2
    assert allocs == [{"id": "sub", "take": 1}, {"id": "topup_old", "take": 2}]


def test_multi_bucket_split_at_boundary():
    # 한 번의 차감이 여러 버킷에 걸침 (Codex가 짚은 케이스)
    buckets = [_b("a", 1), _b("b", 10)]
    allocs, uncovered = allocate_fifo(buckets, 2)
    assert uncovered == 0
    assert allocs == [{"id": "a", "take": 1}, {"id": "b", "take": 1}]


def test_uncovered_when_insufficient():
    allocs, uncovered = allocate_fifo([_b("a", 1)], 5)
    assert uncovered == 4  # 부족 → 호출측 hard error
    assert allocs == [{"id": "a", "take": 1}]


def test_zero_charge_no_allocation():
    allocs, uncovered = allocate_fifo([_b("a", 200)], 0)
    assert uncovered == 0
    assert allocs == []


def test_skips_empty_buckets():
    buckets = [_b("empty", 0), _b("a", 5)]
    allocs, uncovered = allocate_fifo(buckets, 3)
    assert uncovered == 0
    assert allocs == [{"id": "a", "take": 3}]


def test_negative_charge_rejected():
    import pytest

    with pytest.raises(ValueError):
        allocate_fifo([_b("a", 5)], -1)
