import asyncio

import pytest

from scripts import eval_axis_reflection as EVAL


class _ConnectionContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Pool:
    def connection(self):
        return _ConnectionContext()


def test_completed_job_cut_key_uses_cut_from_that_job(monkeypatch):
    calls = {}

    async def get_job(conn, user_id, job_id):
        calls["job"] = (user_id, job_id)
        return {
            "id": job_id,
            "project_id": "project-1",
            "status": "done",
            "result": {"data": [{"id": "A-7"}]},
        }

    async def get_cut(conn, user_id, project_id, client_id):
        calls["cut"] = (user_id, project_id, client_id)
        return {"r2_key": "users/u/projects/project-1/ai/job-1/asset.png"}

    monkeypatch.setattr(EVAL.repo, "get_job", get_job)
    monkeypatch.setattr(EVAL.repo, "get_mannequin_cut_asset", get_cut)

    key = asyncio.run(EVAL._completed_job_cut_key(_Pool(), "user-1", "job-1"))

    assert key.endswith("/job-1/asset.png")
    assert calls == {
        "job": ("user-1", "job-1"),
        "cut": ("user-1", "project-1", "A-7"),
    }


def test_completed_job_cut_key_rejects_failed_job_before_cut_lookup(monkeypatch):
    async def get_job(conn, user_id, job_id):
        return {
            "id": job_id,
            "project_id": "project-1",
            "status": "error",
            "error_message": "generation failed",
        }

    async def unexpected_cut_lookup(*args):
        pytest.fail("failed job must not query a previous project cut")

    monkeypatch.setattr(EVAL.repo, "get_job", get_job)
    monkeypatch.setattr(EVAL.repo, "get_mannequin_cut_asset", unexpected_cut_lookup)

    with pytest.raises(RuntimeError, match="generation failed"):
        asyncio.run(EVAL._completed_job_cut_key(_Pool(), "user-1", "job-1"))


def test_summarize_block_marks_absolute_error_incomplete_and_suspect():
    block = {
        "absolute": [
            {"cut": "A0", "pass": True},
            {"cut": "B0", "error": "judge failed"},
        ],
        "directional": [
            {
                "kind": "treatment",
                "directionalPass": True,
                "abstain": False,
            },
            {
                "kind": "control",
                "observed": "similar",
                "abstain": True,
            },
        ],
    }

    EVAL._summarize_block(block, planned_absolute=2, planned_directional=2)

    assert block["raw"]["absoluteFail"] == 0
    assert block["raw"]["absoluteTotal"] == 1
    assert block["incomplete"] is True
    assert block["suspect"] is True
    assert block["metricDisagreement"] is False


def test_partial_results_require_explicit_opt_in_for_zero_exit():
    records = [{"incomplete": True}]

    assert EVAL._result_exit_code(records, allow_partial=False) == 1
    assert EVAL._result_exit_code(records, allow_partial=True) == 0
