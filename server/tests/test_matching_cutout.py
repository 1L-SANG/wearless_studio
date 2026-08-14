import io
from PIL import Image
from app.services import matching_cutout as mc


def _rgba(alpha_box):
    img = Image.new("RGBA", (40, 60), (0, 0, 0, 0))
    for y in range(alpha_box[1], alpha_box[3]):
        for x in range(alpha_box[0], alpha_box[2]):
            img.putpixel((x, y), (200, 40, 40, 255))
    buf = io.BytesIO(); img.save(buf, "PNG"); return buf.getvalue()


def test_flatten_fills_transparent_with_seed_grey():
    out = mc.flatten_on_bg(_rgba((10, 10, 30, 50)))
    img = Image.open(io.BytesIO(out))
    assert img.mode == "RGB", "불투명이어야 한다"
    assert img.size == (40, 60), "소스 크기 유지"
    assert img.getpixel((0, 0)) == mc.MATCHING_CUTOUT_BG, "투명부는 시드 회색"
    assert img.getpixel((20, 30)) == (200, 40, 40), "옷 픽셀은 그대로"


def test_flatten_is_deterministic():
    src = _rgba((10, 10, 30, 50))
    assert mc.flatten_on_bg(src) == mc.flatten_on_bg(src)


def test_metadata_carries_provenance():
    meta = mc.metadata_for(source_hash="abc", source_asset_id="s1",
                           matching_item_id="custom_x")
    assert meta["type"] == mc.CUTOUT_KIND == "matchingCutout"
    assert meta["algorithmVersion"] == mc.ALGORITHM_VERSION
    for k in ("sourceHash", "sourceAssetId", "matchingItemId"):
        assert meta[k] is not None
