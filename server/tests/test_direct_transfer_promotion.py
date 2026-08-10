"""승격 판정 — 규칙 위반 0 이면 승격, 아니면 이름 붙여 보류.

이 스위트가 지키는 것:
  1. **옳은 렌더는 승격된다** — 안 그러면 게이트가 파이프라인을 막는 장치가 된다.
  2. **각 규칙이 단독으로 승격을 막는다** — 하나라도 막지 못하면 그 규칙은 장식이다.
  3. 측정이 없으면 승격하지 않는다 — "재지 못했다"는 "괜찮다"가 아니다.
"""

import dataclasses

import numpy as np
import pytest

from app.services.hybrid_composite import direct_torso_transfer as dtt
from app.services.hybrid_composite import direct_transfer_promotion as promo
from app.services.hybrid_composite import direct_transfer_qc as qc

from test_direct_torso_transfer import (
    FOUR_COLOUR, IDENTITY_QUAD, carrier, landmarks_for, make_panel_map,
    source_with_margin)


def _report(**kw):
    src, smask, m = source_with_margin(FOUR_COLOUR)
    pm = make_panel_map(IDENTITY_QUAD, w=500, h=700)
    lm = landmarks_for(np.float32([[m, m], [499 - m, m], [499 - m, 699 - m], [m, 699 - m]]),
                       w=500, h=700)
    car = carrier(500, 700)
    cand = dtt.transfer_torso_texture(car, pm, src, source_landmarks=lm,
                                      source_garment_mask=smask,
                                      shading=dtt.SHADING_RAW_SOURCE)
    cand = dataclasses.replace(cand, **kw) if kw else cand
    return qc.evaluate_direct_transfer(
        cand, carrier_bgr=car, source_bgr=src, panel_map=pm, source_landmarks=lm,
        shading=dtt.SHADING_RAW_SOURCE, source_garment_mask=smask), cand, car, src, pm, lm


def test_a_correct_render_is_promoted():
    """게이트가 정상 산출물을 막으면 그것은 게이트가 아니라 고장이다."""
    report, *_ = _report()
    v = promo.evaluate_direct_transfer_promotion(report)
    assert v.promoted is True, v.reasons
    assert v.reasons == ()
    # 연속 지표는 **게이팅하지 않지만** 함께 실려 나온다(Phase L 재료).
    assert v.metrics["abErrorMedian"] is not None
    assert v.metrics["interiorPx"] > 0


def test_nothing_measured_is_not_a_pass():
    """이 파이프라인이 반복해서 틀렸던 방향 — 무측정을 통과로 읽는 것."""
    for empty in (None, object(), dataclasses.replace(_report()[0], checks={})):
        v = promo.evaluate_direct_transfer_promotion(empty)
        assert v.promoted is False
        assert promo.REASON_UNMEASURED in v.reasons


def test_a_refused_input_is_not_a_pass():
    """QC 가 입력을 거부했으면 측정이 없다 — 승격 대상이 아니다."""
    report, cand, car, src, pm, lm = _report()
    refused = qc.evaluate_direct_transfer(
        dataclasses.replace(cand, image_bgr=cand.image_bgr.astype(np.float32)),
        carrier_bgr=car, source_bgr=src, panel_map=pm, source_landmarks=lm,
        shading=dtt.SHADING_RAW_SOURCE)
    v = promo.evaluate_direct_transfer_promotion(refused)
    assert v.promoted is False
    assert promo.REASON_UNMEASURED in v.reasons


# ── 규칙마다 **단독으로** 승격을 막는가 ────────────────────────────────────
@pytest.mark.parametrize("check,patch,reason", [
    ("candidateArrays", {"usable": False}, promo.REASON_CANDIDATE_ARRAYS),
    ("provenance", {"complete": False}, promo.REASON_PROVENANCE),
    ("geometry", {"quadsMatchCaller": False}, promo.REASON_GEOMETRY),
    ("geometry", {"recordedHomographyUsable": False}, promo.REASON_GEOMETRY),
    # `negJacobian` 은 자기보고라 게이팅하지 않는다 — 같은 사실을 호출자 기하에서
    # 다시 센 값으로 판정한다.
    ("mapping", {"torsoNegDetPx": 12}, promo.REASON_REFLECTED),
    ("mapping", {"componentNegDetPx": {"p": 3}}, promo.REASON_REFLECTED),
    ("mapping", {"computable": False}, promo.REASON_MAPPING),
    ("domain", {"paintedOutsideAllowedPx": 1}, promo.REASON_PAINTED_OUT_OF_BOUNDS),
    ("domain", {"paintedInProtectedComponentPx": 1}, promo.REASON_PROTECTED_COMPONENT),
    ("domain", {"paintedWithoutSourceBackingPx": 1}, promo.REASON_NO_SOURCE_BACKING),
    # 원본 마스크가 없어 **검증되지 않은** 경우도 통과가 아니다.
    ("domain", {"paintedWithoutSourceBackingPx": None}, promo.REASON_NO_SOURCE_BACKING),
    ("domain", {"unclaimedChangedPx": 1}, promo.REASON_HIDDEN_PAINT),
    ("containment", {"paintedOutsideGarmentPx": 1}, promo.REASON_SILHOUETTE_LEAK),
    ("containment", {"outsideGarmentUntouched": False}, promo.REASON_SILHOUETTE_LEAK),
    ("containment", {"alphaFinite": False}, promo.REASON_SILHOUETTE_LEAK),
    ("alpha", {"maxAbsDeltaVsExpected": 0.4}, promo.REASON_ALPHA),
    ("alpha", {"hardEdgePx": 3}, promo.REASON_ALPHA),
    ("alpha", {"computable": False}, promo.REASON_ALPHA),
    ("blend", {"impliedOutOfGamutPx": 1}, promo.REASON_BLEND),
    ("blend", {"compositeResidualMedian": None}, promo.REASON_BLEND),
    ("blend", {"computable": False}, promo.REASON_BLEND),
    ("reconstruction", {"computable": False}, promo.REASON_UNMEASURED),
])
def test_each_rule_alone_blocks_promotion(check, patch, reason):
    report, *_ = _report()
    assert promo.evaluate_direct_transfer_promotion(report).promoted is True
    checks = {k: dict(v) for k, v in report.checks.items()}
    checks[check].update(patch)
    v = promo.evaluate_direct_transfer_promotion(
        dataclasses.replace(report, checks=checks))
    assert v.promoted is False, (check, patch)
    assert reason in v.reasons, (check, patch, v.reasons)


def test_a_real_rule_violation_blocks_promotion_end_to_end():
    """합성한 dict 가 아니라 **실제로 규칙을 어긴 렌더**로도 막혀야 한다."""
    report, cand, car, src, pm, lm = _report()
    # 실루엣 밖을 칠하고 그것을 painted 로 주장한다.
    dirty = cand.image_bgr.copy()
    outside = np.nonzero(pm.garment_mask == 0)
    dirty[outside[0][:200], outside[1][:200]] = (0, 255, 0)
    painted = cand.painted.copy()
    painted[outside[0][:200], outside[1][:200]] = 255
    bad = qc.evaluate_direct_transfer(
        dataclasses.replace(cand, image_bgr=dirty, painted=painted),
        carrier_bgr=car, source_bgr=src, panel_map=pm, source_landmarks=lm,
        shading=dtt.SHADING_RAW_SOURCE,
        source_garment_mask=source_with_margin(FOUR_COLOUR)[1])
    v = promo.evaluate_direct_transfer_promotion(bad)
    assert v.promoted is False
    assert promo.REASON_SILHOUETTE_LEAK in v.reasons


def _with(section: str, **updates):
    report, *_ = _report()
    checks = {k: dict(v) for k, v in report.checks.items()}
    checks[section].update(updates)
    return dataclasses.replace(report, checks=checks)


def test_ordinary_fidelity_variation_is_recorded_but_does_not_gate():
    """품질로 줄을 그으면 픽스처에 맞춘 숫자가 계약이 된다 — 보통 편차는 기록만 한다.

    정상 렌더 실측이 abError 0.0 / residual 0.775 이므로, 아래 값들은 실사진에서 흔한
    리샘플링·JPEG 수준보다 이미 넉넉히 크다. 그런데도 통과해야 한다 — 파국선은 훨씬 위다.
    """
    v = promo.evaluate_direct_transfer_promotion(
        _with("reconstruction", abErrorMedian=8.0, abErrorP95=15.0))
    assert v.promoted is True, v.reasons
    assert v.metrics["abErrorMedian"] == 8.0


def test_a_colour_catastrophe_does_not_get_product_authority():
    """마스크·알파·계보·기하가 전부 자기일관인데 내부 색만 전부 틀린 렌더.

    0-계수 규칙은 하나도 건드리지 않는다 — 연속 지표만이 "전송이 일어나지 않았다"를
    보여 준다. 이것이 통과하면 결정론 폴백이 원단 진실이 아닌 그림에 권한을 준다.
    """
    v = promo.evaluate_direct_transfer_promotion(
        _with("reconstruction", abErrorMedian=99.0, abErrorP95=120.0))
    assert v.promoted is False
    assert promo.REASON_COLOUR_CATASTROPHE in v.reasons
    assert v.metrics["abErrorMedian"] == 99.0        # 그래도 값은 실려 나온다


def test_an_unexplainable_composite_does_not_get_product_authority():
    v = promo.evaluate_direct_transfer_promotion(
        _with("blend", compositeResidualP95=80.0))
    assert v.promoted is False
    assert promo.REASON_BLEND in v.reasons


def test_destroyed_texture_does_not_get_product_authority():
    """무늬가 뭉개져 사라진 렌더 — 색은 맞는데 패턴이 없다."""
    v = promo.evaluate_direct_transfer_promotion(
        _with("reconstruction", L_highBandDefined=True, L_highAmplitudeRatio=0.01))
    assert v.promoted is False
    assert promo.REASON_TEXTURE_DESTROYED in v.reasons


def test_a_plain_fabric_is_not_punished_for_having_no_high_band():
    """민무늬 원단은 진폭비가 뜻이 없다 — 거기에 선을 그으면 멀쩡한 옷을 떨어뜨린다."""
    v = promo.evaluate_direct_transfer_promotion(
        _with("reconstruction", L_highBandDefined=False, L_highAmplitudeRatio=0.0))
    assert v.promoted is True, v.reasons


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"),
                                 "not-a-number", True, [1.0]])
def test_an_unreadable_fidelity_value_is_not_treated_as_a_pass(bad):
    """`nan > limit` 은 False 다 — 비교가 False 로 떨어지는 값은 검사한 것이 아니다.

    `True` 도 거절한다: bool 은 측정값이 아니고, 파이썬에서 `True > 20.0` 은 조용히
    False 를 준다.
    """
    v = promo.evaluate_direct_transfer_promotion(
        _with("reconstruction", abErrorMedian=bad))
    assert v.promoted is False, (bad, v.reasons)


def test_a_numeric_string_is_still_a_number():
    """읽을 수 있는 값을 '못 읽었다'고 우기지는 않는다 — 과잉 거절도 결함이다."""
    v = promo.evaluate_direct_transfer_promotion(
        _with("reconstruction", abErrorMedian="8"))
    assert v.promoted is True, v.reasons


def test_the_catastrophic_lines_sit_far_from_measured_healthy_values():
    """선이 정상값 바로 위에 있으면 그건 파국선이 아니라 품질 문턱이다.

    정상 실측: abErrorMedian 0.0, compositeResidualP95 0.775, 진폭비 0.9905.
    """
    report, *_ = _report()
    recon, blend = report.checks["reconstruction"], report.checks["blend"]
    assert recon["abErrorMedian"] < promo.CATASTROPHIC_AB_ERROR_MEDIAN / 10
    assert blend["compositeResidualP95"] < promo.CATASTROPHIC_BLEND_RESIDUAL_P95 / 10
    assert recon["L_highAmplitudeRatio"] > promo.CATASTROPHIC_HIGH_AMPLITUDE_RATIO * 10


def test_reasons_are_named_and_deduplicated():
    report, *_ = _report()
    checks = {k: dict(v) for k, v in report.checks.items()}
    checks["domain"].update({"paintedOutsideAllowedPx": 5, "unclaimedChangedPx": 7})
    v = promo.evaluate_direct_transfer_promotion(
        dataclasses.replace(report, checks=checks))
    assert v.promoted is False
    assert len(v.reasons) == len(set(v.reasons))
    assert set(v.reasons) == {promo.REASON_PAINTED_OUT_OF_BOUNDS,
                             promo.REASON_HIDDEN_PAINT}


def test_promotion_does_not_invent_thresholds():
    """구조로 고정한다: 이 모듈에는 튜닝 가능한 품질 상수가 없다."""
    import inspect
    consts = {k: v for k, v in vars(promo).items()
              if k.startswith("_") and k.isupper()
              and isinstance(v, (int, float)) and not isinstance(v, bool)}
    # 수치 상수는 전부 **부동소수 잡음 폭**이어야 한다 — 품질 손잡이가 아니다.
    # 옳은 값이 0 인 양을 비교할 때 필요한 여유이지, 통과선을 옮기는 값이 아니다.
    assert set(consts) == {"_ALPHA_EPS", "_HOMOGRAPHY_EPS"}, consts
    for name, value in consts.items():
        assert 0 < value <= 1e-3, (name, value)
    src = inspect.getsource(promo.evaluate_direct_transfer_promotion)
    for banned in ("0.9", "0.8", "0.7", "0.5 ", "percentile", "mean("):
        assert banned not in src, banned


def test_an_empty_zone_needs_no_measurement_but_no_zone_at_all_is_not_a_pass():
    """넓은 깃털이 좁은 패널을 다 덮으면 내부가 0 이 된다 — 그때 '계산 불가'는 결함이 아니다.

    실측: 648 개 정상 렌더 중 36 개(narrow quad + band 24)가 내부 0 이었고, 그것만으로
    보류되면 게이트가 정상 산출물을 막는다. 반대로 **두 구역이 다 비면** 잰 것이 없다.
    """
    report, *_ = _report()
    checks = {k: dict(v) for k, v in report.checks.items()}
    checks["domain"].update({"interiorPx": 0})
    checks["reconstruction"].update({"computable": False})
    v = promo.evaluate_direct_transfer_promotion(
        dataclasses.replace(report, checks=checks))
    assert v.promoted is True, v.reasons

    checks["domain"].update({"interiorPx": 0, "rampPx": 0})
    checks["blend"].update({"computable": False})
    v2 = promo.evaluate_direct_transfer_promotion(
        dataclasses.replace(report, checks=checks))
    assert v2.promoted is False
    assert promo.REASON_UNMEASURED in v2.reasons


def test_a_mirrored_component_mapping_is_never_promoted():
    """부위 박스 순서를 뒤집으면 플래킷·단추가 거울상이 된다.

    실측(v7 초안): 행렬식 -1.0, 36,120 px 이 달라졌는데 모든 검사가 깨끗했고 승격까지
    됐다. 몸통의 `negJacobian` 은 부위 사상을 보지 않는다.
    """
    from app.services.hybrid_composite import direct_transfer_gate as gate
    src, smask, m = source_with_margin(FOUR_COLOUR)
    pm = make_panel_map(IDENTITY_QUAD, w=500, h=700)
    lm = landmarks_for(IDENTITY_QUAD, w=500, h=700)
    car = carrier(500, 700)
    sbox = np.float32([[150, 200], [350, 200], [350, 500], [150, 500]])
    upright = np.float32([[150, 200], [350, 200], [350, 500], [150, 500]])
    mirrored = np.float32([[350, 200], [150, 200], [150, 500], [350, 500]])

    good = gate.run_gated_direct_transfer(
        car, pm, src, source_landmarks=lm, source_garment_mask=smask,
        carrier_component_boxes={"p": upright}, source_component_boxes={"p": sbox},
        shading=dtt.SHADING_RAW_SOURCE)
    assert good.promoted is True, good.reasons

    bad = gate.run_gated_direct_transfer(
        car, pm, src, source_landmarks=lm, source_garment_mask=smask,
        carrier_component_boxes={"p": mirrored}, source_component_boxes={"p": sbox},
        shading=dtt.SHADING_RAW_SOURCE)
    assert bad.promoted is False, bad.reasons
    assert promo.REASON_REFLECTED in bad.reasons
    assert bad.image_bgr is None


def test_a_recorded_mapping_that_differs_from_the_caller_blocks_promotion():
    """QC 는 이 차이를 **재고 있었는데** 승격이 읽지 않았다(실측: 100.0 인데 승격)."""
    report, cand, car, src, pm, lm = _report()
    assert promo.evaluate_direct_transfer_promotion(report).promoted is True
    forged = qc.evaluate_direct_transfer(
        dataclasses.replace(cand, provenance={
            **cand.provenance,
            "homography": [[1.0, 0.0, 100.0], [0.0, 1.0, 50.0], [0.0, 0.0, 1.0]]}),
        carrier_bgr=car, source_bgr=src, panel_map=pm, source_landmarks=lm,
        shading=dtt.SHADING_RAW_SOURCE,
        source_garment_mask=source_with_margin(FOUR_COLOUR)[1])
    assert forged.checks["geometry"]["recordedVsCallerMaxAbs"] > 1.0
    v = promo.evaluate_direct_transfer_promotion(forged)
    assert v.promoted is False
    assert promo.REASON_GEOMETRY in v.reasons


def test_an_unpainted_allowed_region_blocks_promotion():
    """`allowed` 는 이미 유효·근거 있음·보호되지 않음을 통과한 픽셀이다.

    실측(v8 초안): 10,000 px 구멍을 내고 painted 에서도 지우면 다른 모든 검사가 깨끗했고
    (숨긴 칠 0, 근거 없는 칠 0, alpha 0.0, 재구성 0.0) 승격까지 됐다. 그 자리는 carrier 가
    그대로 남는다 — 전송이 **덜 된** 것이다.
    """
    report, cand, car, src, pm, lm = _report()
    assert promo.evaluate_direct_transfer_promotion(report).promoted is True
    image = cand.image_bgr.copy()
    image[250:350, 200:300] = car[250:350, 200:300]
    painted = cand.painted.copy()
    painted[250:350, 200:300] = 0
    holed = qc.evaluate_direct_transfer(
        dataclasses.replace(cand, image_bgr=image, painted=painted),
        carrier_bgr=car, source_bgr=src, panel_map=pm, source_landmarks=lm,
        shading=dtt.SHADING_RAW_SOURCE,
        source_garment_mask=source_with_margin(FOUR_COLOUR)[1])
    assert holed.checks["domain"]["allowedNotPaintedPx"] >= 9_000
    v = promo.evaluate_direct_transfer_promotion(holed)
    assert v.promoted is False
    assert promo.REASON_INCOMPLETE_PAINT in v.reasons


@pytest.mark.parametrize("value", [np.array([0, 0]), np.array([0, 5]), np.array([]),
                                   "nonsense", object()])
def test_self_reported_telemetry_cannot_raise_out_of_a_verdict(value):
    """후보가 자기보고 하나로 승격 판정을 예외로 없앨 수 없다."""
    report, *_ = _report()
    checks = {k: dict(v) for k, v in report.checks.items()}
    checks["mapping"].update({"torsoNegDetPx": value})
    v = promo.evaluate_direct_transfer_promotion(
        dataclasses.replace(report, checks=checks))     # 예외 없이 돌아와야 한다
    assert isinstance(v.promoted, bool)
    # 0 이 아닌 원소가 있거나 읽을 수 없으면 **위반으로** 읽는다.
    if not (isinstance(value, np.ndarray) and value.size and not value.any()):
        assert v.promoted is False, value


def test_a_faint_alpha_leak_outside_the_garment_blocks_promotion():
    """실루엣 밖 alpha 는 **정확히 0** 이다 — 부동소수 여유를 주면 누출이 통과한다.

    실측(v9 초안): 밖의 10,000 px 을 0.0009 로 두면 최댓값 비교가 1e-3 여유에 걸려
    모든 검사가 깨끗했고 승격됐다. 개수는 그런 여유를 갖지 않는다.
    """
    report, cand, car, src, pm, lm = _report()
    alpha = np.asarray(cand.alpha, np.float32).copy()
    oy, ox = np.nonzero(pm.garment_mask == 0)
    alpha[oy[:10_000], ox[:10_000]] = 0.0009
    leaked = qc.evaluate_direct_transfer(
        dataclasses.replace(cand, alpha=alpha), carrier_bgr=car, source_bgr=src,
        panel_map=pm, source_landmarks=lm, shading=dtt.SHADING_RAW_SOURCE,
        source_garment_mask=source_with_margin(FOUR_COLOUR)[1])
    assert leaked.checks["containment"]["alphaNonZeroOutsideGarmentPx"] == 10_000
    v = promo.evaluate_direct_transfer_promotion(leaked)
    assert v.promoted is False
    assert promo.REASON_SILHOUETTE_LEAK in v.reasons


def test_a_stale_self_reported_count_does_not_refuse_correct_pixels():
    """`paintedMatchesMetrics` 는 **자기보고 대 자기보고**다 — 픽셀의 진실이 아니다.

    이것으로 게이팅하면 픽셀·alpha·provenance 가 완벽한 렌더가 계수 하나 때문에
    거절된다. 픽셀은 allowed/unclaimed/근거 규칙이 따로 구속하므로, 내부 불일치는
    **진단으로만** 남긴다. (이전 판본은 이것을 게이팅했고, 그래서 옳은 렌더가 막혔다.)
    """
    report, cand, car, src, pm, lm = _report()
    stale = qc.evaluate_direct_transfer(
        dataclasses.replace(cand, metrics={**cand.metrics, "paintedPx": 0}),
        carrier_bgr=car, source_bgr=src, panel_map=pm, source_landmarks=lm,
        shading=dtt.SHADING_RAW_SOURCE,
        source_garment_mask=source_with_margin(FOUR_COLOUR)[1])
    assert stale.checks["domain"]["paintedMatchesMetrics"] is False   # 관측은 남는다
    v = promo.evaluate_direct_transfer_promotion(stale)
    assert v.promoted is True, v.reasons                              # 판정은 흔들리지 않는다


def test_a_painted_pixel_without_alpha_is_caught_by_the_measurement_itself():
    """보고서를 손으로 고쳐 넣지 말고, **실제로 그 상황을 만들어** 확인한다.

    실측(v16 초안): 측정을 0 으로 고정하는 변종이 184개 시험을 전부 통과했다 — 기존
    시험이 이미 만들어진 보고서를 패치했기 때문이다.
    """
    src, smask, m = source_with_margin(FOUR_COLOUR)
    base = make_panel_map(IDENTITY_QUAD, w=500, h=700)
    pm = dataclasses.replace(base, metrics={**base.metrics, "boundary_band_px": 1000})
    lm = landmarks_for(IDENTITY_QUAD, w=500, h=700)
    car = carrier(500, 700)
    cand = dtt.transfer_torso_texture(car, pm, src, source_landmarks=lm,
                                      source_garment_mask=smask,
                                      shading=dtt.SHADING_RAW_SOURCE)

    def run(c):
        return qc.evaluate_direct_transfer(
            c, carrier_bgr=car, source_bgr=src, panel_map=pm, source_landmarks=lm,
            shading=dtt.SHADING_RAW_SOURCE, source_garment_mask=smask)

    assert promo.evaluate_direct_transfer_promotion(run(cand)).promoted is True
    alpha = np.asarray(cand.alpha, np.float32).copy()
    tiny = (cand.painted > 0) & (alpha > 0) & (alpha < 0.001)
    assert int(tiny.sum()) > 1000, int(tiny.sum())
    alpha[tiny] = 0.0
    holed = run(dataclasses.replace(cand, alpha=alpha))
    assert holed.checks["domain"]["paintedWithoutAlphaPx"] == int(tiny.sum())
    v = promo.evaluate_direct_transfer_promotion(holed)
    assert v.promoted is False
    assert promo.REASON_INCOMPLETE_PAINT in v.reasons


#: **위반이 아닌** 계수 — 크기·문맥·자기보고. 각각 왜 게이팅하지 않는지 이유가 있어야
#: 한다. 새 계수를 QC 에 추가하면 이 목록이나 `ZERO_RULES` 중 하나에 넣어야 하고,
#: 그러지 않으면 아래 시험이 실패한다.
NOT_A_VIOLATION = {
    ("domain", "candidatePaintedPx"): "크기",
    ("domain", "metricsPaintedPx"): "자기보고 — 진단용, 게이팅하지 않는다",
    ("domain", "claimedUnchangedPx"): "원본색과 carrier 색이 같으면 정상적으로 생긴다",
    ("domain", "allowedDomainPx"): "크기",
    ("domain", "interiorPx"): "크기",
    ("domain", "rampPx"): "크기",
    ("domain", "unclaimedChangedInAllowedPx"): "unclaimedChangedPx 의 부분집합",
    ("alpha", "expectedRampPx"): "크기",
    ("blend", "rampPx"): "크기",
    ("blend", "rampComponentEdgePx"): "문맥 — 원인이 아니다",
    ("blend", "impliedOutOfGamutFrac"): "impliedOutOfGamutPx 와 같은 사실",
    ("reconstruction", "comparedPx"): "크기",
    ("containment", "alphaMaxOutsideGarment"): "alphaNonZeroOutsideGarmentPx 가 센다",
    ("provenance", "requiredKeys"): "필요 키의 **개수** — 위반 계수가 아니다",
    ("mapping", "negJacobian"): "자기보고 — 호출자 기준 torsoNegDetPx 로 판정한다",
    ("mapping", "callerStretchOverFrac"): "연속 지표 — 실물 분포(Phase L) 전까지 기록만",
    ("mapping", "stretchOverFrac"): "자기보고 텔레메트리(합의된 echo)",
    ("sourceBacking", "paintedPx"): "자기보고 텔레메트리",
    ("sourceBacking", "backgroundRejectedPx"): "자기보고 — 정상 렌더에서도 0 이 아니다",
    ("sourceBacking", "sourceStructureRejectedPx"): "자기보고 — 정상 배제 개수",
    ("sourceBacking", "outOfSourceFrac"): "자기보고 텔레메트리",
    ("geometry", "sourceQuadMaxDeltaPx"): "quadsMatchCaller 가 판정한다",
    ("geometry", "targetQuadMaxDeltaPx"): "quadsMatchCaller 가 판정한다",
    ("reconstruction", "abWithin2Frac"): "연속 지표 — Phase L",
    ("reconstruction", "sigmaPx"): "분할 폭(설정값)",
    ("colour", "comparedPx"): "크기",
    ("components", "filledPx"): "자기보고 텔레메트리",
    ("components", "targetPx"): "자기보고 텔레메트리",
    ("components", "outsideTorsoQuadPx"): "자기보고 — 부위는 몸통 quad 밖일 수 있다",
    ("components", "fill"): "자기보고 텔레메트리 묶음(합의된 echo)",
    ("components", "placement"): "자기보고 텔레메트리 묶음(합의된 echo)",
}


def _report_with_component():
    """부위가 있는 보고서 — 부위 전용 계수도 훑기 위해서다."""
    from test_direct_torso_transfer import _source_with_structure
    src, smask, m, sbox = _source_with_structure(FOUR_COLOUR)
    pm = make_panel_map(IDENTITY_QUAD, w=500, h=700)
    lm = landmarks_for(IDENTITY_QUAD, w=500, h=700)
    car = carrier(500, 700)
    cboxes = {"p": np.float32([[300, 80], [352, 80], [352, 620], [300, 620]])}
    sboxes = {"p": sbox}
    cand = dtt.transfer_torso_texture(
        car, pm, src, source_landmarks=lm, source_garment_mask=smask,
        carrier_component_boxes=cboxes, source_component_boxes=sboxes,
        shading=dtt.SHADING_RAW_SOURCE)
    return qc.evaluate_direct_transfer(
        cand, carrier_bgr=car, source_bgr=src, panel_map=pm, source_landmarks=lm,
        shading=dtt.SHADING_RAW_SOURCE, source_garment_mask=smask,
        carrier_component_boxes=cboxes, source_component_boxes=sboxes)


def test_every_measured_count_is_either_gated_or_explicitly_not_a_violation():
    """같은 실수를 다섯 번 했다 — QC 가 옳게 재는데 승격이 그 값을 읽지 않았다.

    개별 `if` 로 흩어 두면 하나를 빠뜨려도 조용히 통과한다. 이 시험은 QC 가 내는 모든
    계수를 훑어, `ZERO_RULES` 에 있거나 `NOT_A_VIOLATION` 에 이유와 함께 등록돼 있지
    않으면 실패한다. 빠뜨리는 것이 실패가 되게 만드는 것이 요점이다.
    """
    reports = [_report()[0], _report_with_component()]
    gated = {(section, key) for section, key, _reason in promo.ZERO_RULES}

    def walk(section, values, prefix=""):
        """**중첩까지** 훑는다. 한 겹만 보면 dict 안에 숨긴 계수가 그물을 빠져나간다."""
        found = []
        for key, value in values.items():
            name = f"{prefix}{key}"
            if isinstance(value, dict):
                found += walk(section, value, f"{name}.")
                continue
            # **정수 계수**만 본다 — 0 이 원리적으로 옳을 수 있는 것이 그것뿐이기
            # 때문이다. 연속 실수 지표는 계약상 실물 분포(Phase L) 전까지 게이팅하지
            # 않으므로 여기서 셀 대상이 아니다. 이름의 접미사로 거르지는 않는다 —
            # 접미사만 보면 `paintedWithoutAlphaCount` 같은 이름으로 그물을 피할 수 있다.
            # numpy 정수도 정수다 — `isinstance(v, int)` 만 보면 `np.int64(7)` 이
            # 그물을 빠져나간다.
            if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
                continue
            found.append(name)
        return found

    unaccounted = []
    for report in reports:
      for section, values in report.checks.items():
        if not isinstance(values, dict):
            continue
        for key in walk(section, values):
            # **전체 경로**로 대조한다. 루트로 줄이면 등록된 dict 안에 새 계수를 숨길 수
            # 있다(실측: `componentNegDetPx.ruleViolations`).
            if (section, key) in gated or (section, key) in NOT_A_VIOLATION:
                continue
            # 등록된 dict 안의 항목은 그 dict 의 등록 사유로 덮인다 — 단, 그 dict 가
            # 게이팅되는 경우에 한한다.
            root = key.split(".")[0]
            if "." in key and ((section, root) in gated
                               or (section, root) in NOT_A_VIOLATION):
                # 게이팅되는 dict 는 `_nonzero` 가 **안까지** 본다. 자기보고 묶음은
                # 통째로 echo 로 등록돼 있다.
                continue
            unaccounted.append((section, key))
    assert not unaccounted, (
        f"이 계수들이 게이팅되지도, 비위반으로 등록되지도 않았다: {unaccounted}")
    # 등록에는 **이유가 적혀 있어야** 한다. 빈 문자열은 등록이 아니라 회피다.
    for entry, reason in NOT_A_VIOLATION.items():
        assert isinstance(reason, str) and reason.strip(), entry
    for section, key, reason in promo.ZERO_RULES:
        assert isinstance(reason, str) and reason.strip(), (section, key)


def test_every_zero_rule_actually_blocks_promotion():
    """표에 적어 두고 읽지 않으면 표가 장식이 된다 — 한 줄씩 실제로 막는지 본다."""
    report, *_ = _report()
    assert promo.evaluate_direct_transfer_promotion(report).promoted is True
    for section, key, reason in promo.ZERO_RULES:
        checks = {k: dict(v) for k, v in report.checks.items()}
        checks.setdefault(section, {"computable": True})[key] = 7
        v = promo.evaluate_direct_transfer_promotion(
            dataclasses.replace(report, checks=checks))
        assert v.promoted is False, (section, key)
        assert reason in v.reasons, (section, key, v.reasons)


def test_a_painted_pixel_without_alpha_blocks_promotion():
    """칠했다면서 alpha 가 0 이면 그 자리는 carrier 가 그대로 남는다.

    실측(v10 초안): 넓은 밴드에서 기대 alpha 가 0.000955 인 2,156 px 의 alpha 를 0 으로
    두면 `paintedWithoutAlphaPx=2156` 인데 다른 모든 검사가 깨끗해 승격됐다.
    """
    report, *_ = _report()
    checks = {k: dict(v) for k, v in report.checks.items()}
    checks["domain"]["paintedWithoutAlphaPx"] = 2156
    v = promo.evaluate_direct_transfer_promotion(
        dataclasses.replace(report, checks=checks))
    assert v.promoted is False
    assert promo.REASON_INCOMPLETE_PAINT in v.reasons


@pytest.mark.parametrize("section,reason", [
    ("geometry", promo.REASON_GEOMETRY),
    ("mapping", promo.REASON_MAPPING),
    ("domain", promo.REASON_UNMEASURED),
    ("alpha", promo.REASON_ALPHA),
    ("blend", promo.REASON_BLEND),
])
def test_an_uncomputable_check_is_not_a_pass(section, reason):
    """'재지 못했다'는 '괜찮다'가 아니다 — 검사마다 그 규율이 서 있는지 본다."""
    report, *_ = _report()
    checks = {k: dict(v) for k, v in report.checks.items()}
    checks[section]["computable"] = False
    v = promo.evaluate_direct_transfer_promotion(
        dataclasses.replace(report, checks=checks))
    assert v.promoted is False, section
    assert reason in v.reasons, (section, v.reasons)


def test_alpha_where_none_was_expected_blocks_promotion():
    """기대 alpha 가 **정확히 0** 인 자리(보호 부위 등)에 alpha 가 있으면 설명되지 않는다.

    실측(v14 초안): 옷 **안**의 0 자리 1,000 px 을 0.0009 로 두면 최댓값 비교가 여유에
    걸리고 실루엣 밖 계수는 0 이라, 모든 검사가 깨끗한 채 승격됐다.
    """
    src, smask, m = source_with_margin(FOUR_COLOUR)
    pm = make_panel_map(IDENTITY_QUAD, w=500, h=700)
    lm = landmarks_for(IDENTITY_QUAD, w=500, h=700)
    car = carrier(500, 700)
    cboxes = {"p": np.float32([[210, 250], [290, 250], [290, 450], [210, 450]])}
    cand = dtt.transfer_torso_texture(
        car, pm, src, source_landmarks=lm, source_garment_mask=smask,
        carrier_component_boxes=cboxes, shading=dtt.SHADING_RAW_SOURCE)

    def run(c):
        return qc.evaluate_direct_transfer(
            c, carrier_bgr=car, source_bgr=src, panel_map=pm, source_landmarks=lm,
            shading=dtt.SHADING_RAW_SOURCE, source_garment_mask=smask,
            carrier_component_boxes=cboxes)

    honest = run(cand)
    assert honest.checks["alpha"]["nonZeroWhereExpectedZeroPx"] == 0
    assert promo.evaluate_direct_transfer_promotion(honest).promoted is True

    geom = qc._caller_geometry(pm, lm, src.shape)
    rec = qc._reconstruct_from_caller(src, cand.image_bgr.shape[:2], geom[0], geom[1],
                                      cboxes, None, smask)
    allowed, _prot, _illum = qc._caller_allowance(
        pm, geom, cboxes, None, rec, cand.image_bgr.shape[:2])
    expected = qc._expected_alpha(pm, allowed, qc._band_px(pm))
    inside_zero = (expected == 0.0) & (pm.garment_mask > 0) & (cand.painted == 0)
    ys, xs = np.nonzero(inside_zero)
    assert len(ys) >= 1000
    alpha = np.asarray(cand.alpha, np.float32).copy()
    alpha[ys[:1000], xs[:1000]] = np.float32(0.0009)

    bad = run(dataclasses.replace(cand, alpha=alpha))
    assert bad.checks["alpha"]["nonZeroWhereExpectedZeroPx"] == 1000
    v = promo.evaluate_direct_transfer_promotion(bad)
    assert v.promoted is False
    assert promo.REASON_ALPHA in v.reasons


def _empty_zone_report():
    """램프도 내부도 비는 구성 — 빈 구역 분기를 실제로 지난다."""
    src = np.zeros((1000, 1000, 3), np.uint8)
    src[:] = (60, 120, 180)
    smask = np.full((1000, 1000), 255, np.uint8)
    pm = make_panel_map(np.float32([[0, 0], [499, 0], [499, 699], [0, 699]]),
                        w=500, h=700, mask=np.full((700, 500), 255, np.uint8))
    lm = landmarks_for(np.float32([[0, 0], [999, 0], [999, 999], [0, 999]]),
                       w=1000, h=1000)
    car = carrier(500, 700)
    cand = dtt.transfer_torso_texture(car, pm, src, source_landmarks=lm,
                                      source_garment_mask=smask,
                                      shading=dtt.SHADING_RAW_SOURCE)
    return qc.evaluate_direct_transfer(
        cand, carrier_bgr=car, source_bgr=src, panel_map=pm, source_landmarks=lm,
        shading=dtt.SHADING_RAW_SOURCE, source_garment_mask=smask)


def _no_interior_report():
    """내부가 비고 램프만 남는 구성 — 재구성의 빈 구역 분기를 지난다."""
    src, smask, m = source_with_margin(FOUR_COLOUR)
    base = make_panel_map(np.float32([[230, 30], [269, 30], [269, 669], [230, 669]]),
                          w=500, h=700)
    pm = dataclasses.replace(base, metrics={**base.metrics, "boundary_band_px": 24})
    lm = landmarks_for(IDENTITY_QUAD, w=500, h=700)
    car = carrier(500, 700)
    cand = dtt.transfer_torso_texture(car, pm, src, source_landmarks=lm,
                                      source_garment_mask=smask,
                                      shading=dtt.SHADING_RAW_SOURCE)
    return qc.evaluate_direct_transfer(
        cand, carrier_bgr=car, source_bgr=src, panel_map=pm, source_landmarks=lm,
        shading=dtt.SHADING_RAW_SOURCE, source_garment_mask=smask)


@pytest.mark.parametrize("builder,section,key", [
    (_empty_zone_report, "blend", "impliedOutOfGamutPx"),
    (_no_interior_report, "reconstruction", "domainNotReconstructedPx"),
])
def test_a_zero_rule_that_disappears_is_not_a_pass(builder, section, key):
    """규칙이 **없어지는 것**은 통과가 아니다.

    빈 구역에서 키가 사라지면 승격이 그 규칙을 통째로 건너뛴다 — QC 는 0 으로라도
    반드시 내보내야 하고, 승격은 없는 키를 위반으로 읽어야 한다.
    """
    # **빈 구역** 보고서로 확인한다 — 정상 보고서는 일반 경로로 키가 이미 있어서,
    # 빈 구역 분기에서 키를 지우는 변종을 잡지 못한다.
    report = builder()
    assert key in report.checks[section], (section, key)
    checks = {k: dict(v) for k, v in report.checks.items()}
    del checks[section][key]
    v = promo.evaluate_direct_transfer_promotion(
        dataclasses.replace(report, checks=checks))
    assert v.promoted is False, (section, key)
