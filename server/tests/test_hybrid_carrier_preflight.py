from pathlib import Path

import pytest

from app.services.hybrid_composite.carrier_preflight import (
    CARRIER_PREFLIGHT_REASONS,
    PREFLIGHT_POLICY_VERSION,
    PASS,
    REJECT,
    REVIEW,
    RETRY,
    preflight_carrier_quality,
)


ARTIFACT_CARRIER = (
    Path(__file__).resolve().parents[1]
    / "ab_out"
    / "frame_lock"
    / "stripe-projection-protected-v1"
    / "artifacts"
    / "carrier.png"
)


def _landmarks(**overrides):
    row = {
        "shoulder_l": [0.32, 0.24],
        "shoulder_r": [0.68, 0.24],
        "hem_l": [0.34, 0.72],
        "hem_r": [0.66, 0.72],
        "confidence": 0.86,
    }
    row.update(overrides)
    return row


def _inventory(**overrides):
    row = {
        "collar": True,
        "placket": True,
        "cuffs": True,
        "visible_buttons": 6,
        "torso_aspect": 1.45,
        "sleeve_len_ratio": 0.72,
        "garment_categories": ["top"],
    }
    row.update(overrides)
    return row


def _result(**overrides):
    base = {
        "carrier_evidence": {"garment_categories": ["top"]},
        "canonical_evidence": {"expected_categories": ["top"], "hem_y": 0.72},
        "matching_evidence": {"matched": True, "score": 0.82, "frame_iou": 0.74},
        "landmarks": _landmarks(),
        "carrier_inventory": _inventory(),
        "canonical_inventory": _inventory(),
    }
    base.update(overrides)
    return preflight_carrier_quality(**base)


def _codes(result):
    return {reason.code for reason in result.reasons}


def test_known_bad_real_carrier_artifact_is_rejected_before_projection():
    assert ARTIFACT_CARRIER.exists(), "real carrier artifact moved; update preflight regression input"

    # 2026-08-04 4K run에서 사람이 확인한 정규화 관찰과 기하 수치의 replay.
    # 이미지 자체는 decoder/크기 fixture이고, provider를 다시 부르지 않고 동일한
    # 관찰 계약이 projection 전 fail-closed 되는지를 고정한다.
    result = _result(
        carrier_evidence={
            "carrier_image_path": str(ARTIFACT_CARRIER),
            "garment_categories": ["top"],
            "silhouette": "cape",
        },
        carrier_inventory=_inventory(torso_aspect=3.538),
        canonical_inventory=_inventory(torso_aspect=1.353),
        vision_observations={
            "shirtSilhouette": "cape",
            "hemPlausible": False,
            "sleevesPlausible": True,
            "lowerBodyPresent": False,
            "matchingGarmentPresent": None,
            "mannequinFramePreserved": True,
            "garmentCategoryMatches": True,
            "confidence": 0.96,
            "uncertainFields": ["matchingGarmentPresent"],
        },
        require_vision=True,
    )

    assert result.decision == REJECT
    assert result.passed is False
    assert {"carrier_silhouette_cape", "hem_mismatch_gross", "expected_lower_missing"} \
        <= _codes(result)
    assert result.policy_version == PREFLIGHT_POLICY_VERSION
    assert result.metrics["image"]["width"] > 0
    assert result.metrics["image"]["height"] > 0
    assert result.summary()["policyVersion"] == PREFLIGHT_POLICY_VERSION


@pytest.mark.parametrize(
    ("carrier_evidence", "vision_observations", "expected_code"),
    [
        ({"silhouette": "cape"}, {}, "carrier_silhouette_cape"),
        ({}, {"silhouette": {"slab_torso": True}}, "carrier_silhouette_slab_torso"),
    ],
)
def test_existing_carrier_artifact_silhouette_defects_reject_before_projection(
    carrier_evidence,
    vision_observations,
    expected_code,
):
    result = _result(
        carrier_evidence={**carrier_evidence, "garment_categories": ["top"]},
        vision_observations=vision_observations,
    )

    assert result.decision == REJECT
    assert expected_code in _codes(result)


def test_cape_like_geometry_rejects_without_vision_provider_observations():
    result = _result(landmarks=_landmarks(hem_l=[0.20, 0.72], hem_r=[0.80, 0.72]))

    assert result.decision == REJECT
    assert "carrier_silhouette_cape" in _codes(result)
    assert result.reasons[0].code in CARRIER_PREFLIGHT_REASONS


def test_slab_torso_geometry_rejects_when_inventory_confirms_excessive_aspect():
    result = _result(
        landmarks=_landmarks(
            shoulder_l=[0.34, 0.18],
            shoulder_r=[0.66, 0.18],
            hem_l=[0.35, 0.92],
            hem_r=[0.65, 0.92],
        ),
        carrier_inventory=_inventory(torso_aspect=2.65),
    )

    assert result.decision == REJECT
    assert "carrier_silhouette_slab_torso" in _codes(result)


def test_missing_expected_lower_garment_rejects_fail_closed():
    result = _result(
        canonical_evidence={"expected_categories": ["top", "pants"]},
        matching_evidence={"matched": True, "score": 0.8, "garment_categories": ["top"]},
        carrier_inventory=_inventory(garment_categories=["top"]),
    )

    assert result.decision == REJECT
    assert "expected_lower_missing" in _codes(result)


def test_missing_matching_garment_rejects_even_when_geometry_is_measurable():
    result = _result(matching_evidence={"matched": False, "score": 0.12})

    assert result.decision == REJECT
    assert "matching_garment_missing" in _codes(result)


def test_gross_hem_sleeve_and_frame_mismatch_are_typed_reject_reasons():
    result = _result(
        canonical_evidence={"expected_categories": ["top"], "hem_y": 0.46},
        canonical_inventory=_inventory(sleeve_len_ratio=1.05),
        carrier_inventory=_inventory(sleeve_len_ratio=0.52),
        matching_evidence={"matched": True, "score": 0.7, "frame_iou": 0.31},
    )

    assert result.decision == REJECT
    assert {
        "hem_mismatch_gross",
        "sleeve_mismatch_gross",
        "frame_mismatch_gross",
    } <= _codes(result)


def test_unmeasurable_geometry_does_not_auto_pass():
    result = _result(landmarks={"garment_visible": True})

    assert result.decision == RETRY
    assert result.passed is False
    assert "geometry_unmeasurable" in _codes(result)


def test_missing_inventory_does_not_auto_pass():
    result = _result(carrier_inventory={})

    assert result.decision == RETRY
    assert "inventory_unmeasurable" in _codes(result)


def test_explicit_unmeasurable_vision_observation_routes_to_review_not_pass():
    result = _result(vision_observations={"status": "unmeasurable"})

    assert result.decision == REVIEW
    assert result.passed is False
    assert "vision_unmeasurable" in _codes(result)


def test_missing_required_vision_observation_never_auto_passes():
    result = _result(vision_observations={}, require_vision=True)

    assert result.decision == REVIEW
    assert "vision_unmeasurable" in _codes(result)


def test_matching_uncertainty_is_ignored_when_no_matching_garment_was_requested():
    vision = {
        "shirtSilhouette": "shirt",
        "hemPlausible": True,
        "sleevesPlausible": True,
        "lowerBodyPresent": True,
        "matchingGarmentPresent": None,
        "mannequinFramePreserved": True,
        "garmentCategoryMatches": True,
        "confidence": 0.9,
        "uncertainFields": ["matchingGarmentPresent"],
    }
    result = _result(
        vision_observations=vision, require_vision=True, matching_expected=False)

    assert result.decision == PASS


@pytest.mark.parametrize(
    ("field", "expected_code"),
    [
        ("hemPlausible", "hem_mismatch_gross"),
        ("sleevesPlausible", "sleeve_mismatch_gross"),
        ("lowerBodyPresent", "expected_lower_missing"),
        ("matchingGarmentPresent", "matching_garment_missing"),
        ("mannequinFramePreserved", "frame_mismatch_gross"),
        ("garmentCategoryMatches", "garment_category_mismatch"),
    ],
)
def test_normalized_vision_failures_map_to_typed_preflight_reasons(field, expected_code):
    vision = {
        "shirtSilhouette": "shirt",
        "hemPlausible": True,
        "sleevesPlausible": True,
        "lowerBodyPresent": True,
        "matchingGarmentPresent": True,
        "mannequinFramePreserved": True,
        "garmentCategoryMatches": True,
        "confidence": 0.9,
        "uncertainFields": [],
    }
    vision[field] = False

    result = _result(
        vision_observations=vision,
        require_vision=True,
        matching_expected=field == "matchingGarmentPresent",
    )

    assert result.decision == REJECT
    assert expected_code in _codes(result)
