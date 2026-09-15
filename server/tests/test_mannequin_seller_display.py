"""Native mannequin assets stay canonical while seller display uses a safe 2x derivative."""

from __future__ import annotations

import asyncio
from io import BytesIO
from types import SimpleNamespace

from PIL import Image, ImageDraw

from app.services import mannequin_display
from app.workers import mannequin_job
from conftest import make_settings


def _native_png(size=(1024, 1536)) -> bytes:
    image = Image.new("RGB", size, (14, 27, 40))
    draw = ImageDraw.Draw(image)
    draw.rectangle((97, 201, 681, 1097), fill=(231, 83, 57))
    draw.line((0, size[1] - 1, size[0] - 1, 0), fill=(49, 211, 133), width=9)
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def test_lanczos_derivative_has_exact_2x_pixels_and_lineage():
    native = _native_png()
    native_key = "users/u/projects/p/ai/j/asset.png"

    derivative = mannequin_display.build(native, "image/png", native_key)

    assert derivative is not None
    assert derivative.key == "users/u/projects/p/ai/j/asset.png.seller-display-2x.png"
    with Image.open(BytesIO(native)) as source, Image.open(BytesIO(derivative.data)) as actual:
        expected = source.resize((2048, 3072), Image.Resampling.LANCZOS)
        assert actual.size == (2048, 3072)
        assert actual.mode == expected.mode
        assert actual.tobytes() == expected.tobytes()
    assert derivative.metadata == {
        "algorithm": mannequin_display.ALGORITHM,
        "r2Key": derivative.key,
        "mimeType": "image/png",
        "sourceWidth": 1024,
        "sourceHeight": 1536,
        "width": 2048,
        "height": 3072,
        "byteSize": len(derivative.data),
    }


def test_only_native_1k_png_is_enlarged_once():
    assert mannequin_display.build(_native_png((2048, 3072)), "image/png", "large.png") is None
    assert mannequin_display.build(_native_png((1536, 2304)), "image/png", "legacy-2k.png") is None
    assert mannequin_display.build(_native_png(), "image/jpeg", "wrong-mime.jpg") is None


def test_display_key_resolution_is_asset_bound_and_dimension_checked():
    native_key = "users/u/projects/p/ai/j/asset.png"
    derivative = mannequin_display.build(_native_png(), "image/png", native_key)
    asset = {
        "r2_key": native_key,
        "width": 1024,
        "height": 1536,
        "metadata": {"sellerDisplay": derivative.metadata},
    }

    assert mannequin_display.resolve_key(asset) == derivative.key
    assert mannequin_display.resolve_key({
        **asset,
        "metadata": {"sellerDisplay": {**derivative.metadata, "r2Key": "users/other/asset.png"}},
    }) is None
    assert mannequin_display.resolve_key({**asset, "width": 2048, "height": 3072}) is None


class _R2:
    def __init__(self):
        self.puts = []

    def put_bytes(self, key, data, mime, cache=None):
        self.puts.append((key, data, mime, cache))


def test_save_cut_keeps_native_bytes_and_dimensions_and_adds_display_metadata(monkeypatch):
    native = _native_png()
    r2 = _R2()
    monkeypatch.setattr(mannequin_job.uuid, "uuid4", lambda: "asset-id")

    saved = asyncio.run(mannequin_job._save_cut(
        s=make_settings(r2_bucket="bucket"),
        r2=r2,
        user_id="u",
        project_id="p",
        job_id="j",
        candidate="A",
        base_fit="regular",
        res=SimpleNamespace(image=native, mime="image/png"),
        qc_scores=None,
    ))

    assert saved["key"] == "users/u/projects/p/ai/j/asset-id.png"
    assert saved["size"] == len(native)
    assert (saved["width"], saved["height"]) == (1024, 1536)
    assert r2.puts[0][0:3] == (saved["key"], native, "image/png")
    display = saved["generation_metadata"]["sellerDisplay"]
    assert r2.puts[1][0] == display["r2Key"] == (
        "users/u/projects/p/ai/j/asset-id.png.seller-display-2x.png"
    )
    assert r2.puts[1][2] == "image/png"
    assert (display["sourceWidth"], display["sourceHeight"]) == (1024, 1536)
    assert (display["width"], display["height"]) == (2048, 3072)


def test_worker_merges_saved_display_metadata_with_generation_lineage(monkeypatch):
    from test_mannequin_adjust_edit_path import _parent, _run_worker

    calls, _r2 = _run_worker(
        monkeypatch,
        parent=_parent(editDepth=1),
        candidate_metadata={"sellerDisplay": {"algorithm": mannequin_display.ALGORITHM}},
    )
    candidate = calls["success"][0]["candidates"][0]
    assert candidate["generation_metadata"]["sellerDisplay"]["algorithm"] == mannequin_display.ALGORITHM
    assert candidate["generation_metadata"]["generationPath"] == "edit"
