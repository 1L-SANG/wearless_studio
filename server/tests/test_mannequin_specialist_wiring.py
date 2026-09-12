"""저장 경계에서 확인한다. 외부 이미지와 QC 응답만 대체한다."""
import hashlib
from types import SimpleNamespace

import pytest

from app.workers import mannequin_job as worker
from conftest import make_settings
from test_mannequin_final_untuck_qc import run_worker
from test_mannequin_quality_repair import rated


def report(image, verdict="pass"):
    roles = {}
    for role in ("structure", "details", "appearance"):
        fail = verdict == "fail" and role == "details"
        roles[role] = {"status": "ok", "response": {
            "verdict": "fail" if fail else "pass", "summary": "Visible garment assessment",
            "issues": [{"materiality": "material", "repairInstruction": "Restore the missing front line."}] if fail else [],
            "visibilityLimitations": ["Back is outside this front view."],
            "materialUncertainties": [], "confirmedMatches": []}}
    return {"version": "test", "image_hash": hashlib.sha256(image).hexdigest(),
            "verdict": verdict, "complete": verdict != "review", "roles": roles}


def setup(monkeypatch, assessments):
    seen = []
    async def judge(settings, refs, candidate, **kwargs):
        seen.append(candidate.data)
        value = assessments[candidate.data]
        if isinstance(value, Exception): raise value
        return value
    async def source(*args): return {"family": "top", "attributes": [], "uncertainties": []}
    monkeypatch.setattr(worker.mannequin_specialist_qc, "judge", judge)
    monkeypatch.setattr(worker.mannequin_photo_structure, "analyze", source)
    monkeypatch.setattr(worker.mannequin_photo_structure, "prepare", lambda refs: list(refs))
    return seen


def test_specialist_feature_is_off_unless_explicitly_enabled():
    assert make_settings().mannequin_specialist_qc == "off"


@pytest.mark.parametrize("raw,expected", [("nan", 120), ("bad", 120), ("0", 15), ("500", 180), ("90", 90)])
def test_specialist_wait_budget_is_separate_and_bounded(monkeypatch, raw, expected):
    from app import config
    monkeypatch.setenv("MANNEQUIN_SPECIALIST_TIMEOUT_SECONDS", raw)
    assert config._specialist_timeout() == expected


@pytest.mark.parametrize("mode", ["off", "shadow", "enforce"])
def test_normal_front_with_hidden_back_never_spends_repair(monkeypatch, mode):
    seen = setup(monkeypatch, {b"before": report(b"before")})
    _, captures = run_worker(monkeypatch, has_match=False, p2={b"before": rated()},
        settings_overrides={"mannequin_specialist_qc": mode})
    assert captures.puts == [b"before"]
    assert len(captures.requests) == 1
    assert seen == ([] if mode == "off" else [b"before"])


def test_material_failure_gets_one_candidate_based_sunburst_edit_and_fresh_checks(monkeypatch):
    seen = setup(monkeypatch, {b"before": report(b"before", "fail"), b"repaired": report(b"repaired")})
    result, captures = run_worker(monkeypatch, has_match=False,
        generated=(b"before", b"repaired"), p2={b"before": rated(), b"repaired": rated()},
        settings_overrides={"mannequin_specialist_qc": "enforce"})
    assert captures.puts == [b"repaired"] and seen == [b"before", b"repaired"]
    assert len(captures.requests) == 2
    assert captures.requests[-1]["model"] == "gpt-image-2.5-sunburst"
    assert captures.requests[-1]["images"][0].data == b"before"
    assert "Restore the missing front line." in captures.prompts[-1]
    assert "Back is outside" not in captures.prompts[-1]
    assert result["qc_scores"]["quality_repair_kind"] == "targeted_edit"


@pytest.mark.parametrize("after", ["fail", "review", "error"])
def test_failed_or_unavailable_repair_does_not_reach_storage(monkeypatch, after):
    last = RuntimeError("QC unavailable") if after == "error" else report(b"repaired", after)
    setup(monkeypatch, {b"before": report(b"before", "fail"), b"repaired": last})
    captures = SimpleNamespace(judged=[], series=[], puts=[], image_calls=[], events=[], prompts=[])
    with pytest.raises(worker.MannequinQualityError):
        run_worker(monkeypatch, captures=captures, has_match=False,
            generated=(b"before", b"repaired"), p2={b"before": rated(), b"repaired": rated()},
            settings_overrides={"mannequin_specialist_qc": "enforce"})
    assert not captures.puts and len(captures.requests) == 2


def test_shadow_defect_is_observed_without_repair(monkeypatch):
    setup(monkeypatch, {b"before": report(b"before", "fail")})
    _, captures = run_worker(monkeypatch, has_match=False, p2={b"before": rated()},
        settings_overrides={"mannequin_specialist_qc": "shadow"})
    assert len(captures.requests) == 1 and captures.puts == [b"before"]


def test_specialist_only_mode_can_store_its_own_checked_result(monkeypatch):
    setup(monkeypatch, {b"before": report(b"before", "fail"), b"repaired": report(b"repaired")})
    result, captures = run_worker(monkeypatch, mode="off", has_match=False,
        generated=(b"before", b"repaired"), p2={},
        settings_overrides={"mannequin_specialist_qc": "enforce"})
    assert captures.puts == [b"repaired"]
    assert result["qc_scores"]["specialist_qc"]["image_hash"] == hashlib.sha256(b"repaired").hexdigest()


def test_wrong_image_assessment_cannot_approve_storage(monkeypatch):
    setup(monkeypatch, {b"before": report(b"different")})
    captures = SimpleNamespace(judged=[], series=[], puts=[], image_calls=[], events=[], prompts=[])
    with pytest.raises(worker.MannequinQualityError, match="specialist_review_unavailable"):
        run_worker(monkeypatch, captures=captures, has_match=False, p2={b"before": rated()},
            settings_overrides={"mannequin_specialist_qc": "enforce"})
    assert captures.puts == [] and len(captures.requests) == 1


def test_photo_family_conflict_stops_before_paid_generation(monkeypatch):
    setup(monkeypatch, {})
    async def source(*args): return {"family": "bottom", "attributes": [], "uncertainties": []}
    monkeypatch.setattr(worker.mannequin_photo_structure, "analyze", source)
    captures = SimpleNamespace(judged=[], series=[], puts=[], image_calls=[], events=[], prompts=[])
    with pytest.raises(worker.MannequinQualityError, match="source_structure_unavailable"):
        run_worker(monkeypatch, captures=captures, has_match=False, p2={},
            settings_overrides={"mannequin_specialist_qc": "enforce"})
    assert not captures.requests and not captures.puts


def test_combined_existing_and_specialist_defects_share_one_repair_with_both_diagnostics(monkeypatch):
    setup(monkeypatch, {b"first": report(b"first", "fail"), b"second": report(b"second", "fail"), b"repaired": report(b"repaired")})
    result, captures = run_worker(monkeypatch, has_match=False,
        generated=(b"first", b"second", b"repaired"),
        p2={b"first": rated(logo_graphic="critical"), b"second": rated(logo_graphic="critical"), b"repaired": rated()},
        settings_overrides={"mannequin_specialist_qc": "enforce"})
    assert captures.puts == [b"repaired"] and len(captures.requests) == 3
    assert "logo_graphic" in captures.prompts[-1]
    assert "Restore the missing front line." in captures.prompts[-1]
    assert result["qc_scores"]["quality_repair_kind"] == "source_regeneration"


def test_mirrored_source_contract_reaches_specialists_and_targeted_repair(monkeypatch):
    setup(monkeypatch, {})
    observed = []
    async def judge(settings, refs, candidate, **kwargs):
        observed.append(kwargs.get("source_mirrored"))
        return report(candidate.data, "fail" if candidate.data == b"before" else "pass")
    monkeypatch.setattr(worker.mannequin_specialist_qc, "judge", judge)
    _, captures = run_worker(monkeypatch, has_match=False,
        generated=(b"before", b"repaired"), p2={b"before": rated(), b"repaired": rated()},
        analysis_override={"sourceMirrored": True},
        settings_overrides={"mannequin_specialist_qc": "enforce"})
    assert observed == [True, True]
    assert "MIRRORED SOURCE PHOTOS" in captures.prompts[-1]
