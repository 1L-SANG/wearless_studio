"""Phase 3 P0-C 4/N — edit session 무결성(멱등·전이·양방향 계보·flag·prompt).

계약:
  · 멱등 재호출은 **부수효과 0** — 세션도 크레딧도 payload 수정도 없다.
  · 다른 활성 job 에 합류하지 않는다(합류하면 남의 결과가 내 편집으로 보인다).
  · 상태 전이는 UPDATE 자체가 검증한다(읽고 판단하는 방식은 TOCTOU 를 만든다).
  · 편집 결과는 output ↔ session 을 **양방향**으로 잇고, 그 연결은 finalize 와 같은 tx 다.
  · 편집이 켜졌는데 Generation Run 기록이 꺼진 조합은 조용히 허용하지 않는다.
"""

import asyncio
import contextlib
import re
import types

import pytest

from app import repo
from app.services import edit_session as es
from conftest import make_settings

MIGRATION = ("/Users/nojeong-un/devs/wearless_studio/supabase/migrations/"
             "20260801050000_edit_session_integrity.sql")


# ── 1. 멱등 / 활성 job 충돌 ─────────────────────────────────────────────────

SESSION_ROW = {"id": "sess-1", "project_id": "p1", "baseline_id": "base-1",
               "parent_output_id": "out-base", "edit_type": "GARMENT_LENGTH_ONLY",
               "requested_adjustments": {"garmentLengthStep": -1,
                                         "sleeveLengthStep": 0, "bodyWidthStep": 0,
                                         "shoulderWidthStep": 0,
                                         "mannequinVolumeStep": 0, "tuckStateStep": 0},
               "locked_invariants": {}, "allowed_scope": {}, "status": "queued",
               "retry_count": 0, "output_id": None, "edit_qc_result": None,
               "job_id": "job-1"}
BASELINE_ROW = {"id": "base-1", "cut_client_id": "A-3", "output_id": "out-base",
                "generation_run_id": "run-base", "locked_invariants": {}}


class _Conn:
    async def commit(self):
        return None


class _Pool:
    def connection(self):
        @contextlib.asynccontextmanager
        async def _cm():
            yield _Conn()

        return _cm()


@pytest.fixture()
def edit_client(client, monkeypatch):
    client.app.state.pool = _Pool()
    monkeypatch.setattr(
        client.app.state, "settings",
        make_settings(mannequin_edit_intent_qc="enforce", generation_run_log="shadow",
                      r2_bucket="b"),
        raising=False)
    return client


def _wire(monkeypatch, *, prior_job=None, prior_session=None, active_job=None,
          created=True, seen=None):
    seen = seen if seen is not None else {}
    seen.setdefault("created_sessions", [])
    seen.setdefault("reserved", [])
    seen.setdefault("payload_writes", [])

    async def get_project(conn, user_id, project_id):
        return {"id": project_id}

    async def get_active_baseline(conn, project_id):
        return BASELINE_ROW

    async def by_key(conn, user_id, key):
        return prior_job

    async def by_job(conn, job_id):
        return prior_session

    async def active(conn, user_id, project_id, kind):
        return active_job

    async def create_job(conn, **kw):
        return {"id": "job-new"}, created

    async def reserve(conn, user_id, cost):
        seen["reserved"].append(cost)
        return 10

    async def create_session(conn, **kw):
        seen["created_sessions"].append(kw)
        return {**SESSION_ROW, "id": "sess-new", "job_id": "job-new"}

    async def patch_payload(conn, job_id, session_id):
        seen["payload_writes"].append((job_id, session_id))

    for name, fn in (("get_project", get_project),
                     ("get_active_baseline", get_active_baseline),
                     ("get_job_by_idempotency_key", by_key),
                     ("get_edit_session_by_job_id", by_job),
                     ("get_active_job", active), ("create_job", create_job),
                     ("reserve_credits", reserve),
                     ("create_edit_session", create_session),
                     ("update_job_payload_edit_session", patch_payload)):
        monkeypatch.setattr(repo, name, fn)
    return seen


def _post(client, make_token, body=None):
    return client.post(
        "/v1/projects/p1/mannequins:edit",
        json=body or {"editType": "GARMENT_LENGTH_ONLY",
                      "adjustments": {"garmentLengthStep": -1}},
        headers={"Authorization": f"Bearer {make_token(sub='user-1')}",
                 "Idempotency-Key": "k1"})


def test_new_request_creates_exactly_one_session_and_reserves_once(edit_client,
                                                                   make_token,
                                                                   monkeypatch):
    seen = _wire(monkeypatch)
    r = _post(edit_client, make_token)
    assert r.status_code == 200, r.text
    assert len(seen["created_sessions"]) == 1
    assert seen["reserved"] == [2] or len(seen["reserved"]) == 1
    assert len(seen["payload_writes"]) == 1


def test_same_key_retry_returns_the_same_session_with_no_side_effects(edit_client,
                                                                      make_token,
                                                                      monkeypatch):
    seen = _wire(monkeypatch, prior_job={"id": "job-1"}, prior_session=SESSION_ROW)
    r = _post(edit_client, make_token)
    assert r.status_code == 200, r.text
    assert r.json()["id"] == "sess-1" and r.json()["jobId"] == "job-1"
    assert seen["created_sessions"] == [], "재호출인데 세션이 또 생겼다"
    assert seen["reserved"] == [], "재호출인데 크레딧을 또 예약했다"
    assert seen["payload_writes"] == [], "기존 job payload 를 덮어썼다"


def test_same_key_with_a_different_payload_is_a_conflict(edit_client, make_token,
                                                         monkeypatch):
    seen = _wire(monkeypatch, prior_job={"id": "job-1"}, prior_session=SESSION_ROW)
    r = _post(edit_client, make_token,
              {"editType": "SLEEVE_LENGTH_ONLY", "adjustments": {"sleeveLengthStep": 2}})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "idempotency_conflict"
    assert seen["created_sessions"] == [] and seen["reserved"] == []


def test_request_while_another_mannequin_job_runs_is_rejected(edit_client, make_token,
                                                              monkeypatch):
    """regenerate 가 도는 중에 합류하면 그 결과가 내 편집으로 보인다."""
    seen = _wire(monkeypatch, active_job={"id": "job-regen"})
    r = _post(edit_client, make_token)
    assert r.status_code == 409 and r.json()["error"]["code"] == "job_in_progress"
    assert seen["created_sessions"] == [] and seen["reserved"] == []
    assert seen["payload_writes"] == []


def test_duplicate_without_a_key_is_rejected_without_orphan_sessions(edit_client,
                                                                     make_token,
                                                                     monkeypatch):
    seen = _wire(monkeypatch, active_job={"id": "job-other"})
    r = edit_client.post(
        "/v1/projects/p1/mannequins:edit",
        json={"editType": "GARMENT_LENGTH_ONLY",
              "adjustments": {"garmentLengthStep": -1}},
        headers={"Authorization": f"Bearer {make_token(sub='user-1')}"})
    assert r.status_code == 409
    assert seen["created_sessions"] == []


def test_join_race_does_not_create_a_session(edit_client, make_token, monkeypatch):
    """검사와 INSERT 사이의 레이스로 합류하게 되면 거절한다 — 세션·크레딧 없음."""
    seen = _wire(monkeypatch, created=False)
    r = _post(edit_client, make_token)
    assert r.status_code == 409 and r.json()["error"]["code"] == "job_in_progress"
    assert seen["created_sessions"] == [] and seen["reserved"] == []


def test_migration_enforces_one_session_per_job():
    sql = open(MIGRATION, encoding="utf-8").read()
    assert re.search(
        r"create unique index if not exists edit_sessions_one_per_job\s+"
        r"on public\.edit_sessions \(job_id\) where job_id is not null", sql)


# ── 2. 상태 전이 ────────────────────────────────────────────────────────────

class _TransitionCur:
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

    async def fetchone(self):
        if "update edit_sessions" in self._last and "returning" in self._last:
            # where status = any(%s) 를 fake 가 그대로 흉내낸다
            allowed = self._params[-1]
            if not self.state.get("missing") and self.state["status"] in allowed:
                self.state["status"] = self._params[0]
                return {"id": "s1", "status": self.state["status"],
                        "completed_at": "t", "retry_count": 0}
            return None
        if "select" in self._last:
            if self.state.get("missing"):
                return None
            return {"id": "s1", "status": self.state["status"], "completed_at": None,
                    "retry_count": 0}
        return None


class _TransitionConn:
    def __init__(self, state):
        self.state = state

    def cursor(self):
        return _TransitionCur(self.state)


def _transition(current, target, *, missing=False):
    st = {"status": current, "sql": [], "missing": missing}
    return st, asyncio.run(repo.update_edit_session(
        _TransitionConn(st), session_id="s1", status=target))


@pytest.mark.parametrize("cur,nxt", [
    ("queued", "running"), ("queued", "failed"), ("running", "pass"),
    ("running", "review_required"), ("running", "reject"), ("running", "failed"),
])
def test_valid_transitions_succeed(cur, nxt):
    st, row = _transition(cur, nxt)
    assert row["status"] == nxt and st["status"] == nxt


@pytest.mark.parametrize("cur,nxt", [
    ("pass", "running"), ("reject", "running"), ("failed", "running"),
    ("pass", "reject"), ("review_required", "pass"), ("queued", "pass"),
    ("queued", "review_required"), ("queued", "reject"),
])
def test_invalid_transitions_are_blocked(cur, nxt):
    with pytest.raises(repo.InvalidEditTransition):
        _transition(cur, nxt)


def test_transition_is_enforced_by_the_update_itself():
    """읽고 판단한 뒤 일반 UPDATE 를 쏘면 그 사이에 다른 워커가 종결시킬 수 있다."""
    st, _ = _transition("queued", "running")
    upd = [s for s, _p in st["sql"] if s.lower().startswith("update edit_sessions")][0]
    assert "status = any(%s)" in upd and "returning" in upd.lower()


def test_same_terminal_state_is_idempotent():
    st, row = _transition("pass", "pass")
    assert row["status"] == "pass"


def test_updating_a_missing_session_raises():
    with pytest.raises(repo.InvalidEditTransition):
        _transition("queued", "running", missing=True)


def test_repo_and_service_transition_tables_agree():
    """규칙이 두 곳에 사는 이상 갈라지지 않는지 테스트가 지킨다."""
    for target, sources in repo._EDIT_TRANSITION_SOURCES.items():
        for src in sources:
            assert es.can_transition(src, target), f"{src}→{target} 불일치"
    for src in es.STATUSES:
        for target in es.STATUSES:
            if es.can_transition(src, target):
                assert src in repo._EDIT_TRANSITION_SOURCES.get(target, ()), \
                    f"{src}→{target} 가 repo 표에 없다"


# ── 3. output ↔ session 양방향 ──────────────────────────────────────────────

class _FinalCur:
    def __init__(self, sink, fail_output=False):
        self.sink = sink
        self.fail_output = fail_output

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.sink.append((flat, params))
        self._last = flat
        if self.fail_output and flat.startswith("insert into generation_outputs"):
            from psycopg import errors
            raise errors.UndefinedColumn("column does not exist")

    async def fetchone(self):
        low = self._last.lower()
        if "for update" in low:
            return {"id": "job-1"}
        if "max(version)" in low:
            return {"v": 1}
        if low.startswith("insert into mannequin_cuts"):
            return {"id": "cut-uuid"}
        if low.startswith("insert into generation_outputs"):
            return {"id": "out-new"}
        if low.startswith("update edit_sessions") and "returning" in low:
            return {"id": "sess-1", "status": "pass", "completed_at": "t",
                    "retry_count": 0}
        return None


class _FinalConn:
    def __init__(self, sink, fail_output=False):
        self.sink = sink
        self.fail_output = fail_output

    def cursor(self):
        return _FinalCur(self.sink, self.fail_output)


LINEAGE = {"generation_run_id": "run-new", "output_sha256": "sha", "post_processed": False,
           "parent_output_id": "out-base", "baseline_id": "base-1"}


def _finalize(monkeypatch, *, edit_session=None, fail_output=False):
    async def consume(conn, **kw):
        return 5

    monkeypatch.setattr(repo, "_consume_buckets", consume)
    sink: list = []
    cand = {"asset_id": "a-1", "bucket": "b", "key": "k", "mime": "image/png",
            "size": 1, "width": 2, "height": 3, "candidate": "A", "base_fit": "regular",
            "qc_scores": None, "generation_lineage": LINEAGE}
    out = asyncio.run(repo.finalize_mannequin_success(
        _FinalConn(sink, fail_output), job_id="j1", lease_token="t", user_id="u1",
        project_id="p1", candidates=[cand], reserved=2, charge=2, metadata={},
        edit_session=edit_session))
    return sink, out


def test_output_carries_the_edit_session_id(monkeypatch):
    sink, _ = _finalize(monkeypatch,
                        edit_session={"id": "sess-1", "status": "pass",
                                      "qc_result": {"decision": "pass"}})
    sql, params = [(s, p) for s, p in sink
                   if s.startswith("insert into generation_outputs")][0]
    assert "edit_session_id" in sql and params[-1] == "sess-1"
    assert params[7] == "out-base" and params[8] == "base-1"


def test_session_is_linked_to_the_returned_output_id(monkeypatch):
    """'가장 최근 output' 재조회 금지 — RETURNING 으로 받은 그 행을 연결한다."""
    sink, _ = _finalize(monkeypatch,
                        edit_session={"id": "sess-1", "status": "pass",
                                      "qc_result": {}})
    link = [(s, p) for s, p in sink
            if s.startswith("update edit_sessions set output_id")]
    assert link and link[0][1] == ("out-new", "sess-1")
    assert not any("order by created_at desc" in s.lower()
                   and "generation_outputs" in s.lower() for s, _p in sink)


def test_session_status_is_written_in_the_same_transaction(monkeypatch):
    sink, _ = _finalize(monkeypatch,
                        edit_session={"id": "sess-1", "status": "review_required",
                                      "qc_result": {"decision": "review_required"}})
    upd = [(s, p) for s, p in sink if s.startswith("update edit_sessions set status")]
    assert upd and upd[0][1][0] == "review_required"
    # 컷 저장과 세션 종결이 같은 커서 흐름 안에 있다(별도 tx 아님)
    assert any(s.startswith("insert into mannequin_cuts") for s, _p in sink)


def test_edit_path_does_not_fail_open_on_lineage_failure(monkeypatch):
    """계보를 못 남긴 편집을 PASS 로 완료하지 않는다 — finalize 전체가 롤백된다."""
    from psycopg import errors
    with pytest.raises(errors.DatabaseError):
        _finalize(monkeypatch, edit_session={"id": "sess-1", "status": "pass",
                                             "qc_result": {}}, fail_output=True)


def test_fresh_path_keeps_the_phase_1_fail_open_policy(monkeypatch):
    """fresh/regenerate 는 그대로 관측 부가 기능이다 — 컷 출고를 막지 않는다."""
    sink, out = _finalize(monkeypatch, edit_session=None, fail_output=True)
    assert out["cuts"], "fresh 경로의 fail-open 정책이 바뀌었다"


# ── 4. flag 의존성 ──────────────────────────────────────────────────────────

def test_edit_requires_generation_run_logging(client, make_token, monkeypatch):
    client.app.state.pool = _Pool()
    monkeypatch.setattr(
        client.app.state, "settings",
        make_settings(mannequin_edit_intent_qc="shadow", generation_run_log="off",
                      r2_bucket="b"), raising=False)
    r = _post(client, make_token)
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "misconfigured_feature"


def test_both_off_is_allowed(client, make_token, monkeypatch):
    client.app.state.pool = _Pool()
    monkeypatch.setattr(
        client.app.state, "settings",
        make_settings(mannequin_edit_intent_qc="off", generation_run_log="off",
                      r2_bucket="b"), raising=False)
    r = _post(client, make_token)
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "edit_not_enabled"


def test_edit_flag_does_not_silently_enable_generation_logging():
    s = make_settings(mannequin_edit_intent_qc="enforce")
    assert s.generation_run_log == "off", "편집 플래그가 기록 플래그를 몰래 켰다"


# ── 5. prompt 스냅샷 ────────────────────────────────────────────────────────

def test_prompt_write_is_not_a_status_transition():
    """running→running 은 유효한 전이가 아니다 — 프롬프트 기록을 전이에 태우면 실패한다."""
    sink: list = []

    class _Cur(_FinalCur):
        async def fetchone(self):
            return None

    class _Conn2:
        def cursor(self):
            return _Cur(sink)

    asyncio.run(repo.set_edit_session_prompt(
        _Conn2(), session_id="s1", sha="abc", key="users/u/x.txt"))
    sql, params = sink[0]
    assert sql.startswith("update edit_sessions set prompt_sha256")
    assert "status" not in sql
    assert params == ("abc", "users/u/x.txt", "s1")


def test_prompt_columns_are_coalesced_so_retries_do_not_erase_the_first():
    """재시도 교정본이 최초 프롬프트 스냅샷을 덮어쓰지 않는다."""
    import inspect
    src = inspect.getsource(repo.set_edit_session_prompt)
    assert "coalesce(%s, prompt_sha256)" in src
    assert "coalesce(%s, prompt_r2_key)" in src
