"""direct transfer 전용 QC — 관측만 하고 판정하지 않는다.

이 스위트가 지키는 것 두 가지:
  1. 각 검사가 **틀렸을 때 실제로 다른 값을 낸다**(항상 합격하는 검사는 검사가 아니다).
  2. 이 단계에는 **합격선이 없다**. 픽스처 하나로 임계를 만들면 그 픽스처를 통과시키는
     장치가 될 뿐이다. 산출물은 분포를 모을 원시 측정치다.
"""

import dataclasses
import inspect

import cv2
import numpy as np
import pytest

from app.services.hybrid_composite import direct_torso_transfer as dtt
from app.services.hybrid_composite import direct_transfer_qc as qc

from typing import NamedTuple

from test_direct_torso_transfer import (
    FOUR_COLOUR, IDENTITY_QUAD, _source_with_structure, carrier, landmarks_for,
    make_panel_map, source_with_margin)


#: QC 에 넘길 **호출자 입력** 묶음. v3 의 요점이 여기 있다 — 채점 기준은 후보가 아니라
#: 렌더러가 받았던 것과 같은 입력에서 나온다.
class _Case(NamedTuple):
    cand: object
    car: np.ndarray
    src: np.ndarray
    pm: object
    smask: np.ndarray
    lm: object
    cboxes: dict | None = None
    sboxes: dict | None = None
    #: 호출자가 렌더러에 준 조명 모드. QC 도 **같은 입력**을 받아야 램프 기대값이 선다.
    shading: str = dtt.SHADING_SOURCE_HIGHFREQ_CARRIER_LOWFREQ


def _candidate(target_quad=None, *, source_shade=0, carrier_shade=None, **kw):
    src, smask, m = source_with_margin(FOUR_COLOUR, shade=source_shade)
    pm = make_panel_map(target_quad if target_quad is not None else IDENTITY_QUAD,
                        w=500, h=700)
    lm = landmarks_for(np.float32([[m, m], [499 - m, m], [499 - m, 699 - m], [m, 699 - m]]),
                       w=500, h=700)
    car = carrier(500, 700, shade=carrier_shade)
    cand = dtt.transfer_torso_texture(car, pm, src, source_landmarks=lm,
                                      source_garment_mask=smask, **kw)
    assert isinstance(cand, dtt.DirectTorsoCandidate), cand
    return _Case(cand, car, src, pm, smask, lm,
                 kw.get("carrier_component_boxes"), kw.get("source_component_boxes"),
                 kw.get("shading", dtt.SHADING_SOURCE_HIGHFREQ_CARRIER_LOWFREQ))


def _run(case, cand=None):
    return qc.evaluate_direct_transfer(
        cand if cand is not None else case.cand,
        carrier_bgr=case.car, source_bgr=case.src, panel_map=case.pm,
        source_landmarks=case.lm, shading=case.shading,
        source_garment_mask=case.smask,
        carrier_component_boxes=case.cboxes, source_component_boxes=case.sboxes)


def _tamper(case, **kw):
    """후보만 바꾼다. 호출자 입력은 그대로 — 그것이 채점 기준이기 때문이다."""
    return _run(case, dataclasses.replace(case.cand, **kw))


# ── 이 단계의 계약: 판정하지 않는다 ────────────────────────────────────────
def test_this_phase_reports_measurements_and_never_a_pass_or_fail():
    """행동으로 고정한다 — 소스 문자열 검사는 산문에 걸려 넘어질 뿐 증거가 아니다."""
    cases = [
        _candidate(),
        _candidate(np.float32([[140, 40], [360, 40], [430, 660], [70, 660]])),
        _candidate(source_shade=+1, carrier_shade=-1),
    ]
    for c in cases:
        report = _run(c)
        assert report.decision == qc.DECISION_UNTHRESHOLDED
        # **중첩까지** 훑는다. 한 겹만 보면 `checks["domain"]["policy"]={"passed": ...}`
        # 같은 판정을 놓친다.
        def no_verdict(name, values):
            for key, value in values.items():
                assert key not in ("pass", "passed", "ok", "verdict", "decision",
                                   "accepted"), (name, key)
                if isinstance(value, dict):
                    no_verdict(f"{name}.{key}", value)

        for name, values in report.checks.items():
            no_verdict(name, values)


def test_every_check_is_present_even_when_nothing_interesting_happened():
    report = _run(_candidate())
    for name in ("provenance", "mapping", "sampling", "containment", "sourceBacking",
                 "geometry", "domain", "alpha", "reconstruction", "direction",
                 "colour", "luminance", "components"):
        assert name in report.checks, name
        assert "computable" in report.checks[name], name


def test_qc_cannot_be_handed_a_period_or_a_stripe_model():
    """구조로 고정한다: 서명에 주기 인자가 없고, 주기 추정 모듈에 의존하지 않는다."""
    params = set(inspect.signature(qc.evaluate_direct_transfer).parameters)
    for forbidden in ("period_px", "target_period_px", "source_period_px", "period",
                      "model", "stripe_model", "guided_period_px"):
        assert forbidden not in params, forbidden
    imported = {v.__name__ for v in vars(qc).values()
                if getattr(v, "__module__", None) is None and hasattr(v, "__name__")}
    assert "stripe_model" not in imported
    assert not hasattr(qc, "find_period_guided")
    assert not hasattr(qc, "extract_stripe_model")


# ══ v3 의 신뢰 경계: 기대값은 **호출자 입력**에서만 나온다 ═══════════════════
# 아래 세 시험은 Codex 가 v2 에서 재현한 세 가지 공격이다. v2 는 셋 다 만점을 줬다.

def test_a_mirrored_render_cannot_buy_a_pass_by_forging_its_own_geometry():
    """좌우 반전 렌더 + 그에 맞춘 provenance = v2 만점. 기준이 후보 안에 있었기 때문이다.

    v3 는 quad 를 `source_landmarks` 와 `panel_map` 에서 다시 세운다. 후보가 자기
    기록을 아무리 일관되게 위조해도 채점 기준은 밖에 있다.
    """
    case = _candidate()
    honest = _run(case).checks
    assert honest["reconstruction"]["abErrorMedian"] < 1.0, honest["reconstruction"]

    # 렌더를 좌우로 뒤집고, quad·homography 를 그 뒤집힌 사상으로 **자기일관되게** 바꾼다.
    sq = np.asarray(case.cand.provenance["sourceQuad"], np.float32)
    mirrored_sq = sq[[1, 0, 3, 2]]                      # 상단/하단 좌우 교환
    tq = np.asarray(case.cand.provenance["targetQuad"], np.float32)
    H = cv2.getPerspectiveTransform(mirrored_sq, tq)
    flipped = case.cand.image_bgr.copy()
    sel = case.cand.painted > 0
    flipped[sel] = cv2.flip(case.cand.image_bgr, 1)[sel]
    bad = _tamper(case, image_bgr=flipped,
                  provenance={**case.cand.provenance,
                              "sourceQuad": mirrored_sq.tolist(),
                              "homography": H.tolist()}).checks

    # 재구성은 위조를 아예 읽지 않으므로 반전을 그대로 본다.
    assert bad["reconstruction"]["abErrorMedian"] > 10.0, bad["reconstruction"]
    # 그리고 기하 검사는 기록이 호출자 기하와 다르다고 이름을 붙인다.
    assert honest["geometry"]["quadsMatchCaller"] is True, honest["geometry"]
    assert bad["geometry"]["quadsMatchCaller"] is False, bad["geometry"]
    assert bad["geometry"]["sourceQuadMaxDeltaPx"] > 10.0, bad["geometry"]


def test_hiding_half_of_painted_cannot_hide_the_pixels_it_covers():
    """v2 는 `painted` 를 비교 영역으로 썼다. 절반을 지우면 140,800 px 이 사라졌다."""
    case = _candidate()
    h, w = case.cand.image_bgr.shape[:2]
    wrong = case.cand.image_bgr.copy()
    sel = case.cand.painted > 0
    top = np.zeros_like(sel); top[: h // 2] = True
    # 위쪽 절반을 완전히 틀리게 칠하고, 그 절반을 "칠하지 않았다"고 주장한다.
    wrong[sel & top] = (0, 0, 255)
    liar_painted = case.cand.painted.copy()
    liar_painted[top] = 0
    hidden = _tamper(case, image_bgr=wrong, painted=liar_painted).checks

    # 주장한 영역만 보면 깨끗해 보인다 — 그것이 v2 가 속은 지점이다.
    assert hidden["reconstruction"]["comparedPx"] < int(sel.sum())
    # 그러나 "칠하지 않았다"는 픽셀은 carrier 그대로여야 한다. 아니면 숨긴 칠이다.
    assert hidden["domain"]["unclaimedChangedPx"] > 10_000, hidden["domain"]
    assert hidden["domain"]["paintedMatchesMetrics"] is False, hidden["domain"]
    # 정직한 렌더는 두 통 어디에도 잔여를 남기지 않는다.
    honest = _run(case).checks["domain"]
    assert honest["unclaimedChangedPx"] == 0, honest
    assert honest["paintedMatchesMetrics"] is True, honest


def test_a_uniform_luminance_offset_is_caught_although_correlation_cannot_see_it():
    """칠한 픽셀 전부의 휘도를 통째로 들어올린다. 상관은 이것을 원리적으로 못 본다.

    v2 실측: 281,600/281,600 px 이 바뀌었는데 hfCorr 0.9969 로 **옳은 렌더(0.9968)보다
    좋은 점수**가 나왔다. 상관은 스케일·오프셋 불변이다.

    (오프셋은 OpenCV LAB 눈금 +20 = 약 7.8 L*. 아래 임계는 그 크기를 그대로 쓴다 —
     픽스처에 맞춘 숫자가 아니라 주입한 오차의 크기다.)
    """
    case = _raw_candidate()
    good = _run(case).checks["reconstruction"]
    lab = cv2.cvtColor(case.cand.image_bgr, cv2.COLOR_BGR2LAB).astype(np.int16)
    sel = case.cand.painted > 0
    lab[..., 0][sel] = np.clip(lab[..., 0][sel] + 20, 0, 255)
    lifted = cv2.cvtColor(lab.astype(np.uint8), cv2.COLOR_LAB2BGR)
    bad = _tamper(case, image_bgr=lifted).checks["reconstruction"]

    # 상관은 여전히 높다 — 그래서 상관만으로 채점하면 안 된다는 것이 이 시험의 주장이다.
    assert bad["L_highCorr"] >= good["L_highCorr"], (bad, good)
    # 저주파 오프셋은 주입한 크기(≈7.8 L*)만큼 그대로 드러난다.
    assert good["L_lowMeanAbsDelta"] < 1.0, good
    assert bad["L_lowMeanAbsDelta"] > 5.0, bad
    assert bad["L_lowMeanAbsDelta"] > good["L_lowMeanAbsDelta"] * 10, (bad, good)


@pytest.mark.parametrize("mode", [dtt.SHADING_RAW_SOURCE,
                                  dtt.SHADING_SOURCE_HIGHFREQ_CARRIER_LOWFREQ])
def test_the_luminance_offset_is_caught_in_every_shading_mode(mode):
    """저역을 **원본 하나로만** 재면 기본 모드에서 이 공격이 통과한다 — 실제로 통과했다.

    기본 모드(`source_highfreq_carrier_lowfreq`)는 저주파를 carrier 에서 가져오는 것이
    설계다. 그래서 원본 대비 저역 편차는 정상 동작이고(실측 16.8), 오프셋을 주입하면
    그 값이 오히려 **좋아진다**(16.8 → 8.9). 모드마다 저역의 기준이 다르므로 두 기준을
    모두 재야 어느 모드에서도 숨을 곳이 없다.
    """
    src, smask, m = source_with_margin(FOUR_COLOUR)
    pm = make_panel_map(IDENTITY_QUAD, w=500, h=700)
    lm = landmarks_for(np.float32([[m, m], [499 - m, m], [499 - m, 699 - m], [m, 699 - m]]),
                       w=500, h=700)
    car = carrier(500, 700)
    cand = dtt.transfer_torso_texture(car, pm, src, source_landmarks=lm,
                                      source_garment_mask=smask, shading=mode)
    case = _Case(cand, car, src, pm, smask, lm)
    good = _run(case).checks["reconstruction"]
    lab = cv2.cvtColor(cand.image_bgr, cv2.COLOR_BGR2LAB).astype(np.int16)
    sel = cand.painted > 0
    lab[..., 0][sel] = np.clip(lab[..., 0][sel] + 20, 0, 255)
    bad = _tamper(case, image_bgr=cv2.cvtColor(lab.astype(np.uint8), cv2.COLOR_LAB2BGR)
                  ).checks["reconstruction"]

    # 상관은 어느 모드에서도 이것을 보지 못한다.
    assert abs(bad["L_highCorr"] - good["L_highCorr"]) < 0.01, (bad, good)
    # 그 모드가 저역을 가져오는 **기준** 쪽에서 오프셋이 드러나야 한다.
    key = ("L_lowMeanAbsDelta" if mode == dtt.SHADING_RAW_SOURCE
           else "L_lowMeanAbsDeltaVsCarrier")
    assert good[key] < 1.0, (mode, good)
    assert bad[key] > 5.0, (mode, bad)


def test_a_flattened_render_is_caught_by_amplitude_not_only_correlation():
    """속을 단색으로 덮으면 고주파 진폭이 0 으로 간다."""
    case = _raw_candidate()
    good = _run(case).checks["reconstruction"]
    flat = case.cand.image_bgr.copy()
    flat[case.cand.painted > 0] = (228, 228, 228)
    bad = _tamper(case, image_bgr=flat).checks["reconstruction"]
    assert good["L_highAmplitudeRatio"] > 0.5, good
    assert bad["L_highAmplitudeRatio"] < 0.1, bad
    assert bad["L_highRmse"] > good["L_highRmse"] * 3, (bad, good)


# ── 재구성이 위치·내용 어긋남을 본다 ───────────────────────────────────────
def test_reconstruction_catches_a_translated_render():
    """7px 밀면 칠한 픽셀의 60% 가 달라진다 — 전역 통계는 이것을 못 봤다."""
    case = _raw_candidate()
    good = _run(case).checks["reconstruction"]
    shifted = case.cand.image_bgr.copy()
    shifted[:, 7:] = case.cand.image_bgr[:, :-7]
    bad = _tamper(case, image_bgr=shifted).checks["reconstruction"]
    assert good["computable"] and bad["computable"]
    assert good["abErrorMedian"] < 1.0, good
    assert bad["abErrorMedian"] > 10.0, bad
    assert bad["L_highCorr"] < good["L_highCorr"] - 0.5, (bad, good)


def test_reconstruction_uses_the_module_sigma_not_the_candidates():
    """밴드 분할점을 후보가 정하면 오차를 측정되지 않는 밴드로 밀 수 있다."""
    case = _raw_candidate()
    honest = _run(case).checks["reconstruction"]
    lied = _tamper(case, provenance={**case.cand.provenance,
                                     "shadingSigmaShortSideFrac": 0.4}
                   ).checks["reconstruction"]
    assert lied["sigmaPx"] == honest["sigmaPx"], (lied, honest)
    assert honest["sigmaFracMatchesRenderer"] is True
    assert lied["sigmaFracMatchesRenderer"] is False


# ── 방향 검사 ──────────────────────────────────────────────────────────────
def test_direction_prediction_tracks_the_mapping_not_a_constant():
    """기울인 target 에서는 예측 방향 자체가 원본과 달라야 하고, 측정이 그걸 따라야 한다."""
    straight = _run(_candidate()).checks["direction"]
    rotated = np.float32([[180, 60], [430, 180], [320, 640], [70, 520]])
    skewed = _run(_candidate(rotated)).checks["direction"]
    assert straight["computable"] and skewed["computable"]
    assert qc._angle_between(np.array(straight["predictedUnit"]),
                             np.array(straight["sourceUnit"])) < 1.0
    drift = qc._angle_between(np.array(skewed["predictedUnit"]),
                              np.array(skewed["sourceUnit"]))
    assert drift > 1.0, drift
    assert skewed["angleErrorDeg"] < 5.0, skewed


def test_direction_reports_a_large_error_when_the_render_is_rotated():
    """v2 는 provenance 의 homography 를 돌려서 이 시험을 통과시켰다 — 렌더는 그대로인데.

    v3 에서는 후보 기록을 건드려도 예측이 흔들리지 않는다. 실제로 **렌더를** 돌려야
    각도 오차가 난다. 그게 이 검사가 재야 하는 것이다.
    """
    case = _raw_candidate()
    good = _run(case).checks["direction"]
    assert good["angleErrorDeg"] < 1.0, good
    forged = _tamper(case, provenance={
        **case.cand.provenance,
        "homography": [[0.0, -1.0, 500.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]],
    }).checks["direction"]
    assert forged["angleErrorDeg"] == good["angleErrorDeg"], (forged, good)

    turned = case.cand.image_bgr.copy()
    sel = case.cand.painted > 0
    turned[sel] = cv2.rotate(cv2.resize(case.cand.image_bgr, (700, 500)),
                             cv2.ROTATE_90_CLOCKWISE)[sel]
    bad = _tamper(case, image_bgr=turned).checks["direction"]
    assert bad["angleErrorDeg"] > 45.0, bad


def test_direction_refuses_to_answer_without_real_texture():
    """마스크 테두리의 gradient 를 방향이라고 우기면 안 된다."""
    case = _raw_candidate()
    flat = case.cand.image_bgr.copy()
    flat[case.cand.painted > 0] = (228, 228, 228)
    d = _tamper(case, image_bgr=flat).checks["direction"]
    assert d["computable"] is False, d


# ── 기하: 기록을 호출자 기하와 대조한다 ────────────────────────────────────
def test_geometry_compares_the_recorded_mapping_against_caller_geometry():
    case = _raw_candidate()
    good = _run(case).checks["geometry"]
    assert good["quadsMatchCaller"] is True
    assert good["recordedHomographyUsable"] is True
    assert good["recordedVsCallerMaxAbs"] < 1e-3, good
    rot = [[0.0, -1.0, 500.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]
    bad = _tamper(case, provenance={**case.cand.provenance, "homography": rot}
                  ).checks["geometry"]
    assert bad["recordedVsCallerMaxAbs"] > 1.0, bad


@pytest.mark.parametrize("H", [
    [[0.0] * 3] * 3,                                   # 특이 — v2 는 NaN 을 냈다
    [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 0.0]],   # H[2,2] == 0
    [[float("nan")] * 3] * 3,
])
def test_geometry_rejects_degenerate_recorded_homographies(H):
    case = _raw_candidate()
    g = _tamper(case, provenance={**case.cand.provenance, "homography": H}
                ).checks["geometry"]
    assert g["recordedHomographyUsable"] is False, g
    assert g["recordedVsCallerMaxAbs"] is None, g


# ── 나머지 검사 ────────────────────────────────────────────────────────────
def test_provenance_check_names_the_missing_keys():
    case = _candidate()
    full = _run(case).checks["provenance"]
    assert full["complete"] is True and full["missingKeys"] == []
    holed = _tamper(case, provenance={**case.cand.provenance, "sourceSha256": None,
                                      "boundaryBandPx": None}).checks["provenance"]
    assert holed["complete"] is False
    assert set(holed["missingKeys"]) == {"sourceSha256", "boundaryBandPx"}


def test_provenance_requires_every_render_affecting_field():
    """v1 은 렌더를 바꾸는 필드가 빠져도 '완전'이라고 했다."""
    case = _raw_candidate()
    assert _run(case).checks["provenance"]["complete"] is True
    for key in ("sourceMaskInterpolation", "shadingMode", "shadingSigmaShortSideFrac",
                "componentHomographies", "carrierComponentBoxes",
                "sourceGarmentMaskSha256"):
        stripped = {k: v for k, v in case.cand.provenance.items() if k != key}
        holed = _tamper(case, provenance=stripped).checks["provenance"]
        assert holed["complete"] is False, key
        assert key in holed["missingKeys"], key


def test_containment_check_detects_a_pixel_painted_outside_the_garment():
    case = _candidate()
    clean = _run(case).checks["containment"]
    assert clean["paintedOutsideGarmentPx"] == 0
    assert clean["outsideGarmentUntouched"] is True
    dirty_img = case.cand.image_bgr.copy()
    outside = np.nonzero(case.pm.garment_mask == 0)
    dirty_img[outside[0][:50], outside[1][:50]] = (0, 255, 0)
    dirty_painted = case.cand.painted.copy()
    dirty_painted[outside[0][:50], outside[1][:50]] = 255
    bad = _tamper(case, image_bgr=dirty_img, painted=dirty_painted).checks["containment"]
    assert bad["paintedOutsideGarmentPx"] == 50
    assert bad["outsideGarmentUntouched"] is False


def test_colour_compares_corresponding_pixels_not_two_populations():
    """옳은 렌더에 가짜 이동을 보고하면 안 되고, 틀린 렌더에 0 을 보고해도 안 된다."""
    case = _raw_candidate()
    good = _run(case).checks["colour"]
    assert good["perPixelAbErrorMedian"] < 1.0, good
    shifted = case.cand.image_bgr.copy()
    sel = case.cand.painted > 0
    shifted[sel] = np.clip(shifted[sel].astype(np.int16)
                           + np.array([40, -20, -20], np.int16), 0, 255).astype(np.uint8)
    moved = _tamper(case, image_bgr=shifted).checks["colour"]
    assert moved["perPixelAbErrorMedian"] > good["perPixelAbErrorMedian"] + 3.0, moved


def test_colour_error_survives_a_permutation_that_preserves_the_population():
    """좌우로 뒤집으면 색 **모집단**은 그대로다. 중앙값 차는 0.0 을 낸다 — 그것이 함정.

    대응을 뽑아 놓고 집계로 뭉개면 대응을 쓰지 않은 것과 같다.
    """
    case = _raw_candidate()
    sel = case.cand.painted > 0
    mirrored = case.cand.image_bgr.copy()
    mirrored[sel] = cv2.flip(case.cand.image_bgr, 1)[sel]
    c = _tamper(case, image_bgr=mirrored).checks["colour"]
    assert c["populationMedianAbShift"] < 1.0, c        # 집계는 눈이 멀었다
    assert c["perPixelAbErrorMedian"] > 10.0, c         # 대응은 본다


def test_component_checks_carry_fill_and_placement_when_boxes_are_supplied():
    src, smask, m, sbox = _source_with_structure(FOUR_COLOUR)
    pm = make_panel_map(IDENTITY_QUAD, w=500, h=700)
    lm = landmarks_for(np.float32([[m, m], [499 - m, m], [499 - m, 699 - m], [m, 699 - m]]),
                       w=500, h=700)
    cbox = np.float32([[300, 80], [352, 80], [352, 620], [300, 620]])
    car = carrier(500, 700)
    cboxes, sboxes = {"placket_box": cbox}, {"placket_box": sbox}
    cand = dtt.transfer_torso_texture(
        car, pm, src, source_landmarks=lm, source_garment_mask=smask,
        carrier_component_boxes=cboxes, source_component_boxes=sboxes)
    report = _run(_Case(cand, car, src, pm, smask, lm, cboxes, sboxes))
    comp = report.checks["components"]
    assert comp["fill"]["placket_box"]["filled"] is True
    assert comp["placement"]["placket_box"]["iou"] is not None
    assert comp["filledPx"] > 0 and comp["coverage"] is not None
    # 부위 박스도 QC 가 스스로 재구성한다 — 몸통 사상으로만 채점하면 안 된다.
    assert report.checks["domain"]["paintedOutsideAllowedPx"] == 0, report.checks["domain"]


def test_luminance_check_surfaces_clipping_and_retention():
    src, smask, m = source_with_margin(FOUR_COLOUR)
    pm = make_panel_map(IDENTITY_QUAD, w=500, h=700)
    lm = landmarks_for(np.float32([[m, m], [499 - m, m], [499 - m, 699 - m], [m, 699 - m]]),
                       w=500, h=700)
    white = np.full((700, 500, 3), 255, np.uint8)
    cand = dtt.transfer_torso_texture(white, pm, src, source_landmarks=lm,
                                      source_garment_mask=smask)
    lum = _run(_Case(cand, white, src, pm, smask, lm)).checks["luminance"]
    assert lum["clippedFracL"] > 0.2
    assert lum["highFreqRetention"] < 1.0


def test_unusable_caller_geometry_is_reported_not_raised():
    """계산 불가는 예외가 아니라 값이다 — 이제 **호출자 쪽**이 비었을 때 이야기다."""
    case = _raw_candidate()
    blind = _Case(case.cand, case.car, case.src, case.pm, case.smask, None)
    report = qc.evaluate_direct_transfer(
        blind.cand, carrier_bgr=blind.car, source_bgr=blind.src, panel_map=blind.pm,
        source_landmarks=None, source_garment_mask=blind.smask)
    for name in ("geometry", "reconstruction", "direction", "colour"):
        entry = report.checks[name]
        assert entry["computable"] is False, (name, entry)
        assert "caller" in entry["reason"], (name, entry)
    # 후보만 의심스러워도 나머지 관측은 계속 나온다.
    assert report.checks["containment"]["computable"] is True


# ══ Codex 라운드 1 반증: 규칙을 어긴 렌더가 더 좋은 점수를 받았다 ══════════
# 셋 다 "덜 하지 않고 **더 했는데** 점수가 올라간" 경우다. 오라클이 포함 규칙만 알고
# 배제·합성 규칙을 몰랐기 때문이다. 그 규칙은 전부 호출자 입력에서 나온다.

def test_painting_over_a_source_mask_hole_cannot_score_clean():
    """원본 옷 마스크에 구멍이 있으면 그 픽셀은 **근거가 없다**. 칠하면 배경이 실린다.

    실측(v3 초안): 구멍 10,000 px 을 원본 픽셀로 칠하고 painted 로 주장하면
    커버리지 1.0, abErrorMedian 0.0, abErrorP95 0.0 — 옳은 렌더보다 좋은 보고서.
    항등 사상에서는 warp 된 원본이 원본과 같으므로 내용 비교로는 원리적으로 못 잡는다.
    """
    src, smask, m = source_with_margin(FOUR_COLOUR)
    smask[200:300, 200:300] = 0                    # 옷이 아닌 영역
    pm = make_panel_map(IDENTITY_QUAD, w=500, h=700)
    lm = landmarks_for(IDENTITY_QUAD, w=500, h=700)
    car = carrier(500, 700)
    cand = dtt.transfer_torso_texture(car, pm, src, source_landmarks=lm,
                                      source_garment_mask=smask,
                                      shading=dtt.SHADING_RAW_SOURCE)
    case = _Case(cand, car, src, pm, smask, lm)
    honest = _run(case).checks["domain"]
    assert honest["paintedWithoutSourceBackingPx"] == 0, honest

    wrong = cand.image_bgr.copy()
    wrong[200:300, 200:300] = src[200:300, 200:300]
    painted = cand.painted.copy(); painted[200:300, 200:300] = 255
    alpha = cand.alpha.copy(); alpha[200:300, 200:300] = 1.0
    bad = _run(case, dataclasses.replace(
        cand, image_bgr=wrong, painted=painted, alpha=alpha,
        metrics={**cand.metrics, "paintedPx": int((painted > 0).sum())})).checks["domain"]
    assert bad["paintedWithoutSourceBackingPx"] > 9_000, bad
    assert bad["paintedOutsideAllowedPx"] > 9_000, bad


def test_painting_through_a_carrier_only_component_box_cannot_score_clean():
    """carrier 쪽 구조 박스는 이 전송이 소유하지 않는다 — 원본 짝이 없으면 더더욱.

    실측(v3 초안): 플래킷 28,673 px 을 몸통 원단으로 덮으면 커버리지 0.8982 → 1.0.
    carrier 의 실제 플래킷이 원단으로 사라지는데 보고서는 좋아졌다.
    """
    src, smask, m = source_with_margin(FOUR_COLOUR)
    pm = make_panel_map(IDENTITY_QUAD, w=500, h=700)
    lm = landmarks_for(IDENTITY_QUAD, w=500, h=700)
    car = carrier(500, 700)
    cbox = {"placket_box": np.float32([[220, 100], [280, 100], [280, 600], [220, 600]])}
    cand = dtt.transfer_torso_texture(car, pm, src, source_landmarks=lm,
                                      source_garment_mask=smask,
                                      carrier_component_boxes=cbox,   # 원본 짝 없음
                                      shading=dtt.SHADING_RAW_SOURCE)
    case = _Case(cand, car, src, pm, smask, lm, cbox, None)
    honest = _run(case).checks["domain"]
    assert honest["paintedInProtectedComponentPx"] == 0, honest

    box = np.zeros((700, 500), bool)
    box[100:600, 220:280] = True
    wrong = cand.image_bgr.copy()
    wrong[box] = src[box]
    painted = cand.painted.copy(); painted[box] = 255
    alpha = cand.alpha.copy(); alpha[box] = 1.0
    bad = _run(case, dataclasses.replace(
        cand, image_bgr=wrong, painted=painted, alpha=alpha,
        metrics={**cand.metrics, "paintedPx": int((painted > 0).sum())})).checks["domain"]
    assert bad["paintedInProtectedComponentPx"] > 20_000, bad
    assert bad["paintedOutsideAllowedPx"] > 20_000, bad


def test_deleting_the_feather_cannot_produce_a_better_report():
    """깃털을 지우고 alpha 를 1.0 으로 위조하면 재구성 지표가 **완벽해진다**.

    재구성은 합성 **전** warp 와 비교하므로 원리적으로 이것을 못 본다 — 실측으로
    상관·진폭 1.0, RMSE 0, abWithin2Frac 1.0 이 나왔다. 그래서 alpha 자체를 잰다.
    """
    case = _raw_candidate()
    honest = _run(case).checks
    assert honest["alpha"]["computable"] is True
    assert honest["alpha"]["maxAbsDeltaVsExpected"] < 1e-3, honest["alpha"]
    assert honest["alpha"]["hardEdgePx"] == 0, honest["alpha"]
    assert honest["alpha"]["expectedRampPx"] > 0, honest["alpha"]

    # 합성 전 warp 을 그대로 쓰고 alpha 를 1.0 으로 위조한다.
    geom = qc._caller_geometry(case.pm, case.lm, case.src.shape)
    rec = qc._reconstruct_from_caller(case.src, case.cand.image_bgr.shape[:2],
                                      geom[0], geom[1], None, None, case.smask)
    sel = case.cand.painted > 0
    hard = case.cand.image_bgr.copy()
    hard[sel] = rec["warped"][sel]
    forged = np.zeros_like(case.cand.alpha); forged[sel] = 1.0
    bad = _run(case, dataclasses.replace(case.cand, image_bgr=hard, alpha=forged)).checks

    # 재구성만 보면 실제로 '더 좋아진다' — 이 시험은 그 사실을 못 박아 둔다.
    assert bad["reconstruction"]["L_highRmse"] <= honest["reconstruction"]["L_highRmse"]
    # alpha 검사가 잡는다.
    assert bad["alpha"]["maxAbsDeltaVsExpected"] > 0.5, bad["alpha"]
    assert bad["alpha"]["hardEdgePx"] > 100, bad["alpha"]


@pytest.mark.parametrize("band", [0, 1, 3, 8, None])
def test_the_expected_feather_uses_the_same_band_rule_as_the_renderer(band):
    """오라클이 렌더러와 다른 밴드를 쓰면 **옳은 렌더가** 잘린 가장자리로 신고된다.

    실측: `boundary_band_px = 0` 일 때 렌더러는 `max(1.0, 0)` = 1.0 을 쓰는데 오라클은
    `... or 4.0` 때문에 4.0 을 기대해 hardEdgePx 6,420 을 냈다. 0 은 '없는 값'이 아니다.
    """
    src, smask, m = source_with_margin(FOUR_COLOUR)
    base = make_panel_map(IDENTITY_QUAD, w=500, h=700)
    metrics = dict(base.metrics)
    if band is None:
        metrics.pop("boundary_band_px", None)
    else:
        metrics["boundary_band_px"] = band
    pm = dataclasses.replace(base, metrics=metrics)
    lm = landmarks_for(IDENTITY_QUAD, w=500, h=700)
    car = carrier(500, 700)
    cand = dtt.transfer_torso_texture(car, pm, src, source_landmarks=lm,
                                      source_garment_mask=smask,
                                      shading=dtt.SHADING_RAW_SOURCE)
    a = _run(_Case(cand, car, src, pm, smask, lm)).checks["alpha"]
    # 기준은 **호출자 쪽**이다. 후보의 provenance 를 기준으로 삼으면 후보가 자기 시험을
    # 채점하게 된다 — v3 의 이 시험이 정확히 그랬고, 밴드 규칙을 후보에서 읽도록
    # 바꿔도 전부 통과했다.
    assert a["maxAbsDeltaVsExpected"] < 1e-3, (band, a)
    assert a["hardEdgePx"] == 0, (band, a)
    assert a["expectedRampPx"] > 0, (band, a)


def test_honest_alpha_with_an_uncomposited_image_is_caught_by_the_blend_relation():
    """alpha 는 정직한데 이미지만 합성 전 warp 인 경우 — alpha 동등성으로는 못 잡는다."""
    case = _raw_candidate()
    honest = _run(case).checks["blend"]
    geom = qc._caller_geometry(case.pm, case.lm, case.src.shape)
    rec = qc._reconstruct_from_caller(case.src, case.cand.image_bgr.shape[:2],
                                      geom[0], geom[1], None, None, case.smask)
    sel = case.cand.painted > 0
    hard = case.cand.image_bgr.copy()
    hard[sel] = rec["warped"][sel]
    tampered = _tamper(case, image_bgr=hard)
    assert tampered.checks["alpha"]["maxAbsDeltaVsExpected"] < 1e-3   # alpha 는 그대로
    bad = tampered.checks["blend"]
    assert honest["impliedAbErrorAlphaWeightedMean"] < 1.0, honest
    assert (bad["impliedAbErrorAlphaWeightedMean"]
            > honest["impliedAbErrorAlphaWeightedMean"] + 1.0), (bad, honest)


def test_scaling_the_blend_magnitude_is_caught_although_correlation_cannot_see_it():
    """램프 변위를 1.5배로 키우면(픽셀당 최대 43) 상관 기반 지표는 **좋아졌다**.

    실측(v3): blendCorrInRamp 0.8546 → 0.8621, L_highRmse 1.4893 → 1.2369. 라운드 1 에서
    같은 이유로 상관을 버려 놓고 여기서 다시 썼다. 절대값으로 재야 한다.
    """
    case = _raw_candidate()
    good = _run(case).checks["blend"]
    a = np.asarray(case.cand.alpha, np.float32)
    ramp = (a > 0) & (a < 1)
    car = case.car.astype(np.float64)
    harder = case.cand.image_bgr.astype(np.float64).copy()
    harder[ramp] = np.clip(
        car[ramp] + 1.5 * (case.cand.image_bgr.astype(np.float64)[ramp] - car[ramp]),
        0, 255)
    bad = _tamper(case, image_bgr=harder.astype(np.uint8)).checks["blend"]
    assert good["impliedAbErrorAlphaWeightedMean"] < 1.0, good
    assert (bad["impliedAbErrorAlphaWeightedMean"]
            > good["impliedAbErrorAlphaWeightedMean"] + 1.0), (bad, good)


@pytest.mark.parametrize("patch", [
    {"shadingSigmaShortSideFrac": "bogus"},
    {"shadingSigmaShortSideFrac": float("nan")},
    {"sourceSha256": np.zeros(3)},
    {"homography": "not-a-matrix"},
    {"targetQuad": np.zeros((4, 2))},
])
def test_candidate_authored_provenance_cannot_raise_out_of_being_measured(patch):
    """예외는 '계산 불가'보다 나쁘다 — 후보가 채점 자체를 없앨 수 있다는 뜻이다."""
    case = _raw_candidate()
    report = _run(case, dataclasses.replace(
        case.cand, provenance={**case.cand.provenance, **patch}))
    # 후보가 무엇을 적든 렌더 자체의 측정은 계속 나온다.
    assert report.checks["reconstruction"]["computable"] is True
    assert report.checks["reconstruction"]["abErrorMedian"] < 1.0
    for values in report.checks.values():
        for v in values.values():
            assert not (isinstance(v, float) and v != v), values      # NaN 금지


@pytest.mark.parametrize("quad", [
    np.float32([[np.nan, 0], [500, 0], [500, 700], [0, 700]]),
    np.float32([[0, 0], [0, 0], [0, 0], [0, 0]]),
])
def test_a_degenerate_caller_quad_is_uncomputable_not_nan(quad):
    """호출자 쪽이 깨져 있으면 '계산 불가'라고 말한다. NaN 측정치를 내지 않는다."""
    case = _raw_candidate()
    pm = make_panel_map(IDENTITY_QUAD, w=500, h=700)
    broken = dataclasses.replace(pm, panels=(dataclasses.replace(pm.panels[0],
                                                                 quad=quad),))
    report = _run(_Case(case.cand, case.car, case.src, broken, case.smask, case.lm))
    assert report.checks["geometry"]["computable"] is False, report.checks["geometry"]
    for values in report.checks.values():
        for v in values.values():
            assert not (isinstance(v, float) and v != v), values


def test_component_reconstruction_is_actually_exercised():
    """부위 재구성 루프를 지워도 통과하는 시험은 그 루프를 시험하지 않는다.

    부위는 **자기 box→box 사상**으로 채워진다. 몸통 사상으로 채우면 틀린 픽셀이어야
    한다 — 그 차이가 여기서 실제로 관측돼야 한다.
    """
    src, smask, m, sbox = _source_with_structure(FOUR_COLOUR)
    pm = make_panel_map(IDENTITY_QUAD, w=500, h=700)
    lm = landmarks_for(np.float32([[m, m], [499 - m, m], [499 - m, 699 - m], [m, 699 - m]]),
                       w=500, h=700)
    cbox = np.float32([[300, 80], [352, 80], [352, 620], [300, 620]])
    car = carrier(500, 700)
    cboxes, sboxes = {"placket_box": cbox}, {"placket_box": sbox}
    cand = dtt.transfer_torso_texture(
        car, pm, src, source_landmarks=lm, source_garment_mask=smask,
        carrier_component_boxes=cboxes, source_component_boxes=sboxes,
        shading=dtt.SHADING_RAW_SOURCE)
    case = _Case(cand, car, src, pm, smask, lm, cboxes, sboxes)
    good = _run(case).checks["reconstruction"]
    assert good["abErrorMedian"] < 1.0, good

    # 부위 픽셀을 **몸통 사상**의 결과로 덮는다 — box→box 를 쓰지 않은 렌더.
    geom = qc._caller_geometry(pm, lm, src.shape)
    torso_only = qc._reconstruct_from_caller(src, cand.image_bgr.shape[:2],
                                             geom[0], geom[1], None, None, smask)
    box = np.zeros((700, 500), bool)
    box[80:620, 300:352] = True
    sel = box & (cand.painted > 0) & torso_only["valid"]
    assert int(sel.sum()) > 5_000, int(sel.sum())
    wrong = cand.image_bgr.copy()
    wrong[sel] = torso_only["warped"][sel]
    bad = _tamper(case, image_bgr=wrong).checks["reconstruction"]
    # 부위를 몸통 사상으로 채우면 오라클이 그 차이를 본다.
    assert bad["abErrorP95"] > good["abErrorP95"] + 5.0, (bad, good)


# ══ Codex 라운드 2 반증: 합성 전 기준으로 합성 후를 재고 있었다 ═══════════

def test_a_smooth_garment_does_not_produce_pathological_reconstruction():
    """매끈한 원단에서 v3 는 **옳은 렌더**에 L_highAmplitudeRatio 62,135 를 냈다.

    고주파 신호가 없는 원단에서는 깃털 가장자리가 유일한 고주파였고, 오라클이 그것을
    오차로 셌다. 그래서 금지된 하드 엣지가 1.0·1.0·0.0 으로 **더 좋은 점수**를 받았다.
    v4 는 합성이 항등인 내부에서만 내용을 비교한다.
    """
    src, smask, m = source_with_margin(FOUR_COLOUR)
    src[:] = (50, 100, 200)                       # 완전히 매끈
    base = make_panel_map(IDENTITY_QUAD, w=500, h=700)
    pm = dataclasses.replace(base, metrics={**base.metrics, "boundary_band_px": 8})
    lm = landmarks_for(IDENTITY_QUAD, w=500, h=700)
    car = np.full((700, 500, 3), 150, np.uint8)
    cand = dtt.transfer_torso_texture(car, pm, src, source_landmarks=lm,
                                      source_garment_mask=smask,
                                      shading=dtt.SHADING_RAW_SOURCE)
    case = _Case(cand, car, src, pm, smask, lm)
    r = _run(case).checks["reconstruction"]
    # 매끈한 원단에는 고주파가 없다 → 비율·상관은 **정의되지 않는다**고 말해야 한다.
    # 없는 신호에 숫자를 붙이면 그 숫자는 잡음이고, 잡음에는 임계를 못 세운다.
    assert r["L_highBandDefined"] is False, r
    assert r["L_highAmplitudeRatio"] is None and r["L_highCorr"] is None, r
    assert r["L_highRefStd"] < 0.5, r
    assert r["abWithin2Frac"] == 1.0, r
    assert r["abErrorMedian"] < 1.0, r

    # 그리고 하드 엣지는 blend 가 잡는다 — 재구성이 아니라.
    geom = qc._caller_geometry(pm, lm, src.shape)
    rec = qc._reconstruct_from_caller(src, cand.image_bgr.shape[:2], geom[0], geom[1],
                                      None, None, smask)
    hard = cand.image_bgr.copy()
    hard[cand.painted > 0] = rec["warped"][cand.painted > 0]
    good_b = _run(case).checks["blend"]
    bad_b = _tamper(case, image_bgr=hard).checks["blend"]
    assert bad_b["impliedAbErrorAlphaWeightedMean"] > (
        good_b["impliedAbErrorAlphaWeightedMean"] + 5.0), (bad_b, good_b)


def test_the_expected_feather_comes_from_allowed_not_from_painted():
    """기대 alpha 를 `painted` 로 만들면 painted 를 지우는 순간 기대값도 같이 줄었다.

    실측(v3): 10,000 px 구멍을 내고 alpha 를 그 구멍에 맞춰 다시 만들면 alpha 검사는
    maxDelta 0.0 으로 완벽했고 비교 모집단은 281,600 → 271,600 으로 조용히 줄었다.
    """
    case = _raw_candidate()
    good = _run(case).checks
    painted = case.cand.painted.copy()
    painted[250:350, 200:300] = 0
    image = case.cand.image_bgr.copy()
    image[250:350, 200:300] = case.car[250:350, 200:300]
    band = qc._band_px(case.pm)
    sil = np.clip(cv2.distanceTransform(case.pm.garment_mask, cv2.DIST_L2, 3) / band, 0, 1)
    inner = np.clip(cv2.distanceTransform(painted, cv2.DIST_L2, 3) / band, 0, 1)
    alpha = np.minimum(sil, inner).astype(np.float32)
    alpha[case.pm.garment_mask == 0] = 0.0
    bad = _tamper(case, painted=painted, image_bgr=image, alpha=alpha).checks

    assert good["alpha"]["maxAbsDeltaVsExpected"] < 1e-3, good["alpha"]
    assert bad["alpha"]["maxAbsDeltaVsExpected"] > 0.5, bad["alpha"]
    # 비교 모집단은 후보가 줄일 수 없다 — 이제 allowed 에서 나온다.
    assert (bad["reconstruction"]["comparedPx"]
            == good["reconstruction"]["comparedPx"]), (bad, good)
    assert bad["domain"]["allowedNotPaintedPx"] >= 9_000, bad["domain"]


def test_an_ineligible_component_is_absent_from_the_expectation():
    """렌더러가 거부한 부위의 사상을 오라클이 설치하면, 그 부위로 덧칠한 그림이 좋아진다.

    실측(v3): 거부된 부위로 14,641 px 을 다시 칠하자 abErrorP95 175.0 → 0.0.
    """
    src, smask, m, sbox = _source_with_structure(FOUR_COLOUR)
    pm = make_panel_map(IDENTITY_QUAD, w=500, h=700)
    lm = landmarks_for(IDENTITY_QUAD, w=500, h=700)
    car = carrier(500, 700)
    # 원본 박스가 옷 밖으로 크게 나간 부위 — 렌더러는 자기 규칙으로 다룬다.
    cboxes = {"p": np.float32([[220, 100], [280, 100], [280, 600], [220, 600]])}
    sboxes = {"p": np.float32([[-20, 100], [40, 100], [40, 600], [-20, 600]])}
    cand = dtt.transfer_torso_texture(
        car, pm, src, source_landmarks=lm, source_garment_mask=smask,
        carrier_component_boxes=cboxes, source_component_boxes=sboxes,
        shading=dtt.SHADING_RAW_SOURCE)
    d = _run(_Case(cand, car, src, pm, smask, lm, cboxes, sboxes)).checks["domain"]
    # 오라클의 기대가 렌더러의 실제 칠과 정확히 일치해야 한다 — 어느 쪽으로도 어긋나면
    # 한쪽은 오탐, 다른 쪽은 탈출구가 된다.
    assert d["allowedNotPaintedPx"] == 0, d
    assert d["paintedOutsideAllowedPx"] == 0, d


@pytest.mark.parametrize("kw", [
    {"alpha": np.zeros((3, 3), np.float32)},
    {"painted": np.zeros((3, 3), np.uint8)},
    {"alpha": np.full((700, 500), np.nan, np.float32)},
    {"metrics": {"paintedPx": np.zeros(3)}},
])
def test_malformed_candidate_arrays_are_reported_not_raised(kw):
    """후보가 자기 출력을 망가뜨려 채점을 예외로 없앨 수 없다."""
    case = _raw_candidate()
    report = _run(case, dataclasses.replace(case.cand, **kw))   # 예외 없이 돌아와야 한다
    for values in report.checks.values():
        for v in values.values():
            assert not (isinstance(v, float) and v != v), values     # NaN 금지


def test_a_malformed_provenance_value_is_not_reported_complete():
    """값이 '있다'는 것과 '쓸 수 있다'는 것은 다른 사건이다."""
    case = _raw_candidate()
    assert _run(case).checks["provenance"]["complete"] is True
    for key, junk in (("sourceSha256", np.zeros(3)), ("boundaryBandPx", "bogus"),
                      ("homography", [[1.0, 0.0], [0.0, 1.0]])):
        pv = _run(case, dataclasses.replace(
            case.cand, provenance={**case.cand.provenance, key: junk})).checks["provenance"]
        assert pv["complete"] is False, (key, pv)
        assert key in pv["malformedKeys"], (key, pv)


# ══ v4 라운드 1 반증: 램프 휘도가 통째로 측정되지 않았다 ══════════════════

def _smooth_case(band=8, *, src_bgr=(50, 50, 50), car_bgr=(150, 150, 150)):
    src, smask, m = source_with_margin(FOUR_COLOUR)
    src[:] = src_bgr
    base = make_panel_map(IDENTITY_QUAD, w=500, h=700)
    pm = dataclasses.replace(base, metrics={**base.metrics, "boundary_band_px": band})
    lm = landmarks_for(IDENTITY_QUAD, w=500, h=700)
    car = np.full((700, 500, 3), car_bgr, np.uint8)
    cand = dtt.transfer_torso_texture(car, pm, src, source_landmarks=lm,
                                      source_garment_mask=smask,
                                      shading=dtt.SHADING_RAW_SOURCE)
    return _Case(cand, car, src, pm, smask, lm,
                 shading=dtt.SHADING_RAW_SOURCE)


def test_a_blacked_out_feather_ring_cannot_measure_as_well_as_the_composite():
    """램프는 내용 비교에서 빠졌고 blend 는 a/b 만 봤다 → **휘도가 무측정**이었다.

    실측(v4 초안): 깃털 고리 17,024 px 을 전부 검게 칠해도(평균 |ΔBGR| 95.24)
    blend 0.0531 → 0.0699, alpha·재구성·색·domain 전부 동일. 눈에 보이는 검은 테두리가
    옳은 합성과 같은 점수를 받았다.

    되돌린 색이 **진짜 색이 아니면** 그 픽셀은 합성으로 설명되지 않는다. 임계가 아니라
    색의 물리적 범위다.
    """
    case = _smooth_case()
    a = np.asarray(case.cand.alpha, np.float32)
    ramp = (a > 0) & (a < 1)
    black = case.cand.image_bgr.copy()
    black[ramp] = 0
    good = _run(case).checks["blend"]
    bad = _tamper(case, image_bgr=black).checks["blend"]
    assert good["impliedOutOfGamutPx"] == 0, good
    assert bad["impliedOutOfGamutPx"] > 10_000, bad
    assert bad["impliedLAlphaWeightedDeltaVsWarp"] > (
        good["impliedLAlphaWeightedDeltaVsWarp"] + 10.0), (bad, good)


def test_a_subtle_ramp_luminance_error_is_visible_too():
    """검은 고리만 잡고 끝나면 극단만 잡는 검사다. 완만한 휘도 오류도 드러나야 한다."""
    case = _smooth_case()
    a = np.asarray(case.cand.alpha, np.float32)
    ramp = (a > 0) & (a < 1)
    lab = cv2.cvtColor(case.cand.image_bgr, cv2.COLOR_BGR2LAB).astype(np.int16)
    lab[..., 0][ramp] = np.clip(lab[..., 0][ramp] + 26, 0, 255)     # 약 +10 L*
    lifted = cv2.cvtColor(lab.astype(np.uint8), cv2.COLOR_LAB2BGR)
    good = _run(case).checks["blend"]
    bad = _tamper(case, image_bgr=lifted).checks["blend"]
    assert bad["impliedOutOfGamutPx"] > 1_000, bad
    assert bad["impliedLAlphaWeightedDeltaVsWarp"] > (
        good["impliedLAlphaWeightedDeltaVsWarp"] + 10.0), (bad, good)


@pytest.mark.parametrize("band", [3, 12, 40, 80])
def test_a_correct_render_does_not_drift_with_the_boundary_width(band):
    """옳은 렌더가 밴드 폭에 따라 나빠 보이면 안 된다 — 커진 것은 양자화지 오차가 아니다.

    실측(v4 초안): 같은 올바른 렌더가 band 3/12/40 에서 P95 1.88 → 4.01 → 9.53.
    1/alpha 증폭을 되돌리지 않은 통계였다. 색역 판정도 고정 여유를 써서 band 40 에서
    3,760 px 을 색역 밖이라고 신고했다.
    """
    src = np.full((1000, 1200, 3), (179, 148, 6), np.uint8)
    smask = np.zeros((1000, 1200), np.uint8)
    smask[60:940, 60:1140] = 255
    base = make_panel_map(np.float32([[80, 80], [1120, 80], [1120, 920], [80, 920]]),
                          w=1200, h=1000)
    pm = dataclasses.replace(base, metrics={**base.metrics, "boundary_band_px": band})
    lm = landmarks_for(np.float32([[60, 60], [1139, 60], [1139, 939], [60, 939]]),
                       w=1200, h=1000)
    car = np.full((1000, 1200, 3), (137, 227, 240), np.uint8)
    cand = dtt.transfer_torso_texture(car, pm, src, source_landmarks=lm,
                                      source_garment_mask=smask,
                                      shading=dtt.SHADING_RAW_SOURCE)
    r = _run(_Case(cand, car, src, pm, smask, lm))
    assert r.checks["reconstruction"]["abErrorMedian"] == 0.0
    b = r.checks["blend"]
    assert b["impliedOutOfGamutPx"] == 0, (band, b)
    assert b["scaledAbErrorP95"] < 2.0, (band, b)
    assert b["impliedLAlphaWeightedDeltaVsWarp"] < 2.0, (band, b)


@pytest.mark.parametrize("kw", [
    {"image_bgr": np.zeros((700, 500), np.uint8)},                  # 2-D 이미지
    {"image_bgr": np.full((700, 500, 3), np.nan, np.float32)},      # 비유한 이미지
    {"painted": np.full((700, 500), "x")},                          # 문자열 배열
    {"alpha": np.full((700, 500), "x")},
    {"metrics": {"componentFill": np.zeros(3)}},
    {"metrics": {"paintedPx": np.zeros(3)}},
])
def test_unusable_candidate_output_is_reported_not_raised(kw):
    """후보가 자기 출력을 망가뜨려 채점을 예외로 없앨 수 없다 — NaN 도 내보내지 않는다."""
    case = _raw_candidate()
    report = _run(case, dataclasses.replace(case.cand, **kw))   # 예외 없이 돌아와야 한다
    for values in report.checks.values():
        for v in values.values():
            assert not (isinstance(v, float) and v != v), values


@pytest.mark.parametrize("key,junk", [
    ("componentHomographies", "bogus"),
    ("carrierComponentBoxes", np.array([1, 2, 3])),
    ("sourceComponentBoxes", 17),
    ("sourceGarmentMaskSha256", np.array([1, 2, 3])),
    ("homography", [[float("nan")] * 3] * 3),
    ("sourceQuad", [[float("inf"), 0.0]] * 4),
    ("targetQuad", [[float("nan"), 0.0]] * 4),
])
def test_replay_data_that_cannot_be_replayed_is_not_called_complete(key, junk):
    """'값이 있다'와 '그 값으로 다시 만들 수 있다'는 다른 사건이다."""
    case = _raw_candidate()
    assert _run(case).checks["provenance"]["complete"] is True
    pv = _run(case, dataclasses.replace(
        case.cand, provenance={**case.cand.provenance, key: junk})).checks["provenance"]
    assert pv["complete"] is False, (key, pv)
    assert key in pv["malformedKeys"], (key, pv)


def test_provenance_cannot_be_bought_with_well_formed_lies():
    """형이 맞는 값으로 `complete=True` 를 살 수 없어야 한다.

    실측(v4 초안): sha 를 전부 "x", band 12345, sigma 999, 보간·모드 "garbage" 로 적어도
    missing 0 / malformed 0 / complete True 였다. 그 값으로는 이 호출자의 렌더를 다시
    만들 수 없다.
    """
    case = _raw_candidate()
    assert _run(case).checks["provenance"]["complete"] is True
    lies = {**case.cand.provenance, "sourceSha256": "x", "carrierSha256": "x",
            "garmentMaskSha256": "x", "sourceGarmentMaskSha256": "x",
            "boundaryBandPx": 12345, "shadingSigmaShortSideFrac": 999}
    pv = _run(case, dataclasses.replace(case.cand, provenance=lies)).checks["provenance"]
    assert pv["complete"] is False, pv
    for key in ("sourceSha256", "carrierSha256", "garmentMaskSha256",
                "sourceGarmentMaskSha256", "boundaryBandPx",
                "shadingSigmaShortSideFrac"):
        assert key in pv["mismatchedKeys"], (key, pv)


def test_a_malformed_candidate_array_does_not_erase_the_other_measurements():
    """망가진 `alpha`/`painted` 는 후보의 결함이지 **측정 불가 사유가 아니다**.

    실측(v4 초안): alpha 를 (3,3) 으로 두는 것만으로 7px 밀린 렌더의 재구성 오차
    44.8219 가 통째로 사라졌다 — 막으려던 탈출구를 그대로 만들었다.
    """
    case = _raw_candidate()
    shifted = case.cand.image_bgr.copy()
    shifted[:, 7:] = case.cand.image_bgr[:, :-7]
    base = _run(case, dataclasses.replace(case.cand, image_bgr=shifted)).checks
    assert base["reconstruction"]["abErrorMedian"] > 10.0, base["reconstruction"]
    for kw in ({"alpha": np.zeros((3, 3), np.float32)},
               {"painted": np.zeros((3, 3), np.uint8)}):
        broken = _run(case, dataclasses.replace(
            case.cand, image_bgr=shifted, **kw)).checks
        assert (broken["reconstruction"]["abErrorMedian"]
                == base["reconstruction"]["abErrorMedian"]), (kw, broken)
        assert broken["candidateArrays"]["usable"] is False, broken
        assert broken["candidateArrays"]["unusable"], broken


def test_adding_one_code_across_the_ramp_leaves_the_gamut():
    """색역 여유는 **비대칭**이어야 한다 — 절단은 값을 키우지 않는다.

    실측(v4 초안): 대칭 여유 1/alpha 로는 램프 전체에 BGR +1 을 더해 113,876 px 이
    255.5 를 넘고 최대 335.08 이 됐는데도 색역 밖 0 이었다.
    """
    src = np.full((700, 500, 3), 255, np.uint8)
    smask = np.zeros((700, 500), np.uint8)
    smask[40:660, 40:460] = 255
    base = make_panel_map(IDENTITY_QUAD, w=500, h=700)
    pm = dataclasses.replace(base, metrics={**base.metrics, "boundary_band_px": 80})
    lm = landmarks_for(np.float32([[40, 40], [459, 40], [459, 659], [40, 659]]),
                       w=500, h=700)
    car = np.zeros((700, 500, 3), np.uint8)
    cand = dtt.transfer_torso_texture(car, pm, src, source_landmarks=lm,
                                      source_garment_mask=smask,
                                      shading=dtt.SHADING_RAW_SOURCE)
    case = _Case(cand, car, src, pm, smask, lm)
    a = np.asarray(cand.alpha, np.float32)
    ramp = (a > 0) & (a < 1)
    plus = cand.image_bgr.astype(np.int16)
    plus[ramp] = np.clip(plus[ramp] + 1, 0, 255)
    assert _run(case).checks["blend"]["impliedOutOfGamutPx"] == 0
    bad = _tamper(case, image_bgr=plus.astype(np.uint8)).checks["blend"]
    assert bad["impliedOutOfGamutPx"] > 10_000, bad


@pytest.mark.parametrize("dtype", [np.float32, np.float64, np.int16, np.int64])
def test_a_non_uint8_image_is_refused_rather_than_mismeasured(dtype):
    """Lab 변환은 uint8 BGR 의미를 전제한다. 형만 바꿔도 오차가 0 → 181.02 로 뛰었다."""
    case = _raw_candidate()
    report = _run(case, dataclasses.replace(
        case.cand, image_bgr=case.cand.image_bgr.astype(dtype)))
    assert report.checks["inputs"]["computable"] is False
    assert report.checks["inputs"]["reason"] == "image_or_carrier_not_uint8_bgr"


def test_the_component_edge_count_is_real_and_is_context_not_cause():
    """경계 개수는 **맥락**으로 낸다. 원인이라고 주장하지 않는다 — 구성마다 반대였다."""
    src, smask, m, sbox = _source_with_structure(FOUR_COLOUR)
    pm = make_panel_map(IDENTITY_QUAD, w=500, h=700)
    lm = landmarks_for(np.float32([[m, m], [499 - m, m], [499 - m, 699 - m], [m, 699 - m]]),
                       w=500, h=700)
    car = carrier(500, 700)
    # 실루엣에 닿는 박스를 쓴다 — 부위 경계가 램프와 만나야 셀 것이 생긴다.
    cboxes = {"p": np.float32([[0, 80], [52, 80], [52, 620], [0, 620]])}
    sboxes = {"p": sbox}
    with_comp = dtt.transfer_torso_texture(
        car, pm, src, source_landmarks=lm, source_garment_mask=smask,
        carrier_component_boxes=cboxes, source_component_boxes=sboxes,
        shading=dtt.SHADING_RAW_SOURCE)
    b = _run(_Case(with_comp, car, src, pm, smask, lm, cboxes, sboxes,
                   dtt.SHADING_RAW_SOURCE)).checks["blend"]
    assert b["rampComponentEdgePx"] > 100, b          # 상수 1 이 아니라 실제 둘레
    assert b["rampComponentEdgePx"] < b["rampPx"], b
    # 그리고 올바른 렌더이므로 램프 잔차는 작아야 한다(모드를 안 실으면 여기서 57.7 이
    # 나왔고, 시험은 그것을 보지도 않았다).
    assert b["compositeResidualP95"] < 4.0, b
    # 부위가 없으면 경계도 없다 — 상수를 세고 있지 않다는 증거.
    plain = dtt.transfer_torso_texture(car, pm, src, source_landmarks=lm,
                                       source_garment_mask=smask,
                                       shading=dtt.SHADING_RAW_SOURCE)
    b2 = _run(_Case(plain, car, src, pm, smask, lm,
                    shading=dtt.SHADING_RAW_SOURCE)).checks["blend"]
    assert b2["rampComponentEdgePx"] == 0, b2


def test_the_carrier_reference_detects_a_wrong_shading_field_in_the_ramp():
    """기본 모드 렌더의 램프를 raw-source 합성으로 바꾸면 **carrier 기준**만 반응한다.

    두 기준을 다 내보내는 이유가 이것이다 — 모드마다 저역 출처가 다르므로 한쪽 기준만
    보면 그 모드의 결함이 안 보인다. 다만 이 검사는 **이 치환**을 잡는 것이지, 램프
    휘도 오류 일반을 잡는다는 뜻이 아니다(두 기준 사이의 중간값은 양쪽 모두에서
    좋아진다 — 그 한계는 모듈 docstring 에 적어 두었다).
    """
    src, smask, m = source_with_margin(FOUR_COLOUR)
    src[:] = (50, 50, 50)
    base = make_panel_map(IDENTITY_QUAD, w=500, h=700)
    pm = dataclasses.replace(base, metrics={**base.metrics, "boundary_band_px": 8})
    lm = landmarks_for(IDENTITY_QUAD, w=500, h=700)
    car = np.full((700, 500, 3), 150, np.uint8)
    default_cand = dtt.transfer_torso_texture(
        car, pm, src, source_landmarks=lm, source_garment_mask=smask,
        shading=dtt.SHADING_SOURCE_HIGHFREQ_CARRIER_LOWFREQ)
    raw_cand = dtt.transfer_torso_texture(
        car, pm, src, source_landmarks=lm, source_garment_mask=smask,
        shading=dtt.SHADING_RAW_SOURCE)
    case = _Case(default_cand, car, src, pm, smask, lm)
    a = np.asarray(default_cand.alpha, np.float32)
    ramp = (a > 0) & (a < 1)
    swapped = default_cand.image_bgr.copy()
    swapped[ramp] = raw_cand.image_bgr[ramp]
    good = _run(case).checks["blend"]
    bad = _tamper(case, image_bgr=swapped).checks["blend"]
    assert good["impliedLAlphaWeightedDeltaVsCarrier"] < 1.0, good
    assert bad["impliedLAlphaWeightedDeltaVsCarrier"] > 20.0, bad


def test_a_plausible_but_wrong_ramp_field_cannot_beat_the_correct_one():
    """두 기준과의 거리만 내면 **그 사이 중간값이 양쪽 모두에서 이긴다**.

    실측(v4 라운드 2): 6,444 램프 픽셀을 warp 와 carrier 의 중간값으로 정직하게 합성해
    넣자(평균 |ΔBGR| 25.4, 최대 72) a/b 0.1228 → 0.0125, scaled P95 0.5432 → 0.0334,
    L-vs-warp 27.37 → 12.36, L-vs-carrier 17.49 → 12.98 — **네 지표 전부 좋아졌다**.
    통계를 바꿔서 될 일이 아니라 기대값이 **결정**돼야 하는 문제다.
    """
    src, smask, m = source_with_margin(FOUR_COLOUR)
    src[:] = cv2.cvtColor(cv2.cvtColor(src, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
    base = make_panel_map(IDENTITY_QUAD, w=500, h=700)
    pm = dataclasses.replace(base, metrics={**base.metrics, "boundary_band_px": 3})
    lm = landmarks_for(IDENTITY_QUAD, w=500, h=700)
    car = np.full((700, 500, 3), 120, np.uint8)
    mode = dtt.SHADING_SOURCE_HIGHFREQ_CARRIER_LOWFREQ
    cand = dtt.transfer_torso_texture(car, pm, src, source_landmarks=lm,
                                      source_garment_mask=smask, shading=mode)
    case = _Case(cand, car, src, pm, smask, lm, shading=mode)
    geom = qc._caller_geometry(pm, lm, src.shape)
    rec = qc._reconstruct_from_caller(src, cand.image_bgr.shape[:2], geom[0], geom[1],
                                      None, None, smask)
    a = np.asarray(cand.alpha, np.float32)
    ramp = (a > 0) & (a < 1)
    mid = (rec["warped"].astype(np.float64) + car.astype(np.float64)) // 2
    al = a[..., None].astype(np.float64)
    fake = np.clip(al * mid + (1 - al) * car.astype(np.float64), 0, 255).astype(np.uint8)
    wrong = cand.image_bgr.copy()
    wrong[ramp] = fake[ramp]

    good = _run(case).checks["blend"]
    bad = _tamper(case, image_bgr=wrong).checks["blend"]
    # 옳은 렌더는 기대 합성값과 **한 코드 안**에서 일치한다.
    assert good["compositeResidualMax"] < 2.0, good
    assert bad["compositeResidualMedian"] > 10.0, bad
    assert bad["compositeResidualMax"] > 50.0, bad


@pytest.mark.parametrize("mode", [dtt.SHADING_RAW_SOURCE,
                                  dtt.SHADING_SOURCE_HIGHFREQ_CARRIER_LOWFREQ,
                                  dtt.SHADING_CARRIER_LOW_FREQ_L])
def test_the_expected_composite_matches_the_renderer_in_every_shading_mode(mode):
    """오라클의 기대 합성값이 렌더러와 어긋나면 옳은 렌더가 램프에서 틀렸다고 나온다."""
    src, smask, m = source_with_margin(FOUR_COLOUR)
    base = make_panel_map(IDENTITY_QUAD, w=500, h=700)
    pm = dataclasses.replace(base, metrics={**base.metrics, "boundary_band_px": 6})
    lm = landmarks_for(IDENTITY_QUAD, w=500, h=700)
    car = carrier(500, 700, shade=1)
    cand = dtt.transfer_torso_texture(car, pm, src, source_landmarks=lm,
                                      source_garment_mask=smask, shading=mode)
    b = _run(_Case(cand, car, src, pm, smask, lm, shading=mode)).checks["blend"]
    assert b["compositeResidualMedian"] < 2.0, (mode, b)
    assert b["compositeResidualP95"] < 4.0, (mode, b)


def test_an_unknown_shading_mode_is_reported_as_undecidable_not_guessed():
    """모르는 모드에서는 램프 기대값을 만들 수 없다 — 0 이라고 우기지 않는다."""
    case = _raw_candidate()
    blind = _Case(case.cand, case.car, case.src, case.pm, case.smask, case.lm,
                  shading="something_else")
    b = _run(blind).checks["blend"]
    assert b["compositeResidualMedian"] is None, b
    # 모드와 무관한 축(색역)은 그대로 살아 있다.
    assert b["impliedOutOfGamutPx"] == 0, b


def test_a_ragged_candidate_plane_does_not_erase_the_report():
    """가드가 `np.asarray` **밖**에 있으면 가드가 아니다 — 변환 자체가 던진다."""
    case = _raw_candidate()
    shifted = case.cand.image_bgr.copy()
    shifted[:, 7:] = case.cand.image_bgr[:, :-7]
    base = _run(case, dataclasses.replace(case.cand, image_bgr=shifted)).checks
    for kw in ({"alpha": [[0], [0, 0]]}, {"painted": [[0], [0, 0]]}):
        broken = _run(case, dataclasses.replace(
            case.cand, image_bgr=shifted, **kw)).checks
        assert (broken["reconstruction"]["abErrorMedian"]
                == base["reconstruction"]["abErrorMedian"]), (kw, broken)
        assert broken["candidateArrays"]["usable"] is False, broken


def test_a_small_target_is_still_measured_pixel_for_pixel():
    """고정 256 컷오프가 작은 대상을 통째로 무측정으로 만들었다.

    실측(v5 초안): 내부 9 px 을 전부 (1,2,3) 으로 바꿔 최대 226 코드 오차를 넣었는데
    correct 와 defective 의 checks 딕셔너리가 **완전히 같았다**. 픽셀당 오차에는 최소
    표본이 필요 없다 — 표본이 필요한 것은 밴드 통계뿐이다.
    """
    src, smask, m = source_with_margin(FOUR_COLOUR)
    pm = make_panel_map(np.float32([[250, 350], [258, 350], [258, 358], [250, 358]]),
                        w=500, h=700)
    lm = landmarks_for(np.float32([[m, m], [499 - m, m], [499 - m, 699 - m], [m, 699 - m]]),
                       w=500, h=700)
    car = carrier(500, 700)
    cand = dtt.transfer_torso_texture(car, pm, src, source_landmarks=lm,
                                      source_garment_mask=smask,
                                      shading=dtt.SHADING_RAW_SOURCE)
    case = _Case(cand, car, src, pm, smask, lm, shading=dtt.SHADING_RAW_SOURCE)
    a = np.asarray(cand.alpha, np.float32)
    interior = (cand.painted > 0) & (a >= 1.0 - 1e-6)
    assert 0 < int(interior.sum()) < 256, int(interior.sum())
    wrong = cand.image_bgr.copy()
    wrong[interior] = (1, 2, 3)

    good = _run(case).checks
    bad = _tamper(case, image_bgr=wrong).checks
    assert good["reconstruction"]["computable"] is True, good["reconstruction"]
    assert good["reconstruction"]["abErrorMedian"] < 1.0, good["reconstruction"]
    assert bad["reconstruction"]["abErrorMedian"] > 10.0, bad["reconstruction"]
    # 표본이 적으므로 **밴드 통계만** 정의되지 않는다고 말한다.
    assert good["reconstruction"]["L_highBandDefined"] is False, good["reconstruction"]


def test_the_source_image_must_also_be_uint8():
    """원본이 uint8 이 아니면 warp·Lab 이 다른 의미가 된다 — 옳은 렌더에 154.03 이 났다."""
    case = _raw_candidate()
    report = _run(_Case(case.cand, case.car, case.src.astype(np.float32), case.pm,
                        case.smask, case.lm, shading=dtt.SHADING_RAW_SOURCE))
    assert report.checks["inputs"]["computable"] is False
    assert report.checks["inputs"]["reason"] == "source_not_uint8_bgr"


@pytest.mark.parametrize("key,junk", [
    ("shadingMode", "carrier_low_freq_l"),
    ("version", "forged_v999"),
    ("interpolation", "INTER_NEAREST"),
    ("sourceMaskInterpolation", "INTER_NEAREST"),
])
def test_a_forged_render_declaration_is_caught(key, junk):
    """모드·보간·판본은 전부 픽셀을 바꾸는 선언이다. 형만 맞으면 통과하면 안 된다."""
    case = _raw_candidate()
    pv = _run(case, dataclasses.replace(
        case.cand, provenance={**case.cand.provenance, key: junk})).checks["provenance"]
    assert pv["complete"] is False, (key, pv)
    assert key in pv["mismatchedKeys"], (key, pv)


def test_caller_supplied_lineage_hashes_are_not_a_mismatch():
    """호출자가 원본 파일 계보를 넘기면 렌더러는 그것을 기록한다 — 오탐이면 안 된다."""
    src, smask, m = source_with_margin(FOUR_COLOUR)
    pm = make_panel_map(IDENTITY_QUAD, w=500, h=700)
    lm = landmarks_for(IDENTITY_QUAD, w=500, h=700)
    car = carrier(500, 700)
    cand = dtt.transfer_torso_texture(
        car, pm, src, source_landmarks=lm, source_garment_mask=smask,
        shading=dtt.SHADING_RAW_SOURCE, source_sha256="src", carrier_sha256="car")
    pv = qc.evaluate_direct_transfer(
        cand, carrier_bgr=car, source_bgr=src, panel_map=pm, source_landmarks=lm,
        shading=dtt.SHADING_RAW_SOURCE, source_sha256="src", carrier_sha256="car",
        source_garment_mask=smask).checks["provenance"]
    assert pv["complete"] is True, pv
    assert pv["mismatchedKeys"] == [], pv


def test_a_sub_64_pixel_ramp_is_still_measured():
    """램프에도 최소 표본이 없다 — 256 만 고치고 64 를 남긴 것이 같은 실수의 형제였다.

    실측(v6 초안): 램프 60 px 을 전부 (1,2,3) 으로 바꿔 평균 |ΔBGR| 170.88 을 넣었는데
    correct 와 defective 의 checks 가 **완전히 같았다**(`reason="no_ramp"`).
    """
    src, smask, m = source_with_margin(FOUR_COLOUR)
    pm = make_panel_map(np.float32([[250, 350], [257, 350], [257, 357], [250, 357]]),
                        w=500, h=700)
    lm = landmarks_for(np.float32([[m, m], [499 - m, m], [499 - m, 699 - m], [m, 699 - m]]),
                       w=500, h=700)
    car = carrier(500, 700)
    cand = dtt.transfer_torso_texture(car, pm, src, source_landmarks=lm,
                                      source_garment_mask=smask,
                                      shading=dtt.SHADING_RAW_SOURCE)
    case = _Case(cand, car, src, pm, smask, lm, shading=dtt.SHADING_RAW_SOURCE)
    a = np.asarray(cand.alpha, np.float32)
    ramp = (a > 0) & (a < 1)
    assert 0 < int(ramp.sum()) < 64, int(ramp.sum())
    wrong = cand.image_bgr.copy()
    wrong[ramp] = (1, 2, 3)
    good = _run(case).checks["blend"]
    bad = _tamper(case, image_bgr=wrong).checks["blend"]
    assert good["computable"] is True, good
    assert good["compositeResidualMedian"] < 2.0, good
    assert bad["compositeResidualMedian"] > 50.0, bad


def test_a_ragged_candidate_image_does_not_raise():
    """`_plane` 만 감싸고 이미지의 asarray 를 남긴 것이 부분 수정이었다."""
    case = _raw_candidate()
    report = _run(case, dataclasses.replace(case.cand, image_bgr=[[[0]], [[0], [0]]]))
    assert report.checks["inputs"]["computable"] is False
    assert report.checks["inputs"]["reason"] == "image_unconvertible"


def test_a_rotated_component_does_not_get_a_false_direction_error():
    """방향은 **몸통 사상**의 검사다. 부위는 자기 사상으로 옮겨지므로 섞으면 안 된다.

    실측(v6 초안): 45도 돌린 부위가 올바르게 채워졌는데(60,138 px, 재구성 오차 0.0)
    방향 검사는 예측 [1,0] 대 관측 [0.707,0.707] 로 45.0도 오차를 냈다.
    """
    src = np.full((700, 500, 3), 128, np.uint8)
    smask = np.zeros((700, 500), np.uint8)
    smask[40:660, 40:460] = 255
    src[200:500, 150:350] = np.where(
        (np.arange(200) // 6 % 2)[None, :, None] == 0, 0, 255)
    sbox = np.float32([[150, 200], [350, 200], [350, 500], [150, 500]])
    theta, centre = np.pi / 4, np.float32([250, 350])
    rot = np.float32([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
    cbox = ((sbox - centre) @ rot.T + centre).astype(np.float32)
    pm = make_panel_map(IDENTITY_QUAD, w=500, h=700)
    lm = landmarks_for(IDENTITY_QUAD, w=500, h=700)
    car = np.full((700, 500, 3), 128, np.uint8)
    cboxes, sboxes = {"p": cbox}, {"p": sbox}
    cand = dtt.transfer_torso_texture(
        car, pm, src, source_landmarks=lm, source_garment_mask=smask,
        carrier_component_boxes=cboxes, source_component_boxes=sboxes,
        shading=dtt.SHADING_RAW_SOURCE)
    r = _run(_Case(cand, car, src, pm, smask, lm, cboxes, sboxes,
                   dtt.SHADING_RAW_SOURCE)).checks
    assert r["reconstruction"]["abErrorMedian"] < 1.0, r["reconstruction"]
    d = r["direction"]
    # 부위를 뺀 몸통에는 결이 없으므로 **정직하게 계산 불가**라고 말해야 한다.
    # 어떤 경우에도 45도 오차를 지어내면 안 된다.
    assert d["computable"] is False or d["angleErrorDeg"] < 5.0, d


def test_a_component_the_renderer_rejects_is_not_expected_in_provenance():
    """렌더러가 자격 미달로 거부한 부위를 QC 가 기대하면 옳은 렌더가 불일치가 된다."""
    src, smask, m = source_with_margin(FOUR_COLOUR)
    pm = make_panel_map(IDENTITY_QUAD, w=500, h=700)
    lm = landmarks_for(IDENTITY_QUAD, w=500, h=700)
    car = carrier(500, 700)
    # 짧은 변 10px — 렌더러가 `short_side_10px` 로 거부한다.
    cboxes = {"p": np.float32([[220, 100], [230, 100], [230, 600], [220, 600]])}
    sboxes = {"p": np.float32([[100, 100], [110, 100], [110, 600], [100, 600]])}
    cand = dtt.transfer_torso_texture(
        car, pm, src, source_landmarks=lm, source_garment_mask=smask,
        carrier_component_boxes=cboxes, source_component_boxes=sboxes,
        shading=dtt.SHADING_RAW_SOURCE)
    assert cand.provenance["componentHomographies"] == {}, cand.provenance
    pv = _run(_Case(cand, car, src, pm, smask, lm, cboxes, sboxes,
                    dtt.SHADING_RAW_SOURCE)).checks["provenance"]
    assert pv["complete"] is True, pv
    assert pv["mismatchedKeys"] == [], pv


def test_a_wrong_component_homography_is_still_caught():
    """거부된 부위를 빼는 것과 **틀린 값을 눈감는 것**은 다르다."""
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
    case = _Case(cand, car, src, pm, smask, lm, cboxes, sboxes, dtt.SHADING_RAW_SOURCE)
    assert _run(case).checks["provenance"]["complete"] is True
    identity = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    pv = _run(case, dataclasses.replace(cand, provenance={
        **cand.provenance, "componentHomographies": {"p": identity}})).checks["provenance"]
    assert pv["complete"] is False, pv
    assert "componentHomographies" in pv["mismatchedKeys"], pv


@pytest.mark.parametrize("band", [4.123456, 2.5, 7.75])
def test_a_fractional_boundary_band_is_not_a_provenance_mismatch(band):
    """렌더러는 밴드를 소수 4자리로 반올림해 기록한다 — 정확 비교는 오탐을 만든다."""
    src, smask, m = source_with_margin(FOUR_COLOUR)
    base = make_panel_map(IDENTITY_QUAD, w=500, h=700)
    pm = dataclasses.replace(base, metrics={**base.metrics, "boundary_band_px": band})
    lm = landmarks_for(IDENTITY_QUAD, w=500, h=700)
    car = carrier(500, 700)
    cand = dtt.transfer_torso_texture(car, pm, src, source_landmarks=lm,
                                      source_garment_mask=smask,
                                      shading=dtt.SHADING_RAW_SOURCE)
    pv = _run(_Case(cand, car, src, pm, smask, lm,
                    shading=dtt.SHADING_RAW_SOURCE)).checks["provenance"]
    assert pv["complete"] is True, (band, pv)


def test_a_locally_reversed_component_mapping_is_detected():
    """한 점만 보면 **국소적으로** 뒤집힌 오목 사상을 놓친다.

    실측(v8 초안): 오목한 target 박스에서 실제로 칠한 30,003 px 중 11,022 px 의
    야코비 행렬식이 음수인데, 중심에서 잰 값은 양수라 검사가 깨끗했다.
    """
    src, smask, m = source_with_margin(FOUR_COLOUR)
    pm = make_panel_map(IDENTITY_QUAD, w=500, h=700)
    lm = landmarks_for(IDENTITY_QUAD, w=500, h=700)
    car = carrier(500, 700)
    sboxes = {"p": np.float32([[100, 100], [300, 100], [300, 500], [100, 500]])}
    cboxes = {"p": np.float32([[100, 100], [400, 100], [120, 460], [100, 500]])}
    cand = dtt.transfer_torso_texture(
        car, pm, src, source_landmarks=lm, source_garment_mask=smask,
        carrier_component_boxes=cboxes, source_component_boxes=sboxes,
        shading=dtt.SHADING_RAW_SOURCE)
    mp = _run(_Case(cand, car, src, pm, smask, lm, cboxes, sboxes,
                    dtt.SHADING_RAW_SOURCE)).checks["mapping"]
    assert mp["componentNegDetPx"]["p"] > 1_000, mp
    assert "p" in mp["reflectedComponents"], mp


def test_a_thin_convex_component_is_not_called_mirrored():
    """유한차분 한 걸음이 지평선을 넘으면 멀쩡한 부위가 거울상으로 신고된다.

    실측(v8 초안): 874 px 전부 행렬식이 양수(0.0789~6.3735)인데 중심 차분은 -0.0182.
    """
    src, smask, m = source_with_margin(FOUR_COLOUR)
    pm = make_panel_map(IDENTITY_QUAD, w=500, h=700)
    lm = landmarks_for(IDENTITY_QUAD, w=500, h=700)
    car = carrier(500, 700)
    sboxes = {"placket": np.float32([[200, 80], [220, 80], [220, 580], [200, 580]])}
    cboxes = {"placket": np.float32([[209.3851624, 52.9599380],
                                     [194.9279785, 120.2625961],
                                     [189.9193115, 140.8555908],
                                     [84.4222183, 568.1336060]])}
    cand = dtt.transfer_torso_texture(
        car, pm, src, source_landmarks=lm, source_garment_mask=smask,
        carrier_component_boxes=cboxes, source_component_boxes=sboxes,
        shading=dtt.SHADING_RAW_SOURCE)
    mp = _run(_Case(cand, car, src, pm, smask, lm, cboxes, sboxes,
                    dtt.SHADING_RAW_SOURCE)).checks["mapping"]
    assert mp["componentNegDetPx"]["placket"] == 0, mp
    assert mp["reflectedComponents"] == [], mp


def test_a_recorded_quad_off_by_half_a_pixel_is_not_a_match():
    """허용오차는 **기록 폭**(소수 3자리)이지 0.5 px 이 아니다."""
    case = _raw_candidate()
    assert _run(case).checks["geometry"]["quadsMatchCaller"] is True
    shifted = [[float(x) + 0.5, float(y)]
               for x, y in case.cand.provenance["targetQuad"]]
    g = _run(case, dataclasses.replace(case.cand, provenance={
        **case.cand.provenance, "targetQuad": shifted})).checks["geometry"]
    assert g["quadsMatchCaller"] is False, g
    assert g["targetQuadMaxDeltaPx"] == 0.5, g


def test_a_valid_convex_torso_mapping_is_not_refused():
    """렌더러의 유효성 격자가 quad 위치를 무시하면 멀쩡한 사상이 거부된다.

    실측(v8 초안): quad 가 (64,273) 에서 시작하는데 격자는 (0,0)…(372,269) 를 쟀고,
    86,247 px 전부 행렬식이 양수인데 `neg_jacobian=10` 으로 `homography_degenerate`.
    """
    src, _smask, m = source_with_margin(FOUR_COLOUR)
    sq = np.float32([[64, 273], [390, 255], [418, 524], [46, 495]])
    tq = np.float32([[135, 89], [369, 34], [345, 525], [206, 547]])
    pm = make_panel_map(tq, w=500, h=700)
    lm = landmarks_for(sq, w=500, h=700)
    car = carrier(500, 700)
    cand = dtt.transfer_torso_texture(
        car, pm, src, source_landmarks=lm,
        source_garment_mask=np.full((700, 500), 255, np.uint8),
        shading=dtt.SHADING_RAW_SOURCE)
    assert isinstance(cand, dtt.DirectTorsoCandidate), getattr(cand, "reason", cand)
    mp = _run(_Case(cand, car, src, pm, np.full((700, 500), 255, np.uint8), lm,
                    shading=dtt.SHADING_RAW_SOURCE)).checks["mapping"]
    assert mp["torsoNegDetPx"] == 0, mp
    assert mp["torsoReflected"] is False, mp


def test_a_component_outside_the_source_is_skipped_not_called_mirrored():
    """원본 밖 박스는 렌더러가 `out_of_source` 로 건너뛴다 — 그리지 않는 것은 결함이 아니다."""
    case = _raw_candidate()
    cboxes = {"p": np.float32([[200, 200], [260, 200], [260, 260], [200, 260]])}
    sboxes = {"p": np.float32([[600, 100], [660, 100], [660, 160], [600, 160]])}
    cand = dtt.transfer_torso_texture(
        case.car, case.pm, case.src, source_landmarks=case.lm,
        source_garment_mask=case.smask, carrier_component_boxes=cboxes,
        source_component_boxes=sboxes, shading=dtt.SHADING_RAW_SOURCE)
    mp = _run(_Case(cand, case.car, case.src, case.pm, case.smask, case.lm,
                    cboxes, sboxes, dtt.SHADING_RAW_SOURCE)).checks["mapping"]
    assert mp["reflectedComponents"] == [], mp
    assert "p" not in (mp["componentNegDetPx"] or {}), mp


def test_an_overflowing_provenance_value_does_not_erase_the_measurements():
    """`float()` 는 거대한 정수에서 OverflowError 를 던진다 — 그것도 가드 안이어야 한다."""
    case = _raw_candidate()
    report = _run(case, dataclasses.replace(
        case.cand, provenance={**case.cand.provenance, "homography": 10 ** 10000}))
    assert report.checks["reconstruction"]["computable"] is True
    assert report.checks["reconstruction"]["abErrorMedian"] < 1.0
    assert report.checks["geometry"]["recordedHomographyUsable"] is False


def test_the_colour_check_carries_no_candidate_telemetry():
    """측정 구역에 자기보고를 섞으면 그 구역이 더 이상 측정이 아니다."""
    case = _raw_candidate()
    colour = _run(case, dataclasses.replace(
        case.cand, metrics={**case.cand.metrics,
                            "measuredChromaCastAb": [999, -999]})).checks["colour"]
    assert "measuredChromaCastAb" not in colour, colour
    assert colour["perPixelAbErrorMedian"] < 1.0, colour


@pytest.mark.parametrize("patch", [
    {"periodInputs": {"targetPeriodPx": 20.0}},
    {"periodInputs": 1.0},
])
def test_a_period_declaration_is_not_part_of_this_mode(patch):
    """이 모드의 존재 이유가 '주기가 없다' 이다 — 그 선언이 실려 있으면 다른 경로다."""
    case = _raw_candidate()
    pv = _run(case, dataclasses.replace(
        case.cand, provenance={**case.cand.provenance, **patch})).checks["provenance"]
    assert pv["complete"] is False, pv
    assert "periodInputs" in pv["mismatchedKeys"], pv


def test_the_quad_tolerance_is_absolute_not_relative():
    """`np.allclose` 의 기본 rtol 은 좌표가 클수록 허용오차를 키운다."""
    case = _raw_candidate()
    quad = [[float(x), float(y)] for x, y in case.cand.provenance["targetQuad"]]
    quad[0][0] += 0.0076
    g = _run(case, dataclasses.replace(case.cand, provenance={
        **case.cand.provenance, "targetQuad": quad})).checks["geometry"]
    assert g["quadsMatchCaller"] is False, g


def test_a_non_binary_painted_mask_is_not_accepted():
    """`painted` 의 계약은 0/255 다. 다른 값이면 무엇을 주장하는지 알 수 없다."""
    case = _raw_candidate()
    r = _run(case, dataclasses.replace(
        case.cand, painted=np.where(case.cand.painted > 0, 1, 0).astype(np.int16)))
    assert r.checks["candidateArrays"]["usable"] is False, r.checks["candidateArrays"]


def test_a_valid_convex_torso_inside_its_quad_is_not_refused():
    """경계 상자는 quad 밖을 포함한다 — 검사는 실제로 그리는 영역에서만 의미가 있다."""
    src, _sm, m = source_with_margin(FOUR_COLOUR)
    sq = np.float32([[46.3142, 321.1765], [345.5297, 185.3680],
                     [335.1556, 673.5323], [227.1313, 636.6028]])
    tq = np.float32([[145, 146], [355, 160], [341, 524], [159, 510]])
    pm = make_panel_map(tq, w=500, h=700)
    cand = dtt.transfer_torso_texture(
        carrier(500, 700), pm, src, source_landmarks=landmarks_for(sq, w=500, h=700),
        source_garment_mask=np.full((700, 500), 255, np.uint8),
        shading=dtt.SHADING_RAW_SOURCE)
    assert isinstance(cand, dtt.DirectTorsoCandidate), getattr(cand, "reason", cand)


def test_forged_metrics_cannot_move_any_derived_measurement():
    """`metrics` 는 후보가 스스로 적은 값이다. 오라클의 판단이 거기 기대면 안 된다.

    (v3 초안은 침식 반경을 `metrics.shadingSigmaPx` 에서 가져왔다 — 후보가 반경을 키워
     지지 영역을 지우고 `insufficient_support` 뒤로 숨을 수 있었다.)
    """
    case = _raw_candidate()
    honest = _run(case).checks
    # `neg_jacobian` 은 **참 같은 값**이어야 한다. `None` 을 쓰면 `bool(None)` 이 우연히
    # 옳은 답(False)과 같아서, 자기보고로 반사를 유도하는 변종이 통과한다.
    lying = _tamper(case, metrics={**case.cand.metrics, "shadingSigmaPx": 4000.0,
                                   "paintedPx": 1, "neg_jacobian": 7}).checks
    for name in ("geometry", "reconstruction", "direction", "colour"):
        assert lying[name] == honest[name], (name, lying[name], honest[name])
    # `mapping` 은 합의된 echo(`negJacobian` 등)를 함께 싣는다 — 그 값은 당연히 바뀐다.
    # **호출자 기하에서 유도한 항목들**은 흔들리면 안 된다. 이것이 빠져 있어서, 계산
    # 가능 여부를 자기보고로 되돌리는 변종이 243개 시험을 모두 통과했다.
    for key in ("computable", "torsoReflected", "reflectedComponents", "torsoNegDetPx",
                "componentNegDetPx", "callerAnisotropyP99", "callerStretchOverFrac"):
        assert lying["mapping"][key] == honest["mapping"][key], key
    # 다만 자기보고가 어긋났다는 사실 자체는 드러나야 한다.
    assert lying["domain"]["paintedMatchesMetrics"] is False
    assert honest["domain"]["paintedMatchesMetrics"] is True


def test_a_forged_provenance_cannot_make_the_oracle_uncomputable():
    """후보가 provenance 를 지워 채점을 회피하지 못한다 — 기준이 밖에 있기 때문이다."""
    case = _raw_candidate()
    wiped = _tamper(case, provenance={**case.cand.provenance, "sourceQuad": [],
                                      "targetQuad": [], "homography": []}).checks
    for name in ("reconstruction", "direction", "colour", "domain"):
        assert wiped[name]["computable"] is True, (name, wiped[name])
    assert wiped["reconstruction"]["abErrorMedian"] < 1.0, wiped["reconstruction"]
    assert wiped["geometry"]["quadsMatchCaller"] is False, wiped["geometry"]


def _raw_candidate():
    src, smask, m = source_with_margin(FOUR_COLOUR)
    pm = make_panel_map(IDENTITY_QUAD, w=500, h=700)
    lm = landmarks_for(np.float32([[m, m], [499 - m, m], [499 - m, 699 - m], [m, 699 - m]]),
                       w=500, h=700)
    car = carrier(500, 700)
    cand = dtt.transfer_torso_texture(car, pm, src, source_landmarks=lm,
                                      source_garment_mask=smask,
                                      shading=dtt.SHADING_RAW_SOURCE)
    return _Case(cand, car, src, pm, smask, lm,
                 shading=dtt.SHADING_RAW_SOURCE)


def test_the_shared_homography_helper_preserves_negative_w():
    """`np.maximum(w, eps)` 는 w<0 을 1e-9 로 바꿔 좌표를 폭발시킨다.

    정상적인 quad 쌍도 전 영역에서 w<0 일 수 있다(실측: -1.5753…-0.3078). 그때 이
    클램프가 주기 경로에서 멀쩡한 부위를 `scale_resample_unsupported`·coverage 0.0 으로
    만들었다. 이 시험은 **공유 헬퍼**의 부호 보존을 못 박는다.
    """
    from app.services.hybrid_composite.warp_composite import _apply_homography
    sq = np.float32([[42.883499, 144.28287], [271.5331, 165.70212],
                     [390.62994, 333.0186], [20.868774, 338.59686]])
    tq = np.float32([[116.94803, 148.23709], [282.02374, 155.90541],
                     [234.88031, 608.34698], [168.44727, 597.95905]])
    h_inv = np.linalg.inv(cv2.getPerspectiveTransform(sq, tq))
    pts = np.array([[150.0, 300.0], [200.0, 400.0], [250.0, 500.0]], np.float64)
    ph = np.concatenate([pts, np.ones((len(pts), 1))], axis=1)
    w = (h_inv @ ph.T).T[:, 2]
    assert (w < 0).all(), w        # 픽스처가 실제로 음수 w 를 만든다

    got = _apply_homography(h_inv, pts)
    expect = np.array([(h_inv @ np.r_[p, 1.0])[:2] / (h_inv @ np.r_[p, 1.0])[2]
                       for p in pts], np.float32)
    assert np.allclose(got, expect, atol=1e-3), (got, expect)
    # 폭발하지 않았는지도 본다 — 클램프는 1e12 규모를 만든다.
    assert np.abs(got).max() < 1e4, got


def test_a_non_finite_alpha_is_counted_from_the_candidate_not_the_replacement():
    """위생 처리한 대체값을 재면 결함이 사라진다 — 원본 기준으로 말해야 한다.

    실측(v17 초안): NaN 이 정확히 1개인데 `alphaFinite=True`, `alphaNonFinitePx=0` 로
    보고됐다(승격은 `candidate_arrays_unusable` 로 막혔지만, 측정 자체가 틀렸다).
    """
    case = _raw_candidate()
    alpha = np.asarray(case.cand.alpha, np.float32).copy()
    alpha[350, 250] = np.nan
    r = _run(case, dataclasses.replace(case.cand, alpha=alpha)).checks
    assert r["containment"]["alphaFinite"] is False, r["containment"]
    assert r["containment"]["alphaNonFinitePx"] == 1, r["containment"]
    assert r["candidateArrays"]["usable"] is False
