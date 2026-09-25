"""재촬영 요청 — 관리자가 고른 칸만, 등록 상태는 그대로.

이 기능이 위험한 이유: 재촬영은 **등록이 이미 끝난 뒤**(passed, 모델 있음)에 일어난다.
그래서 "사진을 다시 받는다" 를 기존 photos_pending 경로로 열면, 끝난 등록이 되돌아가고
증서·자산까지 흔들린다. 그래서 등록 상태는 건드리지 않고 **요청된 칸만** 갈아 끼운다.

여기서 지키는 것:
  1. 요청된 칸이 아니면 409 — "한 칸 다시 찍어 주세요" 가 "전부 다시" 가 되면 안 된다.
  2. 재촬영 상태가 아니면 지금까지와 똑같이 409.
  3. 다 채우면 저절로 '확인 대기' 로 돌아가 관리자 큐에 다시 뜬다(사람이 또 눌러야 하면 잊힌다).
"""

import asyncio

import pytest
from fastapi import HTTPException

from app.facemarket_enrollment import (
    _consume_reshoot_slot,
    _validate_photo_mutation_enrollment,
    requested_reshoot_slots,
    reshoot_slot_views,
)


def _row(**over):
    base = {"status": "passed", "liveness_session_digest": "d",
            "photo_review_status": "reshoot_requested",
            "reshoot_slots": [{"slot": "sh_34", "reason": "먼 쪽 눈이 안 보여요"}]}
    return {**base, **over}


def _code(excinfo):
    return excinfo.value.detail["code"]


# ── 어떤 칸을 받아도 되는가 ────────────────────────────────────────────────
def test_only_the_requested_slots_are_open():
    assert requested_reshoot_slots(_row()) == {"sh_34"}
    assert _validate_photo_mutation_enrollment(_row(), "sh_34") is not None

    with pytest.raises(HTTPException) as excinfo:
        _validate_photo_mutation_enrollment(_row(), "sh_front")
    assert _code(excinfo) == "invalid_enrollment_state"


def test_a_finished_enrollment_without_a_request_stays_closed():
    """확인 대기·확인 완료 상태에서는 예전과 똑같이 막힌다."""
    for status in ("pending", "approved"):
        with pytest.raises(HTTPException) as excinfo:
            _validate_photo_mutation_enrollment(
                _row(photo_review_status=status, reshoot_slots=None), "sh_34")
        assert _code(excinfo) == "invalid_enrollment_state"


def test_deleting_a_photo_is_still_refused_during_a_reshoot():
    """삭제 경로는 slot 을 안 넘긴다(기본값 None) — 끝난 등록에 빈 칸을 낼 수는 없다."""
    with pytest.raises(HTTPException) as excinfo:
        _validate_photo_mutation_enrollment(_row())
    assert _code(excinfo) == "invalid_enrollment_state"


def test_the_upload_path_still_works_while_photos_are_pending():
    assert _validate_photo_mutation_enrollment(
        {"status": "photos_pending", "liveness_session_digest": None}, "sh_front")


def test_a_broken_reshoot_row_opens_nothing():
    """모양이 깨진 jsonb 나 화이트리스트 밖 이름은 버린다 — 열어 줄 근거가 없다."""
    assert requested_reshoot_slots(_row(reshoot_slots=["sh_34"])) == set()
    assert requested_reshoot_slots(_row(reshoot_slots=[{"slot": "../etc"}])) == set()
    assert requested_reshoot_slots(_row(reshoot_slots=None)) == set()
    assert requested_reshoot_slots(None) == set()


def test_reshoot_views_drop_unknown_slots_but_keep_reasons():
    views = reshoot_slot_views([{"slot": "sh_back", "reason": "얼굴이 보여요"},
                                {"slot": "nope", "reason": "x"},
                                "문자열"])
    assert [(view.slot, view.reason) for view in views] == [("sh_back", "얼굴이 보여요")]


# ── 다 채우면 저절로 돌아간다 ──────────────────────────────────────────────
class _Cur:
    """reshoot_slots 배열 하나만 아는 가짜 커서 — jsonb_agg 필터를 파이썬으로 흉내낸다."""

    def __init__(self, slots, status="reshoot_requested", enrollment_status="passed"):
        self.slots, self.status = slots, status
        self.enrollment_status = enrollment_status
        self.photo_revision = 0
        self.match_scores = {"scores": {"front": 0.9}}
        self.jobs = []
        self.result = None
        self.queries = []

    async def execute(self, sql, params=()):
        query = " ".join(sql.split())
        self.queries.append(query)
        if "set status = 'asset_building'" in query:
            if self.enrollment_status == "vc_pending":
                self.enrollment_status = "asset_building"
                self.photo_revision += 1
                if "match_scores = null" in query:
                    self.match_scores = None
                self.result = {"model_id": "m1", "photo_revision": self.photo_revision}
            else:
                self.result = None
            return
        if "insert into jobs" in query:
            self.jobs.append(params[1].obj)
            return
        if "update fm_models" in query:
            return
        if self.status != "reshoot_requested":
            self.result = None
            return
        if "jsonb_agg(item)" in query:
            slot = params[0]
            self.slots = [item for item in self.slots if item["slot"] != slot]
            self.result = {"remaining": len(self.slots)}
        elif "set photo_review_status = 'pending'" in query:
            self.status, self.slots = "pending", []
            self.result = None
        else:  # pragma: no cover
            raise AssertionError(f"예상 밖 질의: {query}")

    async def fetchone(self):
        return self.result


def test_the_last_reshoot_photo_sends_it_back_to_the_review_queue():
    cur = _Cur([{"slot": "sh_34", "reason": "a"}, {"slot": "sh_back", "reason": "b"}])

    asyncio.run(_consume_reshoot_slot(cur, "e1", "u1", "sh_34"))
    assert cur.status == "reshoot_requested", "아직 한 칸 남았다"

    asyncio.run(_consume_reshoot_slot(cur, "e1", "u1", "sh_back"))
    assert cur.status == "pending" and cur.slots == []


def test_consuming_a_slot_that_is_not_requested_changes_nothing():
    cur = _Cur([{"slot": "sh_34", "reason": "a"}])
    asyncio.run(_consume_reshoot_slot(cur, "e1", "u1", "sh_front"))
    assert cur.status == "reshoot_requested"
    assert [item["slot"] for item in cur.slots] == ["sh_34"]


def test_nothing_happens_when_the_row_is_not_in_reshoot():
    """다른 관리자가 그 사이 '확인 완료' 를 눌렀다 — where 절이 0행을 잡아야 한다."""
    cur = _Cur([{"slot": "sh_34", "reason": "a"}], status="approved")
    asyncio.run(_consume_reshoot_slot(cur, "e1", "u1", "sh_34"))
    assert cur.status == "approved"
    assert not any("set photo_review_status = 'pending'" in q for q in cur.queries)


def test_pending_vc_rebuilds_once_after_the_last_requested_photo():
    cur = _Cur([{"slot": "sh_front"}, {"slot": "sh_34"}], enrollment_status="vc_pending")
    asyncio.run(_consume_reshoot_slot(cur, "e1", "u1", "sh_front"))
    assert cur.enrollment_status == "vc_pending"
    assert cur.jobs == []
    asyncio.run(_consume_reshoot_slot(cur, "e1", "u1", "sh_34"))
    assert cur.enrollment_status == "asset_building"
    assert cur.photo_revision == 1
    assert cur.match_scores is None
    assert cur.jobs == [{"modelId": "m1", "enrollmentId": "e1", "photoRevision": 1}]
    asyncio.run(_consume_reshoot_slot(cur, "e1", "u1", "sh_34"))
    assert len(cur.jobs) == 1
