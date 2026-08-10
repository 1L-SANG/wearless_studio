"""계약 14 — 독립 검증이 **권한을 실제로 막아야** 검증이다.

무엇이 잘못됐었나
-----------------
`verify_absolute_repeat_scale` 은 렌더된 하모닉이 틀렸다는 것을 증명할 수 있었는데, 그
판정을 `absoluteScale.enforced=False` 로 적어만 두고 아무도 읽지 않았다. 판정을 내려
놓고 아무도 안 읽으면 검증이 없는 것과 같다 — 0.5×·2× 로 렌더된 컷이 그대로 소비·과금됐다.

왜 이제 켜도 되는가 (실물 픽셀 검증, provider 호출 0)
-----------------------------------------------------
실사진 크롭에 **참값을 아는 변환**을 걸어 재 봤다. 균일 리스케일은 반복 횟수를 정의상
보존하므로 정답이 ok=True, 절반을 잘라 늘리면 횟수가 반이 되므로 정답이 ok=False 다.
  · 정상 렌더 60건 → 오거부 0건 (0.00%)
  · 횟수 절반 손상 9건 → 9건 모두 검출
  · 740건은 스스로 기권(`computable=False`)
기권이 압도적이라 반경이 좁고, 발화 구간에서 오거부가 없었다.
"""

import inspect

import numpy as np
import pytest

from app.services.hybrid_composite.absolute_scale import verify_absolute_repeat_scale
from app.services.mannequin_cut_authority import (
    REASON_HYBRID_NOT_APPLIED,
    cut_is_consumable,
    evaluate_mannequin_cut_authority,
)
from app.workers import mannequin_job
from tests.test_absolute_stripe_scale import TRUE_PERIOD, _striped


def _cut(**hybrid):
    base = {"mode": "enforce", "applied": True}
    base.update(hybrid)
    return {"candidate": "A", "qc_scores": {"outcome": "auto_pass",
                                            "hybridComposite": base}}


# ── 권한 ────────────────────────────────────────────────────────────────────
def test_a_proven_wrong_scale_loses_product_authority():
    cut = _cut(applied=False, failureReason="absolute_scale_mismatch")
    assert cut_is_consumable(cut) is False
    assert evaluate_mannequin_cut_authority(
        cut["qc_scores"]).reason == REASON_HYBRID_NOT_APPLIED


def test_a_correct_scale_keeps_authority():
    assert cut_is_consumable(_cut()) is True


# ── 발화 조건 ───────────────────────────────────────────────────────────────
def test_the_gate_fires_only_when_it_measured_and_disagreed():
    src = inspect.getsource(mannequin_job)
    marker = src.index("absolute_scale_violated = bool(")
    expr = src[marker:marker + 320]
    assert '"computable") is True' in expr
    assert '"ok") is False' in expr


def test_abstention_never_blocks():
    """92.5% 가 기권이다 — 모르는 것을 위반으로 세면 게이트가 아니라 차단기가 된다."""
    flat = np.full((200, 640, 3), (90, 90, 90), np.uint8)
    verdict = verify_absolute_repeat_scale(source_roi_bgr=flat, output_roi_bgr=flat,
                                           axis="vertical")
    assert verdict.computable is False and verdict.ok is None
    # 기권은 위반이 아니다 — 워커의 발화식이 `ok is False` 만 본다.
    assert cut_is_consumable(_cut()) is True


def test_a_measurement_crash_is_not_a_violation():
    """가드가 터진 것과 가드가 위반을 찾은 것은 다르다."""
    src = inspect.getsource(mannequin_job)
    marker = src.index("except Exception as exc:                    # noqa: BLE001 — **재지")
    block = src[marker:marker + 400]
    assert '"computable": False' in block
    assert '"ok": None' in block


def test_it_is_actually_marked_enforced():
    src = inspect.getsource(mannequin_job)
    assert '"enforced": False' not in src, "shadow 표기가 남아 있다"
    assert '"enforced": True' in src


# ── 판정 자체가 여전히 옳은가 ───────────────────────────────────────────────
@pytest.mark.parametrize("k", [0.5, 2.0, 3.0])
def test_wrong_harmonics_are_still_rejected(k):
    source = _striped(960, 200, TRUE_PERIOD)
    wrong = _striped(640, 200, 640 / (960 / (TRUE_PERIOD * k)))
    v = verify_absolute_repeat_scale(source_roi_bgr=source, output_roi_bgr=wrong,
                                     axis="vertical")
    assert v.computable is True and v.ok is False


def test_the_correct_scale_is_still_accepted():
    source = _striped(960, 200, TRUE_PERIOD)
    right = _striped(640, 200, 640 / (960 / TRUE_PERIOD))
    v = verify_absolute_repeat_scale(source_roi_bgr=source, output_roi_bgr=right,
                                     axis="vertical")
    assert v.computable is True and v.ok is True


def test_a_uniform_rescale_preserves_the_repeat_count():
    """실물 검증이 기댄 성질 — 이것이 거짓이면 위 0% 오거부 근거가 무너진다."""
    import cv2
    src = _striped(960, 200, TRUE_PERIOD)
    smaller = cv2.resize(src, (int(960 * 0.7), int(200 * 0.7)),
                         interpolation=cv2.INTER_AREA)
    v = verify_absolute_repeat_scale(source_roi_bgr=src, output_roi_bgr=smaller,
                                     axis="vertical")
    assert v.computable is True
    assert v.ok is True, v
