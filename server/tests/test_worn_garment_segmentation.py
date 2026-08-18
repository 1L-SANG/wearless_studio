"""착장 마스크 알고리즘 계약 — Base-Diff 의 역할을 코드로 못 박는다.

이 조사에서 실패한 모든 아키텍처는 하나의 실수를 공유했다: **Base-Diff 로 SAM 을 유도한 것**
(점·상자·교집합). 그 실수는 리뷰로 막을 수 없다 — 한 줄만 바꿔도 조용히 되살아나기 때문에
테스트로 막는다.

torch 없이 도는 부분(버전 신원·키·채점·형태 정리)만 다룬다. 모델 추론은 이 파일이 아니라
16건 코퍼스 검증의 몫이다.
"""

from __future__ import annotations

import inspect
import io
import pathlib

import cv2
import numpy as np
import pytest

from sam_service import worn_garment as W
from sam_service.segmentation import ALGORITHM_VERSION as CANONICAL_ALGORITHM
from sam_service.segmentation import CUTOUT_PREFIX

SAM_DIR = pathlib.Path(__file__).resolve().parents[1] / "sam_service"


# ── 신원: 캐노니컬과 절대 섞이지 않는다 ──────────────────────────────────────

def test_editor_algorithm_identity_is_separate_from_canonical():
    assert W.ALGORITHM_VERSION == "editor-worn-garment-sam2-v3"
    assert W.ALGORITHM_VERSION != CANONICAL_ALGORITHM
    assert W.MASK_PREFIX != CUTOUT_PREFIX


def test_mask_key_encodes_the_full_cache_identity():
    key = W.mask_key("a" * 64)
    assert key.startswith(f"{W.MASK_PREFIX}/{W.ALGORITHM_VERSION}/")
    assert key.endswith(".png")
    assert CUTOUT_PREFIX not in key
    # 알고리즘이 바뀌면 다른 객체로 떨어져야 한다 — 옛 규칙으로 만든 마스크를 재사용하지 않는다.
    assert W.mask_key("a" * 64, algorithm_version="v1") != key


def test_validated_generation_parameters_are_pinned():
    """16×16·IoU 0.70·중복제거 0.90·M2M — 검증이 통과한 바로 그 설정."""
    assert W.GRID == 16
    assert W.MIN_IOU_SCORE == 0.7
    assert W.DEDUPE_IOU == 0.90
    assert W.M2M is True


def test_canonical_module_is_not_modified_by_this_capability():
    """캐노니컬 파일이 이 기능을 전혀 모른다는 것이 회귀 없음의 가장 강한 증거다."""
    src = (SAM_DIR / "segmentation.py").read_text(encoding="utf-8")
    for token in ("worn_garment", "editor-worn-garment", "input_masks"):
        assert token not in src, f"segmentation.py 가 {token} 를 알고 있으면 안 된다"


# ── Base-Diff 의 역할 (§3) ───────────────────────────────────────────────────

def test_base_diff_never_prompts_sam():
    """후보 생성 함수는 차분·카테고리·상품을 인자로도 받지 않는다."""
    sig = inspect.signature(W.generate_candidates)
    assert set(sig.parameters) == {"segmenter", "rgb", "grid"}
    src = inspect.getsource(W.generate_candidates)
    for token in ("diff", "evidence", "category", "input_boxes"):
        assert token not in src, f"후보 생성에 {token} 가 들어가면 유도 프롬프트다"


def test_base_diff_is_never_intersected_with_the_final_mask():
    """`mask & diff` 는 저대비 밑단을 잘라낸다 — 이전 실험이 그렇게 죽었다."""
    src = (SAM_DIR / "worn_garment.py").read_text(encoding="utf-8")
    body = src[src.index("def produce("):]
    for pattern in ("& evidence", "evidence &", "* evidence", "np.logical_and(mask, evidence"):
        assert pattern not in body, f"최종 마스크에 {pattern} 이 있으면 안 된다"


def test_base_diff_only_feeds_the_scorer():
    """차분이 등장하는 곳은 채점 하나뿐."""
    assert "evidence" in inspect.signature(W.score_candidate).parameters
    produce = inspect.getsource(W.produce)
    assert "evidence_mask(" in produce and "score_candidate(" in produce


def test_refinement_is_mask_to_mask_not_another_prompt():
    """M2M 의 입력은 후보 자신과 그 무게중심뿐이다."""
    src = inspect.getsource(W.refine)
    assert "input_masks" in src
    for token in ("evidence", "diff", "input_boxes"):
        assert token not in src


# ── 채점 (§11: 일반 축만) ────────────────────────────────────────────────────

def _mask(shape, box):
    m = np.zeros(shape, bool)
    y0, y1, x0, x1 = box
    m[y0:y1, x0:x1] = True
    return m


def test_a_candidate_carrying_the_evidence_outranks_one_that_does_not():
    shape = (100, 60)
    figure = np.ones(shape, np.uint8)
    evidence = _mask(shape, (10, 50, 15, 45)).astype(np.uint8)
    garment = _mask(shape, (10, 50, 15, 45))
    legs = _mask(shape, (60, 99, 20, 40))
    a = W.score_candidate(garment, evidence, figure, "top")
    b = W.score_candidate(legs, evidence, figure, "top")
    assert a["score"] > b["score"]
    assert a["evidence"] == pytest.approx(1.0)


def test_category_forbidden_band_penalises_an_impossible_candidate():
    """상의가 종아리를 덮을 수는 없다 — 카테고리는 상품 메타에서만 온다."""
    shape = (100, 60)
    figure = np.ones(shape, np.uint8)
    evidence = _mask(shape, (10, 50, 15, 45)).astype(np.uint8)
    whole_body = _mask(shape, (10, 99, 15, 45))
    top = W.score_candidate(whole_body, evidence, figure, "top")
    dress = W.score_candidate(whole_body, evidence, figure, "dress")
    assert top["forbidden"] > 0
    assert dress["forbidden"] == 0, "원피스는 하체까지 내려올 수 있다"
    assert dress["score"] > top["score"]


def test_background_spill_is_penalised():
    shape = (100, 60)
    figure = _mask(shape, (10, 90, 20, 40)).astype(np.uint8)
    evidence = _mask(shape, (10, 50, 20, 40)).astype(np.uint8)
    clean = _mask(shape, (10, 50, 20, 40))
    spilled = _mask(shape, (10, 50, 0, 60))
    assert W.score_candidate(clean, evidence, figure, "top")["outsideFigure"] == 0
    assert W.score_candidate(spilled, evidence, figure, "top")["outsideFigure"] > 0


def test_category_mapping_comes_from_metadata_and_falls_back_safely():
    assert W.category_of("outer") == "outer"
    assert W.category_of("DRESS") == "dress"
    assert W.category_of(None) == "top"
    assert W.category_of("nonsense") == "top"


# ── 출력 (§12) ───────────────────────────────────────────────────────────────

def test_mask_png_is_lossless_binary_and_source_sized():
    mask = _mask((40, 30), (5, 20, 5, 25))
    png = W.encode_mask_png(mask)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    from PIL import Image
    import io
    img = Image.open(io.BytesIO(png))
    assert img.mode == "L" and img.size == (30, 40)
    values = set(np.array(img).ravel().tolist())
    assert values <= {0, 255}, "0/255 이외의 값이 나오면 손실 압축이거나 의도치 않은 안티에일리어싱"


def test_tidy_drops_specks_but_keeps_the_garment():
    m = _mask((100, 60), (10, 50, 15, 45))
    m[95, 1] = True                                  # 다리 위의 점 하나
    tidied = W.tidy(m)
    assert tidied[95, 1] == False  # noqa: E712
    assert tidied[30, 30] == True  # noqa: E712


def test_tidy_fills_interior_holes_only():
    m = _mask((100, 60), (10, 50, 15, 45))
    m[25:30, 25:30] = False                          # 옷 안의 구멍
    tidied = W.tidy(m)
    assert tidied[27, 27] == True  # noqa: E712
    assert tidied[5, 5] == False, "바깥으로 자라면 안 된다"  # noqa: E712


def test_produce_is_deterministic_for_identical_input():
    """같은 입력이면 같은 캐시 키 — 재시도가 중복 추론이 되지 않는다."""
    data = b"same-bytes"
    assert W.source_fingerprint(data) == W.source_fingerprint(b"same-bytes")
    assert W.mask_key(W.source_fingerprint(data)) == W.mask_key(W.source_fingerprint(data))
    assert W.source_fingerprint(b"other") != W.source_fingerprint(data)


# ── 코디 의류(매칭 의류)는 조정 대상이 아니다 ─────────────────────────────────
#
# 마네킹컷은 주상품과 코디 의류를 함께 입는다. 파는 옷은 주상품 하나뿐이라, 에디터 마스크가
# 코디 옷에 앉으면 셀러는 구매자가 살 수 없는 색을 발행한다. Base-Diff 는 둘을 구분할 수
# 없으므로(둘 다 "베이스 이후 생긴 것") 코디 쪽은 상품 메타로 들어와 **채점과 거부**에만 쓴다.

def test_matching_band_belongs_to_the_coordinating_garment_only():
    assert W.matching_core_band("top", "bottom") == W.MATCHING_CORE["bottom"]
    assert W.matching_core_band("outer", "bottom") == W.MATCHING_CORE["bottom"]
    assert W.matching_core_band("bottom", "top") == W.MATCHING_CORE["top"]


@pytest.mark.parametrize("clothing_type,side", [
    ("top", None),          # 코디 없이 주상품만 입은 컷
    ("top", ""),
    ("dress", "bottom"),    # 원피스는 종아리까지 — 코디만의 밴드가 성립하지 않는다
    ("top", "top"),         # 잘못 태깅된 커스텀 업로드: 주상품과 같은 쪽
    ("bottom", "bottom"),
    ("nonsense", "bottom"),  # 알 수 없는 종류는 top 으로 떨어지지만 밴드는 여전히 성립
])
def test_matching_band_is_empty_when_the_geometry_cannot_separate(clothing_type, side):
    band = W.matching_core_band(clothing_type, side)
    if clothing_type == "nonsense" and side == "bottom":
        assert band == W.MATCHING_CORE["bottom"], "알 수 없는 종류는 상의로 취급된다"
    else:
        assert band == (), "가를 수 없으면 v1 과 똑같이 판단해야 한다"


def test_matching_band_never_overlaps_what_the_product_can_reach():
    """밴드가 주상품이 닿을 수 있는 구간을 물면 정상 후보가 벌점을 받는다."""
    assert W.MATCHING_CORE["bottom"][0] >= W.CATEGORY_ZONE["top"][1] - 0.10
    assert W.MATCHING_CORE["top"][1] <= W.CATEGORY_ZONE["bottom"][0]


def test_evidence_roi_excludes_the_matching_garment():
    """증거가 가장 무거운 축이다 — 코디 옷이 ROI 에 남으면 두 벌을 함께 덮은 후보가 이긴다."""
    assert W.diff_roi("top", ()) == W.DIFF_ROI["top"]
    assert W.diff_roi("top", W.MATCHING_CORE["bottom"]) == (W.DIFF_ROI["top"][0], 0.60)
    # 하의 상품: 코디 상의 밴드는 이미 ROI 밖이라 그대로다.
    assert W.diff_roi("bottom", W.MATCHING_CORE["top"]) == W.DIFF_ROI["bottom"]


def test_scoring_is_byte_identical_to_v1_without_a_matching_garment():
    shape = (100, 60)
    figure = np.ones(shape, np.uint8)
    evidence = _mask(shape, (10, 50, 15, 45)).astype(np.uint8)
    garment = _mask(shape, (10, 50, 15, 45))
    assert (W.score_candidate(garment, evidence, figure, "top")
            == W.score_candidate(garment, evidence, figure, "top", ()))
    assert W.score_candidate(garment, evidence, figure, "top")["matchZone"] == 0


def test_the_coordinating_bottom_loses_to_the_product_top():
    shape = (100, 60)
    figure = np.ones(shape, np.uint8)
    evidence = _mask(shape, (10, 55, 15, 45)).astype(np.uint8)
    band = W.matching_core_band("top", "bottom")
    product = _mask(shape, (12, 58, 15, 45))          # 상의: 허리 위에서 끝난다
    coordination = _mask(shape, (62, 96, 20, 40))     # 코디 바지
    both = _mask(shape, (12, 96, 15, 45))             # 두 벌을 한 덩어리로 덮은 후보

    good = W.score_candidate(product, evidence, figure, "top", band)
    pants = W.score_candidate(coordination, evidence, figure, "top", band)
    outfit = W.score_candidate(both, evidence, figure, "top", band)

    assert good["matchZone"] == 0
    assert pants["matchZone"] > 0.9 and outfit["matchZone"] > 0.2
    assert good["score"] > pants["score"]
    assert good["score"] > outfit["score"]
    # 밴드가 실제로 벌점을 준다 — v1 채점과 비교해 두 후보 모두 점수가 내려간다.
    assert pants["score"] < W.score_candidate(coordination, evidence, figure, "top")["score"]
    assert outfit["score"] < W.score_candidate(both, evidence, figure, "top")["score"]


def test_a_long_top_dipping_below_the_waist_is_not_thrown_away():
    """밴드는 교집합이 아니라 벌점이다 — 밑단이 조금 내려온 오버사이즈 상의는 살아남는다."""
    shape = (100, 60)
    figure = np.ones(shape, np.uint8)
    evidence = _mask(shape, (10, 58, 15, 45)).astype(np.uint8)
    band = W.matching_core_band("top", "bottom")
    long_top = _mask(shape, (12, 64, 15, 45))         # 0.64 까지 내려온 밑단
    coordination = _mask(shape, (62, 96, 20, 40))
    assert 0 < W.score_candidate(long_top, evidence, figure, "top", band)["matchZone"] \
        <= W.MATCH_ZONE_MAX
    assert (W.score_candidate(long_top, evidence, figure, "top", band)["score"]
            > W.score_candidate(coordination, evidence, figure, "top", band)["score"])


# ── produce() 전체 경로: 모델만 대역, 나머지는 실제 코드 ──────────────────────
#
# 채점은 선호일 뿐이다. 실제로 내주는 마스크가 주상품인지는 M2M·정리까지 지난 **최종 마스크**로
# 판정해야 하고, 그 판정은 produce 안에 있다. torch 없이 그 경로를 도는 방법은 후보 생성과
# 정련만 대역으로 두는 것이다 — diff_map·evidence·figure·채점·거부는 전부 진짜로 돈다.

def _cut_bytes(shape, top_box=None, bottom_box=None):
    """흰 배경 + 회색 마네킹 + (있으면) 상의·하의 색면으로 만든 합성 컷 PNG."""
    h, w = shape
    img = np.full((h, w, 3), 245, np.uint8)                    # 배경
    img[int(h * .05):int(h * .98), int(w * .30):int(w * .70)] = 200   # 마네킹 몸통
    if top_box:
        y0, y1, x0, x1 = top_box
        img[y0:y1, x0:x1] = (40, 90, 200)
    if bottom_box:
        y0, y1, x0, x1 = bottom_box
        img[y0:y1, x0:x1] = (30, 30, 30)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


def _produce_with(monkeypatch, candidates, **kwargs):
    shape = (200, 120)
    base = _cut_bytes(shape)
    dressed = _cut_bytes(shape, top_box=(24, 116, 36, 84), bottom_box=(124, 192, 42, 78))
    monkeypatch.setattr(W, "generate_candidates", lambda *_a, **_k: list(candidates))
    monkeypatch.setattr(W, "refine", lambda _seg, _rgb, mask: mask)
    return W.produce(object(), dressed, base, clothing_type="top", **kwargs)


def test_produce_refuses_a_mask_that_sits_on_the_coordinating_garment(monkeypatch):
    """코디 바지밖에 후보가 없으면 마스크를 내주지 않는다 — 톤 에디터만 그 컷에서 닫힌다."""
    shape = (200, 120)
    pants_only = [_mask(shape, (124, 192, 42, 78))]
    with pytest.raises(W.NoGarmentCandidate) as exc:
        _produce_with(monkeypatch, pants_only, matching_side="bottom")
    assert "matching garment" in str(exc.value)


def test_produce_picks_the_product_over_the_coordinating_garment(monkeypatch):
    shape = (200, 120)
    product = _mask(shape, (24, 116, 36, 84))
    pants = _mask(shape, (124, 192, 42, 78))
    out = _produce_with(monkeypatch, [pants, product], matching_side="bottom")
    assert out.matching_side == "bottom"
    assert out.match_share <= W.MATCH_ZONE_MAX
    from PIL import Image
    chosen = np.array(Image.open(io.BytesIO(out.png)).convert("L")) > 127
    assert chosen[60, 60] and not chosen[160, 60], "상의는 잡고 바지는 두어야 한다"


def test_produce_without_a_coordinating_garment_keeps_v1_behaviour(monkeypatch):
    """코디가 없으면 밴드도 없고 거부도 없다 — 바지밖에 없는 컷도 그대로 내준다(v1 동작)."""
    shape = (200, 120)
    pants_only = [_mask(shape, (124, 192, 42, 78))]
    out = _produce_with(monkeypatch, pants_only)
    assert out.match_share == 0.0 and out.matching_side is None

# ── 거부는 포기가 아니다: 다음 수단으로 넘어간다 ─────────────────────────────
#
# 2026-08-18 실서버 사고. 코디 하의를 함께 입은 컷 3장이 연달아
# `no_garment_candidate`("selected mask sits on the matching garment: 0.63~0.73")로 끝나
# 셀러 화면이 "이 컷은 색감 조정을 지원하지 않아요"가 됐다. 거부 자체는 옳다 — 파는 옷이
# 아닌 픽셀을 물들이면 안 된다. 잘못은 **거부한 뒤 아무것도 더 시도하지 않은 것**이다.

SHAPE = (200, 120)
PRODUCT_BOX = (24, 116, 36, 84)
COORD_BOX = (124, 192, 42, 78)


def _produce_trying(monkeypatch, candidates, *, refine_to=None, **kwargs):
    """`_produce_with` 와 같되 M2M 정제의 결과를 지정할 수 있다(정제가 마스크를 키우는 상황)."""
    base = _cut_bytes(SHAPE)
    dressed = _cut_bytes(SHAPE, top_box=PRODUCT_BOX, bottom_box=COORD_BOX)
    monkeypatch.setattr(W, "generate_candidates", lambda *_a, **_k: list(candidates))
    monkeypatch.setattr(W, "refine",
                        lambda _seg, _rgb, mask: (mask if refine_to is None else refine_to))
    return W.produce(object(), dressed, base, clothing_type="top", **kwargs)


def _served(out):
    from PIL import Image
    return np.array(Image.open(io.BytesIO(out.png)).convert("L")) > 127


def test_refinement_that_spills_onto_the_coordinating_garment_falls_back_to_the_candidate(
        monkeypatch):
    """정제가 마스크를 바지까지 키우면 정제 **전** 마스크를 쓴다 — 컷을 통째로 버리지 않는다."""
    product = _mask(SHAPE, PRODUCT_BOX)
    spilled = product | _mask(SHAPE, COORD_BOX)

    out = _produce_trying(monkeypatch, [product], refine_to=spilled, matching_side="bottom")

    assert out.match_share <= W.MATCH_ZONE_MAX
    chosen = _served(out)
    assert chosen[60, 60] and not chosen[160, 60], "상의는 잡고 바지는 두어야 한다"


def test_a_vetoed_winner_hands_over_to_the_next_plausible_candidate(monkeypatch):
    """1등이 상하의 한 덩어리라 거부되면 2등(허술하지만 상의인) 후보를 쓴다.

    SAM 은 착장 전체를 한 덩어리로 내놓을 때가 잦고, 그 덩어리는 증거를 전부 품어서 점수가
    높다. 1등이 거부됐다는 이유로 그 컷에서 색감 조정을 통째로 닫을 이유는 없다.
    """
    outfit = _mask(SHAPE, PRODUCT_BOX) | _mask(SHAPE, COORD_BOX)
    sloppy_top = _mask(SHAPE, (70, 116, 4, 116))          # 배경까지 번진 상의 — 점수는 낮다

    out = _produce_trying(monkeypatch, [outfit, sloppy_top], matching_side="bottom")

    assert out.match_share <= W.MATCH_ZONE_MAX
    chosen = _served(out)
    assert chosen[100, 60] and not chosen[160, 60], "상의 후보로 내려와야 한다"
