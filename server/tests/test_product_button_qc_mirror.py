"""Count-only inventories corroborate on an independent mirrored pixel view."""
import asyncio
from copy import deepcopy
from io import BytesIO

import pytest
from PIL import Image

from app.agents import cut_generator as cg, product_button_qc as qc, vision_llm
from conftest import make_settings
from test_product_button_qc import Provider, spec

def source_photo():
    buffer = BytesIO()
    pixels = bytes(value for y in range(640) for x in range(320)
                   for value in (x % 256, y % 256, (x + y) % 256))
    Image.frombytes("RGB", (320, 640), pixels).save(buffer, "PNG")
    from app.agents.gemini_image import InlineImage
    return InlineImage("image/png", buffer.getvalue())



def inventory(count=3, certainty="clear", partial=0):
    return {"certainty": certainty, "direction": "front", "closure_layout": "single_row" if count else "none",
            "counts": {"closure": count, "cuffs": 0, "pockets": 0, "shoulders": 0, "other": 0},
            "buttons": [{"region": "closure", "visibility": "partial" if i < partial else "full"}
                        for i in range(count)]}


def test_partial_buttons_count_without_fabricated_position_fields():
    assert qc.validate(inventory(7, partial=1))["counts"]["closure"] == 7


@pytest.mark.parametrize("mutate", [
    lambda raw: raw["counts"].update(closure=4),
    lambda raw: raw["counts"].update(closure=True),
    lambda raw: raw["buttons"][0].update(visibility="hidden"),
    lambda raw: raw["buttons"][0].update(x=.5),
    lambda raw: raw["buttons"][0].update(region="invented"),
    lambda raw: raw["counts"].pop("cuffs"),
])
def test_count_list_contract_rejects_malformed_measurements(mutate):
    raw = inventory(); mutate(raw)
    with pytest.raises(qc.ProductButtonQCError): qc.validate(raw)


def test_mirror_changes_only_pixel_order():
    source = source_photo()
    flipped = qc.mirror_image(source)
    with Image.open(BytesIO(source.data)) as before, Image.open(BytesIO(flipped.data)) as after:
        assert after.size == before.size
        assert after.getpixel((0, 0)) == before.getpixel((319, 0))
        assert after.getpixel((319, 639)) == before.getpixel((0, 639))


def test_count_only_inspection_uses_calibrated_high_effort_budget(monkeypatch):
    calls = []
    async def call(*args, **kwargs):
        calls.append((args, kwargs)); return inventory()
    monkeypatch.setattr(vision_llm, "_call_gpt", call)
    assert asyncio.run(qc.inspect(make_settings(), source_photo()))["counts"]["closure"] == 3
    assert calls[0][1]["reasoning_effort"] == "high"
    assert calls[0][1]["max_completion_tokens"] == 4500
    assert len(calls[0][0][3]) == 1


@pytest.mark.parametrize("second,reason", [
    (inventory(8), "product_button_source_disagreement"),
    (inventory(7, certainty="uncertain"), "product_button_source_uncertain"),
])
def test_source_full_mirror_disagreement_prevents_any_image_generation(monkeypatch, second, reason):
    readings = iter([inventory(7), second]); calls = []
    async def call(settings, model, prompt, images, schema, timeout, **kwargs):
        calls.append((prompt, images)); return deepcopy(next(readings))
    monkeypatch.setattr(vision_llm, "_call_gpt", call)
    provider = Provider([b"UNUSED"])
    with pytest.raises(qc.ProductButtonQCError, match=reason):
        asyncio.run(cg.generate(make_settings(), provider, spec(), {"clothingType": "top"},
            [source_photo()], manifest="1. PRODUCT — front view of the garment"))
    assert provider.calls == []
    assert len(calls) == 2 and all(len(images) == 1 for _, images in calls)
    assert calls[0][1][0].data != calls[1][1][0].data
    assert "closure=7" not in calls[1][0]


@pytest.mark.parametrize("second", [inventory(7, partial=1), inventory(6, certainty="uncertain")])
def test_candidate_mirror_disagreement_gets_only_one_repair(monkeypatch, second):
    from test_product_button_qc import fixture_png, preserved
    readings = iter([inventory(6), inventory(6), inventory(6), second, inventory(6), inventory(6)])
    async def call(settings, model, prompt, images, schema, timeout, **kwargs):
        if "counts" in schema["properties"]:
            assert len(images) == 1 and "closure=6" not in prompt
            return deepcopy(next(readings))
        return preserved()
    monkeypatch.setattr(vision_llm, "_call_gpt", call)
    provider = Provider([b"BAD", b"FIXED"]); audit = {}
    result = asyncio.run(cg.generate(make_settings(), provider, spec(), {"clothingType": "top"},
        [source_photo()], manifest="1. PRODUCT — front view of the garment", product_button_qc_outcome=audit))
    assert result == (fixture_png(b"FIXED"), "image/png")
    assert len(provider.calls) == 2
    assert audit["attempts"][0]["corroboration"]["inventory"] == second


def test_repair_mirror_failure_never_returns_initial_or_repaired_bytes(monkeypatch):
    readings = iter([inventory(6), inventory(6), inventory(7), inventory(6), inventory(7)])
    async def call(*args, **kwargs): return deepcopy(next(readings))
    monkeypatch.setattr(vision_llm, "_call_gpt", call)
    provider = Provider([b"BAD", b"STILL_BAD"]); checkpoint = cg.BaseCheckpoint()
    with pytest.raises(qc.ProductButtonQCError, match="product_button_qc_failed"):
        asyncio.run(cg.generate(make_settings(), provider, spec(), {"clothingType": "top"},
            [source_photo()], manifest="1. PRODUCT — front view of the garment", base_checkpoint=checkpoint))
    assert checkpoint.image is None and len(provider.calls) == 2


def test_agreement_audit_retains_independent_mirror_hash_and_inventory(monkeypatch):
    async def call(*args, **kwargs): return inventory(3)
    monkeypatch.setattr(vision_llm, "_call_gpt", call)
    photo = source_photo()
    result = asyncio.run(qc.inventory(make_settings(), spec(), [photo],
                        "1. PRODUCT — front view of the garment"))
    assert result["sha256"] == result["corroboration"]["originalSha256"] == qc.image_hash(photo)
    assert result["corroboration"]["mirrorSha256"] == qc.image_hash(qc.mirror_image(photo))
    assert result["corroboration"]["inventory"]["counts"]["closure"] == 3
    assert result["corroboration"]["transform"] == "horizontal_flip"


def test_multiple_full_views_use_first_direction_source_without_pooling_counts(monkeypatch):
    from test_product_button_qc import image, install_observations, observed
    calls = install_observations(monkeypatch, {b"FIRST": observed(3), b"SECOND": observed(7)})
    refs = [image(b"FIRST"), image(b"SECOND")]
    result = asyncio.run(qc.inventory(make_settings(), spec(), refs,
        "1. PRODUCT — front view of the garment\n2. PRODUCT — front view of the garment"))
    assert result["index"] == 1 and result["inventory"]["counts"]["closure"] == 3
    assert all(ref.data != refs[1].data for _, images, _ in calls for ref in images)


def test_valid_cmyk_jpeg_mirrors_to_rgb_png():
    from app.agents.gemini_image import InlineImage
    buffer = BytesIO()
    original = Image.new("CMYK", (4, 2))
    original.putdata([(255, 0, 0, 0), (0, 255, 0, 0), (0, 0, 255, 0), (0, 0, 0, 255)] * 2)
    original.save(buffer, "JPEG", quality=100, subsampling=0)
    photo = InlineImage("image/jpeg", buffer.getvalue())
    mirrored = qc.mirror_image(photo)
    with Image.open(BytesIO(photo.data)) as decoded, Image.open(BytesIO(mirrored.data)) as after:
        expected = decoded.convert("RGB")
        assert after.format == "PNG" and after.mode == "RGB" and after.size == (4, 2)
        assert [after.getpixel((x, y)) for y in range(after.height) for x in range(after.width)] == [expected.getpixel((x, y)) for y in range(2) for x in (3, 2, 1, 0)]


def test_mirror_preserves_rgba_transparency():
    from app.agents.gemini_image import InlineImage
    buffer = BytesIO(); original = Image.new("RGBA", (3, 1))
    original.putdata([(255, 0, 0, 0), (0, 255, 0, 127), (0, 0, 255, 255)])
    original.save(buffer, "PNG")
    mirrored = qc.mirror_image(InlineImage("image/png", buffer.getvalue()))
    with Image.open(BytesIO(mirrored.data)) as after:
        assert after.mode == "RGBA"
        assert [after.getpixel((x, y)) for y in range(after.height) for x in range(after.width)] == [(0, 0, 255, 255), (0, 255, 0, 127), (255, 0, 0, 0)]


def test_rgb_calibration_mirror_bytes_remain_identical():
    source = source_photo(); expected = BytesIO()
    with Image.open(BytesIO(source.data)) as original:
        original.transpose(Image.Transpose.FLIP_LEFT_RIGHT).save(expected, "PNG")
    assert qc.mirror_image(source).data == expected.getvalue()


def test_mirror_applies_exif_orientation_before_horizontal_flip():
    from app.agents.gemini_image import InlineImage
    original = Image.new("RGB", (4, 2))
    original.putdata([(x * 70, y * 120, 30) for y in range(2) for x in range(4)])
    exif = Image.Exif(); exif[274] = 6
    buffer = BytesIO(); original.save(buffer, "JPEG", exif=exif, quality=100, subsampling=0)
    photo = InlineImage("image/jpeg", buffer.getvalue())
    mirrored = qc.mirror_image(photo)
    with Image.open(BytesIO(photo.data)) as decoded, Image.open(BytesIO(mirrored.data)) as after:
        assert after.size == (2, 4)
        # Orientation 6 then a horizontal flip is the raw image's main-diagonal transpose.
        assert [after.getpixel((x, y)) for y in range(after.height) for x in range(after.width)] == [decoded.getpixel((y, x)) for y in range(4) for x in range(2)]
        assert after.getexif().get(274, 1) == 1
