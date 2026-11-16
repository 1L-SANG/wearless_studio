import asyncio

import pytest

from app.agents import cut_generator as cg
from app.agents import cut_output_qc as qc
from app.agents.cut_plan import compile_cut_plan
from app.agents.prompts import _product_block, build_mirrored_source_block
from app.agents.vision_llm import _QC_PRODUCT_MIRROR_RULE, qc_system_prompt
from conftest import make_settings
from test_confirmed_gpt_product_qc import _context
from test_cut_output_qc import _img, _plan, _raw


_PRODUCT = {"name": "프린트 티셔츠", "clothingType": "top", "colors": [
    {"isBase": True, "images": [{"id": "front", "slot": "Front"}]}]}
_ANALYSIS = {"fit": "regular", "materials": [{"name": "cotton", "ratio": 100}]}
_MANNEQUIN_MANIFEST = cg.build_manifest(
    [{"slot": "Front"}], has_mannequin=True, has_match=False, mood_count=0)


@pytest.mark.parametrize("cut", ["styling", "horizon"])
@pytest.mark.parametrize("manifest", [
    None,
    cg.build_manifest([{"slot": "Front"}], has_mannequin=False, has_match=False, mood_count=0),
    cg.build_manifest([{"slot": "Front"}], has_mannequin=False, has_match=False,
                      mood_count=0, outfit_roles=("bag",)),
])
def test_worn_mirror_block_is_inserted_once_immediately_after_product_context(cut, manifest):
    spec = {"cutType": cut, "shot": "full", "direction": "front"}
    baseline = cg.build_prompt(spec, _PRODUCT, analysis=_ANALYSIS, manifest=manifest)
    analysis = {**_ANALYSIS, "sourceMirrored": True}
    prompt = cg.build_prompt(spec, _PRODUCT, analysis=analysis, manifest=manifest)
    mirror = (
        build_mirrored_source_block(analysis)
        + " This rule applies only to the sold product and takes precedence over the uncorrected "
          "orientation in the PRODUCT images."
    )

    assert prompt.count(mirror) == 1
    assert prompt.count("MIRRORED SOURCE PHOTOS:") == 1
    assert _product_block(_PRODUCT, analysis) + "\n\n" + mirror in prompt
    assert prompt.replace("\n\n" + mirror, "", 1) == baseline


@pytest.mark.parametrize("cut", ["styling", "horizon"])
@pytest.mark.parametrize("value", [False, None, "true", 1, 0])
def test_nontrue_mirror_values_leave_worn_prompt_identical(cut, value):
    spec = {"cutType": cut, "shot": "full"}
    baseline = cg.build_prompt(spec, _PRODUCT, analysis=_ANALYSIS)
    assert cg.build_prompt(spec, _PRODUCT, analysis={**_ANALYSIS, "sourceMirrored": value}) == baseline
    assert cg.build_prompt(spec, _PRODUCT, analysis=dict(_ANALYSIS)) == baseline


@pytest.mark.parametrize("spec,kwargs", [
    ({"cutType": "styling"}, {"manifest": _MANNEQUIN_MANIFEST}),
    ({"cutType": "horizon"}, {"manifest": _MANNEQUIN_MANIFEST}),
    ({"cutType": "product", "shot": "ghost"}, {}),
    ({"cutType": "product", "shot": "detail"}, {}),
    ({"cutType": "styling"}, {"face_pass": True}),
    ({"cutType": "horizon"}, {"face_pass": True}),
])
def test_mannequin_product_and_face_pass_prompts_remain_identical(spec, kwargs):
    baseline = cg.build_prompt(spec, _PRODUCT, analysis=_ANALYSIS, **kwargs)
    prompt = cg.build_prompt(spec, _PRODUCT, analysis={**_ANALYSIS, "sourceMirrored": True}, **kwargs)
    assert prompt == baseline
    assert "MIRRORED SOURCE PHOTOS:" not in prompt


@pytest.mark.parametrize("source_mirrored,roles,accepted", [
    (True, ("product",), True),
    (True, ("product", "modelFace", "modelBody", "example", "matching"), True),
    (False, ("product",), False),
    (None, ("product",), False),
    ("true", ("product",), False),
    (1, ("product",), False),
    (True, ("product", "mannequin"), False),
    (True, ("mannequin",), False),
    (True, ("example",), False),
    (True, (), False),
])
def test_generic_qc_grants_mirror_authority_only_after_server_reference_checks(
    monkeypatch, source_mirrored, roles, accepted,
):
    captured = {}

    async def analyze(settings, prompt, images, schema, *, system_prompt, **kwargs):
        captured.update(policy=system_prompt, prompt=prompt, images=images)
        return _raw(), "fake"

    monkeypatch.setattr(qc, "analyze_with_fallback", analyze)
    refs = [qc.LabeledReference(role, _img(role.encode())) for role in roles]
    result = asyncio.run(qc.verdict(
        make_settings(), _plan(), refs, _img(b"OUTPUT"), source_mirrored=source_mirrored))
    context = _context(captured["policy"])
    assert context["scope"] == "explicit_fields_only"
    assert ("sourceMirrored" in result["contract"]) is accepted
    assert ("sourceMirrored" in context) is accepted
    assert ("genericProductMirrorAuthority" in context) is accepted
    assert (_QC_PRODUCT_MIRROR_RULE in captured["policy"]) is accepted
    assert context["attributeOwners"]["color"] == "storyboard"
    assert "confirmedGptProductAuthority" not in context
    assert [image.data for image in captured["images"]] == [
        *(role.encode() for role in roles), b"OUTPUT"]
    if accepted:
        assert result["contract"]["sourceMirrored"] is True
        assert context["sourceMirrored"] is True
        assert context["genericProductMirrorAuthority"] is True
        assert '"sourceMirrored":true' in captured["prompt"]
        assert captured["policy"].count(_QC_PRODUCT_MIRROR_RULE) == 1


def test_generic_qc_keeps_product_cut_policy_without_mirror_authority(monkeypatch):
    prompts = []

    async def analyze(settings, prompt, images, schema, *, system_prompt, **kwargs):
        prompts.append((prompt, system_prompt))
        return _raw(), "fake"

    monkeypatch.setattr(qc, "analyze_with_fallback", analyze)
    spec = cg.normalize_spec({"cutType": "product", "shot": "ghost", "productPresentation": "ghost"},
                             clothing_type="top")
    plan = compile_cut_plan(cg.apply_reference_compatibility(spec), "top")
    refs = [qc.LabeledReference("product", _img(b"PRODUCT"))]
    for mirrored in (False, True):
        result = asyncio.run(qc.verdict(
            make_settings(), plan, refs, _img(b"OUTPUT"), source_mirrored=mirrored))
        assert "sourceMirrored" not in result["contract"]

    assert prompts[0] == prompts[1]


@pytest.mark.parametrize("profile,marker,source_mirrored", [
    ("generic_v1", True, "true"),
    ("generic_v1", True, 1),
    ("generic_v1", True, False),
    ("generic_v1", "true", True),
    ("generic_v1", 1, True),
    ("generic_v1", False, True),
    ("generic_v1", {"sourceMirrored": True}, True),
    ("confirmed_gpt_v1", True, True),
])
def test_generic_mirror_marker_and_flag_require_exact_booleans_and_generic_profile(
    profile, marker, source_mirrored,
):
    policy = qc_system_prompt("cut", {
        "authorityProfile": profile,
        "attributeOwners": {"color": "storyboard"},
        "referenceAllowedAttributes": [],
        "genericProductMirrorAuthority": marker,
        "sourceMirrored": source_mirrored,
    })
    context = _context(policy)
    assert context["scope"] == "incomplete_no_additional_authority"
    assert "sourceMirrored" not in context
    assert "genericProductMirrorAuthority" not in context
    assert _QC_PRODUCT_MIRROR_RULE not in policy


def test_plan_cannot_supply_its_own_generic_mirror_authority(monkeypatch):
    captured = {}

    async def analyze(settings, prompt, images, schema, *, system_prompt, **kwargs):
        captured["policy"] = system_prompt
        return _raw(), "fake"

    monkeypatch.setattr(qc, "analyze_with_fallback", analyze)
    plan = _plan(sourceMirrored=True, genericProductMirrorAuthority=True)
    result = asyncio.run(qc.verdict(
        make_settings(), plan, [qc.LabeledReference("product", _img())], _img(b"OUTPUT")))
    assert "sourceMirrored" not in result["contract"]
    assert "genericProductMirrorAuthority" not in result["contract"]
    assert "sourceMirrored" not in _context(captured["policy"])
    assert _QC_PRODUCT_MIRROR_RULE not in captured["policy"]
