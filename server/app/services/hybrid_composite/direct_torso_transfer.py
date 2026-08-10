"""원본 몸통·부위 픽셀을 carrier 로 **주기 없이** 직접 옮기는 후보 (진단 전용).

왜 존재하는가
-------------
주기 경로는 스칼라 하나(`target_period_px`)가 옳은 하모닉일 때만 옳다. 실자산 f91cbac5
에서 guided 후보 격자는 {15,30,45} 였고 결정론 scan/shadow 는 ~20 을 읽었다 — 정답이
격자에 없었다. 그런 run 에는 **어떤 주기도 정본이 아니다**.

이 모듈은 그때 남는 유일한 진실을 쓴다: 원본 사진의 픽셀. 원본 몸통 사각형을 carrier
몸통 사각형으로 homography 사상하고 원본 픽셀을 그대로 읽는다. 몸통 전체가 몸통 전체로
가므로 **몸통을 가로지르는 줄 개수는 구성상 보존된다**(`scale_anchor` 의 불변량) —
주기를 재지 않고도.

계약
----
· 주기 인자를 **받지 않는다**. period_px·target_period_px·guided winner 가 결과에 영향을
  줄 경로가 타입 수준에서 없다. 하모닉 오선택 면역이 시험이 아니라 구조다.
· 칠하는 픽셀은 전부 실제 원본 garment 픽셀에서 온다. 원본 배경·carrier chroma·합성
  프로파일은 텍스처에 들어가지 않는다.
· 실루엣은 건드리지 않는다 — carrier 기하는 그대로고 텍스처만 바뀐다.
· 다루는 면은 **몸통 panel + 넘겨받은 component box** 다. 소매를 하나의 면으로 옮기지는
  않지만(소매 panel 은 carrier 그대로), cuff 처럼 몸통 quad 밖의 부위 box 도 주어지면
  채운다. 몸통 밖에 칠한 양은 `componentPxOutsideTorsoQuad` 로 남긴다.
· **구조는 텍스처가 아니다 — 하지만 버리는 것도 아니다.**
  carrier box 만 주면: 그 부위를 몸통 원단으로 덮지 않고 carrier 에 남긴다(배제).
  양쪽 box 를 다 주면: 그 부위를 **원본의 같은 부위 픽셀로** 채운다(부위별 box→box 사상).
  몸통 사상은 부위를 정렬하지 못하지만(한계 3), 부위 사상은 정의상 정렬한다.
  원본 구조 픽셀은 **몸통 표본에서만** 금지된다 — 부위 표본에서는 그것이 정답이다.

한계(측정해서 남기되 숨기지 않는다)
-----------------------------------
1) 단일 homography 는 평면 사상이다. carrier 가 접히거나 휘면 그 비평면성은 재현되지
   않는다. **SOURCE_PIXEL_GEOMETRY_PRESERVED_UNDER_HOMOGRAPHY** 만 주장한다.

2) 조명 분해가 걷어내는 것은 **전역 저주파 조명**이다. 실자산 Tier-1(3392px 짧은 변):
   원본 저주파 상관 0.937 → 0.116(기본 sigma), 밴드 하한에서 0.026. 그러나 **주름의
   가장자리 그림자**는 이 해상도에서 텍스처 대역(수십 px)이라 등방 Gaussian 하나로는
   9px 줄무늬와 분리되지 않는다. 원본 주름 자국은 남고 거기에 carrier 음영이 얹힌다.

3) 몸통 quad→quad homography 는 **부위를 정렬하지 않는다**. 기록된 실자산 3건에서 원본
   placket box 를 몸통 사상으로 옮기면 carrier placket box 와 IoU 0.016 / 0.000 / 0.000,
   다각형 무게중심은 몸통 폭의 21.8 / 32.7 / 31.6% 어긋난다(collar IoU 0.57/0.41/0.32).
   그래서 부위는 몸통 사상이 아니라 **자기 box→box 사상**으로 채운다.
   `componentPlacement` 로 몸통 사상 기준의 어긋남을 매 호출 남긴다.

4) **저주파는 조명인지 원단인지 구분되지 않는다.** 색블록·옴브레·큰 그래픽처럼 원단이
   저주파 휘도 구조를 가지면 split 이 그것도 지운다. `sourceLowFreqStdL` 이 원본의 저주파
   양을 남기지만 **양을 재지 종류를 가르지 못한다**.
   재현: `test_split_also_erases_genuine_low_frequency_garment_content`
   (밝은/어두운 절반 대비 54.51L → 2.46L, sourceLowFreqStdL 26.328; 평탄 0.748~1.444).

5) L 재결합은 [0,100] 으로 **하드 클리핑**된다. `clippedFracL` 로 매 호출 노출한다.
   톤매핑은 하지 않는다 — 측정 없이 압축하면 무엇을 고쳤는지 알 수 없다.
   재현: `test_bright_carrier_clipping_is_measured_and_exposed`
   (흰 carrier 0.70 / retention 0.7084, 보통 carrier 0.00 / 0.9987).

6) 부위 box 가 겹치면 `sorted()` 순서상 **뒤 이름이 이긴다**(좌표도 소유권도).
   결정론이지만 사전순이라 의미 있는 우선순위가 아니다.

7) 배경 배제는 `source_garment_mask` 가 주어질 때만 보장된다. 없으면 원본 box 안의
   배경 픽셀이 샘플될 수 있다.

8) 부위와 몸통의 경계는 feather 되지 않는다 — 두 사상이 만나는 자리에 이음매가 남을 수
   있다. feather 대상은 실루엣 경계와 carrier 배제 구멍뿐이다.

9) 부위 box 가 실제 부위를 정확히 가리킨다는 보장은 이 모듈 밖에 있다. box 가 틀리면
   틀린 자리에 정확히 옮긴다.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import cv2
import numpy as np

from .color import bgr_to_lab, lab_to_bgr
from .panel_map import PanelMap
from .warp_composite import (
    MIN_DECAL_SCALE, SHADING_SIGMA_MAX_FRAC, SHADING_SIGMA_MIN_FRAC,
    _decal_source_eligible, _homography_validity, _quad_area)

#: v4 — 양쪽 component box 가 모두 주어지면 배제된 부위를 **원본 부위 픽셀로 채운다**
#: (부위별 box→box homography). v3 — 구조 부위 입출력 배제가 추가됐다(같은 입력이라도 component box 유무로 픽셀이
#: 달라진다). v2 — 조명 분해가 바뀌었다. 기본 모드가 high-pass split 이 되었고 legacy
#: `carrier_low_freq_l` 의 sigma 도 밴드 하한에서 기하평균으로 옮겼다. 렌더 결과가
#: 달라지므로 버전을 올린다(같은 버전으로 다른 픽셀을 내면 replay 가 거짓말이 된다).
DIRECT_TORSO_VERSION = "direct_torso_texture_transfer_v4"

#: 원본 휘도를 그대로 옮긴다 — 원본 사진의 조명이 함께 온다. **진단용**.
SHADING_RAW_SOURCE = "raw_source"
#: 주기 경로의 식을 그대로 옮긴 것(`L - mean(L) + blur(L_carrier)`). **진단용**.
#: 주기 경로에서는 옳다 — 거기서 `pattern_lab` 은 합성된 프로파일이라 조명이 애초에
#: 없고, 스칼라 평균이 저주파의 전부다. 원본 픽셀에는 그 전제가 없어서, 스칼라를 빼도
#: 원본 사진의 저주파 조명장은 **그대로 남는다**(실측: 원본 조명 상관 0.975 → 0.975,
#: raw 와 동일). 두 조명이 반대면 서로 상쇄돼 옷이 평평해지고(carrier 상관 0.013),
#: 같은 방향이면 더해져 clipping 으로 패턴 기하까지 무너진다(라벨 일치 0.867).
SHADING_CARRIER_LOW_FREQ_L = "carrier_low_freq_l"
#: 기본값. 같은 GaussianBlur 연산자를 **양쪽에 대칭으로** 건다:
#:   L_out = (L_src - lowpass(L_src)) + lowpass(L_carrier)
#: 원본의 고주파(패턴·직조·디테일)만 남기고 원본 조명은 버린 뒤, carrier 의 주름·음영을
#: 얹는다. 새 relighting 모델이 아니라 이미 쓰던 연산자의 대칭 적용이다.
SHADING_SOURCE_HIGHFREQ_CARRIER_LOWFREQ = "source_highfreq_carrier_lowfreq"

#: 주기가 없으므로 주기 비례 sigma 를 쓸 수 없다. 주기 경로가 이미 정의해 둔 밴드
#: [1.8%, 8%] 의 **기하평균**을 쓴다 — 하한(고주파 누출 방지)과 상한(주름을 저주파에
#: 남김) 사이, 어느 쪽 끝에도 붙지 않는 지점이고 두 상수 모두 코드에 이미 있다.
#: 실측 스윕: frac 0.03~0.08 구간에서 고주파 대비 보존 ≥0.916(raw 0.920 대비 손실 <0.5%),
#: 원본 조명 잔여 상관 ≤0.551(기존 두 모드 0.975). 이 픽스처에서 고른 값이 아니라
#: 밴드 안이면 어디든 성립한다는 뜻이다.
_SHADING_SIGMA_FRAC = float(np.sqrt(SHADING_SIGMA_MIN_FRAC * SHADING_SIGMA_MAX_FRAC))

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


def _masked_lowpass(channel: np.ndarray, mask: np.ndarray, sigma: float) -> np.ndarray:
    """정규화 합성곱 저역통과 — mask 밖 픽셀이 저주파에 새지 않는다.

    warp 된 원본은 quad 밖이 0 이다. 그냥 blur 하면 그 0 이 옷 안쪽으로 번져 가장자리에
    가짜 어두움을 만들고, 그것이 그대로 조명으로 취급된다. blur(L·m)/blur(m) 는 같은
    GaussianBlur 로 그 편향을 없앤다.
    """
    m = (mask > 0).astype(np.float32)
    num = cv2.GaussianBlur(channel.astype(np.float32) * m, (0, 0), sigmaX=sigma)
    den = cv2.GaussianBlur(m, (0, 0), sigmaX=sigma)
    return num / np.maximum(den, 1e-6)


def _project(mat: np.ndarray, grid: np.ndarray, shape) -> tuple[np.ndarray, np.ndarray,
                                                                np.ndarray]:
    """동차 좌표 나눗셈 — → (x, y, 유효). 부호를 보존한다.

    `np.maximum(w, eps)` 로 클램프하면 w 가 **음수**일 때 1e-9 로 바뀌어 좌표가 1e12
    규모로 폭발한다(실측: placket homography 는 박스 전역에서 w<0 이라 전 픽셀이 그렇게
    됐다). 투영 나눗셈은 부호가 의미를 갖는다 — 크기만 막고 부호는 살린 뒤, |w| 가
    너무 작은 곳은 **유효하지 않다**고 표시한다.
    """
    out = mat @ grid
    wz = out[2]
    valid = np.abs(wz) > 1e-9
    safe = np.where(valid, wz, 1.0)
    x = (out[0] / safe).reshape(shape).astype(np.float32)
    y = (out[1] / safe).reshape(shape).astype(np.float32)
    return x, y, valid.reshape(shape)


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


def _array_sha(arr: np.ndarray) -> str:
    """배열 해시 — **shape 와 dtype 을 포함한다.**

    바이트 스트림만 해싱하면 (120,160,3) 과 (160,120,3) 이 같은 해시를 갖는다. 두
    입력은 서로 다른 그림을 내므로(실측 6,373 픽셀 차이) replay 가 구분하지 못한다.
    """
    a = np.ascontiguousarray(arr)
    h = hashlib.sha256()
    h.update(str(a.shape).encode())
    h.update(str(a.dtype).encode())
    h.update(a.tobytes())
    return h.hexdigest()[:16]


def _boxes_provenance(boxes: dict | None) -> dict:
    """부위 박스 기록 — 사람이 읽을 반올림 좌표 + 정확한 float 바이트 해시."""
    out = {}
    for name, quad in sorted((boxes or {}).items()):
        arr = np.ascontiguousarray(np.asarray(quad, np.float64))
        out[name] = {
            "quad": [[round(float(x), 3), round(float(y), 3)] for x, y in arr],
            "sha256": hashlib.sha256(arr.tobytes()).hexdigest()[:16],
        }
    return out


#: 모든 부위가 **같은 키 집합**을 낸다. 없는 키는 0/None 으로 채운다 — 키가 빠지면
#: 소비자가 "시도 안 함"과 "0 픽셀"을 구분하려고 스키마를 추측하게 된다.
_COMPONENT_METRIC_KEYS = ("filled", "px", "reason", "sourceAreaPx2", "targetAreaPx2",
                          "targetMaskPx")


def _component_metric(info: dict, name: str, order: list, owner: np.ndarray,
                      paint: np.ndarray) -> dict:
    """부위 지표 — 최종 paint 와 **픽셀 소유권**으로 확정한다."""
    out = {k: info.get(k) for k in _COMPONENT_METRIC_KEYS}
    if name in order:
        mine = paint & (owner == order.index(name))
        out["px"] = int(mine.sum())
        out["filled"] = bool(mine.any())
    else:
        out["px"] = 0
        out["filled"] = False
    return out


def _polygon_centroid(poly: np.ndarray) -> np.ndarray:
    """다각형 무게중심(모멘트). 면적이 0 이면 꼭짓점 평균으로 물러난다."""
    m = cv2.moments(poly.astype(np.float32))
    if abs(m["m00"]) < 1e-9:
        return poly.mean(axis=0).astype(np.float64)
    return np.array([m["m10"] / m["m00"], m["m01"] / m["m00"]], np.float64)


def _component_placement(H, source_boxes, carrier_boxes, target_quad, shape) -> dict:
    """원본 부위 박스를 몸통 사상으로 옮겼을 때 carrier 부위 박스와 얼마나 겹치는가.

    IoU 와 중심 오차(몸통 폭 대비)를 남긴다. 이 값이 낮다는 것이 "부위는 이 사상으로
    옮길 수 없다" 의 증거이고, 배제 결정의 근거다.
    """
    if not source_boxes or not carrier_boxes:
        return {}
    torso_w = max(float(np.linalg.norm(target_quad[1] - target_quad[0])), 1e-6)
    out = {}
    for name in sorted(set(source_boxes) & set(carrier_boxes)):
        src = np.asarray(source_boxes[name], np.float32).reshape(-1, 1, 2)
        car = np.asarray(carrier_boxes[name], np.float32)
        mapped = cv2.perspectiveTransform(src, H).reshape(-1, 2)
        m1 = np.zeros(shape, np.uint8)
        m2 = np.zeros(shape, np.uint8)
        cv2.fillPoly(m1, [mapped.astype(np.int32)], 1)
        cv2.fillPoly(m2, [car.astype(np.int32)], 1)
        union = int((m1 | m2).sum())
        # 꼭짓점 평균은 평행사변형이 아니면 무게중심이 아니다 — 모멘트로 진짜 중심을 쓴다.
        offset = float(np.linalg.norm(_polygon_centroid(mapped) - _polygon_centroid(car)))
        out[name] = {
            "iou": round(float(int((m1 & m2).sum()) / max(union, 1)), 4),
            "centroidOffsetPx": round(offset, 1),
            "centroidOffsetFracTorsoWidth": round(offset / torso_w, 4),
        }
    return out


def transfer_torso_texture(
    carrier_bgr: np.ndarray,
    panel_map: PanelMap,
    source_bgr: np.ndarray,
    *,
    source_landmarks,
    source_garment_mask: np.ndarray | None = None,
    carrier_component_boxes: dict | None = None,
    source_component_boxes: dict | None = None,
    shading: str = SHADING_SOURCE_HIGHFREQ_CARRIER_LOWFREQ,
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
    validity = _homography_validity(H, bw, bh, _quad_area(sq),
                                    origin=(float(sq[:, 0].min()), float(sq[:, 1].min())),
                                    quad=sq)
    if validity["neg_jacobian"] > 0:
        return DirectTorsoUnavailable(_REASON_HOMOGRAPHY, "사상 방향 반전", dict(validity))
    density, upscale, minification = _sampling_density(H, sq)

    # ── target 몸통 픽셀 → 원본 좌표 ──────────────────────────────────────
    gx, gy = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    grid = np.stack([gx.ravel(), gy.ravel(), np.ones(gx.size, np.float32)], axis=0)
    map_x, map_y, map_valid = _project(Hinv, grid, (h, w))

    torso_region = np.zeros((h, w), np.uint8)
    cv2.fillPoly(torso_region, [tq.astype(np.int32)], 255)
    region = (torso_region > 0) & (panel_map.garment_mask > 0)
    # carrier 쪽 구조 부위는 이 전송이 소유하지 않는다 — 주기 경로가 component 를 별도
    # decal 로 다루는 것과 같은 규율이다. 여기서 덮으면 carrier 의 실제 플래킷이 원단으로
    # 사라진다.
    structure_target = np.zeros((h, w), np.uint8)
    for _name, box in (carrier_component_boxes or {}).items():
        cv2.fillPoly(structure_target, [np.asarray(box, np.int32)], 255)
    # carrier 쪽 배제는 **픽셀을 빼기만** 해야 한다. 조명 추정 support 에까지 구멍을
    # 내면 target 쪽 결정이 멀리 떨어진 픽셀의 색을 바꾼다(실측: 박스 밖 44,745 픽셀이
    # 최대 40px 거리에서 변함). 그래서 조명 support 는 배제 **전** 영역으로 잡고,
    # 배제는 마지막에 paint 에서만 뺀다.
    region_before_structure = region.copy()

    # 옷 밖으로 조금 나가도 배경색이 옷 위에 실리는 일이 구조적으로 불가능해진다.
    # ── 부위 채우기 — 부위별 box→box 사상 ─────────────────────────────────
    # 같은 이름의 부위끼리 사상하므로 정렬은 정의상 성립한다. 몸통 사상이 부위를
    # 못 맞추는 것(한계 6)과 별개의 문제다.
    component_owned = np.zeros((h, w), bool)
    component_report: dict = {}
    # 픽셀별 **소유자**. 겹치면 뒤 이름이 좌표를 덮으므로 소유권도 함께 넘어가야 한다.
    # 시도 시점의 마스크를 그대로 쓰면 가려진 부위가 자기 몫이 아닌 픽셀을 자기 것으로
    # 보고한다(실측: a_box 가 12,261px 를 보고했지만 화면은 전부 b_box 였다).
    component_owner = np.full((h, w), -1, np.int16)
    component_order: list = []
    shared = sorted(set(source_component_boxes or {}) & set(carrier_component_boxes or {}))
    for name in shared:
        s_box = np.asarray(source_component_boxes[name], np.float32)
        c_box = np.asarray(carrier_component_boxes[name], np.float32)
        ok, why = _decal_source_eligible(s_box, c_box)
        if not ok:
            component_report[name] = {"filled": False, "reason": why}
            continue
        try:
            Hc = cv2.getPerspectiveTransform(s_box, c_box)
            Hc_inv = np.linalg.inv(Hc)
        except (cv2.error, np.linalg.LinAlgError) as exc:
            component_report[name] = {"filled": False, "reason": type(exc).__name__}
            continue
        c_region = np.zeros((h, w), np.uint8)
        cv2.fillPoly(c_region, [c_box.astype(np.int32)], 255)
        c_sel = (c_region > 0) & (panel_map.garment_mask > 0)
        if not c_sel.any():
            component_report[name] = {"filled": False, "reason": "target_mask_empty"}
            continue
        cx, cy, c_valid = _project(Hc_inv, grid, (h, w))
        c_in = (c_valid & (cx >= 0) & (cx <= sw - 1) & (cy >= 0) & (cy <= sh - 1))
        c_paint = c_sel & c_in
        if not c_paint.any():
            component_report[name] = {"filled": False, "reason": "out_of_source"}
            continue
        map_x = np.where(c_paint, cx, map_x)
        map_y = np.where(c_paint, cy, map_y)
        # 좌표를 바꿨으면 **유효성도 함께** 바꿔야 한다. 몸통 사상의 map_valid 를 그대로
        # 두면 부위 픽셀이 몸통 homography 의 지평선에 걸려 사라진다(실측: 한 행 81 px).
        map_valid = np.where(c_paint, c_valid, map_valid)
        component_owned |= c_paint
        component_owner = np.where(c_paint, len(component_order), component_owner)
        component_order.append(name)
        component_report[name] = {
            "sourceAreaPx2": round(_quad_area(s_box), 1),
            "targetAreaPx2": round(_quad_area(c_box), 1),
            "targetMaskPx": int(c_sel.sum()),
        }
    # 부위가 채워지는 픽셀은 몸통 배제 대상에서 되돌린다 — carrier 에 남길 이유가 없다.
    # **paint 를 만들기 전에** 합쳐야 한다(뒤에서 합치면 아무 효과가 없다).
    region = region | component_owned
    region_before_structure = region_before_structure | component_owned

    structure_excluded_intent = int((region & (structure_target > 0)).sum())
    in_bounds = (map_valid & (map_x >= 0) & (map_x <= sw - 1)
                 & (map_y >= 0) & (map_y <= sh - 1))
    paint = region_before_structure & in_bounds
    # 원본 garment mask 가 있으면 **배경을 샘플한 픽셀은 칠하지 않는다**. quad 모서리가
    # 원본 쪽 구조 부위도 **읽지 않는다**. 읽으면 그 단추·박음선이 몸통 아무 데나 찍힌다
    # (IoU 0.0 — 한계 5). 원본 근거가 없는 target 픽셀은 carrier 가 계속 소유한다.
    structure_rejected = 0
    if source_component_boxes:
        src_structure = np.zeros((sh, sw), np.uint8)
        for _name, box in source_component_boxes.items():
            cv2.fillPoly(src_structure, [np.asarray(box, np.int32)], 255)
        # **INTER_LINEAR** 로 마스크를 뽑는다. 픽셀은 bilinear 로 읽히므로 최근접 표본이
        # 박스 밖이어도 이웃한 구조 픽셀이 섞여 들어온다(실측: 실자산에서 349 픽셀,
        # 구조 기여 최대 127/255). 마스크를 같은 보간으로 읽고 **0 이 아니면 전부** 버리면
        # 기여가 조금이라도 있는 픽셀이 남을 수 없다 — 샘플러와 가드가 같은 커널을 쓴다.
        sampled_structure = cv2.remap(src_structure.astype(np.float32), map_x, map_y,
                                      interpolation=cv2.INTER_LINEAR,
                                      borderMode=cv2.BORDER_CONSTANT)
        # 부위가 소유한 픽셀은 예외다 — 거기서는 구조가 정답이다.
        torso_sampled = paint & ~component_owned
        structure_rejected = int((torso_sampled & (sampled_structure > 0.0)).sum())
        paint &= (sampled_structure <= 0.0) | component_owned

    background_rejected = 0
    if source_garment_mask is not None:
        # 구조 가드와 **같은 이유로** bilinear 로 읽는다. 최근접이 옷 안이어도 이웃한
        # 배경 픽셀이 섞여 들어온다(실측: 812 픽셀에 배경 기여). 마스크를 같은 커널로
        # 읽고 **완전히 옷 안인 픽셀만** 남긴다.
        smask = cv2.remap((source_garment_mask > 0).astype(np.float32), map_x, map_y,
                          interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
        fully_inside = smask >= 1.0 - 1e-6
        background_rejected = int((paint & ~fully_inside).sum())
        paint &= fully_inside
    # 조명 support = 원본이 뒷받침하는 몸통 전체(carrier 구조 배제 전). 실제로 칠하는
    # 것은 그중 carrier 구조 밖뿐이다.
    illum_support = paint.copy()
    paint = paint & ((structure_target == 0) | component_owned)
    if not paint.any():
        return DirectTorsoUnavailable(
            _REASON_NO_PIXELS, "원본으로 뒷받침되는 몸통 픽셀 0",
            {"torsoRegionPx": int(region.sum()),
             "outOfSourceFrac": round(float((region & ~in_bounds).sum())
                                      / max(1, int(region.sum())), 4)})

    band_px = max(1.0, float(panel_map.metrics.get("boundary_band_px", 4)))
    warped_bgr = cv2.remap(source_bgr, map_x, map_y, interpolation=cv2.INTER_LINEAR,
                           borderMode=cv2.BORDER_CONSTANT)
    source_lab = bgr_to_lab(warped_bgr)
    carrier_lab = bgr_to_lab(carrier_bgr)

    sigma = float(min(h, w)) * _SHADING_SIGMA_FRAC
    out_lab = source_lab.copy()
    # 원본 저주파는 어느 모드에서도 **관측**한다 — split 이 지운 것이 조명이었는지
    # 원단이었는지는 이 값 없이는 사후에 판단할 수 없다.
    source_low_l = _masked_lowpass(source_lab[..., 0], illum_support, sigma)
    carrier_low_l = cv2.GaussianBlur(carrier_lab[..., 0], (0, 0), sigmaX=sigma)
    recombined_l = None
    if shading == SHADING_SOURCE_HIGHFREQ_CARRIER_LOWFREQ:
        # 원본 고주파(패턴·직조) + carrier 저주파(주름·음영). 같은 연산자를 양쪽에.
        recombined_l = source_lab[..., 0] - source_low_l + carrier_low_l
    elif shading == SHADING_CARRIER_LOW_FREQ_L:
        # split 과 같은 원리 — 원본 조명 통계는 **원본이 뒷받침하는 영역**의 성질이지
        # target 쪽에서 무엇을 배제했느냐의 함수가 아니다. paint 로 재면 carrier box 를
        # 하나 추가한 것만으로 스칼라가 움직여 몸통 전체 색이 바뀐다(실측: 49,976 픽셀,
        # 최대 202px 거리).
        src_mean_l = float(source_lab[..., 0][illum_support].mean())
        recombined_l = source_lab[..., 0] - src_mean_l + carrier_low_l
    elif shading != SHADING_RAW_SOURCE:
        return DirectTorsoUnavailable("unknown_shading_mode", str(shading)[:60])
    clipped_frac = 0.0
    if recombined_l is not None:
        clipped_frac = float(((recombined_l > 100.0) | (recombined_l < 0.0))[paint].mean())
        out_lab[..., 0] = np.clip(recombined_l, 0.0, 100.0)

    # ── feather — 실루엣 밴드와 painted 내부 계면을 각각 만들어 최솟값 ─────
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

    # 커버리지 분모 — 몸통과 부위를 각각 자기 분모로 잰다. 합치면 부위가 몸통의
    # 낮은 커버리지를 가린다(실측: 합산 0.8711 인데 실제 몸통은 0.3607).
    torso_paintable = (region_before_structure & (torso_region > 0)
                       & ((structure_target == 0) | component_owned))
    component_target_px = int(sum(
        info.get('targetMaskPx') or 0 for info in component_report.values()))

    # 두 고주파 산포는 **같은 support** 로 재야 비교가 성립한다. 하나는 illum_support,
    # 다른 하나는 paint 로 재면 raw_source 처럼 출력이 원본과 동일한 경우에도 retention
    # 이 1.0 이 아니게 나온다(실측 0.2743). 둘 다 illum_support 로 통일하고 측정 영역만
    # paint 로 좁힌다.
    source_high_std = float((source_lab[..., 0] - source_low_l)[paint].std())
    out_low_l = _masked_lowpass(out_lab[..., 0], illum_support, sigma)
    output_high_std = float((out_lab[..., 0] - out_low_l)[paint].std())

    # 측정만 한다 — 이 phase 에서 carrier chroma 로 원본 색을 끌어당기지 않는다.
    # 원본 색 진실이 이 모드의 존재 이유이고, 보정은 승격 논의 때 별도로 판단한다.
    cast = (np.median(carrier_lab[..., 1:3][paint], axis=0)
            - np.median(source_lab[..., 1:3][paint], axis=0))
    metrics = {
        "shadingMode": shading,
        "torsoRegionPx": int(region.sum()),
        "paintedPx": int(paint.sum()),
        # 두 분모를 모두 남긴다. 배제 후 영역 기준(=실제로 칠할 수 있었던 곳 중 얼마나
        # 칠했나)과 원래 몸통 기준(=몸통 전체 중 얼마가 원본에서 왔나)은 다른 질문이다.
        # 몸통과 부위는 **다른 질문**이다. 합쳐서 재면 부위가 몸통의 낮은 커버리지를
        # 가린다(실측: 합산 0.8711 인데 실제 몸통은 0.3607).
        "torsoCoverage": round(float((paint & torso_paintable).sum())
                               / max(1, int(torso_paintable.sum())), 4),
        "torsoCoverageOfFullTorso": round(
            float((paint & (torso_region > 0)).sum())
            / max(1, int((region & (torso_region > 0)).sum())), 4),
        "componentTargetPx": int(component_target_px),
        "componentCoverage": (round(float((paint & component_owned).sum())
                                    / component_target_px, 4)
                              if component_target_px else None),
        "outOfSourceFrac": round(
            float((region & ~in_bounds).sum()) / max(1, int(region.sum())), 4),
        "backgroundRejectedPx": background_rejected,
        # 실제로 **칠하지 않은** 픽셀만 배제다. 부위가 채운 자리를 배제로 세면
        # 지표가 거짓말이 된다(실측: 28,673 배제 보고, 실제 미착색 0).
        "structureExcludedPx": int(
            (region_before_structure & (structure_target > 0) & ~paint).sum()),
        "structureExcludedIntentPx": structure_excluded_intent,
        "componentFilledPx": int((paint & component_owned).sum()),
        # 최종 paint 기준으로 확정한다 — 후속 guard(배경·구조·마스크)가 지운 픽셀을
        # "채웠다"고 보고하면 지표가 거짓말이 된다(실측: px=28673 인데 실제 0).
        "componentFill": {
            name: _component_metric(info, name, component_order, component_owner, paint)
            for name, info in component_report.items()},
        # 부위 중 몸통 quad 밖에 있는 픽셀 — 계약상 이 모듈이 몸통만 다루지 않는다는 사실.
        "componentPxOutsideTorsoQuad": int(
            (paint & component_owned & (torso_region == 0)).sum()),
        "sourceStructureRejectedPx": structure_rejected,
        # 부위가 사상 아래에서 얼마나 어긋나는가 — 배제의 근거이자, 나중에 정렬이
        # 좋아졌는지 판단할 같은 자다.
        "componentPlacement": _component_placement(
            H, source_component_boxes, carrier_component_boxes, tq, (h, w)),
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
        # 조명 분해가 실제로 무슨 일을 했는지 replay 없이 읽을 수 있게 남긴다. 참조
        # 조명장은 런타임에 없으므로 상관이 아니라 각 성분의 산포를 기록한다.
        # 측정 지점이 둘로 나뉜다:
        #   · source*/carrier* 는 **입력 성분** 측정(분해에 들어간 재료)
        #   · output*/clipped* 는 **재결합 직후의 Lab L** 측정 — alpha 합성·gamut 변환
        #     전이라 반환 이미지의 값과는 다를 수 있고, 여기서 보려는 것은 분해의 효과다.
        "shadingSigmaPx": round(sigma, 2),
        # 원본이 애초에 가지고 있던 저주파 구조. 크면 split 이 지운 것이 조명이 아니라
        # 원단(색블록·옴브레·큰 그래픽)일 수 있다는 신호다 — 한계 3) 참조.
        "sourceLowFreqStdL": round(float(source_low_l[paint].std()), 3),
        "carrierLowFreqStdL": round(float(carrier_low_l[paint].std()), 3),
        "sourceHighFreqStdL": round(float(source_high_std), 3),
        "outputHighFreqStdL": round(float(output_high_std), 3),
        "highFreqRetention": round(float(output_high_std / max(source_high_std, 1e-9)), 4),
        # [0,100] 밖으로 나가 잘린 비율. 밝은 carrier + 고대비 원본에서 커진다.
        "clippedFracL": round(clipped_frac, 4),
        **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in validity.items()},
    }
    provenance = {
        "version": DIRECT_TORSO_VERSION,
        # 호출자가 안 주면 **실제 배열에서 계산한다**. None 으로 두면 원본을 다시 칠해도
        # provenance 가 같아서 replay 가 두 결과를 구분하지 못한다(실측 28,673 픽셀).
        "sourceSha256": source_sha256 or _array_sha(source_bgr),
        "carrierSha256": carrier_sha256 or _array_sha(carrier_bgr),
        "sourceQuad": [[round(float(x), 3), round(float(y), 3)] for x, y in sq],
        "targetQuad": [[round(float(x), 3), round(float(y), 3)] for x, y in tq],
        "homography": [[round(float(v), 9) for v in row] for row in H],
        "interpolation": "INTER_LINEAR",
        "sourceMaskInterpolation": "INTER_LINEAR",   # 샘플러와 같은 커널(가드 계약)
        "garmentMaskSha256": _array_sha(panel_map.garment_mask),
        # 렌더를 바꿀 수 있는 입력은 전부 여기에 있어야 한다. 없으면 같은 provenance 로
        # 다른 픽셀이 나온다(실측: source mask 만 바꿔 3,476 픽셀, band_px 만 바꿔 9,984
        # 픽셀이 달라졌는데 기록은 동일했다).
        "sourceGarmentMaskSha256": (
            _array_sha((source_garment_mask > 0).astype(np.uint8))
            if source_garment_mask is not None else None),
        "boundaryBandPx": round(float(band_px), 4),
        "componentHomographies": {
            k: [[round(float(v), 9) for v in row]
                for row in cv2.getPerspectiveTransform(
                    np.asarray(source_component_boxes[k], np.float32),
                    np.asarray(carrier_component_boxes[k], np.float32))]
            for k in shared
            if _decal_source_eligible(
                np.asarray(source_component_boxes[k], np.float32),
                np.asarray(carrier_component_boxes[k], np.float32))[0]},
        "shadingMode": shading,
        "shadingSigmaShortSideFrac": _SHADING_SIGMA_FRAC,
        # 같은 버전·같은 provenance 로 다른 픽셀이 나오면 replay 가 거짓말이 된다.
        # component box 는 렌더 입력이므로 기록한다.
        # 읽기 쉬운 반올림 값과 **정확한 바이트 해시**를 함께 남긴다. 반올림만 남기면
        # 89.9996 과 90.0004 가 같은 기록이 되면서 래스터화는 달라진다(실측 1,218 픽셀).
        "carrierComponentBoxes": _boxes_provenance(carrier_component_boxes),
        "sourceComponentBoxes": _boxes_provenance(source_component_boxes),
        "periodInputs": None,        # 이 모드는 주기를 받지 않는다 — 계약의 일부다
    }
    return DirectTorsoCandidate(image_bgr=out_bgr, alpha=alpha, painted=painted,
                                metrics=metrics, provenance=provenance)
