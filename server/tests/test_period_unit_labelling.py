"""§12 — 주기 숫자는 **무엇을 재는 수인지** 함께 말한다.

왜
--
`BAND_WIDTH`(선 하나의 폭)와 `FULL_COLOR_REPEAT`(색이 한 바퀴 도는 거리)는 대칭 무늬에서
정확히 2배로 알리아싱된다. 한 숫자가 두 뜻을 겸하면 소비자는 어느 쪽인지 알 방법이 없고,
그 모호성이 그대로 렌더 스케일 오류가 된다.

그래서 어휘는 셋이다 — 그리고 **`UNKNOWN` 이 진짜 값**이다. blind autocorrelation 은
자기유사성이 최대인 lag 을 잡을 뿐이라 어느 쪽을 잡았는지 스스로 알지 못한다. 모르는 것을
`FULL_COLOR_REPEAT` 라고 적는 것이 이 계약이 막으려는 바로 그 거짓말이다.
"""

import numpy as np
import pytest

from app.services.hybrid_composite.absolute_scale import verify_absolute_repeat_scale
from app.services.hybrid_composite.stripe_model import (
    PERIOD_UNIT_BAND_WIDTH,
    PERIOD_UNIT_FULL_COLOR_REPEAT,
    PERIOD_UNIT_UNKNOWN,
    find_period_guided,
)
from tests.test_guided_estimator_semantics import _bands, _model_from

VOCABULARY = {PERIOD_UNIT_FULL_COLOR_REPEAT, PERIOD_UNIT_BAND_WIDTH, PERIOD_UNIT_UNKNOWN}
RUNS = [((240, 240, 240), 0.4), ((30, 40, 190), 0.3),
        ((60, 120, 40), 0.2), ((20, 20, 20), 0.1)]


def test_the_three_labels_are_distinct():
    assert len(VOCABULARY) == 3


# ── 관측 데이터에 단위가 빠지지 않는다 ──────────────────────────────────────
def test_every_emitted_candidate_carries_a_unit():
    """스팟 체크가 아니라 **전수**로 본다 — 하나라도 빠지면 그 행은 뜻이 없는 숫자다."""
    collected: list = []
    find_period_guided(_bands(960, 240, RUNS, 24.0), _model_from(RUNS, 24.0),
                       collect=collected)
    assert collected, "후보 표가 비면 이 시험은 아무것도 지키지 않는다"
    for entry in collected:
        assert "periodPx" in entry
        assert entry.get("periodUnit") in VOCABULARY, entry


def test_the_guided_number_is_labelled_a_full_repeat():
    """guided 는 모델의 **한 full repeat** 프로파일과 맞춰 본 결과다."""
    collected: list = []
    find_period_guided(_bands(960, 240, RUNS, 24.0), _model_from(RUNS, 24.0),
                       collect=collected)
    assert {e["periodUnit"] for e in collected} == {PERIOD_UNIT_FULL_COLOR_REPEAT}


def test_the_blind_repeat_count_admits_it_does_not_know_the_unit():
    """`absolute_scale` 은 blind 로 센다 — 단위를 안다고 주장하면 안 된다."""
    src = _bands(960, 200, RUNS, 24.0)
    out = _bands(640, 200, RUNS, 640 / (960 / 24.0))
    verdict = verify_absolute_repeat_scale(source_roi_bgr=src, output_roi_bgr=out,
                                           axis="vertical")
    assert verdict.period_unit == PERIOD_UNIT_UNKNOWN


def test_an_unknown_unit_still_permits_a_ratio_verdict():
    """단위를 모르는 것과 비교가 무의미한 것은 다르다 — 비율에서는 단위가 약분된다.

    같은 눈으로 양쪽을 세므로, 단위가 무엇이든 **틀린 하모닉은 여전히 잡힌다**.
    """
    src = _bands(960, 200, RUNS, 24.0)
    wrong = _bands(640, 200, RUNS, 640 / (960 / (24.0 * 2)))     # 2× 하모닉 오류
    verdict = verify_absolute_repeat_scale(source_roi_bgr=src, output_roi_bgr=wrong,
                                           axis="vertical")
    assert verdict.period_unit == PERIOD_UNIT_UNKNOWN
    assert verdict.computable is True
    assert verdict.ok is False, verdict


# ── 실패 증거에도 단위가 실린다 ─────────────────────────────────────────────
def test_the_uncertainty_evidence_labels_its_period():
    """`guided_period_unvalidated_harmonic` 증거에 단위가 함께 나가야 한다."""
    import inspect

    from app.workers import mannequin_job
    src = inspect.getsource(mannequin_job)
    assert "guidedObservedPeriodUnit=" in src
    marker = src.index("guidedObservedPeriodUnit=")
    assert "PERIOD_UNIT_FULL_COLOR_REPEAT" in src[marker:marker + 200]


def test_no_bare_period_field_ships_without_a_neighbouring_unit():
    """구조 시험 — 관측 표에 새 주기 필드를 넣으면서 단위를 빼먹는 일을 막는다."""
    import inspect

    from app.services.hybrid_composite import stripe_model
    src = inspect.getsource(stripe_model.find_period_guided)
    assert '"periodPx"' in src
    assert '"periodUnit"' in src
    # 단위가 주기 **바로 옆**에 있어야 한다 — 멀리 떨어지면 다음 사람이 못 본다.
    assert abs(src.index('"periodUnit"') - src.index('"periodPx"')) < 400


def test_the_vocabulary_is_not_silently_widened():
    """어휘가 늘면 소비자의 분기가 조용히 새 값을 만난다 — 늘릴 땐 이 시험을 고쳐야 한다."""
    from app.services.hybrid_composite import stripe_model
    exported = {name for name in dir(stripe_model) if name.startswith("PERIOD_UNIT_")}
    assert exported == {"PERIOD_UNIT_FULL_COLOR_REPEAT", "PERIOD_UNIT_BAND_WIDTH",
                        "PERIOD_UNIT_UNKNOWN"}, exported


def test_a_declined_measurement_has_no_period_to_label():
    """잴 수 없으면 단위를 붙일 숫자 자체가 없다 — 빈 값에 라벨을 다는 시늉을 하지 않는다."""
    flat = np.full((200, 640, 3), (90, 90, 90), np.uint8)
    verdict = verify_absolute_repeat_scale(source_roi_bgr=flat, output_roi_bgr=flat,
                                           axis="vertical")
    assert verdict.computable is False
    assert verdict.source_repeats is None and verdict.output_repeats is None
    assert verdict.period_unit == PERIOD_UNIT_UNKNOWN
