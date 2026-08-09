"""원본 몸통 픽셀을 carrier 몸통으로 **주기 없이** 직접 옮기는 후보 (진단 전용).

왜 존재하는가
-------------
주기 경로는 스칼라 하나(`target_period_px`)가 옳은 하모닉일 때만 옳다. 실자산 f91cbac5
에서 guided 후보 격자는 {15,30,45} 였고 결정론 scan/shadow 는 ~20 을 읽었다 — 정답이
격자에 없었다. 그런 run 에는 **어떤 주기도 정본이 아니다**.

이 모듈은 그 상황에서 남는 유일한 진실을 쓴다: 원본 사진의 픽셀 그 자체. 원본 몸통
사각형을 carrier 몸통 사각형으로 homography 사상하고 원본 픽셀을 그대로 읽는다.
몸통 전체가 몸통 전체로 가므로 **몸통을 가로지르는 줄 개수는 구성상 보존된다**
(`scale_anchor` 가 문서화한 그 불변량) — 주기를 재지 않고도.

계약
----
· 이 함수는 주기 인자를 **받지 않는다**. period_px·target_period_px·guided winner 가
  결과에 영향을 줄 경로가 타입 수준에서 없다. 하모닉 오선택 면역이 시험이 아니라 구조다.
· 칠하는 픽셀은 전부 실제 원본 garment 픽셀에서 온다. 원본 배경·carrier chroma·합성
  프로파일은 텍스처에 들어가지 않는다.
· 실루엣은 건드리지 않는다 — carrier 기하는 그대로고 텍스처만 바뀐다.
· **소매는 이 phase 의 범위가 아니다**(TORSO_ONLY_CANDIDATE). 소매 픽셀은 carrier 것이
  그대로 남는다.

한계(측정해서 남기되 숨기지 않는다)
-----------------------------------
단일 homography 는 평면 사상이다. carrier 몸통이 접히거나 휘면 그 비평면성은 재현되지
않는다. 이 모드는 photorealistic drape 를 주장하지 않고
**SOURCE_PIXEL_GEOMETRY_PRESERVED_UNDER_HOMOGRAPHY** 만 주장한다.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import cv2
import numpy as np

from .color import bgr_to_lab, lab_to_bgr
from .panel_map import PanelMap
from .warp_composite import (
    MIN_DECAL_SCALE, SHADING_SIGMA_MIN_FRAC, _decal_source_eligible,
    _homography_validity, _quad_area)

DIRECT_TORSO_VERSION = "direct_torso_texture_transfer_v1"

#: 원본 휘도를 그대로 옮긴다 — 원본 사진의 조명이 함께 온다.
SHADING_RAW_SOURCE = "raw_source"
#: 원본의 색·패턴 구조 + carrier 의 저주파 휘도(주름·음영). 주기 경로가 쓰는 것과 같은
#: 분해다(warp_composite 의 `pattern_lab - pat_mean + blur_l`) — 새 relighting 을 만들지
#: 않고 이미 있는 원리를 재사용한다.
SHADING_CARRIER_LOW_FREQ_L = "carrier_low_freq_l"

#: 주기가 없으므로 주기 비례 sigma 를 쓸 수 없다. 주기 경로가 이미 하한으로 정의해 둔
#: **짧은 변 대비 비율**을 그대로 쓴다 — 해상도 독립이고 물리적 역할이 같다.
_SHADING_SIGMA_FRAC = SHADING_SIGMA_MIN_FRAC

_REASON_LANDMARKS = "torso_quad_invalid"
_REASON_TARGET = "carrier_torso_panel_missing"
_REASON_SOURCE_SHAPE = "source_torso_ineligible"
_REASON_HOMOGRAPHY = "homography_degenerate"
_REASON_NO_PIXELS = "no_source_backed_pixels"


@dataclass(frozen=True)
class DirectTorsoUnavailable:
    """후보를 만들 수 없다 — 실패가 아니라 **부재**다. 잡을 죽이지 않는다."""

    reason: str
    detail: str = ""
    metrics: dict = field(default_factory=dict)
    version: str = DIRECT_TORSO_VERSION


@dataclass(frozen=True)
class DirectTorsoCandidate:
    image_bgr: np.ndarray
    alpha: np.ndarray
    painted: np.ndarray          # source-derived 픽셀 (0/255)
    metrics: dict
    provenance: dict
    version: str = DIRECT_TORSO_VERSION


def torso_quad(landmarks, *, width: int, height: int) -> np.ndarray | None:
    """landmark(0~1) → 몸통 사각형 (4,2) px, 순서 TL·TR·BR·BL.

    `panel_map.build_panel_map` 의 carrier 몸통 quad 와 **같은 식**이다
    (`_quad([sl, sr, hr, hl])`). 같은 식을 양쪽에 걸어야 "몸통 전체 → 몸통 전체" 가
    성립하고, 그래야 줄 개수 보존이 측정이 아니라 구성이 된다. 새 landmark 를 만들지
    않는다 — `source_torso_roi` 가 쓰는 네 점 그대로다.
    """
    try:
        pts = [landmarks[k] for k in ("shoulder_l", "shoulder_r", "hem_r", "hem_l")]
    except (KeyError, TypeError):
        return None
    q = np.asarray([[float(p[0]) * width, float(p[1]) * height] for p in pts], np.float32)
    if q.shape != (4, 2) or not np.isfinite(q).all():
        return None
    x, y = q[:, 0], q[:, 1]
    signed = 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))
    if signed <= 0:                      # 뒤집힘/자기교차 — panel_map 과 같은 판정
        return None
    if not (0 <= x.min() and x.max() < width and 0 <= y.min() and y.max() < height):
        return None
    return q


def _torso_panel_quad(panel_map: PanelMap) -> np.ndarray | None:
    for panel in panel_map.panels:
        if panel.name == "torso":
            return np.asarray(panel.quad, np.float32)
    return None


def _sampling_density(H: np.ndarray, quad: np.ndarray) -> tuple[float, float, float]:
    """source→target 국소 배율. → (min_sampling_density, max_upscale, max_minification).

    target 픽셀 하나가 원본 픽셀 몇 개어치를 보는가 = sqrt(det J) 의 역수. 확대는 없는
    디테일을 지어내고, 강한 축소는 좁은 밴드를 Nyquist 아래로 밀어 뭉갠다 — 방향이 다른
    두 위험이라 둘 다 남긴다. 원근 사상에서는 한 quad 안에서 동시에 일어날 수 있다.
    """
    xs = np.linspace(quad[:, 0].min(), quad[:, 0].max(), 16)
    ys = np.linspace(quad[:, 1].min(), quad[:, 1].max(), 16)
    gx, gy = np.meshgrid(xs, ys)
    pts = np.stack([gx.ravel(), gy.ravel(), np.ones(gx.size)], axis=0)
    a, b, c = H[0], H[1], H[2]
    denom = c @ pts
    du_dx = (a[0] * denom - (a @ pts) * c[0]) / denom ** 2
    du_dy = (a[1] * denom - (a @ pts) * c[1]) / denom ** 2
    dv_dx = (b[0] * denom - (b @ pts) * c[0]) / denom ** 2
    dv_dy = (b[1] * denom - (b @ pts) * c[1]) / denom ** 2
    det = np.abs(du_dx * dv_dy - du_dy * dv_dx)
    scale = np.sqrt(np.maximum(det, 1e-12))          # target px / source px
    return (float(1.0 / max(scale.max(), 1e-9)), float(scale.max()),
            float(1.0 / max(scale.min(), 1e-9)))


def transfer_torso_texture(
    carrier_bgr: np.ndarray,
    panel_map: PanelMap,
    source_bgr: np.ndarray,
    *,
    source_landmarks,
    source_garment_mask: np.ndarray | None = None,
    shading: str = SHADING_CARRIER_LOW_FREQ_L,
    source_sha256: str | None = None,
    carrier_sha256: str | None = None,
) -> DirectTorsoCandidate | DirectTorsoUnavailable:
    """carrier 몸통을 원본 몸통 픽셀로 덮은 후보를 만든다.

    **주기 인자가 없다** — 이 서명이 하모닉 면역의 근거다.
    """
    h, w = carrier_bgr.shape[:2]
    sh, sw = source_bgr.shape[:2]

    sq = torso_quad(source_landmarks, width=sw, height=sh)
    if sq is None:
        return DirectTorsoUnavailable(_REASON_LANDMARKS, "source 몸통 quad 를 세울 수 없음")
    tq = _torso_panel_quad(panel_map)
    if tq is None:
        return DirectTorsoUnavailable(_REASON_TARGET, "panel_map 에 torso panel 이 없음")

    # 형상 적격성은 component decal 판정을 재사용한다. 다만 몸통 규모에서 short-side·
    # area·aspect 하한은 사실상 발화하지 않는다(몸통은 수백 px) — 의미가 있는 것은
    # **확대 금지**(MIN_DECAL_SCALE) 하나뿐이다. 몸통용으로 새 임계를 만들지 않는다.
    eligible, why = _decal_source_eligible(sq, tq)
    if not eligible:
        return DirectTorsoUnavailable(
            _REASON_SOURCE_SHAPE, why,
            {"sourceQuadAreaPx2": round(_quad_area(sq), 1),
             "targetQuadAreaPx2": round(_quad_area(tq), 1),
             "minSourceOverTargetArea": MIN_DECAL_SCALE})

    try:
        H = cv2.getPerspectiveTransform(sq, tq)
        Hinv = np.linalg.inv(H)
    except (cv2.error, np.linalg.LinAlgError) as exc:
        return DirectTorsoUnavailable(_REASON_HOMOGRAPHY, type(exc).__name__)
    bw = int(sq[:, 0].max() - sq[:, 0].min()) + 1
    bh = int(sq[:, 1].max() - sq[:, 1].min()) + 1
    validity = _homography_validity(H, bw, bh, _quad_area(sq))
    if validity["neg_jacobian"] > 0:
        return DirectTorsoUnavailable(_REASON_HOMOGRAPHY, "사상 방향 반전", dict(validity))
    density, upscale, minification = _sampling_density(H, sq)

    # ── target 몸통 픽셀 → 원본 좌표 ──────────────────────────────────────
    gx, gy = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    grid = np.stack([gx.ravel(), gy.ravel(), np.ones(gx.size, np.float32)], axis=0)
    src = Hinv @ grid
    map_x = (src[0] / np.maximum(src[2], 1e-9)).reshape(h, w).astype(np.float32)
    map_y = (src[1] / np.maximum(src[2], 1e-9)).reshape(h, w).astype(np.float32)

    torso_region = np.zeros((h, w), np.uint8)
    cv2.fillPoly(torso_region, [tq.astype(np.int32)], 255)
    region = (torso_region > 0) & (panel_map.garment_mask > 0)
    in_bounds = ((map_x >= 0) & (map_x <= sw - 1) & (map_y >= 0) & (map_y <= sh - 1))
    paint = region & in_bounds
    # 원본 garment mask 가 있으면 **배경을 샘플한 픽셀은 칠하지 않는다**. quad 모서리가
    # 옷 밖으로 조금 나가도 배경색이 옷 위에 실리는 일이 구조적으로 불가능해진다.
    background_rejected = 0
    if source_garment_mask is not None:
        smask = cv2.remap(source_garment_mask, map_x, map_y,
                          interpolation=cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT)
        background_rejected = int((paint & (smask == 0)).sum())
        paint &= smask > 0
    if not paint.any():
        return DirectTorsoUnavailable(
            _REASON_NO_PIXELS, "원본으로 뒷받침되는 몸통 픽셀 0",
            {"torsoRegionPx": int(region.sum()),
             "outOfSourceFrac": round(float((region & ~in_bounds).sum())
                                      / max(1, int(region.sum())), 4)})

    warped_bgr = cv2.remap(source_bgr, map_x, map_y, interpolation=cv2.INTER_LINEAR,
                           borderMode=cv2.BORDER_CONSTANT)
    source_lab = bgr_to_lab(warped_bgr)
    carrier_lab = bgr_to_lab(carrier_bgr)

    out_lab = source_lab.copy()
    if shading == SHADING_CARRIER_LOW_FREQ_L:
        # 절대 휘도의 정본은 carrier 의 저주파 L, 상대 구조는 원본 것 — 주기 경로가
        # `pattern_lab[...,0] - pat_mean + blur_l` 로 쓰는 것과 같은 분해다.
        sigma = float(min(h, w)) * _SHADING_SIGMA_FRAC
        blur_l = cv2.GaussianBlur(carrier_lab[..., 0], (0, 0), sigmaX=sigma)
        src_mean_l = float(source_lab[..., 0][paint].mean())
        out_lab[..., 0] = np.clip(source_lab[..., 0] - src_mean_l + blur_l, 0.0, 100.0)
    elif shading != SHADING_RAW_SOURCE:
        return DirectTorsoUnavailable("unknown_shading_mode", str(shading)[:60])

    # ── feather — 실루엣 밴드와 painted 내부 계면을 각각 만들어 최솟값 ─────
    band_px = max(1.0, float(panel_map.metrics.get("boundary_band_px", 4)))
    painted = (paint.astype(np.uint8) * 255)
    silhouette_ramp = np.clip(
        cv2.distanceTransform(panel_map.garment_mask, cv2.DIST_L2, 3) / band_px, 0.0, 1.0)
    inner_ramp = np.clip(
        cv2.distanceTransform(painted, cv2.DIST_L2, 3) / band_px, 0.0, 1.0)
    alpha = np.minimum(silhouette_ramp, inner_ramp).astype(np.float32)
    alpha[panel_map.garment_mask == 0] = 0.0

    out_bgr = np.clip(
        alpha[..., None] * lab_to_bgr(out_lab).astype(np.float32)
        + (1.0 - alpha[..., None]) * carrier_bgr.astype(np.float32), 0, 255
    ).astype(np.uint8)

    # 측정만 한다 — 이 phase 에서 carrier chroma 로 원본 색을 끌어당기지 않는다.
    # 원본 색 진실이 이 모드의 존재 이유이고, 보정은 승격 논의 때 별도로 판단한다.
    cast = (np.median(carrier_lab[..., 1:3][paint], axis=0)
            - np.median(source_lab[..., 1:3][paint], axis=0))
    metrics = {
        "shadingMode": shading,
        "torsoRegionPx": int(region.sum()),
        "paintedPx": int(paint.sum()),
        "torsoCoverage": round(float(paint.sum()) / max(1, int(region.sum())), 4),
        "outOfSourceFrac": round(
            float((region & ~in_bounds).sum()) / max(1, int(region.sum())), 4),
        "backgroundRejectedPx": background_rejected,
        "sourceMaskApplied": source_garment_mask is not None,
        "minSourceSamplingDensity": round(density, 4),
        "maxUpscaleFactor": round(upscale, 4),
        "maxMinificationFactor": round(minification, 4),
        "sourceQuadAreaPx2": round(_quad_area(sq), 1),
        "targetQuadAreaPx2": round(_quad_area(tq), 1),
        "measuredChromaCastAb": [round(float(cast[0]), 3), round(float(cast[1]), 3)],
        "sourceChromaMedianAb": [
            round(float(np.median(source_lab[..., 1][paint])), 3),
            round(float(np.median(source_lab[..., 2][paint])), 3)],
        **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in validity.items()},
    }
    provenance = {
        "version": DIRECT_TORSO_VERSION,
        "sourceSha256": source_sha256,
        "carrierSha256": carrier_sha256,
        "sourceQuad": [[round(float(x), 3), round(float(y), 3)] for x, y in sq],
        "targetQuad": [[round(float(x), 3), round(float(y), 3)] for x, y in tq],
        "homography": [[round(float(v), 9) for v in row] for row in H],
        "interpolation": "INTER_LINEAR",
        "sourceMaskInterpolation": "INTER_NEAREST",
        "garmentMaskSha256": hashlib.sha256(
            np.ascontiguousarray(panel_map.garment_mask).tobytes()).hexdigest()[:16],
        "shadingMode": shading,
        "shadingSigmaShortSideFrac": _SHADING_SIGMA_FRAC,
        "periodInputs": None,        # 이 모드는 주기를 받지 않는다 — 계약의 일부다
    }
    return DirectTorsoCandidate(image_bgr=out_bgr, alpha=alpha, painted=painted,
                                metrics=metrics, provenance=provenance)
