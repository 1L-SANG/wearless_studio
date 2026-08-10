"""`_pattern_signal` 의 **대칭 이봉 퇴화** — 공유 측정 원시함수의 2배 하모닉 오류.

무엇이 잘못됐었나
-----------------
패턴 신호는 중앙색으로부터의 ΔE76 이었다. 거리이므로 **부호가 없다**. 두 색이 같은 폭이면
중앙색이 정확히 두 색 사이에 놓여 둘 다 등거리가 되고 신호는 상수가 된다. 그러면 남는
구조는 밝은선-어두운선 **쌍**뿐이라 주기가 2배로 잡힌다.

이것이 "모르겠다" 로 끝났다면 안전했을 것이다. 그러나 `measure_axes` 는 σ=1 잡음만 있어도
24px 무늬를 **47~49px, `valid=True`** 로 보고했다 — autocorr 과 FFT 가 *함께* 틀린 값에
합의하므로 consensus 검사가 잡지 못한다. 확신에 찬 오답이 안전한 침묵보다 나쁘다.

왜 중요한가
-----------
2색 등폭은 가장 흔한 줄무늬(브르통)다. 게다가 ΔE 를 도입하게 만든 파스텔 조합조차 폭이
같으면 똑같이 무너진다. 그리고 `measure_axes` 는 결정론 QC 와 절대 스케일 가드가 함께
쓰는 **공유 눈**이다.

이것은 주기에 가하는 하모닉 보정이 아니다(그런 보정은 금지돼 있다). 신호 함수의 퇴화를
없애는 것이고, 성한 프로파일의 값은 한 비트도 건드리지 않는다.
"""

import numpy as np
import pytest

from app.services.hybrid_composite.color import bgr_to_lab
from app.services.hybrid_composite.stripe_model import (
    _pattern_signal,
    _signal_basis,
    find_period_guided,
    measure_axes,
)
from tests.test_guided_estimator_semantics import _bands, _model_from

TRUE = 24.0

#: 등폭 2색 — 전부 중앙색 대칭이라 ΔE 가 상수가 된다.
DEGENERATE = {
    "breton_black_white": [((240, 240, 240), 0.5), ((30, 30, 30), 0.5)],
    "navy_white": [((240, 240, 240), 0.5), ((90, 40, 20), 0.5)],
    # ΔE 를 도입하게 만든 바로 그 파스텔 조합 — 폭이 같으면 이것도 죽는다.
    "pastel_blue_beige": [((235, 225, 205), 0.5), ((225, 205, 180), 0.5)],
}

#: 비교군 — 원래도 잘 되던 것들. 값이 변하면 안 된다.
HEALTHY = {
    "two_40_60": [((240, 240, 240), 0.4), ((30, 30, 30), 0.6)],
    "three": [((240, 240, 240), 0.34), ((30, 40, 190), 0.33), ((60, 120, 40), 0.33)],
    "four_unequal": [((240, 240, 240), 0.4), ((30, 40, 190), 0.3),
                     ((60, 120, 40), 0.2), ((20, 20, 20), 0.1)],
}


def _img(runs, period=TRUE, noise=0.0, seed=7, width=960, height=240):
    img = _bands(width, height, runs, period)
    if noise:
        rng = np.random.default_rng(seed)
        img = np.clip(img.astype(np.int16)
                      + rng.normal(0, noise, img.shape).astype(np.int16),
                      0, 255).astype(np.uint8)
    return img


# ── 1. 퇴화가 실재한다는 것부터 못 박는다 ───────────────────────────────────
@pytest.mark.parametrize("name", list(DEGENERATE))
def test_delta_e_alone_really_does_collapse_on_symmetric_bimodal(name):
    """가드가 왜 필요한지 — ΔE **단독**은 이 프로파일들에서 상수가 된다.

    이 사실이 무너지면 아래 시험들은 아무것도 지키지 않는 셈이 된다.
    """
    prof = bgr_to_lab(_img(DEGENERATE[name])).mean(axis=0)
    med = np.median(prof, axis=0)
    delta_e = np.sqrt(((prof - med) ** 2).sum(axis=-1))
    assert delta_e.mean() > 1.0, "색 차이 자체는 분명히 있다"
    assert delta_e.std() < 0.15 * delta_e.mean(), (name, delta_e.std(), delta_e.mean())


@pytest.mark.parametrize("name", list(HEALTHY))
def test_healthy_profiles_are_not_flagged_as_degenerate(name):
    assert _signal_basis(bgr_to_lab(_img(HEALTHY[name])).mean(axis=0)) is None, name


def test_a_patternless_fabric_is_not_flagged_and_yields_no_period():
    """무늬가 없으면 퇴화가 아니라 그냥 무늬가 없는 것이다 — 없는 주기를 지어내면 안 된다."""
    flat = np.full((240, 960, 3), (128, 128, 128), np.uint8)
    prof = bgr_to_lab(flat).mean(axis=0)
    assert _signal_basis(prof) is None
    assert measure_axes(flat)["vertical"].period_px is None


# ── 2. 고쳐진 행동 — 확신에 찬 2배 오답이 사라졌다 ──────────────────────────
@pytest.mark.parametrize("name", list(DEGENERATE))
@pytest.mark.parametrize("noise", [0.0, 1.0, 3.0, 8.0])
def test_the_shared_eye_measures_the_true_period_not_twice_it(name, noise):
    """잡음이 있어도 24px 는 24px 다. 고치기 전에는 47~49px 를 `valid=True` 로 냈다."""
    got = measure_axes(_img(DEGENERATE[name], noise=noise))["vertical"].period_px
    assert got is not None, (name, noise)
    assert got == pytest.approx(TRUE, rel=0.12), (name, noise, got)


@pytest.mark.parametrize("name", list(DEGENERATE))
def test_the_guided_estimator_recovers_the_full_repeat(name):
    got = find_period_guided(_img(DEGENERATE[name], noise=2.0),
                             _model_from(DEGENERATE[name], TRUE))
    assert got is not None, name
    _axis, period, score = got
    assert period == pytest.approx(TRUE, rel=0.12), (name, period)
    assert score > 0.5, (name, score)


# ── 3. 회귀 없음 — 성한 경로는 **비트 동일**해야 한다 ───────────────────────
@pytest.mark.parametrize("name", list(HEALTHY))
def test_healthy_profiles_keep_the_exact_delta_e_values(name):
    """가드는 성한 프로파일에서 갈라지지 않는다. ΔE 를 직접 계산해 값이 같은지 본다."""
    prof = bgr_to_lab(_img(HEALTHY[name])).mean(axis=0)
    med = np.median(prof, axis=0)
    delta_e = np.sqrt(((prof - med) ** 2).sum(axis=-1))
    assert np.array_equal(_pattern_signal(prof), delta_e), name


@pytest.mark.parametrize("name", list(HEALTHY))
@pytest.mark.parametrize("noise", [0.0, 2.0])
def test_healthy_measurements_are_unchanged(name, noise):
    got = measure_axes(_img(HEALTHY[name], noise=noise))["vertical"].period_px
    assert got == pytest.approx(TRUE, rel=0.05), (name, noise, got)


# ── 4. 비교 상대끼리 표현이 같아야 한다 — 첫 수정이 놓쳤던 불변식 ────────────
def test_both_sides_of_a_comparison_share_one_representation():
    """한쪽만 부호 사영이 되면 단위가 어긋나 상관이 0 근처로 무너진다.

    실제로 처음 고쳤을 때 이 일이 났다: 접힌 ROI 는 보간 램프 때문에 비율 0.166 으로
    문턱을 넘어 ΔE 로 남고 모델만 부호 사영이 되어, **정답 주기 점수가 0.0004** 였다.
    신호를 고쳐 놓고도 답을 못 고른 것이다. 표현 선택은 비교 단위의 성질이다.
    """
    runs = DEGENERATE["breton_black_white"]
    model = _model_from(runs, TRUE)
    basis = _signal_basis(model.period_profile_lab)
    assert basis is not None, "이 모델은 퇴화 프로파일이어야 한다"

    prof = bgr_to_lab(_img(runs)).mean(axis=0)
    # 같은 basis 를 쓰면 두 신호가 같은 부호 체계를 갖는다.
    shared_model = _pattern_signal(model.period_profile_lab, basis)
    shared_roi = _pattern_signal(prof, basis)
    assert shared_model.min() < 0 < shared_model.max()
    assert shared_roi.min() < 0 < shared_roi.max()

    # 반대로 ROI 가 혼자 정하게 두면 ΔE(전부 양수) 로 남아 단위가 어긋난다.
    solo_roi = _pattern_signal(prof)
    if solo_roi.min() >= 0:
        assert not (shared_roi.min() < 0) == (solo_roi.min() < 0)


def test_the_alignment_loops_pass_a_shared_basis():
    """위상 정렬 루프도 같은 함정을 갖는다 — ref 와 row 가 갈라지면 정렬이 무작위가 된다.

    구조로 고정한다: `_pattern_signal` 을 부르는 **비교** 자리는 basis 인자를 넘겨야 한다.
    """
    import inspect

    from app.services.hybrid_composite import stripe_model
    src = inspect.getsource(stripe_model)
    lines = [ln.strip() for ln in src.splitlines()
             if "_pattern_signal(" in ln and "def " not in ln]
    paired = [ln for ln in lines if "ref_sig" in ln or "row_sig" in ln
              or "expected_sig" in ln or "folded_sig" in ln]
    assert paired, "비교 자리를 못 찾았다 — 이름이 바뀌었으면 이 시험을 고쳐야 한다"
    for line in paired:
        assert "basis" in line, line
