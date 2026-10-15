import asyncio
import hashlib
import io
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from app.services import fal_api, fal_cutout
from app.services.sam_client import SamUnavailable


def png(image):
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def candidate(box, *, size=(100, 100), color=(0, 0, 20)):
    a = np.zeros((size[1], size[0], 3), np.uint8)
    x1, y1, x2, y2 = box
    a[y1:y2, x1:x2] = color
    return png(Image.fromarray(a))


def test_largest_central_garment_wins_over_tiny_high_score_bystander():
    chosen = fal_cutout.select_mask([
        (candidate((2, 2, 12, 22)), 0.99),
        (candidate((25, 15, 75, 90)), 0.7),
    ], np.full((100, 100, 3), 100, np.uint8))
    assert chosen[50, 50] and not chosen[5, 5]


def test_center_gate_fallback_score_tie_and_rejection():
    chosen = fal_cutout.select_mask([
        (candidate((0, 0, 30, 100)), 0.99),
        (candidate((40, 40, 60, 60)), 0.6),
    ], np.full((100, 100, 3), 100, np.uint8))
    assert chosen[50, 50] and not chosen[50, 10]
    chosen = fal_cutout.select_mask([
        (candidate((0, 10, 20, 80)), 0.7),
        (candidate((80, 10, 100, 78)), 0.9),
    ], np.full((100, 100, 3), 100, np.uint8))
    assert chosen[50, 90] and not chosen[50, 10]
    with pytest.raises(fal_cutout.NoGarmentMask):
        fal_cutout.select_mask([(candidate((20, 20, 80, 80)), 0.49),
                                (candidate((0, 0, 10, 20)), 0.99)],
                               np.full((100, 100, 3), 100, np.uint8))


def test_color_mask_fills_black_holes_closes_gaps_and_prunes_debris():
    a = np.zeros((100, 100, 3), np.uint8)
    a[20:80, 20:80, 2] = 1
    a[35:65, 35:65] = 0
    a[20:23, 45:48] = 0
    a[5:8, 5:8, 0] = 10
    mask = fal_cutout.select_mask([(png(Image.fromarray(a)), 0.9)], a)
    assert mask[50, 50] and mask[21, 46] and not mask[6, 6]
    assert mask[25, 25], "one nonzero channel is foreground"


def test_mask_resolution_is_aligned_and_cutout_keeps_source_pixels_and_inward_edge():
    source = Image.new("RGB", (200, 100), (12, 34, 56))
    mask = fal_cutout.select_mask([(candidate((25, 25, 75, 75)), 0.9)], np.array(source))
    image = Image.open(io.BytesIO(fal_cutout.to_cutout_png(source, mask)))
    a = np.array(image)
    assert image.mode == "RGBA" and image.size == (200, 100)
    assert tuple(a[50, 100]) == (12, 34, 56, 255)
    assert a[50, 49, 3] == 0 and 0 < a[50, 50, 3] < 255


def test_closing_a_thin_crack_also_recovers_the_black_interior():
    a = np.zeros((100, 100, 3), np.uint8)
    a[20:80, 20:80] = 20
    a[30:70, 30:70] = 0
    a[20:31, 49:51] = 0
    mask = fal_cutout.select_mask([(png(Image.fromarray(a)), .9)], a)
    assert mask[50, 50]


def test_large_bright_strap_loop_remains_open():
    a = np.zeros((200, 200, 3), np.uint8)
    a[20:180, 20:180] = 100
    a[40:100, 60:140] = 0
    source_rgb = np.full((200, 200, 3), 200, np.uint8)
    mask = fal_cutout.select_mask([(png(Image.fromarray(a)), .9)], source_rgb)
    assert not mask[40:100, 60:140].any()
    assert mask[120:160, 60:140].all()


def test_small_bright_hole_is_filled():
    a = np.zeros((200, 200, 3), np.uint8)
    a[20:180, 20:180] = 100
    a[60:68, 60:68] = 0
    source_rgb = np.full((200, 200, 3), 200, np.uint8)
    mask = fal_cutout.select_mask([(png(Image.fromarray(a)), .9)], source_rgb)
    assert mask[60:68, 60:68].all()
    assert not mask[0, 0]


def test_bright_hole_at_exactly_half_percent_is_not_filled():
    a = np.zeros((240, 240, 3), np.uint8)
    a[20:120, 20:221] = 100  # 20,100 pixels minus the 100-pixel hole = 20,000.
    a[60:70, 60:70] = 0
    source_rgb = np.full((240, 240, 3), 200, np.uint8)
    mask = fal_cutout.select_mask([(png(Image.fromarray(a)), .9)], source_rgb)
    assert not mask[60:70, 60:70].any()


@pytest.mark.parametrize("color,filled", [((0, 0, 0), True), ((30, 20, 10), True),
                                         ((31, 0, 0), False)])
def test_large_hole_uses_original_max_channel_median(color, filled):
    a = np.zeros((200, 200, 3), np.uint8)
    a[20:180, 20:180] = 100
    a[60:140, 60:140] = 0
    source_rgb = np.full((200, 200, 3), 200, np.uint8)
    source_rgb[60:140, 60:140] = color
    source_rgb[60:80, 60:140] = 255  # Bright outliers must not replace the median.
    mask = fal_cutout.select_mask([(png(Image.fromarray(a)), .9)], source_rgb)
    assert np.all(mask[60:140, 60:140] == filled)
    assert mask[30, 30] and not mask[0, 0]


def test_dark_background_connected_to_border_is_not_filled():
    a = np.zeros((200, 200, 3), np.uint8)
    a[20:180, 20:180] = 100
    a[60:140, 60:140] = 0
    a[:70, 90:110] = 0
    mask = fal_cutout.select_mask([(png(Image.fromarray(a)), .9)], a)
    assert not mask[60:140, 60:140].any()
    assert not mask[:70, 90:110].any()


def test_stored_cutout_long_edge_does_not_exceed_2048():
    source = Image.new("RGB", (3000, 1500), (12, 34, 56))
    data = fal_cutout.to_cutout_png(source, np.ones((1500, 3000), bool))
    assert Image.open(io.BytesIO(data)).size == (2048, 1024)


class MemoryR2:
    def __init__(self, source):
        self.objects = {"source": source}

    def get_bytes(self, key):
        return self.objects[key]

    def head(self, key):
        return {"size": len(self.objects[key])} if key in self.objects else None

    def put_bytes(self, key, data, mime):
        assert mime in {"image/png", "image/jpeg"}
        self.objects[key] = data


def wire_fal(monkeypatch, *, fail=False):
    calls = []

    async def run(settings, model, payload):
        calls.append((model, payload))
        if fail:
            raise SamUnavailable("down")
        return {"masks": [{"url": "https://fake/m1"}, {"url": "https://fake/m2"}],
                "scores": [0.99, 0.8], "boxes": [[0, 0, 10, 20], [20, 20, 80, 80]]}

    async def fetch(url):
        return candidate((0, 0, 10, 20) if url.endswith("m1") else (20, 20, 80, 80))

    monkeypatch.setattr(fal_api, "run_model", run)
    monkeypatch.setattr(fal_api, "fetch_image", fetch)
    return calls


@pytest.mark.parametrize("mode,color,filled", [("RGB", (200, 200, 200), False),
                                             ("RGBA", (30, 20, 10, 255), True)])
def test_cutout_uses_prepared_source_pixels_for_holes(monkeypatch, mode, color, filled):
    a = np.zeros((200, 200, 3), np.uint8)
    a[20:180, 20:180] = 100
    a[60:140, 60:140] = 0

    async def run(*args):
        return {"masks": [{"url": "https://fake/mask"}], "scores": [.9]}

    async def fetch(url):
        return png(Image.fromarray(a))

    monkeypatch.setattr(fal_api, "run_model", run)
    monkeypatch.setattr(fal_api, "fetch_image", fetch)
    source = png(Image.new(mode, (200, 200), color))
    data = asyncio.run(fal_cutout.cutout_png(SimpleNamespace(fal_key="test"), source))
    alpha = np.array(Image.open(io.BytesIO(data)).getchannel("A"))
    assert alpha[100, 100] == (255 if filled else 0)


def test_fal_persists_sam_result_contract_and_cache_skips_paid_call(monkeypatch):
    calls = wire_fal(monkeypatch)
    source = png(Image.new("RGB", (100, 100), (30, 40, 50)))
    r2 = MemoryR2(source)
    settings = SimpleNamespace(fal_key="test")
    result = asyncio.run(fal_cutout.segment_garment(settings, r2, {"Front": "source"}))["Front"]
    digest = hashlib.sha256(source).hexdigest()
    assert result.ready and result.source_hash == digest
    assert result.cutout_key == (
        f"derived/canonical-cutout/fal-sam3-v1/fal-ai_sam-3_image/Front/{digest}.png")
    assert result.model_version == "fal-ai/sam-3/image"
    assert result.algorithm_version == "fal-sam3-v1"
    assert result.checksum == hashlib.sha256(r2.objects[result.cutout_key]).hexdigest()
    assert result.byte_size == len(r2.objects[result.cutout_key])
    assert (result.width, result.height) == (100, 100)
    assert calls[0][1] == {"image_url": calls[0][1]["image_url"], "prompt": "clothing",
                           "return_multiple_masks": True, "max_masks": 3,
                           "include_scores": True, "include_boxes": True,
                           "output_format": "png"}
    cached = asyncio.run(fal_cutout.segment_garment(settings, r2, {"Front": "source"}))["Front"]
    assert cached.ready and cached.cached and len(calls) == 1


def test_empty_candidates_fail_per_view_and_http_failure_remains_retryable(monkeypatch):
    r2 = MemoryR2(png(Image.new("RGB", (100, 100))))

    async def empty(*args):
        return {"masks": [], "scores": []}

    monkeypatch.setattr(fal_api, "run_model", empty)
    result = asyncio.run(fal_cutout.segment_garment(SimpleNamespace(fal_key="test"), r2,
                                                  {"Front": "source"}))["Front"]
    assert not result.ready and result.code == "no_garment_mask"
    wire_fal(monkeypatch, fail=True)
    result = asyncio.run(fal_cutout.segment_garment(SimpleNamespace(fal_key="test"), r2,
                                                  {"Front": "source"}))["Front"]
    assert not result.ready and result.code == "unavailable"
