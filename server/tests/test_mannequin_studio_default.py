import pytest

from app.agents.gemini_image import InlineImage
from app.config import Settings, load_settings
from app.agents import prompts
from app.workers import mannequin_job
from conftest import make_settings
from test_mannequin_photo_reference import capture_candidate


@pytest.mark.parametrize("gender", ["men", "women"])
@pytest.mark.parametrize("with_style_reference", [False, True])
def test_fresh_request_uses_the_same_base_led_studio_for_both_genders(
    monkeypatch, gender, with_style_reference,
):
    refs = [InlineImage("image/png", b"unrelated-white-studio")] if with_style_reference else None
    call = capture_candidate(
        monkeypatch,
        profile={"category": "top", "gender": gender, "axes": {"fit": "regular"}, "version": 1},
        style_refs=refs,
    )
    text = call["prompt"]
    assert "STUDIO APPEARANCE AUTHORITY" in text
    assert "light neutral-grey" in text
    assert "off-white resin" in text
    assert "soft floor contact shadow" in text
    assert call["images"][0].data == b"base"
    assert [image.data for image in call["images"][1:3]] == [b"Front", b"Back"]
    assert len(call["images"]) == 3 + int(with_style_reference)
    if with_style_reference:
        assert call["images"][-1].data == b"unrelated-white-studio"
        assert "must never override the base" in text


@pytest.mark.parametrize("gender", ["men", "women"])
def test_existing_cut_fit_edits_preserve_their_current_studio(monkeypatch, gender):
    call = capture_candidate(
        monkeypatch,
        generation_path="edit",
        parent=InlineImage("image/png", b"existing-cut"),
        profile={"category": "top", "gender": gender, "axes": {"fit": "regular"}, "version": 1},
    )
    assert call["images"][0].data == b"existing-cut"
    assert "STUDIO APPEARANCE AUTHORITY" not in call["prompt"]
    assert "plain studio background, lighting" in call["prompt"]


def test_studio_policy_has_a_new_default_draft_fingerprint_version(monkeypatch):
    monkeypatch.delenv("MANNEQUIN_PROMPT_VERSION", raising=False)
    monkeypatch.setenv("APP_ENV", "dev")
    assert Settings.__dataclass_fields__["mannequin_prompt_version"].default == "v1-studio-a"
    assert load_settings().mannequin_prompt_version == "v1-studio-a"


@pytest.mark.parametrize("gender", ["men", "women"])
def test_fresh_fit_fallback_keeps_a_visual_reference_to_the_previous_studio(monkeypatch, gender):
    call = capture_candidate(
        monkeypatch,
        profile={"category": "top", "gender": gender, "axes": {"fit": "regular"}, "version": 1},
        preservation_before=InlineImage("image/png", b"previous-white-studio"),
    )
    assert [image.data for image in call["images"]] == [b"base", b"Front", b"Back", b"previous-white-studio"]
    assert "4. BEFORE STUDIO" in call["prompt"]
    assert "overrides the shared studio default" in call["prompt"]
    assert "not garment identity or fit evidence" in call["prompt"]


def test_reused_draft_keeps_the_selected_cut_as_its_studio_authority(monkeypatch):
    call = capture_candidate(
        monkeypatch, generation_path="edit",
        parent=InlineImage("image/png", b"paid-draft"),
        preservation_before=InlineImage("image/png", b"selected-studio"),
        match_image=InlineImage("image/png", b"matching-garment"),
    )
    assert [image.data for image in call["images"]] == [
        b"paid-draft", b"Front", b"Back", b"matching-garment", b"selected-studio",
    ]
    assert "5. BEFORE STUDIO" in call["prompt"]
    assert "overrides the shared studio default and the editing canvas" in call["prompt"]
    assert "if attached, the last image" not in call["prompt"]
    assert "not garment identity or fit evidence" in call["prompt"]


@pytest.mark.parametrize("with_style_reference", [False, True])
def test_canonical_retry_retains_the_previous_studio_reference_and_policy(with_style_reference):
    raw = [InlineImage("image/png", data) for data in (b"base", b"front", b"back")]
    manifest = "1. Base\n2. Front\n3. Back"
    if with_style_reference:
        raw.append(InlineImage("image/png", b"other-studio"))
        manifest += "\n4. STYLE REFERENCE"
    raw.append(InlineImage("image/png", b"previous-studio"))
    manifest += f"\n{len(raw)}. BEFORE STUDIO"
    canonical = type("Canonical", (), {"image": InlineImage("image/png", b"canonical")})()
    policy = prompts.build_studio_preservation_block()
    result, verdict = mannequin_job._maybe_augment_with_canonical(
        product={"name": "cardigan", "clothing_type": "outer"},
        analysis={"materials": [{"name": "cotton", "ratio": 100}]},
        canonical_refs={"CanonicalFront": canonical}, already_used=False, generation_path="fresh",
        images=raw, image_manifest=manifest,
        template=prompts.load_prompt_template(make_settings()),
        ctx_kwargs={"clothing_type": "outer", "product_count": 2, "base_gender": "men"},
        settings=make_settings(), prompt_suffix=policy,
    )
    assert verdict["augment"] is True
    images, text = result
    assert len(images) == len(raw) + 1
    assert images[0].data == b"base"
    data = [image.data for image in images]
    assert data.count(b"previous-studio") == 1 and data.count(b"canonical") == 1
    assert f"{data.index(b'previous-studio') + 1}. BEFORE STUDIO" in text
    assert text.endswith(policy)
