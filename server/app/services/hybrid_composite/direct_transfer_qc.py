"""direct torso/component transfer 전용 결정론 QC — **관측만**, 임계 없음.

왜 별도인가
-----------
주기 경로의 QC 는 `target_period_px` 를 진실로 놓고 결과 주기를 되잰다. 이 모드에는
주기가 없다(그것이 존재 이유다). 그래서 그 QC 를 빌려 쓰면 없는 진실로 채점하게 된다.

이 모드가 실제로 약속하는 것은 하나다:

    SOURCE_PIXEL_GEOMETRY_PRESERVED_UNDER_MAPPING

그러므로 검사도 거기에 맞춰야 한다 — 원본 근거, 사상 유효성, 표본 밀도, 마스크 포함,
방향 보존, 색 충실도, 부위 일관성. 전부 사상과 원본에서 유도되지 주기에서 오지 않는다.

임계를 아직 두지 않는 이유
--------------------------
픽스처 하나로 만든 임계는 그 픽스처를 통과시키는 장치일 뿐이다. 이 단계의 산출물은
**분포를 모을 수 있는 원시 측정치**이고, `decision` 은 언제나 `unthresholded` 다.
이 값들이 여러 자산에서 어떻게 흩어지는지 본 뒤에야 합격선을 말할 수 있다.

신뢰 경계가 핵심이다
--------------------
v1 은 후보가 기록한 homography 를 믿었다 → 위조된 행렬이 통과했다.
v2 는 후보가 기록한 quad 를 믿었다 → quad 를 재배열하고 그에 맞춰 좌우 반전해 렌더하면
281,600 픽셀 전부가 틀렸는데도 "완벽"으로 채점됐다.
두 번 다 신뢰를 한 칸 옮겼을 뿐 없애지 못했다.

v3 의 규칙: **기대값은 호출자가 준 입력에서만 유도한다.**
  · 기하 → `source_landmarks` + `panel_map` (렌더러가 받은 것과 같은 입력)
  · 영역 → 그 기하로 QC 가 직접 만든다. `candidate.painted` 를 믿지 않는다
           (v2 는 믿었고, painted 의 절반을 지우면 틀린 140,800 픽셀이 숨었다).
  · provenance → 위 값들과 **대조**할 대상이지 진실의 출처가 아니다.

무엇을 못 잡는지도 분명히 해 둔다: QC 는 렌더러와 같은 순수 기하 헬퍼를 쓴다. 그 헬퍼가
틀리면 둘 다 같이 틀린다(공통 모드). 이 QC 는 **렌더 오류와 provenance 위조**를 잡지,
공유 알고리즘의 오류를 잡지 못한다.

핵심은 **독립 재구성**이다
--------------------------
집계 통계로는 이 약속을 검증할 수 없다. 실측(Codex 반증, 자체 재현):
  · 칠한 픽셀을 7px 밀면 60.9% 가 달라지는데 전역 방향각도 색 중앙값도 0.0 그대로였다.
  · 칠한 속을 단색 회색으로 덮으면(텍스처 0) 방향 검사가 여전히 "계산 가능, 오차 0.0"
    을 냈다 — 일관성 0.1855 는 전부 마스크 **테두리**의 gradient 였다.
  · 모든 칠한 픽셀에 +20 L 을 더해도(281,600/281,600 변경) 상관은 0.9969 로 **오히려
    올라갔다**. 상관은 스케일에 불변이다 — Phase A 에서 같은 이유로 진폭 지표로 바꿔
    놓고 여기서 다시 상관을 썼다.
평균·중앙값·전역 축·상관은 평행이동·위상·내용·진폭 오류에 눈이 멀었다.
그래서 v3 의 비교는 성분마다 **상관과 진폭과 오차**를 함께 낸다.

그래서 이 QC 는 후보의 숫자를 믿지 않고 **픽셀을 다시 만든다**: 호출자가 준
`source_landmarks`+`panel_map` 에서 기하를 세워 원본을 warp 하고, 그것과 후보를 대조한다.
그러면 평행이동·위상·내용이 전부 한 번에 잡힌다.

구역마다 약속이 다르다 — v4 의 핵심
------------------------------------
v3 는 칠한 영역 **전체**를 합성 전 warp 와 견줬다. 그런데 깃털 램프에서는 합성이
필수이므로 옳은 렌더가 반드시 warp 와 다르다. 그 필연적 차이를 오차로 세고, 그것을
없앤 하드 엣지를 개선으로 셌다. 매끈한 원단에서는 그 왜곡이 지표를 뒤집었다 —
옳은 렌더가 `L_highAmplitudeRatio` 62,135·`L_highCorr` 0.1607, **금지된** 하드 엣지가
1.0·1.0·0.0. 임계를 매길 수 없는 지표는 Phase D 의 목적을 배반한다.

그래서 화면을 호출자 기준 alpha 로 셋으로 나눈다:

  · alpha == 1  내부 → 합성이 항등. 내용 비교가 **유효한 유일한 구역**.
  · 0 < alpha < 1 램프 → 합성 관계를 **절대값**으로 검사(`blend`). 상관은 쓰지 않는다
    — 척도 불변이라 램프 변위를 1.5배로 키워도 0.8546 → 0.8621 로 좋아졌다.
  · alpha == 0 → carrier 그대로. `domain` 이 잰다.

램프의 **결정적** 지표는 `compositeResidual*` 하나다. 호출자가 준 `shading` 으로 칠할
값을 직접 만들어 `alpha*full + (1-alpha)*carrier` 와 픽셀 단위로 견준다. 기준이 하나여야
하는 이유: warp·carrier 두 기준과의 **거리만** 내면 그 사이 중간값이 양쪽 모두에서 이긴다
(실측: 6,444 램프 픽셀을 전부 틀리게 바꾼 그림이 네 지표 모두 좋아졌다). 그래서
`impliedL...VsWarp/VsCarrier` 는 **진단용**이다 — 어느 쪽에 가까운지 보여 줄 뿐이니
품질 게이트로 쓰면 안 된다.

`shading` 은 provenance 가 아니라 **호출자**에게서 온다(렌더러에 준 바로 그 값). 그래서
신뢰 경계는 그대로다. 다만 이 구역에 한해 오라클이 렌더러와 같은 조명 분해를 계산하므로
**조명 수학 자체의 버그는 램프에서 공통 모드**다. 그 구역이 이전에는 아예 무측정이었으므로
어느 쪽으로도 손해는 없다.

그 대가로 **주장이 좁아진다**: 재구성은 이제 내부 픽셀에 대해서만 말한다. 램프는
합성 관계라는 더 약한 보장만 받는다. 넓게 주장하고 틀리는 것보다 좁게 주장하고 맞는
편이 낫다 — 임계는 유효한 지표 위에만 세울 수 있다.

기대값의 출처도 함께 바뀌었다. 기대 alpha 는 `candidate.painted` 가 아니라 `allowed`
(호출자 입력만으로 만든 허용 영역)에서 나온다. painted 로 만들면 후보가 painted 를
지우는 순간 기대값도 같이 줄어 alpha 는 늘 완벽했다(실측: 10,000 px 구멍에 maxDelta
0.0, 비교 모집단은 281,600 → 271,600 으로 조용히 축소). `allowed` 는 32개 구성에서
렌더러의 paint 집합과 정확히 일치했다.

조명 모드가 있으므로 비교는 성분별로 한다:
  · a/b(색)   — Lab 안에서는 어떤 모드도 건드리지 않는다 → 픽셀 단위로 같아야 한다.
    **다만 정확히는 아니다.** 렌더러는 마지막에 uint8 BGR 로 돌아가고, 그 양자화는 L 에
    의존한다. 그래서 L 을 크게 바꾸는 모드에서는 되읽은 a/b 가 조금 흔들린다(실측:
    같은 구성에서 raw 모드 0.0 대 carrier-low-freq 모드 2.8284 — 중앙값과 P95 가 같은
    **상수 오프셋**이다). 결함이 아니라 파이프라인의 성질이고, 임계 단계는 이 지표에
    모드 의존 바닥이 있다는 것을 알고 선을 그어야 한다. 모드를 모델링하는
    `compositeResidual*` 은 같은 경우에 0.85/0.98/1.00 로 깨끗하다.
  · 고주파 L  — 조명 분해는 저주파만 바꾼다 → 상관 **과 진폭 과 RMSE** 로 본다.
  · 저주파 L  — 모드마다 출처가 다르다 → **두 기준 모두**와 잰다(원본, carrier).

마지막 줄이 늦게 잡힌 함정이다. 저역을 원본 하나로만 재면 기본 모드
(`source_highfreq_carrier_lowfreq`, 저역을 carrier 에서 가져오는 모드)에서 균일 오프셋
공격이 통과한다 — 실측으로 그 지표가 16.8 → 8.9 로 **좋아졌다**. 모드가 저역을 어디서
가져오든 한쪽 기준에서는 반드시 드러나게 두 값을 모두 낸다. 어느 쪽을 쓸지는 후보가
아니라 임계 단계가 정한다.

방향 검사는 남기되 **보조 지표**다. 줄 법선은 국소 Jacobian 의 역전치로 옮겨간다(주기
경로의 `_component_period_under_homography` 와 같은 사실)이고, 그 수학은 옳다. 다만
전역 축 하나로는 위 실패들을 못 잡는다 — 그것을 주장으로 삼았던 것이 v1 의 잘못이다.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field

import cv2
import numpy as np

from .color import bgr_to_lab, lab_to_bgr


def _safe_float(v) -> float | None:
    """후보가 적은 값을 숫자로 **시도만** 한다. 예외로 채점을 피할 수 없게."""
    try:
        f = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    return f if np.isfinite(f) else None


def _equal(a, b) -> bool:
    """형이 무엇이든 안전한 동등 비교. 배열이 섞여도 예외를 내지 않는다."""
    if isinstance(a, np.ndarray) or isinstance(b, np.ndarray):
        aa, bb = np.asarray(a), np.asarray(b)
        return bool(aa.shape == bb.shape and np.array_equal(aa, bb))
    try:
        return bool(a == b)
    except Exception:
        return False


def _as_mapping(v) -> dict:
    """`v or {}` 는 numpy 배열에서 모호한 진리값으로 터진다."""
    return dict(v) if isinstance(v, dict) else {}


def _same_count(n: int, reported) -> bool:
    """자기보고 값이 배열이면 `==` 이 배열을 낸다 — bool() 에서 터진다."""
    r = _safe_float(reported)
    return bool(r is not None and float(n) == r)


def _is_blank(v) -> bool:
    """`v in (None, "")` 는 numpy 배열에서 모호한 진리값으로 터진다."""
    if v is None:
        return True
    if isinstance(v, np.ndarray):
        return v.size == 0
    if isinstance(v, (str, bytes, list, tuple, dict, set)):
        return len(v) == 0
    return False

#: 고주파 밴드가 '있다'고 말할 수 있는 최소 진폭(L*). 합격선이 아니라 8-bit 양자화
#: 잡음 수준 — 이 아래에서는 비율·상관이 수학적으로 정의되지 않는다.
_HIGH_BAND_MIN_STD_L = 0.5

#: quad 는 소수 3자리로 기록된다 — 일치 판정의 허용오차는 그 직렬화 폭이다.
_QUAD_RECORD_EPS = 1e-3

DIRECT_TRANSFER_QC_VERSION = "direct_transfer_qc_v4"

#: 이 단계에서 판정은 없다. 값을 모으는 것이 목적이다.
DECISION_UNTHRESHOLDED = "unthresholded"

#: replay 에 반드시 있어야 하는 provenance 키. 하나라도 없으면 같은 그림을 다시 만들 수
#: 없다 — 그 자체가 관측 대상이다.
_REQUIRED_PROVENANCE = (
    "version", "sourceSha256", "carrierSha256", "sourceQuad", "targetQuad",
    "homography", "interpolation", "garmentMaskSha256", "boundaryBandPx",
    # 아래는 v1 에서 빠져 있었다 — 전부 픽셀을 바꾸는 입력인데 "완전함"으로 보고됐다.
    "sourceMaskInterpolation", "shadingMode", "shadingSigmaShortSideFrac",
    "carrierComponentBoxes", "sourceComponentBoxes", "componentHomographies",
    "sourceGarmentMaskSha256",
    # 이 모드의 존재 이유가 "주기가 없다" 이다. 그 선언이 빠지거나 값이 실려 있으면
    # 같은 provenance 로 다른 경로를 재현하게 된다.
    "periodInputs",
)
#: 값이 `None`/빈 dict 여도 **정상**인 키(부위가 없으면 빈 것이 맞다). 존재 여부만 본다.
_PRESENCE_ONLY = frozenset({
    "carrierComponentBoxes", "sourceComponentBoxes", "componentHomographies",
    "sourceGarmentMaskSha256", "periodInputs",
})


@dataclass(frozen=True)
class DirectTransferQC:
    checks: dict
    decision: str = DECISION_UNTHRESHOLDED
    version: str = DIRECT_TRANSFER_QC_VERSION
    notes: tuple = field(default_factory=tuple)


def _dominant_orientation(gray: np.ndarray, sel: np.ndarray,
                          radius: int = 2) -> tuple | None:
    """구조 텐서의 지배 방향과 일관성. → (unit(2,), coherence) | None.

    `warp_composite` 의 부위 방향 측정과 같은 수학(Sobel 구조 텐서)이다 — 새 추정기를
    만들지 않는다. 2θ 공간에서 평균해야 반대 부호의 법선이 상쇄되지 않는다.
    """
    # **테두리를 뺀다.** Sobel 은 마스크 경계에서 거대한 gradient 를 본다. 그것을 포함하면
    # 속이 완전히 평평한(텍스처 0) 그림도 "방향이 있다"고 보고한다(실측: 단색 회색 내부가
    # 일관성 0.1855, 오차 0.0). 안쪽으로 깎아 실제 원단만 남긴다.
    k = 2 * max(1, int(radius)) + 1
    inner = cv2.erode((sel > 0).astype(np.uint8),
                      np.ones((k, k), np.uint8), iterations=1) > 0
    if int(inner.sum()) < 256:
        return None
    sel = inner
    scale = max(1.0, float(np.abs(gray[sel]).mean()))
    gx = cv2.Sobel(gray.astype(np.float32), cv2.CV_32F, 1, 0, ksize=3) / scale
    gy = cv2.Sobel(gray.astype(np.float32), cv2.CV_32F, 0, 1, ksize=3) / scale
    # 텍스처가 실질적으로 없으면 방향은 정의되지 않는다 — 0 도라고 우기지 않는다.
    if float(np.hypot(gx[sel], gy[sel]).mean()) < 1e-3:
        return None
    jxx = float((gx[sel] * gx[sel]).mean())
    jyy = float((gy[sel] * gy[sel]).mean())
    jxy = float((gx[sel] * gy[sel]).mean())
    trace = jxx + jyy
    if trace < 1e-12:
        return None
    diff = jxx - jyy
    magnitude = float(np.hypot(diff, 2.0 * jxy))
    coherence = magnitude / trace
    theta = 0.5 * float(np.arctan2(2.0 * jxy, diff))       # 지배 gradient 방향
    return np.array([np.cos(theta), np.sin(theta)], np.float64), coherence


def _jacobian_at(H: np.ndarray, point: np.ndarray) -> np.ndarray | None:
    """target 점에서의 국소 Jacobian (source→target 사상 H 기준)."""
    p = np.asarray(point, np.float64)
    try:
        Hinv = np.linalg.inv(H)
    except np.linalg.LinAlgError:
        return None
    back = []
    for delta in ([0.0, 0.0], [1.0, 0.0], [0.0, 1.0]):
        v = Hinv @ np.array([p[0] + delta[0], p[1] + delta[1], 1.0])
        if abs(v[2]) < 1e-12:
            return None
        back.append(v[:2] / v[2])
    src0, sx, sy = back
    inv_j = np.column_stack([sx - src0, sy - src0])        # target→source
    if abs(np.linalg.det(inv_j)) < 1e-12:
        return None
    return np.linalg.inv(inv_j)                            # source→target


def _angle_between(a: np.ndarray, b: np.ndarray) -> float:
    """방향(부호 무시) 사이 각도, degree. 줄에는 앞뒤가 없다."""
    a = a / max(float(np.linalg.norm(a)), 1e-12)
    b = b / max(float(np.linalg.norm(b)), 1e-12)
    cos = abs(float(np.dot(a, b)))
    return float(np.degrees(np.arccos(min(1.0, max(0.0, cos)))))


def _caller_geometry(panel_map, source_landmarks, source_shape) -> tuple | None:
    """호출자 입력만으로 기하를 세운다 — 후보의 기록을 쓰지 않는다.

    렌더러가 받은 것과 **같은 입력**(source landmarks, panel_map)에서 같은 식으로
    quad 를 만든다. 그래야 후보가 자기 기하를 위조해도 QC 는 원래 기하로 채점한다.
    """
    from .direct_torso_transfer import torso_quad
    sh, sw = source_shape[:2]
    sq = torso_quad(source_landmarks, width=sw, height=sh)
    tq = None
    for panel in getattr(panel_map, "panels", ()):
        if panel.name == "torso":
            tq = np.asarray(panel.quad, np.float64)
            break
    if sq is None or tq is None:
        return None
    sq = np.asarray(sq, np.float64)
    tq = np.asarray(tq, np.float64)
    # 모양만 보면 NaN quad 와 영면적 quad 가 '계산 가능'으로 통과해 NaN 측정치를 낸다.
    for q in (sq, tq):
        if q.shape != (4, 2) or not np.isfinite(q).all() or _quad_area(q) < 1.0:
            return None
    return sq, tq


def _quad_area(q: np.ndarray) -> float:
    x, y = q[:, 0], q[:, 1]
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def _as_float_array(v) -> np.ndarray:
    """후보가 적은 것을 실수 배열로 **시도만** 한다. 못 하면 빈 배열.

    `prov.get(k) or []` 는 numpy 배열에서 모호한 진리값으로 터지고, `np.asarray("x",
    float)` 는 ValueError 를 던진다. 둘 다 후보가 채점을 예외로 없애는 통로였다.
    """
    if v is None:
        return np.empty(0, np.float64)
    try:
        a = np.asarray(v, np.float64)
    except (TypeError, ValueError, OverflowError):
        return np.empty(0, np.float64)
    return a


#: replay 근거로서 형이 맞아야 하는 키 → 검사식.
_PROV_SANE = {
    "version": lambda v: isinstance(v, str),
    "sourceSha256": lambda v: isinstance(v, str),
    "carrierSha256": lambda v: isinstance(v, str),
    "garmentMaskSha256": lambda v: isinstance(v, str),
    "interpolation": lambda v: isinstance(v, str),
    "sourceMaskInterpolation": lambda v: isinstance(v, str),
    "shadingMode": lambda v: isinstance(v, str),
    "boundaryBandPx": lambda v: _safe_float(v) is not None,
    "shadingSigmaShortSideFrac": lambda v: _safe_float(v) is not None,
    "sourceQuad": lambda v: _finite_shape(v, (4, 2)),
    "targetQuad": lambda v: _finite_shape(v, (4, 2)),
    "homography": lambda v: _finite_shape(v, (3, 3)),
    # 존재만 보던 키도 **형**은 봐야 한다. 값이 있는데 dict 가 아니면 replay 가 안 된다.
    "carrierComponentBoxes": lambda v: isinstance(v, dict),
    "sourceComponentBoxes": lambda v: isinstance(v, dict),
    "componentHomographies": lambda v: isinstance(v, dict),
    "sourceGarmentMaskSha256": lambda v: isinstance(v, str),
}


def _finite_shape(v, shape) -> bool:
    a = _as_float_array(v)
    return bool(a.shape == shape and np.isfinite(a).all())


def _provenance_vs_caller(prov, source_bgr, carrier_bgr, panel_map, source_mask,
                          carrier_boxes, source_boxes, shading,
                          source_sha256, carrier_sha256) -> list:
    """기록된 replay 근거를 **호출자가 아는 사실**과 대조한다. → 어긋난 키 이름들."""
    from .direct_torso_transfer import (DIRECT_TORSO_VERSION, _SHADING_SIGMA_FRAC,
                                        _array_sha, _boxes_provenance)
    bad = []

    def cmp(key, expected):
        # `!=` 는 numpy 배열에서 배열을 낸다 — bool() 에서 터진다. 같은 실수를 이미
        # `_is_blank`·`_same_count`·`_as_mapping` 에서 고쳐 놓고 여기서 또 만들었다.
        if key in prov and not _equal(prov.get(key), expected):
            bad.append(key)

    # 호출자가 원본 파일 계보를 직접 넘겼다면 렌더러는 **그것을** 기록한다. QC 가 늘
    # 배열 해시를 기대하면 정상 호출이 불일치로 신고된다(옳은 렌더가 complete=False).
    # 렌더러는 빈 문자열을 falsy 로 보고 **자기 해시를 쓴다**. QC 가 빈 문자열을
    # 기대하면 정상 호출이 불일치로 신고된다.
    cmp("sourceSha256", source_sha256 if source_sha256
        else _array_sha(np.asarray(source_bgr)))
    cmp("carrierSha256", carrier_sha256 if carrier_sha256
        else _array_sha(np.asarray(carrier_bgr)))
    # 렌더 결과를 바꾸는 나머지 선언들도 호출자가 아는 값과 맞아야 한다.
    cmp("shadingMode", shading)
    cmp("periodInputs", None)          # 직접 전송에는 주기 입력이 없다
    cmp("version", DIRECT_TORSO_VERSION)
    cmp("interpolation", "INTER_LINEAR")
    cmp("sourceMaskInterpolation", "INTER_LINEAR")
    if "componentHomographies" in prov:
        expected_h = {}
        from .direct_torso_transfer import _decal_source_eligible
        for name in sorted(set(carrier_boxes or {}) & set(source_boxes or {})):
            s_q = np.asarray(source_boxes[name], np.float32)
            c_q = np.asarray(carrier_boxes[name], np.float32)
            if s_q.shape != (4, 2) or c_q.shape != (4, 2):
                continue
            # 렌더러가 거부한 부위는 기록되지 않는다 — QC 가 기대하면 옳은 렌더가
            # 불일치로 신고된다(실측: short_side_10px 로 거부된 부위).
            try:
                ok, _why = _decal_source_eligible(s_q, c_q)
            except Exception:
                ok = False
            if not ok:
                continue
            try:
                h = cv2.getPerspectiveTransform(s_q, c_q)
            except cv2.error:
                continue
            expected_h[name] = [[round(float(v), 9) for v in row] for row in h]
        if not _equal(prov.get("componentHomographies"), expected_h):
            bad.append("componentHomographies")
    cmp("garmentMaskSha256", _array_sha(np.asarray(panel_map.garment_mask)))
    # 렌더러는 **이진화한** 마스크를 해싱한다 — 렌더에 영향을 주는 것이 그것뿐이기
    # 때문이다. 원본 배열을 해싱하면 옳은 렌더가 불일치로 신고된다.
    cmp("sourceGarmentMaskSha256",
        _array_sha((np.asarray(source_mask) > 0).astype(np.uint8))
        if source_mask is not None else None)
    cmp("carrierComponentBoxes", _boxes_provenance(carrier_boxes))
    cmp("sourceComponentBoxes", _boxes_provenance(source_boxes))
    # 렌더러는 `round(float(band_px), 4)` 로 기록한다. 정확 비교를 하면 4.123456 같은
    # 정당한 값이 옳은 렌더에서 불일치로 나온다.
    recorded_band = _safe_float(prov.get("boundaryBandPx"))
    if "boundaryBandPx" in prov and (recorded_band is None
                                     or recorded_band != round(_band_px(panel_map), 4)):
        bad.append("boundaryBandPx")
    if "shadingSigmaShortSideFrac" in prov and _safe_float(
            prov.get("shadingSigmaShortSideFrac")) != float(_SHADING_SIGMA_FRAC):
        bad.append("shadingSigmaShortSideFrac")
    return bad


def _prov_value_sane(key, value) -> bool:
    check = _PROV_SANE.get(key)
    if check is None:
        return True
    try:
        return bool(check(value))
    except Exception:
        return False


def _quad(prov, key) -> np.ndarray | None:
    q = _as_float_array(prov.get(key))
    return q if (q.shape == (4, 2) and np.isfinite(q).all()) else None


def _reconstruct_from_caller(source_bgr, shape, sq, tq, carrier_boxes, source_boxes,
                             source_garment_mask=None):
    """호출자 기하로 원본을 warp 한다. → dict  후보의 기록을 읽지 않는다.

    이미지만 warp 하면 **원본 근거가 없는 픽셀도 완벽하게 재구성된다**. 원본 garment
    mask 에 구멍이 있으면 렌더러는 거기를 칠하지 않는 것이 옳은데, 칠해 버린 후보를
    같은 좌표에서 비교하면 오차 0 이 나온다(실측: 10,000 px 이 abErrorMedian 0.0).
    그래서 마스크도 **같은 사상**으로 표본화해 '칠해도 되는 픽셀'을 함께 만든다.
    렌더러와 같은 커널(bilinear)·같은 문턱을 쓴다 — 가드와 샘플러가 어긋나면 그
    틈으로 배경이 샌다(Phase B 실측 349 px).

    부위도 **렌더러와 같은 규칙**으로만 설치한다: 자격 검사를 통과한 이름만, 그리고
    소유는 박스 전체가 아니라 **실제로 원본 안을 읽는 픽셀**뿐이다. v3 초안은 자격
    미달 부위의 사상까지 설치해, 렌더러가 거부한 부위로 다시 칠한 그림이 abErrorP95
    175.0 → 0.0 으로 좋아졌다.
    """
    from .direct_torso_transfer import _decal_source_eligible
    h, w = shape
    sh, sw = source_bgr.shape[:2]
    try:
        H = cv2.getPerspectiveTransform(sq.astype(np.float32), tq.astype(np.float32))
        maps = [(np.linalg.inv(H), None, None)]
    except (cv2.error, np.linalg.LinAlgError):
        return None
    for name in sorted(set(carrier_boxes or {}) & set(source_boxes or {})):
        s_q = np.asarray(source_boxes[name], np.float32)
        c_q = np.asarray(carrier_boxes[name], np.float32)
        if (s_q.shape != (4, 2) or c_q.shape != (4, 2)
                or not np.isfinite(s_q).all() or not np.isfinite(c_q).all()):
            continue
        try:
            ok, _why = _decal_source_eligible(s_q, c_q)
        except Exception:
            ok = False
        if not ok:                       # 렌더러가 거부한 부위는 여기서도 없는 것이다
            continue
        try:
            maps.append((np.linalg.inv(cv2.getPerspectiveTransform(s_q, c_q)), c_q,
                         name))
        except (cv2.error, np.linalg.LinAlgError):
            continue

    gx, gy = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    grid = np.stack([gx.ravel(), gy.ravel(), np.ones(gx.size, np.float32)])
    mx = np.zeros((h, w), np.float32); my = np.zeros((h, w), np.float32)
    valid = np.zeros((h, w), bool)
    owned = np.zeros((h, w), bool)
    owned_by: dict = {}
    order: list = []
    inv_by: dict = {}
    for inv, quad, name in maps:
        out = inv @ grid
        wz = out[2]
        ok = np.abs(wz) > 1e-9
        safe = np.where(ok, wz, 1.0)
        x = (out[0] / safe).reshape(h, w).astype(np.float32)
        y = (out[1] / safe).reshape(h, w).astype(np.float32)
        ok = ok.reshape(h, w) & (x >= 0) & (x <= sw - 1) & (y >= 0) & (y <= sh - 1)
        if quad is not None:
            ok &= _poly((h, w), quad)
        mx = np.where(ok, x, mx); my = np.where(ok, y, my); valid |= ok
        if quad is not None:
            # 소유는 박스 전체가 아니라 **실제로 읽히는 픽셀**이다. 박스가 원본 밖으로
            # 나가면 렌더러는 그만큼 못 채운다(실측: 5,511 채움에 QC 는 10,521 을 기대).
            owned |= ok
            owned_by[name] = ok
            inv_by[name] = inv
            order.append(name)
    # 겹치는 부위는 **뒤 이름이 좌표를 덮는다** — 소유도 함께 넘어가야 한다. 덮이기
    # 전의 마스크를 그대로 두면, 완전히 가려진 부위의 사상으로 정상 렌더가 막힌다
    # (실측: a 가 0 px 을 그렸는데 a 의 반사 때문에 보류됐다).
    for i, name in enumerate(order):
        later = np.zeros_like(owned_by[name])
        for other in order[i + 1:]:
            later |= owned_by[other]
        owned_by[name] = owned_by[name] & ~later
    warped = cv2.remap(source_bgr, mx, my, interpolation=cv2.INTER_LINEAR,
                       borderMode=cv2.BORDER_CONSTANT)
    out = {"warped": warped, "valid": valid, "componentOwned": owned,
           "componentOwnedBy": owned_by, "componentInverse": inv_by}
    # 원본 옷 안이 **완전히** 뒷받침하는 픽셀만 근거가 있다.
    if source_garment_mask is not None:
        sm = cv2.remap((source_garment_mask > 0).astype(np.float32), mx, my,
                       interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
        out["sourceBacked"] = sm >= 1.0 - 1e-6
    # 원본 쪽 구조를 조금이라도 읽은 픽셀은 몸통이 소유하면 안 된다.
    if source_boxes:
        struct = np.zeros((sh, sw), np.uint8)
        for box in source_boxes.values():
            q = np.asarray(box, np.float32)
            if q.shape == (4, 2) and np.isfinite(q).all():
                cv2.fillPoly(struct, [q.astype(np.int32)], 255)
        out["sourceStructure"] = cv2.remap(
            struct.astype(np.float32), mx, my, interpolation=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT) > 0.0
    return out


def _split_bands(plane: np.ndarray, sel: np.ndarray, sigma: float) -> tuple:
    """정규화 합성곱 저역통과. → (low, high)"""
    m = sel.astype(np.float32)
    num = cv2.GaussianBlur(plane.astype(np.float32) * m, (0, 0), sigmaX=sigma)
    den = cv2.GaussianBlur(m, (0, 0), sigmaX=sigma)
    low = num / np.maximum(den, 1e-6)
    return low, plane - low


def _band_stats(got: np.ndarray, ref: np.ndarray, sel: np.ndarray, sigma: float) -> dict:
    """한 성분의 상관 **과** 진폭 **과** 오차. 상관만으로는 균일 오프셋을 못 잡는다."""
    def split(plane):
        m = sel.astype(np.float32)
        num = cv2.GaussianBlur(plane.astype(np.float32) * m, (0, 0), sigmaX=sigma)
        den = cv2.GaussianBlur(m, (0, 0), sigmaX=sigma)
        low = num / np.maximum(den, 1e-6)
        return low, plane - low
    g_low, g_high = split(got)
    r_low, r_high = split(ref)
    a, b = g_high[sel] - g_high[sel].mean(), r_high[sel] - r_high[sel].mean()
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    r_std = float(r_high[sel].std())
    g_std = float(g_high[sel].std())
    # 기준에 고주파가 **없으면** 비율도 상관도 정의되지 않는다. 매끈한 원단이 실제로
    # 그렇다 — 0/0 이 잡음에 지배돼 옳은 렌더에 8,000~11,000 을 냈다(864 구성 중 144).
    # 없는 신호를 1.0 이라고 우기지도, 잘라 내지도 않는다. 정의 안 됨이라고 말하고
    # 원시 진폭을 함께 낸다 — 임계 단계가 스스로 판단할 수 있게.
    # 문턱 0.5 L 은 합격선이 아니라 8-bit 양자화 잡음의 크기다.
    # 밴드 분리는 정규화 합성곱이라 **공간적 지지**가 필요하다. 표본이 적으면 비율도
    # 상관도 의미가 없다 — 없는 것을 숫자로 만들지 않는다.
    defined = bool(r_std >= _HIGH_BAND_MIN_STD_L and int(sel.sum()) >= 256)
    return {
        "highBandDefined": bool(defined),
        "highRefStd": round(r_std, 4),
        "highGotStd": round(g_std, 4),
        "highCorr": (round(float(a @ b / denom), 4)
                     if (defined and denom > 1e-9) else None),
        "highAmplitudeRatio": (round(float(g_std / r_std), 4) if defined else None),
        "highRmse": round(float(np.sqrt(((g_high[sel] - r_high[sel]) ** 2).mean())), 4),
        "lowMeanAbsDelta": round(float(np.abs(g_low[sel] - r_low[sel]).mean()), 4),
        "lowMaxAbsDelta": round(float(np.abs(g_low[sel] - r_low[sel]).max()), 4),
    }




def _geometry_vs_caller(prov, caller_geom) -> dict:
    """기록된 quad·homography 를 **호출자 기하**와 대조한다.

    v2 는 기록된 quad 끼리의 자기일관성만 봤다. quad 를 재배열하고 그에 맞춰 사상을
    다시 구하면 좌우 반전 렌더가 완벽 점수를 받았다. 기준은 밖에 있어야 한다.
    """
    if caller_geom is None:
        return {"computable": False, "reason": "caller_geometry_unavailable"}
    sq_c, tq_c = caller_geom
    sq_p, tq_p = _quad(prov, "sourceQuad"), _quad(prov, "targetQuad")
    rec = _as_float_array(prov.get("homography"))
    out = {
        "computable": True,
        "sourceQuadMaxDeltaPx": (round(float(np.abs(sq_p - sq_c).max()), 4)
                                 if sq_p is not None else None),
        "targetQuadMaxDeltaPx": (round(float(np.abs(tq_p - tq_c).max()), 4)
                                 if tq_p is not None else None),
        # 허용오차는 **기록 폭**이다. 0.5 px 를 열어 주면 replay 가 다른 그림을 내는
        # 위조 quad 가 통과한다(실측: x 를 전부 +0.5 해도 일치로 봤다).
        "quadsMatchCaller": bool(sq_p is not None and tq_p is not None
                                 and np.allclose(sq_p, sq_c, atol=_QUAD_RECORD_EPS,
                                                 rtol=0.0)
                                 and np.allclose(tq_p, tq_c, atol=_QUAD_RECORD_EPS,
                                                 rtol=0.0)),
    }
    try:
        derived = cv2.getPerspectiveTransform(sq_c.astype(np.float32),
                                              tq_c.astype(np.float32))
    except cv2.error:
        out["recordedVsCallerMaxAbs"] = None
        return out
    # 특이/영정규화 행렬을 통과시키지 않는다(v2 는 all-zero H 에 NaN 을 냈다).
    ok = (rec.shape == (3, 3) and np.isfinite(rec).all()
          and abs(float(rec[2, 2])) > 1e-12
          and abs(float(np.linalg.det(rec))) > 1e-12)
    out["recordedHomographyUsable"] = bool(ok)
    out["recordedVsCallerMaxAbs"] = (
        round(float(np.abs(rec / rec[2, 2] - derived / derived[2, 2]).max()), 6)
        if ok else None)
    return out


def _poly(shape, quad) -> np.ndarray:
    m = np.zeros(shape, np.uint8)
    q = np.asarray(quad, np.float32)
    if q.shape == (4, 2) and np.isfinite(q).all():
        cv2.fillPoly(m, [q.astype(np.int32)], 255)
    return m > 0


def _negative_det_px(H: np.ndarray, quad, shape) -> int | None:
    """quad 안에서 야코비 행렬식이 양수가 **아닌** 픽셀 수. 해석적으로 센다.

    투영변환의 야코비 행렬식은 `det(H) / w^3` 이다(`w = H20·x + H21·y + H22`). 한 점에서
    유한차분으로 재면 그 한 걸음이 지평선(w=0)을 넘을 때 부호가 뒤집혀, 멀쩡한 얇은
    부위가 '거울상'으로 신고된다(실측: 874 px 전부 양수인데 중심 차분은 -0.018).
    반대로 한 점만 보면 **국소적으로** 뒤집힌 오목 사상을 놓친다(실측: 30,003 px 중
    11,022 px 이 음수인데 중심값은 양수였다).
    """
    mask = np.zeros(shape[:2], np.uint8)
    q = np.asarray(quad, np.float32)
    if q.shape != (4, 2) or not np.isfinite(q).all():
        return None
    cv2.fillPoly(mask, [q.astype(np.int32)], 255)
    ys, xs = np.nonzero(mask > 0)
    if xs.size == 0:
        return None
    w = (float(H[2, 0]) * xs + float(H[2, 1]) * ys + float(H[2, 2])).astype(np.float64)
    det_h = float(np.linalg.det(H))
    with np.errstate(divide="ignore", invalid="ignore"):
        det_j = det_h / (w ** 3)
    return int(np.count_nonzero(~(det_j > 0.0)))


def _caller_stretch(caller_geom) -> dict:
    """이방성·신장을 호출자 quad 에서 직접 잰다. 후보의 숫자를 쓰지 않는다."""
    if caller_geom is None:
        return {"callerAnisotropyP99": None, "callerStretchOverFrac": None}
    from .warp_composite import _homography_validity
    from .direct_torso_transfer import _quad_area
    sq, tq = caller_geom
    try:
        h = cv2.getPerspectiveTransform(sq.astype(np.float32), tq.astype(np.float32))
        bw = int(sq[:, 0].max() - sq[:, 0].min()) + 1
        bh = int(sq[:, 1].max() - sq[:, 1].min()) + 1
        v = _homography_validity(h, bw, bh, _quad_area(sq),
                                 origin=(float(sq[:, 0].min()), float(sq[:, 1].min())),
                                 quad=sq)
    except (cv2.error, np.linalg.LinAlgError, ValueError):
        return {"callerAnisotropyP99": None, "callerStretchOverFrac": None}
    return {"callerAnisotropyP99": round(float(v["anisotropy_p99"]), 4),
            "callerStretchOverFrac": round(float(v["stretch_over_frac"]), 4)}


def _negative_det_px_on_mask(h_inv: np.ndarray, mask: np.ndarray) -> int | None:
    """칠한 target 픽셀에서 야코비 행렬식 부호를 센다.

    역사상의 행렬식은 원사상의 역수이므로 **부호가 같다**. 그래서 target 쪽 마스크에서
    바로 셀 수 있다. 원본 quad 전체에서 세면 렌더러가 그리지도 않은 픽셀이 섞여,
    실제로 칠한 10,439 px 이 전부 양수인데 80,081 px 이 음수라고 보고된다.
    """
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        return None
    w = (float(h_inv[2, 0]) * xs + float(h_inv[2, 1]) * ys
         + float(h_inv[2, 2])).astype(np.float64)
    det = float(np.linalg.det(h_inv))
    with np.errstate(divide="ignore", invalid="ignore"):
        det_j = det / (w ** 3)
    return int(np.count_nonzero(~(det_j > 0.0)))


def _quad_pixels_in_image(quad, shape) -> int:
    mask = np.zeros(shape[:2], np.uint8)
    q = np.asarray(quad, np.float32)
    if q.shape != (4, 2) or not np.isfinite(q).all():
        return 0
    cv2.fillPoly(mask, [q.astype(np.int32)], 255)
    return int((mask > 0).sum())


def _reflected_mappings(caller_geom, carrier_boxes, source_boxes, source_shape,
                        rec=None, garment=None) -> dict:
    """호출자 박스만으로 **뒤집힌 사상**을 찾는다. → {"torso": bool, "components": [이름]}

    반사된 사상은 부위를 좌우로 뒤집어 그린다 — 플래킷·단추가 거울상이 된다. 렌더러의
    `neg_jacobian` 은 **몸통만** 보고, 그것도 후보의 자기보고다. 그래서 여기서 다시 센다.
    실측: 부위 박스 순서를 뒤집으면 행렬식 -1.0 로 36,120 px 이 달라지는데 모든 검사가
    깨끗했고 승격까지 됐다.
    """
    from .direct_torso_transfer import _decal_source_eligible
    out = {"torso": False, "components": [], "torsoNegDetPx": None,
           "componentNegDetPx": {}}
    sq, tq = caller_geom
    try:
        h = cv2.getPerspectiveTransform(sq.astype(np.float32), tq.astype(np.float32))
        neg = _negative_det_px(h, sq, source_shape)
    except (cv2.error, np.linalg.LinAlgError):
        neg = None
    out["torsoNegDetPx"] = neg
    out["torso"] = bool(neg is None or neg > 0)   # 셀 수 없으면 깨끗하다고 하지 않는다
    for name in sorted(set(carrier_boxes or {}) & set(source_boxes or {})):
        s_q = np.asarray(source_boxes[name], np.float32)
        c_q = np.asarray(carrier_boxes[name], np.float32)
        if s_q.shape != (4, 2) or c_q.shape != (4, 2):
            continue
        try:
            eligible, _why = _decal_source_eligible(s_q, c_q)
        except Exception:
            eligible = False
        if not eligible:
            continue                            # 렌더러가 안 그리는 부위는 셀 것이 없다

        # 렌더러가 **실제로 칠한** 픽셀만 대상이다. 박스가 원본 밖으로 잘리거나 target
        # 마스크와 만나지 않으면 렌더러는 건너뛰고, 뒤 이름에 완전히 덮이기도 한다 —
        # 그리지 않은 것을 '뒤집혔다'고 하면 정상 렌더가 막힌다.
        if rec is None:
            continue
        mask = (rec.get("componentOwnedBy") or {}).get(name)
        if mask is None:
            continue
        painted_here = mask & garment if garment is not None else mask
        # 원본 근거가 없는 픽셀은 렌더러가 **칠하지 않는다**(마스크 구멍). 그것을 빼지
        # 않으면 실제로 0 px 을 그린 부위가 10,201 px 이 뒤집혔다고 신고된다.
        backed = (rec or {}).get("sourceBacked")
        if backed is not None:
            painted_here = painted_here & backed
        if not painted_here.any():
            continue

        h_inv = (rec.get("componentInverse") or {}).get(name)
        neg_c = (_negative_det_px_on_mask(h_inv, painted_here)
                 if h_inv is not None else None)
        out["componentNegDetPx"][name] = neg_c
        if neg_c is None or neg_c > 0:
            out["components"].append(name)
    return out


def _caller_allowance(panel_map, caller_geom, carrier_boxes, source_boxes, rec, shape):
    """호출자 입력만으로 **어느 픽셀이 칠해져도 되는가**를 만든다. → (allowed, protected)

    v3 초안은 포함 영역만 만들고 **배제 규칙**을 빼먹었다. 그래서 규칙을 어기고 더 많이
    칠한 후보가 더 좋은 점수를 받았다 — 실측 두 건:
      · 원본 마스크 구멍 10,000 px 을 칠하면 커버리지 1.0, abErrorMedian 0.0.
      · carrier 전용 부위 박스(플래킷) 28,673 px 을 뚫고 칠하면 커버리지 0.8982 → 1.0.
    두 규칙 다 호출자가 준 것(원본 마스크, carrier/원본 박스)에서 나온다. 후보의 자기
    보고(`backgroundRejectedPx` 등)를 읽지 않고 여기서 다시 세운다.
    """
    garment = panel_map.garment_mask > 0
    region = _poly(shape, caller_geom[1]) & garment

    # 부위가 실제로 소유하는 픽셀 — 재구성이 이미 자격·경계를 반영해 만들어 둔 것.
    owned = rec["componentOwned"] & garment

    # carrier 쪽 구조 박스는 **이 전송이 소유하지 않는다**. 부위가 채우는 곳만 예외다.
    protected = np.zeros(shape, bool)
    for q in (carrier_boxes or {}).values():
        protected |= _poly(shape, q)
    protected &= ~owned

    allowed = (region | owned) & rec["valid"]
    if "sourceBacked" in rec:
        allowed &= rec["sourceBacked"]
    if "sourceStructure" in rec:
        allowed &= ~rec["sourceStructure"] | owned
    # 렌더러의 조명 support 는 carrier 구조 배제 **전** 영역이다 — 배제 후로 재면 박스를
    # 하나 더하는 것만으로 몸통 전체 색이 움직인다(렌더러가 같은 이유로 그렇게 한다).
    illum_support = allowed.copy()
    return allowed & ~protected, protected, illum_support


def _expected_alpha(panel_map, allowed, band) -> np.ndarray:
    """렌더러의 깃털 규칙을 **호출자 입력만으로** 다시 만든다.

    v3 는 `candidate.painted` 로 만들었다. 그러면 후보가 painted 를 지우는 순간 기대값도
    같이 줄어 alpha 는 늘 완벽했다(실측: 10,000 px 구멍에 maxDelta 0.0). 기대값이 검사
    대상에서 나오면 검사가 아니다. `allowed` 는 호출자 입력만으로 만들어지고 32개 구성
    전부에서 렌더러의 paint 집합과 정확히 일치했다.
    """
    src = (allowed.astype(np.uint8) * 255)
    silhouette = np.clip(
        cv2.distanceTransform(panel_map.garment_mask, cv2.DIST_L2, 3) / band, 0.0, 1.0)
    inner = np.clip(cv2.distanceTransform(src, cv2.DIST_L2, 3) / band, 0.0, 1.0)
    a = np.minimum(silhouette, inner).astype(np.float32)
    a[panel_map.garment_mask == 0] = 0.0
    return a


def _band_px(panel_map) -> float:
    # `... or 4.0` 은 **0 을 없는 값으로 만든다**. 렌더러는 키가 있으면 그 값을 쓰고
    # (0 → max(1.0, 0) = 1.0) 없을 때만 4 를 쓴다. 둘이 어긋나면 올바른 렌더가 잘린
    # 가장자리로 신고된다(실측: band 0 에서 hardEdgePx 6,420).
    band = _safe_float(panel_map.metrics.get("boundary_band_px"))
    return max(1.0, 4.0 if band is None else band)


def _alpha_check(candidate, expected) -> dict:
    """깃털은 **요구사항**이다 — 없애면 점수가 좋아지면 안 된다.

    기대 램프는 전부 호출자 쪽에서 온다(`garment_mask`, `boundary_band_px`, `allowed`).
    후보가 손댈 수 있는 것은 자기 `alpha` 뿐이고, 그것이 검사 대상이다.
    """
    got = np.asarray(candidate.alpha, np.float32)
    if got.shape != expected.shape:
        return {"computable": False, "reason": "alpha_shape_mismatch"}
    if not np.isfinite(got).all():
        return {"computable": False, "reason": "alpha_not_finite",
                "nonFinitePx": int((~np.isfinite(got)).sum())}
    ramp = (expected > 0.0) & (expected < 1.0)
    delta = np.abs(got - expected)
    return {
        "computable": True,
        "expectedRampPx": int(ramp.sum()),
        # 기대 alpha 가 **정확히 0** 인 자리에 alpha 가 있으면 그 픽셀은 설명되지 않는다.
        # 최댓값을 부동소수 여유와 재면 0.0009 짜리 1,000 px 이 통과한다 — 실루엣 밖만
        # 세던 것으로는 부족했다(보호 부위처럼 **옷 안**에도 0 자리가 있다).
        "nonZeroWhereExpectedZeroPx": int(((expected == 0.0) & (got != 0.0)).sum()),
        "maxAbsDeltaVsExpected": round(float(delta.max()), 4),
        "meanAbsDeltaInRamp": (round(float(delta[ramp].mean()), 4)
                               if ramp.any() else None),
        "hardEdgePx": int((ramp & (got >= 1.0 - 1e-6)).sum()),
    }


def _expected_full_bgr(rec, carrier_bgr, shading, illum_support):
    """호출자가 준 모드로 **칠할 값 자체**를 만든다. → bgr | None(모르는 모드)

    이것이 없으면 램프 휘도는 원리적으로 판정 불가다. 기준 두 개(warp·carrier)와의
    거리만 내면 **그 사이 중간값이 양쪽 모두에서 이긴다** — 실측: 6,444 램프 픽셀을
    전부 틀리게(평균 |ΔBGR| 25.4) 바꾼 그림이 a/b 0.1228 → 0.0125, L-vs-warp
    27.37 → 12.36, L-vs-carrier 17.49 → 12.98 로 **모든 지표가 좋아졌다**.

    `shading` 은 provenance 가 아니라 **호출자**에게서 온다 — 호출자가 렌더러에 준 바로
    그 값이다. 그래서 신뢰 경계는 그대로다. 대신 이 구역에 한해 오라클이 렌더러와 같은
    조명 분해를 계산하므로, **조명 수학 자체의 버그는 램프에서 공통 모드**가 된다.
    지금 그 구역은 아예 무측정이므로 어느 쪽으로도 손해는 없다.
    """
    from .direct_torso_transfer import (SHADING_CARRIER_LOW_FREQ_L, SHADING_RAW_SOURCE,
                                        SHADING_SOURCE_HIGHFREQ_CARRIER_LOWFREQ,
                                        _SHADING_SIGMA_FRAC, _masked_lowpass)
    src_lab = bgr_to_lab(rec["warped"]).astype(np.float32)
    car_lab = bgr_to_lab(carrier_bgr).astype(np.float32)
    h, w = src_lab.shape[:2]
    sigma = float(min(h, w)) * _SHADING_SIGMA_FRAC
    out_lab = src_lab.copy()
    recombined = None
    if shading == SHADING_SOURCE_HIGHFREQ_CARRIER_LOWFREQ:
        low = _masked_lowpass(src_lab[..., 0], illum_support, sigma)
        recombined = src_lab[..., 0] - low + cv2.GaussianBlur(
            car_lab[..., 0], (0, 0), sigmaX=sigma)
    elif shading == SHADING_CARRIER_LOW_FREQ_L:
        if not illum_support.any():
            return None
        mean_l = float(src_lab[..., 0][illum_support].mean())
        recombined = src_lab[..., 0] - mean_l + cv2.GaussianBlur(
            car_lab[..., 0], (0, 0), sigmaX=sigma)
    elif shading != SHADING_RAW_SOURCE:
        return None
    if recombined is not None:
        out_lab[..., 0] = np.clip(recombined, 0.0, 100.0)
    return lab_to_bgr(out_lab)


def _blend_check(candidate, carrier_bgr, rec, ramp, expected, full_bgr) -> dict:
    """램프에서는 **합성이 필수**다. 그래서 내용 비교 대신 합성 관계를 절대값으로 잰다.

    상관으로 보면 안 된다 — 척도 불변이라 램프 변위를 1.5배로 키워도(픽셀당 최대 43)
    `blendCorrInRamp` 가 0.8546 → 0.8621 로 **좋아졌다**.

    합성을 되돌린다: `implied = carrier + (image - carrier)/alpha`.

      · **색역** — 진짜 색을 진짜 alpha 로 합성했다면 되돌린 값도 **진짜 색**이어야 한다.
        임계가 아니라 색의 물리적 범위다. 깃털 고리를 검게 칠하면 되돌린 값이
        [-1106.5, -7.1] 로 나온다(실측 17,024 px 전부). 이것이 램프 휘도를 잡는 축이다
        — v4 초안은 a/b 만 봐서 그 고리가 0.0531 → 0.0699 로 **거의 그대로**였다.
      · **채도** — 어떤 조명 모드도 a/b 를 바꾸지 않으므로 warp 의 a/b 와 같아야 한다.
      · **휘도** — 모드마다 출처가 다르므로 **두 기준 모두**와 재고 판정하지 않는다
        (저역 밴드에서와 같은 규율).

    1/alpha 증폭은 통계마다 다르게 다룬다. 가중평균은 alpha 가중으로 상쇄하고, 중앙값·
    P95 는 alpha 를 **곱해** 관측 공간으로 되돌린다. 되돌리지 않으면 밴드가 넓어질수록
    옳은 렌더가 나빠 보인다(실측: 같은 올바른 렌더가 band 3/12/40 에서 P95 1.88 →
    4.01 → 9.53 — 렌더가 아니라 양자화가 커진 것이다).
    """
    if rec is None:
        return {"computable": False, "reason": "caller_reconstruction_unavailable"}
    sel = ramp & rec["valid"]
    if not sel.any():
        # 구역이 비어도 계수는 **0 으로 존재해야** 한다. 키가 사라지면 승격이 그 규칙을
        # 통째로 건너뛴다(실측: 램프가 없으면 색역 규칙이 없어졌다).
        return {"computable": False, "reason": "no_ramp", "rampPx": 0,
                "impliedOutOfGamutPx": 0}
    a = np.clip(expected, 1e-3, 1.0)[..., None].astype(np.float64)
    car = carrier_bgr.astype(np.float64)
    implied = car + (candidate.image_bgr.astype(np.float64) - car) / a

    # 여유는 **그 픽셀 자신의 불확실성**이고, 그 불확실성은 **비대칭**이다.
    # 렌더러의 마지막 변환은 `np.clip(...).astype(np.uint8)` = 절단이다. 그래서 관측된
    # image 는 정확값보다 **작기만** 하고(0 ≤ exact-image < 1), 되돌린 값도 아래로만
    # 밀린다. 위쪽까지 1/alpha 를 열어 주면 진짜 초과가 숨는다 — 실측: 램프 전체에
    # BGR 코드 +1 을 더해 113,876 px 이 255.5 를 넘고 최대 335.08 이 됐는데도 0 이었다.
    # 아래 여유 (1/alpha + 1) 은 절단 두 번(blend 1/alpha, full 1)의 합이다.
    lo_tol = 1.0 / a + 1.0
    hi_tol = 1.0            # 위쪽은 full 의 변환 오차만큼만 — 절단은 값을 키우지 않는다
    out_of_gamut = ((implied < -0.5 - lo_tol)
                    | (implied > 255.5 + hi_tol)).any(axis=2) & sel
    lab_i = cv2.cvtColor((np.clip(implied, 0.0, 255.0) / 255.0).astype(np.float32),
                         cv2.COLOR_BGR2LAB)
    lab_w = cv2.cvtColor((rec["warped"].astype(np.float32) / 255.0), cv2.COLOR_BGR2LAB)
    lab_c = cv2.cvtColor((carrier_bgr.astype(np.float32) / 255.0), cv2.COLOR_BGR2LAB)
    err = np.linalg.norm(lab_i[..., 1:3] - lab_w[..., 1:3], axis=2)[sel]
    wgt = expected[sel].astype(np.float64)
    scaled = err * wgt                       # 관측 공간으로 되돌린 오차
    l_w = np.abs(lab_i[..., 0] - lab_w[..., 0])[sel]
    l_c = np.abs(lab_i[..., 0] - lab_c[..., 0])[sel]
    # **결정된 기대 합성값과의 절대 잔차** — 램프 휘도를 판정할 수 있는 유일한 축.
    composite = None
    if full_bgr is not None:
        exp_bgr = np.clip(expected[..., None] * full_bgr.astype(np.float64)
                          + (1.0 - expected[..., None]) * car, 0, 255)
        resid = np.abs(candidate.image_bgr.astype(np.float64) - exp_bgr).max(axis=2)[sel]
        composite = {
            "compositeResidualMedian": round(float(np.median(resid)), 4),
            "compositeResidualP95": round(float(np.percentile(resid, 95)), 4),
            "compositeResidualMax": round(float(resid.max()), 4),
        }
    # 부위 경계에서는 두 사상이 만나므로 기준(warp) 자체가 불연속이다. 개수를 함께 내는
    # 것은 임계 단계가 그 영향을 **확인할 수 있게** 하기 위해서지, 꼬리의 원인이 항상
    # 경계라는 뜻이 아니다 — 어떤 구성에서는 경계 위 P95 8.42 대 밖 2.77 이었지만 다른
    # 구성에서는 경계 위 0.34 대 밖 1.58 로 반대였다. 원인이 아니라 **맥락**이다.
    owned = rec.get("componentOwned")
    edge_px = 0
    if owned is not None and owned.any():
        k = np.ones((3, 3), np.uint8)
        o8 = owned.astype(np.uint8)
        edge = (cv2.dilate(o8, k) > 0) & ~(cv2.erode(o8, k) > 0)
        edge_px = int((sel & edge).sum())
    return {
        "computable": True,
        "rampPx": int(sel.sum()),
        "rampComponentEdgePx": edge_px,
        # 합성으로 설명되지 않는 픽셀 — 모드와 무관한 축.
        "impliedOutOfGamutPx": int(out_of_gamut.sum()),
        "impliedOutOfGamutFrac": round(float(out_of_gamut.sum() / max(1, int(sel.sum()))), 4),
        "impliedAbErrorAlphaWeightedMean": round(float(np.average(err, weights=wgt)), 4),
        "scaledAbErrorMedian": round(float(np.median(scaled)), 4),
        "scaledAbErrorP95": round(float(np.percentile(scaled, 95)), 4),
        # 휘도는 두 기준 모두와 재고 판정은 임계 단계에 넘긴다.
        "impliedLAlphaWeightedDeltaVsWarp": round(float(np.average(l_w, weights=wgt)), 4),
        "impliedLAlphaWeightedDeltaVsCarrier": round(float(np.average(l_c, weights=wgt)), 4),
        **(composite or {"compositeResidualMedian": None, "compositeResidualP95": None,
                         "compositeResidualMax": None}),
    }


def _domain_check(candidate, carrier_bgr, allowed, protected, backed, metrics) -> dict:
    """비교 영역을 **QC 가** 만들고, 후보가 칠했다는 영역과 대조한다.

    영역을 `painted` 로 잡으면 후보가 `painted` 의 절반을 지우는 것만으로 틀린 픽셀을
    비교에서 빼낼 수 있었다(실측: 140,800 px 이 사라지고 점수는 만점). 그래서 화면을
    두 통으로 나눈다 —

      * 칠했다고 주장한 픽셀 → warp 된 원본과 같아야 한다(reconstruction 이 잰다).
      * 칠하지 않았다고 주장한 픽셀 → **carrier 그대로**여야 한다.

    숨긴 픽셀은 두 번째 통으로 떨어져 `unclaimedChangedPx` 로 드러난다. 그리고 칠해도
    되는 픽셀인지는 `_caller_allowance` 가 따로 판정한다 — 규칙 위반은 '더 잘한 것'이
    아니다.
    """
    painted = candidate.painted > 0
    alpha_pos = np.asarray(candidate.alpha, np.float32) > 0
    changed = np.any(candidate.image_bgr != carrier_bgr, axis=2)
    report = {
        "computable": allowed is not None,
        "candidatePaintedPx": int(painted.sum()),
        "metricsPaintedPx": metrics.get("paintedPx"),
        "paintedMatchesMetrics": _same_count(int(painted.sum()),
                                            metrics.get("paintedPx")),
        "paintedWithoutAlphaPx": int((painted & ~alpha_pos).sum()),
        # 칠했다고 하지 않았는데 바뀐 픽셀 = 숨긴 칠. 이미지 전체에서 본다.
        "unclaimedChangedPx": int((changed & ~painted).sum()),
        "claimedUnchangedPx": int((~changed & painted).sum()),
    }
    if allowed is None:
        return report
    report.update({
        "allowedDomainPx": int(allowed.sum()),
        "paintedOutsideAllowedPx": int((painted & ~allowed).sum()),
        "allowedNotPaintedPx": int((allowed & ~painted).sum()),
        "paintedFracOfAllowed": round(float((painted & allowed).sum())
                                      / max(1, int(allowed.sum())), 4),
        # 규칙별로 이름을 붙여 둔다 — 어떤 규칙을 어겼는지가 임계 단계의 입력이다.
        "paintedInProtectedComponentPx": int((painted & protected).sum()),
        "paintedWithoutSourceBackingPx": (int((painted & ~backed).sum())
                                          if backed is not None else None),
        "unclaimedChangedInAllowedPx": int((changed & ~painted & allowed).sum()),
    })
    return report


def _reconstruction_vs_caller(candidate, carrier_bgr, rec, domain, recorded_frac) -> dict:
    """호출자 기하로 만든 기대값과 픽셀 단위 대조. 성분별 상관·진폭·오차."""
    if rec is None:
        return {"computable": False, "reason": "caller_reconstruction_unavailable"}
    warped, valid = rec["warped"], rec["valid"]
    sel = domain & valid
    if not sel.any():
        return {"computable": False, "reason": "no_overlap", "comparedPx": 0,
                "domainNotReconstructedPx": 0}
    # **픽셀당 오차에는 최소 개수가 없다.** 고정 256 컷오프는 작은 대상을 통째로
    # 무측정으로 만들었다(실측: 내부 9 px 을 전부 바꿔 최대 226 코드 오차를 넣었는데
    # correct/defective 의 checks 가 완전히 같았다). 표본 수가 필요한 것은 **밴드 통계**
    # 뿐이므로, 그쪽만 정의 안 됨으로 내려간다.
    ref = bgr_to_lab(warped).astype(np.float64)
    got = bgr_to_lab(candidate.image_bgr).astype(np.float64)
    ab_err = np.linalg.norm(got[..., 1:3][sel] - ref[..., 1:3][sel], axis=1)
    h, w = candidate.image_bgr.shape[:2]
    # σ 는 **모듈 상수**에서 온다. provenance 의 값을 쓰면 후보가 분할점을 옮겨 오차를
    # 측정되지 않는 밴드로 밀어낼 수 있다. 기록된 값은 대조만 한다.
    from .direct_torso_transfer import _SHADING_SIGMA_FRAC
    sigma = max(1.0, float(min(h, w)) * float(_SHADING_SIGMA_FRAC))
    bands = _band_stats(got[..., 0], ref[..., 0], sel, sigma)
    # 기본 shading 모드는 저주파를 **carrier** 에서 가져온다 — 그 모드에서 원본 대비
    # 저역 편차는 정상 동작이지 오차가 아니다(실측 4~20 L). 그래서 저역은 carrier 와도
    # 잰다. 두 기준을 모두 내보내면 어느 모드에서도 균일 오프셋이 숨을 곳이 없고,
    # 어느 기준을 쓸지는 후보가 아니라 임계 단계가 정한다.
    car_low, _ = _split_bands(bgr_to_lab(carrier_bgr).astype(np.float64)[..., 0],
                              sel, sigma)
    got_low, _ = _split_bands(got[..., 0], sel, sigma)
    bands["lowMeanAbsDeltaVsCarrier"] = round(
        float(np.abs(got_low[sel] - car_low[sel]).mean()), 4)
    bands["lowMaxAbsDeltaVsCarrier"] = round(
        float(np.abs(got_low[sel] - car_low[sel]).max()), 4)
    return {
        "computable": True,
        "comparedPx": int(sel.sum()),
        "domainNotReconstructedPx": int((domain & ~valid).sum()),
        "abErrorMedian": round(float(np.median(ab_err)), 4),
        "abErrorP95": round(float(np.percentile(ab_err, 95)), 4),
        "abWithin2Frac": round(float((ab_err <= 2.0).mean()), 4),
        "sigmaPx": round(sigma, 2),
        "sigmaFracMatchesRenderer": bool(
            _safe_float(recorded_frac) is not None
            and abs(_safe_float(recorded_frac) - float(_SHADING_SIGMA_FRAC)) < 1e-9),
        **{f"L_{k}": v for k, v in bands.items()},
    }



def evaluate_direct_transfer(
    candidate,
    *,
    carrier_bgr: np.ndarray,
    source_bgr: np.ndarray,
    panel_map,
    source_landmarks,
    shading: str = "source_highfreq_carrier_lowfreq",
    source_sha256: str | None = None,
    carrier_sha256: str | None = None,
    source_garment_mask: np.ndarray | None = None,
    carrier_component_boxes: dict | None = None,
    source_component_boxes: dict | None = None,
) -> DirectTransferQC:
    """후보 → 원시 측정치 묶음. **판정하지 않는다.**

    기하·영역·기대값은 전부 **여기 넘어온 호출자 입력**에서 나온다. candidate 의
    provenance 와 painted 는 검증 대상이지 진실의 출처가 아니다.
    """
    checks: dict = {}
    notes: list[str] = []
    metrics = candidate.metrics if isinstance(candidate.metrics, dict) else {}
    prov = candidate.provenance if isinstance(candidate.provenance, dict) else {}

    # 후보 배열이 어긋나면 **인덱싱 전에** 말한다. v3 는 여기서 IndexError·브로드캐스트
    # ValueError 로 터졌다 — 예외는 '계산 불가'보다 나쁘다. 측정 자체가 사라지므로
    # 후보가 채점을 없앨 수 있다는 뜻이기 때문이다.
    try:
        image = np.asarray(candidate.image_bgr)
    except Exception:
        return DirectTransferQC(
            checks={"inputs": {"computable": False,
                               "reason": "image_unconvertible"}},
            decision=DECISION_UNTHRESHOLDED,
            notes=("candidate image cannot be read as an array",))
    shape = image.shape[:2]
    bad_arrays: dict = {}

    # **이미지와 carrier 는 uint8 BGR 이어야 한다.** Lab 변환이 그 의미를 전제한다 —
    # 같은 그림을 float32 로만 바꿔도 재구성 중앙값이 0 → 181.02 로 뛰었고, int16·
    # float64 는 OpenCV 미지원 깊이로 보고서를 통째로 없앴다.
    def _u8_image(arr) -> bool:
        a = np.asarray(arr)
        return a.ndim == 3 and a.shape[2] == 3 and a.dtype == np.uint8

    if not _u8_image(image) or tuple(np.asarray(carrier_bgr).shape[:2]) != shape or (
            not _u8_image(carrier_bgr)):
        return DirectTransferQC(
            checks={"inputs": {
                "computable": False, "reason": "image_or_carrier_not_uint8_bgr",
                "imageShape": f"{image.shape}/{image.dtype}",
                "carrierShape": (f"{np.asarray(carrier_bgr).shape}"
                                 f"/{np.asarray(carrier_bgr).dtype}")}},
            decision=DECISION_UNTHRESHOLDED,
            notes=("image/carrier are not uint8 BGR; the subject itself is unusable",))
    if source_garment_mask is not None and (
            tuple(np.asarray(source_garment_mask).shape[:2])
            != tuple(np.asarray(source_bgr).shape[:2])):
        return DirectTransferQC(
            checks={"inputs": {
                "computable": False, "reason": "source_mask_shape",
                "sourceShape": tuple(np.asarray(source_bgr).shape[:2]),
                "maskShape": tuple(np.asarray(source_garment_mask).shape[:2])}},
            decision=DECISION_UNTHRESHOLDED,
            notes=("source mask does not match the source image",))
    if not _u8_image(source_bgr):
        # 원본이 uint8 이 아니면 warp·Lab 변환이 전부 다른 의미가 된다 — 옳은 렌더에
        # abErrorMedian 154.03 이 나왔다.
        return DirectTransferQC(
            checks={"inputs": {
                "computable": False, "reason": "source_not_uint8_bgr",
                "sourceShape": (f"{np.asarray(source_bgr).shape}"
                                f"/{np.asarray(source_bgr).dtype}")}},
            decision=DECISION_UNTHRESHOLDED,
            notes=("source image is not uint8 BGR; the reference cannot be built",))
    if tuple(np.asarray(panel_map.garment_mask).shape[:2]) != shape:
        return DirectTransferQC(
            checks={"inputs": {"computable": False, "reason": "garment_mask_shape",
                               "imageShape": tuple(shape)}},
            decision=DECISION_UNTHRESHOLDED,
            notes=("caller garment mask does not match the image",))

    # `painted`·`alpha` 가 망가진 것은 **후보의 결함**이지 측정 불가 사유가 아니다.
    # v4 초안은 여기서 전부 반환해 버려서, alpha 를 (3,3) 으로 두는 것만으로 7px 밀린
    # 렌더의 재구성 오차 44.82 가 통째로 사라졌다 — 만들려던 탈출구를 그대로 만들었다.
    # 이제 안전한 대체값을 넣고 **나머지 측정은 계속한다**. 기대 alpha·비교 영역은
    # 어차피 `allowed` 에서 나오므로 이 둘 없이도 대부분이 계산된다.
    non_finite_px: dict = {}

    def _plane(arr, name):
        # `np.asarray` 자체가 던진다(들쭉날쭉한 리스트는 numpy 2 에서 ValueError).
        # 가드가 변환 **밖**에 있으면 가드가 아니다.
        try:
            a = np.asarray(arr)
        except Exception:
            bad_arrays[name] = "unconvertible"
            return None
        if a.dtype.kind not in "biuf" or a.ndim != 2 or tuple(a.shape) != shape:
            bad_arrays[name] = f"{a.shape}/{a.dtype}"
            return None
        if not np.isfinite(a.astype(np.float64)).all():
            bad_arrays[name] = "non_finite"
            # **원본의** 비유한 개수를 남긴다. 뒤에서 0 으로 갈아 끼운 배열을 재면
            # "유한하다"고 보고하게 된다(실측: NaN 1개인데 alphaNonFinitePx 0).
            non_finite_px[name] = int((~np.isfinite(a.astype(np.float64))).sum())
            return None
        return a

    painted_arr = _plane(candidate.painted, "painted")
    alpha_arr = _plane(candidate.alpha, "alpha")
    candidate = dataclasses.replace(
        candidate,
        painted=(painted_arr if painted_arr is not None
                 else np.zeros(shape, np.uint8)),
        alpha=(alpha_arr.astype(np.float32) if alpha_arr is not None
               else np.zeros(shape, np.float32)))
    painted_values = np.unique(np.asarray(candidate.painted))
    if not np.isin(painted_values, (0, 255)).all():
        bad_arrays["painted"] = "not_binary_0_255"
    checks["candidateArrays"] = {
        "computable": True,
        "usable": not bad_arrays,
        "unusable": bad_arrays or None,
    }

    painted = candidate.painted > 0
    garment = panel_map.garment_mask > 0
    # 기하와 재구성은 뒤의 여러 검사가 쓰므로 **맨 앞**에서 세운다.
    caller_geom = _caller_geometry(panel_map, source_landmarks, source_bgr.shape)
    rec = (_reconstruct_from_caller(
        source_bgr, candidate.image_bgr.shape[:2], caller_geom[0], caller_geom[1],
        carrier_component_boxes, source_component_boxes, source_garment_mask)
        if caller_geom is not None else None)

    # 1) replay 근거 — 없으면 같은 그림을 다시 못 만든다.
    missing = [k for k in _REQUIRED_PROVENANCE
               if (k not in prov) or (k not in _PRESENCE_ONLY
                                      and _is_blank(prov.get(k)))]
    # 값이 있어도 형이 틀리면 replay 가 안 된다. '없음'과 '망가짐'은 다른 사건이므로
    # 이름을 따로 붙인다(v3 는 sha 자리에 배열이 와도 complete=True 라고 했다).
    malformed = sorted(k for k in _REQUIRED_PROVENANCE
                       if k in prov and prov.get(k) is not None
                       and not _prov_value_sane(k, prov.get(k)))
    # 형이 맞는 것과 **그 값으로 이 호출자의 렌더를 다시 만들 수 있는 것**은 다르다.
    # 형만 보면 후보가 sha 를 전부 "x", band 를 12345, sigma 를 999 로 적어도
    # complete=True 를 살 수 있었다(실측). 호출자가 아는 값과 직접 대조한다.
    mismatched = _provenance_vs_caller(
        prov, source_bgr, carrier_bgr, panel_map, source_garment_mask,
        carrier_component_boxes, source_component_boxes, shading,
        source_sha256, carrier_sha256)
    checks["provenance"] = {
        "computable": True,
        "requiredKeys": len(_REQUIRED_PROVENANCE),
        "missingKeys": missing,
        "malformedKeys": malformed,
        "mismatchedKeys": sorted(mismatched),
        "complete": not missing and not malformed and not mismatched,
        "version": prov.get("version") if isinstance(prov.get("version"), str) else None,
    }

    # 2) 사상 유효성 — 주기와 무관하게 사상 자체가 성립했는가.
    reflected = (_reflected_mappings(caller_geom, carrier_component_boxes,
                                     source_component_boxes, source_bgr.shape,
                                     rec, garment)
                 if (caller_geom is not None and rec is not None) else None)
    checks["mapping"] = {
        # 계산 가능 여부도 **호출자 기준**으로 정한다. 자기보고의 **존재**로 정하면
        # 값이 게이팅되지 않아도 그 유무만으로 판정이 뒤집힌다(실측: 픽셀이 하나도
        # 다르지 않은데 `neg_jacobian=None` 하나로 보류됐다).
        "computable": (reflected or {}).get("torsoNegDetPx") is not None,
        "negJacobian": metrics.get("neg_jacobian"),
        # 아래 둘은 **호출자 박스에서 다시 센 것**이다 — 자기보고가 아니다.
        "torsoReflected": (reflected or {}).get("torso"),
        "reflectedComponents": (reflected or {}).get("components"),
        "torsoNegDetPx": (reflected or {}).get("torsoNegDetPx"),
        "componentNegDetPx": (reflected or {}).get("componentNegDetPx"),
        # 아래 둘도 **호출자 기하에서 다시 계산한** 값이다. 자기보고를 그대로 실으면
        # 후보가 12345.0 같은 숫자를 측정치 자리에 넣을 수 있다.
        **_caller_stretch(caller_geom),
        "anisotropyP99": metrics.get("anisotropy_p99"),
        "stretchOverFrac": metrics.get("stretch_over_frac"),
    }

    # 3) 표본 밀도 — 확대는 없는 디테일을 지어내고, 강한 축소는 좁은 밴드를 지운다.
    checks["sampling"] = {
        "computable": metrics.get('maxUpscaleFactor') is not None,
        "maxUpscaleFactor": metrics.get("maxUpscaleFactor"),
        "minSourceSamplingDensity": metrics.get("minSourceSamplingDensity"),
        "maxMinificationFactor": metrics.get("maxMinificationFactor"),
    }

    # 4) 마스크 포함 — 실루엣 밖은 carrier 그대로여야 하고, 칠은 옷 안에만 있어야 한다.
    outside = ~garment
    outside_untouched = bool(np.array_equal(candidate.image_bgr[outside],
                                            carrier_bgr[outside]))
    # alpha 가 유한하지 않으면 max() 는 NaN 을 낸다. NaN 은 측정치가 아니라 측정 실패다
    # — 이름을 붙여 내보내야 임계 단계가 그것을 구분할 수 있다.
    alpha_arr = np.asarray(candidate.alpha, np.float64)
    # 후보가 준 **원본** 기준으로 말한다. 위생 처리한 대체값을 재면 결함이 사라진다.
    alpha_non_finite = non_finite_px.get("alpha", 0)
    alpha_finite = bool(np.isfinite(alpha_arr).all()) and alpha_non_finite == 0
    checks["containment"] = {
        "computable": True,
        "paintedOutsideGarmentPx": int((painted & outside).sum()),
        "outsideGarmentUntouched": outside_untouched,
        "alphaFinite": alpha_finite,
        "alphaNonFinitePx": int((~np.isfinite(alpha_arr)).sum()) + alpha_non_finite,
        "alphaMaxOutsideGarment": (round(float(alpha_arr[outside].max()), 6)
                                   if (outside.any() and alpha_finite) else None),
        # 밖의 alpha 는 0 이 **정의**다. 최댓값을 부동소수 여유와 비교하면 0.0009 짜리
        # 누출 10,000 px 이 통과한다 — 개수는 그런 여유를 갖지 않는다.
        # 실루엣 밖 픽셀이 하나도 없으면(전면 마스크) 밖의 위반도 **0** 이다.
        # None 을 내보내면 승격이 그것을 '검증되지 않음'으로 읽어 정상 렌더를 막는다.
        "alphaNonZeroOutsideGarmentPx": (int((alpha_arr[outside] != 0).sum())
                                         if alpha_finite else None),
    }

    # 5) 원본 근거 — 배경/구조 거절이 실제로 일어났는가(그 자체가 관측치다).
    checks["sourceBacking"] = {
        "computable": metrics.get('paintedPx') is not None,
        "paintedPx": metrics.get("paintedPx"),
        "backgroundRejectedPx": metrics.get("backgroundRejectedPx"),
        "sourceStructureRejectedPx": metrics.get("sourceStructureRejectedPx"),
        "outOfSourceFrac": metrics.get("outOfSourceFrac"),
        "sourceMaskApplied": metrics.get("sourceMaskApplied"),
    }

    # 6) 기하 — **호출자 입력**으로 세우고, 기록된 값을 그것과 대조한다.
    checks["geometry"] = _geometry_vs_caller(prov, caller_geom)

    # 8) **화면을 세 구역으로 나눈다.** 구역마다 성립하는 약속이 다르기 때문이다.
    #
    #    v3 는 칠한 영역 전체를 합성 **전** warp 와 견줬다. 그런데 램프에서는 합성이
    #    필수이므로 옳은 렌더가 반드시 warp 와 다르다 — 그 필연적 차이를 오차로 셌고,
    #    그것을 없앤 하드 엣지를 개선으로 셌다(실측: 매끈한 원단에서 옳은 렌더가
    #    L_highAmplitudeRatio 62,135·L_highCorr 0.1607, 금지된 하드 엣지가 1.0·1.0).
    #
    #      · alpha == 1  내부 → 합성이 항등 → 내용 비교가 유효한 **유일한** 구역
    #      · 0 < alpha < 1  램프 → 합성 관계를 절대값으로 검사
    #      · alpha == 0  → carrier 그대로여야 한다(domain 이 잰다)
    allowed = protected = interior = ramp = illum_support = None
    expected_alpha = None
    if rec is not None and caller_geom is not None:
        allowed, protected, illum_support = _caller_allowance(
            panel_map, caller_geom, carrier_component_boxes, source_component_boxes,
            rec, candidate.image_bgr.shape[:2])
        expected_alpha = _expected_alpha(panel_map, allowed, _band_px(panel_map))
        interior = allowed & (expected_alpha >= 1.0 - 1e-6)
        ramp = allowed & (expected_alpha > 0.0) & (expected_alpha < 1.0 - 1e-6)

    # 9) 영역 — QC 가 스스로 만든다. candidate.painted 는 **대조 대상**이다.
    checks["domain"] = _domain_check(
        candidate, carrier_bgr, allowed, protected,
        rec.get("sourceBacked") if rec else None, metrics)
    if interior is not None:
        checks["domain"]["interiorPx"] = int(interior.sum())
        checks["domain"]["rampPx"] = int(ramp.sum())

    # 10) 깃털 — 요구사항을 없앤 렌더가 더 좋은 점수를 받으면 안 된다.
    checks["alpha"] = (_alpha_check(candidate, expected_alpha)
                       if expected_alpha is not None
                       else {"computable": False, "reason": "caller_allowance_unavailable"})

    # 11) 합성 관계 — 램프 전용. 상관이 아니라 절대값으로 잰다.
    full_bgr = (_expected_full_bgr(rec, carrier_bgr, shading, illum_support)
                if (rec is not None and illum_support is not None) else None)
    checks["blend"] = (
        _blend_check(candidate, carrier_bgr, rec, ramp, expected_alpha, full_bgr)
        if ramp is not None
        else {"computable": False, "reason": "caller_allowance_unavailable"})

    # 12) 재구성 대조 — **내부 픽셀 한정.** 평행이동·위상·내용·진폭.
    checks["reconstruction"] = (
        _reconstruction_vs_caller(candidate, carrier_bgr, rec, interior,
                                  prov.get("shadingSigmaShortSideFrac"))
        if interior is not None
        else {"computable": False, "reason": "caller_allowance_unavailable"})

    # 13) 방향 보존 — 회전·전단 전용 보조 지표.
    checks["direction"] = _direction_check(
        candidate, source_bgr, interior if interior is not None else (candidate.painted > 0),
        caller_geom, source_garment_mask, notes,
        rec.get("componentOwned") if rec else None)

    # 14) 색 충실도 — 재구성과 **같은 픽셀끼리**, 같은 구역에서.
    checks["colour"] = (_colour_check(candidate, rec, interior)
                        if interior is not None
                        else {"computable": False,
                              "reason": "caller_allowance_unavailable"})

    # 15) 휘도 건전성 — 클리핑과 고주파 보존.
    checks["luminance"] = {
        "computable": metrics.get('clippedFracL') is not None,
        "clippedFracL": metrics.get("clippedFracL"),
        "highFreqRetention": metrics.get("highFreqRetention"),
        "sourceLowFreqStdL": metrics.get("sourceLowFreqStdL"),
        "carrierLowFreqStdL": metrics.get("carrierLowFreqStdL"),
        "shadingMode": metrics.get("shadingMode"),
    }

    # 16) 부위 일관성 — 채워졌는가, 몸통 사상 기준으로 얼마나 어긋나 있었는가.
    checks["components"] = {
        "computable": True,
        "fill": _as_mapping(metrics.get("componentFill")),
        "placement": _as_mapping(metrics.get("componentPlacement")),
        "filledPx": metrics.get("componentFilledPx"),
        "targetPx": metrics.get("componentTargetPx"),
        "coverage": metrics.get("componentCoverage"),
        "outsideTorsoQuadPx": metrics.get("componentPxOutsideTorsoQuad"),
    }

    return DirectTransferQC(checks=checks, decision=DECISION_UNTHRESHOLDED,
                            notes=tuple(notes))


def _source_region_mask(source_bgr, quad, source_garment_mask):
    quad = np.asarray(quad if quad is not None else [], np.float32)
    if quad.shape != (4, 2):
        return None
    mask = np.zeros(source_bgr.shape[:2], np.uint8)
    cv2.fillPoly(mask, [quad.astype(np.int32)], 255)
    sel = mask > 0
    if source_garment_mask is not None:
        sel &= source_garment_mask > 0
    return sel


def _direction_check(candidate, source_bgr, domain, caller_geom,
                     source_garment_mask, notes, component_owned=None) -> dict:
    """원본 방향을 **호출자 기하**로 옮긴 예측과 출력 측정을 비교한다.

    보조 지표다. 전역 축 하나는 평행이동·진폭 오류를 못 잡는다 — 그건 reconstruction
    이 잡는다. 여기서 잡는 건 회전·전단이다.
    """
    if caller_geom is None:
        notes.append("direction: caller geometry unavailable")
        return {"computable": False, "reason": "caller_geometry_unavailable"}
    sq, tq = caller_geom
    try:
        H = cv2.getPerspectiveTransform(sq.astype(np.float32), tq.astype(np.float32))
    except cv2.error:
        return {"computable": False, "reason": "caller_geometry_degenerate"}
    src_sel = _source_region_mask(source_bgr, sq, source_garment_mask)
    if src_sel is None:
        return {"computable": False, "reason": "caller_geometry_degenerate"}

    # 부위는 **자기 box→box 사상**으로 옮겨진다. 몸통 사상으로 예측해 놓고 부위 픽셀을
    # 함께 관측하면, 45도 돌린 부위가 올바르게 채워졌는데도 45도 오차로 나온다(실측).
    if component_owned is not None:
        domain = domain & ~component_owned
        if int(domain.sum()) < 256:
            return {"computable": False, "reason": "torso_support_too_small"}

    src_gray = cv2.cvtColor(source_bgr, cv2.COLOR_BGR2GRAY)
    out_gray = cv2.cvtColor(candidate.image_bgr, cv2.COLOR_BGR2GRAY)
    # 침식 반경도 **호출자 입력**에서 나온다. metrics 의 값을 쓰면 후보가 반경을 키워
    # 지지 영역을 지우고 `insufficient_support` 뒤로 숨을 수 있다 — 계산 불가는 실패를
    # 대신할 수 없다.
    from .direct_torso_transfer import _SHADING_SIGMA_FRAC
    short = float(min(candidate.image_bgr.shape[:2]))
    radius = int(max(2, min(8, round(short * _SHADING_SIGMA_FRAC / 4.0))))
    src_dir = _dominant_orientation(src_gray, src_sel, radius)
    out_dir = _dominant_orientation(out_gray, domain, radius)
    if (src_dir is not None and not np.isfinite(src_dir[1])) or (
            out_dir is not None and not np.isfinite(out_dir[1])):
        return {"computable": False, "reason": "orientation_not_finite"}
    if src_dir is None or out_dir is None:
        notes.append("direction: not enough oriented support")
        return {"computable": False, "reason": "insufficient_support"}

    if not np.isfinite(H).all():
        return {"computable": False, "reason": "caller_geometry_degenerate"}
    jac = _jacobian_at(H, tq.mean(axis=0))
    if jac is None:
        return {"computable": False, "reason": "jacobian_degenerate"}
    # gradient(법선)는 J^-T 로 옮긴다 — 방향 벡터와 다른 규칙이다.
    predicted = np.linalg.inv(jac).T @ src_dir[0]
    if not np.isfinite(predicted).all():
        return {"computable": False, "reason": "prediction_non_finite"}
    return {
        "computable": True,
        "sourceUnit": [round(float(v), 5) for v in src_dir[0]],
        "sourceCoherence": round(float(src_dir[1]), 4),
        "predictedUnit": [round(float(v), 5)
                          for v in predicted / max(float(np.linalg.norm(predicted)), 1e-12)],
        "observedUnit": [round(float(v), 5) for v in out_dir[0]],
        "observedCoherence": round(float(out_dir[1]), 4),
        "angleErrorDeg": round(_angle_between(predicted, out_dir[0]), 3),
    }


def _colour_check(candidate, rec, domain) -> dict:
    """색은 **재구성과 같은 픽셀끼리** 비교한다.

    v1 은 원본 quad 전체의 중앙값과 칠한 영역의 중앙값을 견줬다. 두 모집단은 원근 가중도
    다르고 배제·부위 구성도 다르다 — 옳은 렌더에 87.5 의 가짜 이동을 보고했고, 60% 가
    틀린 렌더에는 0.0 을 보고했다. 대응하는 픽셀끼리 봐야 의미가 있다.
    """
    if rec is None:
        return {"computable": False, "reason": "caller_reconstruction_unavailable"}
    warped, valid = rec["warped"], rec["valid"]
    sel = domain & valid
    if not sel.any():
        return {"computable": False, "reason": "no_overlap", "comparedPx": 0}
    ref_ab = bgr_to_lab(warped)[..., 1:3][sel].astype(np.float64)
    out_ab = bgr_to_lab(candidate.image_bgr)[..., 1:3][sel].astype(np.float64)
    # **대응 픽셀마다** 재고 그 분포를 낸다. 모집단 중앙값의 차는 대응을 버리는 값이라
    # 281,600 픽셀을 좌우로 뒤집어도 0.0 을 냈다(재구성은 같은 순간 44.8 을 냈다).
    per_px = np.linalg.norm(out_ab - ref_ab, axis=1)
    delta = np.median(out_ab, axis=0) - np.median(ref_ab, axis=0)
    return {
        "computable": True,
        "comparedPx": int(sel.sum()),
        "referenceMedianAb": [round(float(v), 3) for v in np.median(ref_ab, axis=0)],
        "outputMedianAb": [round(float(v), 3) for v in np.median(out_ab, axis=0)],
        "perPixelAbErrorMedian": round(float(np.median(per_px)), 3),
        "perPixelAbErrorP95": round(float(np.percentile(per_px, 95)), 3),
        # 모집단 이동은 **기술적 보조값**으로만 남긴다. 이것으로 채점하면 안 된다.
        "populationMedianAbShift": round(float(np.linalg.norm(delta)), 3),
    }
