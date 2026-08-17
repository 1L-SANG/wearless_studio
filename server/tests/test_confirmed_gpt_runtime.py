from io import BytesIO
from types import SimpleNamespace

from PIL import Image
import pytest

from app.agents import confirmed_gpt_runtime, product_evidence_contract
from app.agents.confirmed_gpt_prompt import (
    CutLock,
    OutfitLock,
    PoseSemantics,
    compile_confirmed_gpt_prompt,
)
from app.agents.gemini_image import InlineImage


def _png(color="navy") -> bytes:
    out = BytesIO()
    Image.new("RGB", (900, 900), color).save(out, format="PNG")
    return out.getvalue()


def _contract(source: bytes) -> dict:
    binding = product_evidence_contract.build_input_binding(
        [(source, "image/png")], [(source, "image/png")], ["Front"]
    )
    return product_evidence_contract.validate_and_bind(
        {
            "panels": [
                {
                    "evidenceOrdinal": 1,
                    "detail": "front neckline, sleeves and hem",
                    "judgeability": "usable",
                    "judgeabilityReasons": ["clear_enough"],
                }
            ],
            "hardFacts": [
                {
                    "code": "front_shape",
                    "value": "The front neckline, sleeves and hem are visible.",
                    "evidenceOrdinals": [1],
                }
            ],
            "uncertainties": [
                {
                    "code": "back_shape",
                    "value": "The hidden back construction is not proven.",
                    "reason": "Only the front seller view is supplied.",
                    "evidenceOrdinals": [1],
                }
            ],
            "visibleSurfacePlan": (
                "FRONT is DOMINANT; preserve the visible neckline, sleeves and hem."
            ),
        },
        binding,
    )


def _directing():
    pose = PoseSemantics(
        action="quiet standing portrait",
        body_direction="front-family with a slight turn",
        weight_and_support="weight biased to one side",
        key_contacts="arms relaxed without a prop",
        gaze="slightly off camera",
        rough_framing="vertical medium styling cut",
    )
    return SimpleNamespace(
        cut_lock=lambda: CutLock(
            shot="medium",
            user_direction="front",
            direction_description="front-family medium view",
            face_exposure="visible with an off-camera gaze",
            requested_framing="complete head through the sold top hem",
        ),
        outfit_lock=lambda *, matching_attached: OutfitLock(
            fixed_inner=None,
            fixed_footwear="not visible in the medium composition",
            matching_attached=matching_attached,
        ),
        pose_semantics=pose,
    )


def _spec():
    return {
        "cutType": "styling",
        "direction": "front",
        "shot": "medium",
        "refScope": "all",
        "pose": "auto",
        "spaceGroupId": None,
        "exampleId": "ex",
        "_referenceDirectionCompatible": True,
    }


def _build(monkeypatch, *, source=None, selected="mE", effective="mE", matches=()):
    source = source or _png()
    monkeypatch.setattr(
        confirmed_gpt_runtime,
        "bind_confirmed_gpt_directing",
        lambda *_args, **_kwargs: _directing(),
    )
    image = InlineImage("image/png", source)
    return confirmed_gpt_runtime.build_packet(
        _spec(),
        clothing_type="top",
        identity_source="VIRTUAL",
        selected_model_id=selected,
        effective_model_id=effective,
        uses_base_color=True,
        mannequin_image=InlineImage("image/png", b"mannequin"),
        face_direction_sheet=InlineImage("image/png", b"face sheet"),
        full_body_direction_sheet=InlineImage("image/png", b"body sheet"),
        seller_images=(("Front", image),),
        matching_images=matches,
        example_image=InlineImage("image/png", b"example"),
        evidence_contract=_contract(source),
    )


def test_packet_replays_exact_role_order_and_compiles(monkeypatch):
    packet = _build(monkeypatch)

    assert [image.data for image in packet.images[:3]] == [
        b"mannequin",
        b"face sheet",
        b"body sheet",
    ]
    assert packet.images[-1].data == b"example"
    assert len(packet.images) == 5
    assert packet.manifest.splitlines() == [
        "1. MANNEQUIN — selected garment-local color and fit authority",
        "2. MODEL FACE — direction sheet",
        "3. MODEL FULL BODY — direction sheet",
        "4. PRODUCT — sold-product labelled evidence grid",
        "5. EXAMPLE REFERENCE (scope: all) — service reference",
    ]
    prompt = compile_confirmed_gpt_prompt(packet.prompt_input)
    assert "existing_exact" not in prompt
    assert "RECENT IPHONE DEFAULT PHOTO CONTRACT" in prompt
    assert "naturally plausible nearby-feeling alternate" in prompt


def test_packet_inserts_only_one_matching_image_before_example(monkeypatch):
    matching = InlineImage("image/png", b"matching")
    packet = _build(monkeypatch, matches=(matching,))
    assert [image.data for image in packet.images[-2:]] == [b"matching", b"example"]
    assert packet.manifest.splitlines()[-2].startswith("5. MATCHING")


def test_packet_fails_on_current_seller_byte_drift(monkeypatch):
    source = _png()
    contract = _contract(source)
    monkeypatch.setattr(
        confirmed_gpt_runtime,
        "bind_confirmed_gpt_directing",
        lambda *_args, **_kwargs: _directing(),
    )
    with pytest.raises(
        confirmed_gpt_runtime.ConfirmedGptRuntimeError,
        match="seller_source_binding_drift",
    ):
        confirmed_gpt_runtime.build_packet(
            _spec(),
            clothing_type="top",
            identity_source="VIRTUAL",
            selected_model_id="mE",
            effective_model_id="mE",
            uses_base_color=True,
            mannequin_image=InlineImage("image/png", b"mannequin"),
            face_direction_sheet=InlineImage("image/png", b"face"),
            full_body_direction_sheet=InlineImage("image/png", b"body"),
            seller_images=(("Front", InlineImage("image/png", _png("red"))),),
            matching_images=(),
            example_image=InlineImage("image/png", b"example"),
            evidence_contract=contract,
        )


def test_packet_forbids_silent_virtual_model_substitution(monkeypatch):
    with pytest.raises(
        confirmed_gpt_runtime.ConfirmedGptRuntimeError,
        match="forbids_model_substitution",
    ):
        _build(monkeypatch, selected="unknown", effective="mB")


def test_uncurated_eligible_example_requests_fail_closed_profile():
    assert confirmed_gpt_runtime.profile_requested(_spec()) is True


def test_explicitly_excluded_example_uses_the_generic_route(monkeypatch):
    monkeypatch.setattr(
        confirmed_gpt_runtime,
        "confirmed_gpt_explicitly_excluded",
        lambda example_id: example_id == "ex",
    )

    assert confirmed_gpt_runtime.profile_requested(_spec()) is False


def test_invalid_eligibility_catalog_fails_the_exact_route_closed(monkeypatch):
    def broken(_example_id):
        raise confirmed_gpt_runtime.ConfirmedGptDirectingError("catalog drift")

    monkeypatch.setattr(
        confirmed_gpt_runtime,
        "confirmed_gpt_explicitly_excluded",
        broken,
    )

    with pytest.raises(
        confirmed_gpt_runtime.ConfirmedGptRuntimeError,
        match="catalog drift",
    ):
        confirmed_gpt_runtime.profile_requested(_spec())
