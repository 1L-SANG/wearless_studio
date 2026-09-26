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
    assert set(got) == {"phash", "dhash", "crops"}
    assert got["crops"] == []            # 가로 사진엔 세로 크롭 변형이 없다


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


def _jpeg_img(im, q=80, w=None):
    if w:
        im = im.resize((w, round(im.height * w / im.width)), Image.LANCZOS)
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=q)
    return Image.open(io.BytesIO(buf.getvalue()))


def _cut_rows(im, oid="o1"):
    out = []
    for i, fp in enumerate(F.cut_fingerprints(im)):
        out.append({"id": f"{oid}-{i}", "publication_id": None, "output_record_id": oid,
                    "kind": fp["kind"], "region_y0": fp.get("region_y0"),
                    "region_y1": fp.get("region_y1"), "phash": fp["phash"], "dhash": fp["dhash"]})
    return out


def test_cut_crops_cover_square_and_portrait_thumbnails_in_860_coords():
    """쇼핑몰 대표 이미지는 정사각·3:4·4:5 로 잘린다(2026-09-27 실측: 컷 전체 지문만으론 정사각 0/22)."""
    im = procedural_photo(848, 1264, 7)
    fps = F.cut_fingerprints(im)
    assert fps[0]["kind"] == "cut" and "region_y0" not in fps[0]
    crops = [f for f in fps if f["kind"] == "cut_crop"]
    h = round(1264 * 860 / 848)
    spans = [(c["region_y0"], c["region_y1"]) for c in crops]
    assert (0, 860) in spans                                  # 위 정사각
    assert (h - 860) // 2 in [y0 for y0, _ in spans]          # 가운데 정사각
    assert all(0 <= y0 < y1 <= h for y0, y1 in spans)
    assert {y1 - y0 for y0, y1 in spans} >= {860, round(860 * 4 / 3), round(860 * 5 / 4)}
    assert F.cut_fingerprints(procedural_photo(900, 900, 8))[1:] == []   # 정사각 원본엔 크롭 없음


@pytest.mark.parametrize("frac", [0.0, 0.25, 0.5])
def test_square_thumbnail_of_a_cut_is_found(frac):
    im = procedural_photo(848, 1264, 11)
    s = im.width
    y = round((im.height - s) * frac)
    thumb = _jpeg_img(im.crop((0, y, s, y + s)), 80, 720)
    rows = _cut_rows(im) + _cut_rows(procedural_photo(848, 1264, 12), "o2")
    hits = F.search(F.query_fingerprints(thumb), rows)
    assert hits and hits[0]["row"]["output_record_id"] == "o1"
    assert all(m["row"]["output_record_id"] == "o1" for m in hits)


def test_white_padded_square_thumbnail_is_trimmed_before_matching():
    im = procedural_photo(848, 1264, 13)
    pad = Image.new("RGB", (1264, 1264), (255, 255, 255))
    pad.paste(im.convert("RGB"), ((1264 - 848) // 2, 0))
    queries = F.query_fingerprints(_jpeg_img(pad, 80, 720))
    assert [q["kind"] for q in queries].count("whole") == 2   # 원본 + 여백 걷어낸 것
    hits = F.search(queries, _cut_rows(im))
    assert hits and hits[0]["row"]["kind"] == "cut"


def test_trim_leaves_full_bleed_images_alone():
    im = procedural_photo(860, 1100, 14)
    assert [q["kind"] for q in F.query_fingerprints(im)].count("whole") == 1
