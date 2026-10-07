"""유료 호출 없이 원단 보호 정보와 실제 생성, 편집 경계를 검증한다."""

import asyncio
from types import SimpleNamespace

import pytest

from app.agents import cut_generator as cg, garment_desquiggle as gd
from app.agents.gemini_image import InlineImage
from test_garment_desquiggle import _FakeGemini, _gen_settings


PLAIN = {"sellingPoints": ["무지", "매끈한 표면"]}
PRODUCT = {"name": "셔츠", "clothingType": "top"}
SPEC = {"cutType": "styling", "shot": "full", "direction": "front"}


@pytest.mark.parametrize("analysis", [None, {}, {"materials": [{"name": "면"}]},
                                      {"sellingPoints": ["무지 추정", "매끈한 표면"]}])
def test_missing_or_uncertain_product_info_preserves_original(monkeypatch, analysis):
    monkeypatch.setattr(gd, "apply", lambda *args: pytest.fail("uncertain garment must be untouched"))
    assert asyncio.run(cg.generate(
        _gen_settings(garment_desquiggle_mode="on"), _FakeGemini(b"RAW"), SPEC, PRODUCT, [],
        garment_protection=gd.build_protection_info(PRODUCT, analysis, []),
    )) == (b"RAW", "image/png")


@pytest.mark.parametrize("matching", [
    [None], [{"name": "팬츠"}], [{"name": "스트라이프 팬츠"}], [{"name": "코듀로이 팬츠"}],
    [{"sellingPoints": ["무지", "매끈한 표면"]}, {"name": "체크 팬츠"}],
])
def test_any_patterned_textured_or_unknown_matching_garment_skips_whole_cut(monkeypatch, matching):
    monkeypatch.setattr(gd, "apply", lambda *args: pytest.fail("matching garment must be untouched"))
    assert asyncio.run(cg.generate(
        _gen_settings(garment_desquiggle_mode="on"), _FakeGemini(b"RAW"), SPEC, PRODUCT, [],
        garment_protection=gd.build_protection_info(PRODUCT, PLAIN, matching),
    )) == (b"RAW", "image/png")


@pytest.mark.parametrize("path", ["generate", "repair", "cached_base"])
@pytest.mark.parametrize("mode", ["on", "off"])
def test_protection_reaches_filter_in_each_generator_path(monkeypatch, path, mode):
    monkeypatch.setattr(gd, "apply", lambda image, mime: (b"CLEAN", "image/png", {"status": "applied"}))
    settings = _gen_settings(garment_desquiggle_mode=mode)
    protection = gd.build_protection_info(PRODUCT, PLAIN, [])
    if path == "repair":
        result = asyncio.run(cg.repair(
            settings, _FakeGemini(b"RAW"), SPEC, PRODUCT, InlineImage("image/png", b"SOURCE"),
            qc_corrections=("framing",), garment_protection=protection))
    else:
        kwargs = {"base_checkpoint": cg.BaseCheckpoint((b"RAW", "image/png"))} if path == "cached_base" else {}
        result = asyncio.run(cg.generate(
            settings, _FakeGemini(b"PROVIDER" if path == "cached_base" else b"RAW"),
            SPEC, PRODUCT, [], garment_protection=protection, **kwargs))
    assert result == (b"CLEAN" if mode == "on" else b"RAW", "image/png")


@pytest.mark.parametrize("point", ["스트라이프", "코듀로이", "니트", "와플"])
def test_analysis_only_pattern_or_texture_overrides_plain_product_name(point):
    protection = gd.build_protection_info({"name": "무지 셔츠"},
                                         {"sellingPoints": [*PLAIN["sellingPoints"], point]}, [])
    assert gd.should_apply(_gen_settings(garment_desquiggle_mode="on"), "gpt-image-2", SPEC,
                           protection)[0] is False


def test_missing_protection_never_falls_back_to_generic_prompt_analysis(monkeypatch):
    monkeypatch.setattr(gd, "apply", lambda *args: pytest.fail("protection was not supplied"))
    assert asyncio.run(cg.generate(
        _gen_settings(garment_desquiggle_mode="on"), _FakeGemini(b"RAW"), SPEC, PRODUCT, [],
        analysis=PLAIN)) == (b"RAW", "image/png")


def test_protection_does_not_change_provider_prompt_or_base_fingerprint(monkeypatch):
    from test_confirmed_gpt_prompt import _dress_request

    prompts = []

    class Provider(_FakeGemini):
        async def generate_content_image(self, model, prompt, *args, **kwargs):
            prompts.append(prompt)
            return SimpleNamespace(image=b"RAW", mime="image/png")

    settings = _gen_settings(garment_desquiggle_mode="on")
    monkeypatch.setattr(gd, "apply", lambda image, mime: (b"CLEAN", mime, {"status": "applied"}))
    confirmed = _dress_request()
    keys = []
    outputs = []
    for protection in (gd.build_protection_info(PRODUCT, PLAIN, []), None):
        kwargs = {"confirmed_prompt_input": confirmed, "garment_protection": protection}
        keys.append(cg.base_fingerprint(settings, SPEC, PRODUCT, [], **kwargs))
        outputs.append(asyncio.run(cg.generate(settings, Provider(b"RAW"), SPEC, PRODUCT, [], **kwargs))[0])
    assert prompts[0] == prompts[1]
    assert keys[0] == keys[1]
    assert outputs == [b"CLEAN", b"RAW"]


@pytest.mark.parametrize("pattern,texture,eligible", [
    ("none", "smooth", True), ("none", "unknown", False),
    ("paired blue lines", "smooth", False), ("none", "raised loops", False),
])
def test_evidenced_surface_observations_control_uncertainty(pattern, texture, eligible):
    import hashlib
    import json
    from app.agents import product_evidence_contract as pec
    from test_confirmed_gpt_runtime import _contract, _png

    contract = _contract(_png())
    contract["pattern_structure"] = {"value": pattern, "evidenceOrdinals": [1]}
    contract["surface_texture"] = {"value": texture, "evidenceOrdinals": [] if texture == "unknown" else [1]}
    unsigned = {k: v for k, v in contract.items() if k != "contractSha256"}
    contract["contractSha256"] = hashlib.sha256(json.dumps(
        unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    pec.validate_persisted(contract)
    info = gd.build_protection_info(PRODUCT, {**PLAIN, pec.PERSISTED_KEY: contract}, [])
    assert gd.should_apply(_gen_settings(garment_desquiggle_mode="on"), "gpt-image-2", SPEC, info)[0] is eligible


def test_invalid_evidence_is_unknown_even_when_text_claims_plain_smooth():
    from app.agents.product_evidence_contract import PERSISTED_KEY

    info = gd.build_protection_info(PRODUCT, {**PLAIN, PERSISTED_KEY: {"pattern_structure": {"value": "none"}}}, [])
    assert gd.should_apply(_gen_settings(garment_desquiggle_mode="on"), "gpt-image-2", SPEC, info)[0] is False


@pytest.mark.parametrize("analysis", [{"sellingPoints": 1}, {"materials": 1},
                                      {"sellingPoints": "무지", "materials": [{"name": "면"}]}])
def test_malformed_protection_source_is_unknown(analysis):
    info = gd.build_protection_info(PRODUCT, analysis, [])
    assert gd.should_apply(_gen_settings(garment_desquiggle_mode="on"), "gpt-image-2", SPEC, info)[0] is False


def test_matching_catalog_tags_are_checked_even_with_plain_name():
    info = gd.build_protection_info(PRODUCT, PLAIN, [{"name": "팬츠", "style_tags": ["stripe"]}])
    assert info.matching[0].has_pattern is True


def test_texture_only_in_analysis_materials_is_protected():
    info = gd.build_protection_info(PRODUCT, {**PLAIN, "materials": [{"name": "코듀로이"}]}, [])
    assert gd.should_apply(_gen_settings(garment_desquiggle_mode="on"), "gpt-image-2", SPEC, info) == (False, "texture")


@pytest.mark.parametrize("analysis", [
    {**PLAIN, "materials": [{"name": "체크 면", "ratio": 100}]},
    {"sellingPoints": [*PLAIN["sellingPoints"], "작은 프린트"]},
    {"sellingPoints": [*PLAIN["sellingPoints"], "미세 자수"]},
])
def test_pattern_clue_overrides_plain_claim_and_preserves_output(monkeypatch, analysis):
    monkeypatch.setattr(gd, "apply", lambda *args: (b"DAMAGED", "image/png", {"status": "applied"}))
    assert asyncio.run(cg.generate(
        _gen_settings(garment_desquiggle_mode="on"), _FakeGemini(b"RAW"), SPEC, PRODUCT, [],
        garment_protection=gd.build_protection_info(PRODUCT, analysis, []),
    )) == (b"RAW", "image/png")


@pytest.mark.parametrize("uncertain", ["무늬 불명", "질감 불명", "소재 미확인", "무지 추정",
                                      "pattern unknown", "surface texture uncertain"])
def test_uncertainty_overrides_conflicting_absence_declarations(uncertain):
    info = gd.build_protection_info(PRODUCT, {"sellingPoints": [*PLAIN["sellingPoints"], uncertain]}, [])
    assert gd.should_apply(_gen_settings(garment_desquiggle_mode="on"), "gpt-image-2", SPEC, info)[0] is False


@pytest.mark.parametrize("pattern", ["꽃무늬", "플라워", "호피", "페이즐리", "카모플라주", "추상 무늬", "floral"])
def test_common_patterns_override_plain_claims_for_product_and_matching(pattern):
    analysis = {"sellingPoints": [*PLAIN["sellingPoints"], pattern]}
    settings = _gen_settings(garment_desquiggle_mode="on")
    sold = gd.build_protection_info(PRODUCT, analysis, [])
    matched = gd.build_protection_info(PRODUCT, PLAIN, [analysis])
    assert gd.should_apply(settings, "gpt-image-2", SPEC, sold) == (False, "pattern")
    assert gd.should_apply(settings, "gpt-image-2", SPEC, matched) == (False, "matching_pattern")


def test_unspecified_matching_inventory_is_unknown():
    info = gd.build_protection_info(PRODUCT, PLAIN, None)
    assert gd.should_apply(_gen_settings(garment_desquiggle_mode="on"), "gpt-image-2", SPEC, info) == (False, "matching_unknown")
