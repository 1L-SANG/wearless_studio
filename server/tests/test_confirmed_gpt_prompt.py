from dataclasses import replace
from hashlib import sha256

import pytest

from app.agents.confirmed_gpt_prompt import (
    ConfirmedGptPromptError,
    ConfirmedGptPromptInput,
    ConfirmedGptScope,
    CutLock,
    InputRole,
    OutfitLock,
    PoseSemantics,
    SellerEvidencePanel,
    SellerFact,
    SellerUncertainty,
    compile_confirmed_gpt_prompt,
)


EXPECTED_DRESS_PROMPT_SHA256 = (
    "a5434a4d953c710129c0bb4f5fb3a2de0657c6235d08cd2ca49f93e077c3445b"
)
EXPECTED_TOP_PROMPT_SHA256 = (
    "e8bfadd835a5a9194d92e4d0bc1adffb5f90ee757d05e08e332754e844f7a70e"
)


def _dress_request() -> ConfirmedGptPromptInput:
    return ConfirmedGptPromptInput(
        scope=ConfirmedGptScope(
            family_mode="styling/direct",
            reference_scope="all",
            pose="auto",
            reference_direction_compatible=True,
            space_group_id=None,
            selected_mannequin=True,
            example_source="service",
        ),
        ordered_roles=(
            InputRole.SELECTED_MANNEQUIN_CUT,
            InputRole.MODEL_FACE_DIRECTION_SHEET,
            InputRole.MODEL_FULL_BODY_DIRECTION_SHEET,
            InputRole.SOLD_PRODUCT_LABELED_EVIDENCE_GRID,
            InputRole.SERVICE_EXAMPLE_REFERENCE,
        ),
        seller_evidence=(
            SellerEvidencePanel(
                panel=1,
                slot="FRONT",
                detail="complete front",
                surface_authority="DOMINANT",
                judgeability="USABLE",
                limits=("fold_distortion", "mixed_light", "background_interference"),
                provided=True,
            ),
            SellerEvidencePanel(
                panel=2,
                slot="FRONT_DETAIL",
                detail="neckline, ties, button and pintucks",
                surface_authority="DOMINANT",
                judgeability="USABLE",
                limits=("fold_distortion", "mixed_light"),
                provided=True,
            ),
            SellerEvidencePanel(
                panel=3,
                slot="BACK",
                detail="complete back",
                surface_authority="CONTEXT",
                judgeability="USABLE",
                limits=("fold_distortion", "mixed_light", "background_interference"),
                provided=True,
            ),
            SellerEvidencePanel(
                panel=4,
                slot="FRONT_DETAIL",
                detail="sleeve tuck bands and elastic cuff",
                surface_authority="DOMINANT",
                judgeability="USABLE",
                limits=("partial_crop", "mixed_light"),
                provided=True,
            ),
        ),
        cut_lock=CutLock(
            shot="full",
            user_direction="front",
            direction_description=(
                "front-family full-body view with the example's walking action, compatible "
                "slight torso turn and naturally exposed side surfaces"
            ),
            face_exposure=(
                "visible; retain the example's broad head turn and off-camera gaze behavior "
                "while using the selected model identity"
            ),
            requested_framing=(
                "Show the complete head, sold dress, both legs, both complete feet and footwear "
                "with a modest floor margin. The short dress/tunic must remain fully inspectable."
            ),
        ),
        visible_surface_plan=(
            "FRONT and FRONT DETAIL panels are dominant. BACK is context only for side/rear "
            "slivers physically revealed by the slight turn. Preserve continuous side seams, "
            "tier volume and three-dimensional drape. Never move back neckline gathering onto "
            "the front or flatten the garment into a paper cutout."
        ),
        hard_facts=(
            SellerFact("color_family", "white color family"),
            SellerFact(
                "front_neckline",
                "split neckline with a narrow band edge, two slim ties and one small front button",
            ),
            SellerFact(
                "front_pintucks",
                "parallel vertical pintuck panels on both sides of the front opening",
            ),
            SellerFact(
                "tier_structure", "high horizontal seam into a gathered flared lower tier"
            ),
            SellerFact(
                "sleeve_construction",
                "voluminous sleeves with three horizontal tuck bands near the lower sleeve and "
                "elasticized cuffs",
            ),
            SellerFact("back_neck_gather", "gathering below the back neckline band"),
            SellerFact(
                "general_length",
                "short mini-dress / tunic proportion rather than midi or maxi length",
            ),
        ),
        uncertainties=(
            SellerUncertainty(
                "exact_hem_geometry",
                "exact high-low curve, wave amplitude and asymmetry of the worn hem",
                "flat presentation and folds do not prove the exact worn hem contour",
            ),
            SellerUncertainty(
                "exact_worn_length",
                "exact position of the hem on the selected model's thighs",
                "seller images contain no worn body scale",
            ),
            SellerUncertainty(
                "exact_white_tone",
                "exact warm/cool white balance",
                "mixed ambient light changes the photographed white",
            ),
            SellerUncertainty(
                "opacity",
                "exact worn opacity",
                "background, folds and light do not prove body-worn transparency",
            ),
            SellerUncertainty(
                "worn_volume",
                "exact torso ease and lower-tier volume on a body",
                "flat presentation does not prove body-worn volume",
            ),
        ),
        outfit=OutfitLock(
            fixed_inner=None,
            fixed_footwear="plain unbranded dark neutral flats",
            matching_attached=False,
        ),
        pose_semantics=PoseSemantics(
            action="a natural mid-step walk",
            body_direction="front-family with a slight turn",
            weight_and_support=(
                "one leg bearing the current step while the other advances; both feet remain "
                "physically grounded in the walking sequence"
            ),
            key_contacts="no support prop and no carried prop",
            gaze=(
                "head and gaze turned away from the camera in the same broad direction as the "
                "example"
            ),
            rough_framing="vertical full-body with head and both complete feet visible",
        ),
    )


def _top_request() -> ConfirmedGptPromptInput:
    return ConfirmedGptPromptInput(
        scope=ConfirmedGptScope(
            family_mode="styling/direct",
            reference_scope="all",
            pose="auto",
            reference_direction_compatible=True,
            space_group_id=None,
            selected_mannequin=True,
            example_source="service",
        ),
        ordered_roles=(
            InputRole.SELECTED_MANNEQUIN_CUT,
            InputRole.MODEL_FACE_DIRECTION_SHEET,
            InputRole.MODEL_FULL_BODY_DIRECTION_SHEET,
            InputRole.SOLD_PRODUCT_LABELED_EVIDENCE_GRID,
            InputRole.MATCHING_GARMENT_EVIDENCE,
            InputRole.SERVICE_EXAMPLE_REFERENCE,
        ),
        seller_evidence=(
            SellerEvidencePanel(
                1, "FRONT", "complete front", "DOMINANT", "USABLE",
                ("fold_distortion", "mixed_light", "background_interference"), True,
            ),
            SellerEvidencePanel(
                2, "FRONT_DETAIL", "collar, placket, buttons and stripe repeat",
                "DOMINANT", "USABLE", ("fold_distortion", "mixed_light"), True,
            ),
            SellerEvidencePanel(
                3, "BACK_DETAIL", "back yoke and stripe repeat", "CONTEXT", "USABLE",
                ("partial_crop", "fold_distortion", "mixed_light"), True,
            ),
        ),
        cut_lock=CutLock(
            shot="medium",
            user_direction="front",
            direction_description=(
                "front-family medium view with the example's relaxed asymmetry and compatible "
                "slight torso turn"
            ),
            face_exposure=(
                "visible; preserve the example's broad off-camera gaze behavior while using the "
                "selected model identity"
            ),
            requested_framing=(
                "Show the complete head and the complete sold shirt through its hem, plus enough "
                "of the selected matching trousers to make the layer relationship unambiguous. "
                "This is an independently composed medium cut, not a crop of a full-body image."
            ),
        ),
        visible_surface_plan=(
            "FRONT and FRONT DETAIL are dominant. BACK DETAIL is context only for the back yoke "
            "and any small rear sliver physically revealed by torso rotation. Preserve stripe "
            "continuity around the body. Never move the back yoke onto the front or turn the "
            "shirt into a flat texture overlay."
        ),
        hard_facts=(
            SellerFact(
                "stripe_family",
                "dense fine vertical pale-blue and beige/taupe multistripes on an off-white ground",
            ),
            SellerFact("collar", "pointed shirt collar"),
            SellerFact(
                "front_placket", "narrow full front button placket with small light buttons"
            ),
            SellerFact("sleeves_and_cuffs", "long sleeves with wide buttoned shirt cuffs"),
            SellerFact("back_yoke", "horizontal upper-back yoke seam"),
            SellerFact("general_length", "short cropped shirt proportion"),
        ),
        uncertainties=(
            SellerUncertainty(
                "exact_stripe_rgb_spacing",
                "exact stripe RGB values, individual line widths and repeat spacing",
                "phone white balance, folds and grid downsampling limit exact measurement",
            ),
            SellerUncertainty(
                "exact_hem_construction",
                "whether the curved/puckered hem appearance is elasticized, gathered or ordinary seam ease",
                "the folded flat view does not expose the full hem construction",
            ),
            SellerUncertainty(
                "exact_worn_fit",
                "exact ease, cropped hem position and volume on the selected model",
                "flat presentation does not prove body-worn fit",
            ),
            SellerUncertainty(
                "fiber_composition",
                "exact fiber composition and fabric weight",
                "appearance supports woven shirting but not composition",
            ),
        ),
        outfit=OutfitLock(
            fixed_inner=None,
            fixed_footwear="not visible unless naturally included by the medium composition",
            matching_attached=True,
        ),
        pose_semantics=PoseSemantics(
            action="quiet standing portrait with relaxed asymmetry",
            body_direction="front-family with a slight turn",
            weight_and_support=(
                "upright near-wall stance with subtle weight bias rather than a symmetric mannequin "
                "stance; whether the wall bears any body weight is uncertain"
            ),
            key_contacts=(
                "arms relaxed beside the torso; preserve the broad near-wall relationship, while "
                "exact physical back/shoulder contact is uncertain; no carried prop"
            ),
            gaze=(
                "head and gaze turned slightly off camera in the same broad direction as the example"
            ),
            rough_framing="vertical medium styling cut from complete head through the sold shirt hem",
        ),
    )


def test_confirmed_dress_fixture_replays_immutable_prompt_byte_for_byte() -> None:
    prompt = compile_confirmed_gpt_prompt(_dress_request())

    assert len(prompt.encode("utf-8")) == 12773
    assert sha256(prompt.encode("utf-8")).hexdigest() == EXPECTED_DRESS_PROMPT_SHA256


def test_confirmed_top_with_matching_replays_immutable_prompt_byte_for_byte() -> None:
    prompt = compile_confirmed_gpt_prompt(_top_request())

    assert len(prompt.encode("utf-8")) == 12731
    assert sha256(prompt.encode("utf-8")).hexdigest() == EXPECTED_TOP_PROMPT_SHA256


def test_confirmed_second_stage_appends_only_bounded_qc_corrections() -> None:
    baseline = compile_confirmed_gpt_prompt(_dress_request())
    repaired = compile_confirmed_gpt_prompt(
        _dress_request(),
        qc_corrections=("Restore the six-button topology.",),
    )

    assert repaired.startswith(baseline + "\n\nQC-VERIFIED SECOND-STAGE CORRECTIONS:\n")
    assert repaired.count("Restore the six-button topology.") == 1
    with pytest.raises(ConfirmedGptPromptError, match="invalid_qc_corrections"):
        compile_confirmed_gpt_prompt(_dress_request(), qc_corrections=tuple("abcdef"))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("family_mode", "styling/reference", "requires_styling_direct"),
        ("reference_scope", "pose", "requires_all_scope"),
        ("pose", "standing", "requires_auto_pose"),
        (
            "reference_direction_compatible",
            False,
            "requires_direction_compatible_reference",
        ),
        ("space_group_id", "space-1", "excludes_space_groups"),
        ("selected_mannequin", False, "requires_selected_mannequin"),
        ("example_source", "raw", "requires_service_example"),
    ),
)
def test_confirmed_prompt_rejects_unsupported_scope(field, value, message) -> None:
    request = _dress_request()
    request = replace(request, scope=replace(request.scope, **{field: value}))

    with pytest.raises(ConfirmedGptPromptError, match=message):
        compile_confirmed_gpt_prompt(request)


def test_confirmed_prompt_rejects_input_order_or_matching_drift() -> None:
    request = _dress_request()
    wrong_order = replace(
        request,
        ordered_roles=(
            InputRole.SELECTED_MANNEQUIN_CUT,
            InputRole.MODEL_FULL_BODY_DIRECTION_SHEET,
            InputRole.MODEL_FACE_DIRECTION_SHEET,
            InputRole.SOLD_PRODUCT_LABELED_EVIDENCE_GRID,
            InputRole.SERVICE_EXAMPLE_REFERENCE,
        ),
    )
    missing_matching = replace(
        request,
        outfit=replace(request.outfit, matching_attached=True),
    )

    with pytest.raises(ConfirmedGptPromptError, match="input_order_mismatch"):
        compile_confirmed_gpt_prompt(wrong_order)
    with pytest.raises(ConfirmedGptPromptError, match="input_order_mismatch"):
        compile_confirmed_gpt_prompt(missing_matching)


def test_confirmed_prompt_rejects_missing_evidence_and_facts() -> None:
    request = _dress_request()

    with pytest.raises(ConfirmedGptPromptError, match="one_to_four_evidence_panels"):
        compile_confirmed_gpt_prompt(replace(request, seller_evidence=()))
    with pytest.raises(ConfirmedGptPromptError, match="hard_facts_required"):
        compile_confirmed_gpt_prompt(replace(request, hard_facts=()))
    with pytest.raises(ConfirmedGptPromptError, match="uncertainties_required"):
        compile_confirmed_gpt_prompt(replace(request, uncertainties=()))


@pytest.mark.parametrize("direction", ["side", "back"])
def test_confirmed_prompt_does_not_expand_bh_front_baseline(direction) -> None:
    request = _dress_request()

    with pytest.raises(ConfirmedGptPromptError, match="requires_front_direction"):
        compile_confirmed_gpt_prompt(
            replace(request, cut_lock=replace(request.cut_lock, user_direction=direction))
        )


def test_confirmed_prompt_rejects_unresolved_tokens_in_dynamic_text() -> None:
    request = _dress_request()
    poisoned = replace(
        request,
        cut_lock=replace(request.cut_lock, requested_framing="${unresolved}"),
    )

    with pytest.raises(ConfirmedGptPromptError, match="contains_prompt_token"):
        compile_confirmed_gpt_prompt(poisoned)
