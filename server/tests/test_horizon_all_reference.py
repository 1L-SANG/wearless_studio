"""Complete horizon references use the existing provider and preserve source frames."""
import pytest
from app.agents import cut_generator as cg, cut_plan, content_roles


def _bound(**changes):
    spec = {"cutType": "horizon", "exampleId": "ss_example", "spaceGroupId": "ssg1__set__one",
            "shot": "medium", "direction": "front", "pose": "auto", **changes}
    cg.bind_horizon_reference(spec, {"shot": "medium", "direction": "front", "directionCompatible": spec["direction"] == "front"})
    return spec


def _prompt(spec):
    manifest = cg.build_manifest([{"slot": "Front"}], has_mannequin=False, has_match=False,
                                 mood_count=0, example_scope="all")
    return cg.build_prompt(spec, {"clothingType": "top"}, manifest=manifest)


def test_published_horizon_group_uses_all_but_styling_still_uses_pose():
    raw = {"cutType": "horizon", "exampleId": "ss_example", "spaceGroupId": "ssg1__set__one", "refScope": "pose"}
    assert cg.normalize_spec(raw)["refScope"] == "all"
    assert cg.normalize_spec({**raw, "cutType": "styling", "refScope": "all"})["refScope"] == "pose"


def test_verified_matching_frame_preserves_the_complete_photo_not_a_pose_mask():
    prompt = _prompt(_bound())
    assert "FRAMING — MATCHED COMPLETE HORIZON EXAMPLE" in prompt
    assert "actual frame boundaries, head/body crop" in prompt
    assert "Do not expand a cropped photograph" in prompt
    assert "POSE-ONLY RETARGETING" not in prompt
    assert "Small hand/foot adjustments" in prompt
    assert "${" not in prompt


@pytest.mark.parametrize("changes", [{"shot": "full"}, {"direction": "back"}])
def test_explicit_shot_or_direction_override_releases_reference_camera(changes):
    spec = _bound(**changes)
    prompt = _prompt(spec)
    assert "FRAMING — MATCHED COMPLETE HORIZON EXAMPLE" not in prompt
    plan = cut_plan.compile_cut_plan(cg.apply_reference_compatibility(cg.normalize_spec(spec)), "top")
    assert "camera" not in plan.reference_attributes
    if changes.get("direction"):
        assert "pose" not in plan.reference_attributes
        assert "DIRECTION-CHANGED" in prompt


def test_explicit_pose_overrides_reference_pose():
    spec = _bound(pose="walking")
    plan = cut_plan.compile_cut_plan(cg.apply_reference_compatibility(cg.normalize_spec(spec)), "top")
    assert plan.attribute_owners["pose"] == "storyboard"
    assert "USER POSE OVERRIDE" in _prompt(spec)


def test_horizon_color_reuse_does_not_force_different_pose():
    spec = _bound(spaceGroupId=None, _exampleRepeatIndex=2)
    assert "REPEATED ALL-SCOPE EXAMPLE" not in _prompt(spec)
    assert cut_plan.compile_cut_plan(cg.apply_reference_compatibility(cg.normalize_spec(spec)), "top").example_repeat_index == 0


def test_reference_frame_metadata_cannot_be_saved_from_client():
    stored = content_roles.canonicalize_storyboard([{"id": "one", "source": "ai", **_bound()}], for_storage=True)[0]
    assert "_horizonReferenceShot" not in stored


def test_standalone_registry_metadata_preserves_frame(monkeypatch):
    monkeypatch.setattr(cg, "load_example_asset_registry", lambda: ("", {"standalone": {"cutType": "horizon", "direction": "front", "shot": "medium"}}))
    spec = {"cutType": "horizon", "exampleId": "standalone", "shot": "medium", "direction": "front", "refScope": "all"}
    assert "FRAMING — MATCHED COMPLETE HORIZON EXAMPLE" in _prompt(spec)


def test_horizon_identity_manifest_names_only_attached_scene_owner():
    options = dict(has_mannequin=False, has_match=False, mood_count=0,
                   has_model_face=True, has_model_full_body=True, example_scope="all")
    original = cg.build_manifest([], **options)
    horizon = cg.build_manifest([], **options, example_is_horizon=True)
    assert "SPACE SET PLATE owns the location" in original
    assert "SPACE SET PLATE" not in horizon
    assert "EXAMPLE REFERENCE owns the studio" in horizon
    assert horizon.splitlines()[0] == original.splitlines()[0]
