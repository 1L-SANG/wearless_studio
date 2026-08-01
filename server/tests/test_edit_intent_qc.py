"""Phase 3 P0-C — Edit Intent QC.

계약:
  · decision 은 **서버 정책**이 만든다. Vision 은 관찰만 준다.
  · 측정 불가는 성공이 아니다 — 자동 PASS 하지 않고 review 로 간다.
  · CUSTOM_REVIEW_REQUIRED 는 자동 PASS 경로 자체가 없다.
  · 잠근 항목이 바뀌면 reject — 예산이 소진돼도 PASS 로 승격되지 않는다.
  · 재시도는 1회, 그리고 무엇을 고칠지 아는 경우에만.
"""

import numpy as np
import pytest

from app.services import edit_intent_qc as qc
from app.services import edit_session as es


def _scene(*, hem=0.80, cuff=0.72, width=0.30, shoulder=0.34, cx=0.5, top=0.20,
           bg=245, size=(600, 400)):
    """밝은 배경 위 단순 실루엣 — 편집 전후를 파라미터로 만든다."""
    h, w = size
    img = np.full((h, w, 3), bg, np.uint8)
    x0, x1 = int((cx - width / 2) * w), int((cx + width / 2) * w)
    y0, y1 = int(top * h), int(hem * h)
    img[y0:y1, x0:x1] = (120, 120, 120)                       # 몸통
    sx0, sx1 = int((cx - shoulder / 2) * w), int((cx + shoulder / 2) * w)
    img[y0:y0 + int(0.06 * h), sx0:sx1] = (120, 120, 120)      # 어깨
    img[int(cuff * h):int((cuff + 0.03) * h), sx0:sx1] = (110, 110, 110)  # 소매 끝
    return img


LENGTH_SCOPE = es.allowed_scope("GARMENT_LENGTH_ONLY")


# ── 측정 ─────────────────────────────────────────────────────────────────────

def test_measure_reports_confidence_and_deltas():
    base, edited = _scene(), _scene(hem=0.74)
    m = qc.measure(base, edited)
    assert m["confidence"] > qc.MIN_MEASURE_CONFIDENCE
    assert m["delta"]["hemY"] < 0, "밑단이 올라갔는데 음수가 아니다"
    assert m["silhouetteIou"] is not None


def test_measure_is_honest_when_the_subject_cannot_be_separated():
    """전경 분리가 실패하면 confidence 0 — 성공으로 넘어가지 않는다."""
    flat = np.full((300, 300, 3), 245, np.uint8)     # 전경 없음
    m = qc.measure(flat, flat)
    assert m["confidence"] == 0.0


def test_measure_detects_background_change():
    base, edited = _scene(bg=245), _scene(bg=180)
    m = qc.measure(base, edited)
    assert m["backgroundDeltaE"] > qc.BACKGROUND_DELTA_TOL


# ── 요청한 변화 ──────────────────────────────────────────────────────────────

def test_requested_length_change_passes():
    base, edited = _scene(), _scene(hem=0.74)          # -7.5% ≈ step -1(-8%)
    r = qc.evaluate(baseline_bgr=base, edited_bgr=edited,
                    edit_type="GARMENT_LENGTH_ONLY", allowed_scope=LENGTH_SCOPE,
                    target_ratio=-0.08)
    assert r["decision"] == "pass", r
    assert r["requestedChangeSatisfied"] is True
    assert r["lockedInvariantViolations"] == []


def test_requested_change_that_never_happened_is_rejected():
    base, edited = _scene(), _scene()                  # 아무것도 안 바뀜
    r = qc.evaluate(baseline_bgr=base, edited_bgr=edited,
                    edit_type="GARMENT_LENGTH_ONLY", allowed_scope=LENGTH_SCOPE,
                    target_ratio=-0.08)
    assert r["decision"] == "reject"
    assert r["requestedChangeSatisfied"] is False
    assert r["regenerationInstructions"], "무엇을 고칠지 없으면 재시도도 못 한다"


def test_change_in_the_wrong_direction_is_rejected():
    base, edited = _scene(), _scene(hem=0.86)          # 길어짐 — 짧게 요청했는데
    r = qc.evaluate(baseline_bgr=base, edited_bgr=edited,
                    edit_type="GARMENT_LENGTH_ONLY", allowed_scope=LENGTH_SCOPE,
                    target_ratio=-0.08)
    assert r["decision"] == "reject" and r["requestedChangeSatisfied"] is False


# ── 요청하지 않은 드리프트 ───────────────────────────────────────────────────

def test_sleeve_drift_during_a_length_only_edit_is_caught():
    """총장만 줄여 달랬는데 소매까지 줄어든 컷 — 상품 QC 는 통과할 수 있다."""
    base, edited = _scene(), _scene(hem=0.74, cuff=0.62)
    r = qc.evaluate(baseline_bgr=base, edited_bgr=edited,
                    edit_type="GARMENT_LENGTH_ONLY", allowed_scope=LENGTH_SCOPE,
                    target_ratio=-0.08)
    assert r["decision"] != "pass"
    assert "cuffY" in r["unexpectedChanges"]


def test_background_drift_during_a_length_only_edit_is_a_violation():
    base, edited = _scene(), _scene(hem=0.74, bg=180)
    r = qc.evaluate(baseline_bgr=base, edited_bgr=edited,
                    edit_type="GARMENT_LENGTH_ONLY", allowed_scope=LENGTH_SCOPE,
                    target_ratio=-0.08)
    assert r["decision"] == "reject"
    assert "background" in r["lockedInvariantViolations"]


def test_framing_drift_is_a_violation():
    base, edited = _scene(), _scene(hem=0.74, cx=0.60)
    r = qc.evaluate(baseline_bgr=base, edited_bgr=edited,
                    edit_type="GARMENT_LENGTH_ONLY", allowed_scope=LENGTH_SCOPE,
                    target_ratio=-0.08)
    assert "framing" in r["lockedInvariantViolations"]
    assert r["decision"] == "reject"


def test_mannequin_volume_edit_flags_background_and_framing_drift():
    scope = es.allowed_scope("MANNEQUIN_VOLUME_ONLY")
    base, edited = _scene(), _scene(width=0.34, bg=180, cx=0.58)
    r = qc.evaluate(baseline_bgr=base, edited_bgr=edited,
                    edit_type="MANNEQUIN_VOLUME_ONLY", allowed_scope=scope,
                    target_ratio=0.08)
    assert r["decision"] == "reject"
    assert {"background", "framing"} <= set(r["lockedInvariantViolations"])


def test_background_only_edit_flags_product_drift():
    scope = es.allowed_scope("BACKGROUND_ONLY")
    base, edited = _scene(bg=245), _scene(bg=180, hem=0.60, width=0.42)
    r = qc.evaluate(baseline_bgr=base, edited_bgr=edited,
                    edit_type="BACKGROUND_ONLY", allowed_scope=scope,
                    target_ratio=None)
    assert r["decision"] == "reject"
    assert "garmentOrMannequin" in r["lockedInvariantViolations"]


# ── 측정 불가 · 자동 PASS 금지 ───────────────────────────────────────────────

def test_unmeasurable_edit_never_auto_passes():
    r = qc.decide(edit_type="GARMENT_LENGTH_ONLY", allowed_scope=LENGTH_SCOPE,
                  target_ratio=-0.08,
                  metrics={"confidence": 0.0, "delta": {}})
    assert r["decision"] == "review_required"
    assert r["checks"]["measurable"] is False


def test_low_confidence_does_not_pass_even_with_a_good_looking_delta():
    r = qc.decide(edit_type="GARMENT_LENGTH_ONLY", allowed_scope=LENGTH_SCOPE,
                  target_ratio=-0.08,
                  metrics={"confidence": 0.2, "delta": {"hemY": -0.08}})
    assert r["decision"] == "review_required"


def test_custom_review_required_never_auto_passes():
    scope = es.allowed_scope("CUSTOM_REVIEW_REQUIRED")
    base, edited = _scene(), _scene(hem=0.74)
    r = qc.evaluate(baseline_bgr=base, edited_bgr=edited,
                    edit_type="CUSTOM_REVIEW_REQUIRED", allowed_scope=scope,
                    target_ratio=None)
    assert r["decision"] == "review_required"


def test_unmeasurable_axis_goes_to_review_not_pass():
    """배경·조명처럼 잴 축이 없는 편집은 자동 통과 대상이 아니다."""
    r = qc.decide(edit_type="LIGHTING_ONLY",
                  allowed_scope=es.allowed_scope("LIGHTING_ONLY"),
                  target_ratio=None,
                  metrics={"confidence": 0.9, "delta": {"hemY": 0.0}})
    assert r["decision"] == "review_required"


# ── Vision 은 관찰만 한다 ────────────────────────────────────────────────────

def test_vision_observations_become_violations_by_policy_not_by_verdict():
    base, edited = _scene(), _scene(hem=0.74)
    r = qc.evaluate(baseline_bgr=base, edited_bgr=edited,
                    edit_type="GARMENT_LENGTH_ONLY", allowed_scope=LENGTH_SCOPE,
                    target_ratio=-0.08,
                    vision={"collarChanged": True, "requestedChangeApplied": True,
                            "confidence": 0.9, "uncertainFields": []})
    assert "collarType" in r["lockedInvariantViolations"]
    assert r["decision"] == "reject"


def test_vision_cannot_override_a_measured_failure_into_pass():
    """LLM 이 "괜찮다"고 해도 측정이 아니라고 하면 통과하지 않는다 — 충돌은 사람이 본다."""
    base, edited = _scene(), _scene()
    r = qc.evaluate(baseline_bgr=base, edited_bgr=edited,
                    edit_type="GARMENT_LENGTH_ONLY", allowed_scope=LENGTH_SCOPE,
                    target_ratio=-0.08,
                    vision={"requestedChangeApplied": True, "collarChanged": False,
                            "confidence": 0.9, "uncertainFields": []})
    assert r["decision"] == "review_required"
    assert r["checks"]["visionConflict"] is True


def test_vision_can_catch_what_measurement_missed():
    """측정은 "됐다", 관찰은 "안 보인다" — 어느 쪽도 자동으로 이기지 않는다."""
    r = qc.decide(edit_type="GARMENT_LENGTH_ONLY", allowed_scope=LENGTH_SCOPE,
                  target_ratio=-0.08,
                  metrics={"confidence": 0.9, "delta": {"hemY": -0.08}},
                  vision={"requestedChangeApplied": False, "confidence": 0.9,
                          "uncertainFields": []})
    assert r["requestedChangeSatisfied"] is False
    assert r["decision"] == "review_required" and r["checks"]["visionConflict"] is True


def test_decision_has_no_free_form_llm_field():
    """Vision 응답에 'decision' 이 실려 와도 정책 결과를 덮지 못한다."""
    base, edited = _scene(), _scene(hem=0.74)
    r = qc.evaluate(baseline_bgr=base, edited_bgr=edited,
                    edit_type="GARMENT_LENGTH_ONLY", allowed_scope=LENGTH_SCOPE,
                    target_ratio=-0.08,
                    vision={"decision": "pass", "collarChanged": True,
                            "confidence": 0.9, "uncertainFields": []})
    assert r["decision"] == "reject", "LLM 이 최종 판정을 만들었다"


# ── 재시도 상한 ──────────────────────────────────────────────────────────────

def test_retry_is_allowed_once_and_only_with_instructions():
    rejected = {"decision": "reject", "regenerationInstructions": ["fix it"]}
    assert qc.should_retry(rejected, retry_count=0) is True
    assert qc.should_retry(rejected, retry_count=1) is False


def test_no_retry_without_a_reason():
    assert qc.should_retry({"decision": "reject", "regenerationInstructions": []},
                           retry_count=0) is False


@pytest.mark.parametrize("d", ["pass", "review_required", "failed"])
def test_only_rejects_are_retried(d):
    assert qc.should_retry({"decision": d, "regenerationInstructions": ["x"]},
                           retry_count=0) is False


# ── 출력 계약 ────────────────────────────────────────────────────────────────

def test_result_shape_is_stable():
    base, edited = _scene(), _scene(hem=0.74)
    r = qc.evaluate(baseline_bgr=base, edited_bgr=edited,
                    edit_type="GARMENT_LENGTH_ONLY", allowed_scope=LENGTH_SCOPE,
                    target_ratio=-0.08)
    for key in ("decision", "requestedChangeSatisfied", "requestedChangeMeasurements",
                "unexpectedChanges", "lockedInvariantViolations",
                "regenerationInstructions", "checks", "metrics"):
        assert key in r
    assert r["decision"] in ("pass", "review_required", "reject")
