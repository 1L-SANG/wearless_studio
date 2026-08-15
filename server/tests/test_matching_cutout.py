import io
import os
import uuid

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


# 리뷰 I4 — 파생 asset 이 기존 정리 경로가 아는 purpose·sourceAssetIds 를 실어야
# "내 옷 삭제" 가 원본 업로드·원본 grid 까지 회수한다.
def test_metadata_carries_cleanup_handles():
    grid = mc.metadata_for(source_hash="h", source_asset_id="old-grid",
                           matching_item_id="custom_x",
                           source_asset_ids=["up-1", "up-2", "old-grid"])
    assert grid["purpose"] == mc.GRID_PURPOSE == "custom_match_grid"
    assert grid["sourceAssetIds"] == ["up-1", "up-2", "old-grid"]

    thumb = mc.metadata_for(source_hash="h", source_asset_id="up-1",
                            matching_item_id="custom_x", purpose=mc.CUTOUT_PURPOSE)
    assert thumb["purpose"] == "custom_match_cutout"
    assert "sourceAssetIds" not in thumb


# 리뷰 I6 — 130px 카드에 2048px 무압축 PNG 를 내려보내지 않는다.
def test_thumbnail_is_a_small_jpeg():
    flat = mc.flatten_on_bg(_rgba((10, 10, 30, 50)))
    # 실사진처럼 압축이 안 먹는 노이즈 — 단색 PNG 로는 크기 비교가 무의미하다.
    big = Image.frombytes("RGB", (1024, 1024), os.urandom(1024 * 1024 * 3))
    buf = io.BytesIO(); big.save(buf, "PNG", optimize=False); big_png = buf.getvalue()

    thumb = mc.encode_thumbnail(big_png)
    img = Image.open(io.BytesIO(thumb))
    assert img.format == "JPEG"
    assert max(img.size) == mc.THUMBNAIL_MAX_PX <= 512
    assert len(thumb) < len(big_png) / 10, "카드용으로 한 자릿수 배 이상 작아야 한다"
    assert len(thumb) < 300_000, "130px 카드 한 장에 실어도 되는 절대 크기"
    # 소스가 이미 작으면 확대하지 않는다
    assert Image.open(io.BytesIO(mc.encode_thumbnail(flat))).size == (40, 60)
    assert mc.encode_thumbnail(big_png) == thumb, "결정론"


# 리뷰 I5 — 스테일 리스 회수로 재실행돼도 같은 asset id 로 수렴해야 R2 객체·asset 행이
# 한 벌 더 쌓이지 않는다.
def test_derived_identity_is_deterministic_per_source_and_version():
    first = mc.derived_asset_id(role="grid", matching_item_id="custom_x", source_hash="h1")
    assert first == mc.derived_asset_id(role="grid", matching_item_id="custom_x",
                                        source_hash="h1")
    assert first != mc.derived_asset_id(role="thumb", matching_item_id="custom_x",
                                        source_hash="h1")
    assert first != mc.derived_asset_id(role="grid", matching_item_id="custom_y",
                                        source_hash="h1")
    assert first != mc.derived_asset_id(role="grid", matching_item_id="custom_x",
                                        source_hash="h2")
    assert uuid.UUID(first).version == 5


def test_source_fingerprint_is_stable_and_order_sensitive():
    assert mc.source_fingerprint(["a", "b"]) == mc.source_fingerprint(["a", "b"])
    assert mc.source_fingerprint(["a", "b"]) != mc.source_fingerprint(["b", "a"])
    assert mc.source_fingerprint([None]) == mc.source_fingerprint([""])  # 해시 결측 허용


# ── 누끼 색 일치 게이트 (2026-08-15 — SAM 이 옷 대신 배경 조각을 딴 실사고) ──────

def _rgb_png(color, size=(60, 80)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def _flat_png(fg_color, size=(60, 80), fg_box=(15, 20, 45, 60)):
    im = Image.new("RGB", size, mc.MATCHING_CUTOUT_BG)
    if fg_color is not None:
        from PIL import ImageDraw
        ImageDraw.Draw(im).rectangle(fg_box, fill=fg_color)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _photo_png(bg, garment, size=(120, 160)):
    """배경 위에 옷이 놓인 원본 사진 — 테두리는 배경, 중앙은 옷."""
    from PIL import ImageDraw
    im = Image.new("RGB", size, bg)
    ImageDraw.Draw(im).rectangle((size[0] // 4, size[1] // 4,
                                  size[0] * 3 // 4, size[1] * 3 // 4), fill=garment)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def test_color_gate_rejects_background_grab():
    """흰 문 배경 + 검정 데님인데 누끼 전경이 흰색 — 배경을 딴 실사고 재현."""
    ok, m = mc.cutout_color_agrees(
        _photo_png((246, 246, 244), (24, 26, 30)), _flat_png((246, 246, 244)))
    assert ok is False
    assert m["delta"] < mc.CUTOUT_BG_DELTA_MIN


def test_color_gate_accepts_small_garment_on_large_background():
    """옷이 프레임을 못 채워도 통과해야 한다 — 중앙 평균 기준이던 시절의 오탐(리뷰 확정)."""
    tiny = Image.new("RGB", (200, 200), (245, 245, 245))
    from PIL import ImageDraw
    ImageDraw.Draw(tiny).rectangle((85, 85, 115, 115), fill=(20, 30, 90))  # 2.25% 면적
    buf = io.BytesIO(); tiny.save(buf, "PNG")
    ok, m = mc.cutout_color_agrees(buf.getvalue(), _flat_png((20, 30, 90)))
    assert ok is True, m


def test_color_gate_accepts_same_garment_and_lighting_shift():
    photo = _photo_png((246, 246, 244), (24, 26, 30))
    assert mc.cutout_color_agrees(photo, _flat_png((24, 26, 30)))[0] is True
    assert mc.cutout_color_agrees(photo, _flat_png((54, 56, 60)))[0] is True  # 조명 +30


def test_color_gate_rejects_empty_foreground():
    ok, m = mc.cutout_color_agrees(_photo_png((246, 246, 244), (24, 26, 30)), _flat_png(None))
    assert ok is False and m["reason"] == "no_foreground"


def test_color_gate_fails_open_on_undecodable_input():
    """게이트 자체가 새 실패원이 되면 안 된다 — 판정 불가는 통과."""
    ok, m = mc.cutout_color_agrees(b"not-an-image", _flat_png((10, 10, 10)))
    assert ok is True and m["reason"] == "gate_error_fail_open"
