"""Stage 3 — geometry carrier 의 garment mask·panel·landmark (OpenCV, 결정론).

carrier(생성 결과 + geometry 편집 완료본)에서 '어디에 패턴을 입힐지'를 만든다.
landmark 는 외부에서 주입된다 — production 은 vision JSON extractor, fixture 는 GT.
여기서는 landmark 를 **검증**하고 mask/panel/보호영역을 결정론적으로 산출한다.

mask 전략은 두 가지를 구현해 fixture 로 비교했다(decision-log 참조):
  · bg_diff  — 밝은 중립 배경과의 색距로 전경 추출 + 모폴로지 정리
  · grabcut  — panel polygon seed 기반 GrabCut
스튜디오 컷 도메인(배경이 항상 밝고 균일)에서는 bg_diff 가 IoU 우위 + 20배 빠름.
"""

from dataclasses import dataclass, field

import cv2
import numpy as np

from .types import CompositeFailure, PANEL_MAP_VERSION

MIN_MASK_CONFIDENCE = 0.80   # panel 합집합과 mask 의 정합(아래 confidence 정의) 하한
BOUNDARY_BAND_PX_FRAC = 0.012  # 이미지 짧은 변 대비 feather 밴드 폭
CONSTRUCTION_COUNT_KEYS = ("visible_buttons",)
CONSTRUCTION_BOOL_KEYS = ("collar", "placket", "cuffs")
CONSTRUCTION_RATIO_KEYS = ("torso_aspect", "sleeve_len_ratio")
CONSTRUCTION_RATIO_TOL = 0.22  # 정규화 비율 상대 오차 허용 — fixture 로 검증된 초기값


@dataclass(frozen=True)
class Panel:
    name: str            # torso | sleeve_l | sleeve_r | collar | placket | cuff_l | cuff_r
    kind: str            # "stripe"(타일 합성) | "decal"(source 패치 warp)
    quad: np.ndarray     # (4,2) float32 — TL, TR, BR, BL (px)


@dataclass(frozen=True)
class PanelMap:
    garment_mask: np.ndarray    # (H,W) uint8 0/255
    protected: np.ndarray       # 내부 보호영역 (패턴이 완전 소유)
    boundary: np.ndarray        # feather 밴드 (mask ∩ ¬protected)
    panels: tuple               # tuple[Panel, ...]
    confidence: float
    strategy: str
    version: str = PANEL_MAP_VERSION
    metrics: dict = field(default_factory=dict)


def _quad(points: list) -> np.ndarray:
    q = np.asarray(points, dtype=np.float32)
    if q.shape != (4, 2):
        raise ValueError("quad 는 (4,2)")
    return q


def _quad_convex_and_ccw_area(q: np.ndarray) -> float:
    """signed area — 자기교차/뒤집힌 quad 검출용 (양수=정상 방향)."""
    x, y = q[:, 0], q[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def mask_bg_diff(carrier_bgr: np.ndarray, panel_polys: list[np.ndarray]) -> np.ndarray:
    """배경 차분 mask — 테두리 밴드에서 배경색을 추정하고 색距로 전경을 자른다."""
    h, w = carrier_bgr.shape[:2]
    band = max(4, min(h, w) // 50)
    border = np.concatenate([
        carrier_bgr[:band].reshape(-1, 3), carrier_bgr[-band:].reshape(-1, 3),
        carrier_bgr[:, :band].reshape(-1, 3), carrier_bgr[:, -band:].reshape(-1, 3)])
    bg = np.median(border, axis=0)
    dist = np.linalg.norm(carrier_bgr.astype(np.float64) - bg, axis=-1)
    fg = (dist > 28).astype(np.uint8) * 255
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, kernel, iterations=2)
    fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, kernel, iterations=1)
    # 의류 후보 = panel 합집합 근방의 전경만 (마네킹 다리·소품 배제)
    poly_mask = np.zeros((h, w), np.uint8)
    for p in panel_polys:
        cv2.fillPoly(poly_mask, [p.astype(np.int32)], 255)
    poly_dilated = cv2.dilate(poly_mask, kernel, iterations=6)
    return cv2.bitwise_and(fg, poly_dilated)


def mask_grabcut(carrier_bgr: np.ndarray, panel_polys: list[np.ndarray]) -> np.ndarray:
    """GrabCut mask — panel polygon 을 확실-전경 seed 로."""
    h, w = carrier_bgr.shape[:2]
    gc = np.full((h, w), cv2.GC_PR_BGD, np.uint8)
    band = max(4, min(h, w) // 50)
    gc[:band] = cv2.GC_BGD; gc[-band:] = cv2.GC_BGD
    gc[:, :band] = cv2.GC_BGD; gc[:, -band:] = cv2.GC_BGD
    poly_mask = np.zeros((h, w), np.uint8)
    for p in panel_polys:
        cv2.fillPoly(poly_mask, [p.astype(np.int32)], 255)
    eroded = cv2.erode(poly_mask, np.ones((9, 9), np.uint8))
    gc[poly_mask > 0] = cv2.GC_PR_FGD
    gc[eroded > 0] = cv2.GC_FGD
    bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    cv2.grabCut(carrier_bgr, gc, None, bgd, fgd, 3, cv2.GC_INIT_WITH_MASK)
    return np.where((gc == cv2.GC_FGD) | (gc == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)


def build_panel_map(
    carrier_bgr: np.ndarray,
    landmarks: dict,
    *,
    source_inventory: dict | None = None,
    carrier_inventory: dict | None = None,
    strategy: str = "bg_diff",
) -> PanelMap | CompositeFailure:
    """landmarks(정규화 0~1 좌표) → PanelMap. 모든 판정 불가는 typed 실패.

    `source_inventory` 와 `carrier_inventory` 가 함께 오면 construction 대조를 수행한다 —
    패턴 합성이 아무리 좋아도 칼라·단추·비율이 다른 carrier 는 같은 상품이 아니다.
    """
    h, w = carrier_bgr.shape[:2]
    required = ("shoulder_l", "shoulder_r", "hem_l", "hem_r")
    missing = [k for k in required if k not in (landmarks or {})]
    if missing:
        return CompositeFailure("panel_landmarks_invalid", f"landmark 누락: {missing}")

    def px(name):
        v = landmarks[name]
        return np.array([v[0] * w, v[1] * h], np.float32)

    sl, sr = px("shoulder_l"), px("shoulder_r")
    hl, hr = px("hem_l"), px("hem_r")
    torso_q = _quad([sl, sr, hr, hl])
    if _quad_convex_and_ccw_area(torso_q) <= 0:
        return CompositeFailure("panel_landmarks_invalid", "torso quad 뒤집힘/자기교차")
    if not (0 <= min(torso_q[:, 0]) and max(torso_q[:, 0]) < w
            and 0 <= min(torso_q[:, 1]) and max(torso_q[:, 1]) < h):
        return CompositeFailure("panel_landmarks_invalid", "torso quad 가 이미지 밖")

    panels: list[Panel] = [Panel("torso", "stripe", torso_q)]
    for side, key in (("l", "sleeve_l_end"), ("r", "sleeve_r_end")):
        if key not in landmarks:
            continue
        end = px(key)
        top = sl if side == "l" else sr
        # 소매 quad — 어깨점에서 소매끝으로, 폭은 어깨-소매끝 거리에 비례한 근사 밴드
        d = end - top
        norm = np.array([-d[1], d[0]], np.float32)
        nlen = float(np.linalg.norm(norm))
        if nlen < 1e-3:
            continue
        norm = norm / nlen * max(8.0, float(np.linalg.norm(d)) * 0.28)
        q = _quad([top - norm * 0.4, top + norm * 0.6, end + norm * 0.6, end - norm * 0.4])
        if abs(_quad_convex_and_ccw_area(q)) < 16:
            continue
        panels.append(Panel(f"sleeve_{side}", "stripe",
                            q if _quad_convex_and_ccw_area(q) > 0 else q[::-1].copy()))

    # construction 대조 — geometry carrier 가 원본과 같은 구조인가
    inv_metrics = {}
    if source_inventory is not None and carrier_inventory is not None:
        for k in CONSTRUCTION_BOOL_KEYS:
            if k in source_inventory and bool(source_inventory[k]) != bool(
                    carrier_inventory.get(k)):
                return CompositeFailure(
                    "geometry_carrier_mismatch", f"construction 불일치: {k}",
                    {"source": source_inventory.get(k), "carrier": carrier_inventory.get(k)})
        for k in CONSTRUCTION_COUNT_KEYS:
            if k in source_inventory and carrier_inventory.get(k) is not None:
                s_val, c_val = int(source_inventory[k]), int(carrier_inventory[k])
                if abs(s_val - c_val) > 1:  # vision 카운트 ±1 관용
                    return CompositeFailure(
                        "geometry_carrier_mismatch", f"{k}: source {s_val} vs carrier {c_val}",
                        {"source": s_val, "carrier": c_val})
                inv_metrics[k] = {"source": s_val, "carrier": c_val}
        for k in CONSTRUCTION_RATIO_KEYS:
            s_val, c_val = source_inventory.get(k), carrier_inventory.get(k)
            if isinstance(s_val, (int, float)) and isinstance(c_val, (int, float)) and s_val > 0:
                rel = abs(c_val - s_val) / s_val
                inv_metrics[k] = {"source": s_val, "carrier": c_val, "rel_err": round(rel, 3)}
                if rel > CONSTRUCTION_RATIO_TOL:
                    return CompositeFailure(
                        "geometry_carrier_mismatch",
                        f"{k} 상대 오차 {rel:.2f} > {CONSTRUCTION_RATIO_TOL}",
                        {"source": s_val, "carrier": c_val})

    polys = [p.quad for p in panels]
    if strategy == "grabcut":
        garment = mask_grabcut(carrier_bgr, polys)
    else:
        garment = mask_bg_diff(carrier_bgr, polys)

    poly_mask = np.zeros((h, w), np.uint8)
    for p in polys:
        cv2.fillPoly(poly_mask, [p.astype(np.int32)], 255)
    inter = cv2.bitwise_and(garment, poly_mask)
    union = cv2.bitwise_or(garment, poly_mask)
    iou = float(np.count_nonzero(inter)) / max(1, np.count_nonzero(union))
    poly_cover = float(np.count_nonzero(inter)) / max(1, np.count_nonzero(poly_mask))
    confidence = min(iou / 0.9, poly_cover)  # panel 이 mask 밖에 있으면 즉시 깎인다
    if confidence < MIN_MASK_CONFIDENCE:
        return CompositeFailure(
            "mask_low_confidence",
            f"mask-panel 정합 {confidence:.2f} < {MIN_MASK_CONFIDENCE}",
            {"iou": round(iou, 3), "poly_cover": round(poly_cover, 3), "strategy": strategy})

    work = inter  # 패턴 대상 = mask ∩ panel 합집합
    band = max(3, int(min(h, w) * BOUNDARY_BAND_PX_FRAC))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (band * 2 + 1, band * 2 + 1))
    protected = cv2.erode(work, kernel)
    boundary = cv2.subtract(work, protected)

    return PanelMap(
        garment_mask=work, protected=protected, boundary=boundary,
        panels=tuple(panels), confidence=float(min(confidence, 1.0)), strategy=strategy,
        metrics={"iou_poly_mask": round(iou, 3), "poly_cover": round(poly_cover, 3),
                 "boundary_band_px": band, **inv_metrics})
