import importlib
import sys

import pytest


@pytest.fixture()
def eval_axis(monkeypatch):
    from scripts import _env

    monkeypatch.setattr(_env, "load_env", lambda: None)
    sys.modules.pop("scripts.eval_axis_fidelity", None)
    module = importlib.import_module("scripts.eval_axis_fidelity")
    yield module
    sys.modules.pop("scripts.eval_axis_fidelity", None)


def test_result_asset_id_requires_successful_job(eval_axis):
    job = {
        "status": "done",
        "result": {"data": [{"src": "/v1/assets/asset-123/file"}]},
    }

    assert eval_axis._result_asset_id(job) == "asset-123"

    with pytest.raises(RuntimeError, match="job error"):
        eval_axis._result_asset_id({"status": "error", "error_message": "quota"})


def test_result_asset_id_rejects_unbound_result(eval_axis):
    with pytest.raises(RuntimeError, match="컷 수 비정상"):
        eval_axis._result_asset_id({"status": "done", "result": {"data": []}})

    with pytest.raises(RuntimeError, match="asset 경로 비정상"):
        eval_axis._result_asset_id(
            {"status": "done", "result": {"data": [{"src": "https://example.com/cut.png"}]}}
        )


def test_directional_rate_counts_similar_as_no_effect(eval_axis):
    results = [
        {"observed": "right", "directionalPass": True},
        {"observed": "similar", "directionalPass": None},
        {"observed": "similar", "directionalPass": None},
        {"observed": "unclear", "directionalPass": None},
    ]

    assert eval_axis._directional_rate(results) == pytest.approx(1 / 3)


def test_control_false_positive_rate_uses_same_visible_denominator(eval_axis):
    results = [
        {"observed": "left"},
        {"observed": "similar"},
        {"observed": "similar"},
        {"observed": "unclear"},
    ]

    assert eval_axis._false_positive_rate(results) == pytest.approx(1 / 3)
