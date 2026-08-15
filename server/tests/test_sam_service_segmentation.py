"""SAM2 서비스 — 잘라낸 로직이 torch 없이도 맞는가, 그리고 경계가 지켜지는가.

여기서 실제 SAM2 추론은 돌리지 않는다(모델 로드 6초 + 뷰당 수십 초). 대신 두 가지를 잠근다:

1. **순수 기하 로직** — 마스크 채우기·후보 걸러내기·컷아웃 인코딩. 예전에 (0,0) 한 점에서만
   flood fill 해 배경을 내부로 세던 버그가 있었으므로 그 케이스를 명시적으로 고정한다.
2. **의존 방향** — main backend 는 torch 도, sam_service 도 임포트하면 안 된다. 이게 깨지면
   prod 이미지에 torch 가 따라 들어오거나, 임포트 시점에 죽는다.
"""
import io
import pathlib
import re

import cv2
import numpy as np
import pytest
from PIL import Image

from sam_service import segmentation as seg


# ── 마스크 기하 (torch 불필요) ───────────────────────────────────────────────

def _ring(h=60, w=40):
    """가운데가 빈 사각 링 — 구멍 채우기가 실제로 필요한 최소 형태."""
    m = np.zeros((h, w), bool)
    m[10:50, 8:32] = True
    m[20:40, 15:25] = False
    return m


def test_fill_holes_closes_the_interior():
    m = _ring()
    assert not m[30, 20]
    assert seg.fill_holes(m)[30, 20]


def test_fill_holes_does_not_swallow_background_disconnected_from_the_origin():
    """(0,0) 한 점 flood fill 이던 시절의 버그 — 모서리와 안 닿는 배경이 내부로 세졌다."""
    m = np.zeros((60, 60), bool)
    m[5:55, 5:55] = True          # 프레임을 거의 덮되 테두리에는 안 닿음
    m[20:40, 20:40] = False       # 진짜 구멍
    filled = seg.fill_holes(m)
    assert filled[30, 30]         # 구멍은 채워지고
    assert not filled[0, 0]       # 바깥 배경은 그대로 배경
    assert not filled[2, 30]


def test_fill_holes_leaves_a_solid_mask_untouched():
    m = np.zeros((40, 40), bool)
    m[10:30, 10:30] = True
    assert np.array_equal(seg.fill_holes(m), m)


@pytest.mark.parametrize("stats,expected", [
    ({"areaFrac": 0.01}, "too_small"),
    ({"areaFrac": 0.80}, "near_full_frame"),
    ({"areaFrac": 0.30, "borderTouchFrac": 0.9}, "border_dominated"),
    ({"areaFrac": 0.30, "borderTouchFrac": 0.1, "aspectHW": 9}, "impossible_aspect"),
    ({"areaFrac": 0.30, "borderTouchFrac": 0.1, "aspectHW": 0.05}, "impossible_aspect"),
])
def test_implausible_candidates_are_rejected_with_a_named_reason(stats, expected):
    ok, why = seg.plausible(stats)
    assert not ok and why == expected


def test_a_normal_garment_shaped_candidate_passes():
    ok, why = seg.plausible({"areaFrac": 0.45, "borderTouchFrac": 0.05, "aspectHW": 1.3})
    assert ok and why == ""


def test_dedupe_drops_near_identical_masks_and_keeps_distinct_ones():
    a = np.zeros((50, 50), bool); a[10:40, 10:40] = True
    almost = a.copy(); almost[10, 10] = False
    other = np.zeros((50, 50), bool); other[0:8, 0:8] = True
    kept = seg.dedupe([a, almost, other])
    assert len(kept) == 2


def _centre_blob(h=100, w=80):
    m = np.zeros((h, w), bool)
    m[20:80, 15:65] = True
    return m


def test_select_prefers_the_largest_plausible_centre_candidate():
    big, small = _centre_blob(), np.zeros((100, 80), bool)
    small[45:55, 35:45] = True
    mask, info = seg.select_garment_mask([small, big])
    assert info["reason"] == "selected"
    assert mask.sum() == big.sum()


def test_select_ignores_candidates_that_miss_the_framed_subject():
    corner = np.zeros((100, 80), bool)
    corner[0:30, 0:25] = True          # plausible 하지만 중앙에 없음
    mask, info = seg.select_garment_mask([corner])
    assert mask is None and info["reason"] == "no_plausible_candidate"


def test_select_reports_when_nothing_survives():
    mask, info = seg.select_garment_mask([])
    assert mask is None and info["reason"] == "no_plausible_candidate"


# ── 끊어진 조각 정리 ─────────────────────────────────────────────────────────
#
# 합성 도형으로만 검증한다. grey-knit 한 장에 맞춰 규칙을 깎으면 다음 옷에서 끈이 지워진다.

def _garment(h=400, w=300):
    m = np.zeros((h, w), bool)
    m[60:340, 60:240] = True                  # 280x180 = 50,400 px
    return m


def test_tiny_far_speckles_are_removed():
    m = _garment()
    m[5:9, 5:9] = True                        # 16px, 멀리
    m[380:384, 290:294] = True
    cleaned, info = seg.prune_disconnected_debris(m)
    assert info["components"] == 3 and info["kept"] == 1
    assert not cleaned[6, 6] and not cleaned[381, 291]


def test_a_legitimate_nearby_secondary_part_is_retained():
    """밑단 아래 떨어져 있는 자락 — 실제 해상도에서 근접 밴드 안에 들어온다.

    근접 밴드는 프레임 대각선 비례라서 픽스처도 실제 촬영 해상도여야 의미가 있다.
    400x300 같은 장난감 프레임에서는 밴드가 10px 라 35px 짜리 조각도 '멀다'가 된다.
    """
    m = np.zeros((2048, 1536), bool)
    m[300:1700, 200:1300] = True              # garment
    m[1730:1810, 600:900] = True              # 24,000px, 밑단에서 30px
    cleaned, info = seg.prune_disconnected_debris(m)
    assert info["kept"] == 2
    assert cleaned[1770, 750]
    assert info["removedFrac"] == 0.0


def test_a_large_but_distant_blob_is_removed():
    """면적은 커도 옷에서 멀면 배경·행거·다른 물체다."""
    m = np.zeros((400, 300), bool)
    m[200:340, 60:240] = True                 # main
    m[5:60, 5:60] = True                      # 3,025px, 크지만 멀다
    cleaned, info = seg.prune_disconnected_debris(m)
    assert info["kept"] == 1
    assert not cleaned[30, 30]


def test_background_island_inside_the_primary_bbox_but_far_from_the_mask_is_removed():
    """이번 회귀의 핵심.

    실제 업로드 이미지에서 옷 bbox 가 프레임을 거의 다 덮어, 배경 조각이 bbox '안'에
    들어와 버렸다. bbox 기준 거리는 전부 0 이 되어 규칙이 무력화됐다. 마스크까지의
    실제 거리로 재면 이 조각은 여전히 멀다.
    """
    m = np.zeros((400, 300), bool)
    # ㄷ 자 모양 — bbox 는 프레임 대부분을 덮지만 가운데는 비어 있다
    m[20:380, 20:60] = True
    m[20:380, 240:280] = True
    m[340:380, 20:280] = True
    island = (slice(150, 210), slice(130, 190))
    m[island] = True                           # bbox 안, 마스크에서는 멀다

    n, _, stats, _ = cv2.connectedComponentsWithStats(m.astype(np.uint8), 8)
    primary = int(np.argmax(stats[1:, cv2.CC_STAT_AREA])) + 1
    px, py, pw, ph = (int(v) for v in stats[primary, :4])
    assert px <= 130 and py <= 150 and px + pw >= 190 and py + ph >= 210, \
        "fixture invalid: island must sit INSIDE the primary bbox"

    cleaned, info = seg.prune_disconnected_debris(m)
    assert info["kept"] == 1
    assert not cleaned[180, 160]                # 섬 제거
    assert cleaned[200, 30] and cleaned[360, 150]   # 옷 본체 그대로


def test_a_component_grazing_the_garment_but_extending_far_away_is_removed():
    """한 점만 스치는 배경 조각 — 최소거리는 작지만 몸통이 멀다(실측 4px / 평균 197px)."""
    m = np.zeros((600, 400), bool)
    m[300:560, 80:320] = True                  # garment
    m[20:290, 150:250] = True                  # 위로 길게 뻗은 배경 — 10px 간격
    cleaned, info = seg.prune_disconnected_debris(m)
    assert info["kept"] == 1
    assert not cleaned[100, 200]


def test_a_strap_lying_alongside_the_garment_is_retained():
    """끈은 옷을 따라 나란히 놓인다 — 픽셀 대부분이 근접 밴드 안."""
    m = np.zeros((600, 400), bool)
    m[200:560, 100:300] = True                 # garment
    m[220:520, 82:96] = True                   # 세로 끈, 4px 간격
    cleaned, info = seg.prune_disconnected_debris(m)
    assert info["kept"] == 2
    assert cleaned[400, 88]
    assert info["removedFrac"] == 0.0


def test_a_small_nearby_speck_is_still_removed():
    """가깝다고 다 남기지 않는다 — 면적 조건과 거리 조건을 모두 넘어야 한다."""
    m = _garment()
    m[345:349, 150:154] = True                # 붙어 있지만 16px
    cleaned, info = seg.prune_disconnected_debris(m)
    assert info["kept"] == 1
    assert not cleaned[346, 151]


def test_pruning_never_touches_the_primary_silhouette():
    m = _garment()
    before = m.copy()
    m[5:9, 5:9] = True
    cleaned, _ = seg.prune_disconnected_debris(m)
    assert np.array_equal(cleaned, before)     # 제거된 건 debris 뿐


def test_a_garment_with_a_hole_keeps_its_hole():
    """구멍은 컴포넌트가 아니다 — 정리 규칙이 건드릴 대상이 아니다."""
    m = _garment()
    m[150:200, 120:180] = False
    cleaned, info = seg.prune_disconnected_debris(m)
    assert info["kept"] == 1
    assert not cleaned[175, 150]
    assert np.array_equal(cleaned, m)


def test_a_single_component_mask_is_returned_untouched():
    m = _garment()
    cleaned, info = seg.prune_disconnected_debris(m)
    assert np.array_equal(cleaned, m)
    assert info["kept"] == 1 and info["removedFrac"] == 0.0


def test_an_empty_mask_does_not_crash():
    cleaned, info = seg.prune_disconnected_debris(np.zeros((20, 20), bool))
    assert not cleaned.any() and info["kept"] == 0


def test_the_rule_is_scale_invariant():
    """같은 형상이면 해상도가 달라도 같은 판단이어야 한다."""
    small = _garment(200, 150); small[3:5, 3:5] = True
    big = _garment(800, 600); big[12:20, 12:20] = True
    assert seg.prune_disconnected_debris(small)[1]["kept"] == 1
    assert seg.prune_disconnected_debris(big)[1]["kept"] == 1


def test_selection_reports_what_it_pruned():
    m = _garment()
    m[5:9, 5:9] = True
    _mask, info = seg.select_garment_mask([m])
    assert info["prune"]["components"] == 2 and info["prune"]["kept"] == 1


def test_pruning_does_not_read_or_change_rgb():
    """알파만 만진다 — 유지된 화소의 RGB 는 원본 그대로."""
    bgr = _bgr(400, 300)
    m = _garment(400, 300); m[5:9, 5:9] = True
    cleaned, _ = seg.prune_disconnected_debris(m)
    rgba = np.asarray(Image.open(io.BytesIO(seg.to_cutout_png(bgr, cleaned, max_px=0))))
    assert tuple(rgba[200, 150, :3]) == tuple(bgr[200, 150, ::-1])
    assert rgba[6, 6, 3] == 0                  # debris 는 투명


# ── 오염 후보 vs 깨끗한 후보 (융합 배경 결함) ────────────────────────────────
#
# 실제 결함의 형태: SAM 이 "옷"과 "옷 + 어깨 위 배경(가는 다리로 연결)" 을 둘 다 내놓는데,
# 오염된 쪽이 오염 때문에 더 크다. 면적만 보면 오염된 쪽이 이기고, 연결요소 정리로는
# 못 지운다 — 배경이 주 성분 안에 들어와 있기 때문이다.

def _clean_garment_candidate(h=2000, w=2200):
    m = np.zeros((h, w), bool)
    m[400:1700, 300:1200] = True               # 몸통: bbox 를 꽉 채운다 → solidity 1.0
    return m


def _contaminated_candidate(h=2000, w=2200):
    """같은 옷 + 어깨 위 배경 덩어리, 가는 다리로 연결.

    실측 결함의 비율을 맞춘 픽스처: 배경이 bbox 를 위·옆으로 늘려 solidity 를 1.00 → 0.51
    로 떨어뜨린다(실제 오염 후보는 0.596이었다). 오염량은 dedupe(IoU 0.80)가 두 후보를
    하나로 합치지 않을 만큼은 되어야 한다 — 실제 이미지에서도 IoU 는 0.786 이었다.
    """
    m = _clean_garment_candidate(h, w)
    m[40:440, 1250:2100] = True                # 배경 덩어리
    m[400:440, 1200:1250] = True               # 다리(bridge)
    return m


def test_the_contaminated_candidate_is_larger_but_less_solid():
    """픽스처가 실제 결함을 재현하는지부터 확인 — 아니면 아래 테스트는 아무것도 안 지킨다."""
    clean, dirty = _clean_garment_candidate(), _contaminated_candidate()
    assert dirty.sum() > clean.sum()
    n, _, _, _ = cv2.connectedComponentsWithStats(dirty.astype(np.uint8), 8)
    assert n - 1 == 1, "오염부는 반드시 연결돼 있어야 한다(연결요소 정리로는 못 지움)"
    assert seg._primary_solidity(dirty) < seg.SOLIDITY_MIN <= seg._primary_solidity(clean)


def test_selection_prefers_the_clean_candidate_over_the_larger_contaminated_one():
    mask, info = seg.select_garment_mask([_contaminated_candidate(), _clean_garment_candidate()])
    assert info["gate"] == "solid"
    assert not mask[200, 1100], "어깨 위 배경이 선택돼 버렸다"
    assert mask[1000, 700]                      # 옷 본체는 남는다
    assert int(mask.sum()) == int(_clean_garment_candidate().sum())


def test_selection_falls_back_when_nothing_is_solid():
    """열린 가디건·바지처럼 bbox 를 못 채우는 옷 — 규칙이 후보를 전멸시키면 안 된다."""
    trousers = np.zeros((2000, 1800), bool)
    trousers[400:1700, 700:900] = True          # 가랑이 사이가 벌어진 바지
    trousers[400:1700, 1300:1500] = True
    trousers[400:520, 700:1500] = True          # 허리
    assert seg._primary_solidity(trousers) < seg.SOLIDITY_MIN
    mask, info = seg.select_garment_mask([trousers])
    assert info["gate"] == "no_solid_candidate_fallback"
    assert mask is not None and mask[1000, 800] and mask[1000, 1400]   # 두 다리 모두 살아있다


def test_a_loose_silhouette_is_not_rejected_merely_for_being_large():
    """면적이 커도 solid 하면 그대로 선택된다 — 오버사이즈 패널티가 아니다."""
    big = np.zeros((2000, 1500), bool)
    big[200:1800, 150:1350] = True              # 프레임의 64%
    mask, info = seg.select_garment_mask([big])
    assert info["gate"] == "solid"
    assert int(mask.sum()) == int(big.sum())


def test_a_garment_touching_the_frame_edge_is_still_allowed():
    m = np.zeros((2000, 1500), bool)
    m[0:1700, 300:1200] = True                  # 위쪽 프레임에 닿음
    mask, info = seg.select_garment_mask([m])
    assert mask is not None and info["gate"] == "solid"


def test_thin_garment_structure_is_not_penalised_by_the_solidity_preference():
    """소매·끈이 가늘어도 주 성분이 채워져 있으면 통과 — solidity 는 주 성분 기준이다."""
    m = _clean_garment_candidate()
    m[700:1500, 250:300] = True                 # 몸통에 붙은 가는 소매
    assert seg._primary_solidity(m) >= seg.SOLIDITY_MIN
    mask, info = seg.select_garment_mask([m])
    assert info["gate"] == "solid" and mask[1000, 270]


def test_disconnected_debris_cleanup_still_runs_after_selection():
    m = _clean_garment_candidate()
    m[50:70, 50:70] = True                      # 멀리 떨어진 speck
    mask, info = seg.select_garment_mask([m])
    assert info["prune"]["kept"] == 1
    assert not mask[60, 60]


# ── 컷아웃 인코딩 ────────────────────────────────────────────────────────────

def _bgr(h=100, w=80):
    img = np.zeros((h, w, 3), np.uint8)
    img[:, :] = (10, 20, 200)          # 배경 (BGR)
    img[20:80, 15:65] = (200, 150, 40)  # 옷
    return img


def test_cutout_is_rgba_with_hard_transparency_outside_the_mask():
    png = seg.to_cutout_png(_bgr(), _centre_blob())
    im = Image.open(io.BytesIO(png))
    assert im.mode == "RGBA"
    a = np.asarray(im)[..., 3]
    assert a[5, 5] == 0 and a[95, 75] == 0      # 바깥은 완전 투명
    assert a[50, 40] == 255                      # 안쪽은 완전 불투명


def test_cutout_never_leaves_partial_alpha_outside_the_mask():
    """페더가 바깥으로 번지면 배경 텍스처가 반투명하게 남는다 — 실제로 관찰된 결함."""
    a = np.asarray(Image.open(io.BytesIO(
        seg.to_cutout_png(_bgr(), _centre_blob()))))[..., 3]
    outside = ~_centre_blob()
    assert a[outside].max() == 0


def test_cutout_preserves_the_original_garment_pixels():
    """생성형 재작화 금지 — 유지 영역 RGB 는 원본 그대로여야 한다."""
    bgr = _bgr()
    rgba = np.asarray(Image.open(io.BytesIO(seg.to_cutout_png(bgr, _centre_blob()))))
    assert tuple(rgba[50, 40, :3]) == (40, 150, 200)      # BGR -> RGB


def test_cutout_downscales_only_when_it_exceeds_the_cap():
    big = np.zeros((3000, 2000, 3), np.uint8)
    mask = np.zeros((3000, 2000), bool); mask[500:2500, 400:1600] = True
    im = Image.open(io.BytesIO(seg.to_cutout_png(big, mask, max_px=1024)))
    assert max(im.size) == 1024
    small = Image.open(io.BytesIO(seg.to_cutout_png(_bgr(), _centre_blob(), max_px=1024)))
    assert small.size == (80, 100)                        # 작은 건 확대하지 않는다


# ── 캐시 정체성 ──────────────────────────────────────────────────────────────

def test_cache_key_changes_with_source_view_and_model():
    a = seg.cache_key("abc", "Front")
    assert a == seg.cache_key("abc", "Front")              # 같은 입력 → 같은 키
    assert a != seg.cache_key("abc", "Back")               # 뷰가 다르면 다른 컷아웃
    assert a != seg.cache_key("def", "Front")              # 소스가 바뀌면 무효
    assert a != seg.cache_key("abc", "Front", model_version="other@v2")


def test_source_fingerprint_is_content_addressed():
    """asset id 가 아니라 내용 — 같은 id 로 다른 사진이 올라오면 캐시가 재사용되면 안 된다."""
    assert seg.source_fingerprint(b"x") != seg.source_fingerprint(b"y")
    assert seg.source_fingerprint(b"x") == seg.source_fingerprint(b"x")


# ── 이미지 인코딩은 뷰당 정확히 1회 ──────────────────────────────────────────
#
# 실제 SAM2 를 돌리지 않고 `Sam2Segmenter.candidates` 의 배선만 검증한다. 모델·프로세서를
# 가짜로 끼워 호출 횟수를 세는 것이 요점 — 예전 구현은 프롬프트 배치마다 이미지를 16장씩
# 넘겨서 인코더가 64번 돌았고, 그건 코드를 읽어서가 아니라 세어봐야 드러난다.

class _FakeTensor:
    """expand/repeat/to 만 흉내내는 최소 스텁."""

    def __init__(self, shape):
        self.shape = tuple(shape)

    def expand(self, *shape):
        return _FakeTensor(shape)

    def repeat(self, *args):
        return _FakeTensor((self.shape[0] * args[0], *self.shape[1:]))

    def to(self, *_a, **_k):
        return self


class _FakeInputs(dict):
    def to(self, *_a, **_k):
        return self


class _FakeMaskTensor:
    """post_process_masks 반환값 — 실제 경로가 쓰는 ndim/인덱싱/.cpu().numpy() 만 흉내낸다."""

    def __init__(self, arr):
        self._a = arr

    @property
    def ndim(self):
        return self._a.ndim

    def __getitem__(self, i):
        return _FakeMaskTensor(self._a[i])

    def cpu(self):
        return self

    def numpy(self):
        return self._a


class _FakeProcessor:
    def __init__(self, log):
        self.log = log

    def __call__(self, images=None, input_points=None, input_labels=None,
                 original_sizes=None, return_tensors=None):
        if images is not None:
            self.log.append(("processor_image", 1 if not isinstance(images, list) else len(images)))
            return _FakeInputs(pixel_values=_FakeTensor((1, 3, 1024, 1024)),
                               original_sizes=_FakeTensor((1, 2)))
        self.log.append(("processor_prompt", len(input_points)))
        return _FakeInputs(input_points=_FakeTensor((len(input_points), 1, 1, 2)),
                           input_labels=_FakeTensor((len(input_points), 1, 1)),
                           original_sizes=original_sizes)

    def post_process_masks(self, pred_masks, sizes):
        b = pred_masks.shape[0]
        return [_FakeMaskTensor(np.zeros((1, 3, 8, 8), bool)) for _ in range(b)]


class _FakeOut:
    def __init__(self, b):
        self.pred_masks = _FakeTensor((b, 1, 3, 256, 256))
        self.iou_scores = _FakeScores(b)


class _FakeScores:
    def __init__(self, b):
        self._b = b

    def __iter__(self):
        return iter([_FakeScore() for _ in range(self._b)])


class _FakeScore:
    def detach(self):
        return self

    def cpu(self):
        return self

    def numpy(self):
        return np.zeros(3)          # all below MIN_IOU_SCORE -> no masks kept

    def reshape(self, *_a):
        return np.zeros(3)


class _FakeModel:
    def __init__(self, log):
        self.log = log

    def get_image_embeddings(self, pixel_values):
        self.log.append(("encode", pixel_values.shape[0]))
        return [_FakeTensor((1, 32, 256, 256)), _FakeTensor((1, 64, 128, 128)),
                _FakeTensor((1, 256, 64, 64))]

    def __call__(self, image_embeddings=None, input_points=None, input_labels=None,
                 pixel_values=None, multimask_output=None, **_kw):
        assert pixel_values is None, "the image must not be re-encoded per prompt batch"
        assert image_embeddings is not None
        b = input_points.shape[0]
        self.log.append(("decode", b))
        return _FakeOut(b)


def _wired_segmenter():
    import contextlib
    s = seg.Sam2Segmenter.__new__(seg.Sam2Segmenter)
    log = []
    s.processor, s.model = _FakeProcessor(log), _FakeModel(log)
    s.device, s.model_id, s.encode_calls = "cpu", "fake", 0
    s._no_grad = contextlib.nullcontext
    return s, log


def test_the_image_encoder_runs_exactly_once_per_view():
    s, log = _wired_segmenter()
    s.candidates(np.zeros((400, 300, 3), np.uint8))
    assert [e for e in log if e[0] == "encode"] == [("encode", 1)]
    assert s.encode_calls == 1


def test_front_and_back_cost_two_encodes_total_not_one_per_prompt():
    s, log = _wired_segmenter()
    for _ in range(2):                          # Front then Back, same process
        s.candidates(np.zeros((400, 300, 3), np.uint8))
    assert len([e for e in log if e[0] == "encode"]) == 2
    assert s.encode_calls == 2


def test_every_prompt_is_still_processed_exactly_once():
    s, log = _wired_segmenter()
    s.candidates(np.zeros((400, 300, 3), np.uint8))
    decoded = sum(b for kind, b in log if kind == "decode")
    assert decoded == seg.GRID * seg.GRID == 64          # none lost, none duplicated


def test_prompt_grouping_is_unchanged():
    s, log = _wired_segmenter()
    s.candidates(np.zeros((400, 300, 3), np.uint8))
    assert [b for kind, b in log if kind == "decode"] == [16, 16, 16, 16]


def test_the_image_is_never_handed_to_the_processor_once_per_prompt():
    """예전 결함의 직접 재발 방지: images=[rgb]*16 이 다시 들어오면 여기서 걸린다."""
    s, log = _wired_segmenter()
    s.candidates(np.zeros((400, 300, 3), np.uint8))
    image_calls = [n for kind, n in log if kind == "processor_image"]
    assert image_calls == [1]


# ── 경계: main backend 는 torch 도 sam_service 도 모른다 ─────────────────────

APP = pathlib.Path(__file__).resolve().parents[1] / "app"


def test_main_backend_never_imports_the_sam_service():
    hits = [str(p.relative_to(APP)) for p in APP.rglob("*.py")
            if re.search(r"^\s*(from|import)\s+sam_service\b",
                         p.read_text(encoding="utf-8", errors="ignore"), re.M)]
    assert not hits, f"main backend imports sam_service: {hits}"


def test_only_embeddings_may_touch_torch_in_the_main_backend():
    """prod 이미지에는 torch 가 없다. 새 모듈이 torch 를 임포트하면 起動 시점에 죽는다."""
    offenders = []
    for p in APP.rglob("*.py"):
        if p.name == "embeddings.py":
            continue        # 기존 lazy-import 예외(선택 의존군), 호출부가 graceful 처리
        if re.search(r"^\s*(from|import)\s+(torch|transformers)\b",
                     p.read_text(encoding="utf-8", errors="ignore"), re.M):
            offenders.append(str(p.relative_to(APP)))
    assert not offenders, f"main backend imports torch/transformers: {offenders}"


SAM = pathlib.Path(__file__).resolve().parents[1] / "sam_service"


def test_the_sam_service_never_imports_the_production_app():
    """반대 방향도 막는다 — 서비스가 app 을 끌어오면 두 배포 단위가 다시 하나가 된다."""
    hits = [str(p.relative_to(SAM)) for p in SAM.rglob("*.py")
            if re.search(r"^\s*(from|import)\s+app\b",
                         p.read_text(encoding="utf-8", errors="ignore"), re.M)]
    assert not hits, f"sam_service imports the production app: {hits}"


def test_the_main_api_image_cannot_ship_the_sam_service():
    ignore = (pathlib.Path(__file__).resolve().parents[1] / ".dockerignore").read_text()
    assert "sam_service" in ignore.split("#")[0] or any(
        ln.strip() == "sam_service" for ln in ignore.splitlines())


def test_sam_dependencies_are_not_in_the_main_backend_project_file():
    """torch 가 pyproject 의 기본 의존성에 들어가면 prod API 이미지가 그대로 무거워진다."""
    import tomllib
    pj = tomllib.loads(
        (pathlib.Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())
    runtime = " ".join(pj["project"]["dependencies"]).lower()
    for banned in ("torch", "transformers", "sam2"):
        assert banned not in runtime, f"{banned} leaked into main runtime dependencies"


def test_segmentation_module_imports_without_torch_installed():
    """이 파일이 임포트된 것 자체가 증거 — 최상위에 torch 가 없다."""
    src = pathlib.Path(seg.__file__).read_text(encoding="utf-8")
    top_level = [ln for ln in src.splitlines()
                 if re.match(r"^(import|from)\s+(torch|transformers)\b", ln)]
    assert not top_level, f"torch imported at module scope: {top_level}"
    assert seg.MODEL_ID == "facebook/sam2.1-hiera-tiny"


# ── EXIF 회전 디코드 (2026-08-15 실사고: 누운 프레임 → 옷 대신 배경 조각 선택) ────

def test_decode_source_applies_exif_orientation():
    """Orientation=6 인 세로 폰 사진은 픽셀이 누워 저장된다 — 디코드가 세워서 돌려줘야
    '중앙 접촉' 게이트가 사람이 보는 그 프레임에서 동작한다."""
    import io as _io
    from PIL import Image as _Image
    from sam_service import segmentation as seg

    # 가로(400x200)로 저장 + Orientation 6(90도 CW 필요) → 적용하면 세로(200x400)
    im = _Image.new("RGB", (400, 200), (10, 20, 30))
    exif = _Image.Exif()
    exif[274] = 6
    buf = _io.BytesIO()
    im.save(buf, "JPEG", exif=exif)

    bgr = seg.decode_source_bgr(buf.getvalue())
    assert bgr is not None
    assert bgr.shape[:2] == (400, 200), "H=400, W=200 — 세워진 프레임"


def test_decode_source_returns_none_on_garbage():
    from sam_service import segmentation as seg
    assert seg.decode_source_bgr(b"not an image") is None


def test_algorithm_version_bumped_for_exif_decode():
    """캐시 키가 알고리즘 버전을 포함한다 — 누운 채 잘린 캐시가 살아남으면 안 된다."""
    from sam_service import segmentation as seg
    assert seg.ALGORITHM_VERSION == "sam2-grid8-v3"
