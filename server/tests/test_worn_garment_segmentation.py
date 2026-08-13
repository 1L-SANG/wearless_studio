"""착장 마스크 알고리즘 계약 — Base-Diff 의 역할을 코드로 못 박는다.

이 조사에서 실패한 모든 아키텍처는 하나의 실수를 공유했다: **Base-Diff 로 SAM 을 유도한 것**
(점·상자·교집합). 그 실수는 리뷰로 막을 수 없다 — 한 줄만 바꿔도 조용히 되살아나기 때문에
테스트로 막는다.

torch 없이 도는 부분(버전 신원·키·채점·형태 정리)만 다룬다. 모델 추론은 이 파일이 아니라
16건 코퍼스 검증의 몫이다.
"""

from __future__ import annotations

import inspect
import pathlib

import numpy as np
import pytest

from sam_service import worn_garment as W
from sam_service.segmentation import ALGORITHM_VERSION as CANONICAL_ALGORITHM
from sam_service.segmentation import CUTOUT_PREFIX

SAM_DIR = pathlib.Path(__file__).resolve().parents[1] / "sam_service"


# ── 신원: 캐노니컬과 절대 섞이지 않는다 ──────────────────────────────────────

def test_editor_algorithm_identity_is_separate_from_canonical():
    assert W.ALGORITHM_VERSION == "editor-worn-garment-sam2-v1"
    assert W.ALGORITHM_VERSION != CANONICAL_ALGORITHM
    assert W.MASK_PREFIX != CUTOUT_PREFIX


def test_mask_key_encodes_the_full_cache_identity():
    key = W.mask_key("a" * 64)
    assert key.startswith(f"{W.MASK_PREFIX}/{W.ALGORITHM_VERSION}/")
    assert key.endswith(".png")
    assert CUTOUT_PREFIX not in key
    # 알고리즘이 바뀌면 다른 객체로 떨어져야 한다 — 옛 규칙으로 만든 마스크를 재사용하지 않는다.
    assert W.mask_key("a" * 64, algorithm_version="v2") != key


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
