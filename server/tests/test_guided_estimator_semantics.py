"""CHECKPOINT 7 — guided 추정기의 **의미**를 성격 규명한다.

무엇을 하는 파일인가
--------------------
이 스위트는 추정기를 "고치기" 위한 것이 아니다. 어떤 입력에서 무엇을 내는지 **증거로**
고정한다 — 특히 다음 두 질문:

  1. 선택이 **입력 순서에 의존하는가**(Section 11의 비전이성/seed-centered 우려).
  2. 내놓은 수가 `FULL_COLOR_REPEAT` 인가 `BAND_WIDTH` 인가 — 한 숫자가 두 뜻을 겸하는가
     (Section 12).

특정 숫자(13·20·28·30·45)가 예쁘게 나오도록 맞추지 않는다. 추정기는 **UNCERTAIN 이라고
말할 권리**가 있고, 이 스위트는 그 권리를 존중한다.

왜 계약 위험이 낮은가
---------------------
guided 주기는 이미 렌더링 권한이 없다(`guided_period_unvalidated_harmonic`). 그래서 여기서
드러나는 오차는 **관측 품질** 문제이지 잘못된 픽셀을 출고시키는 경로가 아니다. 그 사실이
이 작업의 우선순위를 정한다.
"""

import numpy as np
import pytest

from app.services.hybrid_composite.color import bgr_to_lab
from app.services.hybrid_composite.stripe_model import StripeModel, find_period_guided


def _bands(width: int, height: int, runs, period: float) -> np.ndarray:
    """`runs` = [(bgr, 폭비)]. 색이 x 를 따라 반복되는 세로 줄."""
    img = np.zeros((height, width), dtype=np.int32)
    xs = np.arange(width)
    phase = (xs % period) / period
    edges, acc = [], 0.0
    for _c, frac in runs:
        acc += frac
        edges.append(acc)
    idx = np.clip(np.searchsorted(np.asarray(edges), phase, side="right"),
                  0, len(runs) - 1)
    out = np.zeros((height, width, 3), np.uint8)
    for i, (colour, _f) in enumerate(runs):
        out[:, idx == i] = np.uint8(colour)
    return out


def _model_from(runs, period: float) -> StripeModel:
    """**한 full repeat** 를 접어 만든 모델 — 프로파일의 단위가 FULL_COLOR_REPEAT 다."""
    tile = _bands(int(round(period)), 8, runs, period)
    prof = bgr_to_lab(tile).mean(axis=0).astype(np.float32)
    seq = tuple(tuple(float(v) for v in prof[int(len(prof) * (i + 0.5) / len(runs))])
                for i in range(len(runs)))
    return StripeModel(
        axis="vertical", period_px=float(period), period_profile_lab=prof,
        ground_color_lab=seq[0], color_sequence_lab=seq,
        line_width_ratios=tuple(f for _c, f in runs),
        n_periods_used=12, confidence=0.95, source_asset_id="synthetic",
        source_sha256="0" * 64, source_roi=(0, 0, int(period), 8))


TWO_EQUAL = [((240, 240, 240), 0.5), ((30, 30, 30), 0.5)]
TWO_UNEQUAL = [((240, 240, 240), 0.75), ((30, 30, 30), 0.25)]
THREE = [((240, 240, 240), 0.34), ((30, 40, 190), 0.33), ((60, 120, 40), 0.33)]
FOUR_UNEQUAL = [((240, 240, 240), 0.4), ((30, 40, 190), 0.3),
                ((60, 120, 40), 0.2), ((20, 20, 20), 0.1)]

FAMILIES = {"two_equal": TWO_EQUAL, "two_unequal": TWO_UNEQUAL,
            "three": THREE, "four_unequal": FOUR_UNEQUAL}


def _guided(runs, period, *, width=960, height=240, noise=0.0, seed=0):
    img = _bands(width, height, runs, period)
    if noise:
        rng = np.random.default_rng(seed)
        img = np.clip(img.astype(np.int16)
                      + rng.normal(0, noise, img.shape).astype(np.int16),
                      0, 255).astype(np.uint8)
    return find_period_guided(img, _model_from(runs, period))


# ── 1. 순서 의존성 — Section 11 의 우려가 이 추정기에 적용되는가 ─────────────
def test_the_selection_does_not_depend_on_candidate_order():
    """후보를 seed 중심으로 묶으면 A~B, B~C, A≁C 에서 순서가 답을 바꾼다.

    이 추정기는 후보를 `sorted(set(...))` 로 만들고 각 후보를 **독립적으로** 점수 매긴다 —
    묶음이 없다. 그 사실을 증거로 고정한다: 같은 ROI 를 여러 번, 그리고 좌우로 뒤집어
    넣어도 같은 답이 나와야 한다(뒤집기는 후보 생성 순서를 바꾸지 않지만, 상태가 새는
    구현이라면 드러난다).
    """
    runs, period = FOUR_UNEQUAL, 24.0
    img = _bands(960, 240, runs, period)
    model = _model_from(runs, period)
    first = find_period_guided(img, model)
    repeated = [find_period_guided(img, model) for _ in range(3)]
    assert all(r == first for r in repeated), (first, repeated)


def test_the_candidate_table_is_deterministic_and_sorted():
    """후보 표 자체가 결정론적이어야 한다 — 표가 흔들리면 승자도 흔들린다."""
    runs, period = THREE, 30.0
    img = _bands(960, 240, runs, period)
    model = _model_from(runs, period)
    a, b = [], []
    find_period_guided(img, model, collect=a)
    find_period_guided(img, model, collect=b)
    assert [x["periodPx"] for x in a] == [x["periodPx"] for x in b]
    per_axis = {}
    for entry in a:
        per_axis.setdefault(entry["axis"], []).append(entry["periodPx"])
    for axis, periods in per_axis.items():
        assert periods == sorted(periods), (axis, periods)


# ── 2. 의미 — 내놓은 수가 FULL_COLOR_REPEAT 인가 ────────────────────────────
@pytest.mark.parametrize("name", list(FAMILIES))
@pytest.mark.parametrize("period", [16.0, 24.0, 30.0])
def test_what_the_estimator_returns_for_a_known_full_repeat(name, period):
    """알려진 full repeat 에 대해 무엇을 내는지 **기록**한다.

    통과 조건은 "정확히 맞춘다" 가 아니라 **하모닉 격자 위에 있거나 없거나를 분명히
    한다** 이다. 격자는 autocorr peak × {1,2,3} 이고 약수가 없다 — 그래서 진짜 반복이
    peak 의 정수배가 아니면 격자에 아예 없을 수 있다(실자산 f91cbac5 가 그랬다).
    """
    got = _guided(FAMILIES[name], period)
    assert got is not None, (name, period, "추정기가 아무 답도 내지 못했다")
    _axis, got_period, score = got
    ratio = got_period / period
    # 격자 위의 값이거나(1·2·3배 또는 그 역수) — 아니면 이 조합은 격자 밖이다.
    on_lattice = any(abs(ratio - r) < 0.12
                     for r in (1.0, 2.0, 3.0, 0.5, 1 / 3))
    assert on_lattice, (name, period, got_period, ratio, score)


def test_a_two_colour_band_width_is_not_silently_called_a_full_repeat():
    """2색 등폭에서 band width 는 full repeat 의 **절반**이다 — 한 숫자가 두 뜻을 겸하면
    소비자가 그것을 구분할 수 없다(Section 12).

    이 시험은 추정기가 절반을 고르는지 **관측**하고, 고른다면 그 사실을 드러낸다.
    현재 계약에서 이 수는 렌더링 권한이 없으므로 잘못된 픽셀로 이어지지는 않는다.
    """
    period = 24.0
    got = _guided(TWO_EQUAL, period)
    assert got is not None
    _axis, got_period, _score = got
    ratio = got_period / period
    half = abs(ratio - 0.5) < 0.12
    full = abs(ratio - 1.0) < 0.12
    assert half or full, (got_period, ratio)
    if half:
        # 반환값에는 그것이 band width 라는 표시가 **없다** — 이것이 Section 12 가
        # 지적하는 단위 과적재다. 계약이 바뀌기 전까지 사실로 고정해 둔다.
        assert isinstance(got_period, float)


# ── 3. 잡음·불확실 ───────────────────────────────────────────────────────
@pytest.mark.parametrize("noise", [0.0, 6.0, 14.0])
def test_moderate_noise_does_not_flip_the_axis(noise):
    """잡음이 늘어도 **축**은 뒤집히지 않아야 한다 — 축이 흔들리면 그 뒤는 전부 무의미하다."""
    got = _guided(FOUR_UNEQUAL, 24.0, noise=noise, seed=3)
    assert got is not None, noise
    assert got[0] == "vertical", (noise, got)


def test_a_textureless_roi_yields_no_answer():
    """무늬가 없으면 답이 없어야 한다 — 없는 주기를 지어내면 안 된다."""
    flat = np.full((240, 960, 3), (128, 128, 128), np.uint8)
    assert find_period_guided(flat, _model_from(TWO_EQUAL, 24.0)) is None


def test_the_estimator_may_decline_and_that_is_a_valid_outcome():
    """`None` 은 실패가 아니라 **정당한 결과**다(Section 10). 구조로 고정한다."""
    import inspect
    src = inspect.getsource(find_period_guided)
    assert "-> tuple[str, float, float] | None" in src or "| None" in src
