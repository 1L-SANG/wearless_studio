import asyncio
import importlib
import json
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture()
def goldenset(monkeypatch):
    from scripts import _env

    monkeypatch.setattr(_env, "load_env", lambda: None)
    sys.modules.pop("scripts.goldenset_clothing_types", None)
    module = importlib.import_module("scripts.goldenset_clothing_types")
    yield module
    sys.modules.pop("scripts.goldenset_clothing_types", None)


def test_result_asset_id_requires_successful_job(goldenset):
    job = {
        "status": "done",
        "result": {"data": [{"src": "/v1/assets/asset-123/file"}]},
    }

    assert goldenset._result_asset_id(job) == "asset-123"

    with pytest.raises(RuntimeError, match="job error"):
        goldenset._result_asset_id({"status": "error", "error_message": "quota"})


def test_result_asset_id_rejects_ambiguous_or_malformed_result(goldenset):
    with pytest.raises(RuntimeError, match="컷 수 비정상"):
        goldenset._result_asset_id({"status": "done", "result": {"data": []}})

    with pytest.raises(RuntimeError, match="asset 경로 비정상"):
        goldenset._result_asset_id(
            {"status": "done", "result": {"data": [{"src": "https://example.com/cut.png"}]}}
        )


def test_load_rejudge_records_keeps_selected_generated_outputs(goldenset, tmp_path):
    path = tmp_path / "results.jsonl"
    records = [
        {"arm": "top-w", "rep": 0, "genKey": "top/0.png"},
        {"arm": "top-w", "rep": 1, "genKey": "top/1.png", "error": "judge timeout"},
        {"arm": "pants-w", "rep": 0, "genKey": "pants/0.png"},
        {"arm": "dress-w", "rep": 0},
    ]
    path.write_text("\n".join(json.dumps(record) for record in records))

    loaded = goldenset._load_rejudge_records(path, {"top-w"})

    assert loaded == records[:2]


def test_freeze_retrieval_overrides_existing_on_value(goldenset, monkeypatch):
    monkeypatch.setenv("RETRIEVAL_REFIMAGES", "on")

    goldenset._freeze_retrieval()

    assert goldenset.os.environ["RETRIEVAL_REFIMAGES"] == "off"


def test_report_uses_actual_retrieval_snapshot(goldenset, tmp_path):
    goldenset._write_report(
        tmp_path,
        "run-1",
        "baseline",
        "abc123",
        {"retrieval_refimages": "on"},
        [],
    )

    assert "RETRIEVAL=on" in (tmp_path / "REPORT.md").read_text()


def test_rejudge_reuses_recorded_output_without_starting_worker(
    goldenset, monkeypatch, tmp_path
):
    source_dir = tmp_path / "source-run"
    source_dir.mkdir()
    blobs = {
        "gen.png": b"generated",
        "src.png": b"source",
        "base.png": b"base",
    }
    source = {
        "runId": "source-run",
        "phase": "baseline",
        "arm": "top-w",
        "family": "top",
        "rep": 0,
        "projectId": "project-1",
        "gender": "women",
        "genKey": "gen.png",
        "srcKey": "src.png",
        "baseKey": "base.png",
        "outputHash": goldenset._sha(blobs["gen.png"]),
        "error": "ReadTimeout: old judge timed out",
    }
    (source_dir / "results.jsonl").write_text(json.dumps(source) + "\n")

    class FakeR2:
        def get_bytes(self, key):
            return blobs[key]

    async def fake_judge(*_args, **_kwargs):
        return {
            "typeSeen": "t-shirt",
            "lowerBody": {"state": "present"},
            "proportions": {"state": "ok"},
            "garmentFidelity": {"state": "preserved"},
            "mannequinBasePreserved": {"state": "preserved"},
            "notes": "",
        }

    class ForbiddenWorker:
        def __init__(self):
            raise AssertionError("rejudge must not create a generation worker")

    settings = SimpleNamespace(model_text_gemini="judge-model")
    monkeypatch.setattr(goldenset, "OUT", tmp_path)
    monkeypatch.setattr(goldenset, "load_settings", lambda: settings)
    monkeypatch.setattr(goldenset, "R2Client", lambda _settings: FakeR2())
    monkeypatch.setattr(goldenset, "InlineWorker", ForbiddenWorker)
    monkeypatch.setattr(goldenset.SQ, "judge", fake_judge)
    monkeypatch.setattr(goldenset, "_git_sha", lambda: "abc123")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "goldenset_clothing_types",
            "--phase",
            "rejudge",
            "--source-run",
            "source-run",
            "--run-id",
            "rejudge-run",
        ],
    )

    assert asyncio.run(goldenset.main()) == 0

    record = json.loads((tmp_path / "rejudge-run/results.jsonl").read_text())
    assert record["sourceRun"] == "source-run"
    assert record["outputHash"] == source["outputHash"]
    assert record["aggregate"]["overallPass"] is True
