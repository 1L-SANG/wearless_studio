"""등록 사진의 정규화본 — **진짜 이미지 바이트**로 본다.

다른 테스트들은 `b"image"` 같은 가짜 바이트를 올리고 정규화를 스텁으로 통과시킨다(conftest).
그래서 "실제로 변환이 되는가" 를 증명하는 자리는 여기 하나뿐이다.

지키는 것:
  1. EXIF orientation 이 **픽셀에 적용**되고 태그는 남지 않는다 — PIL 은 태그를 적용하지 않고
     cv2 는 적용한다. 그대로 두면 검출(cv2)과 크롭(PIL)이 다른 그림을 본다(2026-08 '칼 사태').
  2. 산출물을 PIL 과 cv2 가 **같은 크기**로 읽는다. 하나가 못 읽으면 실패다.
  3. 원본 픽셀이 그대로다(무손실) — 학습이 보는 화질이 여기서 깎이면 안 된다.
  4. 읽을 수 없는 바이트는 NormalizeFailed — 조용히 통과시키면 학습셋에 깨진 파일이 남는다.
  5. 매직바이트로 형식을 판정한다 — iOS 는 HEIC 의 content-type 을 비워 보낸다.
"""
import io

import numpy as np
import pytest
from PIL import Image

from app.facemarket_photo_normalize import (
    NORMALIZED_MIME, NormalizeFailed, normalize_png, sniff_image_mime,
)


def _jpeg(width, height, *, orientation=None, color=(90, 120, 200)) -> bytes:
    buffer = io.BytesIO()
    image = Image.new("RGB", (width, height), color)
    # 위쪽 띠를 다른 색으로 — 회전이 실제로 픽셀에 적용됐는지 보려면 방향이 보이는 그림이어야 한다.
    image.paste((240, 40, 40), (0, 0, width, max(1, height // 4)))
    if orientation is None:
        image.save(buffer, "JPEG", quality=95)
    else:
        exif = image.getexif()
        exif[274] = orientation
        image.save(buffer, "JPEG", quality=95, exif=exif)
    return buffer.getvalue()


def _png(width, height) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (10, 200, 10)).save(buffer, "PNG")
    return buffer.getvalue()


def test_orientation_is_baked_into_the_pixels():
    png, size = normalize_png(_jpeg(600, 400, orientation=6))

    assert size == (400, 600), "태그가 픽셀에 적용돼야 한다"
    with Image.open(io.BytesIO(png)) as out:
        assert out.size == (400, 600)
        assert out.format == "PNG"
        assert out.getexif().get(274) in (None, 1), "태그가 남으면 두 번 도는 경로가 생긴다"


def test_pil_and_cv2_agree_on_the_result():
    import cv2

    png, size = normalize_png(_jpeg(320, 240))
    decoded = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)

    assert decoded is not None, "cv2 가 못 읽으면 얼굴 검출이 통째로 막힌다"
    assert (decoded.shape[1], decoded.shape[0]) == size


def test_the_rewrite_is_lossless():
    original = _png(64, 48)
    png, size = normalize_png(original)

    assert size == (64, 48)
    with Image.open(io.BytesIO(original)) as before, Image.open(io.BytesIO(png)) as after:
        assert np.array_equal(np.asarray(before.convert("RGB")), np.asarray(after))


def test_unreadable_bytes_are_refused():
    for data in (b"", b"image", b"\x00" * 64):
        with pytest.raises(NormalizeFailed):
            normalize_png(data)


def test_the_output_mime_is_png():
    assert NORMALIZED_MIME == "image/png"


# ── 형식 판정 ──────────────────────────────────────────────────────────────
def _heic_head(brand: bytes) -> bytes:
    return b"\x00\x00\x00\x18ftyp" + brand + b"\x00" * 8


@pytest.mark.parametrize("brand,expected", [
    (b"heic", "image/heic"), (b"heix", "image/heic"), (b"hevc", "image/heic"),
    (b"mif1", "image/heif"), (b"msf1", "image/heif"), (b"heif", "image/heif"),
])
def test_heic_is_recognised_by_its_brand_not_its_content_type(brand, expected):
    """iOS 는 HEIC 의 content-type 을 빈 문자열로 주기도 한다 — 확장자도 제각각(.HEIC/.hif)."""
    assert sniff_image_mime(_heic_head(brand), "") == expected
    assert sniff_image_mime(_heic_head(brand), "application/octet-stream") == expected


def test_ordinary_formats_are_recognised_too():
    assert sniff_image_mime(_jpeg(8, 8), "") == "image/jpeg"
    assert sniff_image_mime(_png(8, 8), "") == "image/png"
    assert sniff_image_mime(b"RIFF\x00\x00\x00\x00WEBPVP8 ", "") == "image/webp"


def test_an_unknown_body_falls_back_to_what_the_client_said():
    """판정 못 한 바이트까지 여기서 막지 않는다 — 화이트리스트 대조는 호출부가 한다."""
    assert sniff_image_mime(b"not an image", "image/jpeg") == "image/jpeg"
    assert sniff_image_mime(b"not an image", None) == ""


def test_a_non_heic_ftyp_box_is_not_treated_as_heic():
    """mp4(ftyp isom)도 같은 박스 구조다 — 브랜드를 안 보면 동영상이 얼굴 사진이 된다."""
    assert sniff_image_mime(_heic_head(b"isom"), "") == ""
    assert sniff_image_mime(_heic_head(b"mp42"), "video/mp4") == "video/mp4"
