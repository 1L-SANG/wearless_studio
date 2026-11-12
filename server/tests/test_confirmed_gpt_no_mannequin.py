"""Product-evidence variant of the confirmed path; all image and IO inputs are local fakes."""

from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from app.agents import confirmed_gpt_prompt as prompt, confirmed_gpt_runtime as runtime
from app.agents.gemini_image import InlineImage
from test_confirmed_gpt_prompt import _top_request
from test_confirmed_gpt_runtime import _contract, _directing, _png, _spec


def _packet(monkeypatch, *, mannequin=None, clothing_type="top", analysis=None, outfits=(), matching=()):
    source = _png()
    monkeypatch.setattr(runtime, "bind_confirmed_gpt_directing", lambda *a, **k: _directing())
    return runtime.build_packet(
        _spec(), clothing_type=clothing_type, identity_source="VIRTUAL",
        selected_model_id="mF", effective_model_id="mF", uses_base_color=True,
        mannequin_image=mannequin,
        face_direction_sheet=InlineImage("image/png", b"face"),
        full_body_direction_sheet=InlineImage("image/png", b"body"),
        seller_images=(("Front", InlineImage("image/png", source)),),
        matching_images=matching, outfit_images=outfits,
        example_image=InlineImage("image/png", b"example"),
        evidence_contract=_contract(source), analysis=analysis,
    )


def test_packet_without_mannequin_has_exact_product_order_and_labels(monkeypatch):
    outfits = tuple((role, InlineImage("image/png", role.encode()))
                    for role in ("inner", "footwear", "bag", "accessory", "accessory"))
    packet = _packet(monkeypatch, clothing_type="outer", outfits=outfits,
                     matching=(InlineImage("image/png", b"bottom"),))
    assert packet.prompt_input.scope.selected_mannequin is False
    assert [i.data for i in packet.images[:2]] == [b"face", b"body"]
    assert sha256(packet.images[2].data).hexdigest() == packet.evidence_grid_sha256
    assert [i.data for i in packet.images[3:]] == [
        b"inner", b"bottom", b"footwear", b"bag", b"accessory", b"accessory", b"example"]
    assert packet.manifest.splitlines() == [
        "1. MODEL FACE — direction sheet",
        "2. MODEL FULL BODY — direction sheet",
        "3. PRODUCT — sold-product labelled evidence grid",
        "4. OUTFIT INNER - directly selected inner evidence",
        "5. MATCHING — directly selected garment evidence",
        "6. OUTFIT FOOTWEAR - directly selected item evidence",
        "7. OUTFIT BAG - directly selected item evidence",
        "8. OUTFIT ACCESSORY - directly selected item evidence",
        "9. OUTFIT ACCESSORY - directly selected item evidence",
        "10. EXAMPLE REFERENCE (scope: all) — service reference",
    ]
    rendered = prompt.compile_confirmed_gpt_prompt(packet.prompt_input)
    assert "MANNEQUIN" not in rendered
    assert "SOLD PRODUCT GARMENT-LOCAL COLOR AND FIT AUTHORITY:" in rendered


def test_product_template_changes_only_authority_paragraphs():
    old = prompt.load_confirmed_gpt_template().split("\n\n")
    new = prompt.load_confirmed_gpt_product_template().split("\n\n")
    assert len(old) == len(new)
    changed = [a.split(":", 1)[0] for a, b in zip(old, new, strict=True) if a != b]
    assert len(changed) == 3
    assert changed[0].startswith("HARD SELLER FACTS")
    assert changed[1].startswith("SELLER-ONLY UNCERTAINTIES")
    assert changed[2] == "SELECTED MANNEQUIN GARMENT-LOCAL COLOR AND FIT AUTHORITY"
    old_request = _top_request()
    new_request = replace(old_request,
        scope=replace(old_request.scope, selected_mannequin=False),
        ordered_roles=old_request.ordered_roles[1:])
    old_rendered = prompt.compile_confirmed_gpt_prompt(old_request).split("\n\n")
    new_rendered = prompt.compile_confirmed_gpt_prompt(new_request).split("\n\n")
    for old_paragraph, new_paragraph in zip(old_rendered, new_rendered, strict=True):
        if old_paragraph.startswith("PRODUCT AND MODEL LOCK"):
            assert old_paragraph.replace("Fixed inner: none. ", "") == new_paragraph
        elif old_paragraph.startswith(("BOUNDED POSE SEMANTICS",
                                     "BOUNDED ADJACENT-PLACE", "CAPTURE RENDERING",
                                     "CAMERA AND PRODUCT SAFETY", "CONDITIONAL POINT LIGHT",
                                     "PHYSICAL INTEGRATION", "Output exactly")):
            assert old_paragraph == new_paragraph


@pytest.mark.parametrize("analysis, expected", [
    ({"fit": "regular"}, "regular"),
    ({"fit": "slim", "fitProfile": {"category": "top", "gender": "women", "version": 2,
      "axes": {"fit": "over", "length": "crop", "sleeve": "cap"}}},
     "fit: oversized volume, dropped shoulders, roomy chest and wide sleeves. Observable target: shoulder seam below the shoulder point and clear air around chest, waist, and sleeves."),
    ({"fit": "slim", "fitProfile": {"category": "top", "gender": "women", "axes": {}}}, None),
    ({}, None),
])
def test_analysis_fit_uses_generic_sources_without_new_length_or_sleeve_axes(monkeypatch, analysis, expected):
    packet = _packet(monkeypatch, analysis=analysis)
    assert packet.prompt_input.sold_product_fit == expected
    rendered = prompt.compile_confirmed_gpt_prompt(packet.prompt_input)
    assert "high waist" not in rendered
    assert "caps the shoulder" not in rendered
    assert "No analysis fit is declared." in rendered if expected is None else expected in rendered


def test_old_packet_ignores_analysis_and_keeps_original_prompt(monkeypatch):
    mannequin = InlineImage("image/png", b"selected")
    baseline = _packet(monkeypatch, mannequin=mannequin)
    with_analysis = _packet(monkeypatch, mannequin=mannequin,
                            analysis={"fit": "over", "fitProfile": {"axes": {"fit": "slim"}}})
    assert with_analysis.images == baseline.images
    assert with_analysis.manifest == baseline.manifest
    assert prompt.compile_confirmed_gpt_prompt(with_analysis.prompt_input) == prompt.compile_confirmed_gpt_prompt(baseline.prompt_input)


@pytest.mark.parametrize("selected_inner", [False, True])
def test_outer_product_variant_uses_selected_inner_without_a_default(monkeypatch, selected_inner):
    outfits = (("inner", InlineImage("image/png", b"chosen-inner")),) if selected_inner else ()
    packet = _packet(monkeypatch, clothing_type="outer", outfits=outfits)
    rendered = prompt.compile_confirmed_gpt_prompt(packet.prompt_input)
    assert "Fixed inner:" not in rendered
    assert "exactly one plain, solid white crew-neck T-shirt" not in rendered
    if selected_inner:
        assert packet.images[3].data == b"chosen-inner"
        assert "Inner: reproduce SELECTED INNER EVIDENCE." in rendered
        assert "solid white crew-neck T-shirt" not in rendered
    else:
        assert "SELECTED INNER EVIDENCE" not in rendered
        assert "crew-neck T-shirt" not in rendered


def test_unselected_inner_does_not_use_the_examples_fixed_inner(monkeypatch):
    packet = _packet(monkeypatch, clothing_type="outer")
    request = replace(packet.prompt_input, outfit=replace(
        packet.prompt_input.outfit, fixed_inner="plain black crew-neck top"))
    rendered = prompt.compile_confirmed_gpt_prompt(request)
    assert "Fixed inner:" not in rendered
    assert "plain black crew-neck top" not in rendered
    assert "Inner: reproduce" not in rendered
    assert "Never copy the example person's face, hair identity, body, clothing" in rendered


def test_product_variant_still_rejects_missing_and_reordered_roles(monkeypatch):
    packet = _packet(monkeypatch)
    for roles in (packet.prompt_input.ordered_roles[1:], tuple(reversed(packet.prompt_input.ordered_roles))):
        with pytest.raises(prompt.ConfirmedGptPromptError, match="input_order_mismatch"):
            prompt.compile_confirmed_gpt_prompt(replace(packet.prompt_input, ordered_roles=roles))
    with pytest.raises(runtime.ConfirmedGptRuntimeError, match="mannequin_image_required"):
        _packet(monkeypatch, mannequin=InlineImage("image/png", b""))


def test_original_template_hash_is_unchanged():
    original = Path(prompt.__file__).resolve().parents[2] / "prompts" / "cut_generate_confirmed_gpt_v1.txt"
    assert sha256(original.read_bytes()).hexdigest() == "d06ff48fa1185efadbda85d2f543b2340577716e72be9b105d6e3b8e407134fd"
