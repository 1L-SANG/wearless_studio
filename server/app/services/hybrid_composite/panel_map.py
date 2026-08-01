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
# 정규화 비율 상대 오차 허용. flat-lay 정면 ↔ 3/4 착장뷰 교차 비교의 노이즈 플로어를
# 같은 셔츠 쌍으로 실측(2026-08-01, vision 3회: 6.4%/25%/29% — 시점 폭 압축 + landmark 지터).
# 진짜 구조 불일치(기장/폭 다른 옷)는 50%+ 로 갈라져 0.35 에서도 차단된다(단위테스트 ×1.5).
CONSTRUCTION_RATIO_TOL = 0.35


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


def mask_stripe_energy(carrier_bgr: np.ndarray, panel_polys: list[np.ndarray]) -> np.ndarray:
    """줄무늬 에너지 mask — 스트라이프 상품 전용: '주기 신호가 있는 곳'이 의류다.

    흰 셔츠/밝은 배경에서는 색距(bg_diff)도 GrabCut 도 실루엣을 못 찾는다(실측: mask 가
    quad 와 사실상 동일 = 정보 없음 → 평판 슬랩 합성). 그러나 carrier 의 셔츠에는 생성된
    줄무늬가 이미 있으므로, 국소 고주파 에너지(DoG)로 줄무늬 영역 자체를 실루엣으로 쓴다.
    배경·마네킹·무지 스커트는 평탄해서 자연히 빠진다. 결정론(고정 커널).
    """
    h, w = carrier_bgr.shape[:2]
    scale = min(1.0, 1200.0 / max(h, w))
    if scale < 1.0:
        small = cv2.resize(carrier_bgr, (int(w * scale), int(h * scale)),
                           interpolation=cv2.INTER_AREA)
        small_polys = [(p * scale).astype(np.float32) for p in panel_polys]
        m = mask_stripe_energy(small, small_polys)
        return cv2.resize(m, (w, h), interpolation=cv2.INTER_NEAREST)
    gray = cv2.cvtColor(carrier_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    # DoG 밴드패스 — 줄 주기 2~12px(축소 공간) 대역의 에너지
    band = cv2.GaussianBlur(gray, (0, 0), 1.0) - cv2.GaussianBlur(gray, (0, 0), 4.0)
    energy = cv2.GaussianBlur(np.abs(band), (0, 0), 9.0)
    poly_mask = np.zeros((h, w), np.uint8)
    for p in panel_polys:
        cv2.fillPoly(poly_mask, [p.astype(np.int32)], 255)
    inside = energy[poly_mask > 0]
    if inside.size == 0:
        return poly_mask
    thr = max(0.5, float(np.median(inside)) * 0.45)
    fg = (energy > thr).astype(np.uint8) * 255
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, kernel, iterations=3)
    fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, kernel, iterations=1)
    # panel 과 겹치는 연결 성분만 (배경 노이즈 성분 제거)
    n_lab, labels = cv2.connectedComponents(fg)
    keep = np.zeros((h, w), np.uint8)
    overlap_ids = set(np.unique(labels[(poly_mask > 0) & (fg > 0)])) - {0}
    for i in overlap_ids:
        keep[labels == i] = 255
    return keep


def mask_grabcut(carrier_bgr: np.ndarray, panel_polys: list[np.ndarray]) -> np.ndarray:
    """GrabCut mask — panel polygon 을 확실-전경 seed 로.

    큰 원본(4K+)에서 GrabCut 은 분 단위로 걸린다(실측 10분+ 타임아웃). 마스크 추정은
    최대변 1200 축소본에서 수행하고 NEAREST 업스케일한다 — 경계 오차 ~3px 는 boundary
    feather 밴드(이미지 짧은 변의 1.2%) 안이다.
    """
    full_h, full_w = carrier_bgr.shape[:2]
    scale = min(1.0, 1200.0 / max(full_h, full_w))
    if scale < 1.0:
        small = cv2.resize(carrier_bgr, (int(full_w * scale), int(full_h * scale)),
                           interpolation=cv2.INTER_AREA)
        small_polys = [(p * scale).astype(np.float32) for p in panel_polys]
        mask_small = mask_grabcut(small, small_polys)
        return cv2.resize(mask_small, (full_w, full_h), interpolation=cv2.INTER_NEAREST)
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
                # 가시 단추 수 관용 ±2 — flat-lay 정면과 3/4 착장뷰는 **가시성** 자체가 다르다
                # (실측: 정면 6개 셔츠가 착장 3/4 뷰에서 4~5개만 보임 — 밑단 드레이프·각도).
                # 구조 단순화(예: 7→4 미만)는 여전히 차단된다.
                if abs(s_val - c_val) > 2:
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
    poly_mask = np.zeros((h, w), np.uint8)
    for p in polys:
        cv2.fillPoly(poly_mask, [p.astype(np.int32)], 255)

    # confidence 정의 — 실질 위험 두 가지만 잰다:
    #  (1) poly_cover: 패널이 garment mask 밖으로 삐져나오면 배경/마네킹에 패턴을 칠한다.
    #  (2) bg_inside: 패널 quad 내부에 배경색 픽셀이 섞이면 landmark 가 의류 밖에 걸린 것.
    # 초기 공식(min(IoU/0.9, cover))은 mask ⊃ panels 를 벌점했는데, 그건 결함이 아니라
    # 정상 기하다 — 패널은 의류 **내부** 보수 quad 고 mask 는 의류 전체다(실사진 실측:
    # cover 0.967 인 유효 구성이 IoU 0.37 로 기각됐다). synthetic 에서 안 드러난 이유는
    # 합성 의류가 패널 합집합과 거의 일치하도록 그려졌기 때문.
    # (bg_inside 색距 가드는 제거 — 흰 셔츠/밝은 배경에서 의류 자체가 배경색과 같아
    # 65% 가 "배경"으로 오판됐다. landmark 이탈 위험은 cover·construction 대조·
    # deterministic QC 의 outside drift 가 담당한다.)
    def score(mask):
        inter = cv2.bitwise_and(mask, poly_mask)
        cover = float(np.count_nonzero(inter)) / max(1, np.count_nonzero(poly_mask))
        return cover, cover, 0.0

    # 전략 선택: synthetic(회색 의류/밝은 배경)에선 bg_diff 가 정확+빠르지만, 실사진의
    # **흰 셔츠/밝은 배경**에서는 색距 전경이 통째로 구멍난다(실측 cover 0.29). auto 는
    # bg_diff 미달 시 grabcut 으로 폴백해 더 나은 쪽을 쓴다 — 결정론(두 알고리즘 모두 고정 seed).
    tried = {}
    if strategy in ("bg_diff", "auto"):
        tried["bg_diff"] = mask_bg_diff(carrier_bgr, polys)
    if strategy in ("stripe_energy", "auto"):
        tried["stripe_energy"] = mask_stripe_energy(carrier_bgr, polys)
    if strategy == "grabcut" or (
            strategy == "auto"
            and max(score(m)[0] for m in tried.values()) < MIN_MASK_CONFIDENCE):
        tried["grabcut"] = mask_grabcut(carrier_bgr, polys)
    strategy_used, garment = max(tried.items(), key=lambda kv: score(kv[1])[0])
    confidence, poly_cover, bg_inside = score(garment)
    iou = float(np.count_nonzero(cv2.bitwise_and(garment, poly_mask))) / max(
        1, np.count_nonzero(cv2.bitwise_or(garment, poly_mask)))  # 관측용 기록
    strategy = strategy_used
    if confidence < MIN_MASK_CONFIDENCE:
        return CompositeFailure(
            "mask_low_confidence",
            f"mask-panel 정합 {confidence:.2f} < {MIN_MASK_CONFIDENCE}",
            {"iou": round(iou, 3), "poly_cover": round(poly_cover, 3),
             "bg_inside_frac": round(bg_inside, 3),
             "strategies_tried": {k: round(score(v)[0], 3) for k, v in tried.items()}})

    work = cv2.bitwise_and(garment, poly_mask)  # 패턴 대상 = mask ∩ panel 합집합
    band = max(3, int(min(h, w) * BOUNDARY_BAND_PX_FRAC))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (band * 2 + 1, band * 2 + 1))
    protected = cv2.erode(work, kernel)
    boundary = cv2.subtract(work, protected)

    return PanelMap(
        garment_mask=work, protected=protected, boundary=boundary,
        panels=tuple(panels), confidence=float(min(confidence, 1.0)), strategy=strategy,
        metrics={"iou_poly_mask": round(iou, 3), "poly_cover": round(poly_cover, 3),
                 "boundary_band_px": band, **inv_metrics})
