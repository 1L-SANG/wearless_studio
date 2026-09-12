"""agents/face_identity — GPU 없이 기하·프롬프트·합성·게이트·배선 검증.

픽스처: prod(Gemini) 착용컷 6장 = FACE_IDENTITY_FIXTURES(기본 ~/Downloads/lora_runs/prod_inputs).
인물 사진이라 레포 밖에 둔다 — 없으면 그 테스트만 skip. YuNet weights(gitignore) 없어도 skip.
build_v4c.py 가 같은 6장으로 만든 학습 표본 control(v4c_ckpt/samples_ctrl)과 픽셀 대조한다(있으면).
기하·합성·프롬프트·배선은 weights 없이도 돈다(plan_from_box 로 계획을 직접 만든다).
"""

import asyncio
import os
from types import SimpleNamespace

from unittest import mock

import cv2
import numpy as np
import pytest
from PIL import Image

from app.agents import cut_generator as cg
from app.agents import face_identity as fi
from conftest import make_settings

FIXTURES = os.path.expanduser(os.getenv("FACE_IDENTITY_FIXTURES", "~/Downloads/lora_runs/prod_inputs"))
SAMPLES_CTRL = os.path.expanduser(
    os.getenv("FACE_IDENTITY_SAMPLES_CTRL", "~/Downloads/lora_runs/v4c_ckpt/samples_ctrl")
)
IDS = ("weak_sit", "weak_34", "weak_full", "good_front", "good_cafe", "good_close")
#: build_v4c.py 가 같은 6장으로 낸 기하(v4c_ckpt/meta.json "samples") — 학습 분포의 정본.
V4C_GEOMETRY = {
    "good_cafe": {"box": [370, 234, 161, 227], "crop": [209, 106, 482], "scale": 2.12},
    "good_close": {"box": [307, 365, 182, 230], "crop": [125, 207, 545], "scale": 1.88},
    "good_front": {"box": [355, 252, 135, 180], "crop": [220, 140, 404], "scale": 2.53},
    "weak_34": {"box": [341, 207, 154, 204], "crop": [186, 77, 463], "scale": 2.21},
    "weak_full": {"box": [374, 186, 82, 112], "crop": [292, 120, 244], "scale": 4.2},
    "weak_sit": {"box": [360, 265, 186, 261], "crop": [173, 116, 558], "scale": 1.84},
}

_HAS_WEIGHTS = os.path.exists(os.path.join(fi.default_model_dir(), "face_detection_yunet_2023mar.onnx"))
_HAS_FIXTURES = all(os.path.exists(os.path.join(FIXTURES, f"{sid}.jpg")) for sid in IDS)
_HAS_SAMPLES = all(os.path.exists(os.path.join(SAMPLES_CTRL, f"prod_{sid}.png")) for sid in IDS)
needs_fixtures = pytest.mark.skipif(not (_HAS_WEIGHTS and _HAS_FIXTURES), reason="YuNet weights/prod 픽스처 없음")
needs_samples = pytest.mark.skipif(
    not (_HAS_WEIGHTS and _HAS_FIXTURES and _HAS_SAMPLES), reason="v4c samples_ctrl 없음"
)


def _fixture_bytes(sid: str) -> bytes:
    with open(os.path.join(FIXTURES, f"{sid}.jpg"), "rb") as f:
        return f.read()


def _fixture_image(sid: str) -> Image.Image:
    with Image.open(os.path.join(FIXTURES, f"{sid}.jpg")) as im:
        return im.convert("RGB")


def _synthetic():
    """weights 없이 쓰는 합성 원본(노이즈 600×800) + 얼굴 박스 계획."""
    rng = np.random.default_rng(7)
    orig = Image.fromarray(rng.integers(0, 256, size=(800, 600, 3), dtype=np.uint8))
    plan = fi.plan_from_box(600, 800, (250.0, 300.0, 120.0, 160.0), yaw_proxy=0.05, eye_dist=50.0)
    return orig, plan


# ---------------------------------------------------------------- 기하 = build_v4c


@needs_fixtures
@pytest.mark.parametrize("sid", IDS)
def test_plan_geometry_matches_build_v4c(sid):
    plan = fi.plan_face_pass(_fixture_bytes(sid))
    assert plan is not None
    expected = V4C_GEOMETRY[sid]
    assert [round(v) for v in plan.box] == expected["box"]
    assert list(plan.crop) == expected["crop"]
    assert round(plan.upscale, 2) == expected["scale"]
    assert plan.width == 848 and plan.height == 1264
    # 크롭 안 얼굴폭은 항상 ≈1024/3 — 게이트로 못 쓴다(모듈 주석 참조). 확인만.
    assert abs(plan.face_box_crop[2] - fi.CROP / 3) < 2.0


@needs_fixtures
def test_low_detail_flag_only_for_weak_full():
    for sid in IDS:
        plan = fi.plan_face_pass(_fixture_bytes(sid))
        assert plan.low_detail is (sid == "weak_full"), sid
    assert fi.plan_face_pass(_fixture_bytes("weak_full")).upscale > 4.0


@needs_samples
@pytest.mark.parametrize("sid", IDS)
def test_build_control_matches_training_sample_control(sid):
    img = _fixture_image(sid)
    plan = fi.plan_from_image(img)
    # 학습 데이터 재현이므로 학습 타원으로 계획을 다시 만든다(제품 기본은 확장 타원).
    plan = fi.plan_from_box(img.size[0], img.size[1], plan.box, yaw_proxy=plan.yaw_proxy, eye_dist=plan.eye_dist,
                            ellipse=fi.ELLIPSE_TRAIN)
    ctrl = np.asarray(fi.build_control(img, plan), np.int16)
    with Image.open(os.path.join(SAMPLES_CTRL, f"prod_{sid}.png")) as ref_im:
        ref = np.asarray(ref_im.convert("RGB"), np.int16)
    assert ctrl.shape == ref.shape
    diff = np.abs(ctrl - ref)
    changed = float((diff.max(axis=2) > 0).mean())
    assert np.array_equal(ctrl, ref), f"{sid}: max diff {int(diff.max())}, 달라진 픽셀 {changed:.2%}"


def test_plan_from_box_clamps_to_image_edge():
    # 얼굴이 가장자리에 붙어 있으면 크롭이 이미지 밖으로 나가지 않는다(build_v4c 의 clamp).
    plan = fi.plan_from_box(500, 700, (10.0, 20.0, 100.0, 130.0))
    x0, y0, side = plan.crop
    assert side == 300 and x0 == 0 and y0 == 0
    assert plan.upscale == pytest.approx(1024 / 300)
    # 얼굴이 이미지보다 크면 side 는 짧은 변에 잘린다.
    plan = fi.plan_from_box(400, 900, (0.0, 0.0, 300.0, 300.0))
    assert plan.crop[2] == 400


def test_plan_from_image_returns_none_without_face_when_weights_present():
    if not _HAS_WEIGHTS:
        pytest.skip("YuNet weights 미번들")
    blank = Image.new("RGB", (640, 640), (120, 120, 120))
    assert fi.plan_from_image(blank) is None


# ---------------------------------------------------------------- 프롬프트


def test_build_prompt_uses_short_sample_form():
    base = (848, 1264, (355.0, 252.0, 135.0, 180.0))
    front = fi.plan_from_box(*base, yaw_proxy=0.05)
    three_q = fi.plan_from_box(*base, yaw_proxy=0.30)
    profile = fi.plan_from_box(*base, yaw_proxy=0.60)
    # 프롬프트 각도 경계는 0.25(프롬프트 전용): 0.162(weak_34)·0.16·0.24 는 전부 정면 문구
    for y in (0.16, 0.162, 0.24):
        assert fi.build_prompt(fi.plan_from_box(*base, yaw_proxy=y)) == "ohwx man, neutral expression, facing the camera, photograph"
    # 표정 추정이 없으면 EXPR_FALLBACK("neutral expression")로 채운다 — 생략하지 않는다.
    assert fi.build_prompt(front) == "ohwx man, neutral expression, facing the camera, photograph"
    assert fi.build_prompt(three_q) == "ohwx man, neutral expression, turned three-quarters toward camera, photograph"
    assert fi.build_prompt(profile) == "ohwx man, neutral expression, in profile, photograph"
    assert fi.build_prompt(front, "smiling") == "ohwx man, smiling, facing the camera, photograph"
    assert fi.build_prompt(front, "neutral") == "ohwx man, neutral expression, facing the camera, photograph"
    assert fi.build_prompt(front, token="sks woman") == "sks woman, neutral expression, facing the camera, photograph"
    # 경계값: 0.25 는 3/4, 0.45 는 profile
    assert "three-quarters" in fi.build_prompt(fi.plan_from_box(*base, yaw_proxy=0.25))
    assert "in profile" in fi.build_prompt(fi.plan_from_box(*base, yaw_proxy=0.45))
    assert fi.build_prompt(front, "slight smile") == "ohwx man, slight smile, facing the camera, photograph"
    with pytest.raises(ValueError):
        fi.build_prompt(front, "slightly turned")
    # 배경·조명·구도 절은 없다 — 배경은 control 이 준다.
    for p in (fi.build_prompt(front), fi.build_prompt(three_q, "smiling")):
        assert "background" not in p and "lighting" not in p and "portrait" not in p


# ---------------------------------------------------------------- 합성


@pytest.mark.parametrize("feather", [0.12, 0.25])
def test_composite_keeps_pixels_outside_mask_identical(feather):
    orig, plan = _synthetic()
    generated = Image.new("RGB", (fi.CROP, fi.CROP), (200, 30, 30))
    out = fi.composite(orig, generated, plan, feather=feather)
    assert out.size == orig.size and out.mode == "RGB"
    o = np.asarray(orig)
    r = np.asarray(out)
    alpha = fi.paste_alpha(plan, feather)
    outside = alpha == 0
    assert outside.any() and (~outside).any()
    assert np.array_equal(r[outside], o[outside])
    # 얼굴 박스 중심은 바뀌어야 한다.
    cx, cy = (int(v) for v in plan.anchor_center)
    assert not np.array_equal(r[cy, cx], o[cy, cx])
    # 크롭 정사각형 밖은 당연히 그대로.
    x0, y0, _side = plan.crop
    assert np.array_equal(r[: y0, :], o[: y0, :]) and np.array_equal(r[:, : x0], o[:, : x0])


def test_composite_wider_feather_touches_more_pixels():
    _, plan = _synthetic()
    a12 = fi.paste_alpha(plan, 0.12) > 0
    a25 = fi.paste_alpha(plan, 0.25) > 0
    assert a25.sum() > a12.sum()


def test_composite_color_shift_and_conditional_grain():
    orig, plan = _synthetic()
    # 생성 얼굴이 단색(고주파 0)이면 원본(노이즈) 대비 0.85 미만 → grain 적용
    flat = Image.new("RGB", (fi.CROP, fi.CROP), (90, 90, 90))
    _, meta = fi.composite_with_meta(orig, flat, plan)
    assert meta["feather"] == fi.FEATHER_FRAC
    assert len(meta["color_shift"]) == 3 and any(abs(v) > 0 for v in meta["color_shift"])
    assert meta["hf_std_result"] < fi.GRAIN_MIN_RATIO * meta["hf_std_orig"]
    assert meta["grain_applied"] is True
    assert meta["hf_std_after_grain"] > meta["hf_std_result"]
    # 강제 off
    _, meta_off = fi.composite_with_meta(orig, flat, plan, grain=False)
    assert meta_off["grain_applied"] is False and "hf_std_after_grain" not in meta_off
    # 생성이 원본 크롭 그대로면 고주파가 같아 grain 이 붙지 않는다
    same = fi.crop_1024(orig, plan)
    _, meta_same = fi.composite_with_meta(orig, same, plan)
    assert meta_same["grain_applied"] is False
    assert meta_same["hf_std_result"] >= fi.GRAIN_MIN_RATIO * meta_same["hf_std_orig"]


def _edge_crossing(*, flush: bool = False):
    """타원이 크롭 상단 밖으로 나가는 계획(ZARA 컷2 와 같은 상황).

    flush=True 면 **크롭 상단이 사진 맨 위에 닿는다**(얼굴이 위쪽에 있어 y0 이 0 으로 clamp) —
    그 변에는 이을 원본이 없어 페이드를 걸지 않는다(fade_sides).
    """
    rng = np.random.default_rng(11)
    if flush:
        orig = Image.fromarray(rng.integers(0, 256, size=(1600, 1200, 3), dtype=np.uint8))
        plan = fi.plan_from_box(1200, 1600, (500.0, 20.0, 300.0, 400.0), yaw_proxy=0.02, eye_dist=140.0)
    else:
        orig = Image.fromarray(rng.integers(0, 256, size=(2000, 1200, 3), dtype=np.uint8))
        plan = fi.plan_from_box(1200, 2000, (500.0, 500.0, 300.0, 400.0), yaw_proxy=0.02, eye_dist=140.0)
    return orig, plan


def test_edge_fade_zeroes_alpha_on_crossing_side():
    """EDGE_FADE_PX: 타원이 크롭을 벗어난 변에서 페더 알파는 1 이지만 합성 알파는 0 이어야 한다.

    단 **크롭 변이 사진 안쪽일 때**다 — 사진 가장자리에 붙은 변은 아래 테스트가 따로 본다.
    """
    _, plan = _edge_crossing()
    left, top, right, bottom = fi.crossing_sides(plan)
    assert top and not (left or right or bottom), "E2 는 상단만 크롭을 벗어난다"
    assert fi.at_photo_edge(plan) == (False, False, False, False), "이 계획은 사진 안쪽이다"
    feathered = np.asarray(fi.feather_mask(plan), np.float32) / 255.0
    assert feathered[0].max() > 0.9, "페더만으로는 상단에서 잘린다(= 사각 테두리 원인)"
    alpha = fi.composite_alpha(plan)
    assert alpha[0].max() == 0.0
    col = fi.CROP // 2
    assert alpha[fi.EDGE_FADE_PX, col] == pytest.approx(feathered[fi.EDGE_FADE_PX, col], abs=1e-6)
    assert alpha[fi.EDGE_FADE_PX // 2, col] < feathered[fi.EDGE_FADE_PX // 2, col]
    # 원본 해상도 알파도 크롭 첫 행이 0 (하드제로 3px 덕분에 축소 보간이 되살리지 않는다)
    pa = fi.paste_alpha(plan)
    x0, y0, side = plan.crop
    assert pa[y0, x0 : x0 + side].max() == 0.0


def test_edge_fade_is_skipped_where_the_crop_touches_the_photo_edge():
    """사진 맨 위에 닿은 변은 페이드하지 않는다 — 이을 원본이 없는데 알파만 깎여 원본 머리가 되살아난다.

    2026-09-11 잔머리 A/B: 상단 48px 띠가 세 컷 모두 정수리 머리 위에 있었고, 띠 안에서 원본이
    최대 100% 로 섞였다. 페이드를 빼면 그 자리에서 생성 쪽이 온전히 들어간다(측정: 1.0 → 0.25~0.28).
    """
    _, plan = _edge_crossing(flush=True)
    assert plan.crop[1] == 0
    assert fi.crossing_sides(plan)[1] is True        # 타원은 여전히 크롭 위로 나간다
    assert fi.at_photo_edge(plan)[1] is True
    assert fi.fade_sides(plan)[1] is False

    feathered = np.asarray(fi.feather_mask(plan), np.float32) / 255.0
    alpha = fi.composite_alpha(plan)
    assert np.array_equal(alpha, feathered), "그 변에서는 페더 그대로 — 깎는 것이 없다"
    x0, y0, side = plan.crop
    assert fi.paste_alpha(plan)[y0, x0 : x0 + side].max() == pytest.approx(1.0, abs=1e-3)


def test_edge_fade_leaves_non_crossing_sides_untouched():
    """벗어나지 않은 변은 손대지 않는다 — 4변 일괄이면 좌·우에서 페더 꼬리를 깎는다."""
    _, plan = _synthetic()
    left, top, right, bottom = fi.crossing_sides(plan)
    assert top and not (left or right or bottom)
    feathered = np.asarray(fi.feather_mask(plan), np.float32) / 255.0
    alpha = fi.composite_alpha(plan)
    assert (alpha <= feathered + 1e-6).all(), "페이드는 알파를 올리지 않는다"
    band = fi.EDGE_FADE_PX
    assert feathered[:, :band].max() > 0.0, "이 픽스처는 좌측 밴드에 페더 꼬리가 있다"
    below = slice(band, None)  # 상단 페이드가 걸리는 행은 제외 — 그건 상단 변의 일이다
    assert np.array_equal(alpha[below, :band], feathered[below, :band])
    assert np.array_equal(alpha[below, -band:], feathered[below, -band:])
    assert np.array_equal(alpha[-band:, :], feathered[-band:, :])
    assert feathered[0].max() > 0.9 and alpha[0].max() == 0.0
    # 페이드를 끄면 정확히 페더로 돌아간다
    assert np.array_equal(fi.composite_alpha(plan, edge_fade_px=0), feathered)


def test_color_correction_stays_global_ring_shift():
    """22.B 회귀 고정: 색 보정은 **전역** 링 shift 다. 국소 차분 필드는 지표는 좋아도 육안이 나빠져 되돌렸다.

    (ZARA 컷1: 링 잔차 27.2→2.26 이지만 목 색이동 38.7→65.6 으로 노랗게 과보정. seamlessClone 은
     신원 붕괴 0.669→0.151.) 그래서 보정량은 타원 안에서 상수여야 한다.
    """
    orig, plan = _synthetic()
    up = np.asarray(fi.crop_1024(orig, plan), np.float32)
    ramp = np.linspace(-40.0, 40.0, fi.CROP, dtype=np.float32)[None, :, None]
    gen = Image.fromarray(np.clip(up + ramp, 0, 255).astype(np.uint8))
    _, meta = fi.composite_with_meta(orig, gen, plan, grain=False)
    assert "color_field_max" not in meta and "color_field_ring_mean" not in meta
    assert len(meta["color_shift"]) == 3
    # 링 평균과 정확히 일치하는 상수여야 한다(공간적으로 변하지 않는다)
    ell = np.asarray(fi.ellipse_mask(plan)) > 127
    k = 2 * fi.COLOR_RING_PX + 1
    ring = ell & ~cv2.erode(ell.astype(np.uint8), np.ones((k, k), np.uint8)).astype(bool)
    gen_arr = np.asarray(gen, np.float32)
    expected = up[ring].mean(axis=0) - gen_arr[ring].mean(axis=0)
    assert meta["color_shift"] == pytest.approx([round(float(v), 2) for v in expected], abs=0.01)


def test_composite_accepts_non_1024_generation():
    orig, plan = _synthetic()
    out = fi.composite(orig, Image.new("RGB", (512, 512), (10, 200, 10)), plan)
    assert out.size == orig.size


def _fake_det(plan):
    """plan 기하를 그대로 만족하는 가짜 검출 — 합성 노이즈 픽스처에는 실제 얼굴이 없다."""
    x, y, w, h = plan.box
    ax, ay = plan.anchor_center
    return fi.FaceDetection(box=(ax - w / 2, ay - h / 2, w, h), yaw_proxy=plan.yaw_proxy,
                            eye_dist=plan.eye_dist, score=1.0,
                            landmarks=((x + w * 0.3, y + h * 0.35), (x + w * 0.7, y + h * 0.35),
                                       (x + w * 0.5, y + h * 0.55),
                                       (x + w * 0.35, y + h * 0.75), (x + w * 0.65, y + h * 0.75)))


def test_gate_identity_low_uses_reference_median():
    """GATE_IDENTITY_MIN: 기준셋을 주면 신원 낮은 결과가 identity_low 로 걸린다. 안 주면 기존과 동일."""
    orig, plan = _synthetic()
    result = fi.composite(orig, fi.crop_1024(orig, plan), plan)
    hi = [np.array([1.0, 0.0], np.float32)] * 3
    lo = [np.array([0.0, 1.0], np.float32)] * 3
    with mock.patch.object(fi, "detect_face", return_value=_fake_det(plan)), \
         mock.patch.object(fi, "face_embedding", return_value=np.array([1.0, 0.0], np.float32)):
        g_ok = fi.evaluate_gate(plan, result, references=hi)
        assert g_ok.reason == "ok" and g_ok.identity == 1.0
        g_low = fi.evaluate_gate(plan, result, references=lo)
        assert g_low.reason == "identity_low" and g_low.passed is False
        assert g_low.identity is not None and g_low.identity < fi.GATE_IDENTITY_MIN
        # references 미지정이면 신원 검사를 하지 않는다(하위 호환)
        g_none = fi.evaluate_gate(plan, result)
        assert g_none.reason == "ok" and g_none.identity is None


def test_gate_lighting_off_uses_color_ring_mean():
    """GATE_COLOR_MAX: 링 차분 필드의 링 평균 |RGB| 최대가 문턱을 넘으면 lighting_off."""
    orig, plan = _synthetic()
    result = fi.composite(orig, fi.crop_1024(orig, plan), plan)
    with mock.patch.object(fi, "detect_face", return_value=_fake_det(plan)):
        assert fi.evaluate_gate(plan, result, color_ring_mean=[fi.GATE_COLOR_MAX - 1, 0.0, 0.0]).reason == "ok"
        g = fi.evaluate_gate(plan, result, color_ring_mean=[0.0, fi.GATE_COLOR_MAX + 1, 0.0])
        assert g.reason == "lighting_off" and g.passed is False
        assert g.color_max == pytest.approx(fi.GATE_COLOR_MAX + 1)
        # 부호 무관
        assert fi.evaluate_gate(plan, result,
                                color_ring_mean=[-(fi.GATE_COLOR_MAX + 1), 0.0, 0.0]).reason == "lighting_off"
        # 미지정이면 검사하지 않는다
        assert fi.evaluate_gate(plan, result).color_max is None


@needs_fixtures
def test_recognizer_wiring_and_cosine():
    """_recognizer 배선: SFace 임베딩 128-d, 자기 자신 코사인 1.0, 0 벡터는 ZeroDivision 없이 0."""
    img = _fixture_image("good_front")
    det = fi.detect_face(img)
    assert det is not None
    emb = fi.face_embedding(img, det)
    assert emb.shape == (128,)
    assert fi.cosine(emb, emb) == pytest.approx(1.0, abs=1e-5)
    assert fi.cosine(emb, np.zeros_like(emb)) == 0.0
    # identity_score 는 기준셋 중앙값 — 자기 자신만 주면 1.0
    assert fi.identity_score(img, [emb], det) == pytest.approx(1.0, abs=1e-3)
    assert fi.identity_score(img, None, det) is None


def test_feather_bottom_none_is_byte_identical():
    """feather_bottom=None 은 기존 단일 σ 경로와 **바이트 동일**해야 한다(기본값 무해)."""
    orig, plan = _synthetic()
    gen = Image.new("RGB", (fi.CROP, fi.CROP), (120, 60, 40))
    a = np.asarray(fi.feather_mask(plan))
    b = np.asarray(fi.feather_mask(plan, feather_bottom=None))
    assert np.array_equal(a, b)
    # 합성 결과도 동일
    out_a = fi.composite(orig, gen, plan, grain=False)
    out_b = fi.composite(orig, gen, plan, grain=False, feather_bottom=None)
    assert out_a.tobytes() == out_b.tobytes()
    # 학습 control 경로(binary_mask)는 feather_bottom 을 아예 보지 않는다
    assert np.array_equal(np.asarray(fi.binary_mask(plan)),
                          np.asarray(fi.feather_mask(plan, fi.FEATHER_FRAC).point(lambda v: 255 if v > 127 else 0)))


def test_feather_bottom_narrows_only_below_chin():
    """하단 σ 는 턱선 아래만 좁힌다 — 턱선 위 σ_상단 밖은 손대지 않는다."""
    _, plan = _synthetic()
    wide = np.asarray(fi.feather_mask(plan), np.float32)
    narrow = np.asarray(fi.feather_mask(plan, feather_bottom=0.03), np.float32)
    fb = plan.face_box_crop
    chin = int(fb[1] + fb[3])
    r_top = max(3, int(fi.FEATHER_FRAC * fb[2]))
    # 보간 시작(턱선 − σ_상단) 위쪽은 완전히 동일
    assert np.array_equal(wide[: chin - r_top], narrow[: chin - r_top])
    # 아래쪽 경계는 더 가파르다(같은 열에서 0→255 전이 폭이 좁아진다)
    col = int(fb[0] + fb[2] / 2)
    def width(mask):
        below = mask[chin + r_top :, col]
        mid = np.nonzero((below > 20) & (below < 235))[0]
        return int(mid.max() - mid.min()) if mid.size else 0
    assert width(narrow) < width(wide)


# ---------------------------------------------------------------- 게이트


@needs_fixtures
def test_gate_passes_for_original_and_fails_on_size_or_center_shift():
    img = _fixture_image("good_front")
    plan = fi.plan_from_image(img)
    ok = fi.evaluate_gate(plan, img)
    assert ok.passed and ok.reason == "ok" and fi.check_gate(plan, img)
    bx, by, bw, bh = plan.box
    wide = fi.plan_from_box(plan.width, plan.height, (bx, by, bw * 1.6, bh))
    assert fi.evaluate_gate(wide, img).reason == "face_width"
    shifted = fi.plan_from_box(plan.width, plan.height, (bx, by + 0.5 * bh, bw, bh))
    assert fi.evaluate_gate(shifted, img).reason == "center_off"
    blank = Image.new("RGB", img.size, (128, 128, 128))
    assert fi.evaluate_gate(plan, blank).reason == "no_face" and not fi.check_gate(plan, blank)


@needs_fixtures
def test_weak_34_prompt_is_front_facing_not_three_quarters():
    plan = fi.plan_face_pass(_fixture_bytes("weak_34"))
    assert 0.15 < plan.yaw_proxy < 0.25
    prompt = fi.build_prompt(plan)
    assert "facing the camera" in prompt and "three-quarters" not in prompt


@needs_fixtures
def test_expression_estimate_fixed_on_prod_fixtures():
    front = fi.plan_face_pass(_fixture_bytes("good_front"))
    cafe = fi.plan_face_pass(_fixture_bytes("good_cafe"))
    assert front.expression == "smiling", front.expression_metrics
    assert cafe.expression == "neutral", cafe.expression_metrics
    for plan in (front, cafe):
        for key in ("mc", "ratio", "teeth", "mouth_w", "lip_px"):
            assert key in plan.expression_metrics, key
    assert fi.build_prompt(front) == "ohwx man, smiling, facing the camera, photograph"
    assert fi.build_prompt(cafe) == "ohwx man, neutral expression, facing the camera, photograph"
    # 명시 None 도 생략이 아니라 neutral expression 으로 채운다(뚱한 얼굴 방지)
    assert fi.build_prompt(front, None) == "ohwx man, neutral expression, facing the camera, photograph"
    assert fi.build_prompt(cafe, "smiling") == "ohwx man, smiling, facing the camera, photograph"
    # 메타에 추정값·근거 수치
    meta = front.to_meta()
    assert meta["expression"] == "smiling" and meta["expression_metrics"]["mc"] <= fi.EXPR_SMILE_MC_MAX


@needs_fixtures
def test_expression_ambiguous_falls_back_to_neutral():
    plan = fi.plan_face_pass(_fixture_bytes("weak_34"))
    assert plan.expression is None and plan.expression_metrics.get("reason") == "ambiguous"
    assert fi.build_prompt(plan) == "ohwx man, neutral expression, facing the camera, photograph"


def test_expression_skipped_for_profiles_and_missing_landmarks():
    img = Image.new("RGB", (400, 400), (150, 120, 100))
    det = fi.FaceDetection(box=(100.0, 100.0, 120.0, 150.0), yaw_proxy=0.5, eye_dist=50.0, score=0.9,
                           landmarks=((120.0, 140.0), (170.0, 140.0), (145.0, 170.0), (125.0, 200.0), (165.0, 200.0)))
    assert fi.estimate_expression(img, det) == (None, {"reason": "profile"})
    det2 = fi.FaceDetection(box=(100.0, 100.0, 120.0, 150.0), yaw_proxy=0.1, eye_dist=50.0, score=0.9)
    assert fi.estimate_expression(img, det2) == (None, {"reason": "no_landmarks"})
    # 입술 마스크가 없는 단색 얼굴 → 생략
    det3 = fi.FaceDetection(box=(100.0, 100.0, 120.0, 150.0), yaw_proxy=0.1, eye_dist=50.0, score=0.9, landmarks=det.landmarks)
    label, metrics = fi.estimate_expression(img, det3)
    assert label is None and metrics["reason"] in ("no_lip_mask", "ambiguous")


def test_gate_fails_when_result_turned_more_than_input(monkeypatch):
    plan = fi.plan_from_box(848, 1264, (341.0, 207.0, 154.0, 204.0), yaw_proxy=0.162)
    assert fi.yaw_drift_limit(0.162) == pytest.approx(0.324)
    assert fi.yaw_drift_limit(0.05) == 0.20

    def fake_detect(yaw):
        return lambda image, model_dir=None: fi.FaceDetection(box=(341.0, 207.0, 154.0, 204.0), yaw_proxy=yaw, eye_dist=60.0, score=0.9)

    img = Image.new("RGB", (848, 1264), (90, 90, 90))
    monkeypatch.setattr(fi, "detect_face", fake_detect(0.43))  # 지난 회차 weak_34 결과
    r = fi.evaluate_gate(plan, img)
    assert not r.passed and r.reason == "yaw_drift" and r.yaw_proxy == 0.43
    monkeypatch.setattr(fi, "detect_face", fake_detect(0.30))
    assert fi.evaluate_gate(plan, img).reason == "ok"
    # 입력이 정면(0.05)이면 상한은 0.20
    front = fi.plan_from_box(848, 1264, (341.0, 207.0, 154.0, 204.0), yaw_proxy=0.05)
    monkeypatch.setattr(fi, "detect_face", fake_detect(0.21))
    assert fi.evaluate_gate(front, img).reason == "yaw_drift"


# ---------------------------------------------------------------- 실행기


class _SeqBackend:
    """시드별로 정해둔 이미지를 돌려준다. 없으면 회색(얼굴 없음 → 게이트 실패)."""

    def __init__(self, by_seed):
        self.by_seed = by_seed
        self.calls = []

    def render(self, control, prompt, seed):
        self.calls.append((prompt, seed))
        return self.by_seed.get(seed, Image.new("RGB", (fi.CROP, fi.CROP), (128, 128, 128)))


class _BoomBackend:
    def render(self, control, prompt, seed):
        raise RuntimeError("gpu on fire")


@needs_fixtures
def test_run_face_pass_adopts_first_seed_that_passes_gate():
    data = _fixture_bytes("good_front")
    img = _fixture_image("good_front")
    plan = fi.plan_from_image(img)
    # seed 44 에서만 '진짜 얼굴'(원본 크롭)을 돌려준다 → 42·43 은 게이트 실패, 44 채택
    backend = _SeqBackend({44: fi.crop_1024(img, plan)})
    res = fi.run_face_pass(data, backend, mime="image/jpeg")
    assert res.applied and res.mime == "image/png" and res.image[:8] == b"\x89PNG\r\n\x1a\n"
    m = res.meta
    assert m["seed"] == 44 and m["attempts"] == 3 and m["fallback"] is False and m["reason"] == "ok"
    assert [t["seed"] for t in m["tries"]] == [42, 43, 44]
    assert m["tries"][0]["gate"] == "no_face" and m["tries"][2]["gate"] == "ok"
    for key in ("upscale", "low_detail", "face_width_in", "face_width_out", "yaw_proxy", "elapsed_ms", "prompt"):
        assert key in m, key
    assert m["prompt"] == fi.build_prompt(plan)
    assert backend.calls[0][0] == m["prompt"]
    with Image.open(__import__("io").BytesIO(res.image)) as out:
        assert out.size == img.size


@needs_fixtures
def test_run_face_pass_falls_back_when_all_seeds_fail_or_backend_raises():
    data = _fixture_bytes("good_cafe")
    res = fi.run_face_pass(data, _SeqBackend({}), seeds=(1, 2), mime="image/jpeg")
    assert not res.applied and res.image == data and res.mime == "image/jpeg"
    assert res.meta["fallback"] is True and res.meta["reason"] == "gate_failed" and res.meta["attempts"] == 2
    res = fi.run_face_pass(data, _BoomBackend(), mime="image/jpeg")
    assert not res.applied and res.image == data
    assert res.meta["reason"] == "error:RuntimeError" and res.meta["attempts"] == 1


def test_run_face_pass_never_raises_on_garbage_bytes():
    res = fi.run_face_pass(b"not an image", fi.NullBackend(), mime="image/jpeg")
    assert not res.applied and res.image == b"not an image" and res.meta["reason"].startswith("error:")


@needs_fixtures
def test_run_face_pass_null_backend_returns_bytes():
    res = fi.run_face_pass(_fixture_bytes("good_close"), fi.NullBackend(), mime="image/jpeg")
    assert isinstance(res.image, bytes) and res.meta["attempts"] >= 1


# ---------------------------------------------------------------- 레지스트리·설정


def test_settings_default_off_and_env_switch(monkeypatch):
    from app.config import load_settings

    for key in ("FACE_IDENTITY_ENABLED", "FACE_IDENTITY_BACKEND_URL", "FACE_IDENTITY_LORA_PATH"):
        monkeypatch.delenv(key, raising=False)
    s = load_settings()
    assert s.face_identity_enabled is False and s.face_identity_backend_url is None and s.face_identity_lora_path is None
    monkeypatch.setenv("FACE_IDENTITY_ENABLED", "true")
    monkeypatch.setenv("FACE_IDENTITY_BACKEND_URL", "http://gpu.internal/face/")
    monkeypatch.setenv("FACE_IDENTITY_LORA_PATH", "/opt/lora")
    s = load_settings()
    assert s.face_identity_enabled is True
    assert s.face_identity_backend_url == "http://gpu.internal/face" and s.face_identity_lora_path == "/opt/lora"


def test_resolve_backend_prefers_url_then_local_lora(tmp_path, monkeypatch):
    monkeypatch.setattr(fi, "_BACKENDS", {})
    spec = fi.FaceIdentitySpec("v5.safetensors")
    http = fi.resolve_backend(SimpleNamespace(face_identity_backend_url="http://gpu/face", face_identity_lora_path=None), spec)
    assert isinstance(http, fi.HttpFaceBackend) and http.lora == "v5.safetensors"
    missing = fi.resolve_backend(SimpleNamespace(face_identity_backend_url=None, face_identity_lora_path=str(tmp_path)), spec)
    assert missing is None
    (tmp_path / "v5.safetensors").write_bytes(b"x")
    local = fi.resolve_backend(SimpleNamespace(face_identity_backend_url=None, face_identity_lora_path=str(tmp_path)), spec)
    assert isinstance(local, fi.QwenLocalBackend) and local.lora_path == str(tmp_path / "v5.safetensors")
    assert local.steps == 25 and local.guidance_scale == 4.0 and local.negative_prompt == ""


# ---------------------------------------------------------------- generate() 배선


class _FakeGemini:
    async def generate_content_image(self, model, prompt, images, image_size, aspect_ratio):
        return SimpleNamespace(image=b"FULL", mime="image/png")


_SPEC = {"cutType": "styling", "direction": "front", "shot": "medium", "refScope": "all",
         "modelId": "mX", "faceExposure": "show"}
_PRODUCT = {"name": "티셔츠", "clothingType": "top", "colors": []}
#: 얼굴 패스 근거는 fm_model_loras 한 곳이다 — 워커가 읽어 face_identity_spec 으로 넘긴다.
#: (가상모델 JSON faceIdentity 경로는 2026-09-11 삭제: 항목 0개, 근거가 둘이면 추적이 두 배)
_LORA_SPEC = fi.FaceIdentitySpec("facemarket/loras/m/v1.safetensors", "ohwx man")


def _forbid_face_pass(monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("face pass must not run")

    monkeypatch.setattr(cg.face_identity, "apply_face_pass", boom)


def test_generate_unchanged_when_face_identity_disabled(monkeypatch):
    _forbid_face_pass(monkeypatch)
    settings = make_settings(gemini_api_key="x")
    assert settings.face_identity_enabled is False
    assert asyncio.run(cg.generate(settings, _FakeGemini(), _SPEC, _PRODUCT, [],
                                   face_identity_spec=_LORA_SPEC)) == (b"FULL", "image/png")


def test_generate_runs_face_pass_only_with_a_lora_spec(monkeypatch):
    seen = []

    async def fake_pass(settings, image, mime, spec, *, expression=None, outcome=None,
                        url_provider=None):
        seen.append((image, mime, spec, expression))
        return b"FACE", "image/png"

    monkeypatch.setattr(cg.face_identity, "apply_face_pass", fake_pass)
    settings = make_settings(gemini_api_key="x", face_identity_enabled=True)
    run = lambda spec, **kw: asyncio.run(  # noqa: E731
        cg.generate(settings, _FakeGemini(), spec, _PRODUCT, [], **kw))

    assert run(_SPEC, face_identity_spec=_LORA_SPEC) == (b"FACE", "image/png")
    assert seen == [(b"FULL", "image/png", _LORA_SPEC, None)]

    seen.clear()
    # 근거(LoRA 행)가 없으면 그대로 — 가상모델도 여기에 해당한다
    assert run(_SPEC) == (b"FULL", "image/png")
    # 얼굴을 가리는 컷 → 그대로
    assert run({**_SPEC, "faceExposure": "hide"}, face_identity_spec=_LORA_SPEC) == (b"FULL", "image/png")
    # 뒷모습 → 그대로
    assert run({**_SPEC, "direction": "back"}, face_identity_spec=_LORA_SPEC) == (b"FULL", "image/png")
    # 상품컷 → 그대로
    assert run({"cutType": "product", "modelId": "mX"}, face_identity_spec=_LORA_SPEC) == (b"FULL", "image/png")
    # modelId 없음 → 그대로
    assert run({k: v for k, v in _SPEC.items() if k != "modelId"},
               face_identity_spec=_LORA_SPEC) == (b"FULL", "image/png")
    assert seen == []


def test_generate_bottom_medium_skips_face_pass_but_still_crops(monkeypatch):
    # 하의 medium 프레이밍은 머리가 프레임에 없다(_face_fits) → 얼굴 패스 생략, 포즈 크롭은 그대로.
    _forbid_face_pass(monkeypatch)
    crops = []

    async def fake_crop(settings, image, mime, clothing_type):
        crops.append(image)
        return b"CROPPED", mime

    monkeypatch.setattr(cg.pose_crop, "crop_pose_medium", fake_crop)
    settings = make_settings(gemini_api_key="x", face_identity_enabled=True)
    manifest = cg.build_manifest([], has_mannequin=False, has_match=False, mood_count=0, example_scope="pose")
    res = asyncio.run(cg.generate(
        settings, _FakeGemini(),
        {**_SPEC, "refScope": "pose"}, {**_PRODUCT, "clothingType": "bottom"}, [], manifest=manifest,
        face_identity_spec=_LORA_SPEC,
    ))
    assert res == (b"CROPPED", "image/png") and crops == [b"FULL"]


def test_generate_bottom_full_shot_gets_face_pass(monkeypatch):
    async def fake_pass(settings, image, mime, spec, *, expression=None, outcome=None,
                        url_provider=None):
        return b"FACE", "image/png"

    monkeypatch.setattr(cg.face_identity, "apply_face_pass", fake_pass)
    settings = make_settings(gemini_api_key="x", face_identity_enabled=True)
    res = asyncio.run(cg.generate(
        settings, _FakeGemini(), {**_SPEC, "shot": "full"}, {**_PRODUCT, "clothingType": "bottom"}, [],
        face_identity_spec=_LORA_SPEC,
    ))
    assert res == (b"FACE", "image/png")

# ---------------------------------------------------------------- 확정 설정(2026-09-08)


def test_expanded_ellipse_is_the_default_and_matches_the_formula():
    # 기본은 E2 에서 하단만 1.20 으로 내린 값(2026-09-10, 8컷 스윕 채택).
    # 하단 1.30 원판은 ELLIPSE_E2_BOTTOM130, 그 이전 확장본은 ELLIPSE_PREV, 학습용은 ELLIPSE_TRAIN.
    assert fi.ELLIPSE == (-0.75, -1.45, 1.75, 1.20)
    assert fi.ELLIPSE_E2_BOTTOM130 == (-0.75, -1.45, 1.75, 1.30)
    assert fi.ELLIPSE_PREV == (-0.55, -1.10, 1.55, 1.25)
    assert fi.ELLIPSE_TRAIN == (-0.30, -0.60, 1.30, 1.15)
    plan = fi.plan_from_box(848, 1264, (355.0, 252.0, 135.0, 180.0))
    fb = plan.face_box_crop
    assert list(plan.ellipse) == pytest.approx([fb[0] - 0.75 * fb[2], fb[1] - 1.45 * fb[3], fb[0] + 1.75 * fb[2], fb[1] + 1.20 * fb[3]])
    prev = fi.plan_from_box(848, 1264, (355.0, 252.0, 135.0, 180.0), ellipse=fi.ELLIPSE_PREV)
    assert list(prev.ellipse) == pytest.approx([fb[0] - 0.55 * fb[2], fb[1] - 1.10 * fb[3], fb[0] + 1.55 * fb[2], fb[1] + 1.25 * fb[3]])
    # 면적 순서: 학습 < 이전 확장 < 현재 < 하단 1.30 원판 (하단만 줄였으므로 현재가 원판보다 작다)
    def area(pl):
        return int((np.asarray(fi.ellipse_mask(pl)) > 127).sum())
    train = fi.plan_from_box(848, 1264, (355.0, 252.0, 135.0, 180.0), ellipse=fi.ELLIPSE_TRAIN)
    e2 = fi.plan_from_box(848, 1264, (355.0, 252.0, 135.0, 180.0), ellipse=fi.ELLIPSE_E2_BOTTOM130)
    assert area(train) < area(prev) < area(plan) < area(e2)
    assert list(train.ellipse) == pytest.approx([fb[0] - 0.30 * fb[2], fb[1] - 0.60 * fb[3], fb[0] + 1.30 * fb[2], fb[1] + 1.15 * fb[3]])
    # 페더 기본은 0.12
    assert fi.FEATHER_FRAC == 0.12


def test_expression_word_is_never_empty():
    base = (848, 1264, (355.0, 252.0, 135.0, 180.0))
    for yaw in (0.05, 0.30, 0.60):
        for expr in (fi.AUTO, None, "neutral", "neutral expression", "smiling", "slight", "slight smile"):
            for est in (None, "neutral", "smiling"):
                plan = fi.plan_from_box(*base, yaw_proxy=yaw, expression=est)
                p = fi.build_prompt(plan, expr)
                parts = p.split(", ")
                assert len(parts) == 4, p
                assert parts[1] in ("neutral expression", "slight smile", "smiling"), p
                assert all(x.strip() for x in parts), p
    with pytest.raises(ValueError):
        fi.build_prompt(fi.plan_from_box(*base), "grinning")


def test_yaw_apply_rule_branches():
    base = (848, 1264, (355.0, 252.0, 135.0, 180.0))

    def branch(yaw):
        plan = fi.plan_from_box(*base, yaw_proxy=yaw)
        if plan.yaw_proxy > fi.YAW_APPLY_MAX:
            return "skip"
        return "pose_risk" if plan.yaw_proxy > fi.YAW_POSE_RISK_MIN else "apply"

    # 0.45 는 Lanczos 기준 잠정값이었고, ESRGAN 전처리 실측으로 0.65 로 올렸다(0.25~0.65 = pose_risk).
    assert fi.YAW_APPLY_MAX == 0.65 and fi.YAW_POSE_RISK_MIN == 0.25
    assert [branch(y) for y in (0.24, 0.26, 0.44, 0.46, 0.64, 0.66)] == [
        "apply", "pose_risk", "pose_risk", "pose_risk", "pose_risk", "skip"]


@needs_fixtures
def test_prepare_image_flags_yaw_skip_and_pose_risk(monkeypatch):
    img = _fixture_image("good_front")  # yaw 0.055
    _, plan, meta = fi.prepare_image(img)
    assert plan is not None and meta["skipped_reason"] is None and meta["pose_risk"] is False
    det = fi.detect_face(img)
    for yaw, expect_reason, expect_risk in ((0.30, None, True), (0.60, None, True), (0.70, "yaw", False)):
        monkeypatch.setattr(fi, "detect_face", lambda image, model_dir=None, _d=det, _y=yaw: fi.FaceDetection(
            box=_d.box, yaw_proxy=_y, eye_dist=_d.eye_dist, score=_d.score, landmarks=_d.landmarks))
        _, _plan2, meta2 = fi.prepare_image(img)
        assert meta2["skipped_reason"] == expect_reason and meta2["pose_risk"] is expect_risk


def test_gate_yaw_flatten(monkeypatch):
    plan = fi.plan_from_box(848, 1264, (341.0, 207.0, 154.0, 204.0), yaw_proxy=0.60)
    img = Image.new("RGB", (848, 1264), (90, 90, 90))
    def fake(yaw):
        return lambda image, model_dir=None: fi.FaceDetection(box=(341.0, 207.0, 154.0, 204.0), yaw_proxy=yaw, eye_dist=60.0, score=0.9)
    monkeypatch.setattr(fi, "detect_face", fake(0.30))   # 0.30 < 0.6×0.60 = 0.36 → 정면화
    assert fi.evaluate_gate(plan, img).reason == "yaw_flatten"
    monkeypatch.setattr(fi, "detect_face", fake(0.45))
    assert fi.evaluate_gate(plan, img).reason == "ok"
    # 입력이 정면이면 검사하지 않는다
    front = fi.plan_from_box(848, 1264, (341.0, 207.0, 154.0, 204.0), yaw_proxy=0.10)
    monkeypatch.setattr(fi, "detect_face", fake(0.01))
    assert fi.evaluate_gate(front, img).reason == "ok"


def test_auto_upscale_small_face(monkeypatch):
    small = Image.new("RGB", (440, 660), (120, 110, 100))
    up, meta = fi.auto_upscale(small, 30.0)
    assert meta["upscale_applied"] and meta["upscale_k"] == 5 and meta["upscale_method"] == "lanczos"
    assert up.size == (2200, 3300) and meta["face_w_after"] >= fi.AUTO_UPSCALE_FACE_W_MIN
    # 이미 큰 얼굴은 그대로
    _, m2 = fi.auto_upscale(small, 150.0)
    assert m2["upscale_applied"] is False and m2["upscale_k"] == 1
    # 20px 은 6배 상한에 걸려 120 미만 → 호출자가 too_small 로 건너뛴다
    _, m3 = fi.auto_upscale(small, 19.0)
    assert m3["upscale_k"] == 6 and m3["face_w_after"] < fi.AUTO_UPSCALE_FACE_W_MIN
    # ESRGAN 훅 등록 시 그것을 쓰고, 실패하면 Lanczos 폴백
    fi.set_upscaler(lambda im, k: im.resize((im.width * k, im.height * k), Image.NEAREST))
    try:
        _, m4 = fi.auto_upscale(small, 30.0)
        assert m4["upscale_method"] == "esrgan"
        fi.set_upscaler(lambda im, k: (_ for _ in ()).throw(RuntimeError("gpu")))
        _, m5 = fi.auto_upscale(small, 30.0)
        assert m5["upscale_method"] == "lanczos" and m5["upscale_applied"]
    finally:
        fi.set_upscaler(None)


@needs_fixtures
def test_run_face_pass_skips_small_face_and_reports(monkeypatch):
    tiny = _fixture_image("good_front").resize((212, 316), Image.LANCZOS)  # 얼굴폭 ≈34px
    buf = __import__("io").BytesIO()
    tiny.save(buf, "PNG")
    data = buf.getvalue()

    class _Boom:
        def render(self, control, prompt, seed):
            raise AssertionError("작은 얼굴은 렌더까지 가면 안 된다")

    res = fi.run_face_pass(data, _Boom(), mime="image/png")
    # 자동 확대가 얼굴폭을 120 이상으로 키우면 렌더로 가고(그 경우 _Boom 이 걸린다), 못 키우면 too_small 로 건너뛴다
    assert res.meta["reason"] in ("too_small", "error:AssertionError")
    if res.meta["reason"] == "too_small":
        assert res.image == data and res.applied is False and res.meta["upscale_applied"] is True


# ---------------------------------------------------------------- 2026-09-09 통합 검증 결함 수정


def test_detect_face_downscales_large_images(monkeypatch):
    """긴 변 2000px 초과면 ×4 축소본에서 검출하고 좌표를 되돌린다(v6 빌더·채점과 같은 규칙)."""
    calls = []

    class FakeDet:
        def setInputSize(self, size):
            calls.append(size)

        def detect(self, arr):
            # 축소본 좌표계에서 얼굴 하나(박스 100, 눈 간격 20, 코는 눈 중점)
            f = np.array([[100.0, 200.0, 100.0, 130.0, 140.0, 240.0, 160.0, 240.0, 150.0, 260.0,
                           145.0, 285.0, 155.0, 285.0, 0.99]], dtype=np.float32)
            return 1, f

    monkeypatch.setattr(fi, "_detector", lambda model_dir: FakeDet())
    big = Image.new("RGB", (4000, 6000), (120, 110, 100))
    det = fi.detect_face(big)
    assert calls[-1] == (1000, 1500)          # ×4 축소본 크기로 검출
    assert det.box == (400.0, 800.0, 400.0, 520.0)   # 좌표 ×4 복원
    assert det.eye_dist == 80.0                       # 랜드마크도 복원(20 × 4)
    assert det.landmarks[0] == (560.0, 960.0)
    assert det.score == pytest.approx(0.99)           # 점수는 스케일 대상 아님
    calls.clear()
    small = Image.new("RGB", (1200, 1800), (120, 110, 100))
    fi.detect_face(small)
    assert calls[-1] == (1200, 1800)          # 2000px 이하는 원본 해상도
    calls.clear()
    fi.detect_face(big, downscale=False)
    assert calls[-1] == (4000, 6000)          # 학습 재현 경로는 축소 없음


@needs_samples
@pytest.mark.parametrize("sid", IDS)
def test_training_control_still_pixel_identical_after_downscale_rule(sid):
    """축소 규칙 도입이 학습 경로를 건드리지 않는지 — prod 입력(848×1264)은 2000px 이하라 무영향."""
    img = _fixture_image(sid)
    assert max(img.size) <= 2000
    plan = fi.plan_from_image(img)
    plan = fi.plan_from_box(img.size[0], img.size[1], plan.box, yaw_proxy=plan.yaw_proxy, eye_dist=plan.eye_dist,
                            ellipse=fi.ELLIPSE_TRAIN)
    ctrl = np.asarray(fi.build_control(img, plan), np.int16)
    with Image.open(os.path.join(SAMPLES_CTRL, f"prod_{sid}.png")) as ref_im:
        ref = np.asarray(ref_im.convert("RGB"), np.int16)
    assert np.array_equal(ctrl, ref)


def test_center_gate_threshold_matches_measured_distribution():
    """0.25 근거: 채택 6컷 0.065~0.145 · 거부 9회 0.164~0.235(확장 타원의 위쪽 편향)."""
    assert fi.GATE_CENTER_FRAC == 0.25
    plan = fi.plan_from_box(848, 1264, (355.0, 252.0, 135.0, 180.0), yaw_proxy=0.05)
    img = Image.new("RGB", (848, 1264), (90, 90, 90))
    bx, by, bw, bh = plan.box

    for frac, expect in ((0.14, "ok"), (0.20, "ok"), (0.24, "ok"), (0.30, "center_off")):
        det = fi.FaceDetection(box=(bx, by + frac * bh, bw, bh), yaw_proxy=0.05, eye_dist=60.0, score=0.9)
        object.__setattr__(det, "box", (bx, by + frac * bh, bw, bh))
        import unittest.mock as um

        with um.patch.object(fi, "detect_face", return_value=det):
            assert fi.evaluate_gate(plan, img).reason == expect, frac


def test_retry_stops_after_three_same_reason_failures():
    """같은 사유 3연속이면 남은 시드를 포기한다(c9_1: center_off 6시드 630초 낭비)."""
    assert fi.GATE_SAME_REASON_STOP == 3
    orig, plan = _synthetic()
    buf = __import__("io").BytesIO()
    orig.save(buf, "PNG")
    data = buf.getvalue()
    calls = []

    class Flat:
        def render(self, control, prompt, seed):
            calls.append(seed)
            return Image.new("RGB", (fi.CROP, fi.CROP), (100, 100, 100))

    import unittest.mock as um

    with um.patch.object(fi, "plan_from_image", return_value=plan), \
         um.patch.object(fi, "prepare_image", return_value=(orig, plan, {"skipped_reason": None, "pose_risk": False})), \
         um.patch.object(fi, "evaluate_gate", return_value=fi.GateResult(False, "center_off", 100.0, 0.4, 0.05)):
        res = fi.run_face_pass(data, Flat(), seeds=(42, 43, 44, 45, 46, 47))
    assert not res.applied
    assert len(calls) == 3, calls                      # 6시드가 아니라 3에서 멈춘다
    assert res.meta["stopped_early"] is True
    assert res.meta["reason"] == "gate_failed:center_offx3"
