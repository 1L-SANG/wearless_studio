import asyncio
import contextlib
import inspect
from pathlib import Path
from types import SimpleNamespace

from app import repo
from app.workers.draft_asset_reclaimer import DraftAssetReclaimer


MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "supabase/migrations/20260822010000_ai_output_cleanup_intents.sql"
)


class _Conn:
    def __init__(self, events):
        self.events = events

    async def commit(self):
        self.events.append("commit")


class _Pool:
    def __init__(self, events):
        self.events = events

    def connection(self):
        @contextlib.asynccontextmanager
        async def _cm():
            yield _Conn(self.events)

        return _cm()


class _R2:
    def __init__(self, events, *, fail_delete=False):
        self.events = events
        self.fail_delete = fail_delete
        self.objects = {"users/u1/projects/p1/ai/j1/a1.png"}

    def delete(self, key):
        self.events.append("delete")
        if self.fail_delete:
            raise RuntimeError("delete failed")
        self.objects.discard(key)

    def head(self, key):
        self.events.append("head")
        return {"size": 1, "mime": "image/png"} if key in self.objects else None


def test_ai_output_cleanup_migration_declares_private_outbox():
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    assert "create table if not exists public.ai_output_cleanup_intents" in sql
    assert "job_id uuid not null" in sql
    assert "asset_id" not in sql
    assert "references public.jobs" not in sql
    assert "references auth.users" not in sql
    assert "project_id" not in sql
    assert "r2_bucket" not in sql
    assert "r2_key text not null unique" in sql
    assert "status in ('pending','delete_pending')" in sql
    assert "alter table public.ai_output_cleanup_intents enable row level security" in sql
    assert "revoke all on public.ai_output_cleanup_intents from anon, authenticated" in sql


def test_reclaimer_drains_unpublished_ai_output_after_restart(monkeypatch):
    events = []
    key = "users/u1/projects/p1/ai/j1/a1.png"

    async def reclaim_drafts(conn):
        events.append("drafts")
        return []

    async def claim_outputs(conn):
        events.append("outputs")
        return [{"id": "intent-1", "r2_key": key}]

    async def clear_output(conn, intent_id):
        events.append(f"clear:{intent_id}")

    monkeypatch.setattr(repo, "reclaim_stale_unreferenced_draft_assets", reclaim_drafts)
    monkeypatch.setattr(
        repo,
        "claim_unpublished_ai_output_cleanup_intents",
        claim_outputs,
        raising=False,
    )
    monkeypatch.setattr(
        repo,
        "clear_ai_output_cleanup_intent",
        clear_output,
        raising=False,
    )

    worker = DraftAssetReclaimer(SimpleNamespace(
        state=SimpleNamespace(pool=_Pool(events), r2=_R2(events))
    ))

    asyncio.run(worker._sweep_once())

    assert events == [
        "drafts",
        "outputs",
        "commit",
        "delete",
        "head",
        "clear:intent-1",
        "commit",
    ]


def test_reclaimer_keeps_retry_state_when_delete_fails(monkeypatch):
    events = []
    key = "users/u1/projects/p1/ai/j1/a1.png"

    async def reclaim_drafts(conn):
        return []

    async def claim_outputs(conn):
        return [{"id": "intent-1", "r2_key": key}]

    async def forbidden_clear(conn, intent_id):
        events.append(f"clear:{intent_id}")

    monkeypatch.setattr(repo, "reclaim_stale_unreferenced_draft_assets", reclaim_drafts)
    monkeypatch.setattr(
        repo,
        "claim_unpublished_ai_output_cleanup_intents",
        claim_outputs,
        raising=False,
    )
    monkeypatch.setattr(
        repo,
        "clear_ai_output_cleanup_intent",
        forbidden_clear,
        raising=False,
    )

    worker = DraftAssetReclaimer(SimpleNamespace(
        state=SimpleNamespace(pool=_Pool(events), r2=_R2(events, fail_delete=True))
    ))

    asyncio.run(worker._sweep_once())

    assert events == ["commit", "delete"]


def test_reclaimer_does_not_delete_when_repo_reports_no_unpublished_outputs(monkeypatch):
    events = []

    async def reclaim_drafts(conn):
        return []

    async def claim_outputs(conn):
        return []

    monkeypatch.setattr(repo, "reclaim_stale_unreferenced_draft_assets", reclaim_drafts)
    monkeypatch.setattr(
        repo,
        "claim_unpublished_ai_output_cleanup_intents",
        claim_outputs,
        raising=False,
    )

    worker = DraftAssetReclaimer(SimpleNamespace(
        state=SimpleNamespace(pool=_Pool(events), r2=_R2(events))
    ))

    asyncio.run(worker._sweep_once())

    assert "delete" not in events


def test_cleanup_claim_waits_until_owner_job_is_not_active():
    source = inspect.getsource(repo.claim_unpublished_ai_output_cleanup_intents)

    assert "status in ('pending','running')" in source
    assert "not exists" in source
    assert "from jobs j" in source


def test_success_finalizers_clear_cleanup_intent_inside_publish_transaction():
    for finalizer in (
        repo.finalize_editor_image_success,
        repo.finalize_detail_page_success,
    ):
        source = inspect.getsource(finalizer)
        asset_insert = source.index("insert into assets")
        intent_clear = source.index("delete from ai_output_cleanup_intents")
        job_done = source.index("update jobs set status = 'done'")

        assert asset_insert < intent_clear < job_done


# ── 상세 컷 체크포인트 표식(2026-09-23) — 새 테이블 없이 not_before 를 TTL 로 쓴다 ──
class _SqlCursor:
    def __init__(self, log, row=None, rows=None):
        self.log, self.row, self.rows = log, row, rows or []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def execute(self, sql, params=None):
        self.log.append((" ".join(sql.split()), params))

    async def fetchone(self):
        return self.row

    async def fetchall(self):
        return self.rows


class _SqlConn:
    def __init__(self, row=None, rows=None):
        self.log = []
        self.row, self.rows = row, rows

    def cursor(self):
        return _SqlCursor(self.log, self.row, self.rows)


def test_checkpoint_intent_is_due_after_the_ttl_not_now():
    conn = _SqlConn(row={"id": "intent-1"})
    got = asyncio.run(repo.create_ai_checkpoint_intent(
        conn, job_id="j1", r2_key="users/u1/projects/p1/ai/j1/ckpt/x/base.bin",
        ttl_seconds=86400))
    sql, params = conn.log[0]
    assert got == "intent-1"
    assert "insert into ai_output_cleanup_intents" in sql
    assert "now() + make_interval(secs => %s)" in sql
    assert params[-1] == 86400
    # 리클레이머는 not_before 가 지난 표식만 집는다 — TTL 이 곧 "24시간 뒤 삭제"다
    assert "i.not_before <= now()" in " ".join(
        inspect.getsource(repo.claim_unpublished_ai_output_cleanup_intents).split())


def test_checkpoint_lookup_only_sees_failed_jobs_of_the_same_project():
    conn = _SqlConn(rows=[{"id": "i1", "r2_key": "k", "job_id": "j1"}])
    rows = asyncio.run(repo.list_detail_cut_checkpoints(
        conn, user_id="u1", project_id="p1", key_pattern="users/u1/projects/p1/ai/%/ckpt/%"))
    sql, params = conn.log[0]
    assert rows == [{"id": "i1", "r2_key": "k", "job_id": "j1"}]
    assert "j.user_id = %s" in sql and "j.project_id = %s" in sql
    assert "j.kind = 'detail_page'" in sql and "j.status = 'error'" in sql
    assert "i.not_before > now()" in sql and "i.status = 'pending'" in sql
    assert params[:3] == ("u1", "p1", "users/u1/projects/p1/ai/%/ckpt/%")
    # 커서 없는 커넥션(스텁)은 None — 호출자가 이어하기를 끈다
    assert asyncio.run(repo.list_detail_cut_checkpoints(
        object(), user_id="u1", project_id="p1", key_pattern="x")) is None


def test_checkpoint_expiry_expires_now_or_extends_only():
    conn = _SqlConn()
    asyncio.run(repo.set_ai_checkpoint_expiry(conn, ["k1"], ttl_seconds=0))
    asyncio.run(repo.set_ai_checkpoint_expiry(conn, ["k1"], ttl_seconds=86400, extend_only=True))
    asyncio.run(repo.set_ai_checkpoint_expiry(conn, [], ttl_seconds=0))       # 빈 목록은 쿼리 없음
    (expire_sql, expire_params), (extend_sql, extend_params) = conn.log
    assert "set not_before = now() + make_interval(secs => %s)" in expire_sql
    assert expire_params == (0, ["k1"])
    assert "greatest(not_before, now() + make_interval(secs => %s))" in extend_sql
    assert extend_params == (86400, ["k1"])
