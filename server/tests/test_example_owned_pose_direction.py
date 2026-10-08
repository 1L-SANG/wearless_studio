from app.agents import content_roles, cut_generator, cut_plan
from app.workers import detail_page_job


def block(**changes):
    return {
        "id": "b1", "source": "ai", "sectionRole": "studio", "sectionId": "s1",
        "cutType": "horizon", "shot": "full", "direction": "front", "colorId": "blue",
        "modelId": "mB", "exampleId": "ex_quarter", "refScope": "all", **changes,
    }


def registry(monkeypatch):
    monkeypatch.setattr(cut_generator, "load_example_asset_registry", lambda: (None, {
        "ex_quarter": {"cutType": "horizon", "direction": "side", "sideStyle": "threeQuarter", "shot": "full"},
        "ex_front": {"cutType": "horizon", "direction": "front", "shot": "full"},
        "ex_back": {"cutType": "product", "direction": "back", "shot": "ghost"},
    }))


def test_saved_blocks_drop_retired_pose_and_follow_published_example(monkeypatch):
    registry(monkeypatch)
    saved = content_roles.canonicalize_storyboard([
        block(pose="leaning on a wall", poseLabel="기대기", poseThumb="old.png"),
        block(id="b2", direction="back", pose="auto"),
    ], for_storage=True)
    assert [(item["direction"], item["sideStyle"]) for item in saved] == [
        ("side", "threeQuarter"), ("side", "threeQuarter"),
    ]
    assert all(not {"pose", "poseLabel", "poseThumb"} & item.keys() for item in saved)
    assert detail_page_job._duplicate_source_indexes(saved, "top") == [None, 0]


def test_virtual_quarter_orders_two_visible_eyes_without_face_pass(monkeypatch):
    registry(monkeypatch)
    prompt = cut_generator.build_prompt(block(pose="walk"), {"clothingType": "top"}, face_pass=False)
    assert "three-quarter view" in prompt
    assert "BOTH eyes visible" in prompt
    assert "a clear side profile" not in prompt
    assert "Pose: walk" not in prompt


def test_legacy_pose_does_not_disable_example_colorway_variation(monkeypatch):
    registry(monkeypatch)
    cards = [block(exampleId="ex_front", pose="walk", colorId=color) for color in ("blue", "white", "blue")]
    assert detail_page_job._example_repeat_indexes(cards, "top") == [0, 1, 0]
    spec = cut_generator.apply_reference_compatibility(cut_generator.normalize_spec(cards[1]))
    plan = cut_plan.compile_cut_plan({**spec, "_exampleRepeatIndex": 1}, "top")
    assert plan.attribute_owners["pose"] == "reference"
    assert plan.example_repeat_index == 1
    assert "pose" not in plan.storyboard_values


def test_product_detail_keeps_source_target_direction(monkeypatch):
    registry(monkeypatch)
    item = content_roles.canonicalize_storyboard_block(block(
        sectionRole="product", contentRole="detail", cutType="product", shot="detail",
        detailTargetId="actual-front-cuff", direction="front", exampleId="ex_back",
    ))
    assert item["direction"] == "front"


def test_missing_example_does_not_get_automatic_poses_or_directions():
    saved = content_roles.canonicalize_storyboard([
        block(id=f"b{i}", exampleId=None, direction=None, pose="auto") for i in range(6)
    ])
    assert all(item["direction"] == "front" for item in saved)
    assert all("pose" not in item for item in saved)
