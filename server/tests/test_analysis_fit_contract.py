"""분석용 핏 계약과 운영 마네킹 경로의 바이트 호환성."""

import hashlib
import itertools
import json
from copy import deepcopy

import pytest

from app.agents import fit_axes


LEGACY_VALUES = {
    "top": {"fit": ["slim", "regular", "semi_over", "over"],
            "length": ["crop", "semi_crop", "basic", "semi_long", "long"],
            "sleeve": ["cap", "cap_short", "short", "elbow"]},
    "pants": {"cut": ["skinny", "slim", "straight", "bootcut", "wide"],
              "length": ["above_ankle", "ankle", "below_ankle"]},
    "skirt": {"length": ["mini", "midi", "long"], "silhouette": ["h_line", "a_line", "mermaid"]},
    "dress": {"length": ["mini", "midi", "long"],
              "silhouette": ["h_line", "a_line", "fit_and_flare", "mermaid"]},
    "outer": {"fit": ["slim", "regular", "semi_over", "over"], "length": ["crop_short", "basic", "long"]},
}


def legacy_values(category, gender):
    values = deepcopy(LEGACY_VALUES[category])
    if gender == "men" and category == "pants":
        values["cut"] = ["slim", "straight", "tapered", "relaxed", "semi_wide", "wide"]
    if gender == "men" and category in ("skirt", "dress"):
        values = {axis: [] for axis in values}
    return values


def legacy_fingerprints():
    """436c618b에서 캡처. 모든 옛 값 조합, 미선택, 코디 축, 조정 블록을 포함한다."""
    catalog = hashlib.sha256()
    blocks = hashlib.sha256()
    normalized = hashlib.sha256()
    for category in LEGACY_VALUES:
        for gender in ("women", "men"):
            values = legacy_values(category, gender)
            entries = {
                axis: [entry for entry in fit_axes.FIT_AXES[category][axis][gender]
                       if entry["value"] in allowed]
                for axis, allowed in values.items()
            }
            catalog.update(json.dumps(entries, ensure_ascii=False).encode())
            matching = [None]
            for match_category, match_axis in (("pants", "cut"), ("skirt", "silhouette"), ("top", "length")):
                match_gender = "women" if match_category == "skirt" else gender
                matching.extend({"clothingId": "match-1", "fitCategory": match_category, "axes": {match_axis: value}}
                                for value in legacy_values(match_category, match_gender)[match_axis])
            for combination in itertools.product(*([None, *allowed] for allowed in values.values())):
                axes = dict(zip(values, combination))
                for match in matching:
                    profile = {"category": category, "gender": gender, "axes": axes, "source": "seller", "version": 2}
                    if match:
                        profile["matchingFit"] = match
                    normalized.update(json.dumps(fit_axes.normalize_fit_profile(profile), ensure_ascii=False).encode())
                    for adjusted in ((), tuple(axes)):
                        blocks.update(fit_axes.build_fit_profile_block(profile, adjusted).encode())
                for cut in [None, *legacy_values("pants", gender)["cut"]]:
                    profile = {"category": category, "gender": gender, "axes": axes, "source": "auto", "version": 1, "matchCut": cut}
                    blocks.update(fit_axes.build_fit_profile_block(profile).encode())
    return catalog.hexdigest(), blocks.hexdigest(), normalized.hexdigest()


def test_legacy_catalog_blocks_and_normalization_are_byte_identical():
    assert legacy_fingerprints() == (
        "98e837c918cd0afda3e8286c24ab0ab12e9fa6d4729e4dfc9716247d4983f1e6",
        "afbfb639f57f6738529be9ddeecf0224a11e88d8f818d0667b2f284669f83f80",
        "593f80bd57e1f52a970e6e392b8f87643528a5768f90c2ff646fa1a372c1c536",
    )


def test_new_analysis_values_normalize_with_axis_sources_only_when_present():
    raw = {"category": "top", "gender": "women", "axes": {"length": "slight_crop", "sleeve": "fingers"},
           "axisSources": {"fit": "ai", "length": "measure", "sleeve": "seller", "cut": "seller", "unknown": "ai"},
           "source": "seller", "version": 2}
    before = deepcopy(raw)
    out = fit_axes.normalize_fit_profile(raw)
    assert out["axes"] == {"length": "slight_crop", "sleeve": "fingers"}
    assert out["axisSources"] == {"fit": "ai", "length": "measure", "sleeve": "seller"}
    assert out["source"] == "seller"
    assert raw == before
    del raw["axisSources"]
    assert "axisSources" not in fit_axes.normalize_fit_profile(raw)


@pytest.mark.parametrize("raw_sources,expected", [
    ({"length": "unsure", "sleeve": "ai", "fit": "measure"}, {"fit": "measure", "length": "unsure", "sleeve": "ai"}),
    ({"length": "auto", "sleeve": ["ai"], "fit": {"source": "seller"}}, {}),
    (None, {}), ([], {}), ("seller", {}),
])
def test_axis_sources_reject_unknown_sources_without_removing_unsure(raw_sources, expected):
    profile = {"category": "top", "gender": "women", "axisSources": raw_sources}
    assert fit_axes.normalize_fit_profile(profile)["axisSources"] == expected


def test_axis_sources_survive_analysis_migration_but_stay_out_of_mannequin_snapshots():
    profile = {"category": "top", "gender": "women", "axes": {"fit": "tight", "length": "crop_basic"},
               "axisSources": {"fit": "seller", "length": "measure"}, "version": 2}
    analysis = fit_axes.normalize_analysis_fit({"fitProfile": profile})
    assert analysis["fitProfile"]["axisSources"] == profile["axisSources"]
    selected = {"fit_profile_snapshot": {"version": 1, "profile": analysis["fitProfile"]}}
    restored = fit_axes.analysis_for_selected_mannequin({}, selected)
    assert restored["fitProfile"] is None
    assert fit_axes.analysis_for_selected_mannequin(analysis, selected, has_mannequin=False) is analysis


def test_catalog_additions_have_analysis_scope_and_fixed_legacy_prompt_text():
    additions = {
        ("top", "length", "women"): ["slight_crop"],
        **{("top", "sleeve", gender): ["above_elbow", "above_wrist", "wrist", "back_of_hand", "fingers"] for gender in ("women", "men")},
        **{("outer", "sleeve", gender): ["cap", "cap_short", "short", "above_elbow", "elbow", "above_wrist", "wrist", "back_of_hand", "fingers"] for gender in ("women", "men")},
        ("dress", "sleeve", "women"): ["cap", "cap_short", "short", "above_elbow", "elbow", "above_wrist", "wrist", "back_of_hand", "fingers"],
        **{(category, "length", "women"): ["above_knee", "knee", "floor"] for category in ("skirt", "dress")},
        **{("pants", "length", gender): ["ankle_cover"] for gender in ("women", "men")},
    }
    for (category, axis, gender), expected in additions.items():
        entries = fit_axes.FIT_AXES[category][axis][gender]
        assert [entry["value"] for entry in entries if entry.get("scope") == "analysis"] == expected
        for value in expected:
            block = fit_axes.build_fit_profile_block({"category": category, "gender": gender, "axes": {axis: value}})
            assert f"- {axis}: " in block
    for gender in ("women", "men"):
        assert [e["value"] for e in fit_axes.FIT_AXES["outer"]["length"][gender]] == ["crop_short", "basic", "long"]
