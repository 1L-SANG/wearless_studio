"""scale_anchor 계약 — 반복 수 불변량과 몸통 종횡비 측정.

이 모듈은 워커와 offline replay 가 **공유하는** 이음매다. 그런데 추출 시점에 테스트가
한 건도 없었고, 그래서 전체 스위트가 green 이어도 여기 회귀는 아무것도 잡히지 않았다.
특히 `aspect_via_stripe_energy` 의 해부학적 클립은 실 4K 표본에서 게이트 판정을
거절→통과로 뒤집은 변경인데, 그 클립을 지워도 CI 는 통과했다. 그 구멍을 메운다.
"""

import numpy as np
import pytest

from app.services.hybrid_composite import scale_anchor as sa


def _landmarks(*, shoulder_y=0.20, hem_y=0.50, x0=0.30, x1=0.70,
               sleeve_end_y=0.55):
    return {
        "shoulder_l": [x0, shoulder_y],
        "shoulder_r": [x1, shoulder_y],
        "hem_l": [x0, hem_y],
        "hem_r": [x1, hem_y],
        "sleeve_l_end": [x0 - 0.12, sleeve_end_y],
        "sleeve_r_end": [x1 + 0.12, sleeve_end_y],
    }


# ── 반복 수 불변량 ────────────────────────────────────────────────────────────

def test_target_period_preserves_repeat_count():
    """같은 옷을 2배로 크게 찍으면 주기도 2배여야 한다 — 줄 개수는 그대로."""
    period = sa.target_period_px(
        source_period_px=10.0, source_span_px=100.0, target_span_px=200.0)
    assert period == pytest.approx(20.0)


def test_target_period_is_scale_free():
    """span 과 period 를 같은 배율로 키우면 결과가 변하지 않는다."""
    a = sa.target_period_px(source_period_px=8.0, source_span_px=96.0,
                            target_span_px=480.0)
    b = sa.target_period_px(source_period_px=80.0, source_span_px=960.0,
                            target_span_px=480.0)
    assert a == pytest.approx(b)


def test_target_period_survives_zero_source_period():
    """0 주기는 호출자가 이미 실패 처리하지만, 여기서 죽으면 원인이 가려진다."""
    assert sa.target_period_px(source_period_px=0.0, source_span_px=100.0,
                               target_span_px=100.0) >= 0.0


def test_span_axis_selects_the_direction_stripes_advance():
    roi = (100, 200, 400, 800)          # w=300, h=600
    assert sa.torso_span(roi, garment_axis="vertical") == pytest.approx(300.0)
    assert sa.torso_span(roi, garment_axis="horizontal") == pytest.approx(600.0)


def test_source_roi_spans_shoulder_to_hem():
    roi = sa.source_torso_roi(_landmarks(), width=1000, height=2000)
    x0, y0, x1, y1 = roi
    assert (x0, x1) == (300, 700)
    assert (y0, y1) == (400, 1000)


def test_carrier_span_matches_roi_span_for_the_same_geometry():
    """carrier 는 landmark 에서, source 는 ROI 에서 재는데 값이 갈리면 환산이 틀어진다."""
    lm = _landmarks()
    roi = sa.source_torso_roi(lm, width=1000, height=2000)
    for axis in ("vertical", "horizontal"):
        assert sa.torso_span(roi, garment_axis=axis) == pytest.approx(
            sa.carrier_torso_span(lm, width=1000, height=2000, garment_axis=axis),
            abs=1.0)


# ── 몸통 종횡비: 해부학적 클립 ────────────────────────────────────────────────

def _striped_torso_with_legs(*, legs: bool) -> np.ndarray:
    """셔츠(어깨 0.20~밑단 0.50) + 선택적으로 아래로 이어지는 좁은 다리 기둥.

    실 4K carrier 에서 관찰된 형태를 최소로 재현한다 — 줄무늬 에너지 성분이 셔츠에서
    출발해 맨다리를 타고 발까지 한 덩어리로 번져, 높이는 전신이고 중간대 폭은 다리가 됐다.
    """
    h, w = 1000, 500
    img = np.full((h, w, 3), 240, np.uint8)
    torso = slice(int(0.20 * h), int(0.50 * h))
    for x in range(150, 350):
        if (x // 6) % 2 == 0:
            img[torso, x] = (60, 90, 200)
    if legs:
        for x in range(220, 280):
            if (x // 6) % 2 == 0:
                img[int(0.50 * h):int(0.95 * h), x] = (60, 90, 200)
    return img


def test_aspect_ignores_pixels_outside_the_garment_band():
    """다리가 붙어 있어도 종횡비가 셔츠의 것이어야 한다.

    클립 이전에는 실측 4.896 — 셔츠로는 불가능한 값이었고, 그 값 하나가
    `geometry_carrier_mismatch` 로 정상 carrier 를 거절했다.
    """
    lm = _landmarks(x0=0.30, x1=0.70, shoulder_y=0.20, hem_y=0.50)
    clean = sa.aspect_via_stripe_energy(_striped_torso_with_legs(legs=False), lm)
    leggy = sa.aspect_via_stripe_energy(_striped_torso_with_legs(legs=True), lm)
    assert clean is not None and leggy is not None
    # 다리 유무가 판정을 바꾸면 안 된다.
    assert leggy == pytest.approx(clean, rel=0.15)
    # 그리고 셔츠다운 값이어야 한다 — 몸통이 폭의 5배로 길 수는 없다.
    assert leggy < 3.0


def test_aspect_still_reports_a_collapsed_mask():
    """클립이 게이트를 눈멀게 하면 안 된다 — 좁고 긴 몸통은 여전히 큰 값이 나와야 한다."""
    h, w = 1000, 500
    img = np.full((h, w, 3), 240, np.uint8)
    for x in range(243, 257):                      # 몸통 폭 14px, 높이 300px
        if (x // 3) % 2 == 0:
            img[int(0.20 * h):int(0.50 * h), x] = (60, 90, 200)
    lm = _landmarks(x0=0.486, x1=0.514)
    aspect = sa.aspect_via_stripe_energy(img, lm)
    assert aspect is None or aspect > 5.0


def test_aspect_returns_none_for_a_degenerate_band():
    """어깨와 밑단이 겹치면 잴 것이 없다 — 값을 지어내지 않는다."""
    img = _striped_torso_with_legs(legs=False)
    lm = _landmarks(shoulder_y=0.40, hem_y=0.40)
    assert sa.aspect_via_stripe_energy(img, lm) is None
