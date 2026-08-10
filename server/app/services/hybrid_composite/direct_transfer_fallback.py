"""텍스처 진실이 불확정일 때의 **결정론적 폴백**. provider 호출 0, DB·R2 접근 0.

왜 이것이 필요한가
-------------------
주기를 신뢰할 수 없으면 투영은 건너뛰는 것이 옳다. 하지만 그 자리에서 멈추면 남는 것은
**검증되지 않은 carrier 후보**뿐이고, 그것이 지금까지 `done` 으로 나가 과금까지 됐다.
사용자 검수는 복구 수단이 아니다 — 복구는 파이프라인이 해야 한다.

이 경로는 주기를 **쓰지 않는다**. 원본 픽셀을 호모그래피로 옮길 뿐이라
`FULL_COLOR_REPEAT` 가 무엇이든 상관이 없다. 그래서 "주기를 모른다"가 곧 "아무것도 못
한다"가 아니게 된다.

무엇을 보장하고 무엇을 보장하지 않는가
--------------------------------------
승격을 통과한 픽셀만 돌려준다(`direct_transfer_gate`). 통과하지 못하면 **픽셀을 주지
않고** 사유만 남긴다 — 보존과 권한은 다른 것이다. 이 모듈은 판정을 새로 만들지 않고
이미 검증된 게이트에 배선만 한다.

원본 garment mask 는 여기서 **원본 landmarks 로 직접 만든다**. 승격 규칙이 원본 근거를
요구하기 때문이다(그것이 이 모드가 파는 유일한 약속이다). 마스크가 없으면 승격은
`painted_without_source_backing` 로 거절되고, 그것이 옳다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .direct_transfer_gate import run_gated_direct_transfer
from .panel_map import build_panel_map
from .types import CompositeFailure

FALLBACK_VERSION = "direct_transfer_fallback_v1"

REASON_CARRIER_PANEL_MAP = "carrier_panel_map_unavailable"
REASON_SOURCE_PANEL_MAP = "source_panel_map_unavailable"
REASON_TRANSFER_RAISED = "direct_transfer_raised"


@dataclass(frozen=True)
class DirectFallback:
    applied: bool
    image_bgr: np.ndarray | None = None
    reasons: tuple = field(default_factory=tuple)
    detail: dict = field(default_factory=dict)
    version: str = FALLBACK_VERSION



def attempt_direct_fallback(
    *,
    carrier_bgr: np.ndarray,
    carrier_landmarks: dict,
    source_bgr: np.ndarray,
    source_landmarks: dict,
    source_inventory: dict | None = None,
    carrier_inventory: dict | None = None,
    carrier_component_boxes: dict | None = None,
    source_component_boxes: dict | None = None,
    source_sha256: str | None = None,
    carrier_sha256: str | None = None,
) -> DirectFallback:
    """불확정 경로에서 **결정론 전송을 시도**한다. 실패는 전부 typed 결과다.

    잡을 실패시키지 않는다 — 폴백이 안 되는 것은 후보 실패이지 잡 실패가 아니다.
    """
    # panel map 구축도 **던질 수 있다**. 이 경로의 계약은 "후보 실패이지 잡 실패가
    # 아니다" 이므로, 예외가 워커로 올라가면 계약이 깨진다.
    try:
        car_pm = build_panel_map(carrier_bgr, carrier_landmarks,
                                 source_inventory=source_inventory,
                                 carrier_inventory=carrier_inventory)
    except Exception as exc:                        # noqa: BLE001
        return DirectFallback(False, reasons=(REASON_CARRIER_PANEL_MAP,),
                              detail={"raised": f"{type(exc).__name__}: {exc}"[:200]})
    if isinstance(car_pm, CompositeFailure) or car_pm is None:
        return DirectFallback(False, reasons=(REASON_CARRIER_PANEL_MAP,),
                              detail={"reason": getattr(car_pm, "reason", None),
                                      "detail": str(getattr(car_pm, "detail", ""))[:200]})

    # 원본 쪽 실루엣 — 승격이 요구하는 **원본 근거**의 출처다. carrier 와 같은 기계로
    # 만든다(새 추정기를 만들지 않는다).
    try:
        src_pm = build_panel_map(source_bgr, source_landmarks)
    except Exception as exc:                        # noqa: BLE001
        return DirectFallback(False, reasons=(REASON_SOURCE_PANEL_MAP,),
                              detail={"raised": f"{type(exc).__name__}: {exc}"[:200]})
    if isinstance(src_pm, CompositeFailure) or src_pm is None:
        return DirectFallback(False, reasons=(REASON_SOURCE_PANEL_MAP,),
                              detail={"reason": getattr(src_pm, "reason", None),
                                      "detail": str(getattr(src_pm, "detail", ""))[:200]})

    try:
        gated = run_gated_direct_transfer(
            carrier_bgr, car_pm, source_bgr,
            source_landmarks=source_landmarks,
            source_garment_mask=src_pm.garment_mask,
            carrier_component_boxes=carrier_component_boxes,
            source_component_boxes=source_component_boxes,
            source_sha256=source_sha256,
            carrier_sha256=carrier_sha256)
    except Exception as exc:                        # noqa: BLE001
        return DirectFallback(False, reasons=(REASON_TRANSFER_RAISED,),
                              detail={"raised": f"{type(exc).__name__}: {exc}"[:200]})

    return DirectFallback(
        applied=gated.promoted,
        image_bgr=gated.image_bgr,          # 승격됐을 때만 채워져 있다
        reasons=tuple(gated.reasons),
        detail={"metrics": gated.metrics,
                "carrierPanelConfidence": round(float(car_pm.confidence), 3),
                "sourcePanelConfidence": round(float(src_pm.confidence), 3)})
