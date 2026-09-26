"""배포본 표식(fm_publication_mark) — sign 단계가 부르는 한 함수의 계약(2026-09-26).

핵심: 어떤 입력에도 raise 하지 않고, 실패하면 **원본 바이트 그대로** + wm_status='failed'.
셀러 다운로드를 막지 않는 게 이 층의 첫 규칙이다.
"""
import io
import zipfile

from PIL import Image

from app.services import fm_publication_mark as M
from app.services import fm_watermark as W
from scripts.fm_trace_robustness import procedural_photo, synthetic_page

CODE = 0x0BADF00D


def _png(im, **kw) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "PNG", **kw)
    return buf.getvalue()


def test_png_is_marked_and_reads_back():
    page = synthetic_page(2000, seed=6, blocks=3)
    raw = _png(page.convert("RGBA"))                 # 브라우저 canvas PNG 처럼 불투명 RGBA
    res = M.mark_publication(raw, "long_png", CODE)
    assert res.wm_status == "embedded" and res.error is None
    assert res.data != raw and res.data[:8] == b"\x89PNG\r\n\x1a\n"
    out = Image.open(io.BytesIO(res.data))
    assert out.size == page.size and out.mode == "RGB"
    assert W.read_image(out).code == CODE
    kinds = {f["kind"] for f in res.fingerprints}
    assert kinds == {"publication", "strip"}


def test_icc_profile_is_preserved():
    im = procedural_photo(900, 900, 1)
    raw = _png(im, icc_profile=b"fake-icc-profile-bytes")
    res = M.mark_publication(raw, "block_png", CODE)
    assert res.wm_status == "embedded"
    assert Image.open(io.BytesIO(res.data)).info.get("icc_profile") == b"fake-icc-profile-bytes"


def test_garbage_bytes_fail_soft_with_original_bytes():
    res = M.mark_publication(b"\x89PNG-not-really", "long_png", CODE)
    assert res.wm_status == "failed"
    assert res.data == b"\x89PNG-not-really"
    assert res.fingerprints == []


def test_missing_code_keeps_original_but_still_fingerprints():
    raw = _png(procedural_photo(1000, 2000, 2))
    res = M.mark_publication(raw, "long_png", None)
    assert res.wm_status == "failed" and res.error == "no_code"
    assert res.data == raw
    assert res.fingerprints


def _zip(entries: list[tuple[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for name, data in entries:
            z.writestr(name, data)
    return buf.getvalue()


def test_zip_marks_every_png_and_keeps_names_order_and_other_files():
    a = _png(procedural_photo(1000, 900, 3))
    b = _png(procedural_photo(1000, 1300, 4))
    raw = _zip([("상세_블록01.png", a), ("readme.txt", b"hello"), ("상세_블록02.png", b)])
    res = M.mark_publication(raw, "zip", CODE)
    assert res.wm_status == "embedded"
    z = zipfile.ZipFile(io.BytesIO(res.data))
    assert [i.filename for i in z.infolist()] == ["상세_블록01.png", "readme.txt", "상세_블록02.png"]
    assert all(i.compress_type == zipfile.ZIP_STORED for i in z.infolist())
    assert z.read("readme.txt") == b"hello"
    for name in ("상세_블록01.png", "상세_블록02.png"):
        assert W.read_image(Image.open(io.BytesIO(z.read(name)))).code == CODE
    regions = [(f["region_y0"], f["region_y1"]) for f in res.fingerprints
               if f["kind"] == "publication" and f["region_y0"] is not None]
    assert len(regions) == 2 and regions[0][0] == 0 and regions[1][0] == regions[0][1]


def test_zip_with_one_broken_png_returns_the_original_zip():
    good = _png(procedural_photo(900, 900, 5))
    raw = _zip([("a.png", good), ("b.png", b"\x89PNG\r\n\x1a\nbroken")])
    res = M.mark_publication(raw, "zip", CODE)
    assert res.wm_status == "failed"
    assert res.data == raw                       # 부분 표식 금지
    assert res.fingerprints                      # 읽을 수 있던 블록 지문은 남긴다


def test_zip_without_png_is_skipped():
    raw = _zip([("a.txt", b"x")])
    res = M.mark_publication(raw, "zip", CODE)
    assert res.wm_status == "skipped" and res.data == raw


def test_zip_bomb_guard_by_declared_size(monkeypatch):
    monkeypatch.setattr(M, "_ZIP_MAX_UNCOMPRESSED", 10)
    raw = _zip([("a.png", _png(procedural_photo(300, 300, 6)))])
    res = M.mark_publication(raw, "zip", CODE)
    assert res.wm_status == "failed" and res.data == raw


def test_not_a_zip_is_failed_not_raised():
    res = M.mark_publication(b"PK\x03\x04-zip-bytes", "zip", CODE)
    assert res.wm_status == "failed" and res.data == b"PK\x03\x04-zip-bytes"
