"""재시도를 밀어줄 사람 — 톤 마스크는 셀러 폴링이 밀지만 이 둘은 보는 화면이 없다.

디스패처 스윕에 얹지 않는다: 디스패처는 워커를 await 한 뒤 다음 반복으로 가므로
(dispatcher.py), detail_page(평균 563초)가 도는 동안 스윕이 멈춘다 — 285초 예산의 타이머로
쓸 수 없다.
"""

import asyncio
from datetime import datetime, timedelta, timezone

from app.services import sam_retry
from app.workers import sam_retry_pusher
from app.workers.sam_retry_pusher import PUSH_KINDS, SamRetryPusher


def _job(**over):
    fin = datetime.now(timezone.utc) - timedelta(seconds=600)
    base = {"id": "j1", "user_id": "u", "project_id": "p", "kind": "matching_cutout",
            "status": "done", "result": {"state": "unavailable"},
            "payload": {"matchingItemId": "i1"}, "finished_at": fin,
            "idempotency_key": "p:matching_cutout:i1:v1"}
    return {**base, **over}


class _Recorder:
    """repo 대역 — 푸셔가 무엇을 걸었는지만 본다."""

    def __init__(self, candidates, latest=None):
        self.candidates = candidates
        self._latest = latest
        self.created = []
        self.list_calls = []

    async def list_retryable_sam_jobs(self, conn, kinds, *, max_retries,
                                      min_age_seconds, limit=50):
        self.list_calls.append({"kinds": kinds, "max_retries": max_retries,
                                "min_age_seconds": min_age_seconds})
        return self.candidates

    async def get_latest_job_generation(self, conn, user_id, base_key):
        return self._latest if self._latest is not None else self.candidates[0]

    async def create_job(self, conn, *, user_id, project_id, kind, payload,
                         idempotency_key, credits_reserved, metadata):
        self.created.append({"kind": kind, "key": idempotency_key, "payload": payload,
                             "credits_reserved": credits_reserved})
        return {"id": "new"}, True


def _push(rec):
    pusher = SamRetryPusher(app=None)
    return asyncio.run(pusher._push_once(rec, None))


def test_it_queues_the_next_generation_for_a_transient_outage():
    rec = _Recorder([_job()])
    pushed = _push(rec)

    assert pushed == 1
    assert rec.created[0]["key"] == "p:matching_cutout:i1:v1:r1"
    assert rec.created[0]["payload"]["retry"] == 1
    assert rec.created[0]["payload"]["matchingItemId"] == "i1", "원래 payload 를 이월해야 한다"
    assert rec.created[0]["credits_reserved"] == 0, "SAM 잡은 무과금이다"


def test_it_only_pushes_kinds_without_a_polling_screen():
    """톤 마스크는 톤 에디터 폴링이 민다 — 여기서 또 밀면 보고 있지 않은 컷까지 예산을 태운다."""
    rec = _Recorder([])
    _push(rec)
    assert rec.list_calls[0]["kinds"] == PUSH_KINDS
    assert "editor_garment_mask" not in PUSH_KINDS
    assert set(PUSH_KINDS) == {"sam_preprocess", "matching_cutout"}


def test_it_asks_the_repo_for_the_shared_budget():
    rec = _Recorder([])
    _push(rec)
    assert rec.list_calls[0]["max_retries"] == sam_retry.MAX_RETRIES
    assert rec.list_calls[0]["min_age_seconds"] == min(sam_retry.BACKOFF_SECONDS)


def test_it_does_not_retry_an_input_verdict():
    rec = _Recorder([_job(result={"state": "failed", "reason": "no_cutout"})])
    assert _push(rec) == 0
    assert rec.created == []


def test_it_waits_for_the_backoff():
    fresh = datetime.now(timezone.utc) - timedelta(seconds=5)   # waits[0] = 15초
    rec = _Recorder([_job(finished_at=fresh)])
    assert _push(rec) == 0


def test_it_stops_at_the_budget():
    spent = _job(payload={"matchingItemId": "i1", "retry": sam_retry.MAX_RETRIES},
                 idempotency_key=f"p:matching_cutout:i1:v1:r{sam_retry.MAX_RETRIES}")
    rec = _Recorder([spent])
    assert _push(rec) == 0


def test_it_ignores_a_stale_generation():
    """이미 다음 세대가 걸린 잡을 또 밀면 세대가 두 갈래로 갈라진다."""
    old = _job(id="old", payload={"matchingItemId": "i1"})
    newer = _job(id="new", payload={"matchingItemId": "i1", "retry": 1},
                 idempotency_key="p:matching_cutout:i1:v1:r1")
    rec = _Recorder([old], latest=newer)
    assert _push(rec) == 0


def test_it_skips_while_the_latest_generation_is_still_running():
    running = _job(id="run", status="running", result=None)
    rec = _Recorder([_job()], latest=running)
    assert _push(rec) == 0


def test_it_revives_a_pre_fix_error_job_without_a_generation_suffix():
    """배포 이전에 error 로 닫힌 sam_preprocess 도 state 가 같으므로 되살아난다."""
    legacy = _job(kind="sam_preprocess", status="error",
                  payload={"mode": "canonical_cutout"},
                  idempotency_key="p:sam_preprocess:abcd1234abcd1234")
    rec = _Recorder([legacy])
    assert _push(rec) == 1
    assert rec.created[0]["kind"] == "sam_preprocess"
    assert rec.created[0]["key"] == "p:sam_preprocess:abcd1234abcd1234:r1"
    assert rec.created[0]["payload"] == {"mode": "canonical_cutout", "retry": 1}


def test_one_bad_candidate_does_not_block_the_rest():
    """한 행이 깨져 있어도(키 없음) 나머지는 밀린다 — 푸셔는 best-effort 다."""
    broken = _job(id="broken", idempotency_key=None)
    good = _job(id="good", idempotency_key="p:matching_cutout:i2:v1",
                payload={"matchingItemId": "i2"})

    class _Rec(_Recorder):
        async def get_latest_job_generation(self, conn, user_id, base_key):
            return good if base_key == "p:matching_cutout:i2:v1" else None

    rec = _Rec([broken, good])
    assert _push(rec) == 1
    assert rec.created[0]["key"] == "p:matching_cutout:i2:v1:r1"


def test_poll_interval_follows_the_shortest_backoff():
    assert sam_retry_pusher.POLL_SECONDS == min(sam_retry.BACKOFF_SECONDS)
