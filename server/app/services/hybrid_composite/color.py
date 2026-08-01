"""색 공간 헬퍼 — BGR↔Lab 변환과 CIEDE2000 (순수 NumPy, 외부 의존 없음).

CIEDE2000 은 OpenCV 에 없다. Sharma·Wu·Dalal(2005)의 기준 구현을 그대로 옮기고, 논문 공개
검증 벡터를 단위 테스트로 고정한다 — 구현이 검증되기 전의 ΔE 숫자는 gate 로 쓸 수 없다.
"""

import cv2
import numpy as np


def bgr_to_lab(img_bgr: np.ndarray) -> np.ndarray:
    """uint8 BGR → float32 Lab (L∈[0,100], a/b 실측 범위). OpenCV 8U Lab 스케일을 복원한다."""
    lab = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    lab[..., 0] *= 100.0 / 255.0
    lab[..., 1] -= 128.0
    lab[..., 2] -= 128.0
    return lab


def lab_to_bgr(lab: np.ndarray) -> np.ndarray:
    """float32 Lab (위 스케일) → uint8 BGR."""
    out = lab.astype(np.float32).copy()
    out[..., 0] *= 255.0 / 100.0
    out[..., 1] += 128.0
    out[..., 2] += 128.0
    out = np.clip(out, 0, 255).astype(np.uint8)
    return cv2.cvtColor(out, cv2.COLOR_LAB2BGR)


def ciede2000(lab1, lab2) -> np.ndarray:
    """ΔE00. 입력은 (..., 3) 배열 또는 3-튜플. Sharma 2005 수식 그대로."""
    lab1 = np.asarray(lab1, dtype=np.float64)
    lab2 = np.asarray(lab2, dtype=np.float64)
    L1, a1, b1 = lab1[..., 0], lab1[..., 1], lab1[..., 2]
    L2, a2, b2 = lab2[..., 0], lab2[..., 1], lab2[..., 2]

    C1 = np.hypot(a1, b1)
    C2 = np.hypot(a2, b2)
    Cbar = (C1 + C2) / 2.0
    G = 0.5 * (1.0 - np.sqrt(Cbar ** 7 / (Cbar ** 7 + 25.0 ** 7)))
    a1p = (1.0 + G) * a1
    a2p = (1.0 + G) * a2
    C1p = np.hypot(a1p, b1)
    C2p = np.hypot(a2p, b2)
    h1p = np.degrees(np.arctan2(b1, a1p)) % 360.0
    h2p = np.degrees(np.arctan2(b2, a2p)) % 360.0
    # C'=0 이면 hue 는 정의되지 않는다 — Sharma 규약대로 0 처리
    h1p = np.where(C1p == 0, 0.0, h1p)
    h2p = np.where(C2p == 0, 0.0, h2p)

    dLp = L2 - L1
    dCp = C2p - C1p
    dh = h2p - h1p
    dhp = np.where(np.abs(dh) <= 180.0, dh, np.where(dh > 180.0, dh - 360.0, dh + 360.0))
    dhp = np.where(C1p * C2p == 0, 0.0, dhp)
    dHp = 2.0 * np.sqrt(C1p * C2p) * np.sin(np.radians(dhp) / 2.0)

    Lbp = (L1 + L2) / 2.0
    Cbp = (C1p + C2p) / 2.0
    hsum = h1p + h2p
    habs = np.abs(h1p - h2p)
    hbp = np.where(
        C1p * C2p == 0, hsum,
        np.where(habs <= 180.0, hsum / 2.0,
                 np.where(hsum < 360.0, (hsum + 360.0) / 2.0, (hsum - 360.0) / 2.0)))

    T = (1.0
         - 0.17 * np.cos(np.radians(hbp - 30.0))
         + 0.24 * np.cos(np.radians(2.0 * hbp))
         + 0.32 * np.cos(np.radians(3.0 * hbp + 6.0))
         - 0.20 * np.cos(np.radians(4.0 * hbp - 63.0)))
    dtheta = 30.0 * np.exp(-(((hbp - 275.0) / 25.0) ** 2))
    RC = 2.0 * np.sqrt(Cbp ** 7 / (Cbp ** 7 + 25.0 ** 7))
    SL = 1.0 + (0.015 * (Lbp - 50.0) ** 2) / np.sqrt(20.0 + (Lbp - 50.0) ** 2)
    SC = 1.0 + 0.045 * Cbp
    SH = 1.0 + 0.015 * Cbp * T
    RT = -np.sin(np.radians(2.0 * dtheta)) * RC

    dE = np.sqrt(
        (dLp / SL) ** 2 + (dCp / SC) ** 2 + (dHp / SH) ** 2
        + RT * (dCp / SC) * (dHp / SH))
    return dE


def delta_e76(lab1, lab2) -> np.ndarray:
    """단순 유클리드 ΔE76 — carrier 보존(mask 밖 drift) 측정용."""
    lab1 = np.asarray(lab1, dtype=np.float64)
    lab2 = np.asarray(lab2, dtype=np.float64)
    return np.sqrt(((lab1 - lab2) ** 2).sum(axis=-1))
