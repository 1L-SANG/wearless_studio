"""Canonical mannequin Frame QC policy and observation contracts."""

import asyncio

import pytest

from app.agents import mannequin_frame_vision as vision
from app.agents.gemini_image import InlineImage
from app.agents.vision_llm import VisionError
from app.services import mannequin_frame_qc as frame_qc
from app.workers import mannequin_job
from conftest import make_settings


def _metrics(**over):
    base = {
        "confidence": 0.9,
        "delta": {"centerX": 0.01, "centerY": 0.01, "subjectHeight": 0.02},
        "backgroundDeltaE": 1.0,
        "outputCropReasons": [],
    }
    base.update(over)
    return base


def _vision(**over):
    base = {
        "canonicalViewFamily": "three_quarter_left",
        "resultViewFamily": "three_quarter_left",
        "orientationMatches": True,
        "cameraYawMatches": True,
        "framingMatches": True,
        "fullBodyVisible": True,
        "backgroundMatches": True,
        "lightingMatches": True,
        "shadowMatches": True,
        "confidence": 0.92,
        "uncertainFields": [],
        "evidence": [],
    }
    base.update(over)
    return base


def test_clean_canonical_frame_passes():
    assert frame_qc.decide(_metrics(), _vision())["decision"] == "pass"


def test_three_quarter_to_front_is_hard_reject():
    out = frame_qc.decide(
        _metrics(),
        _vision(resultViewFamily="front", orientationMatches=False,
                cameraYawMatches=False),
    )
    assert out["decision"] == "reject"
    assert "wrong_view_family" in out["criticalErrors"]


def test_unavailable_or_low_confidence_is_review():
    assert frame_qc.decide(_metrics(confidence=0.0, delta={}), None)[
        "decision"] == "review"
    assert frame_qc.decide(_metrics(), _vision(confidence=0.2))[
        "decision"] == "review"


def test_deterministic_and_vision_conflict_is_review():
    out = frame_qc.decide(
        _metrics(delta={"centerX": 0.40, "centerY": 0.0, "subjectHeight": 0.0}),
        _vision(framingMatches=True),
    )
    assert out["decision"] == "review"
    assert out["checks"]["visionConflict"] is True


def test_schema_is_observation_only_and_strict():
    schema = vision.schema()
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert "decision" not in schema["properties"]
    with pytest.raises(VisionError):
        vision.validate({**_vision(), "decision": "pass"})


def test_observe_sends_canonical_first_and_candidate_second(monkeypatch):
    seen = {}

    async def fake_call(settings, prompt, images, schema, thinking_level=None):
        seen["images"] = images
        return _vision(), "gemini"

    monkeypatch.setattr(vision, "analyze_with_fallback", fake_call)
    canonical = InlineImage("image/png", b"canonical")
    candidate = InlineImage("image/png", b"candidate")
    observation, meta = asyncio.run(vision.observe(
        make_settings(), canonical=canonical, candidate=candidate))

    assert [image.data for image in seen["images"]] == [b"canonical", b"candidate"]
    assert observation["canonicalViewFamily"] == "three_quarter_left"
    assert meta["status"] == "ok"


def test_prompt_forbids_free_verdict_and_garment_judgment():
    prompt = vision.build_prompt()
    assert "Do not judge garment identity" in prompt
    assert "Do not output pass, review, reject" in prompt
    assert "IMAGE 1 = CANONICAL MANNEQUIN PROFILE" in prompt


def _frame_result(decision, *, critical=()):
    return {
        "decision": decision,
        "criticalErrors": list(critical),
        "warnings": [],
        "regenerationInstructions": [
            "Keep the canonical three-quarter-left view; do not output front."
        ] if decision == "reject" else [],
        "checks": {}, "metrics": {}, "vision": {}, "phase": "pre",
        "visionMeta": {"status": "ok"},
    }


def test_worker_retries_wrong_view_once_then_saves_pass(monkeypatch):
    import test_mannequin_axis_qc as harness

    sequence = [
        _frame_result("reject", critical=("wrong_view_family",)),
        _frame_result("pass"),
    ]

    async def fake_frame(**kwargs):
        return sequence.pop(0)

    monkeypatch.setattr(mannequin_job, "_apply_frame_qc", fake_frame)
    result, gemini, r2, emits = harness._run(
        monkeypatch, mode="off", verdicts=[], max_attempts=2,
        mannequin_frame_qc="enforce")

    assert result is not None
    assert len(gemini.calls) == 2
    assert len(r2.puts) == 1
    assert "FRAME LOCK" in gemini.calls[1]["prompt"]
    assert len([payload for event, payload in emits
                if event == "step" and payload.get("status") == "frame_retry"]) == 1


def test_worker_rejects_second_wrong_view_without_saving(monkeypatch):
    import test_mannequin_axis_qc as harness

    async def reject(**kwargs):
        return _frame_result("reject", critical=("wrong_view_family",))

    monkeypatch.setattr(mannequin_job, "_apply_frame_qc", reject)
    result, gemini, r2, emits = harness._run(
        monkeypatch, mode="off", verdicts=[], max_attempts=3,
        mannequin_frame_qc="enforce")

    assert result is None
    assert len(gemini.calls) == 2, "Frame Lock 재시도는 최대 한 번이어야 한다"
    assert r2.puts == []
    assert any(payload.get("status") == "frame_rejected"
               for event, payload in emits if event == "step")


def test_final_frame_regression_rolls_back_before_save(monkeypatch):
    import test_mannequin_axis_qc as harness

    sequence = [_frame_result("pass"),
                _frame_result("reject", critical=("severe_yaw",))]

    async def fake_frame(**kwargs):
        return sequence.pop(0)

    async def drift_after_pre(**kwargs):
        edited = type(kwargs["res"])(
            image=harness._EDITED, mime="image/png")
        return edited, kwargs["p2"], kwargs["calls_spent"]

    monkeypatch.setattr(mannequin_job, "_apply_frame_qc", fake_frame)
    monkeypatch.setattr(mannequin_job, "_apply_edits", drift_after_pre)
    result, _gemini, r2, emits = harness._run(
        monkeypatch, mode="off", verdicts=[], max_attempts=2,
        mannequin_frame_qc="enforce")

    assert result is not None
    assert r2.puts[0][1] == harness._PNG_1PX
    assert result["qc_scores"]["frameLockQc"]["rolledBack"] is True
    assert any(payload.get("status") == "frame_qc_rollback"
               for event, payload in emits if event == "step")
