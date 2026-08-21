"""SAM 잡 3종이 공유하는 일시 장애 재시도 정책.

`editor_garment_mask` 가 2026-08-18 사고 2호 뒤에 갖춘 규칙을 `sam_preprocess`·
`matching_cutout` 이 같이 쓰기 위해 뽑아낸 모듈이다. 순수 판정만 있고 DB·네트워크는 없다.

원칙: **일시 장애는 판정이 아니다.** 인프라 장애(unavailable·unverified)는 다시 돌리면 답이
바뀔 수 있으므로 유한 예산 안에서 재시도하고, 입력에 대한 판정(옷을 못 찾음 등)은 다시 돌려도
같은 답이므로 재시도하지 않는다.
"""

from __future__ import annotations

import contextlib
import re
from datetime import datetime, timedelta, timezone

#: 재시도 상한과 세대별 대기(초). 합 285초(~5분).
#:
#: 상한이 5분인 것은 오너 결정이다(2026-08-21) — "그 이상 걸리면 사용자는 어차피 이탈한다".
#: 근거는 sam2 콜드스타트 실측 101초(RUNNING+HEALTHY) + 모델 lazy load 다. 여유 2배.
#: 실측이 5분을 넘기게 되면 이 값을 늘리지 말고 기동 트리거를 앞당긴다.
MAX_RETRIES = 4
BACKOFF_SECONDS = (15, 60, 90, 120)

#: 이 상태로 끝난 잡은 더 기다릴 게 없다 — 다음 세대를 걸지 말지 판단할 시점이다.
TERMINAL_STATUSES = ("done", "error", "cancelled")

#: 인프라 장애. 입력 판정인 no_garment_candidate·source_rejected 는 의도적으로 없다.
RETRYABLE_STATES = ("unavailable", "unverified")

_GENERATION_SUFFIX = re.compile(r":r\d+$")


def job_retry_count(job: dict) -> int:
    """이 잡이 몇 번째 세대인가. payload 가 깨져 있으면 0으로 본다."""
    payload = (job or {}).get("payload") or {}
    try:
        return int(payload.get("retry") or 0)
    except (TypeError, ValueError):
        return 0


def job_is_retryable(job: dict, *, states: tuple[str, ...] = RETRYABLE_STATES) -> bool:
    """이 종결이 판정이 아니라 일시 장애인가.

    `result.state` 로 판별한다 — 구버전 워커가 `error` 로 종결해 둔 과거 잡도 state 는 같으므로
    배포 이전에 막힌 것들도 이 판별을 지나 되살아난다.
    """
    result = (job or {}).get("result") or {}
    state = str(result.get("state") or "")
    if state in states:
        return True
    # 리스 회수가 서버 재시작 중 실행을 error 로 닫으면 result 자체가 없다. 판정이 아니라
    # 실행 인프라 사망이므로 다음 세대에서 다시 시도한다.
    return str((job or {}).get("status") or "") == "error" and not state


def budget_left(job: dict, *, max_retries: int = MAX_RETRIES) -> bool:
    """이 세대 다음에 또 걸 수 있는가. 화면이 "처리 중"을 유지할지 가르는 판정이다."""
    return job_retry_count(job) < int(max_retries)


def backoff_elapsed(job: dict, *, waits: tuple[int, ...] = BACKOFF_SECONDS,
                    now: datetime | None = None) -> bool:
    """다음 세대를 허용할 시각이 지났는가. 예산을 다 쓴 잡은 항상 False."""
    retry = job_retry_count(job)
    if retry >= len(waits):
        return False
    finished_at = (job or {}).get("finished_at")
    if isinstance(finished_at, str):
        with contextlib.suppress(ValueError):
            finished_at = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
    if not isinstance(finished_at, datetime):
        return False
    if finished_at.tzinfo is None:
        finished_at = finished_at.replace(tzinfo=timezone.utc)
    return (now or datetime.now(timezone.utc)) >= finished_at + timedelta(seconds=waits[retry])


def generation_key(base: str, retry: int) -> str:
    """세대마다 새 신원을 준다. 같은 키로 걸면 이미 끝난 잡에 합류만 하고 아무것도 안 돈다."""
    return base if int(retry) <= 0 else f"{base}:r{int(retry)}"


def base_key(idempotency_key: str) -> str:
    """`base:rN` 에서 base 를 되찾는다. 세대 접미사가 아니면 건드리지 않는다."""
    return _GENERATION_SUFFIX.sub("", str(idempotency_key or ""))
