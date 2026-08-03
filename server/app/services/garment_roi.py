"""원본 flat-lay와 고정 마네킹 결과에서 의류 ROI를 결정적으로 추출한다."""

from __future__ import annotations

import cv2
import numpy as np


def _largest(mask: np.ndarray) -> np.ndarray:
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
    if count <= 1:
        return np.zeros(mask.shape, np.uint8)
    index = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    return (labels == index).astype(np.uint8) * 255


def foreground_mask(image_bgr: np.ndarray) -> tuple[np.ndarray, float]:
    image = np.asarray(image_bgr, np.uint8)
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB).astype(np.float32)
    border = np.concatenate((lab[0], lab[-1], lab[:, 0], lab[:, -1]), axis=0)
    bg = np.median(border, axis=0)
    distance = np.linalg.norm(lab - bg, axis=2)
    raw = (distance > max(7.0, float(np.quantile(distance, 0.55)))).astype(np.uint8)
    kernel = np.ones((5, 5), np.uint8)
    mask = cv2.morphologyEx(raw, cv2.MORPH_CLOSE, kernel, iterations=2)
    mask = _largest(mask)
    ratio = float((mask > 0).mean())
    confidence = 0.0 if ratio < 0.02 or ratio > 0.9 else min(1.0, ratio / 0.15)
    return mask, confidence

def mannequin_difference_mask(
    base_bgr: np.ndarray, output_bgr: np.ndarray,
) -> tuple[np.ndarray, float]:
    output = np.asarray(output_bgr, np.uint8)
    base = cv2.resize(np.asarray(base_bgr, np.uint8), (output.shape[1], output.shape[0]))
    base_lab = cv2.cvtColor(base, cv2.COLOR_BGR2LAB).astype(np.float32)
    out_lab = cv2.cvtColor(output, cv2.COLOR_BGR2LAB).astype(np.float32)
    distance = np.linalg.norm(out_lab - base_lab, axis=2)
    raw = (distance > 10.0).astype(np.uint8)
    raw[: int(output.shape[0] * 0.12)] = 0  # 얼굴/머리 변화는 의류가 아니다.
    kernel = np.ones((7, 7), np.uint8)
    mask = cv2.morphologyEx(raw, cv2.MORPH_CLOSE, kernel, iterations=2)
    mask = _largest(mask)
    ratio = float((mask > 0).mean())
    confidence = 0.0 if ratio < 0.015 or ratio > 0.75 else min(1.0, ratio / 0.12)
    return mask, confidence
