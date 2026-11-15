"""끝에서 옷 색 맞추기 모듈의 규칙을 합성 화소로 검증한다.

실제 사진은 쓰지 않는다. 그림은 모두 numpy 로 만든다(단색 옷, 주름을 흉내 낸 밝기 물결, 줄무늬, 두 톤,
로고 조각, 흰 라벨, 피부색 조각, 구멍, 무늬 배경). 테스트 이름의 ga, na, da, ra, ma, ba, sa 는
명세 11절의 가, 나, 다, 라, 마, 바, 사 항목이고, 주석의 "지시서 N"은 구현 지시서의 열 가지다.
"""
from __future__ import annotations

import gc
import itertools
import json
import math
import weakref

import cv2
import numpy as np
import pytest

from app.agents import garment_color_evidence
from app.services import garment_color_consistency as G
from app.services import tone_mask

W, H = 192, 256                      # 작은 그림. 길이 규칙은 폭 비례라 침식은 최소인 5화소다
BOX = (40, 216, 36, 156)             # 옷 네모 (y0, y1, x0, x1). 폭 120은 주름 주기 24의 정수배다
STUDIO = G.SHIFT_LIMITS["studio"]
STYLING = G.SHIFT_LIMITS["styling"]
SKIP_REASONS = {
    "no_candidate", "ambiguous_candidates", "ambiguous_no_upload_color", "upload_color_untrusted",
    "mask_tiny", "mask_frame_fill", "mask_thin", "mask_fragments", "mask_too_few_pixels",
    "mask_edge_spill", "mask_mixed_color", "mask_mixed_vs_siblings", "mask_edge_vs_siblings",
    "no_sibling_check", "off_siblings", "multicolor_garment", "no_reference_few_studio",
    "no_reference_studio_disagree", "no_reference_no_upload_color", "reference_off_upload",
}


# ── 합성 그림 재료 ──────────────────────────────────────────────────────────

def _rgb8(lab) -> np.ndarray:
    return np.rint(G.oklab_to_srgb(np.asarray(lab, dtype=np.float64))).astype(np.uint8)


def _background(shape=(H, W)) -> np.ndarray:
    """체크와 줄이 섞인 연두빛 배경. 윤곽 밖이 바뀌면 어디서든 드러난다."""
    y, x = np.mgrid[0:shape[0], 0:shape[1]]
    img = np.empty((*shape, 3), np.uint8)
    img[..., 0] = 150 + 40 * ((x // 8 + y // 8) % 2) + (x % 7)
    img[..., 1] = 170 + (y % 11) * 3
    img[..., 2] = 120 + 30 * ((x // 5) % 2)
    return img


def _box(box=BOX, shape=(H, W)) -> np.ndarray:
    mask = np.zeros(shape, bool)
    mask[box[0]:box[1], box[2]:box[3]] = True
    return mask


def _folds(amp, shape=(H, W), x0=BOX[2], period=24) -> np.ndarray:
    """세로 주름. 화소의 Oklab 세 좌표에 곱할 비율이고, 옷 폭이 주기의 정수배라 가운데값이 1이다."""
    x = np.arange(shape[1])
    return np.broadcast_to(1.0 + amp * np.sin(2 * np.pi * (x - x0) / period), shape)


def _paint(img, where, color, ratio=1.0) -> None:
    lab = np.asarray(color, dtype=np.float64) * np.broadcast_to(ratio, img.shape[:2])[where][:, None]
    img[where] = _rgb8(lab)


def _garment(color, *, amp=0.06, box=BOX, shape=(H, W)):
    """무늬 배경 위에 주름진 단색 옷 네모를 그린다. 돌려주는 것은 사진과 옷 윤곽."""
    img, mask = _background(shape), _box(box, shape)
    _paint(img, mask, color, _folds(amp, shape, box[2]))
    return img, mask


def _rows(period, count, shape=(H, W)) -> np.ndarray:
    """가로 줄. period 줄마다 count 줄이 참이다."""
    return np.broadcast_to(((np.arange(shape[0]) % period) < count)[:, None], shape)


def _photo(key, img, masks, kind="studio", option="c1"):
    return G.PhotoInput(key=key, image=img, masks=masks, kind=kind, color_option_id=option,
                        clothing_type="top")


def _center(img, mask) -> np.ndarray:
    return np.array(G.measure_garment_color(img, mask)["center"])


def _triplet(img, mask, kinds=("studio", "studio", "studio"), **colors):
    """같은 사진 세 장(형제 대조를 통과시키려고)을 한 묶음으로 돌린다."""
    return G.correct_color_option([_photo(f"p{i}", img, mask, kind) for i, kind in enumerate(kinds)],
                                  **colors)


def _outcomes(result) -> list[tuple]:
    return [(p.record["status"], p.record["reason"]) for p in result.photos]


def _core(mask) -> np.ndarray:
    k = max(5, round(9 * mask.shape[1] / 1536))
    return cv2.erode(mask.astype(np.uint8), np.ones((k, k), np.uint8)).astype(bool)


def _inside(mask, px) -> np.ndarray:
    return cv2.erode(mask.astype(np.uint8), np.ones((2 * px + 1, 2 * px + 1), np.uint8)).astype(bool)


def _grown(mask, px) -> np.ndarray:
    return cv2.dilate(mask.astype(np.uint8), np.ones((2 * px + 1, 2 * px + 1), np.uint8)).astype(bool)


# ── 가. 색 공간과 재기 ──────────────────────────────────────────────────────

def test_ga1_oklab_matches_production_lab_and_round_trips():
    """가.1: 변환이 운영 _lab 과 같고(1e-9 안), 갔다 돌아오면 반 단계 안이다."""
    steps = (0, 1, 5, 10, 11, 12, 64, 128, 200, 250, 254, 255)
    rgb = np.array([(r, g, b) for r in steps for g in steps for b in steps], np.uint8)
    ours = G.rgb_to_oklab(rgb)
    theirs = np.array([garment_color_evidence._lab(tuple(int(v) for v in c)) for c in rgb])
    assert np.abs(ours - theirs).max() < 1e-9
    assert np.abs(G.oklab_to_srgb(ours) - rgb).max() < 0.5
    # 가중 거리도 운영 식과 같다.
    a, b = theirs[7], theirs[900]
    assert float(G.color_distance(a, b)) == pytest.approx(garment_color_evidence._color_distance(a, b))


@pytest.mark.parametrize("color", [(0.62, 0.03, 0.05), (0.30, 0.0, -0.02), (0.88, -0.01, 0.02),
                                   (0.55, 0.15, 0.08)])
def test_ga2_center_of_a_wrinkled_plain_garment_is_the_painted_color(color):
    """가.2: 주름이 있는 단색 옷의 가운데 색이 만든 색과 0.003 안이다."""
    img, mask = _garment(color, amp=0.08)
    assert np.abs(_center(img, mask) - color).max() < 0.003


def test_ga3_center_uses_every_pixel_not_every_other_one():
    """가.3: 2화소 주기의 밝기 무늬가 있어도 가운데 밝기는 화소 전부의 가운데값이다(일정 간격 뽑기 금지)."""
    img, mask = _background(), _box()
    weave = 1.0 + 0.03 * np.where(np.arange(W) % 2 == 0, 1.0, -1.0)     # 짝수 칸은 밝고 홀수 칸은 어둡다
    _paint(img, mask, (0.62, 0.01, 0.03), _folds(0.04) * weave[None, :])
    lab = G.rgb_to_oklab(img)
    core = _core(mask)
    every = float(np.median(lab[core][:, 0]))
    every_other = float(np.median(lab[::2, ::2][core[::2, ::2]][:, 0]))
    assert abs(every - every_other) > 0.01, "두 칸마다 하나씩 뽑으면 다르게 나오는 그림이어야 한다"
    assert G.measure_garment_color(img, mask)["center"][0] == pytest.approx(every, abs=1e-12)


@pytest.mark.parametrize("dark_rows", [2, 3])
def test_ga4_second_pass_median_ignores_dark_stripes(dark_rows):
    """가.4: 진한 줄이 20%, 30%인 줄무늬에서 두 번 구한 가운데값이 바탕색에서 0.01 안이고 한 번 구한 값보다 가깝다."""
    base, stripe = (0.75, 0.01, 0.03), (0.25, 0.0, -0.03)
    img, mask = _garment(base, amp=0.08)
    _paint(img, mask & _rows(10, dark_rows), stripe, _folds(0.08))
    measure = G.measure_garment_color(img, mask)
    twice = np.abs(np.array(measure["center"]) - base).max()
    once = np.abs(np.array(measure["first_pass_center"]) - base).max()
    assert twice < 0.01
    assert twice < once


def test_ga5_blown_pixels_are_measured_not_dropped():
    """가.5: 날아간 화소가 있어도 빼지 않고 잰다(뺀 값과 다르다)."""
    color = (0.93, 0.0, 0.0)
    img, mask = _garment(color, amp=0.08)          # 주름의 밝은 쪽이 흰색으로 날아간다
    measure = G.measure_garment_color(img, mask)
    assert measure["blown_frac"] > 0.15
    core = _core(mask)
    lightness = G.rgb_to_oklab(img)[core][:, 0]
    kept = img[core].min(axis=1) < G.BLOWN_MIN
    without_blown = float(np.median(lightness[kept]))
    assert abs(measure["center"][0] - color[0]) < 0.003
    assert abs(measure["center"][0] - without_blown) > 0.01


def test_ga2_center_ignores_a_one_sided_shade():
    """가.2, 3.1절: 옷의 왼쪽 30%가 그늘이어도 가운데 색은 만든 색이다. 평균으로 재면 틀리는 그림이다."""
    color = (0.62, 0.03, 0.05)
    img, mask = _garment(color, amp=0.0)
    _paint(img, _box((40, 216, 36, 72)), color, 0.8)        # 왼쪽 36열이 0.8배 그늘
    mean = G.rgb_to_oklab(img)[_core(mask)].mean(axis=0)
    assert abs(mean[0] - color[0]) > 0.03, "평균이면 틀리는 그림이어야 한다"
    assert np.abs(_center(img, mask) - color).max() < 0.003


def _noisy_garment(color=(0.62, 0.03, 0.05)):
    """화소마다 채널별로 -6에서 6단계의 고정 씨앗 잡음을 얹은 옷. 화소를 조금만 덜어 써도 가운데값이 달라진다."""
    img, mask = _garment(color, amp=0.05)
    noise = np.random.default_rng(7).integers(-6, 7, size=img.shape)
    img[mask] = np.clip(img[mask].astype(int) + noise[mask], 0, 255).astype(np.uint8)
    return img, mask


def test_ga3_center_is_the_median_of_every_core_pixel_at_any_stride():
    """가.3, 3.2절: 옷 가운데 색은 속 화소 전부의 가운데값이다. 두 칸, 세 칸, 다섯 칸마다 뽑으면 달라지는 그림으로 본다."""
    img, mask = _noisy_garment()
    lab, core = G.rgb_to_oklab(img), _core(mask)
    every = np.median(lab[core], axis=0)
    for stride in (2, 3, 5):
        assert np.abs(np.median(lab[core][::stride], axis=0) - every).max() > 1e-6
        assert np.abs(np.median(lab[::stride, ::stride][core[::stride, ::stride]], axis=0) - every).max() > 1e-6
    measure = G.measure_garment_color(img, mask)
    assert measure["first_pass_center"] == pytest.approx(every.tolist(), abs=1e-12)
    assert measure["center"] == pytest.approx(every.tolist(), abs=1e-12), "모두 같은 천이라 두 번 구한 값도 같다"


def test_ga3_quality_numbers_and_candidate_colors_use_every_core_pixel():
    """3.2절, 2.4절, 2.2절 4번: 품질 숫자와 후보의 색도 속 화소 전부로 구한다. 일정 간격으로 뽑지 않는다."""
    img, mask = _noisy_garment()
    lab, core = G.rgb_to_oklab(img), _core(mask)
    median = np.median(lab[core], axis=0)
    weighted = G.color_distance(lab[core], median)
    quality = G.measure_mask_quality(img, mask)
    assert quality["w_spread90"] == pytest.approx(float(np.percentile(weighted, 90)), abs=1e-12)
    assert quality["ab_spread90"] == pytest.approx(
        float(np.percentile(G.chroma_distance(lab[core], median), 90)), abs=1e-12)
    thinned = lab[core][::2]
    every_other = float(np.percentile(G.color_distance(thinned, np.median(thinned, axis=0)), 90))
    assert abs(every_other - quality["w_spread90"]) > 1e-7, "두 칸마다 뽑으면 다르게 나오는 그림이어야 한다"

    other = _box((225, 250, 20, 180))                       # 둘째 후보(화면의 8%)
    _paint(img, other, BLUE)
    upload = median + [0.02, 0.0, 0.0]
    pick = G.pick_by_upload_color(img, [mask, other], upload)
    assert pick["index"] == 0
    assert pick["distances"][0] == pytest.approx(0.35 * 0.02, abs=1e-12)


def test_ga5_blown_and_crushed_need_all_three_channels():
    """3.1절: 날아간 화소는 세 채널 모두 250 이상, 뭉개진 화소는 세 채널 모두 5 이하다. 분모는 윤곽 안 화소 전부다."""
    def flat(rgb):
        img = _background()
        img[_box()] = rgb
        return G.measure_garment_color(img, _box())

    assert flat((255, 252, 40))["blown_frac"] == 0.0, "노랑은 날아간 것이 아니다"
    assert flat((3, 4, 120))["crushed_frac"] == 0.0, "짙은 파랑은 뭉개진 것이 아니다"
    assert (flat((250, 250, 250))["blown_frac"], flat((249, 250, 250))["blown_frac"]) == (1.0, 0.0)
    assert (flat((5, 5, 5))["crushed_frac"], flat((5, 6, 5))["crushed_frac"]) == (1.0, 0.0)
    # 속이 아니라 윤곽 안 화소 전부를 센다. 가장자리 2화소 띠만 날아간 사진에서는 띠의 비율이 그대로 나온다.
    img, mask = _garment((0.80, 0.0, 0.01), amp=0.03)
    rim = mask & ~_inside(mask, 2)
    img[rim] = 255
    assert G.measure_garment_color(img, mask)["blown_frac"] == pytest.approx(rim.sum() / mask.sum())


# ── 나. 윤곽 ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("clothing_type,sub_category,expected", [
    ("top", None, ("top", "clothing")),
    ("outer", None, ("outerwear", "clothing")),
    ("bottom", "와이드 팬츠", ("pants", "clothing")),
    ("bottom", "플리츠 스커트", ("skirt", "clothing")),
    ("dress", None, ("dress", "clothing")),
    (None, None, ("top", "clothing")),
    ("nonsense", None, ("top", "clothing")),
])
def test_na1_mask_prompts_are_the_production_table(clothing_type, sub_category, expected):
    """나.1: 낱말 표 여섯 줄이 운영 prompts_for 와 같다(종류 낱말, 예비 낱말 순서)."""
    assert G.mask_prompts(clothing_type, sub_category) == expected
    assert G.mask_prompts(clothing_type, sub_category) == tone_mask.prompts_for(clothing_type, sub_category)


RED, BLUE = (0.55, 0.15, 0.08), (0.45, -0.02, -0.10)
TOP_BOX, PANTS_BOX = (30, 140, 36, 156), (150, 246, 50, 140)


def _outfit(top_color=RED, pants=BLUE):
    """상의와 다른 색 하의를 함께 입은 사진. 돌려주는 것은 사진, 상의 윤곽, 하의 윤곽."""
    img, top, bottom = _background(), _box(TOP_BOX), _box(PANTS_BOX)
    _paint(img, top, top_color, _folds(0.06))
    _paint(img, bottom, pants, _folds(0.06, x0=PANTS_BOX[2]))
    return img, top, bottom


def test_na2_candidates_drop_duplicates_and_slivers():
    """나.2: 겹침 0.90 이상은 하나로, 화면의 2% 미만은 버린다. 운영 중복 문턱과 같다."""
    garment = _box()
    nearly_same = _box((40, 214, 36, 156))                 # 겹침 0.989
    sliver = _box((0, 30, 0, 30))                          # 화면의 1.8%
    assert G.reduce_candidates([sliver, nearly_same, garment]) == [2]
    assert G.reduce_candidates([sliver]) == []
    assert G.CANDIDATE_DEDUPE_IOU == tone_mask.DEDUPE_IOU
    # 문턱 바로 안쪽. 화면의 2.15%인 후보는 남고, 겹침 0.858인 후보는 다른 후보로 남는다.
    small, near = _box((100, 133, 80, 112)), _box((40, 216, 36, 139))
    assert 0.02 < small.mean() < 0.022 and G.reduce_candidates([small]) == [0]
    assert G.reduce_candidates([garment, near]) == [0, 1]
    # 운영 dedupe 와 같은 후보를 같은 순서로 남긴다. 운영 규칙이 바뀌어 어긋나면 여기서 드러난다.
    candidates = [sliver, _box(PANTS_BOX), nearly_same, garment, near]
    kept = [candidates[i] for i in G.reduce_candidates(candidates)]
    production = tone_mask.dedupe([m for m in candidates if m.mean() >= 0.02])
    assert len(kept) == len(production) == 3 and all(ours is theirs for ours, theirs in zip(kept, production))


def test_na2_single_candidate_is_used_as_is_and_two_pick_the_upload_color():
    """나.2: 후보가 하나면 그대로, 둘이면 올린 옷 색과 가까운 쪽을 쓴다."""
    img, top, bottom = _outfit()
    solo, solo_mask = _garment(RED, box=TOP_BOX)
    result = G.correct_color_option(
        [_photo("a", solo, solo_mask), _photo("b", solo, [solo_mask]), _photo("two", img, [bottom, top])],
        upload_color=RED)
    records = {p.record["key"]: p.record for p in result.photos}
    assert records["a"]["mask"]["selection"] == "single"
    assert records["two"]["mask"]["selection"] == "closest_to_upload"
    assert records["two"]["mask"]["candidates_received"] == 2
    assert np.abs(np.array(records["two"]["measure"]["center"]) - RED).max() < 0.003, "상의를 골라야 한다"
    assert result.record["upload_trust"]["trusted"] is True
    assert [r["status"] for r in records.values()] == ["untouched"] * 3


def test_na2_close_candidates_are_ambiguous():
    """나.2: 가장 가까운 후보와 둘째의 거리 차가 0.02 미만이면 고르지 않는다."""
    img, top, bottom = _outfit(pants=(0.57, 0.15, 0.09))    # 상의와 거의 같은 색의 하의
    solo, solo_mask = _garment(RED, box=TOP_BOX)
    result = G.correct_color_option(
        [_photo("a", solo, solo_mask), _photo("b", solo, solo_mask), _photo("two", img, [top, bottom])],
        upload_color=RED)
    record = result.photos[2].record
    assert (record["status"], record["reason"]) == ("skipped", "ambiguous_candidates")
    near, far = sorted(record["mask"]["candidate_distances"])
    assert far - near < G.CANDIDATE_MARGIN
    assert result.photos[2].image is img


def test_na2_two_candidates_without_upload_color_are_ambiguous():
    """나.2: 올린 옷 색이 없으면 후보 둘 가운데 고르지 않는다."""
    img, top, bottom = _outfit()
    solo, solo_mask = _garment(RED, box=TOP_BOX)
    result = G.correct_color_option(
        [_photo("a", solo, solo_mask), _photo("b", solo, solo_mask), _photo("two", img, [top, bottom])])
    assert result.photos[2].record["reason"] == "ambiguous_no_upload_color"


def test_na2_upload_color_far_from_the_garment_is_not_trusted():
    """나.2: 올린 색이 후보 하나짜리 사진들의 옷 색과 0.07 넘게 멀면 믿지 않는다. 그런 사진이 없어도 믿지 않는다."""
    img, top, bottom = _outfit()
    solo, solo_mask = _garment(RED, box=TOP_BOX)
    faded = (RED[0], RED[1] - 0.08, RED[2])                 # 가중 거리 0.08
    result = G.correct_color_option(
        [_photo("a", solo, solo_mask), _photo("b", solo, solo_mask), _photo("two", img, [top, bottom])],
        upload_color=faded)
    assert result.photos[2].record["reason"] == "upload_color_untrusted"
    assert result.record["upload_trust"]["median_distance"] == pytest.approx(0.08, abs=0.003)
    alone = G.correct_color_option([_photo("two", img, [top, bottom])], upload_color=RED)
    assert alone.photos[0].record["reason"] == "upload_color_untrusted"
    assert alone.record["upload_trust"] == {"median_distance": None, "trusted": False}


def test_na3_only_specks_are_removed():
    """나.3: 0.04% 조각은 지워지고 0.07% 조각은 남는다. 둘째 덩어리(30%)도 남는다."""
    mask = np.zeros((160, 200), bool)
    mask[10:110, 10:80] = True          # 7,000
    mask[20:80, 100:150] = True         # 3,000 (윤곽의 30%)
    mask[130:132, 20:22] = True         # 4 화소 = 0.04%
    mask[140, 100:107] = True           # 7 화소 = 0.07%
    assert mask.sum() == 10011
    trimmed, removed, removed_pixels = G.trim_specks(mask)
    assert (removed, removed_pixels) == (1, 4)
    assert not trimmed[130:132, 20:22].any()
    assert trimmed[140, 100:107].all()
    assert trimmed[20:80, 100:150].all() and trimmed[10:110, 10:80].all()
    assert trimmed.sum() == 10007


def test_na4_holes_inside_the_mask_are_never_filled():
    """나.4: 윤곽 안에 갇힌 구멍(옷 위의 손)의 화소는 바뀌지 않는다."""
    img, mask = _garment((0.62, 0.03, 0.05))
    hand = _box((100, 130, 80, 110))
    _paint(img, hand, (0.72, 0.05, 0.05))                   # 피부색
    mask = mask & ~hand
    assert not G.trim_specks(mask)[0][hand].any()
    result = _triplet(img, mask, seller_color=_center(img, mask) - [0.10, 0, 0])
    photo = result.photos[0]
    assert photo.record["status"] == "corrected"
    assert np.array_equal(photo.image[hand], img[hand])
    assert (photo.image[mask] != img[mask]).any(axis=1).mean() > 0.95


def test_na5_clean_mask_passes_quality_check():
    """나.5: 깨끗한 윤곽은 통과한다."""
    img, mask = _garment(RED)
    assert G.judge_mask_quality(G.measure_mask_quality(img, mask)) == []


def _quality_cases():
    img, mask = _garment(RED)
    grown = cv2.dilate(mask.astype(np.uint8), np.ones((9, 9), np.uint8)).astype(bool)   # 배경으로 4화소 번짐
    joined, both = _garment(RED, box=(30, 220, 36, 156))                                # 상의와 하의를 한 윤곽으로
    _paint(joined, _box((140, 220, 36, 156)), BLUE, _folds(0.06))                       # 아래 42%는 다른 옷
    wide, wide_mask = _garment(RED, box=(0, 195, 0, W))                                 # 화면의 76%
    thin, thin_mask = _garment(RED, box=(20, 220, 90, 98))                              # 폭 8화소
    four = _background()
    four_mask = (_box((10, 90, 20, 80)) | _box((10, 90, 110, 170)) | _box((120, 200, 20, 80))
                 | _box((120, 200, 110, 170)))
    _paint(four, four_mask, RED)
    return {
        "mask_edge_spill": (img, grown), "mask_mixed_color": (joined, both),
        "mask_frame_fill": (wide, wide_mask), "mask_thin": (thin, thin_mask),
        "mask_fragments": (four, four_mask),
    }


@pytest.mark.parametrize("code", ["mask_edge_spill", "mask_mixed_color", "mask_frame_fill", "mask_thin",
                                  "mask_fragments"])
@pytest.mark.parametrize("kind", ["studio", "product", "styling"])
def test_na5_bad_masks_are_skipped_with_their_own_code(code, kind):
    """나.5, 지시서 9: 품질 검사에 걸린 사진은 종류와 상관없이 제 코드로 건너뛰고 사진은 그대로다."""
    img, mask = _quality_cases()[code]
    good, good_mask = _garment(RED)
    result = G.correct_color_option(
        [_photo("bad", img, mask, kind), _photo("a", good, good_mask), _photo("b", good, good_mask),
         _photo("c", good, good_mask)], seller_color=RED)
    bad = result.photos[0]
    assert (bad.record["status"], bad.record["reason"]) == ("skipped", code)
    assert code in bad.record["mask"]["quality_codes"]
    assert bad.image is img
    assert bad.record["measure"] is None, "건너뛴 사진은 그때까지 잰 값만 채운다"


def test_na5_tiny_mask_code_and_the_candidate_rule_that_precedes_it():
    """나.5: 화면의 1.9% 윤곽은 품질 검사에서 mask_tiny 다. 묶음 함수에서는 후보 규칙(2%)이 먼저 버려 no_candidate 가 된다."""
    img, mask = _garment(RED, box=(100, 130, 80, 111))      # 930 화소 = 1.89%
    assert mask.mean() < 0.02
    assert G.judge_mask_quality(G.measure_mask_quality(img, mask))[0] == "mask_tiny"
    record = G.correct_color_option([_photo("p", img, mask)], seller_color=RED).photos[0].record
    assert (record["status"], record["reason"]) == ("skipped", "no_candidate")
    # 속 화소가 200개 미만이면 색을 재지 않는다. 이 그림 크기에서는 늘 가는 윤곽 코드가 먼저 걸린다.
    speck, speck_mask = _garment(RED, box=(100, 116, 80, 96))
    quality = G.measure_mask_quality(speck, speck_mask)
    assert quality["core_pixels"] == 144 and quality["far_share"] is None
    assert G.judge_mask_quality(quality) == ["mask_tiny", "mask_thin", "mask_too_few_pixels"]


def test_na6_fewer_than_two_siblings_means_no_correction():
    """나.6: 형제가 2장 미만이면 no_sibling_check 로 건너뛴다."""
    img, mask = _garment(RED)
    off = np.array(RED) - [0.15, 0, 0]
    for count in (1, 2):
        result = G.correct_color_option([_photo(f"p{i}", img, mask) for i in range(count)],
                                        seller_color=off)
        assert [p.record["reason"] for p in result.photos] == ["no_sibling_check"] * count
        assert [p.record["measure"]["sibling_count"] for p in result.photos] == [count - 1] * count


@pytest.mark.parametrize("offset,expected", [
    ((0.0, 0.06, 0.0), "off_siblings"),       # 색 방향 0.06
    ((0.21, 0.0, 0.0), "off_siblings"),       # 밝기 0.21
    ((0.0, 0.04, 0.0), None),                 # 색 방향 0.04 는 형제로 본다
    ((0.19, 0.0, 0.0), None),                 # 밝기 0.19 도 형제로 본다
])
def test_na6_photo_far_from_its_siblings_is_a_redraw_candidate(offset, expected):
    """나.6: 형제 가운데 색에서 색 방향 0.06 또는 밝기 0.21 떨어진 사진은 off_siblings 와 다시 그리기 표시."""
    base = np.array((0.60, 0.02, 0.04))
    img, mask = _garment(base)
    odd, odd_mask = _garment(base + offset)
    result = G.correct_color_option(
        [_photo("a", img, mask), _photo("b", img, mask), _photo("c", img, mask),
         _photo("odd", odd, odd_mask)], seller_color=base)
    record = result.photos[3].record
    if expected:
        assert (record["status"], record["reason"]) == ("skipped", "off_siblings")
        assert "redraw_candidate" in record["flags"]
        assert result.photos[3].image is odd
    else:
        assert record["reason"] != "off_siblings" and "redraw_candidate" not in record["flags"]
    assert [p.record["status"] for p in result.photos[:3]] == ["untouched"] * 3


def test_na6_quality_far_worse_than_the_siblings_is_skipped():
    """2.5절 나: 형제들의 가운데값보다 품질 숫자가 문턱 넘게 나쁘면 건너뛴다. 형제가 2장 미만이면 보지 않는다."""
    clean = {"far_share": 0.02, "w_spread90": 0.02, "ab_spread90": 0.005, "rim_in_far": 0.05}
    center = (0.60, 0.02, 0.04)

    def check(**worse):
        return G.check_against_siblings({**clean, **worse}, center, [clean, clean, clean], [center] * 3)

    assert check()["reason"] is None and check()["codes"] == []
    assert check(far_share=0.31)["reason"] == "mask_mixed_vs_siblings"
    assert check(w_spread90=0.10)["reason"] == "mask_mixed_vs_siblings"
    assert check(ab_spread90=0.03)["reason"] == "mask_mixed_vs_siblings"
    assert check(far_share=0.29, w_spread90=0.08, ab_spread90=0.026)["reason"] is None, "문턱 바로 안쪽"
    assert check(rim_in_far=0.39)["reason"] == "mask_edge_vs_siblings"
    assert check(rim_in_far=None)["reason"] is None, "띠를 못 잰 사진은 띠를 견주지 않는다"
    both = check(far_share=0.31, rim_in_far=0.39)
    assert both["codes"] == ["mask_mixed_vs_siblings", "mask_edge_vs_siblings"]
    alone = G.check_against_siblings({**clean, "far_share": 0.40}, center, [clean], [center])
    assert (alone["reason"], alone["codes"]) == ("no_sibling_check", [])


def test_na2_candidate_margin_is_a_weighted_distance_from_both_sides():
    """나.2, 2.2절 4번: 둘째 후보와의 가중 거리 차가 0.03이면 고르고 0.015면 고르지 않는다. 밝기 차이도 거리에 든다."""
    img, top, bottom = _outfit(pants=(0.55, 0.18, 0.08))                    # 올린 색과 가중 거리 0.03
    pick = G.pick_by_upload_color(img, [bottom, top], RED)
    assert (pick["index"], pick["reason"]) == (1, None)
    assert pick["distances"] == pytest.approx([0.03, 0.0], abs=0.003)
    img, top, bottom = _outfit(pants=(0.55, 0.165, 0.08))                   # 가중 거리 0.015
    assert G.pick_by_upload_color(img, [bottom, top], RED)["reason"] == "ambiguous_candidates"
    # 같은 색상의 어두운 하의. 색 방향 차이만 보면 0.031 이고, 밝기 차이 0.10 을 넣은 가중 거리는 0.046 이다.
    img, top, bottom = _outfit(pants=(0.45, 0.15 * 0.45 / 0.55, 0.08 * 0.45 / 0.55))
    pick = G.pick_by_upload_color(img, [top, bottom], RED)
    assert pick["index"] == 0 and pick["distances"][1] == pytest.approx(0.046, abs=0.004)


def test_na2_upload_trust_is_the_median_distance_against_the_limit():
    """나.2, 2.2절 5번: 올린 색 신뢰는 후보 하나짜리 사진들과의 가중 거리의 가운데값을 0.07과 견준다. 평균도 최솟값도 아니다."""
    up = np.array(RED)

    def trust(*distances):
        return G.upload_color_trust([up + [0.0, d, 0.0] for d in distances], up)

    assert trust(0.01, 0.06, 0.20) == {"median_distance": pytest.approx(0.06), "trusted": True}
    assert trust(0.01, 0.08, 0.09)["trusted"] is False
    assert trust(0.065)["trusted"] is True and trust(0.075)["trusted"] is False


def test_na2_upload_trust_counts_only_single_candidate_photos():
    """9.2절: 올린 색 신뢰의 가운데값은 후보가 1개인 사진만으로 구한다. 후보 둘에서 고른 사진의 거리는 들지 않는다."""
    outfit, top, bottom = _outfit(top_color=np.array(RED) + [0.0, 0.0, 0.03])
    solo, solo_mask = _garment(RED, box=TOP_BOX)
    upload = np.array(RED) + [0.0, 0.0, 0.06]
    result = G.correct_color_option(
        [_photo("a", solo, solo_mask), _photo("two1", outfit, [top, bottom]),
         _photo("two2", outfit, [top, bottom])], upload_color=upload)
    for photo in result.photos[1:]:
        assert photo.record["mask"]["selection"] == "closest_to_upload"
        assert photo.record["mask"]["candidate_distances"][0] == pytest.approx(0.03, abs=0.003)
    assert result.record["upload_trust"] == {"median_distance": pytest.approx(0.06, abs=0.003), "trusted": True}


def test_na3_speck_limit_is_exact_and_pieces_join_in_eight_directions():
    """나.3, 2.3절: 윤곽 넓이의 0.05% 미만만 지운다(0.04995%는 지우고 0.0599%는 남긴다). 덩어리는 8방향으로 잇는다."""
    mask = np.zeros((160, 200), bool)
    mask[10:110, 10:80] = True
    mask[20:80, 100:150] = True                             # 여기까지 10,000 화소
    mask[130, 20:25] = True                                 # 5 화소 = 0.04995%
    mask[140, 100:106] = True                               # 6 화소 = 0.0599%
    trimmed, removed, removed_pixels = G.trim_specks(mask)
    assert (removed, removed_pixels) == (1, 5)
    assert not trimmed[130, 20:25].any() and trimmed[140, 100:106].all()

    corner = np.zeros((H, W), bool)
    corner[BOX[1], BOX[3]] = True                           # 옷 네모와 대각선으로만 닿는 한 화소
    trimmed, removed, _ = G.trim_specks(_box() | corner)
    assert removed == 0 and trimmed[BOX[1], BOX[3]]
    apart = np.zeros((H, W), bool)
    apart[BOX[1] + 1, BOX[3] + 1] = True                    # 한 칸 떨어지면 조각이다
    assert G.trim_specks(_box() | apart)[1:] == (1, 1)


def test_na3_batch_trims_specks_and_moves_the_second_piece():
    """나.3, 2.3절: 묶음 함수도 점 같은 조각만 지운다. 지운 조각은 윤곽 밖이라 그대로이고, 둘째 덩어리는 함께 옮겨진다."""
    base = (0.62, 0.03, 0.05)
    img, mask = _garment(base)
    speck = _box((230, 232, 100, 102))                      # 4화소. 윤곽 넓이의 0.019%
    _paint(img, speck, base)
    photo = _triplet(img, mask | speck, seller_color=_center(img, mask) - [0.10, 0, 0]).photos[0]
    assert photo.record["status"] == "corrected"
    assert (photo.record["mask"]["removed_fragments"], photo.record["mask"]["removed_pixels"]) == (1, 4)
    assert np.array_equal(photo.image[speck], img[speck])

    img, _ = _garment(base)
    left, right = _box((40, 216, 36, 116)), _box((40, 216, 124, 156))      # 팔에 가려 둘로 나뉜 옷. 71%와 29%
    both = left | right
    photo = _triplet(img, both, seller_color=_center(img, both) - [0.10, 0, 0]).photos[0]
    assert photo.record["status"] == "corrected"
    assert photo.record["mask"]["quality"]["fragments"] == 2
    assert (photo.record["mask"]["removed_fragments"], photo.record["mask"]["removed_pixels"]) == (0, 0)
    for piece in (left, right):
        assert (photo.image[piece] != img[piece]).any(axis=1).mean() > 0.95


@pytest.mark.parametrize("readonly", [False, True])
def test_na3_batch_preserves_input_arrays_when_specks_are_trimmed(readonly):
    """2.3절, 사.2: 조각을 실제로 지우고 옷 색을 옮겨도 호출자의 사진과 윤곽은 그대로다. 쓰기 금지 배열도 받는다."""
    base = (0.62, 0.03, 0.05)
    img, mask = _garment(base)
    speck = _box((230, 232, 100, 102))                       # 4화소. 지워지는 조각이다
    _paint(img, speck, base)
    photos = [_photo(f"p{i}", img.copy(), (mask | speck).copy()) for i in range(3)]
    snapshots = [(p.image.copy(), p.masks.copy()) for p in photos]
    if readonly:
        for photo in photos:
            photo.image.setflags(write=False)
            photo.masks.setflags(write=False)

    result = G.correct_color_option(photos, seller_color=_center(img, mask) - [0.10, 0.0, 0.0])
    for given, out, (image_before, mask_before) in zip(photos, result.photos, snapshots):
        assert out.record["status"] == "corrected"
        assert (out.record["mask"]["removed_fragments"], out.record["mask"]["removed_pixels"]) == (1, 4)
        assert np.array_equal(given.image, image_before), "사진 입력 전체가 보존돼야 한다"
        assert np.array_equal(given.masks, mask_before), "지운 조각도 호출자의 윤곽에서는 남아 있어야 한다"
        assert np.array_equal(out.image[speck], image_before[speck])


def test_na5_quality_numbers_match_their_definition():
    """2.4절: 품질 숫자가 식 그대로다. 윤곽 안쪽 6화소 테두리만 다른 색인 그림에서 손으로 센 값과 견준다."""
    img, mask = _garment((0.62, 0.03, 0.05), amp=0.0)
    frame = mask & ~_box((46, 210, 42, 150))
    _paint(img, frame, (0.62, 0.13, 0.05))                  # 가중 거리 0.10
    quality = G.measure_mask_quality(img, mask)
    lab, core = G.rgb_to_oklab(img), _core(mask)
    median = np.median(lab[core], axis=0)
    weighted = G.color_distance(lab[core], median)
    rim = mask & ~cv2.erode(mask.astype(np.uint8), np.ones((15, 15), np.uint8)).astype(bool)   # 3k = 15
    rim_far = float((G.color_distance(lab[rim], median) > 0.06).mean())
    assert quality["core_pixels"] == int(core.sum()) == 172 * 116
    assert quality["core_share"] == pytest.approx(172 * 116 / (176 * 120))
    assert quality["core_px_1536"] == quality["core_pixels"] * 64
    assert quality["far_share"] == pytest.approx(float((weighted > 0.06).mean()), abs=1e-12)
    assert quality["far_share"] == pytest.approx(1 - (164 * 108) / (172 * 116), abs=1e-9)       # 0.112
    assert quality["w_spread90"] == pytest.approx(float(np.percentile(weighted, 90)), abs=1e-12)
    assert quality["w_spread90"] == pytest.approx(0.10, abs=0.005)
    assert quality["ab_spread90"] == pytest.approx(0.10, abs=0.005)
    assert quality["rim_in_far"] == pytest.approx(rim_far, abs=1e-12)
    assert quality["rim_in_far"] > 0.80 and quality["rim_excess"] == pytest.approx(rim_far - quality["far_share"])
    assert G.judge_mask_quality(quality) == ["mask_edge_spill"]


def test_na5_mixed_color_fires_on_either_number_alone():
    """2.4절: 섞인 색은 먼 화소 비율(0.42)만 넘어도, 거리 90번째 백분위(0.14)만 넘어도 걸린다. 둘 다 안쪽이면 통과다."""
    base = (0.62, 0.03, 0.05)
    half = np.broadcast_to(np.arange(W)[None, :] >= BOX[2] + 66, (H, W))    # 오른쪽 45%
    img, mask = _two_color(base, (0.62, 0.12, 0.05), half)                  # 가중 거리 0.09
    quality = G.measure_mask_quality(img, mask)
    assert quality["far_share"] > 0.42 and quality["w_spread90"] < 0.14
    assert "mask_mixed_color" in G.judge_mask_quality(quality)
    img, mask = _two_color(base, (0.62, 0.23, 0.05), _rows(20, 3))          # 15%, 가중 거리 0.20
    quality = G.measure_mask_quality(img, mask)
    assert quality["far_share"] < 0.42 and quality["w_spread90"] > 0.14
    assert "mask_mixed_color" in G.judge_mask_quality(quality)
    img, mask = _two_color(base, (0.62, 0.15, 0.05), _rows(20, 7), amp=0.0)  # 35%, 가중 거리 0.12
    quality = G.measure_mask_quality(img, mask)
    assert quality["far_share"] == pytest.approx(0.35, abs=0.01)
    assert quality["w_spread90"] == pytest.approx(0.12, abs=0.005)
    assert "mask_mixed_color" not in G.judge_mask_quality(quality)


def test_na5_thin_fires_on_the_core_share_or_the_core_pixel_count_alone():
    """2.4절: 가는 윤곽은 속 비율(0.85)만 모자라도, 속 화소 수(폭 1536 환산 40,000)만 모자라도 걸린다."""
    narrow, narrow_mask = _garment(RED, box=(20, 220, 90, 118))             # 폭 28: 속 비율 0.84
    quality = G.measure_mask_quality(narrow, narrow_mask)
    assert quality["core_share"] == pytest.approx(0.84, abs=0.002) and quality["core_px_1536"] > 40_000
    assert G.judge_mask_quality(quality) == ["mask_thin"]
    wider, wider_mask = _garment(RED, box=(20, 220, 90, 122))               # 폭 32: 속 비율 0.8575
    assert G.judge_mask_quality(G.measure_mask_quality(wider, wider_mask)) == []

    shape = (260, 1536)                                                     # 폭 1536 에서는 9화소를 깎는다
    img, small = _background(shape), _box((20, 220, 100, 300), shape)       # 200 x 200
    _paint(img, small, RED)
    quality = G.measure_mask_quality(img, small)
    assert quality["core_pixels"] == 192 * 192 and quality["core_px_1536"] == 36864
    assert quality["core_share"] > 0.92
    assert G.judge_mask_quality(quality) == ["mask_thin"]
    big = _box((20, 232, 100, 312), shape)                                  # 212 x 212. 속 41,616 화소
    _paint(img, big, RED)
    assert G.judge_mask_quality(G.measure_mask_quality(img, big)) == []

    # 속 화소가 200개 이상이면 색 숫자를 잰다. 가는 윤곽 코드만 남는다.
    sliver, sliver_mask = _garment(RED, box=(40, 190, 90, 97))              # 속 3 x 146 = 438 화소
    quality = G.measure_mask_quality(sliver, sliver_mask)
    assert quality["core_pixels"] == 438 and quality["far_share"] == 0.0
    assert G.judge_mask_quality(quality) == ["mask_thin"]


def test_na5_area_and_fragment_limits_from_the_passing_side():
    """2.4절: 화면의 73.8%와 2.15%는 통과한다. 덩어리 3개도 통과하고, 덩어리는 넓이 1% 이상인 것만 8방향으로 센다."""
    img, mask = _garment(RED, box=(0, 189, 0, W))                           # 화면의 73.8%
    assert 0.73 < mask.mean() < 0.75
    assert G.judge_mask_quality(G.measure_mask_quality(img, mask)) == []
    img, mask = _garment(RED, box=(100, 133, 80, 112))                      # 1,056 화소 = 2.15%
    assert 0.02 < mask.mean() < 0.022
    assert "mask_tiny" not in G.judge_mask_quality(G.measure_mask_quality(img, mask))

    img = _background()
    three = _box((30, 226, 6, 62)) | _box((30, 226, 68, 124)) | _box((30, 226, 130, 186))
    _paint(img, three, RED)
    quality = G.measure_mask_quality(img, three)
    assert quality["fragments"] == 3 and G.judge_mask_quality(quality) == []
    img, mask = _garment(RED)                                               # 21,120 화소
    small, big = _box((225, 236, 40, 52)), _box((225, 243, 60, 78))         # 132 화소(0.6%), 324 화소(1.5%)
    _paint(img, small | big, RED)
    assert G.measure_mask_quality(img, mask | small)["fragments"] == 1
    assert G.measure_mask_quality(img, mask | big)["fragments"] == 2
    blocks = _box((20, 120, 20, 96)) | _box((120, 220, 96, 172))            # 모서리 한 점에서만 만나는 두 네모
    _paint(img, blocks, RED)
    assert G.measure_mask_quality(img, blocks)["fragments"] == 1


def test_na5_edge_spill_threshold_and_its_place_before_mixed_color():
    """2.4절, 9.3절: 배경으로 2화소 번진 윤곽은 통과하고 3화소는 걸린다. 번짐과 섞인 색이 함께 걸리면 번짐이 먼저다."""
    img, mask = _garment(RED)
    two, three = G.measure_mask_quality(img, _grown(mask, 2)), G.measure_mask_quality(img, _grown(mask, 3))
    assert two["rim_excess"] == pytest.approx(0.296, abs=0.01) and G.judge_mask_quality(two) == []
    assert three["rim_excess"] == pytest.approx(0.413, abs=0.01)
    assert G.judge_mask_quality(three) == ["mask_edge_spill"]

    img, mask = _two_color((0.62, 0.03, 0.05), (0.62, 0.23, 0.05), _rows(20, 3), amp=0.0)    # 15%, 가중 거리 0.20
    spilled = _grown(mask, 4)
    quality = G.measure_mask_quality(img, spilled)
    assert quality["rim_excess"] > 0.33 and quality["w_spread90"] > 0.14
    assert G.judge_mask_quality(quality) == ["mask_edge_spill", "mask_mixed_color"]
    good, good_mask = _garment((0.62, 0.03, 0.05))
    result = G.correct_color_option(
        [_photo("bad", img, spilled), _photo("a", good, good_mask), _photo("b", good, good_mask),
         _photo("c", good, good_mask)], seller_color=(0.62, 0.03, 0.05))
    assert result.photos[0].record["reason"] == "mask_edge_spill"


def test_na5_lengths_scale_with_the_photo_width():
    """1.1절: 길이는 폭 1536 기준이다. 폭 1536 에서는 9화소, 폭 3072 에서는 18화소를 깎고 경계 띠도 두 배다."""
    base = (0.62, 0.03, 0.05)
    img, mask, _shape = _wide(base, amp=0.0)                                # 옷 180 x 1200
    assert G.measure_mask_quality(img, mask)["core_pixels"] == (180 - 8) * (1200 - 8)
    shape = (260, 3072)
    img, mask = _background(shape), _box((20, 240, 300, 2700), shape)
    _paint(img, mask, base)
    quality = G.measure_mask_quality(img, mask)
    assert quality["core_pixels"] == (220 - 17) * (2400 - 17)
    assert quality["core_px_1536"] == quality["core_pixels"] / 4
    weight = G.move_weight(img, mask, _center(img, mask))
    assert weight[130, 300] == pytest.approx(0.955 / 4, abs=1e-3) and weight[130, 304] == 1.0


def test_na5_mask_that_falls_under_two_percent_after_trimming_is_tiny():
    """2.2절 1번, 2.3절, 2.4절: 후보 규칙(2%)을 겨우 넘은 윤곽이 조각을 지운 뒤 2% 아래로 내려가면 묶음 함수에서도 mask_tiny 다."""
    shape = (600, 400)                                                      # 화면의 2%는 4,800 화소
    img = _background(shape)
    body, speck = _box((100, 159, 100, 181), shape), _box((300, 301, 100, 102), shape)     # 4,779 화소와 2 화소
    body[159, 100:120] = True                                               # 4,799 화소
    _paint(img, body | speck, RED)
    candidate = body | speck
    assert candidate.sum() == 4801 and G.reduce_candidates([candidate]) == [0]
    record = G.correct_color_option([_photo("p", img, candidate)], seller_color=RED).photos[0].record
    assert (record["status"], record["reason"]) == ("skipped", "mask_tiny")
    assert (record["mask"]["removed_fragments"], record["mask"]["removed_pixels"]) == (1, 2)
    assert record["mask"]["quality"]["area_frac"] == pytest.approx(4799 / 240000)


def _quality_numbers(**over):
    return {"far_share": 0.02, "w_spread90": 0.02, "ab_spread90": 0.005, "rim_in_far": 0.05, **over}


def test_na6_sibling_values_are_medians_not_means():
    """나.6, 2.5절: 형제의 가운데 색과 품질 숫자는 가운데값이다. 형제 하나가 멀리 있어도 기준이 끌려가지 않는다."""
    dark, bright = (0.40, 0.02, 0.04), (0.85, 0.02, 0.04)
    check = G.check_against_siblings(_quality_numbers(), (0.32, 0.02, 0.04), [_quality_numbers()] * 3,
                                     [dark, dark, bright])
    assert check["d_l"] == pytest.approx(-0.08) and check["reason"] is None       # 평균이면 -0.23 이라 걸린다
    noisy = [_quality_numbers(), _quality_numbers(), _quality_numbers(far_share=0.30)]
    worse = G.check_against_siblings(_quality_numbers(far_share=0.31), dark, noisy, [dark] * 3)
    assert worse["reason"] == "mask_mixed_vs_siblings"                          # 평균이면 차이가 0.20 이라 통과한다
    inside = G.check_against_siblings(_quality_numbers(rim_in_far=0.37), dark, [_quality_numbers()] * 3, [dark] * 3)
    assert inside["reason"] is None, "띠 문턱(0.33) 바로 안쪽"


def test_na6_photo_that_failed_the_sibling_check_is_not_a_studio_reference():
    """5.2절: 기준에 쓰는 스튜디오 사진은 2절을 모두 통과한 사진이다. 형제 대조에서 떨어진 스튜디오 사진은 세지 않는다."""
    base = np.array((0.60, 0.02, 0.04))
    img, mask = _garment(base)
    odd, _ = _garment(base + [0.0, 0.06, 0.0])
    far, _ = _garment(base + [0.18, 0.0, 0.0])
    result = G.correct_color_option(
        [_photo("s0", img, mask), _photo("s1", img, mask), _photo("s2", odd, mask),
         _photo("t", far, mask, "styling")], upload_color=base)
    assert [p.record["reason"] for p in result.photos] == [
        "no_reference_few_studio", "no_reference_few_studio", "off_siblings", "no_reference_few_studio"]
    assert result.record["studio"]["used"] == 2 and result.record["studio"]["dropped_keys"] == []


# ── 다. 여러 색 옷 ──────────────────────────────────────────────────────────

def test_da1_shaded_plain_garment_is_one_color():
    """다.1: 그늘이 있는 단색 옷(밝기가 0.25 흔들림)은 통과한다."""
    color = (0.62, 0.03, 0.05)
    img, mask = _garment(color, amp=0.125 / color[0])
    lightness = G.rgb_to_oklab(img)[mask][:, 0]
    assert lightness.max() - lightness.min() == pytest.approx(0.25, abs=0.01)
    measure = G.measure_garment_color(img, mask)
    assert measure["multicolor"] is False and measure["same"] >= 0.99
    result = _triplet(img, mask, seller_color=color)
    assert result.record["multicolor"] == {"flagged": False, "keys": []}
    assert [p.record["reason"] for p in result.photos] == ["within_tolerance"] * 3


def _two_color(base, second, where, amp=0.05):
    img, mask = _garment(base, amp=amp)
    _paint(img, mask & where, second, _folds(amp))
    return img, mask


def test_da2_saturated_second_color_fails_the_same_fabric_share():
    """다.2, 지시서 5: 진한 둘째 색이 20%인 줄무늬 옷은 same 으로 걸리고 이유가 남는다."""
    img, mask = _two_color((0.62, 0.03, 0.05), (0.62, 0.13, 0.05), _rows(10, 2))
    measure = G.measure_garment_color(img, mask)
    assert measure["same"] == pytest.approx(0.80, abs=0.02)
    assert measure["cw_c"] <= G.CENTER_WIDTH_CHROMA_MAX, "색 방향 폭이 아니라 same 으로 걸려야 한다"
    assert measure["multicolor"] is True
    result = _triplet(img, mask, seller_color=(0.62, 0.03, 0.05))
    assert _outcomes(result) == [("skipped", "multicolor_garment")] * 3
    assert result.record["multicolor"] == {"flagged": True, "keys": ["p0", "p1", "p2"]}
    assert result.record["reference_color"] is None
    assert result.record["reference_reason"] == "multicolor_garment"
    assert all(p.image is img for p in result.photos)


def test_da2_bold_dark_stripes_are_already_stopped_by_the_mask_check():
    """지시서 5: 밝기가 크게 다른 굵은 줄무늬(30%)는 여러 색 판정에 오기 전에 윤곽 품질 검사(섞인 색)에서 건너뛴다.

    둘째 색이 10%를 넘고 가중 거리 0.14 보다 멀면 거리 90번째 백분위가 문턱을 넘는다. 건너뛰는 것은 같고 이유 코드만 다르다.
    """
    img, mask = _two_color((0.90, 0.0, 0.01), (0.30, 0.0, -0.04), _rows(10, 3))
    assert G.measure_garment_color(img, mask)["multicolor"] is True
    result = _triplet(img, mask, seller_color=(0.90, 0.0, 0.01))
    assert _outcomes(result) == [("skipped", "mask_mixed_color")] * 3
    assert all(p.image is img for p in result.photos)


def test_da3_two_pale_colors_half_and_half_fail_the_chroma_width():
    """다.3, 지시서 5: 연한 두 색이 반반인 두 톤 옷(색 방향 차이 0.04)은 cw_c 로 걸린다."""
    left = np.broadcast_to(np.arange(W)[None, :] < (BOX[2] + BOX[3]) // 2, (H, W))
    img, mask = _two_color((0.80, 0.04, 0.03), (0.80, 0.0, 0.03), left)
    measure = G.measure_garment_color(img, mask)
    assert measure["same"] >= G.SAME_SHARE_MIN, "same 이 아니라 색 방향 폭으로 걸려야 한다"
    assert measure["cw_c"] == pytest.approx(0.04, abs=0.004)
    assert measure["multicolor"] is True
    result = _triplet(img, mask, seller_color=(0.80, 0.02, 0.03))
    assert [p.record["reason"] for p in result.photos] == ["multicolor_garment"] * 3


def test_da4_fine_stripes_pass_and_keep_their_contrast_after_the_move():
    """다.4: 가는 줄(바탕과 색 방향 차이 0.03)은 통과하고, 옮긴 뒤에도 줄과 바탕의 색 방향 차이가 그대로다."""
    base, stripe = (0.70, 0.01, 0.02), (0.70, 0.01, 0.05)
    lines = _rows(5, 1)
    img, mask = _two_color(base, stripe, lines)
    measure = G.measure_garment_color(img, mask)
    assert measure["multicolor"] is False and measure["same"] >= 0.99

    target = np.array(measure["center"]) + [0.0, 0.037, 0.0]        # 색 방향만 0.037 벗어남
    result = _triplet(img, mask, seller_color=target)
    photo = result.photos[0]
    assert photo.record["status"] == "corrected"
    assert photo.record["plan"]["d_ab"] == pytest.approx([0.015, 0.0], abs=1e-9)

    def contrast(picture):
        lab, core = G.rgb_to_oklab(picture), _core(mask)
        return lab[core & lines].mean(axis=0) - lab[core & ~lines].mean(axis=0)

    before, after = contrast(img), contrast(photo.image)
    assert np.hypot(*before[1:]) == pytest.approx(0.03, abs=0.003)
    assert np.abs(after - before).max() < 0.003


def test_da5_one_multicolor_photo_stops_the_whole_color_option():
    """다.5: 한 장이 걸리면 같은 묶음의 다른 사진도 multicolor_garment 로 건너뛴다."""
    base, second = (0.80, 0.0, 0.03), (0.80, 0.035, 0.03)       # 색 방향 차이 0.035. 같은 천으로는 본다
    split = (BOX[2] + int(0.55 * (BOX[3] - BOX[2])))
    right = np.broadcast_to(np.arange(W)[None, :] >= split, (H, W))
    front, mask = _two_color(base, second, right)              # 둘째 색 45%
    side, _ = _two_color(base, second, _rows(5, 1))            # 둘째 색 20%
    assert G.measure_garment_color(front, mask)["multicolor"] is True
    assert G.measure_garment_color(side, mask)["multicolor"] is False
    result = G.correct_color_option(
        [_photo("front", front, mask), _photo("side1", side, mask), _photo("side2", side, mask)],
        seller_color=base)
    assert _outcomes(result) == [("skipped", "multicolor_garment")] * 3
    assert result.record["multicolor"] == {"flagged": True, "keys": ["front"]}


def test_da5_photo_stopped_by_the_sibling_check_for_mixed_color_still_stops_the_color_option():
    """4.3절, 14.1절 4번: 섞인 색으로 형제 대조에서 먼저 건너뛴 사진도 여러 색 사진으로 센다.

    형제는 단색인데 혼자 둘째 색이 20%인 사진은 형제와 견준 품질 검사(2.5절 나)에서 먼저 건너뛴다.
    그 사진은 먼저 걸린 이유를 그대로 갖고, 나머지 사진은 multicolor_garment 로 건너뛴다.
    """
    base = (0.62, 0.03, 0.05)
    plain, mask = _garment(base, amp=0.05)
    striped, _ = _two_color(base, (0.62, 0.13, 0.05), _rows(10, 2))
    assert G.measure_garment_color(striped, mask)["multicolor"] is True
    photos = [_photo("striped", striped, mask), _photo("a", plain, mask), _photo("b", plain, mask),
              _photo("c", plain, mask)]
    result = G.correct_color_option(photos, seller_color=base)
    records = [p.record for p in result.photos]
    assert (records[0]["status"], records[0]["reason"]) == ("skipped", "mask_mixed_vs_siblings")
    assert records[0]["mask"]["quality_codes"] == ["mask_mixed_vs_siblings"]
    assert [(r["status"], r["reason"]) for r in records[1:]] == [("skipped", "multicolor_garment")] * 3
    assert result.record["multicolor"] == {"flagged": True, "keys": ["striped"]}
    assert (result.record["reference_color"], result.record["reference_reason"]) == (None, "multicolor_garment")
    assert all(out.image is given.image for out, given in zip(result.photos, photos))


def test_da5_half_and_half_photo_of_a_two_color_garment_stops_the_other_color_from_moving():
    """14.1절 4번: 두 색 옷(색 방향 차이 0.045)의 반반 사진이 형제 대조에서 빠져도 묶음 전체가 multicolor_garment 다.

    전에는 묶음 판정이 꺼져서, 둘째 색만 찍힌 멀쩡한 사진이 첫째 색 쪽으로 색 방향 0.02 옮겨졌다.
    """
    first = np.array((0.60, 0.02, 0.04))
    second = first + [0.0, 0.045, 0.0]
    left = np.broadcast_to(np.arange(W)[None, :] < (BOX[2] + BOX[3]) // 2, (H, W))
    plain = [_garment(first + off) for off in ([0, 0, 0], [0.004, 0, 0], [-0.004, 0, 0])]
    half = _two_color(first, second, left, amp=0.06)
    other = _garment(second)
    photos = ([_photo(f"a{i}", img, mask) for i, (img, mask) in enumerate(plain)]
              + [_photo("half", *half), _photo("b", *other)])
    result = G.correct_color_option(photos, upload_color=first)
    records = {p.record["key"]: p.record for p in result.photos}
    assert (records["half"]["status"], records["half"]["reason"]) == ("skipped", "mask_mixed_vs_siblings")
    assert "mask_mixed_color" not in records["half"]["mask"]["quality_codes"], "품질 검사는 통과하고 형제 대조에서 걸린 사진이다"
    for key in ("a0", "a1", "a2", "b"):
        assert (records[key]["status"], records[key]["reason"]) == ("skipped", "multicolor_garment"), key
    assert result.record["multicolor"] == {"flagged": True, "keys": ["half"]}
    assert (result.record["reference_source"], result.record["reference_reason"]) == (None, "multicolor_garment")
    assert all(out.image is given.image for out, given in zip(result.photos, photos))


def test_da5_photo_stopped_by_the_mixed_color_quality_code_stops_the_color_option():
    """14.1절 4번: 품질 코드에 mask_mixed_color 가 있는 사진도 여러 색 사진으로 센다. 그 코드가 첫 이유가 아니어도 센다."""
    base = (0.62, 0.03, 0.05)
    good, good_mask = _garment(base)
    siblings = [_photo("a", good, good_mask), _photo("b", good, good_mask), _photo("c", good, good_mask)]
    joined, both = _quality_cases()["mask_mixed_color"]     # 상의와 하의를 한 윤곽으로 딴 사진
    result = G.correct_color_option([_photo("bad", joined, both), *siblings], seller_color=base)
    assert _outcomes(result) == [("skipped", "mask_mixed_color")] + [("skipped", "multicolor_garment")] * 3
    assert result.record["multicolor"] == {"flagged": True, "keys": ["bad"]}

    img, mask = _two_color(base, (0.62, 0.23, 0.05), _rows(20, 3), amp=0.0)
    spilled = _grown(mask, 4)                                # 번짐과 섞인 색이 함께 걸린다. 첫 이유는 번짐이다
    result = G.correct_color_option([_photo("bad", img, spilled), *siblings], seller_color=base)
    assert result.photos[0].record["mask"]["quality_codes"] == ["mask_edge_spill", "mask_mixed_color"]
    assert _outcomes(result) == [("skipped", "mask_edge_spill")] + [("skipped", "multicolor_garment")] * 3
    assert result.record["multicolor"] == {"flagged": True, "keys": ["bad"]}

    # 섞인 색이 아닌 품질 코드(번짐)만 걸린 사진은 묶음을 멈추지 않는다.
    result = G.correct_color_option([_photo("bad", good, _grown(good_mask, 4)), *siblings], seller_color=base)
    assert _outcomes(result) == [("skipped", "mask_edge_spill")] + [("untouched", "within_tolerance")] * 3
    assert result.record["multicolor"] == {"flagged": False, "keys": []}


def test_da6_known_limit_tone_on_tone_passes_and_records_lightness_width():
    """다.6: 알려진 한계를 고정한다. 흰 바탕에 톤만 다른 둘째 색이 30%인 옷은 통과하고 cw_L 이 기록된다."""
    white, tone = (0.95, 0.0, 0.005), (0.75, 0.02, 0.0)      # 0.2 어둡고 진하기 0.02
    img, mask = _two_color(white, tone, _rows(10, 3), amp=0.02)
    measure = G.measure_garment_color(img, mask)
    assert measure["multicolor"] is False
    result = _triplet(img, mask, seller_color=measure["center"])
    assert result.record["multicolor"]["flagged"] is False
    assert [p.record["reason"] for p in result.photos] == ["within_tolerance"] * 3
    recorded = result.photos[0].record["measure"]["cw_l"]
    assert isinstance(recorded, float) and recorded == measure["cw_l"]


def test_da2_same_fabric_share_threshold_from_both_sides():
    """4.2절: 진한 둘째 색이 8%면 same 0.92로 통과하고, 12%면 0.88로 여러 색이다(문턱 0.90)."""
    base, second = (0.62, 0.03, 0.05), (0.62, 0.13, 0.05)
    img, mask = _two_color(base, second, _rows(25, 2))
    measure = G.measure_garment_color(img, mask)
    assert measure["same"] == pytest.approx(0.92, abs=0.01) and measure["multicolor"] is False
    img, mask = _two_color(base, second, _rows(25, 3))
    measure = G.measure_garment_color(img, mask)
    assert measure["same"] == pytest.approx(0.88, abs=0.01) and measure["multicolor"] is True


def test_da3_chroma_width_threshold_and_its_percentiles():
    """4.2절: 색 방향 가운데 폭은 35번째에서 65번째 백분위의 폭이다. 0.02면 통과하고 0.03이면 여러 색이다(문턱 0.025)."""
    left = np.broadcast_to(np.arange(W)[None, :] < (BOX[2] + BOX[3]) // 2, (H, W))
    img, mask = _two_color((0.80, 0.02, 0.03), (0.80, 0.0, 0.03), left)     # 반반, 색 방향 차이 0.02
    measure = G.measure_garment_color(img, mask)
    assert measure["cw_c"] == pytest.approx(0.02, abs=0.003) and measure["multicolor"] is False
    img, mask = _two_color((0.80, 0.03, 0.03), (0.80, 0.0, 0.03), left)     # 반반, 색 방향 차이 0.03
    measure = G.measure_garment_color(img, mask)
    assert measure["cw_c"] == pytest.approx(0.03, abs=0.003) and measure["multicolor"] is True

    base, second = (0.80, 0.0, 0.03), (0.80, 0.035, 0.03)
    img, mask = _two_color(base, second, _rows(10, 3))      # 둘째 색 30%: 35번째와 65번째가 모두 바탕이다
    measure = G.measure_garment_color(img, mask)
    assert measure["same"] >= 0.99 and measure["cw_c"] < 0.01 and measure["multicolor"] is False
    img, mask = _two_color(base, second, _rows(10, 4))      # 둘째 색 40%: 65번째가 둘째 색이다
    measure = G.measure_garment_color(img, mask)
    assert measure["cw_c"] == pytest.approx(0.035, abs=0.004) and measure["multicolor"] is True


def test_da_judging_range_midpoints():
    """4.1절: 판단용 범위. 색 방향은 0.04에서 0.08, 밝기 차이는 0.30에서 0.45 사이에서 같은 천 정도가 0.5를 지난다."""
    def same(second):
        img, mask = _two_color((0.80, 0.01, 0.02), second, _rows(10, 2), amp=0.0)
        return G.measure_garment_color(img, mask)["same"]

    assert same((0.80, 0.065, 0.02)) >= 0.99, "색 방향 0.055 는 같은 천(정도 0.68)"
    assert same((0.80, 0.075, 0.02)) == pytest.approx(0.80, abs=0.01), "색 방향 0.065 는 다른 천(정도 0.32)"
    assert same((0.44, 0.0055, 0.011)) >= 0.99, "0.36 어두운 그늘은 같은 천(정도 0.65)"
    assert same((0.41, 0.0051, 0.0103)) == pytest.approx(0.80, abs=0.01), "0.39 어두우면 다른 천(정도 0.35)"
    assert same((0.80, 0.01, 0.02)) == 1.0


def test_da6_lightness_width_is_the_35_to_65_percentile_span():
    """다.6, 4.2절: cw_L 은 같은 천 화소 밝기의 35번째에서 65번째 백분위의 폭이다. 판정에는 쓰지 않는다."""
    left = np.broadcast_to(np.arange(W)[None, :] < (BOX[2] + BOX[3]) // 2, (H, W))
    img, mask = _two_color((0.95, 0.0, 0.005), (0.75, 0.0, 0.004), left, amp=0.0)     # 반반, 밝기만 0.2 다르다
    measure = G.measure_garment_color(img, mask)
    assert measure["multicolor"] is False
    assert measure["cw_l"] == pytest.approx(0.20, abs=0.01)

    img, mask = _two_color((0.95, 0.0, 0.005), (0.75, 0.02, 0.0), _rows(10, 3), amp=0.02)    # 다.6 의 그림
    measure = G.measure_garment_color(img, mask)
    lightness = G.rgb_to_oklab(img)[_core(mask)][:, 0]
    assert measure["same"] == 1.0
    assert measure["cw_l"] == pytest.approx(
        float(np.percentile(lightness, 65) - np.percentile(lightness, 35)), abs=1e-12)
    assert measure["cw_l"] < 0.03 < float(np.percentile(lightness, 95) - np.percentile(lightness, 5))


def test_da_fewer_than_200_same_fabric_pixels_counts_as_multicolor():
    """3.1절, 4.2절: 같은 천 화소가 200개 미만이면 다듬은 가운데값을 쓰지 않고 그 사진을 여러 색으로 본다.

    묶음 함수에서는 속 화소 수 규칙(가는 윤곽)이 먼저 걸려 폭 110화소 미만인 사진에서만 닿는다. 함수로 직접 본다.
    """
    base = (0.62, 0.03, 0.05)
    img = _background()
    small = _box((100, 119, 80, 98))                        # 속 15 x 14 = 210 화소
    _paint(img, small, base)
    _paint(img, _box((108, 110, 86, 92)), (0.62, 0.16, 0.05))               # 다른 색 12 화소
    measure = G.measure_garment_color(img, small)
    assert measure["same"] == pytest.approx(198 / 210)
    assert (measure["cw_c"], measure["multicolor"]) == (None, True)
    assert measure["center"] == measure["first_pass_center"]
    mid = _box((100, 140, 80, 120))                         # 속 36 x 36 = 1,296 화소
    _paint(img, mid, base, _folds(0.04, x0=80))
    assert G.measure_garment_color(img, mid)["multicolor"] is False


# ── 라. 기준 색 ─────────────────────────────────────────────────────────────

A = np.array((0.60, 0.02, 0.04))


def _studio(*centers):
    return [(f"s{i}", "studio", c) for i, c in enumerate(centers)]


def test_ra1_studio_count_and_disagreement():
    """라.1, 지시서 8: 스튜디오 2장이면 기준 없음. 4장 중 1장이 튀면 3장으로 잡는다. 3장 중 1장이 튀면 기준 없음."""
    far = A + [0.0, 0.05, 0.0]
    two = G.resolve_reference(_studio(A, A), upload_color=A)
    assert (two["color"], two["reason"]) == (None, "no_reference_few_studio")

    four = G.resolve_reference(_studio(A, A + [0.004, 0, 0], A - [0.004, 0, 0], far), upload_color=A)
    assert four["source"] == "studio" and four["color"] == pytest.approx(A.tolist())
    assert (four["studio_used"], four["studio_dropped_keys"]) == (3, ["s3"])

    three = G.resolve_reference(_studio(A, A, far), upload_color=A)
    assert (three["color"], three["reason"]) == (None, "no_reference_studio_disagree")
    assert three["studio_dropped_keys"] == ["s2"]


def test_ra2_product_and_styling_photos_do_not_set_the_reference():
    """라.2: 제품 사진과 장면 사진은 기준 계산에 들지 않는다."""
    elsewhere = A + [0.05, 0.01, 0.01]
    others = [("p0", "product", elsewhere), ("p1", "product", elsewhere), ("t0", "styling", elsewhere),
              ("t1", "styling", elsewhere)]
    mixed = G.resolve_reference(_studio(A, A, A) + others, upload_color=A)
    assert mixed["color"] == pytest.approx(A.tolist()) and mixed["studio_used"] == 3
    few = G.resolve_reference(_studio(A, A) + others, upload_color=A)
    assert few["reason"] == "no_reference_few_studio"


def test_ra3_upload_color_check_on_chroma():
    """라.3: 올린 색과 색 방향 차이 0.01이면 A 그대로, 0.03이면 R에서 0.02 자리, 0.06이면 기준 없음과 다시 그리기 표시."""
    near = G.resolve_reference(_studio(A, A, A), upload_color=A - [0, 0.01, 0])
    assert near["color"] == pytest.approx(A.tolist()) and near["pulled"] is False
    assert near["upload_d_ab"] == pytest.approx(0.01)

    upload = A - [0, 0.03, 0]
    pulled = G.resolve_reference(_studio(A, A, A), upload_color=upload)
    assert pulled["pulled"] is True
    assert pulled["color"] == pytest.approx([A[0], upload[1] + 0.02, A[2]])
    assert math.hypot(pulled["color"][1] - upload[1], pulled["color"][2] - upload[2]) == pytest.approx(0.02)

    off = G.resolve_reference(_studio(A, A, A), upload_color=A - [0, 0.06, 0])
    assert (off["color"], off["reason"], off["redraw_candidate"]) == (None, "reference_off_upload", True)


def test_ra3_upload_chroma_is_scaled_to_the_studio_lightness_before_the_check():
    """라.3, 14.4절 1번(14.1절 3번을 고침): 진하기가 밝기를 그대로 따라간 옷은 올린 색의 진하기를 밝기 비율(A_L / R_L)로 맞춰 견준다.

    같은 천인데 스튜디오가 더 밝아 진하기만 커진 경우에는 끌어오지 않는다. 색 방향이 정말 다르면 전처럼 끌어오거나 기준 없음이다.
    """
    upload = np.array((0.45, 0.03, 0.06))
    fabric = upload * (0.60 / 0.45)                         # 같은 천을 밝게 찍은 색 (0.60, 0.04, 0.08)
    assert float(G.chroma_distance(fabric, upload)) == pytest.approx(0.0224, abs=1e-4), "그대로 견주면 끌어오는 차이다"
    same = G.resolve_reference(_studio(fabric, fabric, fabric), upload_color=upload)
    assert same["upload_d_ab"] == pytest.approx(0.0, abs=1e-12) and same["pulled"] is False
    assert same["color"] == pytest.approx(fabric.tolist()) and same["upload_l_diff"] == pytest.approx(0.15)
    img, mask = _garment(fabric)
    batch = G.correct_color_option([_photo(f"s{i}", img, mask) for i in range(3)], upload_color=upload).record
    assert batch["upload_check"]["pulled"] is False and batch["upload_check"]["d_ab"] < 0.003
    assert batch["reference_color"] == batch["studio"]["center"]

    # 색 방향이 정말 다르면 밝기가 달라도 끌어온다. 끌어오는 자리는 진하기를 맞춘 올린 색에서 0.02 떨어진 곳이다.
    tinted = fabric + [0.0, 0.03, 0.0]
    pulled = G.resolve_reference(_studio(tinted, tinted, tinted), upload_color=upload)
    assert pulled["upload_d_ab"] == pytest.approx(0.03) and pulled["pulled"] is True
    assert pulled["color"] == pytest.approx((fabric + [0.0, 0.02, 0.0]).tolist())
    far = fabric + [0.0, 0.06, 0.0]
    off = G.resolve_reference(_studio(far, far, far), upload_color=upload)
    assert (off["color"], off["reason"], off["redraw_candidate"]) == (None, "reference_off_upload", True)
    assert off["upload_d_ab"] == pytest.approx(0.06)

    # 밝기 비율은 0.5와 2.0에서 잘린다. 진하기 배율은 그 안에서만 고른다.
    assert G.UPLOAD_CHROMA_SCALE_RANGE == (0.5, 2.0)
    dark = G.resolve_reference(_studio(A, A, A), upload_color=(0.20, 0.005, 0.01))      # 비율 3.0 을 2.0 으로. 진하기는 4배다
    assert dark["upload_d_ab"] == pytest.approx(0.5 * math.hypot(0.02, 0.04)) and dark["pulled"] is True, "안 자르면 0.0112 라 그대로다"
    low = A * [0.5, 1.0, 1.0]                                                           # 밝기 0.30
    bright = G.resolve_reference(_studio(low, low, low), upload_color=(0.90, 0.06, 0.12))   # 비율 0.33 을 0.5 로. 진하기는 3분의 1이다
    assert bright["upload_d_ab"] == pytest.approx(0.5 * math.hypot(0.02, 0.04)), "안 자르면 0 이다"


def _upload_check(center, upload):
    """스튜디오 사진 세 장의 가운데 색이 center 일 때 올린 색 upload 와 대조한 결과."""
    return G.resolve_reference(_studio(center, center, center), upload_color=upload)


def _upload_scale_by_the_book(center, upload):
    """14.4절 1번의 식을 그대로 적은 것. 진하기 배율 k, 밝기 비율 s, 색 방향 차이 d 를 돌려준다."""
    s = min(max(center[0] / max(upload[0], 1e-6), 0.5), 2.0)
    a, r = np.asarray(center[1:], dtype=np.float64), np.asarray(upload[1:], dtype=np.float64)
    k = 1.0 if r @ r < 1e-12 else min(max((a @ r) / (r @ r), min(1.0, s)), max(1.0, s))
    return k, s, float(np.hypot(*(a - k * r)))


def test_ra3_upload_chroma_scale_is_picked_between_one_and_the_lightness_ratio():
    """라.3, 14.4절 1번: 올린 색의 진하기 배율 k 는 1배와 밝기 비율 s 사이에서 스튜디오 가운데 색에 가장 가까운 값이다.

    진하기가 밝기를 그대로 따라간 옷도, 따라가지 않은 옷도 끌어오지 않는다. s 하나만 곱하던 때는 뒤쪽 옷이 새로 끌려왔다.
    """
    # 진하기가 밝기를 그대로 따라간 옷. 어둡게 찍힌 올린 색과 같은 천을 1.48배 밝게 찍은 스튜디오다(핫핑크 블라우스 비슷하게).
    upload = np.array((0.417, 0.0507, 0.0261))
    studio = upload * 1.48
    assert float(G.chroma_distance(studio, upload)) == pytest.approx(0.0274, abs=1e-4), "그대로 견주면 끌어오는 차이다"
    followed = _upload_check(studio, upload)
    assert followed["upload_d_ab"] == pytest.approx(0.0, abs=1e-12) and followed["pulled"] is False
    assert followed["color"] == pytest.approx(studio.tolist())

    # 진하기가 밝기를 따라가지 않은 옷. 밝기 비율 1.47 에 진하기는 거의 같다(핑크 레이스 비슷하게). d 는 맞추기 전 값 그대로다.
    upload, studio = np.array((0.602, 0.0245, 0.0)), np.array((0.884, 0.0190, 0.0143))
    unscaled = float(G.chroma_distance(studio, upload))
    assert unscaled == pytest.approx(0.0153, abs=1e-4)
    stayed = _upload_check(studio, upload)
    assert stayed["upload_d_ab"] == pytest.approx(unscaled) and stayed["pulled"] is False, "s 를 곱하면 0.0222 라 끌어온다"
    assert stayed["color"] == pytest.approx(studio.tolist())
    # 밝기만 1.5배이고 색은 같은 옷도 그대로다. s 를 곱하면 0.0224 였다.
    same_color = _upload_check(A, (0.40, 0.02, 0.04))
    assert same_color["upload_d_ab"] == pytest.approx(0.0, abs=1e-12) and same_color["pulled"] is False
    # 진한 옷(진하기 0.17)은 올린 사진이 0.30 밝다는 것만으로 기준 없음이 되지 않는다. s 를 곱하면 0.073 이었다.
    deep = np.array((0.40, 0.15, 0.08))
    brighter_upload = _upload_check(deep, deep + [0.30, 0.0, 0.0])
    assert brighter_upload["upload_d_ab"] == pytest.approx(0.0, abs=1e-12) and brighter_upload["reason"] is None

    # 진하기가 그 사이인 옷. 밝기 비율 1.5, 진하기 1.3배. k 는 1.3 이고 R_ab 에 수직인 성분 0.01 만 남는다.
    upload = np.array((0.40, 0.03, 0.0))
    between = _upload_check((0.60, 0.039, 0.01), upload)
    assert between["upload_d_ab"] == pytest.approx(0.01) and between["pulled"] is False, "k 가 1 이면 0.0135, s 면 0.0117 이다"
    assert _upload_scale_by_the_book((0.60, 0.039, 0.01), upload) == pytest.approx((1.3, 1.5, 0.01))
    # 스튜디오가 s 배보다 더 진하면 k 는 s 에서, 올린 색보다 연하면 1 에서 멈춘다. 넘는 만큼은 차이로 남는다.
    assert _upload_check((0.60, 0.055, 0.0), upload)["upload_d_ab"] == pytest.approx(0.055 - 1.5 * 0.03)
    assert _upload_check((0.60, 0.020, 0.0), upload)["upload_d_ab"] == pytest.approx(0.03 - 0.020)


@pytest.mark.parametrize("apart,pulled,reason", [(0.03, True, None), (0.06, False, "reference_off_upload")])
def test_ra3_hue_angle_difference_is_not_forgiven_at_any_lightness_ratio(apart, pulled, reason):
    """라.3, 14.4절 1번: 진하기는 같고 색상 각도만 달라 0.03, 0.06 떨어진 옷은 밝기 비율이 무엇이든 끌어오기와 기준 없음이 그대로 걸린다."""
    chroma = 0.08
    angle = 2 * math.asin(apart / (2 * chroma))
    studio = np.array((0.60, chroma * math.cos(angle), chroma * math.sin(angle)))
    for upload_l in (0.60, 0.45, 0.20, 0.75, 0.95):           # 밝기 비율 1.0, 1.33, 3.0(2.0 으로 자름), 0.8, 0.63
        ref = _upload_check(studio, (upload_l, chroma, 0.0))
        if upload_l <= 0.60:
            assert ref["upload_d_ab"] == pytest.approx(apart), "스튜디오가 같거나 더 밝으면 배율은 1 이고 d 는 맞추기 전 값이다"
        else:
            # 스튜디오가 더 어두우면 진하기 쪽 차이만 빠진다. R_ab 에 수직인 성분은 줄지 않는다.
            assert chroma * math.sin(angle) - 1e-12 <= ref["upload_d_ab"] <= apart + 1e-12
        assert (ref["pulled"], ref["reason"], ref["color"] is None) == (pulled, reason, reason is not None), upload_l


def test_ra3_upload_chroma_scale_works_the_same_when_the_studio_is_darker():
    """라.3, 14.4절 1번: 스튜디오가 더 어두운 쪽(s 가 1 미만)에서도 대칭이다. 배율은 s 와 1 사이에서 고른다."""
    upload = np.array((0.80, 0.04, 0.08))
    followed = _upload_check(upload * 0.75, upload)           # 같은 천을 어둡게 찍었다. k = s = 0.75
    assert followed["upload_d_ab"] == pytest.approx(0.0, abs=1e-12) and followed["pulled"] is False
    same_color = _upload_check((0.60, 0.04, 0.08), upload)    # 밝기만 어둡고 색은 같다. k = 1
    assert same_color["upload_d_ab"] == pytest.approx(0.0, abs=1e-12) and same_color["pulled"] is False

    upload = np.array((0.80, 0.04, 0.0))
    between = _upload_check((0.60, 0.034, 0.01), upload)      # 진하기 0.85배. k = 0.85 이고 수직 성분 0.01 만 남는다
    assert between["upload_d_ab"] == pytest.approx(0.01) and between["pulled"] is False
    assert _upload_scale_by_the_book((0.60, 0.034, 0.01), upload) == pytest.approx((0.85, 0.75, 0.01))
    assert _upload_check((0.60, 0.020, 0.0), upload)["upload_d_ab"] == pytest.approx(0.75 * 0.04 - 0.020)   # s 에서 멈춘다
    assert _upload_check((0.60, 0.050, 0.0), upload)["upload_d_ab"] == pytest.approx(0.050 - 0.04)          # 1 에서 멈춘다
    pulled = _upload_check((0.60, 0.034, 0.03), upload)       # k = 0.85, R*_ab = (0.034, 0), 수직 성분 0.03
    assert pulled["pulled"] is True and pulled["color"] == pytest.approx([0.60, 0.034, 0.02])


def test_ra3_upload_chroma_scale_stops_at_the_low_end_when_the_colors_point_opposite_ways():
    """라.3, 14.4절 1번: 올린 색과 스튜디오 색의 내적이 음수면(흰 옷, 회색 옷은 a, b 부호가 쉽게 뒤집힌다) k 는 min(1, s)에서 멈춘다."""
    brighter = _upload_check((0.90, -0.012, 0.0), (0.60, 0.006, 0.0))        # s = 1.5, k = 1
    assert brighter["upload_d_ab"] == pytest.approx(0.018) and brighter["pulled"] is False
    darker = _upload_check((0.48, -0.012, 0.0), (0.60, 0.006, 0.0))          # s = 0.8, k = 0.8
    assert darker["upload_d_ab"] == pytest.approx(0.0168) and darker["pulled"] is False


def test_ra3_achromatic_upload_color_has_no_scale_to_pick():
    """라.3, 14.4절 1번: 올린 색이 무채색(R_ab 가 0)이면 k = 1 이고 d 는 스튜디오 진하기 그대로다. 0으로 나누지 않는다."""
    chroma = math.hypot(0.02, 0.04)
    for upload_l in (0.0, 0.30, 0.60, 0.95):
        grey = _upload_check(A, (upload_l, 0.0, 0.0))
        assert grey["upload_d_ab"] == pytest.approx(chroma) and grey["pulled"] is True
        assert grey["color"] == pytest.approx([0.60, *(A[1:] * 0.02 / chroma)])
    # R_ab · R_ab 가 1e-12 미만이면 k = 1 이다. 그 위에서는 1 과 s(여기서는 2) 사이에서 고른다.
    below = _upload_check(A, (0.30, 6e-7, 0.0))
    assert below["upload_d_ab"] == pytest.approx(math.hypot(0.02 - 6e-7, 0.04), abs=1e-9)
    above = _upload_check(A, (0.30, 2e-6, 0.0))
    assert above["upload_d_ab"] == pytest.approx(math.hypot(0.02 - 2 * 2e-6, 0.04), abs=1e-9)


def test_ra3_pulled_reference_sits_at_the_keep_distance_from_the_scaled_upload_color():
    """라.3, 14.4절 1번: 끌어온 기준은 배율을 곱한 올린 색(R*_ab)에서 0.02 떨어진 자리다. 곱하기 전의 올린 색에서가 아니다."""
    upload = np.array((0.40, 0.03, 0.0))
    pulled = _upload_check((0.60, 0.039, 0.03), upload)       # k = 1.3, R*_ab = (0.039, 0), 수직 성분 0.03
    assert pulled["upload_d_ab"] == pytest.approx(0.03) and pulled["pulled"] is True
    assert pulled["color"] == pytest.approx([0.60, 0.039, 0.02])
    # k 가 s 에서 멈춘 경우. R*_ab = (0.045, 0) 이고 기준은 거기서 스튜디오 가운데 색 쪽으로 0.02 간 자리다.
    studio = np.array((0.60, 0.07, 0.03))
    capped = _upload_check(studio, upload)
    scaled = 1.5 * upload[1:]
    assert capped["upload_d_ab"] == pytest.approx(math.hypot(0.025, 0.03)) and capped["pulled"] is True
    offset = np.array(capped["color"][1:]) - scaled
    assert float(np.hypot(*offset)) == pytest.approx(0.02)
    assert offset == pytest.approx((studio[1:] - scaled) * 0.02 / math.hypot(0.025, 0.03))
    assert capped["color"][0] == 0.60


@pytest.mark.parametrize("upload_l", [0.40, 0.60, 0.85, 0.0])
def test_ra3_upload_check_distance_does_not_jump_as_the_upload_chroma_changes(upload_l):
    """라.3, 14.4절 1번: 올린 색의 진하기를 조금씩 바꿔도 d 가 한 칸에 크게 뛰지 않는다.

    k 가 1 이나 s 에서 멈추는 자리, 그 사이, R_ab 가 0인 자리를 모두 지난다. 한 칸의 변화는 배율 최댓값(2) 곱하기 칸 크기를 넘지 않는다.
    """
    studio = np.array((0.60, 0.035, 0.02))
    direction = np.array((math.cos(0.3), math.sin(0.3)))      # 스튜디오와 색상 각도가 조금 다른 방향
    step = 0.0005
    chromas = np.sort(np.concatenate([[0.0, 5e-7, 1e-6, 2e-6, 1e-5, 1e-4], np.arange(step, 0.12, step)]))
    uploads = [(upload_l, *(chroma * direction)) for chroma in chromas]
    distances = [_upload_check(studio, upload)["upload_d_ab"] for upload in uploads]
    book = [_upload_scale_by_the_book(studio, upload) for upload in uploads]
    assert distances == pytest.approx([d for _k, _s, d in book], abs=1e-12)
    for before, after, gap in zip(distances, distances[1:], np.diff(chromas)):
        assert abs(after - before) <= 2.0 * gap + 2e-6, "R_ab 가 0에 가까운 자리에서 배율이 바뀌어도 2e-6 안이다"
    scale = book[-1][1]
    picked = {round(k, 9) for k, _s, _d in book}
    assert {1.0, round(scale, 9)} <= picked, "양쪽 끝에서 멈춘 자리를 지났다"
    assert scale == 1.0 or any(min(1.0, scale) < k < max(1.0, scale) for k in picked), "사이 값도 지났다"


def test_ra4_upload_lightness_is_recorded_and_never_moves_the_reference():
    """라.4, 14.1절 2번: 기준 밝기는 늘 A의 밝기다. 올린 색이 더 밝든 어둡든 A_L - R_L 만 기록하고 기준을 올리지 않는다."""
    for off in (0.06, 0.25, -0.10):
        ref = G.resolve_reference(_studio(A, A, A), upload_color=A + [off, 0, 0])
        assert ref["source"] == "studio" and ref["color"] == pytest.approx(A.tolist())
        assert ref["upload_l_diff"] == pytest.approx(-off)
        assert "raised" not in ref, "올림 기록 항목은 없다"
    assert not hasattr(G, "UPLOAD_LIGHTNESS_FLOOR"), "올림 상수도 없다"


@pytest.mark.parametrize("brighter", [0.12, 0.20, 0.25])
def test_ra4_brighter_upload_color_does_not_move_the_studio_photos(brighter):
    """14.1절 2번: 올린 색이 스튜디오보다 0.12, 0.20, 0.25 밝아도 서로 잘 맞는 스튜디오 사진은 옮겨지지 않는다.

    전에는 기준 밝기를 올려서 네 장이 모두 밝아졌다(0.20 과 0.25 에서 +0.10). 올린 사진의 밝기는 찍은 조명을 따라가므로 기록만 한다.
    """
    base = np.array((0.60, 0.02, 0.04))
    pictures = [_garment(base + off) for off in ([0, 0, 0], [0.01, 0, 0], [-0.01, 0, 0], [0.005, 0.002, 0])]
    photos = [_photo(f"s{i}", img, mask) for i, (img, mask) in enumerate(pictures)]
    result = G.correct_color_option(photos, upload_color=base + [brighter, 0.0, 0.0])
    assert _outcomes(result) == [("untouched", "within_tolerance")] * 4
    assert all(out.image is given.image for out, given in zip(result.photos, photos))
    assert [p.record["plan"]["d_l"] for p in result.photos] == [0.0] * 4
    record = result.record
    assert record["reference_source"] == "studio"
    assert record["reference_color"][0] == record["studio"]["center"][0], "기준 밝기는 스튜디오 가운데 밝기 그대로다"
    assert record["upload_check"]["l_diff"] == pytest.approx(-brighter, abs=0.005)
    assert set(record["upload_check"]) == {"d_ab", "pulled", "l_diff"}


def test_ra5_seller_color_is_the_absolute_reference():
    """라.5, 지시서 8: 셀러 색이 있으면 그것이 기준이다. 스튜디오 사진이 없어도 되고 올린 색도 보지 않는다."""
    seller = (0.70, -0.01, 0.02)
    for eligible in ([], _studio(A, A, A)):
        chosen = G.resolve_reference(eligible, upload_color=A, seller_color=seller)
        assert (chosen["source"], chosen["color"], chosen["reason"]) == ("seller", list(seller), None)


def test_ra6_no_upload_color_and_no_seller_color_means_no_reference():
    """라.6: 올린 색과 셀러 색이 둘 다 없으면 기준 없음."""
    nothing = G.resolve_reference(_studio(A, A, A))
    assert (nothing["source"], nothing["color"]) == (None, None)
    assert nothing["reason"] == "no_reference_no_upload_color"


@pytest.mark.parametrize("scenario,reason,redraw", [
    ("two_studio", "no_reference_few_studio", False),
    ("studio_disagree", "no_reference_studio_disagree", False),
    ("no_upload", "no_reference_no_upload_color", False),
    ("off_upload", "reference_off_upload", True),
])
def test_ra_batch_without_a_reference_is_left_alone(scenario, reason, redraw):
    """지시서 8: 스튜디오 사진이 모자라거나 서로 어긋나면 기준을 잡지 않고, 그 색상 옵션의 사진은 모두 그대로다."""
    base = np.array((0.60, 0.02, 0.04))
    img, mask = _garment(base)
    drift, _ = _garment(base + [0.0, 0.04, 0.0])            # 형제로는 통과하지만 스튜디오끼리는 어긋난다
    far, _ = _garment(base + [0.18, 0.0, 0.0])              # 기준이 있었다면 옮겨졌을 장면 사진
    photos = {
        "two_studio": [_photo("s0", img, mask), _photo("s1", img, mask), _photo("t", far, mask, "styling")],
        "studio_disagree": [_photo("s0", img, mask), _photo("s1", img, mask), _photo("s2", drift, mask),
                            _photo("t", far, mask, "styling")],
        "no_upload": [_photo("s0", img, mask), _photo("s1", img, mask), _photo("s2", img, mask),
                      _photo("t", far, mask, "styling")],
        "off_upload": [_photo("s0", img, mask), _photo("s1", img, mask), _photo("s2", img, mask),
                       _photo("t", far, mask, "styling")],
    }[scenario]
    upload = {"no_upload": None, "off_upload": base + [0.0, -0.06, 0.0]}.get(scenario, base)
    result = G.correct_color_option(photos, upload_color=upload)
    assert _outcomes(result) == [("skipped", reason)] * len(photos)
    assert all(("redraw_candidate" in p.record["flags"]) is redraw for p in result.photos)
    assert all(out.image is given.image for out, given in zip(result.photos, photos))
    assert (result.record["reference_color"], result.record["reference_reason"]) == (None, reason)
    assert result.record["redraw_candidate"] is redraw


def test_ra_studio_reference_moves_only_the_outlier():
    """지시서 3: 스튜디오 사진 셋의 가운데 색이 기준이 되고, 크게 튄 장면 사진만 기준 쪽으로 옮겨진다."""
    base = np.array((0.60, 0.02, 0.04))
    img, mask = _garment(base)
    far, _ = _garment(base + [0.18, 0.0, 0.0])
    photos = [_photo("s0", img, mask), _photo("s1", img, mask), _photo("s2", img, mask),
              _photo("t", far, mask, "styling")]
    result = G.correct_color_option(photos, upload_color=base)
    assert result.record["reference_source"] == "studio"
    assert result.record["reference_color"] == pytest.approx(base.tolist(), abs=0.003)
    assert [p.record["status"] for p in result.photos] == ["untouched"] * 3 + ["corrected"]
    assert all(out.image is given.image for out, given in zip(result.photos[:3], photos[:3]))
    moved = _center(result.photos[3].image, mask)
    reference = result.record["reference_color"]
    assert moved[0] - reference[0] == pytest.approx(STYLING["lightness_tolerance"], abs=0.003)


def test_ra1_studio_center_is_a_median_taken_again_after_dropping():
    """라.1, 5.2절: 스튜디오 가운데 색은 가운데값이고, 튀는 사진을 뺀 뒤 다시 구한다. 튀는 사진은 가중 거리 0.03으로 가린다."""
    ref = G.resolve_reference(_studio(A, A + [0, 0.002, 0], A + [0, 0.010, 0], A + [0, 0.060, 0]), upload_color=A)
    assert (ref["studio_used"], ref["studio_dropped_keys"]) == (3, ["s3"])
    assert ref["color"] == pytest.approx((A + [0, 0.002, 0]).tolist()), "남은 셋의 가운데값(평균 0.004, 빼기 전 가운데값 0.006)"
    # 처음 구하는 가운데 색도 가운데값이다. 평균이면 0.035 떨어진 사진이 0.026 으로 보여 안 빠진다.
    ref = G.resolve_reference(_studio(A, A, A, A + [0, 0.035, 0]), upload_color=A)
    assert (ref["studio_used"], ref["studio_dropped_keys"]) == (3, ["s3"])
    kept = G.resolve_reference(_studio(A, A, A, A + [0, 0.025, 0]), upload_color=A)
    assert (kept["studio_used"], kept["studio_dropped_keys"]) == (4, [])
    # 밝기만 0.10 다른 사진은 가중 거리 0.035 라 빠지고, 0.07 다른 사진(0.0245)은 남는다.
    light = G.resolve_reference(_studio(A, A, A, A + [0.10, 0, 0]), upload_color=A)
    assert light["studio_dropped_keys"] == ["s3"]
    assert G.resolve_reference(_studio(A, A, A, A + [0.07, 0, 0]), upload_color=A)["studio_dropped_keys"] == []


def test_ra3_upload_check_boundaries():
    """라.3, 5.3절: 색 방향 0.02와 0.05의 양쪽."""
    def ref(d_ab=0.0):
        return G.resolve_reference(_studio(A, A, A), upload_color=A - [0.0, d_ab, 0])

    assert ref(d_ab=0.018)["pulled"] is False and ref(d_ab=0.022)["pulled"] is True
    assert ref(d_ab=0.045)["pulled"] is True and ref(d_ab=0.045)["color"] is not None
    assert ref(d_ab=0.055)["reason"] == "reference_off_upload"


def test_ra_batch_record_carries_the_reference_story():
    """9.2절: 묶음 기록에 쓴 스튜디오 사진 수, 뺀 사진의 열쇠, 스튜디오 가운데 색, 올린 색과의 대조가 값으로 남는다."""
    base = np.array((0.60, 0.02, 0.04))
    img, mask = _garment(base)
    drift, _ = _garment(base + [0.0, 0.04, 0.0])            # 형제로는 통과(0.05 안), 스튜디오끼리는 튄다(0.03 밖)
    upload = base - [0.0, 0.03, 0.0]                        # 색 방향 0.03 이라 끌어온다. 밝기는 같다
    result = G.correct_color_option(
        [_photo("s0", img, mask), _photo("s1", img, mask), _photo("s2", img, mask), _photo("s3", drift, mask)],
        upload_color=upload)
    record = result.record
    assert record["studio"]["used"] == 3 and record["studio"]["dropped_keys"] == ["s3"]
    assert record["studio"]["center"] == pytest.approx(base.tolist(), abs=0.003)
    check = record["upload_check"]
    assert set(check) == {"d_ab", "pulled", "l_diff"} and check["pulled"] is True
    assert check["d_ab"] == pytest.approx(0.03, abs=0.003) and check["l_diff"] == pytest.approx(0.0, abs=0.003)
    assert record["reference_color"] == pytest.approx([base[0], upload[1] + 0.02, base[2]], abs=0.003)
    odd = result.photos[3].record["measure"]
    assert odd["sibling_count"] == 3
    assert odd["sibling_d_l"] == pytest.approx(0.0, abs=0.003) and odd["sibling_d_ab"] == pytest.approx(0.04, abs=0.003)
    # 올린 색이 0.06 밝은 묶음. 밝기 차이는 기록만 하고 기준 밝기는 스튜디오 가운데 밝기 그대로다.
    brighter = G.correct_color_option([_photo(f"s{i}", img, mask) for i in range(3)],
                                      upload_color=base + [0.06, 0.0, 0.0]).record
    assert brighter["upload_check"]["pulled"] is False
    assert brighter["upload_check"]["l_diff"] == pytest.approx(-0.06, abs=0.003)
    assert brighter["reference_color"] == brighter["studio"]["center"]


def test_ra_untrusted_upload_color_is_still_used_for_the_reference_check():
    """5.3절, 14.3절: 올린 색 신뢰(2.2절 5번)는 후보 고르기에만 쓰고, 기준 색 대조에는 믿지 않는 색도 그대로 쓴다.

    밝기는 기록만 하므로(14.1절 2번) 믿지 않는 올린 색이 0.25 밝아도 스튜디오 사진은 그대로다. 전에는 세 장이 +0.10 옮겨졌다.
    색 방향 대조는 믿지 않는 색으로도 걸린다. 믿지 않는 올린 색으로는 기준을 잡지 않기로 정하면 이 테스트를 함께 고친다.
    """
    base = np.array((0.60, 0.02, 0.04))
    img, mask = _garment(base)
    photos = [_photo(f"s{i}", img, mask) for i in range(3)]
    result = G.correct_color_option(photos, upload_color=base + [0.25, 0.0, 0.0])    # 가중 거리 0.0875
    assert result.record["upload_trust"]["trusted"] is False
    assert result.record["reference_source"] == "studio"
    assert result.record["upload_check"]["l_diff"] == pytest.approx(-0.25, abs=0.003)
    assert result.record["reference_color"][0] == result.record["studio"]["center"][0]
    assert _outcomes(result) == [("untouched", "within_tolerance")] * 3
    off = G.correct_color_option(photos, upload_color=base + [0.0, 0.09, 0.0])       # 가중 거리 0.09, 색 방향 0.09
    assert off.record["upload_trust"]["trusted"] is False
    assert _outcomes(off) == [("skipped", "reference_off_upload")] * 3


# ── 마. 옮길 양 ─────────────────────────────────────────────────────────────

def test_ma1_photos_within_tolerance_come_back_byte_identical():
    """마.1, 지시서 2: 허용 폭 안의 사진은 바이트 그대로 돌아온다."""
    base = np.array((0.60, 0.02, 0.04))
    offsets = ([0, 0, 0], [0.03, 0.005, 0], [-0.03, 0, 0.01], [0.04, -0.01, 0.01])
    pictures = [_garment(base + offset) for offset in offsets]
    photos = [_photo(f"s{i}", img, mask) for i, (img, mask) in enumerate(pictures[:3])]
    photos.append(_photo("t", *pictures[3], "styling"))
    originals = [p.image.copy() for p in photos]
    result = G.correct_color_option(photos, upload_color=base)
    assert _outcomes(result) == [("untouched", "within_tolerance")] * 4
    for out, given, original in zip(result.photos, photos, originals):
        assert out.image is given.image
        assert np.array_equal(out.image, original), "입력 배열도 건드리지 않는다"
    assert result.record["status_counts"] == {"corrected": 0, "untouched": 4, "skipped": 0, "reverted": 0}


@pytest.mark.parametrize("color", [(0.62, 0.03, 0.05), (0.30, 0.0, 0.0), (0.85, 0.0, 0.01),
                                   (0.50, 0.12, 0.06)])
@pytest.mark.parametrize("sign", [1, -1])
def test_ma2_no_jump_at_the_lightness_tolerance_edge(color, sign):
    """마.2, 지시서 4: 허용 폭보다 0.004 안쪽인 사진은 그대로이고, 0.004 바깥쪽인 사진은 화소마다 2단계 안으로만 바뀐다."""
    img, mask = _garment(color)
    center = _center(img, mask)
    edge = STUDIO["lightness_tolerance"]
    inside = _triplet(img, mask, seller_color=center - [sign * (edge - 0.004), 0, 0]).photos[0]
    assert inside.record["reason"] == "within_tolerance" and inside.image is img
    outside = _triplet(img, mask, seller_color=center - [sign * (edge + 0.004), 0, 0]).photos[0]
    assert outside.record["status"] == "corrected"
    assert outside.record["plan"]["d_l"] == pytest.approx(-sign * 0.004, abs=1e-9)
    step = np.abs(outside.image.astype(int) - img.astype(int))
    assert 1 <= step.max() <= 2


@pytest.mark.parametrize("direction,max_step", [
    ((1.0, 0.0), 2), ((-1.0, 0.0), 2),
    # 명세의 "2단계 안"은 b축에서 성립하지 않는다. 밝기 0.62 인 옷에서 b 를 0.004 옮기면 파란 채널이 3단계 바뀐다.
    ((0.0, 1.0), 3), ((0.0, -1.0), 3),
])
def test_ma2_no_jump_at_the_chroma_tolerance_edge(direction, max_step):
    """마.2, 지시서 4: 색 방향도 허용 폭 끝에서 0에서 시작한다. 0.004 바깥쪽의 변화는 a축 2단계, b축 3단계다."""
    img, mask = _garment((0.62, 0.03, 0.05))
    center = _center(img, mask)
    edge = STUDIO["chroma_tolerance"]
    away = np.array([0.0, *direction])
    inside = _triplet(img, mask, seller_color=center + (edge - 0.004) * away).photos[0]
    assert inside.record["reason"] == "within_tolerance" and inside.image is img
    outside = _triplet(img, mask, seller_color=center + (edge + 0.004) * away).photos[0]
    assert outside.record["status"] == "corrected"
    assert outside.record["plan"]["d_ab"] == pytest.approx((0.004 * away[1:]).tolist(), abs=1e-9)
    assert np.abs(outside.image.astype(int) - img.astype(int)).max() == max_step


@pytest.mark.parametrize("sign", [1, -1])
def test_ma3_outlier_lands_on_the_lightness_tolerance_edge(sign):
    """마.3, 지시서 3: 크게 튄 사진은 기준 쪽으로 옮겨지고, 다시 재면 허용 폭 끝에서 0.003 안이다."""
    img, mask = _garment((0.62, 0.03, 0.05))
    center = _center(img, mask)
    target = center - [sign * 0.09, 0, 0]
    photo = _triplet(img, mask, seller_color=target).photos[0]
    assert photo.record["status"] == "corrected" and photo.record["flags"] == []
    assert photo.record["plan"]["d_l"] == pytest.approx(-sign * (0.09 - STUDIO["lightness_tolerance"]))
    again = _center(photo.image, mask)
    assert sign * (again[0] - target[0]) == pytest.approx(STUDIO["lightness_tolerance"], abs=0.003)
    assert abs(again[0] - target[0]) < abs(center[0] - target[0])


def test_ma3_outlier_lands_on_the_chroma_tolerance_edge():
    """마.3, 지시서 3: 색 방향이 튄 사진도 허용 폭 끝에서 0.003 안으로 온다."""
    img, mask = _garment((0.62, 0.03, 0.05))
    center = _center(img, mask)
    target = center + [0.0, 0.021, -0.028]                 # 색 방향 0.035
    photo = _triplet(img, mask, seller_color=target).photos[0]
    assert photo.record["status"] == "corrected" and photo.record["plan"]["d_l"] == 0.0
    again = _center(photo.image, mask)
    assert float(G.chroma_distance(again, target)) == pytest.approx(STUDIO["chroma_tolerance"], abs=0.003)


def test_ma4_lightness_and_chroma_are_judged_separately():
    """마.4: 밝기만 벗어난 무채색 옷은 색 방향 이동이 0이다. 색 방향만 벗어난 옷은 밝기 이동이 0이다."""
    grey, mask = _garment((0.60, 0.0, 0.0))
    center = _center(grey, mask)
    plan = _triplet(grey, mask, seller_color=center - [0.09, 0, 0]).photos[0].record["plan"]
    assert plan["d_l"] == pytest.approx(-0.037) and plan["d_ab"] == [0.0, 0.0]

    tinted, mask = _garment((0.62, 0.03, 0.05))
    center = _center(tinted, mask)
    photo = _triplet(tinted, mask, seller_color=center + [0.0, 0.035, 0.0]).photos[0]
    assert photo.record["plan"]["d_l"] == 0.0
    assert photo.record["plan"]["d_ab"] == pytest.approx([0.013, 0.0], abs=1e-9)
    assert _center(photo.image, mask)[0] == pytest.approx(center[0], abs=0.003)


def test_ma5_tolerance_depends_on_the_photo_kind():
    """마.5: 같은 차이(밝기 0.08)가 스튜디오에서는 옮겨지고 장면과 제품에서는 그대로다."""
    img, mask = _garment((0.62, 0.03, 0.05))
    result = G.correct_color_option(
        [_photo("studio", img, mask, "studio"), _photo("styling", img, mask, "styling"),
         _photo("product", img, mask, "product")], seller_color=_center(img, mask) - [0.08, 0, 0])
    studio, styling, product = (p.record for p in result.photos)
    assert studio["status"] == "corrected" and studio["plan"]["d_l"] == pytest.approx(-0.027)
    assert (styling["status"], styling["reason"]) == ("untouched", "within_tolerance")
    assert (product["status"], product["reason"]) == ("untouched", "within_tolerance")
    assert (studio["plan"]["lightness_tolerance"], styling["plan"]["lightness_tolerance"]) == (0.053, 0.13)


@pytest.mark.parametrize("kind,cap", [("studio", 0.10), ("styling", 0.08), ("product", 0.10)])
def test_ma6_need_beyond_the_cap_moves_by_the_cap_and_is_flagged(kind, cap):
    """마.6, 지시서 3: 필요한 양이 한도를 넘으면 한도만큼만 옮기고 표시가 남는다. 옮긴 뒤에도 허용 폭 밖이다."""
    img, mask = _garment((0.70, 0.03, 0.05))
    center = _center(img, mask)
    target = center - [0.30, 0.0, 0.0]
    photo = _triplet(img, mask, kinds=(kind,) * 3, seller_color=target).photos[0]
    assert photo.record["status"] == "corrected"
    assert "capped_lightness" in photo.record["flags"]
    assert photo.record["plan"]["d_l"] == pytest.approx(-cap)
    assert photo.record["plan"]["n_l"] > cap
    again = _center(photo.image, mask)
    assert center[0] - again[0] == pytest.approx(cap, abs=0.003)
    assert again[0] - target[0] > photo.record["plan"]["lightness_tolerance"]


def test_ma6_chroma_cap_is_flagged_too():
    """마.6: 색 방향도 한도(0.02)만큼만 옮기고 표시가 남는다."""
    img, mask = _garment((0.62, 0.03, 0.05))
    center = _center(img, mask)
    photo = _triplet(img, mask, seller_color=center + [0.0, 0.0, -0.06]).photos[0]
    assert photo.record["status"] == "corrected" and "capped_chroma" in photo.record["flags"]
    assert photo.record["plan"]["d_ab"] == pytest.approx([0.0, -0.02], abs=1e-9)
    assert float(G.chroma_distance(_center(photo.image, mask), center)) == pytest.approx(0.02, abs=0.003)


def _dark_garment():
    """그늘이 깊은 어두운 옷. 어둡게 할수록 뭉개지는 화소가 늘어난다."""
    return _garment((0.20, 0.0, -0.005), amp=0.20)


def test_ma7_applied_shift_never_shrinks_when_the_request_grows():
    """마.7: 어두운 옷을 어둡게 하는 요청을 0.01씩 키워도 적용 크기가 줄지 않는다(절반으로 줄이지 않는다)."""
    img, mask = _dark_garment()
    center = _center(img, mask)
    crushed = G.measure_garment_color(img, mask)["crushed_frac"]
    applied, reduced = [], []
    for request in np.arange(1, 11) / 100:
        target = center - [STUDIO["lightness_tolerance"] + request, 0.0, 0.0]
        plan = G.plan_shift(center, target, "studio", blown_frac=0.0,
                            grow=lambda d_l, d_ab: G.shift_clip_growth(img, mask, center, d_l, d_ab))
        assert plan["n_l"] == pytest.approx(request)
        applied.append(-plan["d_l"])
        reduced.append("clip_guard_reduced" in plan["flags"])
        assert applied[-1] <= request + 1e-12
        if reduced[-1]:
            assert plan["d_l_before_clip_guard"] == pytest.approx(-request)
            grown = G.lightness_clip_growth(img, mask, center, plan["d_l"])
            assert grown <= G.CLIP_GROWTH_MAX
    assert crushed < 0.001
    assert reduced == [False] * 5 + [True] * 5, "이 그림에서는 0.06부터 뭉개짐 한도에 걸린다"
    resolution = 0.10 / 2 ** G.CLIP_GUARD_ITERATIONS          # 이분법 12회의 눈금
    assert all(later >= earlier - resolution for earlier, later in zip(applied, applied[1:]))
    assert max(applied[5:]) - min(applied[5:]) <= resolution, "한도에 걸린 요청은 모두 같은 자리에 멈춘다"
    assert applied[5] > applied[4]


def test_ma8_blown_photo_keeps_its_lightness():
    """마.8: 이미 날아간 화소가 1%를 넘는 사진은 밝기를 건드리지 않고 표시가 남는다."""
    img, mask = _garment((0.88, 0.0, 0.01), amp=0.03)
    img[_box((60, 90, 60, 90))] = 255                       # 옷 넓이의 4%가 날아갔다
    center = _center(img, mask)
    result = _triplet(img, mask, seller_color=center - [0.10, 0, 0])
    record = result.photos[0].record
    assert record["measure"]["blown_frac"] > G.BLOWN_SKIP_FRAC
    assert (record["status"], record["reason"]) == ("untouched", "lightness_skipped_blown")
    assert record["flags"] == ["lightness_skipped_blown"]
    assert record["plan"]["n_l"] > 0 and record["plan"]["d_l"] == 0.0
    assert result.photos[0].image is img


def _plan(center, target, kind="studio", blown_frac=0.0, grow=lambda d_l, d_ab: 0.0):
    return G.plan_shift(center, target, kind, blown_frac=blown_frac, grow=grow)


def test_ma_lightness_guards_in_the_plan():
    """6.2절 밝기 1번에서 5번: 걸리면 밝기만 0으로 두고 표시를 남긴다. 색 방향은 계속 본다."""
    extreme = _plan((0.98, 0.0, 0.0), (0.80, 0.0, 0.04))
    assert extreme["d_l"] == 0.0 and extreme["flags"] == ["capped_lightness", "lightness_skipped_extreme"]
    assert extreme["d_ab"] == pytest.approx([0.0, 0.018]) and extreme["reason"] is None
    black = _plan((0.04, 0.0, 0.0), (0.20, 0.0, 0.0))
    assert (black["d_l"], black["reason"]) == (0.0, "lightness_skipped_extreme")

    # 밝힐 때 옷 가운데 밝기는 0.99 까지만 간다.
    ceiling = _plan((0.95, 0.0, 0.0), (1.20, 0.0, 0.0))
    assert ceiling["n_l"] > 0.10 and ceiling["d_l"] == pytest.approx(0.04)

    # 0.003 보다 작은 이동은 하지 않는다.
    small = _plan((0.60, 0.0, 0.0), (0.60 - 0.053 - 0.002, 0.0, 0.024))
    assert small["n_l"] == pytest.approx(0.002) and small["n_c"] == pytest.approx(0.002)
    assert (small["d_l"], small["d_ab"], small["reason"]) == (0.0, [0.0, 0.0], "within_tolerance")

    # 한도 안에서 옮길 수 있는 크기가 0.003 보다 작으면 밝기를 옮기지 않는다.
    blocked = _plan((0.60, 0.0, 0.0), (0.50, 0.0, 0.0), grow=lambda d_l, d_ab: 1.0)
    assert blocked["d_l"] == 0.0 and blocked["d_l_before_clip_guard"] == pytest.approx(-0.047)
    assert blocked["flags"] == ["clip_guard_reduced", "lightness_skipped_clip_guard"]
    assert blocked["reason"] == "lightness_skipped_clip_guard"

    # 밝기를 옮기면 진하기가 같은 비율로 따라간다. 색 방향은 그 뒤의 예상 색에서 판단한다.
    vivid = _plan((0.50, 0.20, 0.0), (0.40, 0.20, 0.0))
    assert vivid["d_l"] == pytest.approx(-0.047)
    assert vivid["e_c"] == pytest.approx(0.20 * 0.047 / 0.50) and vivid["d_ab"] == [0.0, 0.0]


def test_ma_strength_formula_starts_at_zero_and_grows(monkeypatch):
    """6.2절: 세기가 0이면 허용 폭 끝까지만, 1이면 벗어날수록 기준 색 쪽으로 더 옮긴다. 둘 다 허용 폭 끝에서 0으로 시작한다."""
    edge = STUDIO["lightness_tolerance"]

    def shift(off):
        return -_plan((0.60, 0.0, 0.0), (0.60 - off, 0.0, 0.0))["d_l"]

    assert G.STRENGTH_LAMBDA == 0.0
    assert [shift(edge), shift(0.08)] == pytest.approx([0.0, 0.027])
    monkeypatch.setattr(G, "STRENGTH_LAMBDA", 1.0)
    assert shift(edge) == 0.0
    assert shift(edge + 0.0031) == pytest.approx(0.0061, abs=2e-4), "경계 근처에서는 약 두 배"
    assert shift(0.08) == pytest.approx(0.08 - edge * math.exp(-(0.08 - edge) / edge))
    assert 0.08 - shift(0.08) == pytest.approx(0.0318, abs=1e-4), "남는 차이는 허용 폭보다 작다"


def test_ma_default_values_are_the_spec_tables():
    """6.1절 표와 10.4절, 10.5절의 기본값이다. 값을 바꾸면 명세와 이 테스트를 함께 고친다(기본값이 조용히 바뀌지 않게)."""
    assert G.SHIFT_LIMITS == {
        "studio": {"chroma_tolerance": 0.022, "lightness_tolerance": 0.053, "chroma_cap": 0.02, "lightness_cap": 0.10},
        "styling": {"chroma_tolerance": 0.035, "lightness_tolerance": 0.13, "chroma_cap": 0.02, "lightness_cap": 0.08},
        "product": {"chroma_tolerance": 0.035, "lightness_tolerance": 0.13, "chroma_cap": 0.02, "lightness_cap": 0.10},
    }
    assert (G.STRENGTH_LAMBDA, G.MIN_SHIFT, G.CLIP_GROWTH_MAX, G.CLIP_GUARD_ITERATIONS) == (0.0, 0.003, 0.01, 12)
    assert (G.BLOWN_MIN, G.CRUSHED_MAX, G.BLOWN_SKIP_FRAC, G.LIGHTNESS_RANGE, G.BRIGHTEN_CEILING) == (
        250, 5, 0.01, (0.05, 0.97), 0.99)
    assert (G.RECHECK_RATIO, G.RECHECK_FLOOR) == (0.15, 0.003) and G.GAMUT_ITERATIONS >= 12


@pytest.mark.parametrize("kind,chroma_tol,light_tol,light_cap", [
    ("studio", 0.022, 0.053, 0.10), ("styling", 0.035, 0.13, 0.08), ("product", 0.035, 0.13, 0.10)])
def test_ma5_every_kind_uses_its_own_tolerances_and_caps(kind, chroma_tol, light_tol, light_cap):
    """마.5, 마.6, 6.1절: 종류마다 허용 폭 바로 안쪽은 그대로, 바로 바깥쪽은 넘은 만큼, 한도를 넘으면 한도만큼과 표시다."""
    g = np.array((0.60, 0.0, 0.0))

    def plan(d_l=0.0, d_c=0.0):
        return _plan(g, g - [d_l, d_c, 0.0], kind)

    assert plan(d_c=chroma_tol - 0.002)["reason"] == "within_tolerance"
    assert plan(d_c=chroma_tol + 0.006)["d_ab"] == pytest.approx([-0.006, 0.0], abs=1e-9)
    capped = plan(d_c=chroma_tol + 0.05)
    assert capped["d_ab"] == pytest.approx([-0.02, 0.0], abs=1e-9) and "capped_chroma" in capped["flags"]
    assert plan(d_l=light_tol - 0.002)["reason"] == "within_tolerance"
    assert plan(d_l=light_tol + 0.006)["d_l"] == pytest.approx(-0.006)
    assert plan(d_l=light_tol + light_cap - 0.002)["flags"] == []
    capped = plan(d_l=light_tol + light_cap + 0.002)
    assert capped["d_l"] == pytest.approx(-light_cap) and capped["flags"] == ["capped_lightness"]


def test_ma_small_guard_boundaries_in_the_plan():
    """6.2절: 가장 작은 이동 0.003, 이미 날아간 화소 1%, 가운데 밝기 범위(0.05 초과, 0.97 미만)의 양쪽."""
    g = (0.60, 0.0, 0.0)
    assert _plan(g, (0.60 - 0.053 - 0.0035, 0.0, 0.0))["d_l"] == pytest.approx(-0.0035)
    assert _plan(g, (0.60, 0.022 + 0.0035, 0.0))["d_ab"] == pytest.approx([0.0035, 0.0])
    off = (0.50, 0.0, 0.0)
    assert _plan(g, off, blown_frac=0.009)["d_l"] == pytest.approx(-0.047)
    assert _plan(g, off, blown_frac=0.011)["flags"] == ["lightness_skipped_blown"]
    assert _plan((0.06, 0, 0), (0.20, 0, 0))["d_l"] > 0 and _plan((0.05, 0, 0), (0.20, 0, 0))["d_l"] == 0.0
    assert _plan((0.96, 0, 0), (0.80, 0, 0))["d_l"] < 0 and _plan((0.97, 0, 0), (0.80, 0, 0))["d_l"] == 0.0


def test_ma7_clip_guard_stops_at_the_largest_safe_size():
    """6.3절: 한도에 걸리면 한도 안에서 가장 큰 크기까지 간다. 1% 더 가면 한도를 넘는 자리다(절반으로 줄이지 않는다)."""
    img, mask = _dark_garment()
    center = _center(img, mask)

    def grow(d_l, d_ab=(0.0, 0.0)):
        return G.shift_clip_growth(img, mask, center, d_l, d_ab)

    plan = G.plan_shift(center, center - [0.053 + 0.09, 0.0, 0.0], "studio", blown_frac=0.0, grow=grow)
    assert "clip_guard_reduced" in plan["flags"]
    assert grow(plan["d_l"]) <= 0.01 < grow(plan["d_l"] * 1.01)


def _shaded_dark_garment():
    """어두운 옷. 아래쪽 12줄은 밝기가 0.125 에서 0.19 까지 고르게 깔린 깊은 그늘이라 어둡게 할수록 뭉개짐이 조금씩 는다."""
    color = (0.24, 0.0, -0.004)
    img, mask = _garment(color, amp=0.03)
    y0 = BOX[1] - 12
    ratio = np.ones((H, W))
    ratio[y0:BOX[1], BOX[2]:BOX[3]] = np.linspace(0.125, 0.19, BOX[3] - BOX[2])[None, :] / color[0]
    _paint(img, _box((y0, BOX[1], BOX[2], BOX[3])), color, ratio)
    return img, mask


def test_ma7_clip_guard_allows_growth_up_to_one_point_and_stops_there():
    """6.3절, 지시서 6: 뭉개짐이 1%p 안에서 느는 요청은 줄이지 않고, 넘는 요청은 1%p 바로 아래까지만 간다(증가 0만 허용하는 것이 아니다)."""
    img, mask = _shaded_dark_garment()
    center = _center(img, mask)

    def grow(d_l, d_ab=(0.0, 0.0)):
        return G.shift_clip_growth(img, mask, center, d_l, d_ab)

    def plan(request):
        return G.plan_shift(center, center - [0.053 + request, 0.0, 0.0], "studio", blown_frac=0.0, grow=grow)

    small = plan(0.03)
    assert small["flags"] == [] and small["d_l"] == pytest.approx(-0.03), "증가 0.8%p 는 한도 안이라 줄이지 않는다"
    assert 0.005 < grow(small["d_l"]) <= 0.01
    large = plan(0.10)
    assert "clip_guard_reduced" in large["flags"] and -0.10 < large["d_l"] < -0.03
    assert 0.005 < grow(large["d_l"]) <= 0.01 < grow(large["d_l"] * 1.03)
    photo = _triplet(img, mask, seller_color=center - [0.053 + 0.10, 0, 0]).photos[0]
    assert photo.record["status"] == "corrected"
    assert 0.005 < photo.record["result"]["clip_growth"] <= 0.01
    assert 0.005 < _crushed(photo.image, mask) - _crushed(img, mask) <= 0.01


def test_ma7_clip_guard_counts_only_pixels_that_actually_move():
    """6.3절: 한도는 가중치까지 실제와 같게 잰다. 가중치가 0이라 안 옮겨지는 짙은 조각은 뭉개질 화소로 세지 않는다."""
    img, mask = _garment((0.45, 0.0, 0.0), amp=0.03)
    ink = _box((80, 120, 60, 110))                          # 옷의 9.5%. 가운데 밝기의 0.29배라 가중치 0
    _paint(img, ink, (0.13, 0.0, 0.0))                      # sRGB 7. 옮겨진다면 뭉개진다
    center = _center(img, mask)
    assert np.all(G.move_weight(img, mask, center)[_inside(ink, 3)] == 0.0)
    assert G.lightness_clip_growth(img, mask, center, -0.10) == 0.0
    photo = _triplet(img, mask, seller_color=center - [0.053 + 0.10, 0, 0]).photos[0]
    assert photo.record["status"] == "corrected"
    assert "clip_guard_reduced" not in photo.record["flags"] and photo.record["plan"]["d_l"] == pytest.approx(-0.10)
    assert np.array_equal(photo.image[_inside(ink, 3)], img[_inside(ink, 3)])


def test_ma7_guard_that_leaves_less_than_the_smallest_move_keeps_the_original():
    """6.2절 밝기 5번, 지시서 6: 한도 안에서 갈 수 있는 크기가 0.003 에 못 미치면 밝기를 옮기지 않고 원본을 둔다.

    아주 검은 옷(sRGB 3)에 sRGB 6 인 결이 40% 있으면, 0.003 만 어둡게 해도 그 결이 한꺼번에 뭉개진다.
    """
    img, mask = _background(), _box()
    img[mask] = 3
    img[mask & _rows(10, 4)] = 6
    center = _center(img, mask)
    assert G.lightness_clip_growth(img, mask, center, -0.002) == 0.0
    assert G.lightness_clip_growth(img, mask, center, -0.0029) > 0.30
    photo = _triplet(img, mask, seller_color=center - [0.053 + 0.02, 0, 0]).photos[0]
    assert (photo.record["status"], photo.record["reason"]) == ("untouched", "lightness_skipped_clip_guard")
    assert photo.record["flags"] == ["clip_guard_reduced", "lightness_skipped_clip_guard"]
    assert photo.record["plan"]["d_l"] == 0.0
    assert photo.record["plan"]["d_l_before_clip_guard"] == pytest.approx(-0.02)
    assert photo.image is img


def test_ma7_applied_chroma_never_shrinks_when_the_chroma_request_grows():
    """14.1절 1번: 색 방향만 옮기는 요청(밝기 이동 0)을 키워도 적용 크기가 줄지 않는다. 한도에 걸린 요청은 같은 자리에 멈춘다."""
    img, mask = _garment((0.975, 0.0, 0.02), amp=0.01)       # 아이보리. 흰색 쪽으로 옮기면 밝은 주름이 날아간다
    center = _center(img, mask)
    applied, reduced = [], []
    for request in np.arange(1, 8) * 0.004:
        target = center + [0.0, 0.0, -(STUDIO["chroma_tolerance"] + request)]
        plan = G.plan_shift(center, target, "studio", blown_frac=0.0,
                            grow=lambda d_l, d_ab: G.shift_clip_growth(img, mask, center, d_l, d_ab))
        assert plan["n_c"] == pytest.approx(request) and plan["d_l"] == 0.0
        applied.append(math.hypot(*plan["d_ab"]))
        reduced.append("clip_guard_reduced" in plan["flags"])
        assert applied[-1] <= min(request, STUDIO["chroma_cap"]) + 1e-12
        assert G.shift_clip_growth(img, mask, center, 0.0, plan["d_ab"]) <= G.CLIP_GROWTH_MAX
    assert reduced == [False] * 4 + [True] * 3, "이 그림에서는 0.020부터 날아감 한도에 걸린다"
    resolution = STUDIO["chroma_cap"] / 2 ** G.CLIP_GUARD_ITERATIONS      # 이분법 12회의 눈금
    assert all(later >= earlier - resolution for earlier, later in zip(applied, applied[1:]))
    assert max(applied[4:]) - min(applied[4:]) <= resolution
    assert applied[4] > applied[3]


def test_ma7_clip_guard_measures_lightness_and_chroma_together():
    """14.1절 1번, 6.3절: 한도는 밝기와 색 방향을 함께 옮긴 결과로 잰다. 넘으면 밝기는 두고 색 방향에 곱할 비율을 이분법으로 줄인다."""
    center = (0.60, 0.0, 0.0)
    calls = []

    def grow(d_l, d_ab):
        calls.append((d_l, tuple(d_ab)))
        return 0.15 * abs(d_l) + 0.6 * math.hypot(*d_ab)      # 밝기 0.04 가 0.6%p, 색 방향 0.02 가 1.2%p 를 보탠다

    def plan(lightness_off, chroma_off, grow=grow):
        return G.plan_shift(center, (0.60 - lightness_off, 0.0, chroma_off), "studio", blown_frac=0.0, grow=grow)

    both = plan(0.053 + 0.04, 0.05)
    assert calls[0] == (pytest.approx(-0.04), (0.0, 0.0)), "밝기 단계는 밝기만으로 잰다"
    assert both["d_l"] == pytest.approx(-0.04) and both["d_l_before_clip_guard"] is None, "밝기는 줄이지 않는다"
    assert both["d_ab_before_clip_guard"] == pytest.approx([0.0, 0.02])
    resolution = 0.02 / 2 ** G.CLIP_GUARD_ITERATIONS
    assert both["d_ab"][0] == 0.0 and 0.02 / 3 - resolution <= both["d_ab"][1] <= 0.02 / 3, "0.6% + 1.2% * q <= 1% 인 가장 큰 q"
    assert both["flags"] == ["capped_chroma", "clip_guard_reduced"] and both["reason"] is None
    assert grow(both["d_l"], both["d_ab"]) <= G.CLIP_GROWTH_MAX < grow(both["d_l"], [1.01 * v for v in both["d_ab"]])

    inside = plan(0.053 + 0.04, 0.022 + 0.006)                # 0.6% + 0.36% 는 한도 안이다
    assert inside["d_ab"] == pytest.approx([0.0, 0.006]) and inside["d_ab_before_clip_guard"] is None
    assert inside["flags"] == []

    # 줄인 뒤 색 방향 이동이 0.003 에 못 미치면 색 방향은 옮기지 않는다. 밝기는 그대로 옮긴다.
    little = plan(0.053 + 0.06, 0.05)                         # 밝기만으로 0.9%. 색 방향은 0.0017 까지만 된다
    assert little["d_l"] == pytest.approx(-0.06) and little["d_ab"] == [0.0, 0.0]
    assert little["d_ab_before_clip_guard"] == pytest.approx([0.0, 0.02])
    assert little["flags"] == ["capped_chroma", "clip_guard_reduced"] and little["reason"] is None

    # 밝기도 한도에 걸려 줄었으면 색 방향이 들어갈 자리가 없다. 표시는 한 번만 단다.
    tight = plan(0.053 + 0.09, 0.05)
    assert tight["d_l_before_clip_guard"] == pytest.approx(-0.09)
    assert -0.01 / 0.15 <= tight["d_l"] <= -0.01 / 0.15 + 0.09 / 2 ** G.CLIP_GUARD_ITERATIONS
    assert tight["d_ab"] == [0.0, 0.0] and tight["flags"] == ["clip_guard_reduced", "capped_chroma"]

    # 색 방향만 옮기려던 사진이 한도 때문에 못 옮기면 그 표시가 이유다. 옮길 필요가 없었던 사진과 구별된다.
    blocked = plan(0.0, 0.05, grow=lambda d_l, d_ab: 1.0 if any(d_ab) else 0.0)
    assert (blocked["d_l"], blocked["d_ab"]) == (0.0, [0.0, 0.0])
    assert blocked["flags"] == ["capped_chroma", "clip_guard_reduced"] and blocked["reason"] == "clip_guard_reduced"


# ── 바. 화소 적용 ───────────────────────────────────────────────────────────

def test_ba1_nothing_outside_the_mask_changes():
    """바.1, 지시서 1: 윤곽 밖 화소는 한 화소도 바뀌지 않는다(바깥 전체 비교, 무늬 배경)."""
    img, _ = _garment((0.62, 0.03, 0.05))
    y, x = np.mgrid[0:H, 0:W]
    mask = ((x - 96) / 60.0) ** 2 + ((y - 128) / 90.0) ** 2 < 1.0     # 둥근 윤곽. 옷 네모보다 작다
    mask[70:90, 80:100] = False                                        # 구멍
    original = img.copy()
    moved, stats = G.apply_shift(img, mask, _center(img, mask), -0.10, (0.02, -0.01))
    assert np.array_equal(img, original), "입력을 고치지 않는다"
    assert np.array_equal(moved[~mask], original[~mask])
    assert stats["changed_pixels"] == int((moved != original).any(axis=2).sum()) > 0.9 * mask.sum()

    result = _triplet(img, mask, seller_color=_center(img, mask) - [0.12, 0.0, 0.04])
    photo = result.photos[0]
    assert photo.record["status"] == "corrected"
    assert np.array_equal(photo.image[~mask], original[~mask])
    assert photo.record["result"]["outside_changed_pixels"] == 0


def test_ba2_darkening_scales_all_three_coordinates_alike():
    """바.2: 어둡게 하면 옮겨진 화소의 Oklab 세 좌표가 같은 비율로 준다."""
    img, mask = _garment((0.62, 0.06, 0.09), amp=0.08)
    center = _center(img, mask)
    moved, _ = G.apply_shift(img, mask, center, -0.06, (0.0, 0.0))
    deep = _inside(mask, 2)
    before, after = G.rgb_to_oklab(img)[deep], G.rgb_to_oklab(moved)[deep]
    gain = (center[0] - 0.06) / center[0]
    ratios = np.median(after / before, axis=0)
    assert ratios == pytest.approx([gain] * 3, abs=0.004)
    assert np.abs(after - gain * before).max() < 0.005       # 8비트 반올림만큼만 어긋난다


def test_ba3_brightening_curve_gives_dark_pixels_the_base_gain():
    """바.3: 밝힐 때 옷 가운데보다 어두운 화소의 배율은 바탕과 같고, 밝은 화소만 눌려서 1을 넘지 않는다."""
    center_l, d_l = 0.85, 0.06
    gain = (center_l + d_l) / center_l
    lightness = np.linspace(0.0, 1.0, 2001)
    ratio = G.lightness_ratio(lightness, center_l, d_l)
    assert np.all(ratio[lightness <= center_l] == gain)
    out = lightness * ratio
    assert out.max() <= 1.0 and out[-1] == pytest.approx(1.0)
    assert np.all(np.diff(out) > 0), "밝기 순서가 뒤집히지 않는다"
    assert np.all(ratio[lightness > center_l] < gain), "밝은 화소는 바탕보다 덜 밝아진다"
    just_above = G.lightness_ratio(np.array([center_l + 1e-9]), center_l, d_l)[0]
    assert just_above == pytest.approx(gain, abs=1e-6), "옷 가운데에서 이어진다"
    # 어둡게 할 때는 모든 화소가 같은 배율이다.
    assert np.all(G.lightness_ratio(lightness, center_l, -0.06) == (center_l - 0.06) / center_l)


def test_ba3_dark_lettering_does_not_brighten_more_than_the_fabric():
    """바.3: 흰 옷을 밝혀도 글자와 그늘이 바탕보다 더 많이 밝아지지 않는다(양끝 고정 곡선을 쓰지 않는다)."""
    base = (0.85, 0.0, 0.01)
    img, mask = _garment(base, amp=0.07)
    grey_text, black_text, shade = _box((60, 80, 50, 70)), _box((60, 80, 100, 120)), _box((150, 170, 50, 110))
    _paint(img, grey_text, (0.45, 0.0, 0.005))               # 가운데 밝기의 0.53배
    _paint(img, black_text, (0.20, 0.0, 0.0))                # 0.24배. 적용 범위(0.30) 밖이라 그대로 둔다
    _paint(img, shade, base, 0.75 * _folds(0.07))            # 깊은 주름 그늘
    center = _center(img, mask)
    assert center[0] == pytest.approx(0.85, abs=0.015)       # 어두운 조각이 옷의 9%라 조금 내려온다
    assert G.lightness_clip_growth(img, mask, center, 0.06) <= G.CLIP_GROWTH_MAX
    moved, _ = G.apply_shift(img, mask, center, 0.06, (0.0, 0.0))
    before, after = G.rgb_to_oklab(img)[..., 0], G.rgb_to_oklab(moved)[..., 0]
    gain = (center[0] + 0.06) / center[0]

    pieces = _box((55, 85, 45, 125)) | _box((145, 175, 45, 115))
    fabric_below = _inside(mask, 2) & ~pieces & (before <= center[0])
    for name, where in (("fabric", fabric_below), ("text", _inside(grey_text, 2)),
                        ("shade", _inside(shade, 2))):
        assert np.median(after[where] / before[where]) == pytest.approx(gain, abs=0.004), name
    text_gain = np.median(after[_inside(grey_text, 2)] / before[_inside(grey_text, 2)])
    assert text_gain <= np.median(after[fabric_below] / before[fabric_below]) + 0.004
    assert np.array_equal(moved[_inside(black_text, 2)], img[_inside(black_text, 2)])

    bright = _inside(mask, 2) & (before > center[0])
    assert after[bright].max() < 1.0
    assert np.all(after[bright] >= before[bright])
    assert np.all(after[bright] / before[bright] < gain + 0.004)


def test_ba4_chroma_shift_follows_pixel_lightness():
    """바.4: 색 방향을 옮기면 그늘 화소는 밝기 비율만큼 덜 옮겨지고, 바탕보다 밝은 화소는 바탕만큼 옮겨진다."""
    base = (0.40, 0.0, 0.0)
    img, mask = _garment(base, amp=0.02)
    shade, bright, flat = _box((60, 100, 50, 140)), _box((120, 160, 50, 140)), _box((170, 210, 50, 140))
    _paint(img, shade, base, 0.6 * _folds(0.02))
    _paint(img, bright, base, 1.9 * _folds(0.02))             # 햇빛 받은 같은 천. 밝기 차이로 재면 다른 천이 된다
    center = _center(img, mask)
    moved, _ = G.apply_shift(img, mask, center, 0.0, (0.02, 0.0))
    before, after = G.rgb_to_oklab(img), G.rgb_to_oklab(moved)
    step = {name: float((after[..., 1] - before[..., 1])[_inside(where, 2)].mean())
            for name, where in (("shade", shade), ("bright", bright), ("flat", flat))}
    assert step["flat"] == pytest.approx(0.02, abs=0.002)
    assert step["bright"] == pytest.approx(0.02, abs=0.002)
    assert step["shade"] == pytest.approx(0.6 * 0.02, abs=0.002)
    follow = np.clip(before[..., 0] / center[0], 0.0, 1.0) * 0.02
    deep = _inside(mask, 2)
    assert np.abs((after[..., 1] - before[..., 1]) - follow)[deep].mean() < 0.002
    assert np.abs(after[..., 0] - before[..., 0])[deep].max() < 0.004, "밝기는 그대로다"


def _wide(color, amp=0.04):
    """폭 1536 그림. 가중치 흐림과 경계 띠가 실제 크기로 돈다."""
    shape, box = (220, 1536), (20, 200, 168, 1368)
    img, mask = _background(shape), _box(box, shape)
    _paint(img, mask, color, _folds(amp, shape, box[2], period=48))
    return img, mask, shape


def test_ba5_different_colored_pieces_and_the_hand_stay_as_they_are():
    """바.5: 천과 색 방향이 0.08 넘게 다른 로고, 짙은 옷 위의 흰 라벨, 옷 위의 손은 그대로다(조각 가장자리 2화소 제외)."""
    fabric = (0.62, 0.02, 0.04)
    img, mask, shape = _wide(fabric)
    logo, hand = _box((60, 120, 400, 480), shape), _box((90, 170, 800, 900), shape)
    _paint(img, logo, (0.62, 0.16, 0.10))                    # 색 방향 차이 0.15
    _paint(img, hand, (0.72, 0.05, 0.05))
    mask = mask & ~hand                                      # 손은 윤곽의 구멍이다
    center = _center(img, mask)
    moved, _ = G.apply_shift(img, mask, center, -0.08, (0.02, 0.0))
    assert np.array_equal(moved[_inside(logo, 2)], img[_inside(logo, 2)])
    assert np.array_equal(moved[hand], img[hand])
    cloth = _inside(mask, 3) & ~_around(logo)
    assert (moved[cloth] != img[cloth]).any(axis=1).all(), "천은 모두 옮겨졌다"

    dark, dark_mask, _ = _wide((0.25, 0.0, -0.02))
    label = _box((60, 100, 400, 520), shape)
    _paint(dark, label, (0.95, 0.0, 0.0))                    # 밝기 비율 3.8
    center = _center(dark, dark_mask)
    moved, _ = G.apply_shift(dark, dark_mask, center, 0.08, (0.0, 0.02))
    assert np.array_equal(moved[_inside(label, 2)], dark[_inside(label, 2)])
    cloth = _inside(dark_mask, 3) & ~_around(label)
    assert (moved[cloth] != dark[cloth]).any(axis=1).all()


def _around(piece) -> np.ndarray:
    """조각과 그 둘레 4화소. 가중치 흐림이 닿는 자리라 천이 다 옮겨졌는지 볼 때는 뺀다."""
    return cv2.dilate(piece.astype(np.uint8), np.ones((9, 9), np.uint8)).astype(bool)


def _hue(lab) -> np.ndarray:
    return np.degrees(np.arctan2(lab[..., 2], lab[..., 1]))


@pytest.mark.parametrize("rgb", [(225, 20, 30), (240, 120, 0)])
def test_ba6_out_of_gamut_request_reduces_chroma_without_clipping_channels(rgb):
    """바.6, 지시서 7: 색역 밖으로 나가는 요청(진한 빨강, 주황을 더 진하게)에서 채널이 잘리지 않고 색상각이 유지된다."""
    red = G.rgb_to_oklab(np.array([rgb], np.uint8))[0]
    img, mask = _garment(red, amp=0.05)
    center = _center(img, mask)
    push = 0.05 * center[1:] / np.hypot(*center[1:])         # 같은 색상각으로 더 진하게
    moved, stats = G.apply_shift(img, mask, center, 0.0, push)
    assert stats["gamut_reduced_frac"] > 0.9

    deep = _inside(mask, 2)
    before, after = G.rgb_to_oklab(img)[deep], G.rgb_to_oklab(moved)[deep]
    wanted = before.copy()
    wanted[:, 1:] += np.clip(before[:, :1] / center[0], 0.0, 1.0) * push
    clipped = G.rgb_to_oklab(_rgb8(wanted))                  # 채널을 그냥 자르면 이렇게 된다
    assert np.median(np.abs(_hue(clipped) - _hue(wanted))) > 1.5, "자르면 색상각이 틀어지는 요청이어야 한다"
    assert np.abs(_hue(after) - _hue(wanted)).max() < 0.3
    assert np.abs(after[:, 0] - wanted[:, 0]).max() < 0.004, "밝기는 그대로 두고 진하기만 줄인다"
    assert np.all(np.hypot(after[:, 1], after[:, 2]) <= np.hypot(wanted[:, 1], wanted[:, 2]) + 0.002)

    fitted, reduced = G.fit_to_gamut(wanted)
    encoded = G._encode_srgb(G._oklab_to_linear(fitted))
    assert reduced.mean() > 0.9
    assert encoded.min() >= -G.GAMUT_TOLERANCE_8BIT and encoded.max() <= 255 + G.GAMUT_TOLERANCE_8BIT
    assert np.abs(_hue(fitted) - _hue(wanted)).max() < 1e-9
    inside, untouched = G.fit_to_gamut(before)
    assert not untouched.any() and np.array_equal(inside, before)


def test_ba7_brightening_a_light_garment_is_held_at_the_blown_limit():
    """바.7, 지시서 6: 밝은 옷을 밝혀도 날아간 화소가 1%p 넘게 늘지 않는다. 넘을 요청은 한도까지 줄인다.

    줄여도 0.003 에 못 미쳐 원본을 두는 쪽은 test_ma_lightness_guards_in_the_plan 이 본다.
    셀러 색의 밝기는 1 이하여야 해서(14.1절 5번) 가운데 밝기 0.86 에 주름이 밝은 옷으로 본다.
    """
    img, mask = _garment((0.86, 0.0, 0.005), amp=0.15)
    center = _center(img, mask)
    assert G.measure_garment_color(img, mask)["blown_frac"] <= G.BLOWN_SKIP_FRAC
    unguarded, _ = G.apply_shift(img, mask, center, 0.07, (0.0, 0.0))
    assert _blown(unguarded, mask) - _blown(img, mask) > 0.10, "그냥 밝히면 한도를 크게 넘는 그림이어야 한다"

    photo = _triplet(img, mask, seller_color=center + [STUDIO["lightness_tolerance"] + 0.07, 0, 0]).photos[0]
    assert (photo.record["status"], photo.record["flags"]) == ("corrected", ["clip_guard_reduced"])
    assert photo.record["plan"]["d_l_before_clip_guard"] == pytest.approx(0.07)
    assert G.MIN_SHIFT <= photo.record["plan"]["d_l"] < 0.07, "한도 안에서 갈 수 있는 데까지만 간다"
    assert _blown(photo.image, mask) - _blown(img, mask) <= G.CLIP_GROWTH_MAX
    assert photo.record["result"]["clip_growth"] <= G.CLIP_GROWTH_MAX


def test_ba7_darkening_a_black_garment_is_held_at_the_crushed_limit():
    """바.7, 지시서 6: 검은 옷을 어둡게 해도 뭉개진 화소가 1%p 넘게 늘지 않는다. 넘을 요청은 한도까지 줄인다."""
    img, mask = _dark_garment()
    center = _center(img, mask)
    unguarded, _ = G.apply_shift(img, mask, center, -0.09, (0.0, 0.0))
    assert _crushed(unguarded, mask) - _crushed(img, mask) > 0.10

    photo = _triplet(img, mask, seller_color=center - [STUDIO["lightness_tolerance"] + 0.09, 0, 0]).photos[0]
    assert (photo.record["status"], photo.record["flags"]) == ("corrected", ["clip_guard_reduced"])
    assert photo.record["plan"]["d_l_before_clip_guard"] == pytest.approx(-0.09)
    assert -0.09 < photo.record["plan"]["d_l"] <= -G.MIN_SHIFT, "한도 안에서 갈 수 있는 데까지만 간다"
    assert _crushed(photo.image, mask) - _crushed(img, mask) <= G.CLIP_GROWTH_MAX
    assert photo.record["result"]["clip_growth"] <= G.CLIP_GROWTH_MAX


def _blown(img, mask) -> float:
    return float((img[mask].min(axis=1) >= G.BLOWN_MIN).mean())


def _crushed(img, mask) -> float:
    return float((img[mask].max(axis=1) <= G.CRUSHED_MAX).mean())


def test_ba8_edge_weight_ramps_over_two_pixels_at_full_width():
    """바.8: 폭 1536인 그림에서 윤곽 바로 안쪽 화소는 절반쯤, 그 안쪽부터는 다 옮겨진다.

    cv2.distanceTransform(DIST_L2, 3)은 바로 안쪽 화소의 거리를 1이 아니라 0.955로 준다. 그래서 가중치는
    첫 화소 0.4775, 둘째 화소 0.955, 셋째 화소부터 1이다. 명세의 "둘째 화소부터는 다"는 셋째 화소부터 정확히 맞는다.
    """
    img, mask, _shape = _wide((0.60, 0.0, 0.0), amp=0.0)
    center = _center(img, mask)
    weight = G.move_weight(img, mask, center)
    row, left = 110, 168
    assert np.all(weight[~mask] == 0.0)
    assert weight[row, left] == pytest.approx(0.4775, abs=1e-3)
    assert weight[row, left + 1] == pytest.approx(0.955, abs=1e-3)
    assert np.all(weight[row, left + 2:left + 40] == 1.0)

    moved, _ = G.apply_shift(img, mask, center, -0.08, (0.0, 0.0))
    change = (img.astype(int) - moved.astype(int))[row, :, 0]
    full = change[left + 20]
    assert full >= 20 and np.all(change[left + 2:left + 40] == full)
    assert change[left - 1] == 0
    assert 0.40 * full <= change[left] <= 0.56 * full
    assert change[left + 1] >= 0.93 * full


def _weight_by_the_book(img, mask, center):
    """명세 7.1절의 식을 경계 상자 없이 사진 전체에서 그대로 계산한다."""
    def fall(x, low, high):
        s = np.clip((x - low) / (high - low), 0.0, 1.0)
        return 1.0 - s * s * (3.0 - 2.0 * s)

    scale = img.shape[1] / 1536
    lab = G.rgb_to_oklab(img)
    ratio = lab[..., 0] / max(center[0], 1e-6)
    t = np.clip(ratio, 0.0, 1.5)
    apart = np.hypot(lab[..., 1] - t * center[1], lab[..., 2] - t * center[2])
    fabric = fall(apart, 0.04, 0.08) * (1.0 - fall(ratio, 0.30, 0.45)) * fall(ratio, 2.2, 3.0)
    depth = cv2.distanceTransform(mask.astype(np.uint8), cv2.DIST_L2, 3)
    inner = np.clip(depth / max(1.0, 2.0 * scale), 0.0, 1.0)
    num = cv2.GaussianBlur(fabric * mask, (0, 0), 1.0 * scale)
    den = cv2.GaussianBlur(mask.astype(np.float64), (0, 0), 1.0 * scale)
    return np.where(mask, inner * num / np.maximum(den, 1e-6), 0.0)


@pytest.mark.parametrize("wide", [True, False])
def test_ba8_weight_equals_the_formula_computed_on_the_whole_photo(wide):
    """7.1절: 경계 상자 안에서만 계산해도 가중치가 사진 전체에서 계산한 것과 같다. 윤곽이 화면 끝에 닿아도 같다."""
    if wide:
        img, mask, shape = _wide((0.62, 0.02, 0.04))
        _paint(img, _box((60, 120, 400, 480), shape), (0.62, 0.16, 0.10))       # 로고
        _paint(img, _box((60, 100, 700, 820), shape), (0.25, 0.01, 0.02))       # 깊은 그늘(비율 0.4)
        mask = mask & ~_box((90, 170, 900, 1000), shape)                        # 구멍
    else:
        img, mask = _garment((0.62, 0.02, 0.04), box=(0, 200, 96, W))           # 위쪽과 오른쪽 끝에 닿는 윤곽
        _paint(img, _box((60, 100, 120, 160)), (0.62, 0.16, 0.10))
    center = _center(img, mask)
    weight = G.move_weight(img, mask, center)
    expected = _weight_by_the_book(img, mask, center)
    inside = weight[mask]
    assert (inside == 0.0).any() and (inside == 1.0).any() and ((inside > 0.0) & (inside < 1.0)).any()
    assert np.abs(weight - expected).max() < 1e-12
    assert np.all(weight[~mask] == 0.0)


def test_ba3_brightening_scales_all_three_coordinates_and_joins_smoothly():
    """바.3, 7.2절: 밝힐 때도 세 좌표에 같은 배율을 곱한다(진하기가 따라간다). 곡선은 옷 가운데에서 값과 기울기가 이어진다."""
    img, mask = _garment((0.55, 0.06, 0.09), amp=0.08)
    center = _center(img, mask)
    moved, _ = G.apply_shift(img, mask, center, 0.05, (0.0, 0.0))
    before, after = G.rgb_to_oklab(img), G.rgb_to_oklab(moved)
    below = _inside(mask, 2) & (before[..., 0] <= center[0])
    gain = (center[0] + 0.05) / center[0]
    ratios = np.median(after[below] / before[below], axis=0)
    assert ratios[0] == pytest.approx(gain, abs=0.004)
    assert ratios[1:] == pytest.approx([gain] * 2, abs=0.012)               # 8비트 반올림만큼 어긋난다
    assert np.abs(after[below] - gain * before[below]).max() < 0.006
    above = _inside(mask, 2) & (before[..., 0] > center[0] + 0.02)
    ratio = after[above] / before[above]
    assert np.abs(ratio[:, 1] - ratio[:, 0]).max() < 0.02 and np.abs(ratio[:, 2] - ratio[:, 0]).max() < 0.02

    step = 1e-4
    lightness = np.array([0.85 - step, 0.85, 0.85 + step])
    out = lightness * G.lightness_ratio(lightness, 0.85, 0.06)
    left, right = (out[1] - out[0]) / step, (out[2] - out[1]) / step
    assert left == pytest.approx(0.91 / 0.85, abs=1e-6) and right == pytest.approx(left, abs=0.01)


def test_ba5_protection_range_boundaries_in_the_weight():
    """바.5, 7.1절: 적용용 범위의 양쪽. 색 방향 0.035는 옮기고 0.09는 그대로, 밝기 비율 0.47과 2.1은 옮기고 0.28과 3.1은 그대로다."""
    fabric = np.array((0.30, 0.01, 0.02))
    img, mask, shape = _wide(fabric, amp=0.0)
    pieces = {
        "chroma_0.035": (_box((40, 80, 200, 260), shape), (0.30, 0.045, 0.02), 1.0),
        "chroma_0.090": (_box((40, 80, 300, 360), shape), (0.30, 0.100, 0.02), 0.0),
        "dark_0.28": (_box((40, 80, 400, 460), shape), 0.28 * fabric, 0.0),
        "dark_0.47": (_box((40, 80, 500, 560), shape), 0.47 * fabric, 1.0),
        "light_2.1": (_box((40, 80, 600, 660), shape), 2.1 * fabric, 1.0),
        "light_3.1": (_box((40, 80, 700, 760), shape), 3.1 * fabric, 0.0),
    }
    for where, color, _expected in pieces.values():
        _paint(img, where, color)
    weight = G.move_weight(img, mask, _center(img, mask))
    for name, (where, _color, expected) in pieces.items():
        assert np.all(weight[_inside(where, 4)] == expected), name


def test_ba6_gamut_fit_keeps_the_largest_chroma_within_half_a_step():
    """바.6, 7.4절: 색역 처리 뒤 채널은 8비트 반 단계 안에 들고, 진하기를 1% 더하면 넘는다(계단식으로 줄이지 않는다)."""
    red = G.rgb_to_oklab(np.array([[225, 20, 30]], np.uint8))[0]
    wanted = np.tile(red, (200, 1))
    wanted[:, 0] *= np.linspace(0.8, 1.1, 200)
    wanted[:, 1:] *= np.linspace(1.1, 1.6, 200)[:, None]
    fitted, reduced = G.fit_to_gamut(wanted)
    assert reduced.all()
    encoded = G._encode_srgb(G._oklab_to_linear(fitted))
    assert encoded.min() >= -0.5 and encoded.max() <= 255.5
    more = fitted.copy()
    more[:, 1:] *= 1.01
    encoded = G._encode_srgb(G._oklab_to_linear(more))
    assert (np.maximum(encoded.max(axis=1) - 255.0, -encoded.min(axis=1)) > 0.5).all()


def test_ba_combined_move_lands_on_the_predicted_center():
    """6.2절, 7.3절: 밝기와 색 방향을 한 번에 옮기면 다시 잰 가운데 색이 예상 색(g')에 온다.

    색 방향을 따라가는 정도는 옮기기 전 밝기로 정한다. 바탕은 전부, 0.6배 그늘은 0.6배만큼 옮겨진다.
    """
    base = (0.62, 0.03, 0.05)
    img, mask = _garment(base, amp=0.02)
    shade = _box((60, 100, 50, 140))
    _paint(img, shade, base, 0.6 * _folds(0.02))
    center = _center(img, mask)
    d_l, d_ab = -0.10, (0.02, 0.0)
    moved, _ = G.apply_shift(img, mask, center, d_l, d_ab)
    gain = (center[0] + d_l) / center[0]
    predicted = np.array([center[0] + d_l, gain * center[1] + d_ab[0], gain * center[2] + d_ab[1]])
    assert np.abs(_center(moved, mask) - predicted).max() < 0.003
    assert G.self_check(img, moved, mask, center, d_l, d_ab)["reason"] is None
    before, after = G.rgb_to_oklab(img), G.rgb_to_oklab(moved)
    added = after[..., 1] - gain * before[..., 1]           # 밝기 배율을 뺀 뒤 더해진 색 방향
    flat = _inside(mask, 3) & ~_grown(shade, 4)
    assert added[flat].mean() == pytest.approx(0.02, abs=0.0015)
    assert added[_inside(shade, 2)].mean() == pytest.approx(0.6 * 0.02, abs=0.0015)


# ── 사. 자기 검사, 기록, 결정성 ─────────────────────────────────────────────

def test_sa1_shortfall_from_the_gamut_reverts_to_the_original():
    """사.1: 색역 때문에 목표에 못 간 사진은 원본으로 돌아오고 reverted_shortfall 이 남는다."""
    red = G.rgb_to_oklab(np.array([[225, 20, 30]], np.uint8))[0]
    img, mask = _garment(red, amp=0.05)
    center = _center(img, mask)
    target = center + [0.0, *(0.05 * center[1:] / np.hypot(*center[1:]))]      # 같은 색상각으로 더 진한 색
    result = _triplet(img, mask, seller_color=target)
    photo = result.photos[0]
    assert (photo.record["status"], photo.record["reason"]) == ("reverted", "reverted_shortfall")
    assert photo.image is img
    assert photo.record["plan"]["d_ab"] != [0.0, 0.0]
    assert photo.record["result"]["error_ab"] > G.RECHECK_FLOOR
    assert photo.record["result"]["gamut_reduced_frac"] > 0.9
    assert result.record["status_counts"]["reverted"] == 3


def test_sa1_self_check_reports_each_failure():
    """8절: 윤곽 밖이 바뀌었거나 날아간 화소가 한도 넘게 늘었으면 그 코드가 나온다. 통과하면 None 이다."""
    img, mask = _garment((0.62, 0.03, 0.05))
    center = _center(img, mask)
    moved, _ = G.apply_shift(img, mask, center, -0.05, (0.0, 0.0))
    assert G.self_check(img, moved, mask, center, -0.05, (0.0, 0.0))["reason"] is None

    leaked = moved.copy()
    leaked[0, 0, 0] ^= 1                                     # 윤곽에서 먼 구석의 한 화소
    check = G.self_check(img, leaked, mask, center, -0.05, (0.0, 0.0))
    assert (check["reason"], check["outside_changed_pixels"]) == ("reverted_outside_changed", 1)

    burnt = moved.copy()
    burnt[_box((60, 80, 60, 80))] = 255                      # 옷 넓이의 1.9%를 날렸다
    assert G.self_check(img, burnt, mask, center, -0.05, (0.0, 0.0))["reason"] == "reverted_clip_guard"

    assert G.self_check(img, moved, mask, center, -0.08, (0.0, 0.0))["reason"] == "reverted_shortfall"


def test_sa1_self_check_sees_one_changed_pixel_right_next_to_the_mask():
    """8절 1번: 윤곽 밖은 가장자리 띠를 빼지 않고 전부 본다. 윤곽 바로 바깥 화소 하나가 1단계만 달라도 되돌린다."""
    img, mask = _garment((0.62, 0.03, 0.05))
    center = _center(img, mask)
    moved, _ = G.apply_shift(img, mask, center, -0.05, (0.0, 0.0))
    for spot in ((100, BOX[2] - 1), (BOX[0] - 1, 100), (BOX[1], BOX[3])):
        leaked = moved.copy()
        leaked[spot][1] ^= 1
        check = G.self_check(img, leaked, mask, center, -0.05, (0.0, 0.0))
        assert (check["reason"], check["outside_changed_pixels"]) == ("reverted_outside_changed", 1), spot
    # 윤곽 안에 갇힌 구멍도 윤곽 밖이다.
    hole = mask.copy()
    hole[120, 90] = False
    moved, _ = G.apply_shift(img, hole, center, -0.05, (0.0, 0.0))
    assert G.self_check(img, moved, hole, center, -0.05, (0.0, 0.0))["reason"] is None
    moved[120, 90, 0] ^= 1
    assert G.self_check(img, moved, hole, center, -0.05, (0.0, 0.0))["reason"] == "reverted_outside_changed"


def test_sa1_self_check_limits_from_both_sides():
    """8절 2번과 3번: 다시 잰 오차는 옮긴 양의 15%, 최소 0.003 까지 통과한다. 날아간 화소는 1%p 까지 늘어도 된다."""
    img, mask = _garment((0.62, 0.03, 0.05))
    center = _center(img, mask)
    moved, _ = G.apply_shift(img, mask, center, -0.07, (0.0, 0.0))
    assert G.self_check(img, moved, mask, center, -0.08, (0.0, 0.0))["reason"] is None, "오차 0.010 은 15%(0.012) 안"
    assert G.self_check(img, moved, mask, center, -0.085, (0.0, 0.0))["reason"] == "reverted_shortfall"
    small, _ = G.apply_shift(img, mask, center, -0.010, (0.0, 0.0))
    assert G.self_check(img, small, mask, center, -0.0125, (0.0, 0.0))["reason"] is None, "오차 0.0025 는 바닥 0.003 안"
    assert G.self_check(img, small, mask, center, -0.0145, (0.0, 0.0))["reason"] == "reverted_shortfall"
    for rows, expected in ((8, None), (12, "reverted_clip_guard")):         # 옷 넓이의 0.76%, 1.14%
        burnt = moved.copy()
        burnt[_box((60, 60 + rows, 60, 80))] = 255
        assert G.self_check(img, burnt, mask, center, -0.07, (0.0, 0.0))["reason"] == expected


def test_sa1_self_check_counts_clipping_in_the_rim_band_too():
    """8절 2번: 날아감 증가의 분모와 분자는 윤곽 안 화소 전부다. 가장자리 2화소 띠만 날아가도 그 비율이 그대로 잡힌다."""
    img, mask = _garment((0.80, 0.0, 0.01), amp=0.03)
    rim = mask & ~_inside(mask, 2)                          # 옷의 5.5%
    center = _center(img, mask)
    moved, _ = G.apply_shift(img, mask, center, -0.05, (0.0, 0.0))
    burnt = moved.copy()
    burnt[rim] = 255
    check = G.self_check(img, burnt, mask, center, -0.05, (0.0, 0.0))
    assert check["reason"] == "reverted_clip_guard"
    assert check["clip_growth"] == pytest.approx(rim.sum() / mask.sum())


def test_sa1_self_check_reports_the_first_failure_in_order():
    """8절: 실패가 겹치면 표의 순서대로 먼저 걸린 것 하나가 이유다(윤곽 밖, 날아감과 뭉개짐, 다시 재기)."""
    img, mask = _garment((0.62, 0.03, 0.05))
    center = _center(img, mask)
    moved, _ = G.apply_shift(img, mask, center, -0.05, (0.0, 0.0))
    leaked = moved.copy()
    leaked[0, 0, 0] ^= 1
    burnt = moved.copy()
    burnt[_box((60, 80, 60, 80))] = 255
    both = burnt.copy()
    both[0, 0, 0] ^= 1
    assert G.self_check(img, leaked, mask, center, -0.08, (0.0, 0.0))["reason"] == "reverted_outside_changed"
    assert G.self_check(img, burnt, mask, center, -0.08, (0.0, 0.0))["reason"] == "reverted_clip_guard"
    assert G.self_check(img, both, mask, center, -0.05, (0.0, 0.0))["reason"] == "reverted_outside_changed"


@pytest.mark.parametrize("reason", ["reverted_outside_changed", "reverted_clip_guard", "reverted_shortfall"])
def test_sa1_batch_gives_back_the_original_for_every_self_check_failure(monkeypatch, reason):
    """8절: 자기 검사가 어느 이유로든 실패하면 묶음 함수는 원본 배열을 그대로 돌려주고 그 이유를 남긴다."""
    img, mask = _garment((0.62, 0.03, 0.05))
    real = G.self_check
    monkeypatch.setattr(G, "self_check", lambda *args: {**real(*args), "reason": reason})
    result = _triplet(img, mask, seller_color=_center(img, mask) - [0.10, 0, 0])
    photo = result.photos[0]
    assert (photo.record["status"], photo.record["reason"]) == ("reverted", reason)
    assert photo.image is img
    assert result.record["status_counts"] == {"corrected": 0, "untouched": 0, "skipped": 0, "reverted": 3}


def test_sa1_chroma_move_that_would_blow_out_is_reduced_to_the_clip_limit():
    """14.1절 1번, 지시서 6: 색 방향만 옮기는 사진(밝기 이동 0)도 날아간 화소가 1%p 넘게 늘 요청이면 한도 안까지 줄여서 옮긴다.

    아이보리 옷을 흰색 쪽으로 옮기는 색 방향 이동이다. 전에는 자기 검사가 원본으로 되돌렸다(8절 2번).
    """
    img, mask = _garment((0.975, 0.0, 0.02), amp=0.01)
    center = _center(img, mask)
    assert G.measure_garment_color(img, mask)["blown_frac"] == 0.0
    unguarded, _ = G.apply_shift(img, mask, center, 0.0, (0.0, -0.02))
    assert _blown(unguarded, mask) - _blown(img, mask) > 0.05, "그냥 옮기면 한도를 크게 넘는 그림이어야 한다"
    result = _triplet(img, mask, seller_color=center + [0.0, 0.0, -0.045])
    photo = result.photos[0]
    plan = photo.record["plan"]
    assert (photo.record["status"], photo.record["reason"]) == ("corrected", None)
    assert photo.record["flags"] == ["capped_chroma", "clip_guard_reduced"]
    assert plan["d_l"] == 0.0 and plan["d_l_before_clip_guard"] is None
    assert plan["d_ab_before_clip_guard"] == pytest.approx([0.0, -0.02], abs=1e-9)
    assert plan["d_ab"][0] == pytest.approx(0.0, abs=1e-9) and -0.02 < plan["d_ab"][1] <= -G.MIN_SHIFT
    assert photo.record["result"]["clip_growth"] <= G.CLIP_GROWTH_MAX
    assert _blown(photo.image, mask) - _blown(img, mask) <= G.CLIP_GROWTH_MAX
    ratio = plan["d_ab"][1] / plan["d_ab_before_clip_guard"][1]
    step = 1 / 2 ** G.CLIP_GUARD_ITERATIONS                  # 이분법 12회의 눈금
    further, _ = G.apply_shift(img, mask, center, 0.0, [0.0, (ratio + step) * -0.02])
    assert _blown(further, mask) - _blown(img, mask) > G.CLIP_GROWTH_MAX, "한 눈금만 더 가도 한도를 넘는 자리다"
    assert result.record["status_counts"] == {"corrected": 3, "untouched": 0, "skipped": 0, "reverted": 0}


@pytest.mark.parametrize("chroma_off", [
    (0.03, 0.0),                                            # 전에는 요청 0.05 부터 통째로 되돌려졌다
    (0.0, -0.08),                                           # 전에는 요청 0.03 부터 통째로 되돌려졌다
])
def test_sa1_lightness_and_chroma_together_are_reduced_not_reverted_as_the_request_grows(chroma_off):
    """14.1절 1번: 밝히면서 색 방향도 옮기는 요청을 0.01씩 키워도 적용이 갑자기 0이 되지 않는다.

    한도(6.3절)를 밝기와 색 방향을 함께 옮긴 결과로 재므로 자기 검사(8절 2번)가 되돌릴 일이 없다.
    내보내는 사진의 날아감 증가는 늘 1%p 안이고, 적용된 밝기는 요청이 커져도 줄지 않는다(허용 오차는 이분법 눈금).
    색 방향은 밝기가 쓰고 남은 자리만 쓴다. 그래서 밝기 요청이 커지면 색 방향 적용은 줄고, 자리가 0.003 에 못 미치면 0이 된다.
    """
    img, mask = _garment((0.84, 0.0, 0.005), amp=0.17)       # 주름이 밝은 옷. 밝힐수록 밝은 주름이 날아간다
    center = _center(img, mask)
    lightness, chroma, reduced = [], [], []
    for request in np.arange(1, 11) / 100:
        seller = center + [STUDIO["lightness_tolerance"] + request, *chroma_off]
        photo = _triplet(img, mask, seller_color=seller).photos[0]
        plan = photo.record["plan"]
        assert photo.record["status"] == "corrected", f"요청 {request}: 자기 검사가 되돌리지 않는다"
        assert photo.record["result"]["clip_growth"] <= G.CLIP_GROWTH_MAX
        assert _blown(photo.image, mask) - _blown(img, mask) <= G.CLIP_GROWTH_MAX
        assert _center(photo.image, mask)[0] - center[0] == pytest.approx(plan["d_l"], abs=0.003)
        assert seller[0] <= 1.0
        lightness.append(plan["d_l"])
        chroma.append(math.hypot(*plan["d_ab"]))
        reduced.append(plan["d_ab_before_clip_guard"] is not None)
        if reduced[-1]:
            assert chroma[-1] < math.hypot(*plan["d_ab_before_clip_guard"])
            assert "clip_guard_reduced" in photo.record["flags"]
    resolution = 0.10 / 2 ** G.CLIP_GUARD_ITERATIONS          # 이분법 12회의 눈금
    assert lightness[0] == pytest.approx(0.01) and lightness[-1] > 0.04
    assert all(later >= earlier - resolution for earlier, later in zip(lightness, lightness[1:]))
    assert chroma[0] >= G.MIN_SHIFT and not reduced[0], "작은 요청에서는 색 방향도 다 옮긴다"
    assert reduced[-1] and chroma[-1] == 0.0, "밝기가 한도까지 차면 색 방향이 들어갈 자리가 없다"
    assert lightness[-1] < 0.10 and lightness[-1] == pytest.approx(lightness[-2], abs=resolution), "밝기도 한도에서 멈춘다"


def _mixed_batch():
    base = np.array((0.60, 0.02, 0.04))
    img, mask = _garment(base)
    far, _ = _garment(base + [0.18, 0.0, 0.0])
    thin, thin_mask = _garment(base, box=(20, 220, 90, 98))
    outfit, top, bottom = _outfit(top_color=base)
    return [_photo("s0", img, mask), _photo("s1", img, mask), _photo("s2", img, mask),
            _photo("far", far, mask, "styling"), _photo("thin", thin, thin_mask, "product"),
            _photo("two", outfit, [top, bottom], "styling"), _photo("none", img, [], "styling")], base


def test_sa2_same_input_gives_the_same_output():
    """사.2, 지시서 10: 같은 입력을 두 번 넣으면 결과 바이트와 기록이 같다."""
    photos, base = _mixed_batch()
    first = G.correct_color_option(photos, upload_color=base)
    second = G.correct_color_option(photos, upload_color=base)
    assert first.record == second.record
    for a, b in zip(first.photos, second.photos):
        assert a.record == b.record
        assert np.array_equal(a.image, b.image)
    assert first.record["status_counts"] == {"corrected": 1, "untouched": 4, "skipped": 2, "reverted": 0}


def _assert_same_batch_by_photo_key(first, second):
    before = {p.record["key"]: p for p in first.photos}
    after = {p.record["key"]: p for p in second.photos}
    assert before.keys() == after.keys()
    for key in before:
        assert before[key].record == after[key].record, key
        assert np.array_equal(before[key].image, after[key].image), key

    def normalized(record):
        # 이 두 목록은 입력 순서대로 기록한다. 다른 기록은 값과 목록 순서까지 그대로 견준다.
        return {**record,
                "studio": {**record["studio"], "dropped_keys": sorted(record["studio"]["dropped_keys"])},
                "multicolor": {**record["multicolor"], "keys": sorted(record["multicolor"]["keys"])}}

    assert normalized(first.record) == normalized(second.record)


def test_sa2_photo_order_does_not_change_sibling_results():
    """2.5절, 사.2: 밝기 0.40, 0.59, 0.70인 세 형제는 어느 순서로 받아도 같은 사진을 고치고 같은 이유를 남긴다."""
    photos = [_photo(f"p{i}", *_garment((lightness, 0.0, 0.0), amp=0.0))
              for i, lightness in enumerate((0.40, 0.59, 0.70))]
    first = G.correct_color_option(photos, seller_color=(0.50, 0.0, 0.0))
    assert _outcomes(first) == [("skipped", "off_siblings"), ("corrected", None), ("skipped", "off_siblings")]
    for order in itertools.permutations(photos):
        result = G.correct_color_option(order, seller_color=(0.50, 0.0, 0.0))
        assert [p.record["key"] for p in result.photos] == [p.key for p in order]
        _assert_same_batch_by_photo_key(first, result)


@pytest.mark.parametrize("case", ["mixed", "dropped_studio", "multicolor"])
def test_sa2_photo_order_preserves_pixels_and_records(case):
    """사.2, 9.2절: 사진 순서가 달라도 화소와 기록은 같다. 빠진 사진과 여러 색 사진의 열쇠 목록은 내용으로 견준다."""
    if case == "mixed":
        photos, upload = _mixed_batch()
    elif case == "dropped_studio":
        upload = np.array((0.60, 0.02, 0.04))
        photos = [_photo(f"p{i}", *_garment(upload + [0.0, offset, 0.0], amp=0.0))
                  for i, offset in enumerate((0.0, 0.0, 0.0, 0.04, 0.045))]
    else:
        upload = np.array((0.80, 0.0, 0.03))
        right = np.broadcast_to(np.arange(W)[None, :] >= BOX[2] + 66, (H, W))
        front, mask = _two_color(upload, upload + [0.0, 0.035, 0.0], right)
        plain, _ = _garment(upload)
        photos = ([_photo(f"front{i}", front, mask) for i in range(2)]
                  + [_photo(f"plain{i}", plain, mask) for i in range(3)])

    first = G.correct_color_option(photos, upload_color=upload)
    if case == "dropped_studio":
        assert len(first.record["studio"]["dropped_keys"]) == 2
    elif case == "multicolor":
        assert len(first.record["multicolor"]["keys"]) == 2
    for order in (list(reversed(photos)), photos[2:] + photos[:2], photos[::2] + photos[1::2]):
        result = G.correct_color_option(order, upload_color=upload)
        assert [p.record["key"] for p in result.photos] == [p.key for p in order]
        _assert_same_batch_by_photo_key(first, result)


@pytest.mark.parametrize("count", [3, 12])
@pytest.mark.parametrize("moving", [False, True])
def test_sa_batch_releases_pixel_regions_between_photos(monkeypatch, count, moving):
    """0절, 사.2: 잰 값만 묶음에 남긴다. 기준 색을 정할 때와 각 사진 적용 뒤에는 화소 영역이 살아 있지 않다."""
    img, mask = _garment((0.62, 0.03, 0.05))
    photos = [_photo(f"p{i}", img.copy(), mask.copy()) for i in range(count)]
    seller = _center(img, mask) - [0.10 if moving else 0.0, 0.0, 0.0]
    refs = []
    applying = False
    real_region, real_reference, real_correct = G._region, G.resolve_reference, G._correct

    def assert_released():
        gc.collect()
        assert not any(ref() is not None for ref in refs), "지난 사진의 화소 배열을 묶음에 보관하면 안 된다"

    def tracked_region(*args):
        if applying and not moving:
            pytest.fail("허용 폭 안의 사진은 화소 영역을 다시 만들 필요가 없다")
        region = real_region(*args)
        refs.append(weakref.ref(region))
        return region

    def reference(*args, **kwargs):
        nonlocal applying
        assert refs, "측정에서 영역을 만든 묶음이어야 한다"
        assert_released()
        applying = True
        return real_reference(*args, **kwargs)

    def correct(*args):
        assert_released()
        real_correct(*args)
        assert_released()

    monkeypatch.setattr(G, "_region", tracked_region)
    monkeypatch.setattr(G, "resolve_reference", reference)
    monkeypatch.setattr(G, "_correct", correct)
    result = G.correct_color_option(photos, seller_color=seller)
    assert_released()
    assert [p.record["status"] for p in result.photos] == ["corrected" if moving else "untouched"] * count


def test_sa3_records_carry_what_was_done_and_why():
    """사.3: 기록에 9절의 항목이 있다. 건너뛴 사진마다 이유 코드가 하나 있다."""
    photos, base = _mixed_batch()
    result = G.correct_color_option(photos, upload_color=base)
    records = {p.record["key"]: p.record for p in result.photos}
    json.dumps([result.record, *records.values()])           # 기록은 그대로 저장할 수 있는 값이다

    for given, record in zip(photos, records.values()):
        assert (record["key"], record["color_option_id"], record["kind"], record["clothing_type"]) == (
            given.key, "c1", given.kind, "top")
        assert record["algorithm_version"] == G.ALGORITHM_VERSION
        assert record["status"] in {"corrected", "untouched", "skipped", "reverted"}
        assert (record["reason"] is None) == (record["status"] == "corrected")
        if record["status"] == "skipped":
            assert record["reason"] in SKIP_REASONS
    assert records["thin"]["reason"] == "mask_thin"
    assert records["two"]["mask"]["selection"] == "closest_to_upload"
    assert len(records["two"]["mask"]["candidate_distances"]) == 2
    assert (records["two"]["status"], records["two"]["reason"]) == ("untouched", "within_tolerance")
    assert records["none"]["reason"] == "no_candidate" and records["none"]["mask"]["candidates_received"] == 0

    moved = records["far"]
    assert moved["status"] == "corrected"
    assert set(moved["mask"]) == {"candidates_received", "selection", "candidate_distances",
                                  "removed_fragments", "removed_pixels", "quality", "quality_codes"}
    assert set(moved["mask"]["quality"]) == {
        "area_frac", "fragments", "core_share", "core_pixels", "core_px_1536", "far_share", "w_spread90",
        "ab_spread90", "rim_in_far", "rim_excess"}
    assert set(moved["measure"]) == {"center", "first_pass_center", "same", "cw_c", "cw_l", "blown_frac",
                                     "crushed_frac", "sibling_count", "sibling_d_l", "sibling_d_ab"}
    assert set(moved["plan"]) == {"e_l", "e_c", "lightness_tolerance", "chroma_tolerance", "lightness_cap",
                                  "chroma_cap", "n_l", "n_c", "d_l", "d_ab", "d_l_before_clip_guard",
                                  "d_ab_before_clip_guard"}
    assert set(moved["result"]) == {"remeasured_center", "error_l", "error_ab", "clip_growth",
                                    "gamut_reduced_frac", "changed_pixels", "outside_changed_pixels"}
    assert moved["result"]["outside_changed_pixels"] == 0 and moved["result"]["changed_pixels"] > 0
    assert moved["measure"]["sibling_count"] == 4
    assert records["thin"]["plan"] is None and records["thin"]["result"] is None

    assert set(result.record) == {
        "color_option_id", "photo_count", "reference_source", "reference_color", "reference_reason",
        "redraw_candidate", "studio", "upload_check", "upload_trust", "multicolor", "status_counts",
        "algorithm_version"}
    assert (result.record["color_option_id"], result.record["photo_count"]) == ("c1", 7)
    assert result.record["reference_source"] == "studio" and result.record["studio"]["used"] == 3
    assert sum(result.record["status_counts"].values()) == 7


def test_sa4_wrong_inputs_raise():
    """사.4: 크기가 다른 윤곽, uint8 이 아닌 사진, 모르는 사진 종류는 예외다. 그 밖의 틀린 입력도 조용히 넘어가지 않는다."""
    img, mask = _garment((0.62, 0.03, 0.05))
    bad_inputs = [
        [_photo("p", img, mask[:-1])],                                  # 크기가 다른 윤곽
        [_photo("p", img, mask.astype(np.uint8))],                      # bool 이 아닌 윤곽
        [_photo("p", img.astype(np.float32), mask)],                    # uint8 이 아닌 사진
        [_photo("p", img[..., 0], mask)],                               # 채널이 없는 사진
        [_photo("p", img, mask, kind="horizon")],                       # 모르는 사진 종류
        [_photo("p", img, mask), _photo("q", img, mask, option="c2")],  # 색상 옵션이 섞였다
        [_photo("p", img, mask), _photo("p", img, mask)],               # 열쇠가 겹친다
        [],
    ]
    for photos in bad_inputs:
        with pytest.raises(ValueError):
            G.correct_color_option(photos)
    for color in ((0.6, 0.0), (0.6, 0.0, float("nan")), "red"):
        with pytest.raises(ValueError):
            G.correct_color_option([_photo("p", img, mask)], upload_color=color)
        with pytest.raises(ValueError):
            G.correct_color_option([_photo("p", img, mask)], seller_color=color)
    with pytest.raises(ValueError):
        G.apply_shift(img, mask[:, :-1], (0.6, 0.0, 0.0), -0.05, (0.0, 0.0))
    with pytest.raises(ValueError):
        G.measure_garment_color(img.astype(np.int32), mask)
    with pytest.raises(ValueError):
        G.measure_garment_color(img, _box((20, 220, 90, 93)))            # 속이 비는 가는 윤곽은 색을 잴 수 없다
    with pytest.raises(ValueError):
        G.apply_shift(img, mask, (0.995, 0.0, 0.0), 0.01, (0.0, 0.0))    # 가운데 밝기가 0.99 이상이면 밝힐 수 없다


@pytest.mark.parametrize("lightness,valid", [(-0.001, False), (-0.2, False), (1.001, False), (1.4, False),
                                             (0.0, True), (0.001, True), (1.0, True)])
def test_sa4_optional_input_colors_need_a_lightness_from_zero_to_one(lightness, valid):
    """사.4, 14.4절 2번(1.4절, 14.1절 5번을 고침): 올린 색과 셀러 색의 밝기가 0 이상 1 이하가 아니면 묶음 함수가 예외를 던진다."""
    img, mask = _garment((0.62, 0.03, 0.05))
    photos = [_photo(f"p{i}", img, mask) for i in range(3)]
    color = (lightness, 0.01, 0.02)
    for colors in ({"upload_color": color}, {"seller_color": color},
                   {"upload_color": color, "seller_color": (0.62, 0.03, 0.05)},
                   {"upload_color": (0.62, 0.03, 0.05), "seller_color": color}):
        if valid:
            assert len(G.correct_color_option(photos, **colors).photos) == 3
        else:
            with pytest.raises(ValueError, match="lightness"):
                G.correct_color_option(photos, **colors)


def test_sa4_pure_black_input_color_runs_like_any_other_color():
    """사.4, 14.4절 2번: 순수 검정(#000000, 밝기 0)은 정상 색이다. 올린 색이나 셀러 색으로 넣어도 예외 없이 돈다.

    유채색 옷에 검정 올린 색이면 색 방향이 멀어 기준 없음(reference_off_upload)으로 건너뛰고, 검정 옷이면 그대로 둔다.
    """
    black = (0.0, 0.0, 0.0)
    img, mask = _garment((0.62, 0.03, 0.05))                 # 진하기 0.058
    photos = [_photo(f"p{i}", img, mask) for i in range(3)]
    colored = G.correct_color_option(photos, upload_color=black)
    assert _outcomes(colored) == [("skipped", "reference_off_upload")] * 3
    assert colored.record["upload_check"]["d_ab"] == pytest.approx(math.hypot(0.03, 0.05), abs=0.003)
    assert all(out.image is given.image for out, given in zip(colored.photos, photos))
    seller = G.correct_color_option(photos, seller_color=black)
    assert (seller.record["reference_source"], seller.record["reference_color"]) == ("seller", [0.0, 0.0, 0.0])
    assert _outcomes(seller) == [("corrected", None)] * 3, "검정 쪽으로 옮길 수 있는 만큼(상한까지) 옮긴다"

    dark_img, dark_mask = _garment((0.15, 0.0, 0.0))         # 검정 옷
    dark_photos = [_photo(f"p{i}", dark_img, dark_mask) for i in range(3)]
    dark = G.correct_color_option(dark_photos, upload_color=black)
    assert _outcomes(dark) == [("untouched", "within_tolerance")] * 3
    assert dark.record["reference_source"] == "studio" and dark.record["upload_check"]["pulled"] is False
    assert dark.record["upload_check"]["d_ab"] < 0.003
    assert dark.record["upload_check"]["l_diff"] == pytest.approx(0.15, abs=0.003)
    assert all(out.image is given.image for out, given in zip(dark.photos, dark_photos))
    dark_seller = G.correct_color_option(dark_photos, seller_color=black)
    assert dark_seller.record["reference_color"] == [0.0, 0.0, 0.0]
    assert all(p.record["status"] in {"corrected", "untouched", "reverted"} for p in dark_seller.photos)


@pytest.mark.parametrize("shape", [(0, 10), (10, 0)])
def test_sa4_zero_size_photo_raises_instead_of_crashing(shape):
    """사.4, 1.4절: 높이나 폭이 0인 사진과 윤곽은 예외다. 그대로 두면 cv2 안에서 프로세스가 죽어 잡을 수 없다."""
    img, mask = np.zeros((*shape, 3), np.uint8), np.zeros(shape, bool)
    with pytest.raises(ValueError):
        G.correct_color_option([_photo(f"p{i}", img, mask) for i in range(3)], seller_color=(0.5, 0.0, 0.0))
    with pytest.raises(ValueError):
        G.correct_color_option([_photo(f"p{i}", img, []) for i in range(3)], seller_color=(0.5, 0.0, 0.0))
    with pytest.raises(ValueError):
        G.trim_specks(mask)
    with pytest.raises(ValueError):
        G.measure_mask_quality(img, mask)
