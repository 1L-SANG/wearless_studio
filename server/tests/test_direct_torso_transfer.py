"""주기 없이 원본 몸통 픽셀을 옮기는 후보 — 결정론 오라클 검증.

오라클 독립성: 원본은 '직사각형을 순서대로 칠하기'로 그리고, 기대값은 **homography 를
해석적으로 적용해** 계산한다. 결과 판정에 `stripe_model` 을 쓰지 않는다 — 추출기로
추출기를 검증하면 순환이다.

이 phase 의 질문 하나: 주기 진실 없이도 원본에 근거한 결정론 몸통 후보를 만들 수 있는가.
승격(READY/authority) 은 여기서 다루지 않는다.
"""

import hashlib
import inspect

import cv2
import numpy as np
import pytest

from app.services.hybrid_composite import direct_torso_transfer as dtt
from app.services.hybrid_composite.color import bgr_to_lab
from app.services.hybrid_composite.panel_map import Panel, PanelMap

# ── 오라클 원단 ─────────────────────────────────────────────────────────────
TWO_COLOUR = [((235, 235, 235), 12), ((60, 60, 60), 8)]                  # repeat 20
FOUR_COLOUR = [((228, 228, 228), 14), ((190, 110, 60), 2),
               ((90, 155, 90), 2), ((70, 70, 190), 2)]                   # repeat 20
BACKGROUND = (255, 0, 255)          # 원단에 절대 없는 마젠타 — 오염 탐지용


def draw_stripes(runs, w, h, *, background=None):
    img = np.zeros((h, w, 3), np.uint8)
    if background is not None:
        img[:] = background
    period = sum(px for _c, px in runs)
    x = 0
    while x < w:
        off = 0
        for bgr, px in runs:
            img[:, x + off:min(x + off + px, w)] = bgr
            off += px
        x += period
    return img


def source_with_margin(runs, *, w=500, h=700, margin=30):
    """옷은 안쪽에만 있고 바깥은 마젠타 배경 — quad 가 새면 즉시 드러난다."""
    img = np.full((h, w, 3), BACKGROUND, np.uint8)
    fabric = draw_stripes(runs, w - 2 * margin, h - 2 * margin)
    img[margin:h - margin, margin:w - margin] = fabric
    mask = np.zeros((h, w), np.uint8)
    mask[margin:h - margin, margin:w - margin] = 255
    return img, mask, margin


def landmarks_for(quad_px, *, w, h):
    """(4,2) px quad(TL,TR,BR,BL) → 정규화 landmark dict."""
    (tlx, tly), (trx, try_), (brx, bry), (blx, bly) = [tuple(p) for p in quad_px]
    return {"shoulder_l": [tlx / w, tly / h], "shoulder_r": [trx / w, try_ / h],
            "hem_r": [brx / w, bry / h], "hem_l": [blx / w, bly / h]}


def make_panel_map(target_quad, *, w, h, mask=None):
    if mask is None:
        mask = np.zeros((h, w), np.uint8)
        cv2.fillPoly(mask, [np.asarray(target_quad, np.int32)], 255)
    protected = cv2.erode(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
    return PanelMap(garment_mask=mask, protected=protected,
                    boundary=cv2.subtract(mask, protected),
                    panels=(Panel("torso", "stripe", np.asarray(target_quad, np.float32)),),
                    confidence=1.0, strategy="synthetic",
                    metrics={"boundary_band_px": 3})


def carrier(w, h, *, gradient=False):
    img = np.full((h, w, 3), 150, np.uint8)
    if gradient:                    # 세로 저주파 음영 — carrier 주름의 대역
        ramp = np.linspace(0.65, 1.35, h).reshape(-1, 1, 1)
        img = np.clip(img.astype(np.float64) * ramp, 0, 255).astype(np.uint8)
    return img


def nearest_labels(bgr_row, runs):
    ref = bgr_to_lab(np.array([[c for c, _w in runs]], np.uint8))[0].astype(np.float64)
    lab = bgr_to_lab(bgr_row[None, :, :])[0].astype(np.float64)
    d = np.linalg.norm(lab[:, None, :] - ref[None, :, :], axis=2)
    return np.argmin(d, axis=1), d.min(axis=1)


def predicted_labels(H, xs, y, runs, origin):
    """target 행 픽셀 → homography 역사상 → 원본 x → 그린 규칙 그대로의 색 index.

    이것이 오라클이다. 렌더러를 전혀 쓰지 않고 '무엇이 나와야 하는가' 를 해석적으로 만든다.
    `origin` 은 원단이 시작하는 원본 x — 위상의 기준이다(마진만큼 어긋나면 오라클이 틀린다).
    """
    period = sum(px for _c, px in runs)
    bounds = np.cumsum([px for _c, px in runs])
    Hinv = np.linalg.inv(H)
    pts = np.stack([xs.astype(np.float64), np.full(len(xs), float(y)), np.ones(len(xs))])
    src = Hinv @ pts
    sx = src[0] / src[2]
    return np.searchsorted(bounds, np.mod(sx - origin, period), side="right"), sx


def runs_of(labels):
    out, s = [], 0
    for i in range(1, len(labels) + 1):
        if i == len(labels) or labels[i] != labels[s]:
            out.append((int(labels[s]), i - s))
            s = i
    return out


def interior_row(candidate, panel_map, *, margin=6):
    """alpha==1 인 내부 스캔라인 — feather·경계 픽셀을 판정에서 제외한다."""
    ys = np.nonzero((candidate.alpha > 0.999).any(axis=1))[0]
    y = int(ys[len(ys) // 2])
    xs = np.nonzero(candidate.alpha[y] > 0.999)[0]
    return y, np.arange(int(xs.min()) + margin, int(xs.max()) - margin)


def run_transfer(runs, target_quad, *, source_size=(500, 700), carrier_size=None,
                 shading=dtt.SHADING_RAW_SOURCE, gradient=False, margin=30):
    sw, sh = source_size
    cw, ch = carrier_size or (sw, sh)
    src, smask, m = source_with_margin(runs, w=sw, h=sh, margin=margin)
    src_quad = np.float32([[m, m], [sw - m - 1, m], [sw - m - 1, sh - m - 1], [m, sh - m - 1]])
    pm = make_panel_map(target_quad, w=cw, h=ch)
    out = dtt.transfer_torso_texture(
        carrier(cw, ch, gradient=gradient), pm, src,
        source_landmarks=landmarks_for(src_quad, w=sw, h=sh),
        source_garment_mask=smask, shading=shading,
        source_sha256="src", carrier_sha256="car")
    return out, pm, src, src_quad, m


# ── A. identity — 원본 오라클 재현 ─────────────────────────────────────────
def test_identity_quad_reproduces_the_source_fabric():
    quad = np.float32([[30, 30], [469, 30], [469, 669], [30, 669]])
    cand, pm, src, _sq, _mg = run_transfer(FOUR_COLOUR, quad)
    assert isinstance(cand, dtt.DirectTorsoCandidate), cand
    y, xs = interior_row(cand, pm)
    obs, dist = nearest_labels(cand.image_bgr[y, xs], FOUR_COLOUR)
    exp, _sx = predicted_labels(np.asarray(cand.provenance["homography"]), xs, y, FOUR_COLOUR, _mg)
    assert float(np.mean(obs == exp)) > 0.99
    assert float(np.median(dist)) < 1.0, "identity 사상은 원본 팔레트를 그대로 내야 한다"


# ── B. 축소(원본이 더 큼) — 기하 정확성 ────────────────────────────────────
def test_two_times_downscale_matches_the_analytic_mapping():
    quad = np.float32([[40, 40], [259, 40], [259, 359], [40, 359]])   # 220×320 target
    cand, pm, _src, _sq, _mg = run_transfer(FOUR_COLOUR, quad, carrier_size=(300, 400))
    assert isinstance(cand, dtt.DirectTorsoCandidate), cand
    y, xs = interior_row(cand, pm)
    obs, _d = nearest_labels(cand.image_bgr[y, xs], FOUR_COLOUR)
    exp, _sx = predicted_labels(np.asarray(cand.provenance["homography"]), xs, y, FOUR_COLOUR, _mg)
    assert float(np.mean(obs == exp)) > 0.90       # 축소 표본화의 경계 흐림 허용
    assert cand.metrics["maxUpscaleFactor"] < 1.0


# ── C. 원근 사다리꼴 ───────────────────────────────────────────────────────
def test_perspective_trapezoid_follows_the_homography_prediction():
    quad = np.float32([[120, 40], [380, 40], [440, 660], [60, 660]])
    cand, pm, _src, _sq, _mg = run_transfer(TWO_COLOUR, quad)
    assert isinstance(cand, dtt.DirectTorsoCandidate), cand
    H = np.asarray(cand.provenance["homography"])
    agree = []
    for frac in (0.25, 0.5, 0.75):
        ys = np.nonzero((cand.alpha > 0.999).any(axis=1))[0]
        y = int(ys[int(len(ys) * frac)])
        xs = np.nonzero(cand.alpha[y] > 0.999)[0]
        xs = np.arange(int(xs.min()) + 6, int(xs.max()) - 6)
        obs, _d = nearest_labels(cand.image_bgr[y, xs], TWO_COLOUR)
        exp, _sx = predicted_labels(H, xs, y, TWO_COLOUR, _mg)
        agree.append(float(np.mean(obs == exp)))
    assert min(agree) > 0.90, agree
    # 사다리꼴은 위에서 세게 축소된다 — 그 사실이 지표로 드러나야 한다.
    assert cand.metrics["maxMinificationFactor"] > 1.3
    # 원근이면 국소 간격이 행마다 달라지는 것이 **정상**이다. 약속은 상수 주기가 아니라
    # 원본 픽셀 기하의 보존이다.


# ── D/E. 반복 수·좁은 밴드·색 순서 ─────────────────────────────────────────
@pytest.mark.parametrize("runs,label", [(TWO_COLOUR, "2col"), (FOUR_COLOUR, "4col")])
def test_repeat_count_and_colour_order_survive(runs, label):
    quad = np.float32([[30, 30], [469, 30], [469, 669], [30, 669]])
    cand, pm, _src, sq, _mg = run_transfer(runs, quad)
    assert isinstance(cand, dtt.DirectTorsoCandidate), cand
    y, xs = interior_row(cand, pm)
    obs, _d = nearest_labels(cand.image_bgr[y, xs], runs)
    exp, _sx = predicted_labels(np.asarray(cand.provenance["homography"]), xs, y, runs, _mg)

    period = sum(px for _c, px in runs)
    # 창이 실제로 덮은 **원본 구간**을 해석적으로 계산해 그 안의 반복 수와 비교한다.
    # 측정 창을 원본 전체 폭과 비교하면 잘린 가장자리만큼 늘 어긋난다.
    covered_source_span = float(_sx.max() - _sx.min())
    expected_cycles = covered_source_span / period
    full_torso_repeats = float(sq[1][0] - sq[0][0]) / period
    obs_runs = [r for r in runs_of(obs)[1:-1]]
    exp_runs = [r for r in runs_of(exp)[1:-1]]
    obs_cycles = sum(1 for lab, _n in obs_runs if lab == 0)
    # 반복 수는 주기를 재지 않고 구성으로 얻은 것이다 — 사상이 덮은 원본 구간의 반복 수와
    # 같아야 한다. 몸통 전체 반복 수도 함께 남긴다(창이 그 대부분을 덮는다).
    assert abs(obs_cycles - expected_cycles) <= 1.0, (
        label, obs_cycles, expected_cycles, full_torso_repeats)
    assert expected_cycles > full_torso_repeats * 0.9
    assert len(obs_runs) == pytest.approx(len(exp_runs), abs=2)
    # 색 **순환 순서** 보존
    order = [lab for lab, _n in obs_runs][:len(runs) * 2]
    assert order == [lab for lab, _n in exp_runs][:len(order)], label
    if label == "4col":       # 2px 좁은 밴드가 살아 있는가
        narrow = [n for lab, n in obs_runs if lab in (1, 2, 3)]
        assert narrow and min(narrow) >= 1


def test_narrow_bands_blur_under_strong_minification_and_metrics_say_so():
    """한계를 숨기지 않는다: 2px 밴드는 강한 축소에서 Nyquist 아래로 내려간다.

    이것은 전송의 결함이 아니라 표본화의 물리다. 승격 논의는 이 지표를 보고 해야 한다.
    """
    quad = np.float32([[120, 40], [380, 40], [440, 660], [60, 660]])
    cand, pm, _src, _sq, _mg = run_transfer(FOUR_COLOUR, quad)
    assert isinstance(cand, dtt.DirectTorsoCandidate), cand
    H = np.asarray(cand.provenance["homography"])
    ys = np.nonzero((cand.alpha > 0.999).any(axis=1))[0]
    y = int(ys[int(len(ys) * 0.05)])                      # 가장 좁아지는 위쪽
    xs = np.nonzero(cand.alpha[y] > 0.999)[0]
    xs = np.arange(int(xs.min()) + 6, int(xs.max()) - 6)
    obs, _d = nearest_labels(cand.image_bgr[y, xs], FOUR_COLOUR)
    exp, _sx = predicted_labels(H, xs, y, FOUR_COLOUR, _mg)
    top_agree = float(np.mean(obs == exp))
    assert cand.metrics["maxMinificationFactor"] > 1.3
    assert top_agree < 0.95, "이 fixture 는 실제로 축소 한계를 건드려야 한다"
    # 지배색(ground)은 살아남는다 — 무너지는 것은 2px 계열이다.
    assert float(np.mean(obs[exp == 0] == 0)) > 0.85


# ── F. 하모닉 메타데이터 면역 — 구조적 보장 ────────────────────────────────
def test_the_function_cannot_accept_any_period_argument():
    params = set(inspect.signature(dtt.transfer_torso_texture).parameters)
    for forbidden in ("period_px", "target_period_px", "source_period_px",
                      "period", "guided_period_px", "model"):
        assert forbidden not in params, forbidden


def test_repeated_calls_are_byte_identical_and_record_no_period_input():
    quad = np.float32([[30, 30], [469, 30], [469, 669], [30, 669]])
    a, _pm, _s, _q, _mg = run_transfer(FOUR_COLOUR, quad)
    b, _pm2, _s2, _q2, _mg = run_transfer(FOUR_COLOUR, quad)
    assert np.array_equal(a.image_bgr, b.image_bgr)
    assert a.provenance["periodInputs"] is None
    assert a.provenance["homography"] == b.provenance["homography"]


# ── G/H. 부재 조건 ─────────────────────────────────────────────────────────
def test_degenerate_landmarks_are_unavailable_not_an_error():
    src, smask, _m = source_with_margin(FOUR_COLOUR)
    pm = make_panel_map(np.float32([[30, 30], [469, 30], [469, 669], [30, 669]]),
                        w=500, h=700)
    flipped = {"shoulder_l": [0.9, 0.1], "shoulder_r": [0.1, 0.1],
               "hem_r": [0.1, 0.9], "hem_l": [0.9, 0.9]}
    out = dtt.transfer_torso_texture(carrier(500, 700), pm, src,
                                     source_landmarks=flipped, source_garment_mask=smask)
    assert isinstance(out, dtt.DirectTorsoUnavailable)
    assert out.reason == "torso_quad_invalid"


def test_insufficient_source_resolution_is_refused_by_the_existing_guard():
    """확대 합성 금지 — 기존 MIN_DECAL_SCALE 을 낮추지 않는다."""
    quad = np.float32([[20, 20], [979, 20], [979, 1379], [20, 1379]])   # target ≫ source
    out, _pm, _s, _q, _mg = run_transfer(FOUR_COLOUR, quad, source_size=(300, 420),
                                    carrier_size=(1000, 1400), margin=20)
    assert isinstance(out, dtt.DirectTorsoUnavailable)
    assert out.reason == "source_torso_ineligible"
    assert out.detail == "upscale_from_source"


def test_missing_torso_panel_is_unavailable():
    src, smask, _m = source_with_margin(FOUR_COLOUR)
    mask = np.zeros((700, 500), np.uint8)
    mask[30:670, 30:470] = 255
    pm = PanelMap(garment_mask=mask, protected=mask, boundary=mask,
                  panels=(Panel("sleeve_l", "stripe",
                                np.float32([[0, 0], [10, 0], [10, 10], [0, 10]])),),
                  confidence=1.0, strategy="synthetic", metrics={})
    out = dtt.transfer_torso_texture(
        carrier(500, 700), pm, src,
        source_landmarks=landmarks_for(
            np.float32([[30, 30], [469, 30], [469, 669], [30, 669]]), w=500, h=700),
        source_garment_mask=smask)
    assert isinstance(out, dtt.DirectTorsoUnavailable)
    assert out.reason == "carrier_torso_panel_missing"


# ── I. 배경 오염 0 ─────────────────────────────────────────────────────────
def test_no_source_background_pixel_ever_reaches_the_output():
    """원본 quad 를 일부러 옷 밖까지 늘려도 마젠타 배경은 한 픽셀도 실리면 안 된다."""
    sw, sh, m = 500, 700, 30
    src, smask, _ = source_with_margin(FOUR_COLOUR, w=sw, h=sh, margin=m)
    over = np.float32([[m - 18, m - 18], [sw - m + 17, m - 18],
                       [sw - m + 17, sh - m + 17], [m - 18, sh - m + 17]])
    quad = np.float32([[30, 30], [469, 30], [469, 669], [30, 669]])
    pm = make_panel_map(quad, w=sw, h=sh)
    cand = dtt.transfer_torso_texture(
        carrier(sw, sh), pm, src, source_landmarks=landmarks_for(over, w=sw, h=sh),
        source_garment_mask=smask, shading=dtt.SHADING_RAW_SOURCE)
    assert isinstance(cand, dtt.DirectTorsoCandidate), cand
    assert cand.metrics["backgroundRejectedPx"] > 0, "이 fixture 는 배경을 실제로 겨냥한다"
    bg = np.array(BACKGROUND, np.int16)
    delta = np.abs(cand.image_bgr.astype(np.int16) - bg).sum(axis=2)
    assert int((delta < 40).sum()) == 0, "마젠타 배경이 출력에 존재한다"


# ── J. 실루엣 불변 ─────────────────────────────────────────────────────────
def test_carrier_silhouette_and_outside_pixels_are_untouched():
    quad = np.float32([[120, 40], [380, 40], [440, 660], [60, 660]])
    cand, pm, _src, _sq, _mg = run_transfer(FOUR_COLOUR, quad, gradient=True,
                                       shading=dtt.SHADING_CARRIER_LOW_FREQ_L)
    assert isinstance(cand, dtt.DirectTorsoCandidate), cand
    base = carrier(500, 700, gradient=True)
    outside = pm.garment_mask == 0
    assert np.array_equal(cand.image_bgr[outside], base[outside])
    assert float(cand.alpha[outside].max()) == 0.0
    # 마스크·target quad 는 입력 그대로 — 기하 생성 없음
    assert cand.provenance["garmentMaskSha256"] == hashlib.sha256(
        np.ascontiguousarray(pm.garment_mask).tobytes()).hexdigest()[:16]
    assert cand.provenance["targetQuad"] == [
        [round(float(x), 3), round(float(y), 3)] for x, y in quad]


# ── 8. shading 전략 비교 ───────────────────────────────────────────────────
def test_carrier_low_frequency_luminance_is_adopted_without_moving_colour_order():
    quad = np.float32([[30, 30], [469, 30], [469, 669], [30, 669]])
    raw, pm, _s, _q, _mg = run_transfer(FOUR_COLOUR, quad, gradient=True,
                                   shading=dtt.SHADING_RAW_SOURCE)
    shaded, _pm2, _s2, _q2, _mg = run_transfer(FOUR_COLOUR, quad, gradient=True,
                                          shading=dtt.SHADING_CARRIER_LOW_FREQ_L)
    sel = pm.garment_mask > 0
    raw_l = bgr_to_lab(raw.image_bgr)[..., 0]
    shaded_l = bgr_to_lab(shaded.image_bgr)[..., 0]
    top = sel.copy(); top[350:] = False
    bottom = sel.copy(); bottom[:350] = False
    # carrier 는 위가 어둡고 아래가 밝다. raw 는 그 음영을 모른다.
    assert abs(float(raw_l[bottom].mean() - raw_l[top].mean())) < 6.0
    assert float(shaded_l[bottom].mean() - shaded_l[top].mean()) > 12.0
    # 색 순서는 두 모드 모두 보존
    for cand in (raw, shaded):
        y, xs = interior_row(cand, pm)
        obs, _d = nearest_labels(cand.image_bgr[y, xs], FOUR_COLOUR)
        exp, _sx = predicted_labels(np.asarray(cand.provenance["homography"]),
                                    xs, y, FOUR_COLOUR, _mg)
        assert float(np.mean(obs == exp)) > 0.88


def test_source_chroma_is_preserved_and_carrier_cast_is_only_measured():
    quad = np.float32([[30, 30], [469, 30], [469, 669], [30, 669]])
    cand, pm, src, sq, _mg = run_transfer(FOUR_COLOUR, quad,
                                     shading=dtt.SHADING_CARRIER_LOW_FREQ_L)
    sub = src[int(sq[0][1]) + 5:int(sq[2][1]) - 5, int(sq[0][0]) + 5:int(sq[1][0]) - 5]
    src_ab = np.median(bgr_to_lab(sub)[..., 1:3].reshape(-1, 2), axis=0)
    got = np.array(cand.metrics["sourceChromaMedianAb"])
    assert float(np.linalg.norm(got - src_ab)) < 2.0
    assert "measuredChromaCastAb" in cand.metrics    # 측정만, 적용은 안 한다


# ── provenance / 진단 계약 ────────────────────────────────────────────────
def test_provenance_is_enough_for_exact_replay():
    quad = np.float32([[120, 40], [380, 40], [440, 660], [60, 660]])
    cand, _pm, _s, _q, _mg = run_transfer(FOUR_COLOUR, quad)
    p = cand.provenance
    for key in ("version", "sourceSha256", "carrierSha256", "sourceQuad", "targetQuad",
                "homography", "interpolation", "garmentMaskSha256", "shadingMode",
                "periodInputs"):
        assert key in p, key
    assert p["version"] == dtt.DIRECT_TORSO_VERSION
    assert np.asarray(p["homography"]).shape == (3, 3)


def test_metrics_report_sampling_density_and_coverage():
    quad = np.float32([[30, 30], [469, 30], [469, 669], [30, 669]])
    cand, _pm, _s, _q, _mg = run_transfer(FOUR_COLOUR, quad)
    m = cand.metrics
    for key in ("torsoCoverage", "outOfSourceFrac", "minSourceSamplingDensity",
                "maxUpscaleFactor", "backgroundRejectedPx", "paintedPx"):
        assert key in m, key
    assert m["torsoCoverage"] > 0.95
    assert m["outOfSourceFrac"] == 0.0


# ── 이 phase 가 건드리지 않는 것 ──────────────────────────────────────────
def test_the_module_is_isolated_from_the_periodic_path_and_product_flow():
    src = inspect.getsource(dtt)
    for forbidden in ("plan_periodic_projection", "synthesize_pattern_lab",
                      "composite_stripe", "finalize", "credits", "jobs.status",
                      "period_profile_lab"):
        assert forbidden not in src, forbidden


# ── 18. 주기 경로와의 대조 (승자 선언이 아니라 트레이드오프 이해) ──────────
def test_periodic_and_direct_agree_on_colour_order_and_differ_in_provenance():
    """같은 합성 입력에 두 경로를 나란히 돌린다.

    주기 경로는 **주기 진실이 있을 때만** 돌릴 수 있고(여기서는 오라클이라 있다), 직접
    전송은 그것 없이 돈다. 둘이 픽셀 단위로 같아야 할 이유는 없다 — 확인하는 것은
    색 순환 순서와 반복 기하가 양쪽 다 살아 있는가, 그리고 무엇을 근거로 삼았는가다.
    """
    from app.services.hybrid_composite.stripe_model import extract_stripe_model_scan
    from app.services.hybrid_composite.types import CompositeFailure
    from app.services.hybrid_composite.warp_composite import composite_stripe

    quad = np.float32([[30, 30], [469, 30], [469, 669], [30, 669]])
    direct, pm, src, sq, mg = run_transfer(TWO_COLOUR, quad, gradient=True,
                                           shading=dtt.SHADING_CARRIER_LOW_FREQ_L)
    assert isinstance(direct, dtt.DirectTorsoCandidate), direct

    fabric = src[int(sq[0][1]):int(sq[2][1]), int(sq[0][0]):int(sq[1][0])]
    model = extract_stripe_model_scan(fabric, source_asset_id="fx", source_sha256="0" * 8,
                                      source_roi=(0, 0, fabric.shape[1], fabric.shape[0]))
    if isinstance(model, CompositeFailure):
        pytest.skip(f"periodic producer could not model the oracle fabric: {model.reason}")
    # 원본 몸통 span 과 target 몸통 span 이 같으므로 반복 수 보존 주기 = 원본 주기.
    periodic = composite_stripe(carrier(500, 700, gradient=True), pm, model,
                                target_period_px=float(model.period_px),
                                target_axis="vertical", allow_low_source_coverage=True)
    if isinstance(periodic, CompositeFailure):
        pytest.skip(f"periodic path declined this synthetic carrier: {periodic.reason}")

    for tag, cand_img, alpha in (("direct", direct.image_bgr, direct.alpha),
                                 ("periodic", periodic.image_bgr, periodic.alpha)):
        ys = np.nonzero((alpha > 0.999).any(axis=1))[0]
        y = int(ys[len(ys) // 2])
        xs = np.nonzero(alpha[y] > 0.999)[0]
        xs = np.arange(int(xs.min()) + 6, int(xs.max()) - 6)
        obs, _d = nearest_labels(cand_img[y, xs], TWO_COLOUR)
        cycles = sum(1 for lab, _n in runs_of(obs)[1:-1] if lab == 0)
        order = [lab for lab, _n in runs_of(obs)[1:-1]][:6]
        assert order == [0, 1, 0, 1, 0, 1] or order == [1, 0, 1, 0, 1, 0], (tag, order)
        assert cycles > 15, (tag, cycles)      # 두 경로 모두 반복 기하를 낸다

    # 근거의 차이가 핵심이다: 직접 전송은 주기를 입력으로 받지 않는다.
    assert direct.provenance["periodInputs"] is None
    assert periodic.metrics["target_period_px"] == pytest.approx(float(model.period_px),
                                                                abs=0.05)
