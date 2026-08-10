"""direct transfer 후보 → **제품 컷으로 승격해도 되는가**. 순수 함수, 부수효과 0.

Phase D 는 재기만 하고 판정하지 않는다(`decision == "unthresholded"`). 이 모듈이 그
측정치를 받아 **권한**을 판정한다. 두 단계를 나눈 이유: 임계가 측정 안에 섞이면 그
임계를 통과시키려고 측정을 손대게 된다.

무엇으로 판정하는가 — **0 이 원리적으로 옳은 규칙만**
------------------------------------------------------
실물 분포가 없으면 연속 지표의 합격선은 지어낸 숫자다. 그래서 이 단계는 **위반 개수가
0 이어야 한다는 규칙**들만 본다. 0 은 튜닝한 임계가 아니라 규칙의 정의다:

  · 칠해도 되는 곳 밖을 칠했는가            → 0 이어야 한다
  · carrier 가 소유한 구조 부위를 덮었는가  → 0
  · 원본 근거 없는 픽셀을 칠했는가          → 0
  · 칠했다고 하지 않은 곳을 바꿨는가        → 0
  · 실루엣 밖으로 샜는가                    → 0
  · 합성으로 설명되지 않는 색이 있는가      → 0
  · 사상이 뒤집힌 픽셀이 있는가             → 0
  · replay 근거가 완전한가 / 기하가 호출자와 맞는가 → 예/아니오

**무엇을 판정하지 않는지도 계약이다**
--------------------------------------
아래는 여기서 게이팅하지 **않는다**. 실물 분포(Phase L) 없이 선을 그으면 그 선은
픽스처에 맞춘 숫자가 된다:

  · `abError*`, `L_high*`, `compositeResidual*` 같은 연속 충실도 지표
  · 몸통 quad 대비 커버리지(원본이 뒷받침하지 못한 면적이 얼마나 되는가)

이 값들은 `metrics` 로 함께 실어 보낸다 — 나중에 분포를 모아 선을 그을 때 쓰기 위해서다.
그때까지 이 단계는 **규칙 위반이 없으면 승격**한다. 규칙을 통과했는데 품질이 나쁜 컷이
있을 수 있고, 그것은 Phase L 이 답할 문제다.

승격 = **제품으로 소비해도 된다**는 뜻이지 "좋다"는 뜻이 아니다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

PROMOTION_VERSION = "direct_transfer_promotion_v1"

#: 보류 사유 — 내부 어휘. 공개 응답에 그대로 싣지 않는다.
REASON_UNMEASURED = "qc_not_measurable"
REASON_CANDIDATE_ARRAYS = "candidate_arrays_unusable"
REASON_PROVENANCE = "provenance_incomplete"
REASON_GEOMETRY = "geometry_does_not_match_caller"
REASON_MAPPING = "mapping_degenerate"
REASON_REFLECTED = "mapping_is_mirrored"
REASON_PAINTED_OUT_OF_BOUNDS = "painted_outside_allowed_region"
REASON_PROTECTED_COMPONENT = "painted_over_protected_component"
REASON_NO_SOURCE_BACKING = "painted_without_source_backing"
REASON_HIDDEN_PAINT = "changed_pixels_not_claimed"
REASON_INCOMPLETE_PAINT = "allowed_region_not_painted"
REASON_SILHOUETTE_LEAK = "painted_outside_garment"
REASON_SELF_REPORT_CONTRADICTS = "self_report_contradicts_pixels"
REASON_ALPHA = "feather_does_not_match_expected"
REASON_BLEND = "composite_not_explicable"
REASON_COLOUR_CATASTROPHE = "transferred_colour_is_not_the_source_colour"
REASON_TEXTURE_DESTROYED = "source_texture_amplitude_destroyed"

# ── 파국 감지선 ─────────────────────────────────────────────────────────────
# 아래 세 값은 **품질 문턱이 아니다**. 품질로 줄을 그으면 픽스처에 맞춘 숫자가 계약이
# 되고, 그건 이 모듈이 피하려던 바로 그 함정이다. 이건 "이 값이면 전송이 일어나지
# 않은 것" 을 뜻하는 정의상의 파국선이고, 정상값에서 아득히 떨어뜨려 둔다.
#
# 근거(정상 전송 실측): 직접 전송은 source 픽셀을 기하 변환으로 **복사**하므로 색 오차가
# 원리상 0 이다 — 측정값 abErrorMedian 0.0, abErrorP95 0.0, compositeResidualP95 0.775,
# L_highAmplitudeRatio 0.9905. 실사진에서는 리샘플링·JPEG 로 몇 단위 오르지만 여전히
# 한 자리수다. 아래 선은 그보다 20~40배 위/아래에 있다.
#
# Lab a/b 평면에서 ΔE 2.3 이 겨우 구분되는 차이다. 중앙값 20 은 "약간 다르다" 가 아니라
# **다른 색**이다. 이 선을 넘겨서 통과시킬 정상 렌더는 존재하지 않는다.
CATASTROPHIC_AB_ERROR_MEDIAN = 20.0
#: 8비트 절대 단위. 정상 0.775 → 40배. 이 이상이면 합성식으로 설명되는 그림이 아니다.
CATASTROPHIC_BLEND_RESIDUAL_P95 = 32.0
#: 고주파 진폭이 원본 대비 이 밑이면 무늬가 **뭉개져 사라진** 것이다(정상 0.99).
#: 고주파 대역이 정의된 경우에만 본다 — 민무늬 원단에서는 이 수가 뜻이 없다.
CATASTROPHIC_HIGH_AMPLITUDE_RATIO = 0.05

#: **0 이 정답인 계수들** — (검사, 키) → 보류 사유. 표로 두는 이유가 있다.
#: 같은 실수를 다섯 번 했다: QC 가 옳게 재고 있는데 승격이 그 값을 읽지 않아 결함이
#: 통과했다(`recordedVsCallerMaxAbs`, `allowedNotPaintedPx`,
#: `alphaNonZeroOutsideGarmentPx`, `paintedMatchesMetrics`, `paintedWithoutAlphaPx`).
#: 이제 새 계수를 QC 에 추가하면 시험이 이 표에 등록하라고 요구한다 — 빠뜨리는 것이
#: 조용한 통과가 아니라 실패가 된다.
ZERO_RULES = (
    ("domain", "paintedOutsideAllowedPx", REASON_PAINTED_OUT_OF_BOUNDS),
    ("domain", "paintedInProtectedComponentPx", REASON_PROTECTED_COMPONENT),
    ("domain", "paintedWithoutSourceBackingPx", REASON_NO_SOURCE_BACKING),
    ("domain", "unclaimedChangedPx", REASON_HIDDEN_PAINT),
    ("domain", "allowedNotPaintedPx", REASON_INCOMPLETE_PAINT),
    # 칠했다면서 alpha 가 0 인 픽셀 — 그 자리는 carrier 가 그대로 남는다.
    ("domain", "paintedWithoutAlphaPx", REASON_INCOMPLETE_PAINT),
    ("containment", "paintedOutsideGarmentPx", REASON_SILHOUETTE_LEAK),
    ("containment", "alphaNonZeroOutsideGarmentPx", REASON_SILHOUETTE_LEAK),
    ("containment", "alphaNonFinitePx", REASON_SILHOUETTE_LEAK),
    ("alpha", "hardEdgePx", REASON_ALPHA),
    ("alpha", "nonZeroWhereExpectedZeroPx", REASON_ALPHA),
    ("blend", "impliedOutOfGamutPx", REASON_BLEND),
    # `negJacobian` 은 **후보의 자기보고**다 — 게이팅하면 후보가 자기 결과를 바꿀 수
    # 있다(실측: 픽셀이 같은데 그 값만 7 로 바꾸면 보류됐고, 호출자 기준 값은 0 이었다).
    # 같은 사실을 호출자 기하에서 다시 센 `torsoNegDetPx` 로만 판정한다.
    ("mapping", "torsoNegDetPx", REASON_REFLECTED),
    # 부위별 계수도 **직접** 센다 — dict 안에 숨긴 값이 그물을 빠져나가지 않게.
    ("mapping", "componentNegDetPx", REASON_REFLECTED),
    ("reconstruction", "domainNotReconstructedPx", REASON_UNMEASURED),
)

#: alpha 는 float32 로 계산되므로 정확히 0 을 요구하면 반올림에 걸린다. 품질 손잡이가
#: 아니라 **부동소수 반올림 폭**이다.
_ALPHA_EPS = 1e-3

#: 기록된 사상과 호출자 사상의 정규화 차. 옳은 값은 **0** 이고 이 폭은 부동소수 잡음이다.
_HOMOGRAPHY_EPS = 1e-6


@dataclass(frozen=True)
class PromotionVerdict:
    promoted: bool
    reasons: tuple = field(default_factory=tuple)
    metrics: dict = field(default_factory=dict)
    version: str = PROMOTION_VERSION


def _nonzero(check: dict | None, key: str) -> bool:
    """값이 **0 이 아니면** True. 없거나(None) 셀 수 없으면 그것도 위반으로 읽는다.

    `!= 0` 을 그대로 쓰면 numpy 배열이 실려 왔을 때 모호한 진리값으로 터진다 — 후보가
    자기보고 하나로 승격 판정을 예외로 없앨 수 있다는 뜻이다.
    """
    value = (check or {}).get(key)
    if value is None:
        return True
    try:
        if isinstance(value, dict):
            # 이름별 계수 묶음 — **어느 하나라도** 0 이 아니면 위반이다.
            return any(_nonzero({k: v}, k) for k, v in value.items()) if value else False
        arr = np.asarray(value)
        if arr.size == 0:
            return True
        # 배열이면 **어느 원소라도** 0 이 아니면 위반이다. 첫 원소만 보면 [0, 5] 가
        # 통과한다.
        return bool(np.any(arr != 0))
    except Exception:
        return True


def _int(check: dict | None, key: str):
    return (check or {}).get(key)


def _float(check: dict | None, key: str) -> tuple[float | None, bool]:
    """→ (읽힌 값 | None, **실려 있는데 못 읽었는가**).

    두 번째 항이 따로 필요하다. `NaN` 은 어떤 비교에도 False 를 주므로 그냥 `None` 과
    똑같이 다루면 파국이 '검사했고 통과' 로 둔갑한다. 비교가 False 로 떨어지는 값은
    검사한 것이 아니다 — 없는 것과 못 읽는 것을 구분해야 한다.
    """
    if check is None or key not in check:
        return None, False
    value = check.get(key)
    if value is None:
        return None, False
    if isinstance(value, bool):
        return None, True
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None, True
    return (out, False) if math.isfinite(out) else (None, True)


def evaluate_direct_transfer_promotion(report) -> PromotionVerdict:
    """`DirectTransferQC` → 이 후보를 제품 컷으로 써도 되는가.

    측정이 없으면 승격하지 않는다. "재지 못했다"는 "괜찮다"가 아니다 — 이 방향으로
    기울지 않는 것이 이 파이프라인이 반복해서 틀렸던 지점이다.
    """
    checks = getattr(report, "checks", None)
    if not isinstance(checks, dict) or not checks:
        return PromotionVerdict(False, (REASON_UNMEASURED,))
    # QC 가 입력 자체를 거부했으면(`inputs`) 측정이 아예 없다.
    if "inputs" in checks or "shapes" in checks:
        return PromotionVerdict(False, (REASON_UNMEASURED,))

    reasons: list[str] = []
    arrays = checks.get("candidateArrays") or {}
    if arrays.get("usable") is not True:
        reasons.append(REASON_CANDIDATE_ARRAYS)

    if (checks.get("provenance") or {}).get("complete") is not True:
        reasons.append(REASON_PROVENANCE)

    geom = checks.get("geometry") or {}
    if not (geom.get("computable") and geom.get("quadsMatchCaller") is True
            and geom.get("recordedHomographyUsable") is True):
        reasons.append(REASON_GEOMETRY)
    else:
        # QC 는 이 차이를 **재고 있었는데** 승격이 읽지 않았다. quad 가 같아도 기록된
        # 사상이 다르면 replay 가 다른 그림을 낸다(실측: 평행이동 100 px 이 기록돼
        # `recordedVsCallerMaxAbs=100.0` 인데 승격됐다).
        delta = geom.get("recordedVsCallerMaxAbs")
        if delta is None or float(delta) > _HOMOGRAPHY_EPS:
            reasons.append(REASON_GEOMETRY)

    mapping = checks.get("mapping") or {}
    if mapping.get("computable") is not True:
        reasons.append(REASON_MAPPING)
    # **뒤집힌 사상**은 부위를 거울상으로 그린다. 몸통의 `negJacobian` 은 이것을 못 본다.
    if mapping.get("torsoReflected") is not False or mapping.get("reflectedComponents"):
        reasons.append(REASON_REFLECTED)

    domain = checks.get("domain") or {}
    if domain.get("computable") is not True:
        reasons.append(REASON_UNMEASURED)
    # `paintedMatchesMetrics` 는 **자기보고 대 자기보고**다(`painted` 배열 대
    # `metrics.paintedPx`). 둘이 어긋나는 것은 생산자의 내부 불일치 신호이지 **픽셀이
    # 틀렸다는 뜻이 아니다** — 픽셀은 allowed/unclaimed/근거 규칙이 따로 구속한다.
    # 이것으로 게이팅하면 픽셀이 완벽한 렌더가 계수 하나 때문에 거절된다(실측: 같은
    # 픽셀·alpha·provenance 인데 paintedPx 를 0 으로 적으면 거절). 진단으로만 남긴다.

    # **0 이 정답인 계수는 전부 표에서 읽는다.** 개별 if 로 흩어 두면 하나를 빠뜨려도
    # 조용히 통과한다 — 실제로 다섯 번 그랬다.
    for section, key, reason in ZERO_RULES:
        check = checks.get(section) or {}
        # 계산 자체가 불가능한 검사는 그 검사의 자기 사유가 이미 붙는다.
        if section in ("domain",) and check.get("computable") is not True:
            continue
        # 키가 없으면 **건너뛰지 않는다** — 규칙이 조용히 사라지는 통로였다. QC 는
        # 빈 구역에서도 계수를 0 으로 낸다.
        if _nonzero(check, key):
            reasons.append(reason)

    cont = checks.get("containment") or {}
    if (cont.get("outsideGarmentUntouched") is not True
            or cont.get("alphaFinite") is not True):
        reasons.append(REASON_SILHOUETTE_LEAK)

    alpha = checks.get("alpha") or {}
    if alpha.get("computable") is not True:
        reasons.append(REASON_ALPHA)
    else:
        delta = alpha.get("maxAbsDeltaVsExpected")
        if delta is None or float(delta) > _ALPHA_EPS:
            reasons.append(REASON_ALPHA)

    # **빈 구역은 측정이 필요 없다.** 넓은 깃털이 좁은 패널을 다 덮으면 내부 픽셀이 0 이
    # 되고, 그때 재구성이 '계산 불가'인 것은 결함이 아니라 그 구역이 없다는 뜻이다
    # (실측: 648 개 정상 렌더 중 36 개가 narrow quad + band 24 에서 그렇다).
    # 구역 크기는 `allowed` 와 호출자 기하에서 나오므로 후보가 조작할 수 없다.
    interior_px = _int(domain, "interiorPx")
    ramp_px = _int(domain, "rampPx")
    if not (interior_px or ramp_px):
        reasons.append(REASON_UNMEASURED)

    blend = checks.get("blend") or {}
    if blend.get("computable") is not True:
        if ramp_px:
            reasons.append(REASON_BLEND)
    else:
        # 색역 밖 = 이 alpha 로는 설명되지 않는 색. 모드를 몰라 기대 합성값을 못 만든
        # 경우(`compositeResidualMedian is None`)도 **검증되지 않은 것**이다.
        if blend.get("compositeResidualMedian") is None:
            reasons.append(REASON_BLEND)

    recon = checks.get("reconstruction") or {}
    if recon.get("computable") is not True and interior_px:
        reasons.append(REASON_UNMEASURED)

    # 파국 감지 — 연속 지표로 **품질**을 재지는 않지만, "전송이 아예 일어나지 않았다"는
    # 것은 연속 지표로만 보인다. 마스크·알파·계보·기하가 전부 자기일관이면서 내부 색만
    # 전부 틀린 렌더는 위의 0-계수 규칙을 하나도 건드리지 않고 통과한다. 그 구멍을 막되,
    # 정상값에서 아득히 떨어진 선에서만 막는다(위 상수 근거 참조).
    ab_median, ab_bad = _float(recon, "abErrorMedian")
    if ab_bad:
        reasons.append(REASON_UNMEASURED)
    elif ab_median is not None and ab_median > CATASTROPHIC_AB_ERROR_MEDIAN:
        reasons.append(REASON_COLOUR_CATASTROPHE)

    residual_p95, residual_bad = _float(blend, "compositeResidualP95")
    if residual_bad:
        reasons.append(REASON_BLEND)
    elif residual_p95 is not None and residual_p95 > CATASTROPHIC_BLEND_RESIDUAL_P95:
        reasons.append(REASON_BLEND)

    # 고주파 대역이 **정의된** 경우에만 본다. 민무늬 원단은 진폭비가 뜻이 없고,
    # 거기에 선을 그으면 멀쩡한 무지 옷을 떨어뜨린다.
    if recon.get("L_highBandDefined") is True:
        amp, amp_bad = _float(recon, "L_highAmplitudeRatio")
        if amp_bad:
            reasons.append(REASON_UNMEASURED)
        elif amp is not None and amp < CATASTROPHIC_HIGH_AMPLITUDE_RATIO:
            reasons.append(REASON_TEXTURE_DESTROYED)

    # 게이팅하지 않지만 **분포를 모으기 위해** 함께 낸다(Phase L 이 선을 그을 재료).
    metrics = {
        "abErrorMedian": recon.get("abErrorMedian"),
        "abErrorP95": recon.get("abErrorP95"),
        "highBandDefined": recon.get("L_highBandDefined"),
        "highAmplitudeRatio": recon.get("L_highAmplitudeRatio"),
        "compositeResidualP95": blend.get("compositeResidualP95"),
        "interiorPx": domain.get("interiorPx"),
        "rampPx": domain.get("rampPx"),
        "paintedFracOfAllowed": domain.get("paintedFracOfAllowed"),
        "allowedDomainPx": domain.get("allowedDomainPx"),
    }
    ordered = tuple(dict.fromkeys(reasons))     # 순서 유지, 중복 제거
    return PromotionVerdict(not ordered, ordered, metrics)
