"""A QA run with the artifact directory unset is not a dataset run.

This is the check P3.5 asked for. Two production runs took the guided path on 2026-08-01
and neither can be replayed, because the torso ROI only ever reaches disk through
`qa_flush`, and `qa_flush` returns immediately when HYBRID_COMPOSITE_ARTIFACT_DIR is
unset. Spending provider calls in that state buys evidence that cannot be used.
"""
import ast
import pathlib

import pytest

from app.services import controlled_qa_preflight as preflight

SERVER = pathlib.Path(__file__).resolve().parents[1]


def test_ready_when_the_artifact_directory_is_set_and_writable(tmp_path):
    report = preflight.check(artifact_dir=str(tmp_path))
    assert report.ready, report.as_dict()
    assert report.blocking_reasons == []
    d = report.as_dict()
    assert d["imageCallCap"] == 3
    assert d["providerBudgetPersistent"] is True
    for capture in ("sourceTextureContextCapture", "candidateAttemptExecutionScopeCapture",
                    "guidedCandidateCapture", "shadowMultiRoiCapture"):
        assert d[capture] is True, capture


def test_unset_artifact_dir_is_not_ready_and_says_why(monkeypatch):
    monkeypatch.delenv(preflight.ARTIFACT_DIR_ENV, raising=False)
    report = preflight.check()
    assert not report.ready
    assert report.as_dict()["qaArtifactDirSet"] is False
    assert any("unset" in r for r in report.blocking_reasons)


def test_unwritable_artifact_dir_is_not_ready(tmp_path):
    blocked = tmp_path / "not-a-dir"
    blocked.write_text("i am a file")
    report = preflight.check(artifact_dir=str(blocked / "child"))
    assert not report.ready
    assert report.as_dict()["qaArtifactDirWritable"] is False


def test_require_refuses_rather_than_returning_a_partial_environment(monkeypatch):
    monkeypatch.delenv(preflight.ARTIFACT_DIR_ENV, raising=False)
    with pytest.raises(RuntimeError) as e:
        preflight.require()
    assert preflight.ARTIFACT_DIR_ENV in str(e.value)


def test_the_check_does_not_leave_its_probe_behind(tmp_path):
    preflight.check(artifact_dir=str(tmp_path))
    assert list(tmp_path.iterdir()) == []


def test_preflight_reads_the_env_var_the_worker_actually_gates_on():
    """If the worker ever gates on a different name, this check becomes theatre."""
    src = (SERVER / "app/workers/mannequin_job.py").read_text()
    tree = ast.parse(src)
    gated_on = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute) and node.func.attr == "getenv"
        and node.args and isinstance(node.args[0], ast.Constant)
    }
    assert preflight.ARTIFACT_DIR_ENV in gated_on


def test_preflight_runs_no_provider_and_no_vision_call():
    """It inspects config and module surfaces — nothing that costs money."""
    import inspect as _inspect

    src = _inspect.getsource(preflight)
    for forbidden in ("generate_content_image", "vision", "httpx", "gemini"):
        assert forbidden not in src.lower().replace("provider budget", ""), forbidden
