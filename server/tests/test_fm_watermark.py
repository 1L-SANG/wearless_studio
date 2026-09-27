"""추적 층 워터마크(fm_watermark) — 박은 코드가 조각에서 다시 읽히는가, 안 보이는가(2026-09-26).

되돌아가면: 레이아웃이 조용히 바뀌어 이미 나간 배포본을 영영 못 읽거나(골든 해시), 워터마크가
눈에 보이거나(PSNR), 없는 곳에서 코드를 지어내거나(깨끗한 이미지) 한다.
"""
import io

import numpy as np
import pytest
from PIL import Image

from app.services import fm_watermark as W
from scripts.fm_trace_robustness import procedural_photo, psnr, synthetic_page

CODE = 0xC0FFEE42


@pytest.fixture(scope="module")
def page():
    # 5블록(글·전폭 사진·글·2열 사진·전폭 사진) — 2000×약 6000. 실제 배포본과 같은 폭.
    return synthetic_page(2000, seed=4, blocks=5)


@pytest.fixture(scope="module")
def marked(page):
    return W.embed_image(page, CODE)


def test_layout_is_pinned():
    """🔴 이 값이 바뀌면 이미 셀러에게 나간 모든 배포본의 워터마크를 못 읽는다.

    정말 바꿔야 하면 VERSION 을 올리고 판독기가 옛 레이아웃도 시도하게 만든 뒤에 이 값을 갱신한다.
    """
    assert W.layout_fingerprint() == (
        "50a84a950adfd95775bb1c9e0bdd616ca26e3fb0406616e6517cedddfb673cd2"
    )
    assert (W.CANON_W, W.TILE, W.CHIP, W.NBITS) == (860, 126, 3, 48)


def test_crc16_ccitt_false_check_value():
    assert W.crc16(b"123456789") == 0x29B1


def test_code_bits_roundtrip_and_crc_rejects_flip():
    for code in (1, 0xC0FFEE42, (1 << 32) - 1, W.new_code()):
        bits = W._code_bits(code)
        assert W._bits_code(bits) == (code, True)
        flipped = bits.copy()
        flipped[5] ^= 1
        assert W._bits_code(flipped)[1] is False


def test_new_code_is_32bit_nonzero():
    codes = {W.new_code() for _ in range(200)}
    assert all(0 < c < (1 << 32) for c in codes)
    assert len(codes) > 190


def test_code_out_of_range_is_refused():
    for bad in (0, 1 << 32, -1):
        with pytest.raises(ValueError):
            W._code_bits(bad)


def test_embed_is_invisible_by_psnr(page, marked):
    assert marked.size == page.size
    assert psnr(page, marked) >= 40.0
    diff = np.abs(np.asarray(marked, np.int16) - np.asarray(page, np.int16))
    assert diff.max() <= 3            # 질감 최대 ±3 레벨


def test_full_page_reads_back(marked):
    got = W.read_image(marked)
    assert got.code == CODE
    assert got.votes >= 2
    assert got.best_z >= W.MIN_SYNC_Z


@pytest.mark.parametrize("y_frac", [0.05, 0.37, 0.71])
def test_any_single_strip_reads_on_its_own_after_mall_resize_and_jpeg(marked, y_frac):
    """쇼핑몰 흔한 경로 — 860 폭 축소 + JPEG q70, 페이지 임의 높이의 ~1000px 조각 하나만."""
    small = marked.resize((860, round(marked.height * 860 / marked.width)), Image.LANCZOS)
    buf = io.BytesIO()
    small.save(buf, "JPEG", quality=70)
    found = Image.open(io.BytesIO(buf.getvalue()))
    y0 = int(y_frac * (found.height - 1000))
    assert W.read_image(found.crop((0, y0, 860, y0 + 1000))).code == CODE


def test_clean_image_yields_no_code(page):
    assert W.read_image(page).code is None
    assert W.read_image(procedural_photo(860, 1200, 99)).code is None


def test_embed_is_deterministic(page):
    a = np.asarray(W.embed_image(page.crop((0, 0, 2000, 1500)), CODE))
    b = np.asarray(W.embed_image(page.crop((0, 0, 2000, 1500)), CODE))
    assert np.array_equal(a, b)


def test_opaque_rgba_becomes_rgb_and_transparent_alpha_survives():
    base = procedural_photo(900, 700, 3)
    opaque = base.convert("RGBA")
    assert W.embed_image(opaque, CODE).mode == "RGB"
    translucent = base.convert("RGBA")
    alpha = Image.new("L", base.size, 255)
    alpha.paste(0, (0, 0, 100, 100))
    translucent.putalpha(alpha)
    out = W.embed_image(translucent, CODE)
    assert out.mode == "RGBA"
    assert out.getchannel("A").tobytes() == alpha.tobytes()


def test_band_processing_matches_single_pass(page, monkeypatch):
    """띠(1024행) 단위 처리가 한 번에 처리한 것과 같은 픽셀을 낸다 — 경계 마스크 여백이 원본을 본다."""
    crop = page.crop((0, 0, 2000, 2500))
    banded = np.asarray(W.embed_image(crop, CODE))
    monkeypatch.setattr(W, "_BAND_ROWS", 10_000)
    single = np.asarray(W.embed_image(crop, CODE))
    assert np.array_equal(banded, single)


def test_oversized_image_is_refused():
    class Huge:
        width, height = 20_000, 20_000
    with pytest.raises(ValueError):
        W.check_size(Huge())
