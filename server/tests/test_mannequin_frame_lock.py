"""Canonical mannequin Frame Lock regression contracts."""

from app.agents import mannequin
from app.agents.prompts import load_prompt_template, render_mannequin_prompt
from app.workers import mannequin_job
from conftest import make_settings


def _fresh_prompt() -> str:
    settings = make_settings()
    template = load_prompt_template(settings)
    context = mannequin.prompt_context(
        clothing_type="top",
        product_count=1,
        base_gender="women",
        image_manifest=(
            "1. Base mannequin — the immutable canvas\n"
            "2. front view of the garment"
        ),
    )
    return render_mannequin_prompt(
        template,
        context,
        product={"name": "striped shirt", "clothing_type": "top"},
        analysis={"clothingType": "top", "targetGenders": ["women"]},
    )


def test_fresh_prompt_is_an_edit_contract_not_a_new_composition():
    prompt = _fresh_prompt()

    assert "brand-new" not in prompt.lower()
    assert "IMAGE 1 IS THE IMMUTABLE CANVAS" in prompt
    assert "not a new composition" in prompt.lower()
    assert "three-quarter versus frontal view" in prompt
    assert "left-versus-right body orientation" in prompt


def test_style_reference_has_no_frame_authority():
    guard = mannequin_job._STYLE_REF_GUARD

    assert "camera framing" not in guard.lower()
    assert "background tone" not in guard.lower()
    assert "lighting" not in guard.lower()
    assert "shadow" not in guard.lower()
    assert "mannequin profile" in guard.lower()


def test_style_reference_manifest_cannot_claim_studio_frame():
    manifest = mannequin_job._ref_manifest_lines(4, 1)

    assert "studio look" not in manifest.lower()
    assert "camera" not in manifest.lower()
    assert "background" not in manifest.lower()
    assert "garment rendering finish" in manifest.lower()
