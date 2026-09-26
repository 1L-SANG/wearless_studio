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
    assert "FRAMING: MATCHED COMPLETE HORIZON EXAMPLE" in prompt
    assert "actual frame boundaries, head/body crop" in prompt
    assert "Do not expand a cropped photograph" in prompt
    assert "POSE-ONLY RETARGETING" not in prompt
    assert "Small hand/foot adjustments" in prompt
    assert "${" not in prompt


def test_matching_full_shot_keeps_the_feet_and_hem_safety_framing():
    """풀샷은 기장·밑단·신발 비율을 보는 컷이다. 예시와 샷이 같아도 기본 풀샷 문구를 쓴다(2026-09-26)."""
    spec = {"cutType": "horizon", "exampleId": "ss_example", "spaceGroupId": "ssg1__set__one",
            "shot": "full", "direction": "front", "pose": "auto"}
    cg.bind_horizon_reference(spec, {"shot": "full", "direction": "front", "directionCompatible": True})
    prompt = _prompt(spec)
    assert "never crop toes, heels, or the garment hem" in prompt
    assert "MATCHED COMPLETE HORIZON EXAMPLE" not in prompt
    assert "FRAMING: FRAMING" not in prompt
    assert "${" not in prompt


@pytest.mark.parametrize("changes", [{"shot": "full"}, {"direction": "back"}])
def test_explicit_shot_or_direction_override_releases_reference_camera(changes):
    spec = _bound(**changes)
    prompt = _prompt(spec)
    assert "FRAMING: MATCHED COMPLETE HORIZON EXAMPLE" not in prompt
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


def test_horizon_color_reuse_adds_bounded_micro_pose():
    """같은 완성 예시를 두 번째 색상부터 다시 쓰면 작은 포즈 변주를 준다(2026-09-26 오너 결정)."""
    spec = _bound(spaceGroupId=None, _exampleRepeatIndex=2)
    prompt = _prompt(spec)
    assert "REPEATED ALL-SCOPE EXAMPLE" in prompt
    assert "THIRD-USE MICRO-POSE" in prompt
    assert "POSE FROM EXAMPLE" in prompt
    assert "Preserve the complete example's body direction" not in prompt
    assert "deliberately vary poses across colorways" not in prompt
    assert "${" not in prompt
    assert cut_plan.compile_cut_plan(cg.apply_reference_compatibility(cg.normalize_spec(spec)), "top").example_repeat_index == 2
    first_color = _prompt(_bound(spaceGroupId=None, _exampleRepeatIndex=0))
    assert "Preserve the complete example's body direction" in first_color
    assert "REPEATED ALL-SCOPE EXAMPLE" not in first_color


def test_horizon_set_member_reuse_keeps_the_example_pose():
    spec = _bound(_exampleRepeatIndex=1)
    prompt = _prompt(spec)
    assert "REPEATED ALL-SCOPE EXAMPLE" not in prompt
    assert "Preserve the complete example's body direction" in prompt
    assert cut_plan.compile_cut_plan(cg.apply_reference_compatibility(cg.normalize_spec(spec)), "top").example_repeat_index == 0


@pytest.mark.parametrize("pose", content_roles._STUDIO_POSE_ROTATION)
def test_saved_auto_studio_pose_on_a_set_member_does_not_override_the_example(pose):
    """옛 서버가 저장해 둔 핏 섹션 자동 포즈는 예시 포즈를 덮지 않는다. 셀러가 고른 포즈는 그대로다."""
    spec = _bound(pose=pose)
    assert cg.normalize_spec(spec)["pose"] == "auto"
    assert "USER POSE OVERRIDE" not in _prompt(spec)
    assert cg.normalize_spec(_bound(pose="walking"))["pose"] == "walking"
    assert cg.normalize_spec({**_bound(pose=pose), "spaceGroupId": None})["pose"] != "auto"


def test_editor_single_cut_clears_saved_auto_pose_only_where_the_example_governs(monkeypatch):
    """에디터 한 컷 다시 만들기도 옛 자동 포즈 대신 예시 포즈를 따른다(2026-09-27). 방향이 달라 예시가
    포즈를 못 정하는 컷과 셀러가 고른 포즈는 그대로 둔다."""
    monkeypatch.setattr(cg, "load_example_asset_registry", lambda: ("", {"standalone": {"cutType": "horizon", "direction": "front", "shot": "full"}}))
    rotated = content_roles._STUDIO_POSE_ROTATION[1]
    spec = {"cutType": "horizon", "exampleId": "standalone", "shot": "full", "direction": "front",
            "refScope": "all", "pose": rotated}
    assert cg.clear_legacy_studio_pose(spec)["pose"] == "auto"
    assert cg.clear_legacy_studio_pose({**spec, "direction": "back"})["pose"] == rotated
    assert cg.clear_legacy_studio_pose({**spec, "pose": "walking"})["pose"] == "walking"
    assert cg.clear_legacy_studio_pose({**spec, "cutType": "styling"})["pose"] == rotated


def test_snake_case_set_members_are_also_skipped_by_studio_pose_rotation():
    grouped = [{"sectionRole": "studio", "cutType": "horizon", "pose": "auto",
                "space_group_id": "ssg1__set__one", "exampleId": "ss_example"} for _ in range(3)]
    assert all(b.get("pose") in (None, "", "auto") for b in content_roles.canonicalize_storyboard(grouped))


def test_reference_frame_metadata_cannot_be_saved_from_client():
    stored = content_roles.canonicalize_storyboard([{"id": "one", "source": "ai", **_bound()}], for_storage=True)[0]
    assert "_horizonReferenceShot" not in stored


def test_standalone_registry_metadata_preserves_frame(monkeypatch):
    monkeypatch.setattr(cg, "load_example_asset_registry", lambda: ("", {"standalone": {"cutType": "horizon", "direction": "front", "shot": "medium"}}))
    spec = {"cutType": "horizon", "exampleId": "standalone", "shot": "medium", "direction": "front", "refScope": "all"}
    assert "FRAMING: MATCHED COMPLETE HORIZON EXAMPLE" in _prompt(spec)


def test_horizon_identity_manifest_names_only_attached_scene_owner():
    options = dict(has_mannequin=False, has_match=False, mood_count=0,
                   has_model_face=True, has_model_full_body=True, example_scope="all")
    original = cg.build_manifest([], **options)
    horizon = cg.build_manifest([], **options, example_is_horizon=True)
    assert "SPACE SET PLATE owns the location" in original
    assert "SPACE SET PLATE" not in horizon
    assert "EXAMPLE REFERENCE owns the studio" in horizon
    assert horizon.splitlines()[0] == original.splitlines()[0]


@pytest.mark.parametrize("in_set", [True, False])
def test_horizon_example_keeps_model_body_continuity_rules(monkeypatch, in_set):
    """호리존 라벨 문구가 바뀌어도 선택 모델 체형 규칙([[BODY_REF]])은 빠지면 안 된다(2026-09-26 회귀)."""
    monkeypatch.setattr(cg, "load_example_asset_registry", lambda: ("", {"standalone": {"cutType": "horizon", "direction": "front", "shot": "medium"}}))
    manifest = cg.build_manifest([{"slot": "Front"}], has_mannequin=False, has_match=False, mood_count=0,
                                 has_model_face=True, has_model_full_body=True,
                                 example_scope="all", example_is_horizon=True)
    assert "EXAMPLE REFERENCE owns the studio" in manifest
    spec = _bound() if in_set else {"cutType": "horizon", "exampleId": "standalone", "shot": "medium",
                                    "direction": "front", "refScope": "all"}
    prompt = cg.build_prompt(spec, {"clothingType": "top"}, manifest=manifest)
    assert "MODEL FULL-BODY CONTINUITY" in prompt
    assert "${" not in prompt
