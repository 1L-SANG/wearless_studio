"""torso 기준 스케일 앵커 — source 줄 주기를 carrier 픽셀 주기로 옮기는 순수 계산.

이 계산이 워커 안에만 있으면 offline replay 가 같은 값을 얻을 방법이 없고, 결국
공식을 복사하게 된다. 복사본은 원본과 조용히 갈라지므로 replay 통과가 프로덕션을
보증하지 못한다 — 그래서 이음매를 여기로 뺀다. 순수 함수이고 IO·모델 호출이 없다.

핵심 불변량: **몸통을 가로지르는 줄의 개수는 사진이 바뀌어도 같다.** 원본에서 몸통
span 을 주기로 나눠 반복 수를 얻고, carrier 의 몸통 span 을 그 반복 수로 나누면
carrier 에서의 주기가 나온다. 절대 픽셀 크기가 아니라 반복 수를 보존하는 이유는,
같은 옷을 다른 해상도·다른 프레이밍으로 찍어도 줄 개수는 불변이기 때문이다.
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np

from . import panel_map as _panel_map


def source_torso_roi(landmarks: Mapping, *, width: int, height: int) -> tuple[int, int, int, int]:
    """원본 정면에서 몸통 ROI (x0, y0, x1, y1) — 어깨선~밑단, 좌우 최외곽."""
    y0 = int(min(landmarks["shoulder_l"][1], landmarks["shoulder_r"][1]) * height)
    y1 = int(max(landmarks["hem_l"][1], landmarks["hem_r"][1]) * height)
    x0 = int(min(landmarks["shoulder_l"][0], landmarks["hem_l"][0]) * width)
    x1 = int(max(landmarks["shoulder_r"][0], landmarks["hem_r"][0]) * width)
    return x0, y0, x1, y1


def torso_span(roi: tuple[int, int, int, int], *, garment_axis: str) -> float:
    """줄 진행 방향으로 잰 몸통 폭/높이 — 반복 수를 세는 축."""
    x0, y0, x1, y1 = roi
    return float(y1 - y0) if garment_axis == "horizontal" else float(x1 - x0)


def carrier_torso_span(landmarks: Mapping, *, width: int, height: int,
                       garment_axis: str) -> float:
    """carrier 에서 같은 축으로 잰 몸통 span (픽셀)."""
    if garment_axis == "horizontal":
        return (max(landmarks["hem_l"][1], landmarks["hem_r"][1])
                - min(landmarks["shoulder_l"][1], landmarks["shoulder_r"][1])) * height
    return (max(landmarks["shoulder_r"][0], landmarks["hem_r"][0])
            - min(landmarks["shoulder_l"][0], landmarks["hem_l"][0])) * width


def target_period_px(*, source_period_px: float, source_span_px: float,
                     target_span_px: float) -> float:
    """반복 수 보존 환산. source 주기가 0 에 가까우면 호출자가 이미 실패 처리한다."""
    repeats = float(source_span_px) / max(float(source_period_px), 1e-6)
    return float(target_span_px / max(repeats, 1e-6))


def aspect_via_stripe_energy(image: np.ndarray, landmarks: Mapping) -> float | None:
    """landmark quad 를 씨앗으로 줄무늬 에너지 mask 를 만들고 그 실루엣의 torso 종횡비.

    vision 이 준 torso_aspect 는 호출마다 흔들려 같은 셔츠를 상대오차 0.8 로 오판한다.
    같은 **측정 연산자**를 source/carrier 양쪽에 걸면 뷰 차이만 남고 landmark 잡음은
    사라진다. offline replay 도 같은 값을 얻어야 하므로 워커 안에 두지 않는다.
    """
    ih, iw = image.shape[:2]
    quad = np.array([
        [landmarks["shoulder_l"][0] * iw, landmarks["shoulder_l"][1] * ih],
        [landmarks["shoulder_r"][0] * iw, landmarks["shoulder_r"][1] * ih],
        [landmarks["hem_r"][0] * iw, landmarks["hem_r"][1] * ih],
        [landmarks["hem_l"][0] * iw, landmarks["hem_l"][1] * ih],
    ], np.float32)
    mask = _panel_map.mask_stripe_energy(image, [quad])
    # 해부학적 y-클립. `build_panel_map` 은 이미 같은 클립을 걸지만 이 경로는 raw mask 를
    # 그대로 재고 있었다 — 그래서 마네킹 맨다리까지 한 성분으로 딸려 들어와, 높이는
    # 전신이고 중간대 폭은 다리인 값이 나왔다(실측: 셔츠가 이미지의 26% 인데 mask bbox
    # 는 89%, 종횡비 4.90). 어깨 위·밑단 아래는 셔츠가 존재할 수 없는 영역이다.
    top = max(0, int((min(landmarks["shoulder_l"][1], landmarks["shoulder_r"][1]) - 0.02) * ih))
    bottom = min(ih, int((max(landmarks["hem_l"][1], landmarks["hem_r"][1]) + 0.03) * ih))
    if bottom - top < 8:
        return None
    clipped = np.zeros_like(mask)
    clipped[top:bottom] = mask[top:bottom]
    return _panel_map.mask_aspect_from_silhouette(clipped)
