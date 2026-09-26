"""추적 층 지문(fm_fingerprint) — imagehash 와 같은 값인가, 띠가 페이지를 다 덮는가, 탐색이 맞는가.

되돌아가면: 저장값이 표준 pHash 와 어긋나 외부 도구로 재현이 안 되거나(호환 테스트), 페이지
바닥이 지문에서 빠지거나(띠 커버), 창 조회가 전체 지문과 섞여 헛후보가 나온다(탐색 규칙).
"""
import io

import numpy as np
import pytest
from PIL import Image

from app.services import fm_fingerprint as F
from scripts.fm_trace_robustness import procedural_photo, synthetic_page


def _images():
    ims = [procedural_photo(300 + 37 * i, 200 + 23 * i, i) for i in range(12)]
    ims.append(procedural_photo(640, 480, 99).convert("RGBA"))
    ims.append(procedural_photo(500, 700, 98).convert("P"))
    ims.append(procedural_photo(1024, 1536, 97).convert("L"))
    ims.append(Image.new("RGB", (400, 400), (255, 255, 255)))
    return ims


def test_hashes_are_bit_identical_to_imagehash():
    """prod 에는 imagehash(scipy 동반)를 안 넣었다 — 대신 값이 비트 단위로 같다는 걸 여기서 잠근다."""
    imagehash = pytest.importorskip("imagehash")
    for im in _images():
        assert F.phash(im) == int(str(imagehash.phash(im)), 16)
        assert F.dhash(im) == int(str(imagehash.dhash(im)), 16)


def test_signed_roundtrip_for_postgres_bigint():
    for v in (0, 1, (1 << 63) - 1, 1 << 63, (1 << 64) - 1):
        s = F.to_signed(v)
        assert -(1 << 63) <= s < (1 << 63)
        assert F.to_unsigned(s) == v


def test_safe_image_hashes_never_raises():
    assert F.safe_image_hashes(b"not an image") is None
    buf = io.BytesIO()
    procedural_photo(320, 240, 5).save(buf, "PNG")
    got = F.safe_image_hashes(buf.getvalue())
    assert set(got) == {"phash", "dhash"}


def test_publication_strips_cover_the_whole_page_including_bottom():
    page = synthetic_page(2000, seed=2, blocks=5)
    norm = F.normalize(page)
    fps = F.publication_fingerprints([norm])
    whole = [f for f in fps if f["kind"] == "publication"]
    strips = [f for f in fps if f["kind"] == "strip"]
    assert len(whole) == 1 and whole[0]["region_y0"] is None
    assert strips[0]["region_y0"] == 0
    assert max(f["region_y1"] for f in strips) == norm.height      # 바닥까지
    starts = sorted(f["region_y0"] for f in strips)
    assert max(b - a for a, b in zip(starts, starts[1:])) <= F.STRIP_STEP


def test_blank_strips_are_not_stored():
    blank = Image.new("L", (860, 3000), 255)
    fps = F.publication_fingerprints([blank])
    assert [f["kind"] for f in fps] == ["publication"]      # 빈 띠는 헛후보만 만든다


def test_zip_blocks_get_their_own_region_rows():
    a = F.normalize(procedural_photo(1000, 900, 1))
    b = F.normalize(procedural_photo(1000, 1200, 2))
    fps = F.publication_fingerprints([a, b])
    blocks = [f for f in fps if f["kind"] == "publication" and f["region_y0"] is not None]
    assert [(f["region_y0"], f["region_y1"]) for f in blocks] == [
        (0, a.height), (a.height, a.height + b.height)]


def _rows(fps, pub):
    return [{**f, "publication_id": pub, "output_record_id": None} for f in fps]


def test_search_finds_own_strip_and_rejects_other_page():
    page = synthetic_page(2000, seed=3, blocks=6)
    other = synthetic_page(2000, seed=17, blocks=6)
    rows = _rows(F.publication_fingerprints([F.normalize(page)]), "self")
    rows += _rows(F.publication_fingerprints([F.normalize(other)]), "other")
    small = page.resize((860, round(page.height * 860 / page.width)), Image.LANCZOS)
    buf = io.BytesIO()
    small.save(buf, "JPEG", quality=70)
    found = Image.open(io.BytesIO(buf.getvalue())).crop((0, 1337, 860, 1337 + 1000))
    matches = F.search(F.query_fingerprints(found), rows)
    assert matches, "자기 페이지 조각을 못 찾았다"
    assert matches[0]["row"]["publication_id"] == "self"
    assert matches[0]["phash_distance"] <= F.STRONG_PHASH
    assert all(m["row"]["publication_id"] == "self" for m in matches)


def test_windows_only_compare_against_strips():
    """800px 창을 컷·전체 지문과 비교하면 모양이 다른 것끼리 우연히 가까운 헛후보가 생긴다."""
    q = [{"kind": "window", "y0": 0, "y1": 800, "phash": 0x1234, "dhash": 0x1234}]
    rows = [{"kind": "cut", "phash": 0x1234, "dhash": 0x1234, "publication_id": None,
             "output_record_id": "c1"}]
    assert F.search(q, rows) == []
    q.append({"kind": "whole", "y0": 0, "y1": 900, "phash": 0x1234, "dhash": 0x1234})
    assert F.search(q, rows)[0]["row"]["output_record_id"] == "c1"


def test_search_accepts_signed_db_values_and_chunks():
    v = (1 << 63) + 12345
    rows = [{"kind": "strip", "phash": F.to_signed(v), "dhash": F.to_signed(v),
             "publication_id": f"p{i}", "output_record_id": None} for i in range(7)]
    q = [{"kind": "window", "y0": 0, "y1": 800, "phash": v ^ 0b111, "dhash": v}]
    got = F.search(q, rows, chunk=3)
    assert len(got) == 7 and {m["phash_distance"] for m in got} == {3}


def test_popcount_fallback_matches_numpy():
    x = np.array([[0, 1, (1 << 64) - 1, 0xF0F0]], dtype=np.uint64)
    b = x.view(np.uint8).reshape(*x.shape, 8)
    fallback = np.unpackbits(b, axis=-1).sum(axis=-1)
    assert fallback.tolist() == [[0, 1, 64, 8]]
    assert F._popcount(x).tolist() == [[0, 1, 64, 8]]
