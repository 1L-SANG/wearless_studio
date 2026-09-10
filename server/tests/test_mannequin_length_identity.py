"""Request-boundary contracts, not assertions of image quality."""
import re

import pytest

from app.agents.mannequin_adjust import build_adjust_directives
from app.agents.mannequin_fit_qc import declared_axis_spec
from test_mannequin_photo_reference import capture_candidate


@pytest.mark.parametrize("gender", ["women", "men"])
@pytest.mark.parametrize("path", ["fresh", "edit"])
def test_long_pants_request_sets_hem_position_not_a_required_fold_count(monkeypatch, gender, path):
    from app.agents.gemini_image import InlineImage

    profile = {"category": "pants", "gender": gender, "version": 2, "source": "seller",
               "axes": {"length": "below_ankle"}}
    call = capture_candidate(
        monkeypatch, clothing_type="bottom", profile=profile, generation_path=path,
        parent=InlineImage("image/png", b"current") if path == "edit" else None,
    )
    directive = next(line for line in call["prompt"].splitlines()
                     if line.startswith("- length:") or line.startswith("- MAIN PRODUCT"))
    assert "ankle" in directive and "instep" in directive
    assert "reaches the top of the foot" in directive
    assert not re.search(r"\b(?:one|single)\b.*?\b(?:break|fold)\b", directive)
    assert "fabric" in directive


def test_long_pants_fit_judge_uses_hem_position_not_required_folds():
    profile = {"category": "pants", "gender": "women", "axes": {"length": "below_ankle"}}
    specs = declared_axis_spec(profile)
    target = next(row["observableTarget"] for row in specs if row["axis"] == "length")
    assert "ankle" in target
    assert "reaching the tops of the feet" in target
    assert not re.search(r"\b(?:one|single)\b.*?\b(?:break|fold)\b", target)


def test_matching_top_change_does_not_inject_main_pants_length():
    profile = {"category": "pants", "gender": "women", "version": 2,
               "axes": {"length": "below_ankle"},
               "matchingFit": {"clothingId": "white-top", "fitCategory": "top", "axes": {"length": "crop"}}}
    directives = build_adjust_directives(profile, ())
    assert "MATCHING TOP" in directives
    assert "MAIN PRODUCT" not in directives
    assert "ankle" not in directives


@pytest.mark.parametrize("slots", [
    ("Front",),
    ("Front", "Back", "Detail", "BackDetail"),
    ("BackDetail", "Front", "Detail"),
])
def test_adjust_request_keeps_each_reference_role_bound_to_its_image(monkeypatch, slots):
    from app.agents.gemini_image import InlineImage

    call = capture_candidate(
        monkeypatch, clothing_type="bottom", generation_path="edit",
        parent=InlineImage("image/png", b"current"), slots=slots, with_product_roles=True,
        match_image=InlineImage("image/png", b"white-top"),
        profile={"category": "pants", "gender": "women", "axes": {"length": "below_ankle"}},
    )
    lines = call["prompt"].splitlines()
    for index, slot in enumerate(slots, start=2):
        label = next(line for line in lines if line.startswith(f"{index}."))
        assert slot in label
        assert call["images"][index - 1].data == slot.encode()
    match = next(line for line in lines if line.startswith(f"{len(slots) + 2}."))
    assert "MATCHING TOP" in match
    assert call["images"][-1].data == b"white-top"


def test_legacy_unlabelled_edit_references_are_not_assumed_to_be_front_and_back(monkeypatch):
    from app.agents.gemini_image import InlineImage

    call = capture_candidate(monkeypatch, generation_path="edit", parent=InlineImage("image/png", b"current"))
    labels = [line for line in call["prompt"].splitlines() if line.startswith(("2.", "3."))]
    assert all("Front" not in label and "Back" not in label for label in labels)


def test_edit_request_does_not_treat_current_cut_as_product_design_evidence(monkeypatch):
    from app.agents.gemini_image import InlineImage

    call = capture_candidate(
        monkeypatch, generation_path="edit", parent=InlineImage("image/png", b"current"),
        with_product_roles=True,
    )
    assert "current cut alone never authorizes a garment detail" in call["prompt"].lower()
    assert "occlusion or an unavailable view is not evidence of absence" in call["prompt"].lower()
    assert "appear in neither the current cut nor the reference photos" not in call["prompt"]
