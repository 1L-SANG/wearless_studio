"""Phase 2 — Approved Baseline 과 파생 계보.

계약:
  · baseline 은 "가장 최근 생성본"이 아니다. **승인 API 만이** 만든다.
  · project 당 active baseline 은 하나. 새 승인이 이전 것을 supersede 한다(같은 tx).
  · 같은 컷 재승인은 멱등 — baseline 상태(id·approved_at·supersede)는 그대로. 단 시도
    자체는 감사 event(baseline_reapproved)로 남는다.
  · 승인은 선택을 포함하지만(selected_mannequin_id 동기화), 선택은 승인이 아니다.
  · generation_run_id(이 결과를 만든 호출) ≠ parent_output_id(무엇을 편집했는가).
  · 값을 모르는 invariant 는 거짓으로 채우지 않고 unavailable + 사유로 남긴다.
"""

import asyncio
import contextlib
import re
import types

import pytest

from app import repo
from app.services import baseline as bl
from app.workers import mannequin_job as mj

MIGRATION = (
    "/Users/nojeong-un/devs/wearless_studio/supabase/migrations/"
    "20260801020000_approved_baselines.sql"
)


# ── fake DB: 실행된 SQL 을 그대로 붙잡아 계약을 검사한다 ─────────────────────

class _Cur:
    def __init__(self, state):
        self.state = state
        self._last = ""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.state["sql"].append((flat, params))
        self._last = flat.lower()

    async def fetchone(self):
        low = self._last
        if "from projects" in low and "for update" in low:
            return None if self.state.get("not_owner") else {"id": "p1"}
        if "from approved_baselines" in low and "superseded_at is null" in low:
            return self.state.get("active")
        if "from generation_outputs" in low and "mannequin_cut_id" in low:
            return self.state.get("cut_output")
        if "returning" in low and "approved_baselines" in low:
            self.state["inserted"] = True
            return {"id": "base-new", "project_id": "p1",
                    "baseline_cut_id": self.state["cut"]["mannequin_cut_id"],
                    "output_id": (self.state.get("cut_output") or {}).get("id"),
                    "generation_run_id": (self.state.get("cut_output") or {}).get(
                        "generation_run_id"),
                    "locked_invariants": {}, "approved_at": "t", "superseded_at": None}
        return None


class _Conn:
    def __init__(self, state):
        self.state = state

    def cursor(self):
        return _Cur(self.state)

    async def commit(self):
        return None


CUT = {"mannequin_cut_id": "cut-uuid-1", "id": "A-3", "asset_id": "asset-1",
       "qc_scores": {"outcome": "auto_pass"}, "product_id": "prod-1",
       "clothing_type": "top",
       "generation_metadata": {"profileGender": "women", "profileCategory": "top",
                               "promptVersion": "v1", "generationPath": "fresh"}}


def _approve(state=None, cut=None):
    st = {"sql": [], "cut": cut or CUT, **(state or {})}
    out = asyncio.run(repo.approve_mannequin_baseline(
        _Conn(st), user_id="u1", project_id="p1", cut=cut or CUT,
        locked_invariants={"garmentCategory": {"status": "recorded", "value": "top"}}))
    return st, out


def _sql_of(st, prefix):
    return [(s, p) for s, p in st["sql"] if s.lower().startswith(prefix)]


# ── 승인 ─────────────────────────────────────────────────────────────────────

def test_first_approval_creates_active_baseline():
    st, out = _approve({"cut_output": {"id": "out-1", "generation_run_id": "run-1"}})
    assert out["idempotent"] is False and out["superseded_id"] is None
    ins = _sql_of(st, "insert into approved_baselines")
    assert len(ins) == 1
    params = ins[0][1]
    assert params[2] == "cut-uuid-1"     # baseline_cut_id
    assert params[3] == "out-1"          # output_id — 승인 컷의 output
    assert params[4] == "run-1"          # generation_run_id
    assert out["baseline"]["output_id"] == "out-1"


def test_approval_locks_the_project_row_first():
    """동시 승인은 여기서 줄을 선다 — 잠금 없이 읽으면 둘 다 active 를 만든다."""
    st, _ = _approve()
    first = st["sql"][0][0].lower()
    assert "from projects" in first and "for update" in first


def test_approval_of_a_legacy_cut_without_output_still_succeeds():
    """Phase 1 기록 이전 컷도 승인할 수 있다 — output/run 만 null 이다."""
    st, out = _approve({"cut_output": None})
    params = _sql_of(st, "insert into approved_baselines")[0][1]
    assert params[3] is None and params[4] is None
    assert out["baseline"]["baseline_cut_id"] == "cut-uuid-1"


def test_reapproving_the_same_cut_leaves_baseline_state_untouched():
    """멱등 = baseline 상태 불변. 감사 기록까지 없는 게 아니다(시도는 남는다)."""
    st, out = _approve({"active": {"id": "base-old", "baseline_cut_id": "cut-uuid-1"}})
    assert out["idempotent"] is True and out["superseded_id"] is None
    assert not _sql_of(st, "insert into approved_baselines"), "행이 또 생겼다"
    assert not _sql_of(st, "update approved_baselines"), "supersede 가 일어났다"
    events = [s for s, _p in st["sql"]
              if s.startswith("insert into baseline_review_events")]
    assert len(events) == 1 and "baseline_reapproved" in events[0]


def test_new_approval_supersedes_the_previous_baseline():
    st, out = _approve({"active": {"id": "base-old", "baseline_cut_id": "other-cut"}})
    assert out["superseded_id"] == "base-old"
    upd = _sql_of(st, "update approved_baselines")
    assert upd and "superseded_at = now()" in upd[0][0]
    assert upd[0][1] == ("base-old",)
    # supersede 와 새 승인이 같은 tx 안에서 일어난다(커밋은 라우트가 한 번만 한다)
    assert _sql_of(st, "insert into approved_baselines")


def test_supersede_and_approve_are_recorded_as_events():
    st, _ = _approve({"active": {"id": "base-old", "baseline_cut_id": "other-cut"}})
    actions = [p for s, p in st["sql"]
               if s.startswith("insert into baseline_review_events")]
    joined = " ".join(s for s, _p in st["sql"]
                      if s.startswith("insert into baseline_review_events"))
    assert "baseline_superseded" in joined and "baseline_approved" in joined
    assert len(actions) == 2
    assert all("u1" in [str(x) for x in p] for p in actions), "actor 가 빠졌다"


def test_approval_rejects_a_project_the_user_does_not_own():
    with pytest.raises(PermissionError):
        _approve({"not_owner": True})


def test_approval_syncs_the_selected_pointer():
    """승인은 선택을 포함한다 — 기존 UI 는 selected_mannequin_id 만 읽는다."""
    st, _ = _approve()
    upd = _sql_of(st, "update projects set selected_mannequin_id")
    assert upd and upd[0][1] == ("A-3", "p1", "u1")


def test_patching_the_selected_pointer_never_creates_a_baseline():
    """역은 성립하지 않는다 — 선택은 승인이 아니다(PATCH 경로에 baseline 이 없다)."""
    src = inspect_source(repo.patch_project)
    assert "approved_baselines" not in src
    assert "selected_mannequin_id" in repo.PATCHABLE_COLUMNS


def inspect_source(fn):
    import inspect
    return inspect.getsource(fn)


# ── active 단일성·경쟁 조건 (DB 제약) ────────────────────────────────────────

def test_migration_enforces_single_active_baseline_per_project():
    sql = open(MIGRATION, encoding="utf-8").read()
    assert re.search(
        r"create unique index if not exists approved_baselines_one_active_per_project\s+"
        r"on public\.approved_baselines \(project_id\) where superseded_at is null",
        sql), "동시 승인을 막는 partial unique index 가 없다"


def test_migration_protects_approved_cuts_from_deletion():
    """승인된 컷이 지워지면 계보가 끊긴다 — restrict 로 삭제 자체를 막는다."""
    sql = open(MIGRATION, encoding="utf-8").read()
    assert "baseline_cut_id uuid not null references public.mannequin_cuts (id) " \
           "on delete restrict" in sql
    # baseline 은 hard delete 하지 않는다 — supersede 로만 물러난다
    assert "superseded_at timestamptz" in sql


def test_migration_keeps_unbuilt_entities_nullable_without_fk():
    """Product Truth·QC Result 는 아직 없다 — FK 를 걸면 migration 자체가 실패한다."""
    sql = open(MIGRATION, encoding="utf-8").read()
    assert re.search(r"truth_package_id uuid,\s", sql)
    assert re.search(r"baseline_qc_result_id uuid,\s", sql)
    assert "references public.product_truth" not in sql
    assert "qc_scores_snapshot jsonb" in sql, "QC 테이블이 없는 동안의 판정 보존 수단"


def test_migration_scopes_rls_by_project_ownership():
    sql = open(MIGRATION, encoding="utf-8").read()
    assert "alter table public.approved_baselines enable row level security;" in sql
    assert "alter table public.baseline_review_events enable row level security;" in sql
    assert sql.count("p.user_id = (select auth.uid())") >= 2


def test_migration_is_append_only():
    """기존 migration 은 손대지 않는다 — 새 파일만 추가한다."""
    sql = open(MIGRATION, encoding="utf-8").read()
    assert "drop table" not in sql.lower()
    assert not re.search(r"alter table public\.(mannequin_cuts|projects|assets) drop",
                         sql.lower())
    assert "add column if not exists" in sql  # generation_outputs 확장은 additive


# ── locked invariants: 모르는 값을 지어내지 않는다 ───────────────────────────

def test_locked_invariants_cover_every_required_key():
    inv = bl.build_locked_invariants(CUT)
    for key in bl.ALWAYS_LOCKED:
        assert key in inv, f"필수 invariant 누락: {key}"


def test_unavailable_invariants_say_so_instead_of_faking_values():
    inv = bl.build_locked_invariants(CUT)
    for key in ("pose", "camera", "framing", "background", "lighting"):
        assert inv[key]["status"] == "unavailable"
        assert inv[key]["reason"] == "no_structured_profile_prompt_fixed"
    # 실제로 그것들을 고정하는 것은 프롬프트 버전 — 그건 기록된다
    assert inv["promptVersion"] == {"status": "recorded", "value": "v1"}


def test_available_invariants_are_recorded():
    inv = bl.build_locked_invariants(CUT)
    assert inv["garmentCategory"] == {"status": "recorded", "value": "top"}
    assert inv["mannequinIdentity"]["gender"] == "women"
    assert inv["mannequinIdentity"]["baseMannequinAssetId"] is None, \
        "설정값을 승인 시점 사실인 척 적으면 안 된다"


def test_invariants_degrade_honestly_when_metadata_is_missing():
    inv = bl.build_locked_invariants({"generation_metadata": None})
    assert inv["mannequinIdentity"]["status"] == "unavailable"
    assert inv["garmentCategory"]["status"] == "unavailable"
    assert inv["promptVersion"]["status"] == "unavailable"


def test_review_state_marks_explicit_user_approval():
    st = bl.approval_review_state({"qc_scores": {"outcome": "needs_review"}})
    assert st["needsReview"] is True and st["explicitUserApproval"] is True
    assert st["qcOutcome"] == "needs_review"


def test_profile_snapshots_record_absence_not_none():
    snaps = bl.build_profile_snapshots(CUT)
    for key in ("framing_profile", "background_profile", "lighting_profile"):
        assert snaps[key]["available"] is False and snaps[key]["reason"]
    assert snaps["mannequin_profile"]["available"] is True


# ── 파생 계보: parent_output_id ≠ generation_run_id ──────────────────────────

class _FakeRunlog:
    def __init__(self, run_id="run-new"):
        self.run_id = run_id

    def has_recorded_success(self, candidate=None):
        return True

    def output_lineage(self, image, candidate=None, carrier_run_id=None):
        return {"generation_run_id": self.run_id, "output_sha256": "sha-new",
                "post_processed": False}


def test_edit_output_links_to_the_baseline_output_as_parent():
    res = types.SimpleNamespace(image=b"x", mime="image/png")
    lin = mj._output_lineage(
        _FakeRunlog(), res, "A", None, None,
        {"generation_output_id": "out-baseline", "baseline_id": "base-1",
         "generation_run_id": "run-baseline"})
    assert lin["parent_output_id"] == "out-baseline"
    assert lin["baseline_id"] == "base-1"
    # 두 축을 섞지 않는다: 이 결과를 만든 호출은 새 run 이다
    assert lin["generation_run_id"] == "run-new"
    assert lin["generation_run_id"] != "run-baseline"


def test_fresh_generation_has_no_parent_output():
    res = types.SimpleNamespace(image=b"x", mime="image/png")
    lin = mj._output_lineage(_FakeRunlog(), res, "A", None, None, None)
    assert lin["parent_output_id"] is None and lin["baseline_id"] is None
    assert lin["generation_run_id"] == "run-new"


def test_legacy_parent_without_output_row_falls_back_to_null():
    """output 행이 없는 컷이 부모면 잘못된 부모를 지어내지 않는다."""
    res = types.SimpleNamespace(image=b"x", mime="image/png")
    lin = mj._output_lineage(
        _FakeRunlog(), res, "A", None, None,
        {"asset_id": "asset-legacy", "generation_output_id": None,
         "generation_run_id": None, "baseline_id": None})
    assert lin["parent_output_id"] is None
    assert lin["generation_run_id"] == "run-new", "새 호출 계보까지 잃으면 안 된다"


# ── finalize 가 실제로 두 컬럼을 쓴다 ────────────────────────────────────────

class _FinalizeCur:
    def __init__(self, sink, fail=False):
        self.sink = sink
        self.fail = fail

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.sink.append((flat, params))
        self._last = flat
        if self.fail and flat.startswith("insert into generation_outputs"):
            from psycopg import errors
            raise errors.UndefinedColumn("column does not exist")

    async def fetchone(self):
        low = self._last.lower()
        if "for update" in low:
            return {"id": "job-1"}
        if "max(version)" in low:
            return {"v": 2}
        if "returning id" in low:
            return {"id": "cut-uuid-9"}
        return None


class _FinalizeConn:
    def __init__(self, sink, fail=False):
        self.sink = sink
        self.fail = fail

    def cursor(self):
        return _FinalizeCur(self.sink, self.fail)


def _finalize(monkeypatch, lineage, *, fail=False):
    async def consume(conn, **kw):
        return 5

    monkeypatch.setattr(repo, "_consume_buckets", consume)
    sink: list = []
    cand = {"asset_id": "a-9", "bucket": "b", "key": "k", "mime": "image/png",
            "size": 1, "width": 2, "height": 3, "candidate": "A", "base_fit": "regular",
            "qc_scores": None, "generation_lineage": lineage}
    out = asyncio.run(repo.finalize_mannequin_success(
        _FinalizeConn(sink, fail), job_id="j9", lease_token="t", user_id="u1",
        project_id="p1", candidates=[cand], reserved=1, charge=1, metadata={}))
    return sink, out


LINEAGE = {"generation_run_id": "run-new", "output_sha256": "sha-new",
           "post_processed": False, "parent_output_id": "out-baseline",
           "baseline_id": "base-1"}


def test_finalize_persists_parent_and_baseline_columns(monkeypatch):
    sink, out = _finalize(monkeypatch, LINEAGE)
    ins = [(s, p) for s, p in sink if s.startswith("insert into generation_outputs")]
    assert len(ins) == 1
    sql, params = ins[0]
    assert "parent_output_id" in sql and "baseline_id" in sql
    assert params[0] == "run-new" and params[7] == "out-baseline" and params[8] == "base-1"
    assert out["cuts"], "컷 출고는 정상"


def test_missing_lineage_columns_do_not_block_cut_delivery(monkeypatch, caplog):
    """migration 미적용(컬럼 없음)이어도 컷은 나간다 — 기록은 부가 기능이다."""
    with caplog.at_level("WARNING"):
        _sink, out = _finalize(monkeypatch, LINEAGE, fail=True)
    assert out["cuts"], "계보 기록 실패가 출고를 막았다"
    assert any("generation_outputs insert failed" in r.getMessage()
               for r in caplog.records)


# ── 부모 조회가 baseline 을 함께 준다 ────────────────────────────────────────

class _ParentCur:
    def __init__(self, sink, fail_lineage=False):
        self.sink = sink
        self.fail_lineage = fail_lineage
        self._kind = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.sink.append(flat)
        if "approved_baselines" in flat:
            self._kind = "lineage"
            if self.fail_lineage:
                from psycopg import errors
                raise errors.UndefinedTable("relation does not exist")
        elif "mannequin_cuts" in flat:
            self._kind = "parent"
        else:
            self._kind = "other"

    async def fetchone(self):
        if self._kind == "parent":
            return {"id": "A-2", "mannequin_cut_id": "cut-2", "asset_id": "asset-2",
                    "r2_key": "k", "mime_type": "image/png", "generation_metadata": {}}
        if self._kind == "lineage":
            return {"generation_output_id": "out-2", "generation_run_id": "run-2",
                    "baseline_id": "base-2"}
        return None


class _ParentConn:
    def __init__(self, sink, fail_lineage=False):
        self.sink = sink
        self.fail_lineage = fail_lineage

    def cursor(self):
        return _ParentCur(self.sink, self.fail_lineage)


def test_edit_parent_carries_the_active_baseline_id():
    out = asyncio.run(repo.get_mannequin_edit_parent(_ParentConn([]), "u1", "p1"))
    assert out["baseline_id"] == "base-2"
    assert out["generation_output_id"] == "out-2"


def test_edit_parent_still_works_without_baseline_tables():
    """migration 미적용에서도 조정 편집은 살아 있어야 한다(계보만 빈다)."""
    sink: list = []
    out = asyncio.run(repo.get_mannequin_edit_parent(
        _ParentConn(sink, fail_lineage=True), "u1", "p1"))
    assert out is not None and out["r2_key"] == "k"
    assert out["baseline_id"] is None and out["generation_output_id"] is None
    assert any(s.startswith("rollback to savepoint edit_parent_lineage") for s in sink)


# ── 라우트 계약: 인증·소유권·404 ─────────────────────────────────────────────

class _RoutePool:
    def __init__(self, state):
        self.state = state

    def connection(self):
        @contextlib.asynccontextmanager
        async def _cm():
            yield _Conn(self.state)

        return _cm()


@pytest.fixture()
def approve_client(client, monkeypatch):
    """승인 라우트용 — DB 는 fake, repo 는 테스트별로 갈아끼운다."""
    client.app.state.pool = _RoutePool({"sql": [], "cut": CUT})
    return client


def _auth(make_token, sub="user-1"):
    return {"Authorization": f"Bearer {make_token(sub=sub)}"}


def test_approve_route_requires_authentication(approve_client):
    r = approve_client.post("/v1/projects/p1/mannequins:approve", json={"cutId": "A-3"})
    assert r.status_code in (401, 403)


def test_approve_route_404s_for_a_project_the_user_does_not_own(approve_client,
                                                                make_token, monkeypatch):
    async def no_project(conn, user_id, project_id):
        return None

    monkeypatch.setattr(repo, "get_project", no_project)
    r = approve_client.post("/v1/projects/p1/mannequins:approve",
                            json={"cutId": "A-3"}, headers=_auth(make_token))
    assert r.status_code == 404


def test_approve_route_404s_for_a_cut_from_another_project(approve_client, make_token,
                                                           monkeypatch):
    """다른 프로젝트의 컷 id 를 넣어도 조회가 project 범위라 걸리지 않는다."""
    async def project(conn, user_id, project_id):
        return {"id": project_id}

    async def no_cut(conn, user_id, project_id, cut_id):
        return None

    monkeypatch.setattr(repo, "get_project", project)
    monkeypatch.setattr(repo, "get_mannequin_cut_for_approval", no_cut)
    r = approve_client.post("/v1/projects/p1/mannequins:approve",
                            json={"cutId": "B-9"}, headers=_auth(make_token))
    assert r.status_code == 404


def test_approve_route_returns_the_baseline_and_records_invariants(approve_client,
                                                                   make_token,
                                                                   monkeypatch):
    seen = {}

    async def project(conn, user_id, project_id):
        return {"id": project_id}

    async def cut(conn, user_id, project_id, cut_id):
        return CUT

    async def approve(conn, **kw):
        seen.update(kw)
        return {"baseline": {"id": "base-1", "project_id": "p1",
                             "baseline_cut_id": "cut-uuid-1", "output_id": "out-1",
                             "generation_run_id": "run-1",
                             "locked_invariants": kw["locked_invariants"],
                             "approved_at": "2026-08-01T00:00:00Z",
                             "superseded_at": None},
                "superseded_id": "base-0", "idempotent": False}

    monkeypatch.setattr(repo, "get_project", project)
    monkeypatch.setattr(repo, "get_mannequin_cut_for_approval", cut)
    monkeypatch.setattr(repo, "approve_mannequin_baseline", approve)
    r = approve_client.post("/v1/projects/p1/mannequins:approve",
                            json={"cutId": "A-3"}, headers=_auth(make_token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cutId"] == "A-3" and body["supersededBaselineId"] == "base-0"
    assert body["idempotent"] is False
    inv = body["lockedInvariants"]
    assert inv["garmentCategory"] == {"status": "recorded", "value": "top"}
    assert inv["pose"]["status"] == "unavailable"
    assert inv["approvalReview"]["explicitUserApproval"] is True
    # 프로필 스냅샷 4종이 승인에 전달됐는가
    assert seen["framing_profile"]["available"] is False


def test_approve_route_maps_ownership_failure_to_404(approve_client, make_token,
                                                     monkeypatch):
    async def project(conn, user_id, project_id):
        return {"id": project_id}

    async def cut(conn, user_id, project_id, cut_id):
        return CUT

    async def approve(conn, **kw):
        raise PermissionError("project_not_owned")

    monkeypatch.setattr(repo, "get_project", project)
    monkeypatch.setattr(repo, "get_mannequin_cut_for_approval", cut)
    monkeypatch.setattr(repo, "approve_mannequin_baseline", approve)
    r = approve_client.post("/v1/projects/p1/mannequins:approve",
                            json={"cutId": "A-3"}, headers=_auth(make_token))
    assert r.status_code == 404


def test_baseline_route_returns_null_before_any_approval(approve_client, make_token,
                                                         monkeypatch):
    async def project(conn, user_id, project_id):
        return {"id": project_id}

    async def none_baseline(conn, project_id):
        return None

    monkeypatch.setattr(repo, "get_project", project)
    monkeypatch.setattr(repo, "get_active_baseline", none_baseline)
    r = approve_client.get("/v1/projects/p1/mannequins/baseline",
                           headers=_auth(make_token))
    assert r.status_code == 200 and r.json() is None


# ── 릴리스 전 보정: actor 삭제가 승인 기록을 지우지 않는다 ───────────────────

INTEGRITY_MIGRATION = (
    "/Users/nojeong-un/devs/wearless_studio/supabase/migrations/"
    "20260801030000_baseline_integrity.sql"
)


def _integrity_sql():
    return open(INTEGRITY_MIGRATION, encoding="utf-8").read()


def test_approved_by_becomes_nullable():
    """actor 가 사라져도 행이 남으려면 컬럼이 null 을 받아야 한다."""
    assert re.search(
        r"alter table public\.approved_baselines\s+alter column approved_by drop not null",
        _integrity_sql())


def test_cascade_fk_on_approved_by_is_dropped():
    """cascade 였다면 탈퇴 한 번에 승인 baseline 이 통째로 사라진다."""
    sql = _integrity_sql()
    assert "drop constraint if exists approved_baselines_approved_by_fkey" in sql
    idx_drop = sql.index("drop constraint if exists approved_baselines_approved_by_fkey")
    idx_add = sql.index("add constraint approved_baselines_approved_by_fkey")
    assert idx_drop < idx_add, "drop 이 add 보다 뒤면 기존 cascade 가 남는다"


def test_approved_by_fk_is_recreated_as_set_null():
    assert re.search(
        r"add constraint approved_baselines_approved_by_fkey\s+"
        r"foreign key \(approved_by\) references auth\.users \(id\) on delete set null",
        _integrity_sql())


def test_baseline_actor_policy_matches_the_audit_table():
    """두 테이블의 actor 삭제 정책이 갈라져 있으면 한쪽만 증발한다."""
    base_sql = open(MIGRATION, encoding="utf-8").read()
    assert "actor_id uuid references auth.users (id) on delete set null" in base_sql
    assert "on delete set null" in _integrity_sql()
    # 보정 후 approved_baselines 에 cascade 정책이 남아 있으면 안 된다
    assert "references auth.users (id) on delete cascade" not in _integrity_sql()


def test_baseline_row_survives_actor_deletion_by_contract():
    """정적 계약: 승인 baseline 은 actor 삭제로 지워지지 않고 approved_by 만 null 이 된다.

    (실행 검증은 migration 적용 후 가능 — 여기서는 스키마 계약을 고정한다.)
    """
    sql = _integrity_sql()
    assert "alter column approved_by drop not null" in sql
    assert "on delete set null" in sql
    assert "drop table" not in sql.lower() and "delete from" not in sql.lower()


def test_audit_action_values_are_constrained_to_the_three_in_use():
    """자유 text 는 오타를 영구 데이터로 만든다. 목록은 코드 실측 3개뿐이다."""
    sql = _integrity_sql()
    assert re.search(
        r"check \(action in \('baseline_approved', 'baseline_superseded', "
        r"'baseline_reapproved'\)\)", sql)


def test_constrained_actions_match_every_action_the_code_writes():
    """코드가 쓰는 action 과 CHECK 목록이 어긋나면 승인이 런타임에 죽는다."""
    import inspect
    src = inspect.getsource(repo.approve_mannequin_baseline)
    used = set(re.findall(r"'(baseline_[a-z_]+)'", src))
    allowed = set(re.findall(
        r"check \(action in \((.*?)\)\)", _integrity_sql(), re.S)[0].replace("'", "").split(", "))
    allowed = {a.strip() for a in allowed}
    assert used, "action 리터럴을 찾지 못했다 — 테스트 전제 확인"
    assert used <= allowed, f"CHECK 에 없는 action 사용: {used - allowed}"
    assert allowed <= used, f"코드가 쓰지 않는 action 을 추측으로 허용: {allowed - used}"


def test_integrity_migration_is_append_only():
    sql = _integrity_sql()
    assert "drop table" not in sql.lower()
    # 기존 migration 파일은 손대지 않는다 — 그 파일에는 여전히 cascade 원문이 있다
    assert "on delete cascade" in open(MIGRATION, encoding="utf-8").read()


# ── Phase 경계 계약 (동작 변경 아님, 사실 고정) ──────────────────────────────

def test_edit_parent_still_resolves_by_selected_pointer_in_phase_2():
    """Phase 2 는 저장·계보 인프라까지다. edit input 정본 전환은 Phase 3."""
    import inspect
    src = inspect.getsource(repo.get_mannequin_edit_parent)
    assert "pr.selected_mannequin_id =" in src, "선택 포인터 우선 정렬이 바뀌었다"
    assert "Phase 3" in src, "경계가 코드에 남아 있어야 한다"


def test_baseline_id_is_null_when_the_edited_cut_is_not_the_baseline():
    """다른 컷을 선택한 상태의 조정은 baseline 파생이 아니다 — null 이 정직하다."""
    res = types.SimpleNamespace(image=b"x", mime="image/png")
    lin = mj._output_lineage(
        _FakeRunlog(), res, "A", None, None,
        {"generation_output_id": "out-other", "baseline_id": None})
    assert lin["parent_output_id"] == "out-other"
    assert lin["baseline_id"] is None
