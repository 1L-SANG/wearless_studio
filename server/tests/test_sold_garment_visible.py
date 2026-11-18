from hashlib import sha256

import pytest

from app.agents import cut_generator as cg
from app.agents import cut_output_qc as qc
from app.agents.confirmed_gpt_prompt import compile_confirmed_gpt_prompt
from app.agents.gemini_image import InlineImage
from test_confirmed_gpt_no_mannequin import _packet
from test_cut_output_qc import _plan


_RULE_START = "Where the sold garment meets another garment, the sold garment is never hidden:"
_UNTUCKED = (
    f"{_RULE_START} wear it outside the bottom with its whole hem visible, not tucked in. "
    "Leaving it out never changes its length; the hem stays where the PRODUCT references put it. "
    "A declared fit value that says otherwise takes precedence."
)
_VISIBLE_WAIST = (
    f"{_RULE_START} its waistband and front construction stay fully visible. "
    "The coordinating top may be tucked in or end above the waistband, but must not cover them."
)
_CATEGORIES = [
    pytest.param("top", _UNTUCKED, id="top"),
    pytest.param("outer", _UNTUCKED, id="outer"),
    pytest.param("bottom", _VISIBLE_WAIST, id="bottom"),
    pytest.param("dress", None, id="dress"),
    pytest.param(None, None, id="unknown"),
]
_LENGTH_ANALYSIS = {"fitProfile": {"category": "top", "gender": "women", "version": 2,
                                    "axes": {"length": "crop"}, "axisSources": {"length": "seller"}}}


def _generic(clothing_type, cut, shot="full", *, mannequin=False, face_pass=False, analysis=None):
    product = {"name": "Sold garment"}
    if clothing_type is not None:
        product["clothingType"] = clothing_type
    manifest = cg.build_manifest(
        [{"slot": "Front"}], has_mannequin=mannequin, has_match=False, mood_count=0,
    )
    return cg.build_prompt({"cutType": cut, "shot": shot, "direction": "front"}, product,
                           analysis=analysis, manifest=manifest, face_pass=face_pass)


def _visibility_paragraphs(prompt):
    return [paragraph for paragraph in prompt.split("\n\n") if paragraph.startswith(_RULE_START)]


@pytest.mark.parametrize("cut", ["styling", "horizon"])
@pytest.mark.parametrize("clothing_type, expected", _CATEGORIES)
def test_generic_visibility_uses_sold_product_type_without_fit_profile(clothing_type, expected, cut):
    rendered = _generic(clothing_type, cut)
    assert _visibility_paragraphs(rendered) == ([expected] if expected else [])


@pytest.mark.parametrize("clothing_type, expected", _CATEGORIES)
def test_precise_visibility_uses_packet_product_type_without_fit_profile(monkeypatch, clothing_type, expected):
    packet = _packet(monkeypatch, clothing_type=clothing_type)
    rendered = compile_confirmed_gpt_prompt(packet.prompt_input)
    assert _visibility_paragraphs(rendered) == ([expected] if expected else [])
    if expected:
        assert f"{expected}\n\nPRODUCT AND MODEL LOCK:" in rendered


def test_generic_visibility_immediately_follows_fit_block():
    rendered = _generic("top", "styling", analysis=_LENGTH_ANALYSIS)
    fit_block, visibility = rendered.split("\n\nLENGTH (seller-confirmed;", 1)[1].split("\n\n", 1)
    assert fit_block
    assert visibility.split("\n\n", 1)[0] == _UNTUCKED


def test_precise_visibility_is_between_fit_block_and_product_lock(monkeypatch):
    packet = _packet(monkeypatch, analysis=_LENGTH_ANALYSIS)
    rendered = compile_confirmed_gpt_prompt(packet.prompt_input)
    fit_and_visibility = rendered.split("\n\nLENGTH (seller-confirmed;", 1)[1]
    assert f"\n\n{_UNTUCKED}\n\nPRODUCT AND MODEL LOCK:" in fit_and_visibility


def test_generic_visibility_does_not_infer_product_type_from_fit_profile():
    assert _visibility_paragraphs(_generic(None, "styling", analysis=_LENGTH_ANALYSIS)) == []


def test_precise_visibility_does_not_infer_product_type_from_fit_profile(monkeypatch):
    packet = _packet(monkeypatch, clothing_type=None, analysis=_LENGTH_ANALYSIS)
    assert _visibility_paragraphs(compile_confirmed_gpt_prompt(packet.prompt_input)) == []


# These prompt hashes were captured on d5996430 with these same inputs.
@pytest.mark.parametrize("clothing_type, cut, shot, options, expected_hash", [
    pytest.param("top", "styling", "full", {"mannequin": True},
                 "a7b9e6778c013bd42dda9a0df0cf6857a909de1a57c93bba53fc3ee114d96ab6", id="mannequin"),
    pytest.param("top", "product", "ghost", {},
                 "9b9bada6afdd7e305597071af4062f58d64f436c5bea54b7b960593bdba3706f", id="ghost"),
    pytest.param("top", "product", "detail", {},
                 "52a7210c67e9b163029932768f0aad128cbac8f0308b25d9906a1b04c616edf6", id="detail"),
    pytest.param("dress", "styling", "full", {},
                 "0565c98c79f968c2751403c59dd5458f96c83e6b145c5a02db5194dd52aad94a", id="dress"),
])
def test_generic_excluded_prompts_match_baseline(clothing_type, cut, shot, options, expected_hash):
    assert sha256(_generic(clothing_type, cut, shot, **options).encode()).hexdigest() == expected_hash


@pytest.mark.parametrize("clothing_type, mannequin, expected_hash", [
    pytest.param("top", InlineImage("image/png", b"mannequin"),
                 "8316f225ee43b196a674b384d4c9ce9915b26ceffed200d3133e84806148b048", id="mannequin"),
    pytest.param("dress", None,
                 "6d133c20d32ae84d4a87bee6915bc5ab64b8ae297d144185cf0423b5145cc5e3", id="dress"),
])
def test_precise_excluded_prompts_match_baseline(monkeypatch, clothing_type, mannequin, expected_hash):
    packet = _packet(monkeypatch, clothing_type=clothing_type, mannequin=mannequin)
    assert sha256(compile_confirmed_gpt_prompt(packet.prompt_input).encode()).hexdigest() == expected_hash


@pytest.mark.parametrize("cut", ["styling", "horizon"])
def test_generic_face_pass_cut_keeps_the_sold_garment_block(cut):
    # face_pass 는 뒤에 얼굴 패스가 붙는 실존 모델 컷의 1차 생성 지시문이다. 옷 규칙은 똑같이 나가야 한다.
    assert "the sold garment is never hidden" in _generic("top", cut, face_pass=True)


_QC_UNTUCKED = (
    "For gate 11, when the sold garment's hem is in frame, the sold garment tucked into the bottom, "
    "or lengthened beyond PRODUCT evidence, FAILS unless a declared or record-only fit axis covers it."
)
_QC_VISIBLE_WAIST = (
    "For gate 11, a coordinating top tucked into the sold bottom is acceptable; "
    "a top that covers the sold bottom's waistband or front construction FAILS."
)


def _qc_prompt(authority, recipe, clothing_type, roles):
    contract = qc.normalize_plan(_plan(recipeFamily=recipe, clothingType=clothing_type))
    references = [qc.LabeledReference(role, InlineImage("image/png", role.encode())) for role in roles]
    return qc.build_prompt(contract, references, authority_profile=authority)


@pytest.mark.parametrize("authority", ["generic_v1", "confirmed_gpt_v1"])
@pytest.mark.parametrize("recipe", ["styling", "horizon"])
@pytest.mark.parametrize("clothing_type, expected", [
    pytest.param("top", _QC_UNTUCKED, id="top"),
    pytest.param("outer", _QC_UNTUCKED, id="outer"),
    pytest.param("bottom", _QC_VISIBLE_WAIST, id="bottom"),
    pytest.param("dress", None, id="dress"),
    pytest.param("unknown", None, id="unknown"),
])
def test_qc_gate_11_visibility_uses_sold_product_type(authority, recipe, clothing_type, expected):
    rendered = _qc_prompt(authority, recipe, clothing_type, ("product",))
    rules = [line for line in rendered.splitlines() if line.startswith("For gate 11,")]
    assert rules == ([expected] if expected else [])


@pytest.mark.parametrize("authority", ["generic_v1", "confirmed_gpt_v1"])
@pytest.mark.parametrize("recipe, roles", [
    pytest.param("styling", ("mannequin", "product"), id="styling-with-mannequin"),
    pytest.param("horizon", ("mannequin", "product"), id="horizon-with-mannequin"),
    pytest.param("product", ("product",), id="product-cut"),
    pytest.param("styling", ("example", "matching"), id="styling-without-product"),
    pytest.param("horizon", ("example", "matching"), id="horizon-without-product"),
])
def test_qc_gate_11_visibility_requires_a_worn_cut_and_product_without_mannequin(authority, recipe, roles):
    rendered = _qc_prompt(authority, recipe, "top", roles)
    assert "For gate 11," not in rendered
