import pytest

from app.agents.cut_plan import CutPlanError, compile_cut_plan, render_prompt_contract


def worn_spec(cut_type="styling", **changes):
    spec = {
        "cutType": cut_type,
        "direction": None if cut_type == "mirror" else "front",
        "shot": "full",
        "faceExposure": "hide" if cut_type == "mirror" else "same",
        "pose": "auto",
        "colorId": "black",
        "matchIds": [],
        "modelId": None,
        "outerClosureState": None,
        "exampleId": None,
        "spaceGroupId": None,
        "refScope": "all",
    }
    spec.update(changes)
    return spec


def product_spec(**changes):
    spec = {
        "cutType": "product",
        "direction": "front",
        "shot": "ghost",
        "faceExposure": None,
        "pose": "auto",
        "colorId": "black",
        "matchIds": [],
        "modelId": None,
        "outerClosureState": None,
        "exampleId": None,
        "spaceGroupId": None,
        "refScope": "all",
    }
    spec.update(changes)
    return spec


def test_styling_without_reference_uses_lifestyle_recipe_and_no_reference_owner():
    plan = compile_cut_plan(worn_spec(), "top")

    assert plan.recipe_family == "styling"
    assert plan.capture_mode == "lifestyle"
    assert plan.reference_mode == "none"
    assert plan.reference_attributes == ()
    assert plan.attribute_owners["construction"] == "productTruth"
    assert plan.attribute_owners["length"] == "productTruth"
    assert plan.attribute_owners["silhouette"] == "productTruth"
    assert plan.attribute_owners["textLogo"] == "productTruth"
    assert plan.attribute_owners["color"] == "storyboard"
    assert plan.attribute_owners["direction"] == "storyboard"
    assert plan.attribute_owners["pose"] == "recipe"
    assert plan.attribute_owners["scene"] == "recipe"


def test_mirror_maps_to_styling_recipe_with_mirror_capture():
    plan = compile_cut_plan(
        worn_spec("mirror", exampleId="ex_mirror", refScope="all"), "dress"
    )

    assert plan.recipe_family == "styling"
    assert plan.capture_mode == "mirrorSelfie"
    assert plan.product_variant is None
    assert plan.reference_mode == "all"
    assert plan.attribute_owners["pose"] == "reference"
    assert plan.attribute_owners["camera"] == "reference"
    assert plan.attribute_owners["direction"] == "storyboard"


def test_horizon_all_reference_loses_explicit_pose_and_direction():
    plan = compile_cut_plan(
        worn_spec(
            "horizon",
            direction="side",
            pose="one hand in pocket",
            exampleId="ex_horizon",
            refScope="all",
        ),
        "outer",
    )

    assert plan.recipe_family == "horizon"
    assert plan.capture_mode == "studio"
    assert plan.attribute_owners["pose"] == "storyboard"
    assert "pose" not in plan.reference_attributes
    assert plan.attribute_owners["direction"] == "storyboard"
    assert plan.attribute_owners["camera"] == "reference"
    assert plan.to_dict()["conflictResolution"]["storyboardDirectionOverridesReference"]
    assert plan.to_dict()["conflictResolution"]["explicitStoryboardPoseOverridesReference"]
    assert plan.to_dict()["conflictResolution"]["explicitStoryboardPoseApplied"]


def test_all_reference_with_changed_direction_loses_pose_and_camera_authority():
    plan = compile_cut_plan(
        worn_spec(
            direction="side",
            exampleId="front-example",
            refScope="all",
            _referenceDirectionCompatible=False,
        ),
        "top",
    )

    assert plan.reference_direction_compatible is False
    assert plan.reference_attributes == ("scene", "light", "captureTone")
    assert plan.attribute_owners["pose"] == "recipe"
    assert plan.attribute_owners["camera"] == "recipe"
    assert plan.attribute_owners["direction"] == "storyboard"


def test_reference_face_visibility_is_preserved_for_independent_qc():
    plan = compile_cut_plan(
        worn_spec(
            exampleId="hidden-face-example",
            refScope="all",
            _referenceFaceVisibility="hidden",
        ),
        "outer",
    )

    assert plan.reference_face_visibility == "hidden"
    assert plan.to_dict()["referenceFaceVisibility"] == "hidden"


def test_selected_model_owns_face_and_body_while_example_person_owns_neither():
    plan = compile_cut_plan(
        worn_spec(modelId="model-a", exampleId="example-a", refScope="all"),
        "top",
    )

    assert plan.attribute_owners["faceIdentity"] == "modelFace"
    assert plan.attribute_owners["bodyProportions"] == "modelFullBody"
    assert "faceIdentity" not in plan.reference_attributes
    assert "bodyProportions" not in plan.reference_attributes
    assert plan.to_dict()["conflictResolution"]["examplePersonFaceAndBodyAuthority"] is False


def test_house_model_recipe_owns_face_and_body_when_no_model_is_selected():
    plan = compile_cut_plan(worn_spec(), "top")

    assert plan.attribute_owners["faceIdentity"] == "recipe"
    assert plan.attribute_owners["bodyProportions"] == "recipe"


@pytest.mark.parametrize("variant", ["ghost", "detail"])
def test_product_recipe_uses_product_variant_and_never_reference_pose(variant):
    plan = compile_cut_plan(
        product_spec(shot=variant, exampleId="ex_product", refScope="all"), "bottom"
    )

    assert plan.recipe_family == "product"
    assert plan.capture_mode is None
    assert plan.product_variant == variant
    assert plan.reference_mode == "all"
    assert "pose" not in plan.reference_attributes
    assert plan.attribute_owners["pose"] == "recipe"
    assert plan.attribute_owners["camera"] == "reference"


def test_reference_scope_owns_only_its_compatible_attributes():
    pose_plan = compile_cut_plan(
        worn_spec(exampleId="ex_pose", refScope="pose"), "top"
    )
    bg_plan = compile_cut_plan(
        worn_spec(exampleId="ex_bg", refScope="bg"), "top"
    )

    assert pose_plan.reference_attributes == ("pose",)
    assert pose_plan.attribute_owners["camera"] == "recipe"
    assert pose_plan.attribute_owners["scene"] == "recipe"
    assert bg_plan.reference_attributes == ("scene", "light", "captureTone")
    assert bg_plan.attribute_owners["pose"] == "recipe"
    assert bg_plan.attribute_owners["camera"] == "recipe"


def test_space_set_forces_pose_reference_and_owns_only_continuity():
    plan = compile_cut_plan(
        worn_spec(
            exampleId="set_member",
            spaceGroupId="ssg1__set__instance",
            refScope="all",
            _spaceSetContinuity=True,
        ),
        "top",
    )

    assert plan.reference_mode == "pose"
    assert plan.space_set_continuity is True
    assert plan.attribute_owners["sceneContinuity"] == "spaceSet"
    assert plan.attribute_owners["pose"] == "reference"
    assert plan.attribute_owners["camera"] == "recipe"
    assert plan.attribute_owners["direction"] == "storyboard"
    assert plan.to_dict()["conflictResolution"]["spaceSetCameraAndPoseRemainCutSpecific"]


def test_explicit_pose_still_wins_inside_space_set():
    plan = compile_cut_plan(
        worn_spec(
            pose="crossed arms",
            spaceGroupId="ssg1__set__instance",
            refScope="pose",
        ),
        "top",
    )

    assert plan.reference_mode == "pose"
    assert plan.attribute_owners["pose"] == "storyboard"
    assert "pose" not in plan.reference_attributes


def test_fit_profile_owns_only_declared_axes():
    plan = compile_cut_plan(
        worn_spec(),
        "top",
        fit_profile={"axes": {"fit": "over", "length": "crop", "unused": None}},
    )

    assert plan.declared_fit_axes == ("fit", "length")
    assert plan.attribute_owners["fit.fit"] == "fitProfile"
    assert plan.attribute_owners["fit.length"] == "fitProfile"
    assert "fit.unused" not in plan.attribute_owners
    assert "fit" not in plan.attribute_owners
    assert plan.attribute_owners["material"] == "productTruth"


def test_contract_renderer_is_compact_and_names_ownership_rules():
    plan = compile_cut_plan(
        worn_spec(
            exampleId="ex", refScope="all", pose="walking", modelId="model-a"
        ),
        "top",
    )

    rendered = render_prompt_contract(plan)
    assert rendered.startswith("CUT PLAN AUTHORITY")
    assert "recipe=styling/lifestyle" in rendered
    assert (
        "productTruth: construction, hardware, length, material, pattern, silhouette, textLogo"
        in rendered
    )
    assert "storyboard:" in rendered and "pose" in rendered
    assert "modelFace: faceIdentity" in rendered
    assert "modelFullBody: bodyProportions" in rendered
    assert "reference: camera, captureTone, light, scene" in rendered
    assert "explicit pose override the example" in rendered
    assert "example person owns neither" in rendered
    assert "Seller name/category/sales/legacy-fit text owns no geometry" in rendered
    assert "Styling/all keeps scene type, ordinary ambience, palette, time" in rendered
    assert ">=1 spatial-structure and >=2 furniture/sign/prop placement changes" in rendered
    assert len(rendered) < 1_200


def test_location_recomposition_applies_only_to_lifestyle_styling_all():
    styling_all = compile_cut_plan(
        worn_spec(exampleId="ex", refScope="all"), "top"
    )
    excluded = (
        compile_cut_plan(worn_spec(exampleId="ex", refScope="bg"), "top"),
        compile_cut_plan(
            worn_spec(
                "mirror", direction=None, faceExposure="hide",
                exampleId="ex", refScope="all",
            ),
            "top",
        ),
        compile_cut_plan(
            worn_spec("horizon", exampleId="ex", refScope="all"), "top"
        ),
        compile_cut_plan(
            product_spec(exampleId="ex", refScope="all"), "top"
        ),
        compile_cut_plan(
            worn_spec(
                exampleId="set-member",
                spaceGroupId="ssg1__set__instance",
                refScope="all",
            ),
            "top",
        ),
    )

    assert styling_all.uses_styling_all_location_recomposition() is True
    assert styling_all.to_dict()["conflictResolution"][
        "stylingAllLocationRecompositionApplied"
    ] is True
    for plan in excluded:
        assert plan.uses_styling_all_location_recomposition() is False
        assert plan.to_dict()["conflictResolution"][
            "stylingAllLocationRecompositionApplied"
        ] is False
        assert "different location instance" not in render_prompt_contract(plan)


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        (worn_spec(cutType="unknown"), "unknown_cut_type"),
        (worn_spec(shot="ghost"), "invalid_styling_spec"),
        (worn_spec("mirror", direction="front"), "invalid_mirror_spec"),
        (product_spec(refScope="pose", exampleId="ex"), "product_reference_mode_must_be_all"),
        (worn_spec(_referenceDirectionCompatible="yes"),
         "invalid_reference_direction_compatibility"),
        (worn_spec(_referenceFaceVisibility="unknown"),
         "invalid_reference_face_visibility"),
    ],
)
def test_invalid_normalized_combinations_fail_closed(spec, message):
    with pytest.raises(CutPlanError, match=message):
        compile_cut_plan(spec, "top")


def test_reference_scope_is_none_when_there_is_no_example_or_space_set():
    plan = compile_cut_plan(worn_spec(refScope="bg"), "top")
    assert plan.reference_mode == "none"
    assert plan.space_set_continuity is None


def test_invalid_clothing_type_and_space_continuity_fail_closed():
    with pytest.raises(CutPlanError, match="unknown_clothing_type"):
        compile_cut_plan(worn_spec(), "hat")
    with pytest.raises(CutPlanError, match="invalid_space_set_continuity"):
        compile_cut_plan(
            worn_spec(spaceGroupId="ssg1__set__instance", _spaceSetContinuity="yes"),
            "top",
        )
