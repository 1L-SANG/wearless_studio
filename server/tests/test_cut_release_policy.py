"""Release decisions must follow the selected candidate's validated judgments."""

import pytest

from app.agents.cut_output_qc import GATES, validate
from app.agents.cut_release_policy import evaluate_cut_release
from app.config import load_settings


@pytest.fixture
def evaluate():
    return evaluate_cut_release


def _qc(**statuses):
    qc = validate({"gates": [
        {"gate": gate, "status": statuses.get(gate, "PASS"), "evidence": "Visible comparison."}
        for gate in GATES
    ]})
    qc["identityReview"] = {"status": "PASS", "evidence": "Visible eyelids and nose match target."}
    return qc


def test_primary_face_pass_without_independent_confirmation_holds(evaluate):
    qc = _qc()
    qc.pop("identityReview")
    decision = evaluate(qc, policy="critical")
    assert decision["allowed"] is False
    assert decision["blockingGates"] == ["identityReview"]


@pytest.mark.parametrize("review", [None, {}, {"status": "FAIL", "evidence": "Mixed eyes."},
    {"status": "UNJUDGEABLE", "evidence": "Insufficient visible face."}, {"status": "PASS", "evidence": ""}])
def test_secondary_confirmation_must_be_a_valid_pass(evaluate, review):
    qc = _qc()
    qc["identityReview"] = review
    assert evaluate(qc, policy="critical")["allowed"] is False


def test_off_allows_missing_qc(evaluate):
    assert evaluate(None, policy="off") == {
        "allowed": True, "blockingGates": [], "reason": "policy_off", "finalSource": "stage1",
    }


def test_critical_holds_missing_qc(evaluate):
    decision = evaluate(None, policy="critical")
    assert decision["allowed"] is False
    assert decision["blockingGates"] == ["modelIdentity", "garmentColor", "matchingGarmentIdentity"]


@pytest.mark.parametrize("gate,status", [
    ("modelIdentity", "FAIL"), ("garmentColor", "UNJUDGEABLE"),
    ("matchingGarmentIdentity", "FAIL"),
])
def test_critical_holds_named_failure_even_with_misleading_passed(evaluate, gate, status):
    qc = _qc(**{gate: status})
    qc["passed"] = True
    result = evaluate(qc, policy="critical")
    assert result["allowed"] is False
    assert result["blockingGates"] == [gate]
    assert gate in result["reason"] and status in result["reason"]


def test_validated_hidden_face_and_no_matching_na_allow_release(evaluate):
    qc = validate({"gates": [
        {"gate": gate, "status": "PASS", "evidence": "Visible comparison."}
        for gate in GATES
    ]}, applicable={"modelIdentity": False, "matchingGarmentIdentity": False})
    assert evaluate(qc, policy="critical")["allowed"] is True


def test_unrelated_pose_failure_does_not_block_narrow_guard(evaluate):
    assert evaluate(_qc(framingDirectionFacePose="FAIL"), policy="critical")["allowed"] is True


@pytest.mark.parametrize("bad_row", [None, {}, {"status": "PASS"},
    {"status": "PASS", "evidence": " "}, {"status": "PASS", "evidence": b"image bytes"},
    {"status": ["PASS"], "evidence": "Invalid status."},
    {"status": "pass", "evidence": "Invalid status."}])
def test_malformed_critical_judgment_holds(evaluate, bad_row):
    qc = _qc()
    qc["gates"]["modelIdentity"] = bad_row
    assert evaluate(qc, policy="critical")["blockingGates"] == ["modelIdentity"]


@pytest.mark.parametrize("accepted,stage2,want_allowed,want_source", [
    (True, _qc(), True, "stage2"),
    (False, _qc(), False, "stage1"),
    (True, _qc(garmentColor="FAIL"), False, "stage2"),
    (True, None, False, "stage2"),
    (True, {"passed": True, "gates": {}}, False, "stage2"),
])
def test_only_accepted_stage2_can_override_stage1(evaluate, accepted, stage2, want_allowed, want_source):
    qc = _qc(modelIdentity="FAIL")
    qc["repair"] = {"accepted": accepted, "finalSource": want_source, "stage2Qc": stage2}
    decision = evaluate(qc, policy="critical")
    assert decision["allowed"] is want_allowed
    assert decision["finalSource"] == want_source


def test_corrupt_accepted_stage2_does_not_fall_back_to_stage1_pass(evaluate):
    qc = _qc()
    qc["repair"] = {"accepted": True, "finalSource": "stage2", "stage2Qc": None}
    assert evaluate(qc, policy="critical")["allowed"] is False


def test_errored_repair_uses_stage1(evaluate):
    qc = _qc(modelIdentity="FAIL")
    qc["repair"] = {"accepted": False, "error": "repair_unavailable", "finalSource": "stage1", "stage2Qc": _qc()}
    assert evaluate(qc, policy="critical")["blockingGates"] == ["modelIdentity"]


@pytest.mark.parametrize("qc", [None, [], {"passed": True}, {"gates": []}])
def test_missing_or_malformed_qc_cannot_release(evaluate, qc):
    assert evaluate(qc, policy="critical")["allowed"] is False


def test_environment_opt_in_controls_release_without_changing_qc_mode(monkeypatch):
    monkeypatch.delenv("CUT_OUTPUT_RELEASE_POLICY", raising=False)
    monkeypatch.setenv("CUT_OUTPUT_QC_MODE", "shadow")
    assert getattr(load_settings(), "cut_output_release_policy", None) == "off"
    monkeypatch.setenv("CUT_OUTPUT_RELEASE_POLICY", "critical")
    settings = load_settings()
    assert settings.cut_output_release_policy == "critical"
    assert settings.cut_output_qc_mode == "shadow"


@pytest.mark.parametrize("policy,accepted,route,lighting,target,matching,want", [
    ("critical", True, "EDIT_STAGE1", "PASS", "PASS", "PASS", ("target", "matching")),
    ("off", True, "EDIT_STAGE1", "PASS", "PASS", "PASS", ()),
    ("critical", False, "EDIT_STAGE1", "PASS", "PASS", "PASS", ()),
    ("critical", True, "REGENERATE_FROM_SCRATCH", "PASS", "PASS", "PASS", ()),
    ("critical", True, "EDIT_STAGE1", "FAIL", "PASS", "PASS", ()),
    ("critical", True, "EDIT_STAGE1", "UNJUDGEABLE", "PASS", "PASS", ()),
    ("critical", True, "EDIT_STAGE1", "PASS", "FAIL", "PASS", ("matching",)),
    ("critical", True, "EDIT_STAGE1", "PASS", "PASS", "NA", ("target",)),
    ("critical", True, "EDIT_STAGE1", "PASS", "UNJUDGEABLE", "NA", ()),
])
def test_color_protection_only_applies_to_approved_local_baselines(policy, accepted, route, lighting, target, matching, want):
    from app.agents import cut_release_policy
    helper = getattr(cut_release_policy, "protected_color_axes", None)
    assert helper is not None, "shared color applicability is missing"
    qc = _qc(lightingShadowReflectionDrape=lighting, garmentColor=target, matchingGarmentIdentity=matching)
    qc["repair"] = {"accepted": accepted, "route": route, "stage2Qc": _qc()}
    assert helper(qc, policy=policy) == want
    assert helper(None, policy=policy) == ()


@pytest.mark.parametrize("problem", ["missing", "shifted", "uncertain", "empty", "baseline_hash", "candidate_hash", "valid"])
def test_approved_local_edit_requires_bound_color_witness(problem):
    from app.agents import cut_release_policy
    from test_cut_color_review import color_raw
    import importlib

    assert importlib.util.find_spec("app.agents.cut_color_review"), "color reviewer is missing"
    reviewer = importlib.import_module("app.agents.cut_color_review")
    qc = _qc()
    stage2 = _qc()
    raw = color_raw(target="shifted" if problem == "shifted" else "uncertain" if problem == "uncertain" else "preserved")
    if problem == "empty":
        raw["target"]["evidence"] = ""
    review = reviewer.validate(raw, protected_axes=("target", "matching"))
    review.update(baselineSha256="c" * 64 if problem == "baseline_hash" else "a" * 64,
                  candidateSha256="c" * 64 if problem == "candidate_hash" else "b" * 64)
    if problem != "missing":
        stage2["colorReview"] = review
    qc["repair"] = {"accepted": True, "route": "EDIT_STAGE1", "stage2Qc": stage2}
    decision = cut_release_policy.evaluate_cut_release(qc, policy="critical", baseline_sha256="a" * 64, candidate_sha256="b" * 64)
    assert decision["allowed"] is (problem == "valid")
    assert decision["blockingGates"] == ([] if problem == "valid" else ["colorReview"])
