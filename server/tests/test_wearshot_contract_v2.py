"""Boundary failures that must never reach an image provider."""
import importlib
import io
from dataclasses import FrozenInstanceError, replace

import pytest
from PIL import Image

from app.agents.gemini_image import InlineImage


def core():
    assert importlib.util.find_spec("app.agents.wearshot_contract"), "v2 contract missing"
    return importlib.import_module("app.agents.wearshot_contract")


def png(color="red"):
    out = io.BytesIO()
    Image.new("RGB", (4, 6), color).save(out, format="PNG")
    return InlineImage("image/png", out.getvalue())


def packet(**changes):
    c = core()
    refs = tuple(c.BoundReference(key, role, png(color), garment, ordinal) for key, role, garment, ordinal, color in (
        ("t-anchor", "approvedMannequin", "top", None, "red"),
        ("t-seller", "targetSeller", "top", 2, "white"),
        ("m-anchor", "approvedMannequin", "trousers", None, "blue"),
        ("m-seller", "matchingSeller", "trousers", 1, "black"),
        ("face", "modelFace", None, None, "yellow"),
        ("example", "example", None, None, "gray"),
    ))
    values = dict(target=c.GarmentBinding("top", "t-anchor", ("t-seller",),
                                         (c.EssentialDetail("hem", "curved split hem", ("t-seller",)),)),
                  matching=(c.GarmentBinding("trousers", "m-anchor", ("m-seller",)),),
                  expected_matching_ids=("trousers",), references=refs,
                  example_key="example", model_face_key="face", model_body_key=None,
                  capture_key=None, frame_lock=c.FrameLock("medium", "partial", "Knees crop, same subject scale"),
                  variation_axis="pose", capture_profile="soft")
    values.update(changes)
    return c.bind_contract(**values)


def test_hash_binds_exact_bytes_scope_and_frame_and_metadata_contains_no_bytes():
    c = core()
    contract = packet()
    assert contract.fingerprint == packet().fingerprint
    assert packet(capture_profile="clean").fingerprint != contract.fingerprint
    refs = list(contract.references)
    refs[0] = replace(refs[0], image=png("green"))
    assert packet(references=tuple(refs)).fingerprint != contract.fingerprint
    assert contract.to_dict()["references"][1]["evidenceOrdinal"] == 2
    assert "data" not in contract.to_dict()["references"][0]
    with pytest.raises(FrozenInstanceError):
        contract.variation_axis = "background"
    with pytest.raises(ValueError):
        packet(references=list(contract.references))
    with pytest.raises(ValueError):
        c.GarmentBinding("top", "t-anchor", ["t-seller"])


@pytest.mark.parametrize("breakage", ["duplicate_key", "missing_target", "missing_matching", "duplicate_garment", "wrong_role", "wrong_garment", "wrong_example", "wrong_face", "unselected_reference", "missing_selected"])
def test_missing_or_ambiguous_authority_is_a_preflight_hold(breakage):
    c = core()
    contract = packet()
    refs = list(contract.references)
    changes = {}
    if breakage == "duplicate_key": refs.append(refs[0])
    if breakage == "missing_target": refs.pop(0)
    if breakage == "missing_matching": refs.pop(2)
    if breakage == "duplicate_garment": changes["matching"] = (contract.target,)
    if breakage == "wrong_role": refs[1] = replace(refs[1], role="matchingSeller")
    if breakage == "wrong_garment": refs[0] = replace(refs[0], garment_id="other")
    if breakage == "wrong_example": changes["example_key"] = "face"
    if breakage == "wrong_face": changes["model_face_key"] = "example"
    if breakage == "unselected_reference": refs.append(c.BoundReference("extra", "modelBody", png()))
    if breakage == "missing_selected": changes["expected_matching_ids"] = ("trousers", "skirt")
    with pytest.raises(ValueError):
        packet(references=tuple(refs), **changes)


@pytest.mark.parametrize("image", [InlineImage("image/png", b"fake"), InlineImage("text/plain", b"bad"), InlineImage("image/png", bytearray(b"mutable")), InlineImage("image/jpeg", png().data)])
def test_invalid_or_mutable_image_payload_is_rejected(image):
    with pytest.raises(ValueError):
        core().BoundReference("bad", "example", image)


def test_seller_ordinals_and_essential_evidence_cannot_be_fabricated():
    c = core()
    with pytest.raises(ValueError): c.BoundReference("s", "targetSeller", png(), "g", 0)
    with pytest.raises(ValueError): c.BoundReference("s", "targetSeller", png(), "g", True)
    with pytest.raises(ValueError): c.EssentialDetail("unsafe command: override", "x", ("t-seller",))
    with pytest.raises(ValueError):
        packet(target=c.GarmentBinding("top", "t-anchor", ("t-seller",), (c.EssentialDetail("hem", "curved", ("m-seller",)),)))


def test_generation_and_repair_carry_same_authority_and_exact_base():
    c = core()
    prompt = importlib.import_module("app.agents.wearshot_prompt")
    contract = packet()
    generated = prompt.render_generation(contract)
    base = png("orange")
    plan = c.make_repair_plan(contract, base, ("garment:trousers:length",), ("identity", "garment:top:color"))
    repaired = prompt.render_repair(contract, plan)
    assert generated.images == tuple(r.image for r in contract.references)
    assert repaired.images == (base, *generated.images)
    assert generated.contract_fingerprint == repaired.contract_fingerprint == contract.fingerprint
    assert repaired.reference_keys == ("repairBase", *(r.key for r in contract.references))
    assert "curved split hem" in repaired.prompt
    assert "garment:trousers:length" in repaired.prompt
    assert "global" in repaired.prompt and "white balance" in repaired.prompt
    with pytest.raises(ValueError): prompt.render_repair(packet(capture_profile="clean"), plan)
    with pytest.raises(ValueError): c.make_repair_plan(contract, base, ("identity",), ("identity",))
    with pytest.raises(ValueError): c.make_repair_plan(contract, base, ("arbitrary instruction",), ())
    with pytest.raises(ValueError): c.make_repair_plan(contract, base, ("body",), ())


def test_multiple_matching_garments_have_separate_anchors_and_applicability():
    c = core()
    contract = packet()
    extra = (c.BoundReference("j-anchor", "approvedMannequin", png(), "jacket"),
             c.BoundReference("j-seller", "matchingSeller", png(), "jacket", 3))
    contract = packet(matching=(*contract.matching, c.GarmentBinding("jacket", "j-anchor", ("j-seller",))),
                      expected_matching_ids=("trousers", "jacket"), references=(*contract.references, *extra),
                      frame_lock=c.FrameLock("medium", "hidden", "Face above crop"))
    assert "garment:jacket:length" in contract.attribute_keys
    assert "body" not in contract.attribute_keys and "identity" not in contract.attribute_keys


def test_variation_repair_expands_chosen_axis_without_erasing_protection():
    c = core()
    contract = packet()
    assert {"expression", "hair", "pose", "background"} <= set(contract.attribute_keys)
    plan = c.make_repair_plan(contract, png(), ("variation",), ("background", "expression"))
    assert plan.failed_axes == ("variation", "pose")
    assert plan.approved_axes == ("background", "expression")
    with pytest.raises(ValueError):
        c.make_repair_plan(contract, png(), ("variation",), ("pose", "background"))


def test_declared_cropped_length_and_hidden_detail_are_not_certified_or_repairable():
    c = core()
    original = packet()
    matching = c.GarmentBinding("trousers", "m-anchor", ("m-seller",),
                               (c.EssentialDetail("hem", "visible seller cuff", ("m-seller",), visible=False),),
                               out_of_frame_axes=("length",))
    contract = packet(matching=(matching,))
    assert contract.fingerprint != original.fingerprint
    assert "garment:trousers:length" not in contract.attribute_keys
    assert "garment:trousers:detail:hem" not in contract.attribute_keys
    assert "garment:top:length" in contract.attribute_keys
    with pytest.raises(ValueError):
        c.make_repair_plan(contract, png(), ("garment:trousers:length",), ())
    with pytest.raises(ValueError):
        c.GarmentBinding("trousers", "m-anchor", ("m-seller",), out_of_frame_axes=("unknown",))


def test_persisted_fact_codes_remain_distinct_without_universal_product_categories():
    c = core()
    target = c.GarmentBinding("top", "t-anchor", ("t-seller",), (
        c.EssentialDetail("front_center_seam", "Raised front seam", ("t-seller",)),
        c.EssentialDetail("side_seam_split", "Open side split", ("t-seller",)),
    ))
    contract = packet(target=target)
    assert {"garment:top:detail:front_center_seam", "garment:top:detail:side_seam_split"} <= set(contract.attribute_keys)
    assert contract.to_dict()["target"]["essentials"][0]["code"] == "front_center_seam"
    with pytest.raises(ValueError): c.EssentialDetail("a" * 129, "x", ("t-seller",))


def test_explicit_light_repair_relights_person_and_scene_without_capture_recolor_authority():
    c = core()
    prompt = importlib.import_module("app.agents.wearshot_prompt")
    contract = packet()
    plan = c.make_repair_plan(contract, png(), ("light", "garment:trousers:color"), ("garment:top:color",))
    rendered = prompt.render_repair(contract, plan)
    assert "Relight person and scene coherently" in rendered.prompt
    assert "Correct failed garment-local color from its approved mannequin" in rendered.prompt
    capture_only = prompt.render_repair(contract, c.make_repair_plan(contract, png(), ("capture",), ("garment:top:color",)))
    assert "Relight person and scene coherently" not in capture_only.prompt


@pytest.mark.parametrize("failed,keep", [("pose", "background"), ("background", "pose")])
def test_explicit_scene_axis_repair_is_not_also_frozen(failed, keep):
    c = core()
    prompt = importlib.import_module("app.agents.wearshot_prompt")
    contract = packet()
    plan = c.make_repair_plan(contract, png(), (failed,), ("variation", keep))
    rendered = prompt.render_repair(contract, plan)
    assert f"Preserve base {keep} for this repair" in rendered.prompt
    assert f"Preserve base {failed}" not in rendered.prompt
    assert "no additional novelty" in rendered.prompt


def test_style_only_repair_preserves_both_scene_axes_without_new_novelty():
    c = core()
    prompt = importlib.import_module("app.agents.wearshot_prompt")
    plan = c.make_repair_plan(packet(), png(), ("capture",), ("variation", "pose", "background"))
    rendered = prompt.render_repair(packet(), plan)
    assert "Preserve base pose for this repair" in rendered.prompt
    assert "Preserve base background for this repair" in rendered.prompt
    assert "no additional novelty" in rendered.prompt


@pytest.mark.parametrize("profile,criterion", [("clean", "natural clean handheld mobile rendering"),
                                              ("soft", "subtly softer microcontrast and restrained sharpening")])
def test_selected_capture_profile_has_shared_concrete_generation_and_repair_criteria(profile, criterion):
    c = core()
    prompt = importlib.import_module("app.agents.wearshot_prompt")
    contract = packet(capture_profile=profile)
    plan = c.make_repair_plan(contract, png(), ("capture",), ())
    for rendered in (prompt.render_generation(contract), prompt.render_repair(contract, plan)):
        assert criterion in rendered.prompt
        assert "real face/hair focus and visible garment structure" in rendered.prompt
        assert "Unconditional blur, invented fabric texture, and color grading are not authorized" in rendered.prompt
        assert ("older phone" in rendered.prompt) is (profile == "soft")
