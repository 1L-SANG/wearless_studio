"""전송 → 측정 → 승격을 **한 번에** 묶는 이음매. DB·provider·R2 접근 0.

왜 별도 모듈인가
-----------------
Phase A–E 가 만든 세 조각(`transfer_torso_texture`, `evaluate_direct_transfer`,
`evaluate_direct_transfer_promotion`)은 각각 순수하지만, 그것을 **어떤 순서로 어떤 입력을
공유해서** 부르느냐가 계약의 절반이다. 특히 QC 는 렌더러가 받은 것과 **같은 호출자 입력**
을 받아야 한다 — 다른 값을 넘기면 오라클이 다른 그림을 채점한다. 호출부마다 그 배선을
다시 쓰면 언젠가 어긋난다. 그래서 배선을 여기 한 곳에 고정한다.

이 함수가 지키는 것
-------------------
  · 렌더러와 QC 에 **같은** landmarks·mask·박스·shading 을 넘긴다(인자 하나에서 갈라진다).
  · 렌더가 불가능하면 그것은 **후보 실패**지 잡 실패가 아니다 — typed 결과로 돌려준다.
  · 승격되지 않은 후보도 **버리지 않는다**. 보존과 권한은 다른 것이고, 보존해야 왜
    막혔는지 사후에 볼 수 있다.
  · **승격되지 않은 후보를 제품으로 내보내지 않는다.** `image_bgr` 는 승격됐을 때만
    채워진다 — 호출부가 실수로 미승격 픽셀을 쓰는 길을 타입에서 막는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .direct_torso_transfer import (SHADING_SOURCE_HIGHFREQ_CARRIER_LOWFREQ,
                                    DirectTorsoCandidate, transfer_torso_texture)
from .direct_transfer_promotion import (REASON_UNMEASURED,
                                        evaluate_direct_transfer_promotion)
from .direct_transfer_qc import evaluate_direct_transfer

GATE_VERSION = "direct_transfer_gate_v1"

#: 렌더 자체가 성립하지 않은 경우 — 승격 사유 어휘와 섞지 않는다.
REASON_TRANSFER_UNAVAILABLE = "transfer_unavailable"


@dataclass(frozen=True)
class GatedDirectTransfer:
    """승격됐을 때만 픽셀을 준다. 나머지는 전부 **관측 기록**이다."""

    promoted: bool
    #: 승격된 경우에만 채워진다. 미승격 후보의 픽셀은 제품 경로로 나가지 않는다.
    image_bgr: np.ndarray | None = None
    reasons: tuple = field(default_factory=tuple)
    qc_checks: dict = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)
    #: 미승격이어도 후보는 남긴다 — 보존 ≠ 권한. 왜 막혔는지 보려면 이것이 있어야 한다.
    candidate: DirectTorsoCandidate | None = None
    version: str = GATE_VERSION


def run_gated_direct_transfer(
    carrier_bgr: np.ndarray,
    panel_map,
    source_bgr: np.ndarray,
    *,
    source_landmarks,
    source_garment_mask: np.ndarray | None = None,
    carrier_component_boxes: dict | None = None,
    source_component_boxes: dict | None = None,
    shading: str = SHADING_SOURCE_HIGHFREQ_CARRIER_LOWFREQ,
    source_sha256: str | None = None,
    carrier_sha256: str | None = None,
) -> GatedDirectTransfer:
    """결정론 전송을 시도하고, **측정을 통과한 경우에만** 픽셀을 돌려준다.

    같은 인자가 렌더러와 QC 양쪽으로 간다 — 그래야 오라클이 실제로 그려진 그림을 채점한다.
    """
    # 렌더러가 **던지는** 것도 후보 실패다. 이음매에서 새면 잡이 죽는다(실측:
    # carrier 와 garment mask 의 모양이 어긋나면 브로드캐스트 ValueError 가 올라온다).
    try:
        candidate = transfer_torso_texture(
            carrier_bgr, panel_map, source_bgr,
            source_landmarks=source_landmarks,
            source_garment_mask=source_garment_mask,
            carrier_component_boxes=carrier_component_boxes,
            source_component_boxes=source_component_boxes,
            shading=shading,
            source_sha256=source_sha256,
            carrier_sha256=carrier_sha256)
    except Exception as exc:                    # noqa: BLE001 - 후보 실패로 봉인한다
        return GatedDirectTransfer(
            promoted=False,
            reasons=(REASON_TRANSFER_UNAVAILABLE,),
            metrics={"reason": "transfer_raised",
                     "detail": f"{type(exc).__name__}: {exc}"[:200]})

    if not isinstance(candidate, DirectTorsoCandidate):
        # 렌더 불가는 **후보 실패**다. 사유는 그대로 실어 보내되 잡을 실패시키지 않는다.
        detail = {}
        for key in ("reason", "detail", "metrics"):
            value = getattr(candidate, key, None)
            if value is not None:
                detail[key] = value
        return GatedDirectTransfer(
            promoted=False,
            reasons=(REASON_TRANSFER_UNAVAILABLE,),
            metrics=detail)

    # **잰 픽셀과 내보내는 픽셀이 같아야 한다.** 후보 배열은 가변이므로, 측정 뒤에
    # 누가 손대면 승격 도장은 다른 그림에 찍힌다(실측: QC 는 281,600 px 변경을 쟀는데
    # 반환된 그림은 0 px 변경이었고 63개 시험이 전부 통과했다).
    assessed_bgr = np.array(candidate.image_bgr, copy=True)
    try:
        report = evaluate_direct_transfer(
            candidate,
            carrier_bgr=carrier_bgr,
            source_bgr=source_bgr,
            panel_map=panel_map,
            source_landmarks=source_landmarks,
            shading=shading,
            source_sha256=source_sha256,
            carrier_sha256=carrier_sha256,
            source_garment_mask=source_garment_mask,
            carrier_component_boxes=carrier_component_boxes,
            source_component_boxes=source_component_boxes)
    except Exception as exc:                    # noqa: BLE001
        # 측정이 사라지면 승격도 없다 — 예외를 통과로 읽지 않는다.
        return GatedDirectTransfer(
            promoted=False,
            reasons=(REASON_UNMEASURED,),
            metrics={"reason": "qc_raised",
                     "detail": f"{type(exc).__name__}: {exc}"[:200]},
            candidate=candidate)
    if not np.array_equal(assessed_bgr, np.asarray(candidate.image_bgr)):
        # 측정 도중에 후보가 바뀌었다 — 무엇을 채점했는지 말할 수 없다.
        return GatedDirectTransfer(
            promoted=False,
            reasons=(REASON_UNMEASURED,),
            qc_checks=report.checks,
            metrics={"reason": "candidate_mutated_during_assessment"},
            candidate=candidate)

    try:
        verdict = evaluate_direct_transfer_promotion(report)
    except Exception as exc:                    # noqa: BLE001
        # 판정이 사라지면 승격도 없다 — 예외를 통과로 읽지 않는다.
        return GatedDirectTransfer(
            promoted=False,
            reasons=(REASON_UNMEASURED,),
            qc_checks=report.checks,
            metrics={"reason": "promotion_raised",
                     "detail": f"{type(exc).__name__}: {exc}"[:200]},
            candidate=candidate)

    return GatedDirectTransfer(
        promoted=verdict.promoted,
        # 승격됐을 때만 픽셀이 나간다.
        # 잰 그 픽셀을 돌려준다 — 이후 후보가 바뀌어도 제품 경로는 흔들리지 않는다.
        image_bgr=assessed_bgr if verdict.promoted else None,
        reasons=verdict.reasons or ((REASON_UNMEASURED,) if not verdict.promoted else ()),
        qc_checks=report.checks,
        metrics=verdict.metrics,
        candidate=candidate)
