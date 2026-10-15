"""ADR-0017 2026-10-11, 두 워커가 공유하는 대체 규칙."""
import copy

import pytest

from app.agents import cut_generator
from app.cut_failures import CUT_FAILURE_REASONS, failure_info
from app.services import matching


FORMER_SELLER_REASONS = (
    "no_garment_photo", "matching_asset_unavailable", "no_angle_photo",
    "identity_scope_mismatch", "real_model_studio_only", "pose_direction_incompatible",
    "invalid_cut_spec", "detail_target_unavailable", "invalid_detail_region",
    "detail_reference_required", "detail_color_reference_required", "detail_source_unavailable",
    "detail_target_color_changed", "detail_target_source_changed",
)


@pytest.mark.parametrize("reason", FORMER_SELLER_REASONS)
def test_former_seller_failures_are_retryable(reason):
    assert "seller_action" not in CUT_FAILURE_REASONS.values()
    assert failure_info(reason) == {"reason": reason, "retryable": True}


def item(identifier, clothing_type="bottom", category="팬츠", **extra):
    return {"id": identifier, "clothingType": clothing_type, "category": category, **extra}


def test_matching_candidates_preserve_rank_with_category_before_type_and_exclusions():
    recommendations = [
        item("missing"), item("other-category", category="스커트"), item("already-used"),
        item("custom", isCustom=True), item("incompatible", isCompatible=False),
        item("other-type", clothing_type="top"), item("same-category-1"),
        item("same-category-2"), item("other-category-2", category="쇼츠"),
    ]
    original = copy.deepcopy(recommendations)
    assert matching.matching_fallback_candidates(
        recommendations, "missing", ["missing", "already-used"],
        {"clothing_type": "top", "category": "셔츠"},
    ) == ["same-category-1", "same-category-2", "other-category", "other-category-2"]
    assert recommendations == original


def test_matching_candidates_use_repository_metadata_only_when_saved_item_absent():
    assert matching.matching_fallback_candidates(
        [item("type-only", category="스커트"), item("category-first")],
        "missing", [], {"clothing_type": "bottom", "category": "팬츠"},
    ) == ["category-first", "type-only"]


@pytest.mark.parametrize("recommendations,metadata", [
    ([item("candidate")], None),
    ([{"id": "missing", "category": "팬츠"}, item("candidate")], None),
    ([item("missing"), item("candidate", clothing_type="top")], None),
])
def test_matching_candidates_are_empty_without_known_type_or_eligible_candidates(recommendations, metadata):
    assert matching.matching_fallback_candidates(recommendations, "missing", [], metadata) == []
