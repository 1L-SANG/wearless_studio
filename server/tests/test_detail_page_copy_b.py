"""Concurrent page copy never gates cuts; no real providers or image services."""
import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from app import repo
from app.agents import page_copy
from app.workers import detail_page_job as worker
from conftest import fake_worker_app, make_settings, worker_job
from test_woc6b_part_copy import board, analysis
from page_copy_test_support import page_copy_prompt_dir


def setup(monkeypatch, copy, cuts, *, key=True, timeout=90, persist_failure=False):
    original = analysis()
    monkeypatch.setattr(repo, "get_project", AsyncMock(return_value={"copywriting": True}))
    monkeypatch.setattr(repo, "get_product", AsyncMock(return_value={"name": "셔츠", "colors": []}))
    monkeypatch.setattr(repo, "get_storyboard", AsyncMock(return_value=board()))
    monkeypatch.setattr(repo, "get_analysis", AsyncMock(return_value=original))
    monkeypatch.setattr(page_copy, "generate", copy)
    monkeypatch.setattr(worker, "_gen_cuts", cuts)
    events, assembled = [], {}

    async def emit(pool, job_id, event_type, payload):
        events.append((event_type, deepcopy(payload)))

    def assemble(storyboard, cut_results, copy_results, product, enabled, **kwargs):
        assembled.update(copy=deepcopy(copy_results), analysis=deepcopy(kwargs["analysis"]))
        return [{"id": "assembled", "elements": []}]

    monkeypatch.setattr(worker, "_emit", emit)
    monkeypatch.setattr(worker.page_assembler, "assemble", assemble)
    save = AsyncMock(side_effect=RuntimeError("save failed") if persist_failure else None)
    success = AsyncMock(return_value={"editor_blocks": [], "available": 1})
    failure = AsyncMock(return_value=True)
    monkeypatch.setattr(repo, "save_analysis", save)
    monkeypatch.setattr(repo, "finalize_detail_page_success", success)
    monkeypatch.setattr(repo, "finalize_detail_page_failure", failure)
    # Replacing fields on a tiny wrapper also works while the new Settings field
    # is introduced in the separately tested configuration change.
    from types import SimpleNamespace
    settings = make_settings(gemini_api_key="fake" if key else None, r2_bucket="b")
    settings = SimpleNamespace(**vars(settings), **{})
    settings.page_copy_timeout_seconds = timeout
    app = fake_worker_app(settings)
    return app, events, assembled, save, success, failure, original


def cuts_result():
    return ([{"blockId": "detail", "imageUrl": "/detail.jpg", "width": 600, "height": 900}], [], 0, [], None, [])


COPY = {"copyResults": [{"blockId": "points", "texts": [{"role": "title", "text": "눈여겨볼 디테일", "draft": False}]}],
        "featureCopy": [{"point": "단추", "desc": "단추로 여밀 수 있어요.", "label": "앞 단추"}],
        "partDescriptionCopy": [{"point": "단추", "desc": "앞에서 여미는 단추예요.", "label": "앞 단추"}],
        "promptVersion": "v2", "model": "gemini-4-argon"}


@pytest.mark.parametrize("copy_first", [True, False])
def test_copy_and_cuts_overlap_and_progress_never_regresses(monkeypatch, copy_first):
    async def run():
        cut_started, release_copy, copy_finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def generate(*args, **kwargs):
            await cut_started.wait()
            if not copy_first:
                await release_copy.wait()
            copy_finished.set()
            return deepcopy(COPY)

        async def cuts(app, job, prepared, product, current_analysis, **kwargs):
            assert kwargs["template_context"]["copy_results"] == []
            assert current_analysis == original
            cut_started.set()
            await worker._emit_progress(app.state.pool, job["id"], {"progress": 45, "phase": "cut"})
            if copy_first:
                await copy_finished.wait()
                await asyncio.sleep(0)
            else:
                release_copy.set()
            await worker._emit_progress(app.state.pool, job["id"], {"progress": 80, "phase": "cut"})
            return cuts_result()

        app, events, assembled, save, success, failure, original = setup(monkeypatch, generate, cuts)
        await asyncio.wait_for(worker.run_detail_page_job(app, worker_job()), 2)
        assert success.await_count == 1 and failure.await_count == 0
        assert original == analysis()
        progress = [p["progress"] for kind, p in events if kind == "progress"]
        assert progress == sorted(progress)
        ready = [p for kind, p in events if p.get("phase") == "copy_ready"]
        assert len(ready) == 1
        assert ready[0]["progress"] >= 45
        assert assembled["copy"] == [{"blockId": "points", "texts": [{"role": "title", "text": "눈여겨볼 디테일"}]}]
        patch = save.call_args.args[2]
        assert set(patch) == {"featureCopy", "partDescriptionCopy", "pageCopy"}
        assert patch["pageCopy"]["copyResults"] == assembled["copy"]
        assert patch["pageCopy"]["model"] == "gemini-4-argon"
        assert patch["pageCopy"]["promptVersion"] == "v2"
        assert patch["pageCopy"]["createdAt"].endswith("+00:00")
        assert assembled["analysis"]["featureCopy"][-1]["label"] == "앞 단추"
    asyncio.run(run())


@pytest.mark.parametrize("mode", ["partial", "empty", "exception", "cancelled_copy", "timeout", "no_key", "persist_failure"])
def test_optional_copy_outcomes_still_finalize(monkeypatch, mode):
    cancelled = []

    async def generate(*args, **kwargs):
        if mode == "exception":
            raise RuntimeError("fake provider failed")
        if mode == "cancelled_copy":
            raise asyncio.CancelledError
        if mode == "timeout":
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(True)
        if mode == "empty":
            return {}
        return {"copyResults": COPY["copyResults"]}

    call = AsyncMock(side_effect=generate)
    app, events, assembled, save, success, failure, original = setup(
        monkeypatch, call, AsyncMock(return_value=cuts_result()), key=mode != "no_key",
        timeout=0.02, persist_failure=mode == "persist_failure")
    asyncio.run(worker.run_detail_page_job(app, worker_job()))
    assert success.await_count == 1 and failure.await_count == 0
    assert call.await_count == (0 if mode == "no_key" else 1)
    assert bool(assembled["copy"]) == (mode in ("partial", "persist_failure"))
    assert assembled["analysis"]["featureCopy"] == original["featureCopy"]
    if mode == "timeout":
        assert cancelled == [True]


def test_copy_ready_event_failure_after_deadline_does_not_fail_job(monkeypatch):
    async def generate(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(.01)

    app, events, assembled, save, success, failure, original = setup(
        monkeypatch, generate, AsyncMock(return_value=cuts_result()), timeout=.02)

    async def emit(pool, job_id, kind, payload):
        if payload.get("phase") == "copy_ready":
            raise RuntimeError("event store unavailable")

    monkeypatch.setattr(worker, "_emit", emit)
    asyncio.run(worker.run_detail_page_job(app, worker_job()))
    assert success.await_count == 1 and failure.await_count == 0


def test_assembly_wait_uses_only_time_left_since_copy_start(monkeypatch):
    from types import SimpleNamespace

    async def run():
        clock = [0.0]
        started, cancelled = asyncio.Event(), asyncio.Event()
        monkeypatch.setattr(worker, "time", SimpleNamespace(monotonic=lambda: clock[0]))

        async def generate(*args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async def cuts(*args, **kwargs):
            await started.wait()
            clock[0] = 89.99
            return cuts_result()

        app, events, assembled, save, success, failure, original = setup(monkeypatch, generate, cuts)
        # Renewing the full 90 seconds at assembly would hit this outer guard.
        await asyncio.wait_for(worker.run_detail_page_job(app, worker_job()), .2)
        assert cancelled.is_set() and success.await_count == 1 and failure.await_count == 0
    asyncio.run(run())


@pytest.mark.parametrize("cancel_job", [True, False])
def test_failed_or_cancelled_job_cancels_copy_task(monkeypatch, cancel_job):
    async def run():
        started, cancelled = asyncio.Event(), asyncio.Event()

        async def generate(*args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async def cuts(*args, **kwargs):
            await started.wait()
            if cancel_job:
                raise asyncio.CancelledError
            raise RuntimeError("cut failure")

        app, events, assembled, save, success, failure, original = setup(monkeypatch, generate, cuts)
        if cancel_job:
            with pytest.raises(asyncio.CancelledError):
                await worker.run_detail_page_job(app, worker_job())
        else:
            await worker.run_detail_page_job(app, worker_job())
        assert cancelled.is_set()
        assert success.await_count == 0 and failure.await_count == 1
        assert not save.called
        assert not any(payload.get("phase") == "copy_ready" for kind, payload in events)
    asyncio.run(run())


@pytest.mark.parametrize("mode", ["success", "partial", "failure", "timeout", "no_key"])
def test_real_copy_and_assembly_with_fake_provider_through_job_finalization(monkeypatch, page_copy_prompt_dir, mode):
    real_generate = page_copy.generate
    real_assemble = worker.page_assembler.assemble
    calls = []

    async def provider(settings, request, timeout, metadata):
        calls.append(request)
        if mode == "failure":
            raise RuntimeError("fake provider unavailable")
        if mode == "timeout":
            await asyncio.Event().wait()
        return {"response": {"sections": [{"key": "S2", "texts": [
            {"role": "title", "text": "단추 포인트"}]}],
            "partDescriptionCopy": [{"point": "단추", "desc": "완벽한 선택" if mode == "partial" else "단추로 앞을 여밀 수 있어요"}]},
            "usage": {"input_tokens": 8, "output_tokens": 9}}

    app, events, assembled, save, success, failure, original = setup(
        monkeypatch, real_generate, AsyncMock(return_value=cuts_result()),
        key=mode != "no_key", timeout=.05 if mode == "timeout" else 2)
    monkeypatch.setattr(page_copy, "_call", provider)
    monkeypatch.setattr(worker.page_assembler, "assemble", real_assemble)
    asyncio.run(worker.run_detail_page_job(app, worker_job()))
    assert success.await_count == 1 and failure.await_count == 0
    blocks = success.call_args.kwargs["editor_blocks"]
    texts = [el.get("text") for block in blocks for el in block["elements"] if el["type"] == "text"]
    assert ("단추 포인트" in texts) == (mode in ("success", "partial"))
    if mode == "success":
        assert "단추로 앞을 여밀 수 있어요" in texts
    if mode == "partial":
        assert "완벽한 선택" not in texts
    assert bool(calls) == (mode != "no_key")
    assert all(call["stage"] == "single" for call in calls)
    if mode in ("success", "partial"):
        assert save.call_args.args[2]["pageCopy"]["copyResults"][0]["blockId"] == "points"
