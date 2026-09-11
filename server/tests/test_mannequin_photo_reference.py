"""Photo-led material policy at the real mannequin provider boundary."""
import asyncio
import copy
import types

import pytest

from app.agents.gemini_image import InlineImage
from app.agents.mannequin_adjust import build_adjust_directives
from app.agents.prompts import _product_block, load_prompt_template
from app.agents.product_reference import ProductReference
from app.workers import mannequin_job
from conftest import make_settings


PROFILE = {
    "category": "top", "gender": "women", "source": "seller",
    "axes": {"fit": "slim", "length": "crop"}, "version": 1,
}


def test_photo_policy_does_not_translate_material_metadata_into_visual_claims():
    product = {"name": "제품", "clothing_type": "top"}
    analysis = {"sellingPoints": ["두 개의 주머니"], "materials": [{"name": "cotton", "ratio": 100}]}
    other = {**analysis, "materials": [{"name": "polyester", "ratio": 100}]}
    before = copy.deepcopy(analysis)

    actual = _product_block(product, analysis, material_policy="photo_evidence")
    assert actual == _product_block(product, other, material_policy="photo_evidence")
    assert actual == _product_block(product, {**analysis, "materials": []}, material_policy="photo_evidence")
    assert "cotton" not in actual and "polyester" not in actual
    assert "두 개의 주머니" in actual
    assert analysis == before  # Rendering must not erase the seller's stored composition.
    assert "cotton 100%" in _product_block(product, analysis, material_policy="legacy")


class _ProviderCaptured(Exception):
    pass


def capture_candidate(monkeypatch, *, clothing_type="top", name="제품", slots=("Front", "Back"),
                      generation_path="fresh", parent=None, profile=None, match_image=None,
                      with_product_roles=False):
    """Run real orchestration until its first provider request, with no DB/R2 writes."""
    captured = {}

    class Capture:
        async def generate_content_image(self, model, prompt, images, size, aspect_ratio=None):
            captured.update(model=model, prompt=prompt, images=images, size=size, aspect=aspect_ratio)
            raise _ProviderCaptured

    async def no_emit(*args, **kwargs):
        pass

    monkeypatch.setattr(mannequin_job, "_emit", no_emit)
    settings = make_settings()
    app = types.SimpleNamespace(state=types.SimpleNamespace(settings=settings, pool=None, r2=None, gemini=Capture()))
    images = [InlineImage("image/png", slot.encode()) for slot in slots]
    refs = [ProductReference(slot, f"asset-{slot}", image)
            for slot, image in zip(slots, images, strict=True)] if with_product_roles else None
    manifest = mannequin_job._build_manifest([{"slot": slot} for slot in slots], match_image is not None, clothing_type)
    directive_profile = profile or PROFILE
    directives = build_adjust_directives(directive_profile, tuple(directive_profile["axes"]))
    with pytest.raises(_ProviderCaptured):
        asyncio.run(mannequin_job._run_candidate(
            app=app, job={"id": "job", "user_id": "user", "project_id": "project"},
            candidate="A", base_fit="regular", base_gender=(profile or {}).get("gender", "women"),
            base_img=InlineImage("image/png", b"base"), prod_imgs=images, match_img=match_image,
            product_count=len(images), template=load_prompt_template(settings),
            product={"name": name, "clothing_type": clothing_type},
            analysis={"materials": [{"name": "cotton", "ratio": 100}], "sellingPoints": ["두 개의 주머니"]},
            clothing_type=clothing_type, image_manifest=manifest,
            fit_profile=profile, adjusted_axes=("fit", "length") if profile else (),
            generation_path=generation_path, parent_cut_img=parent, adjust_directives=directives,
            product_refs=refs,
        ))
    return captured


@pytest.mark.parametrize("generation_path,parent", [("fresh", None), ("edit", InlineImage("image/png", b"current"))])
def test_pants_matching_top_is_named_as_top_at_provider_boundary(monkeypatch, generation_path, parent):
    call = capture_candidate(monkeypatch, clothing_type="bottom", generation_path=generation_path,
                             parent=parent, match_image=InlineImage("image/png", b"white-top"),
                             profile={"category": "pants", "gender": "women", "axes": {"length": "ankle"}, "version": 1})
    assert call["images"][-1].data == b"white-top"
    assert "matching top" in call["prompt"].lower()
    manifest_line = next(line for line in call["prompt"].splitlines() if line.startswith("4."))
    assert "matching top" in manifest_line.lower()


def test_matching_top_adjustment_does_not_use_bottom_scope():
    profile = {"category": "pants", "gender": "women", "axes": {"length": "ankle"},
               "matchingFit": {"fitCategory": "top", "axes": {"length": "crop"}}}
    directives = build_adjust_directives(profile, ("length",))
    assert "MATCHING TOP" in directives
    assert "MATCHING BOTTOM" not in directives


def test_only_matching_top_change_does_not_retailor_main_pants():
    profile = {"category": "pants", "gender": "women", "axes": {"cut": "wide", "length": "below_ankle"},
               "version": 2, "matchingFit": {"clothingId": "white-top", "fitCategory": "top", "axes": {"length": "crop"}}}
    directives = build_adjust_directives(profile, ())
    assert "MATCHING TOP" in directives
    assert "MAIN PRODUCT" not in directives
    assert "cropped hem" in directives


@pytest.mark.parametrize("clothing_type,name", [("bottom", "퍼플 바지"), ("outer", "크로셰 가디건"), ("top", "검정 셔츠")])
@pytest.mark.parametrize("slots", [("Front",), ("Front", "Back"), ("Front", "Back", "Detail", "BackDetail")])
def test_live_fresh_requests_use_photos_without_composition_guidance(monkeypatch, clothing_type, name, slots):
    call = capture_candidate(monkeypatch, clothing_type=clothing_type, name=name, slots=slots)
    assert "cotton" not in call["prompt"]
    assert "Material rendering guidance" not in call["prompt"]
    assert "두 개의 주머니" in call["prompt"]
    assert [image.data for image in call["images"]] == [b"base", *(slot.encode() for slot in slots)]
    assert call["model"] == "gpt-image-2.5-flare"


def test_fresh_adjustment_fallback_keeps_declared_fit_and_photo_policy(monkeypatch):
    call = capture_candidate(monkeypatch, generation_path="edit", parent=None, profile=PROFILE)
    assert "FIT PROFILE" in call["prompt"]
    assert "- fit:" in call["prompt"] and "- length:" in call["prompt"]
    assert "cotton" not in call["prompt"]
    assert call["images"][0].data == b"base"


def test_edit_path_keeps_current_cut_and_material_identity_anchors(monkeypatch):
    call = capture_candidate(monkeypatch, generation_path="edit", parent=InlineImage("image/png", b"current"), profile=PROFILE)
    assert [im.data for im in call["images"]] == [b"current", b"Front", b"Back"]
    assert "MAIN PRODUCT" in call["prompt"]
    assert "cotton" not in call["prompt"]
    assert "color" in call["prompt"].lower() and "texture" in call["prompt"].lower()


def test_canonical_retry_keeps_photo_policy_and_raw_references():
    raw = [InlineImage("image/png", value) for value in (b"base", b"front", b"back")]
    canonical = types.SimpleNamespace(image=InlineImage("image/png", b"canonical"))
    settings = make_settings()
    result, verdict = mannequin_job._maybe_augment_with_canonical(
        product={"name": "cardigan", "clothing_type": "outer"},
        analysis={"materials": [{"name": "cotton", "ratio": 100}]},
        canonical_refs={"CanonicalFront": canonical}, already_used=False, generation_path="fresh",
        images=raw, image_manifest="1. Base\n2. Front\n3. Back", template=load_prompt_template(settings),
        ctx_kwargs={"clothing_type": "outer", "product_count": 2, "base_gender": "women", "fit_profile": PROFILE},
        settings=settings, prompt_suffix="LOOK ONLY GUARD",
    )
    assert verdict["augment"] is True
    images, prompt = result
    assert [im.data for im in images] == [b"base", b"front", b"back", b"canonical"]
    assert "cotton" not in prompt and "Material rendering guidance" not in prompt
    assert "FIT PROFILE" in prompt and prompt.endswith("LOOK ONLY GUARD")
