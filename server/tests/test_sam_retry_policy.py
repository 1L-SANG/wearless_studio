"""세 SAM 잡이 공유하는 재시도 정책의 순수 판정.

톤 마스크(2026-08-18 사고 2호)에서 나온 규칙을 sam_preprocess·matching_cutout 이 같이 쓴다.
원칙: 일시 장애는 판정이 아니다. 인프라 장애만 다시 돌리고, 입력에 대한 판정은 그대로 둔다.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.services import sam_retry


def _job(**over):
    base = {"status": "done", "result": {"state": "unavailable"}, "payload": {},
            "finished_at": datetime(2026, 8, 21, 0, 0, tzinfo=timezone.utc)}
    return {**base, **over}


def test_budget_is_four_generations_over_285_seconds():
    assert sam_retry.MAX_RETRIES == 4
    assert sam_retry.BACKOFF_SECONDS == (15, 60, 90, 120)
    assert sum(sam_retry.BACKOFF_SECONDS) == 285


@pytest.mark.parametrize("state", ["unavailable", "unverified"])
def test_infrastructure_states_are_retryable(state):
    assert sam_retry.job_is_retryable(_job(result={"state": state})) is True


@pytest.mark.parametrize("state", ["no_garment_candidate", "source_rejected", "failed",
                                   "skipped", "ready", "partial"])
def test_input_verdicts_are_not_retryable(state):
    assert sam_retry.job_is_retryable(_job(result={"state": state})) is False


def test_lease_recovery_error_without_result_is_retryable():
    """리스 회수가 실행을 error 로 닫으면 result 가 없다. 판정이 아니라 실행 인프라 사망이다."""
    assert sam_retry.job_is_retryable(_job(status="error", result=None)) is True


def test_done_without_result_is_not_retryable():
    """리스 회수가 아닌 정상 done 은 result 가 없어도 재시도 대상이 아니다."""
    assert sam_retry.job_is_retryable(_job(status="done", result=None)) is False


def test_retry_count_reads_payload_and_survives_garbage():
    assert sam_retry.job_retry_count(_job(payload={"retry": 3})) == 3
    assert sam_retry.job_retry_count(_job(payload={})) == 0
    assert sam_retry.job_retry_count(_job(payload={"retry": "x"})) == 0
    assert sam_retry.job_retry_count(_job(payload=None)) == 0


def test_backoff_uses_the_wait_for_the_current_generation():
    fin = datetime(2026, 8, 21, 0, 0, tzinfo=timezone.utc)
    job = _job(payload={"retry": 1}, finished_at=fin)          # 다음 대기 = waits[1] = 60초
    assert sam_retry.backoff_elapsed(job, now=fin + timedelta(seconds=59)) is False
    assert sam_retry.backoff_elapsed(job, now=fin + timedelta(seconds=60)) is True


def test_backoff_is_false_once_the_budget_is_spent():
    fin = datetime(2026, 8, 21, 0, 0, tzinfo=timezone.utc)
    job = _job(payload={"retry": sam_retry.MAX_RETRIES}, finished_at=fin)
    assert sam_retry.backoff_elapsed(job, now=fin + timedelta(days=1)) is False


def test_backoff_parses_iso_strings_and_assumes_utc_when_naive():
    job = _job(payload={"retry": 0}, finished_at="2026-08-21T00:00:00Z")
    assert sam_retry.backoff_elapsed(
        job, now=datetime(2026, 8, 21, 0, 0, 20, tzinfo=timezone.utc)) is True
    naive = _job(payload={"retry": 0}, finished_at=datetime(2026, 8, 21, 0, 0))
    assert sam_retry.backoff_elapsed(
        naive, now=datetime(2026, 8, 21, 0, 0, 20, tzinfo=timezone.utc)) is True


def test_backoff_is_false_without_a_finish_time():
    assert sam_retry.backoff_elapsed(_job(finished_at=None)) is False


def test_generation_key_leaves_the_base_untouched_at_zero():
    assert sam_retry.generation_key("p:kind:x:v1", 0) == "p:kind:x:v1"
    assert sam_retry.generation_key("p:kind:x:v1", 2) == "p:kind:x:v1:r2"


def test_base_key_strips_only_a_generation_suffix():
    assert sam_retry.base_key("p:kind:x:v1:r3") == "p:kind:x:v1"
    assert sam_retry.base_key("p:kind:x:v1") == "p:kind:x:v1"
    # 'r' 로 시작하지만 숫자가 아닌 꼬리는 신원의 일부다 — 잘라내면 다른 잡이 된다.
    assert sam_retry.base_key("p:kind:x:region") == "p:kind:x:region"


def test_budget_left_counts_remaining_generations():
    """화면이 "처리 중"을 유지할지 가르는 판정 — 예산이 남았으면 아직 포기가 아니다."""
    assert sam_retry.budget_left(_job(payload={})) is True
    assert sam_retry.budget_left(_job(payload={"retry": sam_retry.MAX_RETRIES - 1})) is True
    assert sam_retry.budget_left(_job(payload={"retry": sam_retry.MAX_RETRIES})) is False
