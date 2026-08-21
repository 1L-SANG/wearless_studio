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
