"""Phase 3 P0-C 8/N — 검수 이력·공개 DTO·오류 정제.

계약:
  · 신규와 replay 는 **같은 공개 DTO**(jobId). 내부 job row 는 나가지 않는다.
  · WardrobeImage 는 추가만 한다 — legacy row 는 전부 null/false 로 직렬화된다.
  · machine QC 와 사용자 판단은 다른 사실이다. 검수는 append-only 이고 QC 를 덮지 않는다.
  · provider 원문은 job metadata·로그·API 어디에도 남지 않는다(도메인 코드는 계약이라 보존).
"""

import asyncio
import contextlib
import re

import pytest

from app import repo
from app.workers import editor_image_job as eij

MIGRATION = ("/Users/nojeong-un/devs/wearless_studio/supabase/migrations/"
             "20260802010000_edit_review_events.sql")


def _sql():
    return open(MIGRATION, encoding="utf-8").read()


# ── WardrobeImage DTO ───────────────────────────────────────────────────────

LEGACY_ROW = {"id": "w1", "asset_id": "a1", "ai": True, "cut_type": "styling"}
VARY_ROW = {**LEGACY_ROW, "edit_session_id": "sess-1", "qc_status": "review_required",
            "source_asset_id": "src-1", "review_decision": "accepted",
            "edit_qc_result": {"decision": "review_required",
                               "requestedChangeSatisfied": True,
                               "unexpectedChanges": ["cuffY"],
                               "lockedInvariantViolations": [],
                               "regenerationInstructions": ["secret instruction"],
                               "metrics": {"delta": {"hemY": -0.08}},
                               "vision": {"observation": {"collarChanged": False},
                                          "meta": {"status": "ok",
                                                   "provider": "gemini"}}}}


def test_legacy_row_serialises_with_null_phase3_fields():
    """migration 미적용·mode:new row 에서도 직렬화가 죽지 않는다."""
    out = repo._wardrobe_image_api(LEGACY_ROW)
    assert out["id"] == "w1" and out["src"] == "/v1/assets/a1/file"
    assert out["ai"] is True and out["cutType"] == "styling"
    assert out["editSessionId"] is None and out["qcStatus"] is None
    assert out["needsReview"] is False and out["reviewDecision"] is None
    assert out["sourceAssetId"] is None and out["sourceSrc"] is None
    assert out["qcSummary"] is None


def test_vary_row_exposes_review_state():
    out = repo._wardrobe_image_api(VARY_ROW)
    assert out["editSessionId"] == "sess-1"
    assert out["qcStatus"] == "review_required" and out["needsReview"] is True
    assert out["reviewDecision"] == "accepted"
    assert out["sourceSrc"] == "/v1/assets/src-1/file"


def test_user_acceptance_does_not_change_the_machine_verdict():
    """사용자가 승인해도 판정은 그대로다 — 둘은 다른 사실이다."""
    out = repo._wardrobe_image_api(VARY_ROW)
    assert out["qcStatus"] == "review_required" and out["needsReview"] is True


def test_qc_summary_carries_only_safe_fields():
    summary = repo._wardrobe_image_api(VARY_ROW)["qcSummary"]
    assert set(summary) <= {"decision", "requestedChangeSatisfied", "unexpectedChanges",
                            "lockedInvariantViolations", "visionStatus"}
    flat = str(summary)
    for leak in ("secret instruction", "metrics", "observation", "provider"):
        assert leak not in flat, f"요약에 {leak} 이 샜다"


def test_qc_summary_is_none_without_a_result():
    assert repo._wardrobe_image_api({**LEGACY_ROW, "edit_qc_result": None})[
        "qcSummary"] is None


def test_finalize_and_list_share_one_serializer():
    """같은 row 가 finalize 응답과 목록에서 다른 의미를 갖지 않게."""
    import inspect
    src = inspect.getsource(repo.finalize_editor_image_success)
    assert "_wardrobe_image_api(" in src


# ── review 이력 ─────────────────────────────────────────────────────────────

class _Cur:
    def __init__(self, state):
        self.state = state

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, sql, params=None):
        self.state["sql"].append((" ".join(sql.split()), params))
        self._last = " ".join(sql.split()).lower()

    async def fetchone(self):
        if "from edit_sessions es" in self._last:
            return self.state.get("session")
        if "from edit_review_events" in self._last:
            return self.state.get("prior")
        if "insert into edit_review_events" in self._last:
            return {"id": 1, "decision": self.state["decision"],
                    "reason": None, "created_at": "t"}
        return None


class _Conn:
    def __init__(self, state):
        self.state = state

    def cursor(self):
        return _Cur(self.state)

    async def commit(self):
        return None


SESSION = {"id": "sess-1", "status": "review_required", "output_id": "out-1",
           "project_id": "p1", "wardrobe_image_id": "w1"}


def _review(decision="accepted", *, session=SESSION, prior=None, key="k1", reason=None):
    st = {"sql": [], "session": session, "prior": prior, "decision": decision}
    return st, asyncio.run(repo.record_edit_review(
        _Conn(st), project_id="p1", user_id="u1", session_id="sess-1",
        decision=decision, reason=reason, idempotency_key=key))


def test_review_appends_an_event():
    st, out = _review()
    assert out["idempotent"] is False
    ins = [s for s, _p in st["sql"] if s.startswith("insert into edit_review_events")]
    assert len(ins) == 1


def test_review_never_touches_the_session_or_qc():
    """machine QC 를 사용자 승인으로 덮어쓰지 않는다."""
    st, _ = _review()
    assert not [s for s, _p in st["sql"] if s.startswith("update edit_sessions")]
    assert not [s for s, _p in st["sql"] if "edit_qc_result" in s and "update" in s]


def test_review_links_the_wardrobe_row_and_output():
    st, _ = _review()
    params = [p for s, p in st["sql"]
              if s.startswith("insert into edit_review_events")][0]
    assert "w1" in params and "out-1" in params and "u1" in params


def test_only_review_required_sessions_are_reviewable():
    for status in ("pass", "reject", "failed", "running", "queued"):
        with pytest.raises(ValueError) as e:
            _review(session={**SESSION, "status": status})
        assert str(e.value) == "not_reviewable"


def test_session_from_another_project_is_not_found():
    """소유권은 SQL 조인이 건다 — 존재 여부를 노출하지 않는다."""
    with pytest.raises(LookupError):
        _review(session=None)


def test_same_key_same_decision_returns_the_existing_event():
    st, out = _review(prior={"id": 9, "decision": "accepted", "reason": None,
                             "created_at": "t"})
    assert out["idempotent"] is True and out["event"]["id"] == 9
    assert not [s for s, _p in st["sql"]
                if s.startswith("insert into edit_review_events")]


def test_same_key_different_decision_conflicts():
    with pytest.raises(ValueError) as e:
        _review(decision="rejected",
                prior={"id": 9, "decision": "accepted", "reason": None,
                       "created_at": "t"})
    assert str(e.value) == "idempotency_conflict"


# ── migration 정적 계약 ─────────────────────────────────────────────────────

def test_events_survive_actor_deletion():
    """승인 이력은 사람보다 오래 산다."""
    assert "actor_id uuid references auth.users (id) on delete set null" in _sql()


def test_decision_values_are_constrained():
    assert "check (decision in ('accepted', 'rejected'))" in _sql()
    assert "'review_required'" not in _sql().split("check (decision")[1][:80]


def test_reason_length_is_bounded():
    assert re.search(r"length\(reason\) <= 500", _sql())


def test_idempotency_is_enforced_by_the_database():
    assert re.search(
        r"create unique index if not exists edit_review_events_idempotency\s+"
        r"on public\.edit_review_events \(edit_session_id, idempotency_key\)", _sql())


def test_rls_is_scoped_by_project_ownership():
    sql = _sql()
    assert "enable row level security" in sql
    assert "p.user_id = (select auth.uid())" in sql


def test_migration_is_append_only():
    sql = _sql().lower()
    assert "drop table" not in sql and "delete from" not in sql
    assert "update public.edit_sessions" not in sql


# ── 오류 원문 정제 ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("msg", [
    "Gemini 500: https://host/v1/models?key=SECRET body",
    "Authorization: Bearer sk-abc123",
    "select * from wardrobe_images where id = 'x'",
    "PROMPT: You are an expert product photographer " * 20,
])
def test_provider_error_text_never_reaches_job_metadata(msg):
    from app.agents.gemini_image import GeminiError
    meta = eij._editor_failure_meta(GeminiError(msg))
    flat = str(meta)
    for leak in ("SECRET", "Bearer", "select *", "expert product", "://"):
        assert leak not in flat
    assert meta["error"] == "generation_failed" and meta["category"]


def test_domain_error_codes_are_preserved():
    """자체 ValueError 의 메시지는 곧 에러 **코드**라 계약이다 — 모양까지 그대로."""
    assert eij._editor_failure_meta(ValueError("invalid_color")) == {
        "error": "invalid_color"}


@pytest.mark.parametrize("msg", ["Invalid color: #ff0000 at /v1/x", "실패했어요",
                                 "a" * 100])
def test_non_code_value_errors_are_not_leaked_as_codes(msg):
    meta = eij._editor_failure_meta(ValueError(msg))
    assert meta["error"] == "generation_failed" and msg not in str(meta)


def test_safe_code_rejects_non_domain_exceptions():
    from app.agents.gemini_image import GeminiError
    assert eij._safe_error_code(GeminiError("invalid_color")) is None
    assert eij._safe_error_code(RuntimeError("invalid_color")) is None
