import pytest

from app.services.hybrid_composite.texture_projection import plan_periodic_projection


def test_projection_preserves_source_repeat_count_across_carrier_span():
    plan = plan_periodic_projection(
        pattern_type="stripe",
        source_period_px=20,
        source_span_px=200,
        target_span_px=300,
        target_axis="vertical",
        source_model_confidence=0.91,
    )
    assert plan.ok is True
    assert plan.target_period_px == 30
    assert plan.metrics["sourceRepeats"] == 10
    assert plan.summary()["version"] == "texture_projection_2d_v1"


def test_projection_rejects_unsupported_pattern_without_guessing():
    plan = plan_periodic_projection(
        pattern_type="floral",
        source_period_px=20,
        source_span_px=200,
        target_span_px=300,
        target_axis="vertical",
        source_model_confidence=0.9,
    )
    assert plan.ok is False
    assert plan.reason == "unsupported_pattern"
    assert plan.target_period_px is None


@pytest.mark.parametrize("pattern_type", ["check", "plaid", "gingham", "tartan"])
def test_projection_mvp_rejects_two_axis_patterns_as_unsupported(pattern_type):
    plan = plan_periodic_projection(
        pattern_type=pattern_type,
        source_period_px=20,
        source_span_px=200,
        target_span_px=300,
        target_axis="vertical",
        source_model_confidence=0.9,
    )
    assert plan.ok is False
    assert plan.reason == "unsupported_pattern"
    assert plan.metrics["patternType"] == pattern_type


def test_projection_rejects_low_source_repeat_count():
    plan = plan_periodic_projection(
        pattern_type="stripe",
        source_period_px=80,
        source_span_px=200,
        target_span_px=240,
        target_axis="horizontal",
        source_model_confidence=0.9,
    )
    assert plan.ok is False
    assert plan.reason == "reference_insufficient"
    assert plan.metrics["sourceRepeats"] < 4


def test_projection_rejects_micro_period_on_target():
    plan = plan_periodic_projection(
        pattern_type="stripe",
        source_period_px=4,
        source_span_px=400,
        target_span_px=400,
        target_axis="vertical",
        source_model_confidence=0.9,
    )
    assert plan.ok is False
    assert plan.reason == "target_period_too_small"


def test_projection_low_confidence_is_visible_not_silent_pass():
    plan = plan_periodic_projection(
        pattern_type="stripe",
        source_period_px=20,
        source_span_px=220,
        target_span_px=260,
        target_axis="vertical",
        source_model_confidence=0.4,
    )
    assert plan.ok is False
    assert plan.reason == "projection_low_confidence"
    assert plan.summary()["confidence"] == 0.4
