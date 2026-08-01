"""Phase 3 P0-C 7/N — editor vary 실경로 배선(route·preflight·계측·finalize).

계약:
  · mode:new 와 플래그 off vary 는 **완전 불변** — 세션·계측·QC 0.
  · 판정 경로만 엄격하다: source 는 DB asset 이 정본, 계보는 유일할 때만 잇는다.
  · preflight 를 통과해야 provider 를 부른다(한 번 요청하고 두 번 과금되는 경로 차단).
  · Generation Run 스냅샷은 **실제로 나갈 prepared 객체**에서 뜬다.
  · 성공/검수 결과는 한 tx 에서 output↔session↔wardrobe 를 잇고, 실패하면 롤백한다.
"""

import asyncio
import contextlib
import hashlib
import re
import types

import pytest

from app import repo
from app.agents import cut_variator
from app.agents.gemini_image import InlineImage
from app.services import editor_vary as ev
from app.workers import editor_image_job as eij
from conftest import make_settings

MIGRATION = ("/Users/nojeong-un/devs/wearless_studio/supabase/migrations/"
             "20260802000000_editor_vary_result_integrity.sql")


def _sql():
    return open(MIGRATION, encoding="utf-8").read()


# ── migration ────────────────────────────────────────────────────────────────

def test_session_and_qc_status_must_be_present_together():
    sql = _sql()
    assert "edit_session_id is null and qc_status is null" in sql
    assert "edit_session_id is not null" in sql
    assert "qc_status in ('pass', 'review_required')" in sql


def test_reject_is_not_a_wardrobe_status():
    """거부된 결과는 애초에 진열되지 않는다 — enum 에 넣으면 저장 경로가 열린다."""
    assert "'reject'" not in _sql()


def test_one_wardrobe_result_per_session():
    assert re.search(
        r"create unique index if not exists wardrobe_images_one_per_edit_session\s+"
        r"on public\.wardrobe_images \(edit_session_id\) where edit_session_id is not null",
        _sql())


def test_migration_is_append_only():
    sql = _sql().lower()
    assert "drop table" not in sql and "drop column" not in sql


# ── cut_variator prepare/execute ────────────────────────────────────────────

def test_prepare_exposes_the_exact_request():
    s = make_settings()
    src = InlineImage("image/png", b"src")
    ref = InlineImage("image/png", b"ref")
    p = cut_variator.prepare(s, src, [{"type": "pose", "value": "side view"}],
                             "styling", ref_bg=ref)
    assert p.images[0] is src and p.images[1] is ref and p.has_ref_bg is True
    assert p.image_size == s.mannequin_image_size
    assert p.aspect_ratio == s.mannequin_aspect_ratio
    assert "side view" in p.prompt and p.model
    # refBg 가 붙으면 bg 칩은 프롬프트에서 빠지고 레퍼런스 지시가 대신한다(기존 계약)
    p2 = cut_variator.prepare(s, src, [{"type": "bg", "value": "studio"}], None,
                              ref_bg=ref)
    assert "studio" not in p2.prompt and "background reference" in p2.prompt


def test_generate_wrapper_uses_the_same_prepared_request(monkeypatch):
    """기록용 프롬프트를 따로 재조립하지 않는다 — 같은 객체에서 나온다."""
    seen = {}

    class _G:
        async def generate_content_image(self, model, prompt, images, size,
                                         aspect_ratio=None):
            seen.update(model=model, prompt=prompt, images=images)
            return types.SimpleNamespace(image=b"out", mime="image/png")

    s = make_settings()
    src = InlineImage("image/png", b"src")
    prepared = cut_variator.prepare(s, src, [{"type": "pose", "value": "side"}], None)
    asyncio.run(cut_variator.generate(s, _G(), src, [{"type": "pose", "value": "side"}],
                                      None))
    assert seen["prompt"] == prepared.prompt and seen["model"] == prepared.model


# ── route 배선 ──────────────────────────────────────────────────────────────

SRC_ID = "11111111-1111-4111-8111-111111111111"
SRC = f"/v1/assets/{SRC_ID}/file"


class _Conn:
    async def commit(self):
        return None


class _Pool:
    def connection(self):
        @contextlib.asynccontextmanager
        async def _cm():
            yield _Conn()

        return _cm()


def _wire_route(monkeypatch, *, flag="enforce", genlog="shadow", prior_job=None,
                prior_session=None, unique_output=None, asset=True, seen=None):
    seen = seen if seen is not None else {}
    seen.setdefault("sessions", []).clear()
    seen.setdefault("reserved", []).clear()
    seen.setdefault("payload_writes", []).clear()

    async def get_project(conn, uid, pid):
        return {"id": pid}

    async def get_asset(conn, uid, aid):
        return {"id": aid, "project_id": "p1", "mime_type": "image/png",
                "r2_key": "k"} if asset else None

    async def by_key(conn, uid, key):
        return prior_job

    async def by_job(conn, jid):
        return prior_session

    async def unique_out(conn, aid):
        return unique_output or {"output_id": None, "generation_run_id": None,
                                 "status": "none"}

    async def create_job(conn, **kw):
        return {"id": "job-new", "payload": kw.get("payload")}, True

    async def reserve(conn, uid, cost):
        seen["reserved"].append(cost)
        return 10

    async def create_session(conn, **kw):
        seen["sessions"].append(kw)
        return {"id": "sess-new", **kw}

    async def patch_payload(conn, jid, sid):
        seen["payload_writes"].append((jid, sid))

    for name, fn in (("get_project", get_project), ("get_asset_for_user", get_asset),
                     ("get_job_by_idempotency_key", by_key),
                     ("get_edit_session_by_job_id", by_job),
                     ("get_unique_output_for_asset", unique_out),
                     ("create_job", create_job), ("reserve_credits", reserve),
                     ("create_editor_edit_session", create_session),
                     ("update_job_payload_edit_session", patch_payload)):
        monkeypatch.setattr(repo, name, fn)
    return seen


@pytest.fixture()
def vary_client(client, monkeypatch):
    client.app.state.pool = _Pool()
    return client


def _post(client, make_token, *, body=None, key="k1"):
    headers = {"Authorization": f"Bearer {make_token(sub='user-1')}"}
    if key:
        headers["Idempotency-Key"] = key
    return client.post("/v1/projects/p1/editor:generate-image",
                       json=body or {"mode": "vary", "source": {"src": SRC},
                                     "changes": [{"type": "bg", "value": "studio"}]},
                       headers=headers)


def _settings(client, monkeypatch, **kw):
    monkeypatch.setattr(client.app.state, "settings",
                        make_settings(r2_bucket="b", **kw), raising=False)


def test_flag_off_creates_no_session(vary_client, make_token, monkeypatch):
    seen = _wire_route(monkeypatch)
    _settings(vary_client, monkeypatch, editor_vary_intent_qc="off",
              generation_run_log="off")
    r = _post(vary_client, make_token)
    assert r.status_code in (200, 202), r.text
    assert seen["sessions"] == [] and seen["payload_writes"] == []


def test_mode_new_never_creates_a_session(vary_client, make_token, monkeypatch):
    seen = _wire_route(monkeypatch)
    _settings(vary_client, monkeypatch, editor_vary_intent_qc="enforce",
              generation_run_log="shadow")
    r = _post(vary_client, make_token,
              body={"mode": "new", "colorId": "base", "cutType": "styling"})
    assert r.status_code in (200, 202), r.text
    assert seen["sessions"] == []


def test_enabled_vary_creates_one_session(vary_client, make_token, monkeypatch):
    seen = _wire_route(monkeypatch)
    _settings(vary_client, monkeypatch, editor_vary_intent_qc="enforce",
              generation_run_log="shadow")
    r = _post(vary_client, make_token)
    assert r.status_code in (200, 202), r.text
    assert len(seen["sessions"]) == 1
    kw = seen["sessions"][0]
    assert kw["source_asset_id"] == SRC_ID
    assert kw["edit_type"] == "BACKGROUND_ONLY"
    assert kw["allowed_scope"]["allowedObservations"]
    assert len(seen["payload_writes"]) == 1


def test_generation_log_off_is_a_misconfiguration(vary_client, make_token, monkeypatch):
    seen = _wire_route(monkeypatch)
    _settings(vary_client, monkeypatch, editor_vary_intent_qc="shadow",
              generation_run_log="off")
    r = _post(vary_client, make_token)
    assert r.status_code == 503 and r.json()["error"]["code"] == "misconfigured_feature"
    assert seen["sessions"] == []


def test_unknown_change_type_is_rejected_before_any_job(vary_client, make_token,
                                                        monkeypatch):
    seen = _wire_route(monkeypatch)
    _settings(vary_client, monkeypatch, editor_vary_intent_qc="enforce",
              generation_run_log="shadow")
    r = _post(vary_client, make_token,
              body={"mode": "vary", "source": {"src": SRC},
                    "changes": [{"type": "vibe", "value": "x"}]})
    assert r.status_code == 400 and r.json()["error"]["code"] == "unknown_change_type"
    assert seen["sessions"] == [] and seen["reserved"] == []


def test_missing_source_asset_is_rejected(vary_client, make_token, monkeypatch):
    seen = _wire_route(monkeypatch, asset=False)
    _settings(vary_client, monkeypatch, editor_vary_intent_qc="enforce",
              generation_run_log="shadow")
    r = _post(vary_client, make_token)
    assert r.status_code == 400 and r.json()["error"]["code"] == "source_asset_missing"
    assert seen["sessions"] == []


def test_same_key_same_request_reuses_the_session(vary_client, make_token, monkeypatch):
    prior_session = {"id": "sess-1", "source_asset_id": SRC_ID,
                     "edit_type": "BACKGROUND_ONLY",
                     "requested_adjustments": {"changes": [{"type": "bg",
                                                            "value": "studio"}]}}
    seen = _wire_route(monkeypatch,
                       prior_job={"id": "job-1", "payload": {"mode": "vary"}},
                       prior_session=prior_session)
    _settings(vary_client, monkeypatch, editor_vary_intent_qc="enforce",
              generation_run_log="shadow")
    r = _post(vary_client, make_token)
    assert r.status_code in (200, 202)
    assert seen["sessions"] == [] and seen["reserved"] == []
    assert seen["payload_writes"] == []


def test_same_key_different_request_is_a_conflict(vary_client, make_token, monkeypatch):
    prior_session = {"id": "sess-1", "source_asset_id": "other",
                     "edit_type": "BACKGROUND_ONLY",
                     "requested_adjustments": {"changes": []}}
    seen = _wire_route(monkeypatch,
                       prior_job={"id": "job-1", "payload": {"mode": "vary"}},
                       prior_session=prior_session)
    _settings(vary_client, monkeypatch, editor_vary_intent_qc="enforce",
              generation_run_log="shadow")
    r = _post(vary_client, make_token)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "idempotency_conflict"
    assert seen["sessions"] == [] and seen["reserved"] == []


def test_parent_output_is_linked_only_when_unique(vary_client, make_token, monkeypatch):
    seen = _wire_route(monkeypatch,
                       unique_output={"output_id": "out-1",
                                      "generation_run_id": "run-1", "status": "linked"})
    _settings(vary_client, monkeypatch, editor_vary_intent_qc="enforce",
              generation_run_log="shadow")
    _post(vary_client, make_token)
    assert seen["sessions"][0]["parent_output_id"] == "out-1"


def test_ambiguous_source_lineage_stays_null(vary_client, make_token, monkeypatch):
    """여러 output 이 걸리면 하나를 고르는 건 추정이지 계보가 아니다."""
    seen = _wire_route(monkeypatch,
                       unique_output={"output_id": None, "generation_run_id": None,
                                      "status": "ambiguous"})
    _settings(vary_client, monkeypatch, editor_vary_intent_qc="enforce",
              generation_run_log="shadow")
    _post(vary_client, make_token)
    kw = seen["sessions"][0]
    assert kw["parent_output_id"] is None
    assert kw["locked_invariants"]["lineageStatus"] == "ambiguous"


# ── repo: 유일 output 조회 ──────────────────────────────────────────────────

class _OutCur:
    def __init__(self, rows):
        self.rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, sql, params=None):
        return None

    async def fetchall(self):
        return self.rows


class _OutConn:
    def __init__(self, rows):
        self.rows = rows

    def cursor(self):
        return _OutCur(self.rows)


@pytest.mark.parametrize("rows,status,out_id", [
    ([], "none", None),
    ([{"id": "o1", "generation_run_id": "r1"}], "linked", "o1"),
    ([{"id": "o1", "generation_run_id": "r1"},
      {"id": "o2", "generation_run_id": "r2"}], "ambiguous", None),
])
def test_unique_output_lookup(rows, status, out_id):
    got = asyncio.run(repo.get_unique_output_for_asset(_OutConn(rows), "a1"))
    assert got["status"] == status and got["output_id"] == out_id


# ── finalize 계보 ───────────────────────────────────────────────────────────

class _FinCur:
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
            raise errors.UndefinedColumn("nope")

    async def fetchone(self):
        low = self._last.lower()
        if "for update" in low:
            return {"id": "job-1"}
        if "max(sort_order)" in low:
            return {"v": 0}
        if low.startswith("insert into wardrobe_images"):
            return {"id": "w-1"}
        if low.startswith("insert into generation_outputs"):
            return {"id": "out-new"}
        if low.startswith("update edit_sessions") and "returning" in low:
            return {"id": "sess-1", "status": "pass", "completed_at": "t",
                    "retry_count": 0}
        return None


class _FinConn:
    def __init__(self, sink, fail_output=False):
        self.sink = sink
        self.fail_output = fail_output

    def cursor(self):
        return _FinCur(self.sink, self.fail_output)


def _finalize(monkeypatch, *, edit_session=None, fail_output=False):
    async def consume(conn, **kw):
        return 3

    monkeypatch.setattr(repo, "_consume_buckets", consume)
    sink: list = []
    out = asyncio.run(repo.finalize_editor_image_success(
        _FinConn(sink, fail_output), job_id="j1", lease_token="t", user_id="u1",
        project_id="p1",
        image={"asset_id": "a-new", "bucket": "b", "key": "k", "mime": "image/png",
               "size": 1, "width": 2, "height": 3},
        group=None, cut_type="styling", reserved=1, charge=1, metadata={},
        edit_session=edit_session))
    return sink, out


SESSION_FIN = {"id": "sess-1", "status": "pass", "qc_status": "pass",
               "qc_result": {"decision": "pass"},
               "lineage": {"generation_run_id": "run-1", "parent_output_id": "out-src",
                           "output_sha256": "sha-x",
                           "transformation": {"editorVary": {"changes": []}}}}


def test_wardrobe_row_carries_session_and_status(monkeypatch):
    sink, _ = _finalize(monkeypatch, edit_session=SESSION_FIN)
    sql, params = [(s, p) for s, p in sink
                   if s.startswith("insert into wardrobe_images")][0]
    assert "edit_session_id" in sql and "qc_status" in sql
    assert params[-2] == "sess-1" and params[-1] == "pass"


def test_generation_output_has_editor_vary_shape(monkeypatch):
    sink, _ = _finalize(monkeypatch, edit_session=SESSION_FIN)
    sql, params = [(s, p) for s, p in sink
                   if s.startswith("insert into generation_outputs")][0]
    assert "mannequin_cut_id, asset_id" in sql and "returning id" in sql
    assert params[0] == "run-1"            # 이번 호출
    assert params[2] == "a-new"            # 생성 asset
    assert params[3] == "sha-x"            # 최종 SHA
    assert params[5] == "out-src"          # parent output
    assert params[6] == "sess-1"           # edit session


def test_session_is_linked_to_the_returned_output(monkeypatch):
    sink, _ = _finalize(monkeypatch, edit_session=SESSION_FIN)
    link = [(s, p) for s, p in sink
            if s.startswith("update edit_sessions set output_id")]
    assert link and link[0][1] == ("out-new", "sess-1")


def test_lineage_failure_is_not_fail_open_for_vary(monkeypatch):
    from psycopg import errors
    with pytest.raises(errors.DatabaseError):
        _finalize(monkeypatch, edit_session=SESSION_FIN, fail_output=True)


def test_legacy_editor_finalize_is_unchanged(monkeypatch):
    """mode:new·플래그 off 는 세션 인자가 없어 계보 경로를 타지 않는다."""
    sink, out = _finalize(monkeypatch, edit_session=None)
    assert not [s for s, _p in sink if s.startswith("insert into generation_outputs")]
    ward = [(s, p) for s, p in sink if s.startswith("insert into wardrobe_images")][0]
    assert ward[1][-2] is None and ward[1][-1] is None
    assert out is not None


# ── 워커 preflight ──────────────────────────────────────────────────────────

def _preflight(monkeypatch, *, session, genlog="shadow", src_id=SRC_ID):
    seen = {"failed": [], "transitions": []}

    async def get_session(conn, sid):
        return session

    async def update_session(conn, **kw):
        seen["transitions"].append(kw)
        if kw.get("status") == "running" and session.get("_block"):
            raise repo.InvalidEditTransition("nope")

    monkeypatch.setattr(repo, "get_edit_session", get_session)
    monkeypatch.setattr(repo, "update_edit_session", update_session)
    monkeypatch.setattr(eij, "_emit", lambda *a, **k: _noop())

    async def fail(msg, meta):
        seen["failed"].append(meta)

    app = types.SimpleNamespace(state=types.SimpleNamespace(
        settings=make_settings(editor_vary_intent_qc="enforce",
                               generation_run_log=genlog, r2_bucket="b"),
        pool=_Pool(), r2=None))
    job = {"id": "j1", "user_id": "u1", "project_id": "p1"}
    ctx = asyncio.run(eij._vary_preflight(
        app, job, session_id="sess-1", src_asset={"id": src_id},
        changes=[{"type": "bg"}], fail=fail))
    return ctx, seen


async def _noop():
    return None


OK_SESSION = {"id": "sess-1", "job_id": "j1", "source_kind": "editor_asset",
              "source_asset_id": SRC_ID, "baseline_id": None, "status": "queued",
              "parent_output_id": None}


def test_preflight_passes_for_a_healthy_session(monkeypatch):
    ctx, seen = _preflight(monkeypatch, session=OK_SESSION)
    assert ctx and ctx["edit_type"] == "BACKGROUND_ONLY"
    assert seen["failed"] == []


@pytest.mark.parametrize("patch,reason", [
    ({"status": "pass"}, "edit_session_not_runnable"),
    ({"job_id": "other"}, "edit_session_job_mismatch"),
    ({"source_kind": "approved_baseline"}, "edit_session_source_mismatch"),
    ({"source_asset_id": "different"}, "edit_session_source_mismatch"),
    ({"baseline_id": "b1"}, "edit_session_source_mismatch"),
])
def test_preflight_blocks_bad_sessions(monkeypatch, patch, reason):
    ctx, seen = _preflight(monkeypatch, session={**OK_SESSION, **patch})
    assert ctx is None
    assert seen["failed"][0]["error"] == reason


def test_preflight_blocks_when_session_is_missing(monkeypatch):
    ctx, seen = _preflight(monkeypatch, session=None)
    assert ctx is None and seen["failed"][0]["error"] == "edit_session_missing"


def test_preflight_blocks_on_failed_transition(monkeypatch):
    ctx, seen = _preflight(monkeypatch, session={**OK_SESSION, "_block": True})
    assert ctx is None and seen["failed"][0]["error"] == "edit_session_not_runnable"


def test_preflight_blocks_when_generation_log_is_off(monkeypatch):
    ctx, seen = _preflight(monkeypatch, session=OK_SESSION, genlog="off")
    assert ctx is None and seen["failed"][0]["error"] == "misconfigured_feature"


# ── 로그 위생 ───────────────────────────────────────────────────────────────

def test_provider_category_keeps_no_raw_text():
    from app.agents.gemini_image import GeminiError
    cat = eij._provider_category(
        GeminiError("Gemini 503: https://host?key=SECRET body"))
    assert cat == "http_503" and "SECRET" not in cat


def test_r2_cleanup_failure_does_not_log_the_key(monkeypatch, caplog):
    class R2:
        def delete(self, key):
            raise RuntimeError("boom")

    app = types.SimpleNamespace(state=types.SimpleNamespace(r2=R2()))
    with caplog.at_level("WARNING"):
        asyncio.run(eij._r2_cleanup(app, "users/u1/projects/p1/ai/j/secret.png"))
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "secret.png" not in msgs and "users/" not in msgs
    assert "RuntimeError" in msgs
