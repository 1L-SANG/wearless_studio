import asyncio
import hashlib
from types import SimpleNamespace

import pytest

from app.agents import cut_generator as cg
from app.agents.gemini_image import InlineImage
from conftest import make_settings


PRODUCT = {
    "name": "Outer", "clothingType": "outer",
    "colors": [{"id": "base", "isBase": True,
                "images": [{"slot": "Front", "id": "front"}]}],
}
DEFAULT_INNER = (
    "Dress the model in exactly one plain, solid white crew-neck T-shirt underneath."
)


@pytest.mark.parametrize("cut_type", ["styling", "horizon"])
@pytest.mark.parametrize("state,visibility", [
    ("closed", "The inner garment must be almost entirely hidden, with at most its collar\n"
               "  line naturally visible."),
    ("openToChest", "The inner garment shows only as a small V below the neckline."),
    ("partial", "The inner garment is visible down to mid-torso."),
    ("openToWaist", "The inner garment is visible through most of the front."),
    ("open", "The inner garment must be naturally visible through the open front."),
])
def test_outer_without_references_has_no_default_inner_and_keeps_selected_opening(cut_type, state, visibility):
    prompt = cg.build_prompt(
        {"cutType": cut_type, "direction": "front", "shot": "full", "outerClosureState": state},
        PRODUCT,
    )

    assert DEFAULT_INNER not in prompt
    assert "plain, solid white crew-neck T-shirt" not in prompt
    assert visibility in prompt
    assert "inner T-shirt from the MANNEQUIN image" not in prompt
    assert "OUTERWEAR INNER — MANNEQUIN ANCHOR" not in prompt
    assert "Never transfer an inner garment or layer from EXAMPLE REFERENCE or MOOD images." in prompt


@pytest.mark.parametrize("cut_type", ["styling", "horizon"])
def test_outer_without_mannequin_uses_selected_inner_photo(cut_type):
    images = [InlineImage("image/png", b"PRODUCT"), InlineImage("image/png", b"SELECTED INNER")]
    manifest = cg.build_manifest(
        [{"slot": "Front"}], has_mannequin=False, has_match=False,
        mood_count=0, outfit_roles=("inner",),
    )
    received = []

    class FakeGemini:
        async def generate_content_image(self, model, prompt, images, image_size, aspect_ratio):
            received.append((prompt, images))
            return SimpleNamespace(image=b"OUTER", mime="image/png")

    result = asyncio.run(cg.generate(
        make_settings(gemini_api_key="x"), FakeGemini(),
        {"cutType": cut_type, "direction": "front", "shot": "full"}, PRODUCT, images,
        manifest=manifest,
    ))

    prompt, sent_images = received[0]
    assert result == (b"OUTER", "image/png")
    assert sent_images == images
    assert manifest.splitlines()[1] == (
        "2. OUTFIT INNER — the selected actual inner garment; preserve its type, color, neckline and design"
    )
    assert "SELECTED ACTUAL OUTFIT AUTHORITY" in prompt
    assert "Preserve their actual type, shape, color, neckline and design" in prompt
    assert DEFAULT_INNER not in prompt
    assert "inner T-shirt from the MANNEQUIN image" not in prompt
    assert "The inner garment must be naturally visible through the open front." in prompt


@pytest.mark.parametrize("cut_type,with_inner,expected_hash", [
    ("styling", False, "1f100cf58642fad4afb946ae740a7f942a9f0ec8903970462ecdcadb0cf9ec20"),
    ("styling", True, "d695b9cd77499137bc08bf016a60d1f8b8299af5e0d84fd49244cd1324bfbd0d"),
    ("horizon", False, "9f59f48bbac80ce0abfe9c3b4293f8b1879dbe809e84742044e31a3603d170e5"),
    ("horizon", True, "38e785b0fca57c4016f0b2af3aacbc0c65581269d7932cce7a0c241d0545f6d0"),
])
def test_outer_with_mannequin_preserves_original_prompt_bytes(cut_type, with_inner, expected_hash):
    manifest = cg.build_manifest(
        [{"slot": "Front"}], has_mannequin=True, has_match=False,
        mood_count=0, outfit_roles=("inner",) if with_inner else (),
    )
    prompt = cg.build_prompt(
        {"cutType": cut_type, "direction": "front", "shot": "full", "outerClosureState": "open"},
        PRODUCT, manifest=manifest,
    )

    assert hashlib.sha256(prompt.encode()).hexdigest() == expected_hash


@pytest.mark.parametrize("matching_fit", [{}, {"matchingFit": None}])
def test_selected_matching_photo_needs_no_matching_fit_without_mannequin(matching_fit):
    images = [InlineImage("image/png", b"PRODUCT"), InlineImage("image/png", b"SELECTED BOTTOM")]
    manifest = cg.build_manifest(
        [{"slot": "Front"}], has_mannequin=False, has_match=True, mood_count=0,
    )
    received = []

    class FakeGemini:
        async def generate_content_image(self, model, prompt, images, image_size, aspect_ratio):
            received.append((prompt, images))
            return SimpleNamespace(image=b"OUTFIT", mime="image/png")

    result = asyncio.run(cg.generate(
        make_settings(gemini_api_key="x"), FakeGemini(),
        {"cutType": "styling", "direction": "front", "shot": "full"}, PRODUCT, images,
        manifest=manifest,
        analysis={"fitProfile": {
            "version": 2, "category": "outer", "gender": "women",
            "axes": {"fit": "regular"}, **matching_fit,
        }},
    ))

    prompt, sent_images = received[0]
    assert result == (b"OUTFIT", "image/png")
    assert sent_images == images
    assert manifest.splitlines() == [
        "1. PRODUCT — front view of the garment",
        "2. MATCHING — the user-selected coordinating garment worn in the same outfit",
    ]
    assert "MATCHING is the seller's chosen coordinating garment and must also be reproduced" in prompt
    assert "silhouette, rise, length, leg/sleeve width, hem" in prompt
    assert "- fit: regular outerwear fit with natural room for layering" in prompt
    assert "- matching " not in prompt
    assert "matchingFit" not in prompt
