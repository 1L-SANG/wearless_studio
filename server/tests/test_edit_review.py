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
    """DB 를 흉내내되 **멱등 인덱스만** 흉내낸다 — 같은 키가 이미 있으면 insert 는 0행."""

    def __init__(self, state):
        self.state = state

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.state["sql"].append((flat, params))
        self._last = flat.lower()
        self._params = params
        if self._last.startswith("insert into edit_review_events"):
            self.state["insert_attempts"] = self.state.get("insert_attempts", 0) + 1

    async def fetchone(self):
        if "from edit_sessions es" in self._last:
            return self.state.get("session")
        if self._last.startswith("insert into edit_review_events"):
            if self.state.get("prior") is not None:
                return None            # on conflict do nothing → 0행
            self.state["rows"] = self.state.get("rows", 0) + 1
            return {"id": 1, "decision": self.state["decision"],
                    "reason": self.state.get("reason"), "created_at": "t"}
        if "from edit_review_events" in self._last:
            return self.state.get("prior")
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
    st = {"sql": [], "session": session, "prior": prior, "decision": decision,
          "reason": reason}
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
    assert st.get("rows", 0) == 0      # 행은 늘지 않는다


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


# ── idempotency race ───────────────────────────────────────────────────────

def test_insert_races_on_the_unique_index_not_on_a_prior_select():
    """SELECT-then-INSERT 면 두 요청이 동시에 '없다'를 보고 둘 다 넣는다."""
    st, _ = _review()
    stmts = [s for s, _p in st["sql"] if "edit_review_events" in s]
    assert stmts[0].startswith("insert into edit_review_events")
    assert "on conflict (edit_session_id, idempotency_key)" in stmts[0]
    assert "where idempotency_key is not null do nothing" in stmts[0]


def test_a_lost_race_reads_the_winning_row_instead_of_failing():
    st, out = _review(prior={"id": 7, "decision": "accepted", "reason": None,
                             "created_at": "t"})
    assert st.get("insert_attempts") == 1        # 시도는 했다
    assert st.get("rows", 0) == 0                # 행은 하나뿐
    assert out["idempotent"] is True and out["event"]["id"] == 7


def test_a_lost_race_with_a_different_decision_conflicts():
    with pytest.raises(ValueError) as e:
        _review(decision="rejected",
                prior={"id": 7, "decision": "accepted", "reason": None,
                       "created_at": "t"})
    assert str(e.value) == "idempotency_conflict"


def test_the_conflict_path_never_aborts_the_transaction():
    """unique violation 을 raise 시키면 트랜잭션 전체가 죽어 이후 쿼리가 전부 실패한다."""
    import inspect
    src = inspect.getsource(repo.record_edit_review)
    assert "do nothing" in src
    assert "UniqueViolation" not in src and "rollback" not in src
    # 충돌 뒤에도 같은 커서로 재조회가 실행된다(=커서가 살아 있다).
    st, _ = _review(prior={"id": 7, "decision": "accepted", "reason": None,
                           "created_at": "t"})
    after = [s for s, _p in st["sql"]
             if s.startswith("select id, decision, reason, created_at")]
    assert len(after) == 1


def test_a_missing_row_after_conflict_is_not_silently_accepted():
    class _Empty(_Cur):
        async def fetchone(self):
            if "from edit_sessions es" in self._last:
                return SESSION
            return None       # insert 0행 + 재조회 0행
    st = {"sql": [], "session": SESSION, "prior": None, "decision": "accepted"}

    class _C(_Conn):
        def cursor(self):
            return _Empty(self.state)
    with pytest.raises(ValueError) as e:
        asyncio.run(repo.record_edit_review(
            _C(st), project_id="p1", user_id="u1", session_id="sess-1",
            decision="accepted", reason=None, idempotency_key="k1"))
    assert str(e.value) == "review_not_recorded"


def test_review_not_recorded_is_not_reported_as_a_user_error():
    """알 수 없는 상태를 409 로 위장하면 클라이언트가 재시도로 고칠 수 있다고 오해한다."""
    import inspect
    from app import routes
    src = inspect.getsource(routes.review_edit_session)
    assert 'if code not in ("idempotency_conflict", "not_reviewable")' in src
    assert "raise   #" in src


def test_a_new_judgement_uses_its_own_key_so_history_grows():
    """accepted → rejected → accepted 는 이벤트 3건이고 최신이 이긴다."""
    events = []
    for i, d in enumerate(("accepted", "rejected", "accepted")):
        st, out = _review(d, key=f"k{i}")     # 판단마다 새 키
        assert out["idempotent"] is False
        events.append(out["event"]["decision"])
    assert events == ["accepted", "rejected", "accepted"]
    assert events[-1] == "accepted"           # effective decision = 최신 행


def test_a_reused_key_for_a_new_judgement_is_refused_not_replayed():
    """고정 키(`sess:accepted`)를 쓰면 두 번째 accepted 가 과거 이벤트 replay 가 된다."""
    _, out = _review("accepted", key="sess-1:accepted",
                     prior={"id": 3, "decision": "accepted", "reason": None,
                            "created_at": "old"})
    assert out["idempotent"] is True and out["event"]["created_at"] == "old"


# ── 감사 보존 (20260802020000) ──────────────────────────────────────────────

INTEGRITY = ("/Users/nojeong-un/devs/wearless_studio/supabase/migrations/"
             "20260802020000_edit_review_integrity.sql")


def _isql():
    return open(INTEGRITY, encoding="utf-8").read()


def test_hard_delete_cannot_silently_drop_the_audit_trail():
    sql = _isql()
    assert ("foreign key (project_id) references public.projects (id) "
            "on delete restrict") in sql
    assert ("foreign key (edit_session_id) references public.edit_sessions (id) "
            "on delete restrict") in sql


def test_incidental_links_still_null_out():
    """무엇을/누가 는 사라져도 판단 자체(decision·reason·created_at)는 남는다."""
    sql = _isql()
    for col in ("wardrobe_image_id", "output_id", "actor_id"):
        assert f"add constraint edit_review_events_{col}_fkey" not in sql


def test_append_only_is_enforced_by_a_row_level_trigger():
    sql = _isql()
    assert "before update or delete on public.edit_review_events" in sql
    assert "for each row execute function" in sql
    # statement-level 은 대상 0행 cascade 에도 발동해 삭제 전체를 막는다(20260616105745).
    assert "for each statement" not in sql


def test_the_trigger_refuses_every_delete():
    sql = _isql()
    assert "if tg_op = 'DELETE' then" in sql
    assert sql.count("raise exception 'edit_review_events is append-only'") == 2


def test_the_trigger_guards_every_meaningful_column():
    sql = _isql()
    for col in ("decision", "reason", "idempotency_key", "created_at",
                "project_id", "edit_session_id", "id"):
        assert f"new.{col} is distinct from old.{col}" in sql


def test_the_trigger_allows_only_fk_driven_nulling():
    sql = _isql()
    for col in ("wardrobe_image_id", "output_id", "actor_id"):
        assert f"new.{col} is not null" in sql, f"{col} 은 값이 채워지면 위조다"


def test_direct_client_writes_are_revoked():
    assert ("revoke insert, update, delete on public.edit_review_events "
            "from anon, authenticated") in _isql()


def test_the_integrity_migration_does_not_edit_the_original():
    """기존 migration 무수정 — 후속 migration 만 추가한다."""
    base = _sql()
    assert "on delete cascade" in base          # 원본은 그대로
    assert "create table if not exists public.edit_review_events" not in _isql()


def test_the_integrity_migration_is_replayable():
    sql = _isql()
    assert sql.count("drop constraint if exists") == 2
    assert "drop trigger if exists" in sql
    assert "create or replace function" in sql


# ── source src 원문 제거 ────────────────────────────────────────────────────

@pytest.mark.parametrize("src", [
    "https://cdn.example.com/x.png?token=SECRET&sig=abc",
    "/v1/assets/not-a-uuid/file?key=SECRET",
    "javascript:alert(1)",
    "/v1/assets/" + "-" * 36 + "/file",
    "a" * 4000,
])
def test_source_url_never_reaches_failure_metadata(src):
    from app.workers.editor_image_job import _parse_source_asset_id, _safe_asset_id
    meta = {"error": "source_asset_missing",
            **_safe_asset_id(_parse_source_asset_id(src))}
    flat = str(meta)
    for leak in ("SECRET", "token", "http", "javascript", "?", "cdn.example"):
        assert leak not in flat
    assert set(meta) <= {"error", "sourceAssetId"}


def test_a_valid_asset_id_is_kept_for_diagnosis():
    from app.workers.editor_image_job import _parse_source_asset_id, _safe_asset_id
    aid = "1f0b2c3d-4e5f-4a6b-8c9d-0e1f2a3b4c5d"
    meta = _safe_asset_id(_parse_source_asset_id(f"/v1/assets/{aid}/file"))
    assert meta == {"sourceAssetId": aid}


def test_the_worker_no_longer_stores_the_raw_src():
    import inspect
    from app.workers import editor_image_job
    src = inspect.getsource(editor_image_job)
    assert '"src": source.get("src")' not in src
    assert '"error": "source_asset_missing", **_safe_asset_id(asset_id)' in src


# ── 동시 요청 (결정적 인터리빙 시뮬레이션) ─────────────────────────────────
# 실 Postgres 대신 partial unique index 의 의미론만 공유 상태로 흉내낸다. 두 코루틴이
# 세션 확인을 **둘 다** 통과한 뒤에야 insert 로 들어가도록 강제해, SELECT-then-INSERT
# 였다면 행이 2개가 됐을 인터리빙을 재현한다. (실 DB 적용은 이번 범위 밖 — migration 미적용)

class _RacingCur(_Cur):
    async def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self._last = flat.lower()
        idx = self.state["index"]
        if self._last.startswith("insert into edit_review_events"):
            await asyncio.sleep(0)          # 다른 요청에게 양보 — 여기서 겹친다
            key = (params[1], params[7])
            if params[7] is not None and key in idx:
                if "on conflict" not in self._last:
                    # 실 DB 라면 여기서 unique violation 이 나고 트랜잭션이 통째로 죽는다.
                    raise RuntimeError("unique violation — transaction aborted")
                self._rows = None           # on conflict do nothing
            else:
                row = {"id": len(idx) + 1, "decision": params[5],
                       "reason": params[6], "created_at": f"t{len(idx) + 1}"}
                if params[7] is not None:
                    idx[key] = row
                self.state["rows"] = self.state.get("rows", 0) + 1
                self._rows = row
        elif "from edit_review_events" in self._last:
            self._rows = idx.get((params[0], params[1]))
        elif "from edit_sessions es" in self._last:
            await asyncio.sleep(0)          # 두 요청이 함께 통과하게 한다
            self._rows = SESSION
        else:
            self._rows = None

    async def fetchone(self):
        return self._rows


class _RacingConn(_Conn):
    def cursor(self):
        return _RacingCur(self.state)


def _race(payloads):
    state = {"index": {}, "rows": 0}

    async def go():
        async def one(decision, reason, key):
            try:
                return await repo.record_edit_review(
                    _RacingConn(state), project_id="p1", user_id="u1",
                    session_id="sess-1", decision=decision, reason=reason,
                    idempotency_key=key)
            except ValueError as e:
                return e
        return await asyncio.gather(*(one(*p) for p in payloads))
    return state, asyncio.run(go())


def test_concurrent_identical_requests_produce_one_row():
    state, (a, b) = _race([("accepted", None, "k"), ("accepted", None, "k")])
    assert state["rows"] == 1, "동시 요청이 이력을 두 줄로 부풀렸다"
    assert not isinstance(a, Exception) and not isinstance(b, Exception)
    assert a["event"]["id"] == b["event"]["id"] == 1
    assert {a["idempotent"], b["idempotent"]} == {False, True}


def test_concurrent_conflicting_requests_store_one_and_refuse_the_other():
    state, (a, b) = _race([("accepted", None, "k"), ("rejected", None, "k")])
    assert state["rows"] == 1
    errs = [x for x in (a, b) if isinstance(x, ValueError)]
    oks = [x for x in (a, b) if not isinstance(x, ValueError)]
    assert len(errs) == 1 and str(errs[0]) == "idempotency_conflict"
    assert len(oks) == 1 and oks[0]["idempotent"] is False


def test_concurrent_distinct_judgements_both_land():
    """다른 키 = 다른 판단이므로 둘 다 기록돼야 한다(최신이 유효 판단)."""
    state, out = _race([("accepted", None, "k1"), ("rejected", None, "k2")])
    assert state["rows"] == 2
    assert all(not isinstance(x, Exception) for x in out)


def test_a_race_never_surfaces_as_a_server_error():
    _, out = _race([("accepted", None, "k")] * 4)
    assert not [x for x in out if isinstance(x, Exception)]
    assert len({x["event"]["id"] for x in out}) == 1
