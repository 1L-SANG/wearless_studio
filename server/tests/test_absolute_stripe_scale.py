"""CHECKPOINT 6 — **절대 반복 스케일**을 독립적으로 검증한다.

왜 이 파일이 필요한가
---------------------
스칼라 하나(`target_period_px`)가 잘못된 하모닉이면 결과는 **절대 스케일만 배수로 틀린**
그림이 된다. 그런데 색 순서·선 폭 비·팔레트는 전부 보존되므로, 그 축들을 보는 검사는
전부 통과한다. 그리고 기존 결정론 QC 는 기대 주기를 **렌더를 만든 그 스칼라에서** 가져온다
(`verify_composite(..., target_period_px=target_period_px)`) — 즉 "렌더러가 준 스칼라를
그대로 썼는가" 를 확인할 뿐 "그 스칼라가 옳은 하모닉인가" 는 묻지 않는다.

이 스위트는 두 가지를 한다:
  1. 그 자기일관 통과를 **재현**해 증거로 고정한다(가드가 왜 필요한지).
  2. 새 독립 가드가 0.5×·2×·3× 를 거부하고 1× 는 통과시키는지 본다.

독립성의 근거
-------------
가드는 **픽셀에서 반복 횟수를 두 번 센다** — 원본 토르소에서 한 번, 출력 토르소에서 한 번.
어느 쪽도 후보 스칼라를 쓰지 않는다. 스칼라가 k 배 틀리면 출력의 반복 횟수만 1/k 배가
되므로 두 수가 갈라진다. 같은 상류 스칼라를 공유하는 비교는 독립 검증이 아니다.
"""

import numpy as np
import pytest

from app.services.hybrid_composite.stripe_model import measure_axes

TRUE_PERIOD = 24.0          # 진짜 FULL_COLOR_REPEAT
HARMONICS = [0.5, 1.0, 2.0, 3.0]


def _striped(width: int, height: int, period: float) -> np.ndarray:
    """가로 방향 4색 반복. **색 순서·폭 비·팔레트는 주기와 무관하게 고정**이다.

    그래서 하모닉이 틀려도 그 축들을 보는 검사는 전부 통과한다 — 이 픽스처의 요점이다.
    """
    runs = [((30, 40, 190), 0.25), ((240, 240, 240), 0.25),
            ((60, 120, 40), 0.25), ((20, 20, 20), 0.25)]
    img = np.zeros((height, width, 3), np.uint8)
    xs = np.arange(width)
    phase = (xs % period) / period
    edges, acc = [], 0.0
    for _c, frac in runs:
        acc += frac
        edges.append(acc)
    idx = np.searchsorted(np.asarray(edges), phase, side="right")
    idx = np.clip(idx, 0, len(runs) - 1)
    for i, (colour, _frac) in enumerate(runs):
        img[:, idx == i] = np.uint8(colour)
    return img


def _measured_repeats(img: np.ndarray) -> float:
    """이미지 폭에 몇 번의 반복이 들어 있는가 — **스칼라를 쓰지 않고** 픽셀에서만."""
    measured = measure_axes(img)
    period = measured["vertical"].period_px
    assert period and period > 0, measured
    return img.shape[1] / float(period)


# ── 1. 픽스처가 실제로 하모닉 계열인지 먼저 못 박는다 ───────────────────────
@pytest.mark.parametrize("k", HARMONICS)
def test_the_harmonic_family_really_differs_only_in_absolute_scale(k):
    """폭 비·색 순서는 같고 **절대 주기만** k 배여야 한다. 아니면 이 스위트가 무의미하다."""
    base = _striped(960, 200, TRUE_PERIOD)
    other = _striped(960, 200, TRUE_PERIOD * k)
    assert _measured_repeats(base) == pytest.approx(960 / TRUE_PERIOD, rel=0.08)
    assert _measured_repeats(other) == pytest.approx(960 / (TRUE_PERIOD * k), rel=0.10)
    # 팔레트가 같다는 것도 확인한다 — 색으로는 구분할 수 없어야 한다.
    assert {tuple(c) for c in np.unique(base.reshape(-1, 3), axis=0)} == \
           {tuple(c) for c in np.unique(other.reshape(-1, 3), axis=0)}


# ── 2. 기존 계약의 자기일관성을 **증거로** 고정한다 ─────────────────────────
@pytest.mark.parametrize("k", [0.5, 2.0, 3.0])
def test_a_shared_scalar_comparison_cannot_reject_a_wrong_harmonic(k):
    """같은 스칼라를 렌더러와 검증기에 함께 주면, 하모닉이 틀려도 상대오차는 0 이다.

    이것이 "상류를 공유하는 비교는 독립 검증이 아니다" 의 수치적 의미다.
    """
    span_src, span_tgt = 960.0, 640.0
    # 추정기가 k 배 틀린 주기를 냈다고 하자.
    wrong_source_period = TRUE_PERIOD * k
    repeats_from_scalar = span_src / wrong_source_period
    target_period = span_tgt / repeats_from_scalar        # 렌더러가 쓰는 스칼라

    rendered = _striped(int(span_tgt), 200, target_period)
    measured = float(measure_axes(rendered)["vertical"].period_px)

    # 검증기가 같은 스칼라로 기대를 세우면 — 오차가 없다.
    rel_err_shared = abs(span_tgt / measured - span_tgt / target_period) / (span_tgt / target_period)
    assert rel_err_shared < 0.12, rel_err_shared      # 통과해 버린다

    # 그러나 **원본을 직접 재면** 반복 횟수가 k 배 어긋난다.
    source = _striped(int(span_src), 200, TRUE_PERIOD)
    true_repeats = _measured_repeats(source)
    rendered_repeats = span_tgt / measured
    assert rendered_repeats == pytest.approx(true_repeats / k, rel=0.15), (
        rendered_repeats, true_repeats, k)


# ── 3. 독립 가드 — 반복 횟수를 양쪽에서 **각각** 센다 ───────────────────────
def test_the_independent_guard_accepts_the_correct_scale():
    from app.services.hybrid_composite.absolute_scale import verify_absolute_repeat_scale
    source = _striped(960, 200, TRUE_PERIOD)
    repeats = 960 / TRUE_PERIOD
    rendered = _striped(640, 200, 640 / repeats)
    verdict = verify_absolute_repeat_scale(
        source_roi_bgr=source, output_roi_bgr=rendered, axis="vertical")
    assert verdict.computable is True, verdict
    assert verdict.ok is True, verdict
    assert verdict.harmonic_ratio == pytest.approx(1.0, abs=0.15), verdict


@pytest.mark.parametrize("k", [0.5, 2.0, 3.0])
def test_the_independent_guard_rejects_wrong_harmonics(k):
    """0.5×·2×·3× 는 거부돼야 한다 — 색·폭 비·팔레트가 전부 같아도."""
    from app.services.hybrid_composite.absolute_scale import verify_absolute_repeat_scale
    source = _striped(960, 200, TRUE_PERIOD)
    repeats_wrong = 960 / (TRUE_PERIOD * k)
    rendered = _striped(640, 200, 640 / repeats_wrong)
    verdict = verify_absolute_repeat_scale(
        source_roi_bgr=source, output_roi_bgr=rendered, axis="vertical")
    assert verdict.computable is True, verdict
    assert verdict.ok is False, (k, verdict)
    assert verdict.harmonic_ratio == pytest.approx(1.0 / k, rel=0.2), verdict


def test_the_guard_says_uncomputable_rather_than_guessing():
    """줄이 없는 원단에서는 반복 횟수가 정의되지 않는다 — 통과라고 우기지 않는다."""
    from app.services.hybrid_composite.absolute_scale import verify_absolute_repeat_scale
    flat = np.full((200, 640, 3), (90, 90, 90), np.uint8)
    verdict = verify_absolute_repeat_scale(
        source_roi_bgr=flat, output_roi_bgr=flat, axis="vertical")
    assert verdict.computable is False
    assert verdict.ok is None
    assert verdict.reason


def test_the_guard_does_not_read_any_candidate_scalar():
    """구조로 고정한다 — 가드가 후보 스칼라를 받으면 독립성이 사라진다."""
    import inspect

    from app.services.hybrid_composite import absolute_scale
    params = set(inspect.signature(
        absolute_scale.verify_absolute_repeat_scale).parameters)
    for forbidden in ("target_period_px", "source_period_px", "period_px",
                      "model", "stripe_model", "expected_period"):
        assert forbidden not in params, forbidden
    # 산문이 아니라 **코드**를 본다 — 주석에 이름이 나온다고 그 값을 쓰는 것은 아니다.
    code = inspect.getsource(absolute_scale)
    code_only = "".join(code.split('"""')[::2])       # docstring 제거
    code_only = "\n".join(l for l in code_only.splitlines()
                          if not l.lstrip().startswith("#"))
    for forbidden in ("target_period_px", "source_period_px", "model.period_px"):
        assert forbidden not in code_only, forbidden
