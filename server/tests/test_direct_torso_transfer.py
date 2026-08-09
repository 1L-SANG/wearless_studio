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


def illumination(h, w, direction):
    """저주파 곱셈 조명장. direction: 0 없음, +1 아래가 밝음, -1 위가 밝음."""
    if direction == 0:
        return np.ones((h, w), np.float64)
    ramp = (np.linspace(0.62, 1.38, h) if direction > 0
            else np.linspace(1.38, 0.62, h))
    return np.repeat(ramp.reshape(-1, 1), w, axis=1)


def source_with_margin(runs, *, w=500, h=700, margin=30, shade=0):
    """옷은 안쪽에만 있고 바깥은 마젠타 배경 — quad 가 새면 즉시 드러난다.

    `shade` 는 원본 **사진의 조명**이다. 원단 자체가 아니라 촬영 조건이며, 마네킹으로
    옮겨서는 안 되는 성분이다.
    """
    img = np.full((h, w, 3), BACKGROUND, np.uint8)
    fabric = draw_stripes(runs, w - 2 * margin, h - 2 * margin)
    if shade:
        f = illumination(*fabric.shape[:2], shade)
        fabric = np.clip(fabric.astype(np.float64) * f[..., None], 0, 255).astype(np.uint8)
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


def carrier(w, h, *, gradient=False, shade=None):
    img = np.full((h, w, 3), 150, np.uint8)
    direction = shade if shade is not None else (1 if gradient else 0)
    if direction:                   # 세로 저주파 음영 — carrier 주름의 대역
        f = illumination(h, w, direction)
        img = np.clip(img.astype(np.float64) * f[..., None], 0, 255).astype(np.uint8)
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
                 shading=dtt.SHADING_RAW_SOURCE, gradient=False, margin=30,
                 source_shade=0, carrier_shade=None):
    sw, sh = source_size
    cw, ch = carrier_size or (sw, sh)
    src, smask, m = source_with_margin(runs, w=sw, h=sh, margin=margin,
                                       shade=source_shade)
    src_quad = np.float32([[m, m], [sw - m - 1, m], [sw - m - 1, sh - m - 1], [m, sh - m - 1]])
    pm = make_panel_map(target_quad, w=cw, h=ch)
    out = dtt.transfer_torso_texture(
        carrier(cw, ch, gradient=gradient, shade=carrier_shade), pm, src,
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


# ── PHASE A: shading 분해 — 분리 측정 ──────────────────────────────────────
# 주장은 하나다: 원본 사진의 **조명**은 옮기지 않고, 원본의 **패턴**은 전부 옮기고,
# 마네킹의 **주름·음영**은 채택한다. 세 가지를 따로 잰다.

IDENTITY_QUAD = np.float32([[30, 30], [469, 30], [469, 669], [30, 669]])
ANALYSIS_FRAC = 0.08          # 주름 대역. 모드 비교를 같은 자로 하기 위해 고정한다.


def _corr(a, b):
    a = np.asarray(a, np.float64).ravel(); a = a - a.mean()
    b = np.asarray(b, np.float64).ravel(); b = b - b.mean()
    d = np.linalg.norm(a) * np.linalg.norm(b)
    return float(a @ b / d) if d > 1e-9 else 0.0


def _per_band_chroma_error(cand, runs, origin):
    """색 index 별 a/b 오차의 최대값 — 좁은 유채색 밴드가 지워지면 즉시 드러난다."""
    ref = bgr_to_lab(np.array([[c for c, _w in runs]], np.uint8))[0].astype(np.float64)
    ys = np.nonzero((cand.alpha > 0.999).any(axis=1))[0]
    H = np.asarray(cand.provenance["homography"])
    worst = 0.0
    for frac in (0.3, 0.5, 0.7):
        y = int(ys[int(len(ys) * frac)])
        xs = np.nonzero(cand.alpha[y] > 0.999)[0]
        xs = np.arange(int(xs.min()) + 6, int(xs.max()) - 6)
        exp, _sx = predicted_labels(H, xs, y, runs, origin)
        lab = bgr_to_lab(cand.image_bgr[y, xs][None, :, :])[0].astype(np.float64)
        for i in range(len(runs)):
            m = exp == i
            if int(m.sum()) < 5:
                continue
            worst = max(worst, float(np.linalg.norm(
                np.median(lab[m][:, 1:3], axis=0) - ref[i][1:3])))
    return worst


def _bands_keep_identity(cand, runs, origin):
    """각 유채색 밴드의 중앙 a/b 가 여전히 **자기 색에 가장 가까운가** (엄격 argmin).

    → (ok, judged). 허용 오차가 없는 범주적 판정이다.

    **중성 밴드는 판정 대상이 아니다.** 회색 두 개는 a/b 가 둘 다 ~(0,0) 이라 chroma
    로는 원리적으로 구분되지 않는다 — 거기서 argmin 은 동전 던지기다. 그런 팔레트에서
    이 검사는 공허하므로 `judged == 0` 을 돌려주고, 호출자가 그 사실을 명시적으로
    단언하게 한다(조용히 통과시키지 않는다).
    """
    ref = bgr_to_lab(np.array([[c for c, _w in runs]], np.uint8))[0].astype(np.float64)
    chromatic = [i for i in range(len(runs))
                 if np.linalg.norm(ref[i][1:3]) > 5.0]
    if len(chromatic) < 2:
        return True, 0
    ys = np.nonzero((cand.alpha > 0.999).any(axis=1))[0]
    H = np.asarray(cand.provenance["homography"])
    judged = 0
    for frac in (0.3, 0.5, 0.7):
        y = int(ys[int(len(ys) * frac)])
        xs = np.nonzero(cand.alpha[y] > 0.999)[0]
        xs = np.arange(int(xs.min()) + 6, int(xs.max()) - 6)
        exp, _sx = predicted_labels(H, xs, y, runs, origin)
        lab = bgr_to_lab(cand.image_bgr[y, xs][None, :, :])[0].astype(np.float64)
        for i in chromatic:
            m = exp == i
            if int(m.sum()) < 5:
                continue
            med = np.median(lab[m][:, 1:3], axis=0)
            d = np.linalg.norm(ref[:, 1:3] - med, axis=1)
            judged += 1
            if int(np.argmin(d)) != i:      # 엄격한 argmin — 여유값을 두지 않는다
                return False, judged
    return True, judged


def shading_report(runs, *, source_shade, carrier_shade, shading):
    """한 조명 조합·한 모드의 분리 측정값."""
    cand, pm, _src, _sq, mg = run_transfer(
        runs, IDENTITY_QUAD, shading=shading,
        source_shade=source_shade, carrier_shade=carrier_shade)
    assert isinstance(cand, dtt.DirectTorsoCandidate), cand
    sel = cand.alpha > 0.999
    h, w = cand.image_bgr.shape[:2]
    sigma = min(h, w) * ANALYSIS_FRAC
    out_l = bgr_to_lab(cand.image_bgr)[..., 0].astype(np.float64)
    # 렌더가 masked lowpass 를 쓰므로 분석도 같은 연산자를 쓴다. 평범한 blur 를 쓰면
    # 옷 밖 carrier/마젠타 픽셀이 오라클을 오염시킨다.
    out_lf = dtt._masked_lowpass(out_l.astype(np.float32), sel, sigma).astype(np.float64)
    out_hf = out_l - out_lf

    truth, _m, _mm = source_with_margin(runs, shade=0)          # 조명 없는 원단 진실
    truth_lab = bgr_to_lab(truth).astype(np.float64)
    truth_hf = truth_lab[..., 0] - dtt._masked_lowpass(
        truth_lab[..., 0].astype(np.float32), sel, sigma).astype(np.float64)

    src_field = illumination(h, w, source_shade)
    car_field = illumination(h, w, carrier_shade)
    # 상관은 진폭에 눈이 멀었다 — 램프의 1% 만 남아도 상관은 0.99 다. 남은 조명의
    # **크기**를 L 단위로 잰다: 칠한 영역의 행 평균 저주파 L 의 최대-최소.
    rows = np.nonzero(sel.any(axis=1))[0]
    row_mean_lf = np.array([out_lf[r][sel[r]].mean() for r in rows])
    lf_amplitude = float(row_mean_lf.max() - row_mean_lf.min())
    clipped = float(((out_l >= 99.5) | (out_l <= 0.5))[sel].mean())
    y, xs = interior_row(cand, pm)
    obs, _d = nearest_labels(cand.image_bgr[y, xs], runs)
    exp, _sx = predicted_labels(np.asarray(cand.provenance["homography"]), xs, y, runs, mg)
    return {
        "geometry": float(np.mean(obs == exp)),
        # **밴드별로** 잰다. 전체 중앙값은 70% 를 차지하는 중성 바탕이 지배해서, 유채색
        # 밴드를 전부 지워도 0 이 나온다(Codex 반증: 30% 오염, 중앙값 0.0, 평균 14.7).
        "chroma_de_per_band": _per_band_chroma_error(cand, runs, mg),
        "bands_keep_identity": _bands_keep_identity(cand, runs, mg),
        "hf_ratio": float(out_hf[sel].std() / max(truth_hf[sel].std(), 1e-9)),
        "lf_amplitude_l": lf_amplitude,
        "clipped_frac": clipped,
        "carrier_lf_corr": _corr(out_lf[sel], car_field[sel]) if carrier_shade else None,
        "source_lf_corr": _corr(out_lf[sel], src_field[sel]) if source_shade else None,
        "metrics": cand.metrics,
    }


def test_source_photo_illumination_is_rejected_only_by_the_high_pass_split():
    """가장 중요한 경우: 원본 사진에만 조명이 있고 마네킹은 평평하다.

    `carrier_low_freq_l` 이 스칼라 평균만 빼기 때문에 원본 조명장을 **전혀** 제거하지
    못한다는 것이 이 모드를 기본값에서 내린 근거다.
    """
    r = {m: shading_report(FOUR_COLOUR, source_shade=+1, carrier_shade=0, shading=m)
         for m in (dtt.SHADING_RAW_SOURCE, dtt.SHADING_CARRIER_LOW_FREQ_L,
                   dtt.SHADING_SOURCE_HIGHFREQ_CARRIER_LOWFREQ)}
    # carrier 가 평평하므로 출력 저주파의 세로 진폭은 전부 **원본 조명**에서 온다.
    raw = r[dtt.SHADING_RAW_SOURCE]["lf_amplitude_l"]
    scalar = r[dtt.SHADING_CARRIER_LOW_FREQ_L]["lf_amplitude_l"]
    split = r[dtt.SHADING_SOURCE_HIGHFREQ_CARRIER_LOWFREQ]["lf_amplitude_l"]
    assert raw > 10.0, raw                       # 픽스처가 실제로 조명을 담고 있다
    # 스칼라 평균은 저주파장을 못 건드린다 — raw 와 사실상 같은 진폭이 남는다.
    assert scalar == pytest.approx(raw, rel=0.05), (scalar, raw)
    # 모드 간 판정은 같은 실행 안의 상대 비교로 한다(0.25 는 여유 있는 배수 여백).
    assert split < raw * 0.25, (split, raw)
    # 방향 상관도 함께 남긴다(진폭과 달리 부호만 말해준다).
    assert r[dtt.SHADING_RAW_SOURCE]["source_lf_corr"] > 0.9


def test_carrier_shading_wins_when_the_two_illuminations_disagree():
    """반대 방향이면 스칼라 모드는 두 조명이 상쇄돼 옷이 평평해진다."""
    scalar = shading_report(FOUR_COLOUR, source_shade=+1, carrier_shade=-1,
                            shading=dtt.SHADING_CARRIER_LOW_FREQ_L)
    split = shading_report(FOUR_COLOUR, source_shade=+1, carrier_shade=-1,
                           shading=dtt.SHADING_SOURCE_HIGHFREQ_CARRIER_LOWFREQ)
    assert split["carrier_lf_corr"] > 0.9, split["carrier_lf_corr"]
    assert abs(scalar["carrier_lf_corr"]) < 0.4, scalar["carrier_lf_corr"]


def test_same_direction_illumination_does_not_clip_the_pattern():
    """같은 방향이면 스칼라 모드는 두 조명이 더해져 clipping 으로 기하까지 깨진다."""
    scalar = shading_report(FOUR_COLOUR, source_shade=+1, carrier_shade=+1,
                            shading=dtt.SHADING_CARRIER_LOW_FREQ_L)
    split = shading_report(FOUR_COLOUR, source_shade=+1, carrier_shade=+1,
                           shading=dtt.SHADING_SOURCE_HIGHFREQ_CARRIER_LOWFREQ)
    assert split["geometry"] > 0.98, split["geometry"]
    # 기전은 clipping 이다: 두 조명이 더해져 L 이 범위를 넘는다. 스캔라인 라벨보다
    # clipping 비율이 그 기전을 직접 잰다.
    assert split["clipped_frac"] <= scalar["clipped_frac"], (split["clipped_frac"],
                                                             scalar["clipped_frac"])
    assert split["lf_amplitude_l"] < scalar["lf_amplitude_l"], (
        split["lf_amplitude_l"], scalar["lf_amplitude_l"])


def test_flat_source_shaded_carrier_adopts_carrier_shading_in_every_mode():
    """원본에 조명이 없으면 스칼라 평균이 곧 저주파 전부라 두 모드가 같아야 한다."""
    for mode in (dtt.SHADING_CARRIER_LOW_FREQ_L,
                 dtt.SHADING_SOURCE_HIGHFREQ_CARRIER_LOWFREQ):
        rep = shading_report(FOUR_COLOUR, source_shade=0, carrier_shade=+1, shading=mode)
        assert rep["carrier_lf_corr"] > 0.9, (mode, rep["carrier_lf_corr"])
        assert rep["geometry"] > 0.98, (mode, rep["geometry"])


@pytest.mark.parametrize("runs,label", [(TWO_COLOUR, "wide"), (FOUR_COLOUR, "narrow_2px")])
def test_high_frequency_texture_and_colour_survive_the_split(runs, label):
    """조명을 벗겨내면서 패턴 대비와 색을 잃으면 의미가 없다."""
    split = shading_report(runs, source_shade=+1, carrier_shade=-1,
                           shading=dtt.SHADING_SOURCE_HIGHFREQ_CARRIER_LOWFREQ)
    raw = shading_report(runs, source_shade=+1, carrier_shade=-1,
                         shading=dtt.SHADING_RAW_SOURCE)
    # 고주파 대비는 raw 대비 거의 그대로 (raw 는 조명까지 통째로 옮기는 상한선이다)
    assert split["hf_ratio"] > raw["hf_ratio"] * 0.9, (label, split["hf_ratio"],
                                                       raw["hf_ratio"])
    assert split["geometry"] > 0.97, (label, split["geometry"])
    # a/b 는 어떤 모드에서도 건드리지 않는다 — 색 진실은 원본 것이다.
    # 밴드 **정체성**이 살아 있는지를 본다 — 임계를 지어내지 않는 범주적 판정이다.
    # (수치 오차 자체는 gamut 왕복 때문에 split 이 raw 보다 크다. 아래 전용 테스트에서
    #  L 이동이 거의 없는 대조로 a/b 불변 계약을 확인한다.)
    split_ok, split_judged = split["bands_keep_identity"]
    raw_ok, raw_judged = raw["bands_keep_identity"]
    assert split_ok and raw_ok, label
    if label == "narrow_2px":
        assert split_judged > 0 and raw_judged > 0, (split_judged, raw_judged)
    else:
        # 중성 2색 팔레트 — chroma 로는 판정할 수 없다는 사실을 명시한다.
        assert split_judged == 0 and raw_judged == 0, (split_judged, raw_judged)


def test_shading_sigma_comes_from_the_existing_documented_band():
    """sigma **값**은 기존 두 상수에서 유도된다는 것을 고정한다.

    정직하게: 기하평균을 고른 것 자체는 새 정책 결정이다(Codex 지적, 타당). 고정하는
    것은 "밴드 안이고 두 끝 상수에서 유도된다"이지 "임계를 하나도 안 만들었다"가 아니다.
    아래 테스트들도 절대 상수를 여럿 쓴다 — 대부분은 픽스처가 실제로 그 현상을 담고
    있는지 확인하는 sanity 하한이고, 모드 간 판정은 같은 실행 안의 상대 비교다.
    """
    from app.services.hybrid_composite.warp_composite import (
        SHADING_SIGMA_MAX_FRAC, SHADING_SIGMA_MIN_FRAC)
    assert dtt._SHADING_SIGMA_FRAC == pytest.approx(
        float(np.sqrt(SHADING_SIGMA_MIN_FRAC * SHADING_SIGMA_MAX_FRAC)))
    assert SHADING_SIGMA_MIN_FRAC < dtt._SHADING_SIGMA_FRAC < SHADING_SIGMA_MAX_FRAC


def test_the_split_is_the_default_mode():
    import inspect
    default = inspect.signature(
        dtt.transfer_torso_texture).parameters["shading"].default
    assert default == dtt.SHADING_SOURCE_HIGHFREQ_CARRIER_LOWFREQ


def test_masked_lowpass_does_not_let_outside_pixels_bleed_in():
    """quad 밖 0 이 저주파로 새면 가장자리에 가짜 어두움이 생기고 조명으로 오인된다."""
    field = np.full((200, 200), 50.0, np.float32)
    mask = np.zeros((200, 200), np.uint8)
    mask[50:150, 50:150] = 255
    low = dtt._masked_lowpass(field, mask, 12.0)
    inside = low[mask > 0]
    assert float(np.abs(inside - 50.0).max()) < 1e-3
    plain = cv2.GaussianBlur(field * (mask > 0), (0, 0), sigmaX=12.0)
    assert float(np.abs(plain[mask > 0] - 50.0).max()) > 1.0   # 대조: 그냥 blur 는 샌다


def test_shading_metrics_expose_what_the_decomposition_did():
    cand, _pm, _s, _q, _mg = run_transfer(
        FOUR_COLOUR, IDENTITY_QUAD, source_shade=+1, carrier_shade=-1,
        shading=dtt.SHADING_SOURCE_HIGHFREQ_CARRIER_LOWFREQ)
    m = cand.metrics
    for key in ("shadingSigmaPx", "sourceLowFreqStdL", "carrierLowFreqStdL",
                "outputHighFreqStdL", "sourceHighFreqStdL"):
        assert m[key] is not None, key
    assert m["outputHighFreqStdL"] > m["sourceHighFreqStdL"] * 0.85


def test_the_split_writes_only_L_and_leaves_ab_untouched():
    """계약: 색은 원본 것이다. L 이동이 **거의 없는** 대조에서 확인한다.

    "정확히 0" 은 아니다 — carrier 는 uint8 이라 원본 평균 L 을 정수 레벨로만 근사할 수
    있고, 남는 차이를 아래에서 측정해 함께 못 박는다(그 크기가 결론의 전제다).

    실제 렌더에서 L 이 크게 바뀌면 saturated 좁은 밴드의 a/b 가 BGR gamut 왕복에서
    끌려간다(측정: 밴드별 최대 오차 raw 6.71 → split 9.06). 그것은 분해가 색을 건드린
    것이 아니라 8bit 왕복의 결과다. carrier 저주파 L 을 원본 저주파 L 과 같게 맞추면
    L 변화가 사라지고, 그때 두 모드의 a/b 는 사실상 같아야 한다.
    """
    src, smask, m = source_with_margin(FOUR_COLOUR, shade=0)
    pm = make_panel_map(IDENTITY_QUAD, w=500, h=700)
    # 원본 자체의 평균 L 을 가진 평평한 carrier — split 의 L 이동량이 0 에 가깝다.
    src_l = bgr_to_lab(src)[..., 0][smask > 0].mean()
    level = int(round(float(src_l) * 255.0 / 100.0))
    flat = np.full((700, 500, 3), level, np.uint8)
    outs = {}
    for mode in (dtt.SHADING_RAW_SOURCE, dtt.SHADING_SOURCE_HIGHFREQ_CARRIER_LOWFREQ):
        o = dtt.transfer_torso_texture(flat, pm, src,
                                       source_landmarks=landmarks_for(
                                           np.float32([[m, m], [499 - m, m],
                                                       [499 - m, 699 - m], [m, 699 - m]]),
                                           w=500, h=700),
                                       source_garment_mask=smask, shading=mode)
        assert isinstance(o, dtt.DirectTorsoCandidate), o
        outs[mode] = o
    # uint8 carrier 로 맞출 수 있는 한계를 명시적으로 잰다.
    achieved = bgr_to_lab(flat)[..., 0][0, 0]
    assert abs(float(achieved) - float(src_l)) < 3.0, (achieved, src_l)
    raw_err = _per_band_chroma_error(outs[dtt.SHADING_RAW_SOURCE], FOUR_COLOUR, m)
    split_err = _per_band_chroma_error(
        outs[dtt.SHADING_SOURCE_HIGHFREQ_CARRIER_LOWFREQ], FOUR_COLOUR, m)
    assert outs[dtt.SHADING_SOURCE_HIGHFREQ_CARRIER_LOWFREQ].metrics["clippedFracL"] == 0.0
    assert split_err == pytest.approx(raw_err, abs=0.5), (split_err, raw_err)


# ── Codex round 1 반증에서 나온 대조들 ─────────────────────────────────────
def _flat_fabric_halves():
    """원단 **자체**가 저주파 구조를 가진 경우 — 색블록/옴브레의 최소 모형."""
    runs_top, runs_bottom = (235, 235, 235), (90, 90, 90)
    img = np.full((700, 500, 3), BACKGROUND, np.uint8)
    img[30:350, 30:470] = runs_top
    img[350:670, 30:470] = runs_bottom
    mask = np.zeros((700, 500), np.uint8)
    mask[30:670, 30:470] = 255
    return img, mask


def test_split_also_erases_genuine_low_frequency_garment_content():
    """한계 3) 을 시험으로 고정한다 — 숨기지 않고 재현 가능하게 남긴다.

    등방 Gaussian 하나로는 '어두운 아래쪽 절반'이 조명인지 원단인지 가릴 수 없다.
    split 은 둘 다 지운다. 이 사실을 통과시키는 것이 아니라 **기록**하는 테스트다.
    """
    src, smask = _flat_fabric_halves()
    pm = make_panel_map(IDENTITY_QUAD, w=500, h=700)
    lm = landmarks_for(np.float32([[30, 30], [469, 30], [469, 669], [30, 669]]),
                       w=500, h=700)
    got = {}
    for mode in (dtt.SHADING_RAW_SOURCE, dtt.SHADING_SOURCE_HIGHFREQ_CARRIER_LOWFREQ):
        o = dtt.transfer_torso_texture(carrier(500, 700), pm, src,
                                       source_landmarks=lm, source_garment_mask=smask,
                                       shading=mode)
        assert isinstance(o, dtt.DirectTorsoCandidate), o
        lab_l = bgr_to_lab(o.image_bgr)[..., 0].astype(np.float64)
        sel = o.alpha > 0.999
        top = sel.copy(); top[350:] = False
        bot = sel.copy(); bot[:350] = False
        got[mode] = (float(lab_l[top].mean() - lab_l[bot].mean()), o.metrics)
    raw_contrast, _ = got[dtt.SHADING_RAW_SOURCE]
    split_contrast, split_metrics = got[dtt.SHADING_SOURCE_HIGHFREQ_CARRIER_LOWFREQ]
    assert raw_contrast > 40.0, raw_contrast              # 픽스처가 실제로 대비를 담는다
    assert abs(split_contrast) < raw_contrast * 0.1, (split_contrast, raw_contrast)
    # 이 상황은 **탐지 가능**해야 한다: 원본 저주파 산포가 크다는 것이 그 신호다.
    assert split_metrics["sourceLowFreqStdL"] > 10.0, split_metrics["sourceLowFreqStdL"]


def test_bright_carrier_clipping_is_measured_and_exposed():
    """흰 carrier + 고대비 원본이면 재결합 L 이 범위를 넘는다. 절대값으로 못 박는다."""
    src, smask, m = source_with_margin(FOUR_COLOUR, shade=0)
    pm = make_panel_map(IDENTITY_QUAD, w=500, h=700)
    lm = landmarks_for(np.float32([[m, m], [499 - m, m], [499 - m, 699 - m], [m, 699 - m]]),
                       w=500, h=700)
    white = np.full((700, 500, 3), 255, np.uint8)
    o = dtt.transfer_torso_texture(white, pm, src, source_landmarks=lm,
                                   source_garment_mask=smask,
                                   shading=dtt.SHADING_SOURCE_HIGHFREQ_CARRIER_LOWFREQ)
    assert isinstance(o, dtt.DirectTorsoCandidate), o
    # 지표가 실제로 그 사실을 말한다 — 조용히 잘리지 않는다.
    assert o.metrics["clippedFracL"] > 0.2, o.metrics["clippedFracL"]
    assert o.metrics["highFreqRetention"] < 1.0
    # raw_source 는 재결합 자체를 하지 않으므로 언제나 0 이어야 한다.
    raw = dtt.transfer_torso_texture(white, pm, src, source_landmarks=lm,
                                     source_garment_mask=smask,
                                     shading=dtt.SHADING_RAW_SOURCE)
    assert raw.metrics["clippedFracL"] == 0.0, raw.metrics["clippedFracL"]
    # 그리고 평범한 carrier 에서는 0 이어야 한다(이 지표가 늘 켜져 있으면 쓸모없다).
    o2 = dtt.transfer_torso_texture(carrier(500, 700), pm, src, source_landmarks=lm,
                                    source_garment_mask=smask,
                                    shading=dtt.SHADING_SOURCE_HIGHFREQ_CARRIER_LOWFREQ)
    assert o2.metrics["clippedFracL"] == 0.0, o2.metrics["clippedFracL"]


def test_default_shading_is_exercised_end_to_end_without_passing_the_argument():
    """서명 검사만으로는 기본 경로가 실제로 도는지 알 수 없다 — 인자를 빼고 호출한다."""
    src, smask, m = source_with_margin(FOUR_COLOUR, shade=+1)
    pm = make_panel_map(IDENTITY_QUAD, w=500, h=700)
    lm = landmarks_for(np.float32([[m, m], [499 - m, m], [499 - m, 699 - m], [m, 699 - m]]),
                       w=500, h=700)
    omitted = dtt.transfer_torso_texture(carrier(500, 700, shade=-1), pm, src,
                                         source_landmarks=lm, source_garment_mask=smask)
    explicit = dtt.transfer_torso_texture(
        carrier(500, 700, shade=-1), pm, src, source_landmarks=lm,
        source_garment_mask=smask,
        shading=dtt.SHADING_SOURCE_HIGHFREQ_CARRIER_LOWFREQ)
    assert isinstance(omitted, dtt.DirectTorsoCandidate), omitted
    assert np.array_equal(omitted.image_bgr, explicit.image_bgr)
    assert omitted.metrics["shadingMode"] == dtt.SHADING_SOURCE_HIGHFREQ_CARRIER_LOWFREQ
    # 그리고 raw 와는 실제로 다른 픽셀이 나온다(기본값이 이름만 바뀐 것이 아니다).
    raw = dtt.transfer_torso_texture(carrier(500, 700, shade=-1), pm, src,
                                     source_landmarks=lm, source_garment_mask=smask,
                                     shading=dtt.SHADING_RAW_SOURCE)
    assert not np.array_equal(omitted.image_bgr, raw.image_bgr)


def test_version_was_bumped_because_rendering_changed():
    """같은 버전으로 다른 픽셀을 내면 provenance 가 거짓말이 된다."""
    assert dtt.DIRECT_TORSO_VERSION.endswith("_v2")
