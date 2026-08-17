import hashlib
import json
from pathlib import Path

import pytest

from app.agents.confirmed_gpt_directing import (
    ConfirmedGptDirectingError,
    bind_confirmed_gpt_directing,
    confirmed_gpt_explicitly_excluded,
    load_confirmed_gpt_directing_catalog,
)


_EXAMPLE_ASSETS = (
    Path(__file__).resolve().parents[1] / "app" / "data" / "example_assets.json"
)
_HISTORICAL_SOURCE_IDS = {
    "ex_styling_women_dress_full_city_03",
    "ex_styling_women_top_medium_snapshot_04",
}
_EXCLUDED_HEAD_CROPPED_FULL_IDS = {
    "ex_styling_men_outer_full_06",
    "ex_styling_women_dress_full_home_01",
    "ex_styling_women_outer_full_alley_01",
    "ex_styling_women_top_full_mia_cafe_snapshot_01",
    "ex_styling_women_top_full_snapshot_04",
}


def _registry(tmp_path, image_bytes=b"released image bytes"):
    image_hash = hashlib.sha256(image_bytes).hexdigest()
    raw = {
        "assets": {
            "ex": {
                "all": "https://images.example.test/ex.png",
                "applicableClothingTypes": ["top"],
                "cutType": "styling",
                "direction": "front",
                "shot": "medium",
                "confirmedGptDirectingV1": {
                    "allSha256": image_hash,
                    "directionDescription": "front-family medium view",
                    "faceExposure": "visible with an off-camera gaze",
                    "requestedFraming": "complete head through the sold top hem",
                    "fixedFootwear": "not visible in the medium composition",
                    "fixedInner": None,
                    "poseSemantics": {
                        "action": "quiet standing portrait",
                        "bodyDirection": "front-family with a slight turn",
                        "weightAndSupport": "weight biased to one side",
                        "keyContacts": "arms relaxed without a prop",
                        "gaze": "slightly off camera",
                        "roughFraming": "vertical medium styling cut",
                    },
                },
            }
        }
    }
    path = tmp_path / "example_assets.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path, image_bytes, raw


def test_live_catalog_covers_every_compatible_visually_verified_example():
    assets = json.loads(_EXAMPLE_ASSETS.read_text(encoding="utf-8"))["assets"]
    structurally_eligible = {
        example_id
        for example_id, entry in assets.items()
        if entry.get("cutType") == "styling"
        and entry.get("direction") == "front"
        and entry.get("shot") in {"full", "medium"}
        and bool(entry.get("all"))
        and bool(entry.get("applicableClothingTypes"))
    }
    catalog = load_confirmed_gpt_directing_catalog()

    assert len(structurally_eligible) == 55
    assert len(catalog) == 50
    assert structurally_eligible - set(catalog) == _EXCLUDED_HEAD_CROPPED_FULL_IDS
    assert set(catalog) - structurally_eligible == set()
    assert _HISTORICAL_SOURCE_IDS <= set(catalog)
    assert catalog["ex_styling_women_dress_full_city_03"].all_sha256 == (
        "7f2c5f93b6a53cd2e21773e491eedf7c665a7b42e8d83608cb51961b6786813a"
    )
    assert catalog["ex_styling_women_top_medium_snapshot_04"].all_sha256 == (
        "df639ea0c4e388bef9e0baeba9acbd7351052723669cacf904cd0c99bca74a69"
    )


@pytest.mark.parametrize("example_id", sorted(_EXCLUDED_HEAD_CROPPED_FULL_IDS))
def test_head_cropped_full_examples_fail_closed(example_id):
    # The exclusion is from direct review of the released pixels, not any
    # inference from the example id. A full cut must satisfy the exact
    # head-to-feet framing contract, so these four source crops cannot bind.
    with pytest.raises(
        ConfirmedGptDirectingError,
        match=f"confirmed_gpt_directing_not_curated:{example_id}",
    ):
        bind_confirmed_gpt_directing(
            example_id,
            b"content is irrelevant before a missing metadata rejection",
            shot="full",
            direction="front",
            clothing_type="outer",
        )

    assert confirmed_gpt_explicitly_excluded(example_id) is True


def test_missing_metadata_is_not_silently_treated_as_an_explicit_exclusion(
    tmp_path,
):
    path, _image_bytes, raw = _registry(tmp_path)
    del raw["assets"]["ex"]["confirmedGptDirectingV1"]
    path.write_text(json.dumps(raw), encoding="utf-8")

    assert confirmed_gpt_explicitly_excluded("ex", path) is False


def test_explicit_exclusion_cannot_coexist_with_directing_metadata(tmp_path):
    path, _image_bytes, raw = _registry(tmp_path)
    raw["assets"]["ex"]["confirmedGptEligibleV1"] = False
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(
        ConfirmedGptDirectingError,
        match="confirmed_gpt_directing_conflicting_eligibility:ex",
    ):
        confirmed_gpt_explicitly_excluded("ex", path)


def test_bind_returns_prompt_contract_types_only_after_hash_and_cut_match(tmp_path):
    path, image_bytes, _raw = _registry(tmp_path)

    metadata = bind_confirmed_gpt_directing(
        "ex",
        image_bytes,
        shot="medium",
        direction="front",
        clothing_type="top",
        registry_path=path,
    )

    assert metadata.cut_lock().requested_framing == (
        "complete head through the sold top hem"
    )
    assert metadata.pose_semantics.action == "quiet standing portrait"
    assert metadata.outfit_lock(matching_attached=True).matching_attached is True


def test_bind_rejects_replaced_example_bytes(tmp_path):
    path, _image_bytes, _raw = _registry(tmp_path)

    with pytest.raises(
        ConfirmedGptDirectingError,
        match="confirmed_gpt_directing_hash_mismatch:ex",
    ):
        bind_confirmed_gpt_directing(
            "ex",
            b"replacement",
            shot="medium",
            direction="front",
            clothing_type="top",
            registry_path=path,
        )


@pytest.mark.parametrize(
    ("shot", "direction", "clothing_type", "error"),
    [
        ("full", "front", "top", "cut_mismatch"),
        ("medium", "side", "top", "cut_mismatch"),
        ("medium", "front", "dress", "clothing_mismatch"),
    ],
)
def test_bind_rejects_incompatible_runtime_cut(
    tmp_path, shot, direction, clothing_type, error
):
    path, image_bytes, _raw = _registry(tmp_path)

    with pytest.raises(ConfirmedGptDirectingError, match=error):
        bind_confirmed_gpt_directing(
            "ex",
            image_bytes,
            shot=shot,
            direction=direction,
            clothing_type=clothing_type,
            registry_path=path,
        )


def test_bind_rejects_uncurated_example_instead_of_inferring_from_filename(tmp_path):
    path, image_bytes, _raw = _registry(tmp_path)

    with pytest.raises(
        ConfirmedGptDirectingError,
        match="confirmed_gpt_directing_not_curated:unknown",
    ):
        bind_confirmed_gpt_directing(
            "unknown",
            image_bytes,
            shot="medium",
            direction="front",
            clothing_type="top",
            registry_path=path,
        )


def test_catalog_rejects_partial_pose_metadata(tmp_path):
    path, _image_bytes, raw = _registry(tmp_path)
    del raw["assets"]["ex"]["confirmedGptDirectingV1"]["poseSemantics"]["gaze"]
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(
        ConfirmedGptDirectingError,
        match="confirmed_gpt_directing_invalid_pose_fields:ex",
    ):
        load_confirmed_gpt_directing_catalog(path)
