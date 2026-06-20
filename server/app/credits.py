"""크레딧 버킷 FIFO 배분 — 순수 함수 (DB/IO 없음). credit_system_design.md §1·§3.3.

소진 순서: 구독 버킷 먼저 → 추가구매(topup) FIFO(오래된 순). 정렬은 호출측(repo)이
`order by source_type, created_at`으로 보장한다('subscription' < 'topup' 사전순).
실제 DB 차감·원장 기록은 repo가 한다.
"""


def allocate_fifo(buckets: list[dict], charge: int) -> tuple[list[dict], int]:
    """이미 (구독먼저→오래된순) 정렬된 active 버킷에서 charge를 FIFO로 배분.

    buckets: [{"id", "remaining_credits", ...}] (정렬 전제)
    반환: (allocations, uncovered)
      - allocations: [{"id", "take"}] (take>0 인 것만, 순서대로)
      - uncovered: 배분 후 남은 금액. >0 이면 active 잔액 부족 → 호출측이 hard error(불변식 5).
    """
    if charge < 0:
        raise ValueError("charge must be >= 0")
    allocations: list[dict] = []
    remain = charge
    for b in buckets:
        if remain <= 0:
            break
        take = min(b["remaining_credits"], remain)
        if take > 0:
            allocations.append({"id": b["id"], "take": take})
            remain -= take
    return allocations, remain
