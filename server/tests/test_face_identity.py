"""agents/face_identity — GPU 없이 기하·프롬프트·합성·게이트·배선 검증.

픽스처: prod(Gemini) 착용컷 6장 = FACE_IDENTITY_FIXTURES(기본 ~/Downloads/lora_runs/prod_inputs).
인물 사진이라 레포 밖에 둔다 — 없으면 그 테스트만 skip. YuNet weights(gitignore) 없어도 skip.
build_v4c.py 가 같은 6장으로 만든 학습 표본 control(v4c_ckpt/samples_ctrl)과 픽셀 대조한다(있으면).
기하·합성·프롬프트·배선은 weights 없이도 돈다(plan_from_box 로 계획을 직접 만든다).
"""

import asyncio
import os
from types import SimpleNamespace

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


def test_composite_accepts_non_1024_generation():
    orig, plan = _synthetic()
    out = fi.composite(orig, Image.new("RGB", (512, 512), (10, 200, 10)), plan)
    assert out.size == orig.size


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


def test_registry_entry_parsing():
    assert fi.face_identity_from_registry_entry(None) is None
    assert fi.face_identity_from_registry_entry({"gender": "men"}) is None
    assert fi.face_identity_from_registry_entry({"faceIdentity": {}}) is None
    assert fi.face_identity_from_registry_entry({"faceIdentity": {"loraPath": "  "}}) is None
    spec = fi.face_identity_from_registry_entry({"faceIdentity": {"loraPath": "ohwx_man_v5.safetensors"}})
    assert spec == fi.FaceIdentitySpec("ohwx_man_v5.safetensors", "ohwx man")
    spec = fi.face_identity_from_registry_entry({"faceIdentity": {"loraPath": "/abs/x.safetensors", "token": "sks man"}})
    assert spec.token == "sks man"
    assert fi.resolve_lora_file(spec, "/lora") == "/abs/x.safetensors"
    rel = fi.FaceIdentitySpec("v5.safetensors")
    assert fi.resolve_lora_file(rel, "/lora") == "/lora/v5.safetensors"
    assert fi.resolve_lora_file(rel, "/lora/single.safetensors") == "/lora/single.safetensors"


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
_REGISTRY = {"mX": {"gender": "men", "faceIdentity": {"loraPath": "ohwx_man_v5.safetensors"}},
             "mY": {"gender": "men"}}


def _forbid_face_pass(monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("face pass must not run")

    monkeypatch.setattr(cg.face_identity, "apply_face_pass", boom)


def test_generate_unchanged_when_face_identity_disabled(monkeypatch):
    monkeypatch.setattr(cg, "load_virtual_model_registry", lambda: _REGISTRY)
    _forbid_face_pass(monkeypatch)
    settings = make_settings(gemini_api_key="x")
    assert settings.face_identity_enabled is False
    assert asyncio.run(cg.generate(settings, _FakeGemini(), _SPEC, _PRODUCT, [])) == (b"FULL", "image/png")


def test_generate_runs_face_pass_only_with_flag_and_registry_lora(monkeypatch):
    monkeypatch.setattr(cg, "load_virtual_model_registry", lambda: _REGISTRY)
    seen = []

    async def fake_pass(settings, image, mime, spec, *, expression=None):
        seen.append((image, mime, spec, expression))
        return b"FACE", "image/png"

    monkeypatch.setattr(cg.face_identity, "apply_face_pass", fake_pass)
    settings = make_settings(gemini_api_key="x", face_identity_enabled=True)
    assert asyncio.run(cg.generate(settings, _FakeGemini(), _SPEC, _PRODUCT, [])) == (b"FACE", "image/png")
    assert seen == [(b"FULL", "image/png", fi.FaceIdentitySpec("ohwx_man_v5.safetensors", "ohwx man"), None)]

    seen.clear()
    # 레지스트리에 faceIdentity 가 없는 모델 → 그대로
    assert asyncio.run(cg.generate(settings, _FakeGemini(), {**_SPEC, "modelId": "mY"}, _PRODUCT, [])) == (b"FULL", "image/png")
    # 얼굴을 가리는 컷 → 그대로
    assert asyncio.run(cg.generate(settings, _FakeGemini(), {**_SPEC, "faceExposure": "hide"}, _PRODUCT, [])) == (b"FULL", "image/png")
    # 뒷모습 → 그대로
    assert asyncio.run(cg.generate(settings, _FakeGemini(), {**_SPEC, "direction": "back"}, _PRODUCT, [])) == (b"FULL", "image/png")
    # 상품컷 → 그대로
    assert asyncio.run(cg.generate(settings, _FakeGemini(), {"cutType": "product", "modelId": "mX"}, _PRODUCT, [])) == (b"FULL", "image/png")
    # modelId 없음 → 그대로
    assert asyncio.run(cg.generate(settings, _FakeGemini(), {k: v for k, v in _SPEC.items() if k != "modelId"}, _PRODUCT, [])) == (b"FULL", "image/png")
    assert seen == []


def test_generate_bottom_medium_skips_face_pass_but_still_crops(monkeypatch):
    # 하의 medium 프레이밍은 머리가 프레임에 없다(_face_fits) → 얼굴 패스 생략, 포즈 크롭은 그대로.
    monkeypatch.setattr(cg, "load_virtual_model_registry", lambda: _REGISTRY)
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
    ))
    assert res == (b"CROPPED", "image/png") and crops == [b"FULL"]


def test_generate_bottom_full_shot_gets_face_pass(monkeypatch):
    monkeypatch.setattr(cg, "load_virtual_model_registry", lambda: _REGISTRY)

    async def fake_pass(settings, image, mime, spec, *, expression=None):
        return b"FACE", "image/png"

    monkeypatch.setattr(cg.face_identity, "apply_face_pass", fake_pass)
    settings = make_settings(gemini_api_key="x", face_identity_enabled=True)
    res = asyncio.run(cg.generate(
        settings, _FakeGemini(), {**_SPEC, "shot": "full"}, {**_PRODUCT, "clothingType": "bottom"}, [],
    ))
    assert res == (b"FACE", "image/png")

# ---------------------------------------------------------------- 확정 설정(2026-09-08)


def test_expanded_ellipse_is_the_default_and_matches_the_formula():
    # 기본은 E2(2026-09-09 타원 3단계 시험 채택). 이전 확장본은 ELLIPSE_PREV, 학습용은 ELLIPSE_TRAIN.
    assert fi.ELLIPSE == (-0.75, -1.45, 1.75, 1.30)
    assert fi.ELLIPSE_PREV == (-0.55, -1.10, 1.55, 1.25)
    assert fi.ELLIPSE_TRAIN == (-0.30, -0.60, 1.30, 1.15)
    plan = fi.plan_from_box(848, 1264, (355.0, 252.0, 135.0, 180.0))
    fb = plan.face_box_crop
    assert list(plan.ellipse) == pytest.approx([fb[0] - 0.75 * fb[2], fb[1] - 1.45 * fb[3], fb[0] + 1.75 * fb[2], fb[1] + 1.30 * fb[3]])
    prev = fi.plan_from_box(848, 1264, (355.0, 252.0, 135.0, 180.0), ellipse=fi.ELLIPSE_PREV)
    assert list(prev.ellipse) == pytest.approx([fb[0] - 0.55 * fb[2], fb[1] - 1.10 * fb[3], fb[0] + 1.55 * fb[2], fb[1] + 1.25 * fb[3]])
    # 면적 순서: 학습 < 이전 확장 < 현재 E2 (시험에서 잰 45.6→56.5% 관계와 같은 방향)
    def area(pl):
        return int((np.asarray(fi.ellipse_mask(pl)) > 127).sum())
    train = fi.plan_from_box(848, 1264, (355.0, 252.0, 135.0, 180.0), ellipse=fi.ELLIPSE_TRAIN)
    assert area(train) < area(prev) < area(plan)
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
