"""Catalog alpha is flattened only at AI input boundaries, using local fake IO."""
import asyncio
import hashlib
from io import BytesIO
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from app.agents import outfit_recommender
from app.services import outfit_generation
from app.workers import mannequin_job
from conftest import FakeR2
from test_outfit_generation import app, snapshot
from test_editor_image_fallback import runtime, run as run_editor, recommendation
from test_detail_page_fallback import _run, _block, _item
from test_detail_incomplete_cuts_partial import _R2
from test_mannequin_job_cancellation import _wire_worker


def catalog_png(mode="RGBA"):
    image = Image.new(mode, (12, 16), (0, 0, 0, 0) if mode == "RGBA" else (232, 232, 230))
    image.putpixel((5, 7), (0, 0, 0, 255) if mode == "RGBA" else (0, 0, 0))
    data = BytesIO()
    image.save(data, "PNG")
    return data.getvalue()


def assert_catalog(data):
    with Image.open(BytesIO(data)) as image:
        assert image.mode == "RGB"
        assert image.size == (12, 16)
        assert image.getpixel((0, 0)) == (232, 232, 230)
        assert image.getpixel((5, 7)) == (0, 0, 0)


@pytest.mark.parametrize("mannequin_only", [False, True])
def test_outfit_refs_flatten_alpha_but_recipe_hashes_original(app, mannequin_only):
    application, _ = app
    raw = catalog_png()
    application.state.r2.get_bytes = lambda key: raw
    provenance = []
    kwargs = dict(snapshot=snapshot(inner="inner", footwear="footwear", bag="bag", accessory="accessory"),
                  clothing_type="outer", user_id="user", project_id="project", mannequin_only=mannequin_only)
    refs = asyncio.run(outfit_generation.resolve_images(application, resolved_assets=provenance, **kwargs))
    assert len(refs) == (1 if mannequin_only else 4)
    for (_, image), entry in zip(refs, provenance):
        assert image.mime == "image/png"
        assert_catalog(image.data)
        assert entry["imageSha256"] == hashlib.sha256(raw).hexdigest()
        assert entry["imageSha256"] != hashlib.sha256(image.data).hexdigest()
    replay = asyncio.run(outfit_generation.resolve_images(application, reference_assets=provenance, **kwargs))
    assert replay == refs


def test_mannequin_worker_flattens_matching_before_generation(monkeypatch):
    class Captured(BaseException):
        pass

    captured = {}

    async def candidate(**kwargs):
        captured.update(kwargs)
        raise Captured

    application, job, r2, _ = _wire_worker(monkeypatch, state={"cancelled": False}, candidate_runner=candidate)
    monkeypatch.setattr(mannequin_job.repo, "get_analysis", AsyncMock(return_value={
        "targetGenders": ["women"], "matchClothing": [{"id": "bottom", "selected": True}]}))
    monkeypatch.setattr(mannequin_job.repo, "get_matching_item_asset", AsyncMock(return_value="matching"))
    raw = catalog_png()
    r2.get_bytes = lambda key: raw
    with pytest.raises(Captured):
        asyncio.run(mannequin_job.run_mannequin_job(application, job))
    assert_catalog(captured["match_img"].data)
    assert captured["base_img"].data == raw
    assert captured["prod_imgs"][0].data == raw


@pytest.mark.parametrize("fallback", [False, True])
def test_editor_worn_matching_and_qc_receive_opaque_reference(runtime, monkeypatch, fallback):
    raw = catalog_png()
    original_read = runtime["app"].state.r2.get_bytes
    runtime["app"].state.r2.get_bytes = lambda key: raw if key == "asset-bottom" else original_read(key)
    runtime["analysis"]["matchClothing"] = [recommendation("missing"), recommendation("bottom")]
    if fallback:
        runtime["catalog_missing"].add("missing")
    qc = []

    async def verdict(settings, plan, references, output):
        qc.extend(references)
        return {"overall": "PASS"}

    from app.workers import editor_image_job
    monkeypatch.setattr(editor_image_job.cut_output_qc, "verdict", verdict)
    run_editor(runtime, {"cutType": "horizon", "matchIds": ["missing" if fallback else "bottom"]})
    assert "failure" not in runtime
    images = runtime["generations"][0]["images"]
    assert images[0] == b"product"
    assert_catalog(images[1])
    assert any(reference.image.data == images[1] for reference in qc)


@pytest.mark.parametrize("fallback", [False, True])
def test_detail_page_worn_matching_is_opaque(monkeypatch, fallback):
    r2 = _R2()
    r2.objects["k/match:bottom"] = (catalog_png(), "image/png", None)
    result = _run(monkeypatch, [_block(matchIds=["missing" if fallback else "bottom"])],
                  [_item("missing"), _item("bottom")], missing_rows=["missing"] if fallback else [], r2=r2)
    assert "failure" not in result
    images = result["generated"]["b1"]["images"]
    assert images[0] == b"PRODUCT"
    assert_catalog(images[1])


def test_recommendation_catalog_background_changes_without_changing_product_processing():
    class Images(FakeR2):
        def get_bytes(self, key):
            return catalog_png()

    request = asyncio.run(outfit_recommender.build_request(Images(), [(catalog_png(), "image/png")],
        {"bottom": [{"id": "bottom", "thumb_key": "thumb"}]}, context={"clothingType": "top"}, fixed={"slots": {}}))
    product, candidate = [Image.open(BytesIO(image.data)) for image in request["images"]]
    assert product.getpixel((0, 15)) == (255, 255, 255)
    assert all(abs(actual - expected) <= 3 for actual, expected in zip(candidate.getpixel((0, 15)), (232, 232, 230)))


@pytest.mark.parametrize("format", ["PNG", "JPEG"])
def test_non_alpha_catalog_preserves_exact_bytes_and_mime(format):
    from app.agents.catalog_image import catalog_image
    source = BytesIO()
    Image.new("RGB", (24, 32), (0, 0, 0)).save(source, format=format)
    raw = source.getvalue()
    mime = "image/png" if format == "PNG" else "image/jpeg"
    result = catalog_image(mime, raw)
    assert result.data is raw
    assert result.mime == mime
    assert hashlib.sha256(result.data).digest() == hashlib.sha256(raw).digest()


@pytest.mark.parametrize("mode", ["RGBA", "LA", "P", "RGB"])
def test_alpha_channels_and_png_transparency_preserve_black_and_canvas(mode):
    from app.agents.catalog_image import catalog_image
    image = Image.new(mode, (12, 16))
    if mode == "P":
        image.putpalette([0, 0, 0, 0, 0, 0] + [0] * 762)
        image.info["transparency"] = 0
        image.putpixel((5, 7), 1)
    elif mode == "RGB":
        image.paste((255, 255, 255), (0, 0, 12, 16))
        image.info["transparency"] = (255, 255, 255)
        image.putpixel((5, 7), (0, 0, 0))
    else:
        image.putpixel((5, 7), (0, 255) if mode == "LA" else (0, 0, 0, 255))
        image.putpixel((6, 7), (0, 128) if mode == "LA" else (0, 0, 0, 128))
    source = BytesIO()
    image.save(source, "PNG")
    result = catalog_image("image/png", source.getvalue())
    assert_catalog(result.data)
    if mode in ("RGBA", "LA"):
        assert Image.open(BytesIO(result.data)).getpixel((6, 7)) == (116, 116, 115)
