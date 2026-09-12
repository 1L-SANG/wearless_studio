"""얼굴 마감 — 크롭만 ESRGAN · 사진 가장자리에서는 페이드 안 함 · 레시피 해시.

세 가지가 한 묶음인 이유: 전부 "생성 얼굴을 원본에 어떻게 되붙이나"를 건드리고, 셋 다 옷 픽셀을
바꾸면 안 된다는 같은 계약 아래 있다.

근거 실험(2026-09-11, 스튜디오 3컷 × v6-1500, ~/Downloads/studio_cut_out/ab_recipe · ab_hair):
  · 화면 전체 ESRGAN(G) 은 옷 영역 |Δ| 평균 3.9~6.4(p99 43) — 상품 픽셀을 바꾼다. 쓰지 않는다.
  · 크롭만 ESRGAN(H) 은 얼굴 고주파를 올리고(컷1 1.52→5.76) 옷은 타원 밖 alpha=0 이라 그대로다.
  · 상단 48px 페이드 띠는 세 컷 모두 정수리 머리 위에 있었다.
GPU 는 여기서 쓰지 않는다 — 확대기는 가짜 함수로, 렌더는 NullBackend 로 대신한다.
"""

import numpy as np
import pytest
from PIL import Image

from app.agents import face_identity as fi
from app.agents import face_recipe


def _plan(w=1024, h=1536, box=(360.0, 300.0, 180.0, 240.0)):
    return fi.plan_from_box(w, h, box, yaw_proxy=0.05, eye_dist=60.0, expression="neutral")


def _photo(w=1024, h=1536, seed=7):
    rng = np.random.default_rng(seed)
    return Image.fromarray(rng.integers(0, 255, (h, w, 3), dtype=np.uint8))


# ── 1) 얼굴 크롭만 확대 ──────────────────────────────────────────────────────
def test_no_upscaler_is_byte_identical_to_before():
    """확대기를 안 주면 지금까지와 **같은 픽셀**이어야 한다 — 기본 경로 회귀 고정."""
    img, plan = _photo(), _plan()
    x0, y0, side = plan.crop
    expected = img.crop((x0, y0, x0 + side, y0 + side)).resize((fi.CROP, fi.CROP), Image.LANCZOS)
    assert fi.crop_1024(img, plan).tobytes() == expected.tobytes()


def test_upscaler_is_called_once_with_the_ceil_scale():
    calls = []

    def up(image, k):
        calls.append((image.size, k))
        return image.resize((image.width * k, image.height * k), Image.NEAREST)

    plan = _plan()                       # side = 3×180 = 540 → ceil(1024/540) = 2
    out, meta = fi.crop_1024_with_meta(_photo(), plan, upscaler=up)
    assert out.size == (fi.CROP, fi.CROP)
    assert calls == [((540, 540), 2)]
    assert meta == {"applied": True, "method": "esrgan", "k": 2, "side": 540}


def test_scale_is_capped_at_the_models_native_four():
    calls = []
    plan = _plan(box=(360.0, 300.0, 60.0, 80.0))      # side 180 → ceil(1024/180) = 6
    fi.crop_1024(_photo(), plan, upscaler=lambda im, k: calls.append(k) or im)
    assert calls == [fi.CROP_UPSCALE_MAX]


def test_a_crop_bigger_than_1024_is_not_upscaled():
    """축소 크롭은 확대기를 부르지 않는다 — 부를 이유가 없고 느리기만 하다."""
    calls = []
    plan = _plan(w=2048, h=3072, box=(700.0, 600.0, 400.0, 520.0))    # side 1200 > 1024
    _, meta = fi.crop_1024_with_meta(_photo(2048, 3072), plan,
                                     upscaler=lambda im, k: calls.append(k) or im)
    assert calls == [] and meta["applied"] is False and meta["method"] == "lanczos"


@pytest.mark.parametrize("upscaler,expect_error", [
    (lambda im, k: None, False),                              # 못 하겠다고 None
    (lambda im, k: (_ for _ in ()).throw(RuntimeError("x")), True),   # 터짐
])
def test_a_failing_upscaler_falls_back_to_lanczos(upscaler, expect_error):
    img, plan = _photo(), _plan()
    out, meta = fi.crop_1024_with_meta(img, plan, upscaler=upscaler)
    assert out.tobytes() == fi.crop_1024(img, plan).tobytes()
    assert meta["applied"] is False and meta["method"] == "lanczos"
    assert ("error" in meta) is expect_error


def test_clothing_pixels_stay_byte_identical_even_with_an_upscaled_crop():
    """타원 밖 alpha 는 0 이다 — 크롭을 어떻게 키우든 옷·배경 픽셀은 원본과 같아야 한다.

    화면 전체 확대(G)를 버린 이유가 이것이다(그쪽은 옷 |Δ| 평균 3.9~6.4).
    """
    img, plan = _photo(), _plan()
    sharp = fi.crop_1024(img, plan, upscaler=lambda im, k: im.resize(
        (im.width * k, im.height * k), Image.NEAREST))
    generated = Image.new("RGB", (fi.CROP, fi.CROP), (255, 0, 200))
    out, _ = fi.composite_with_meta(img, generated, plan, crop=sharp)

    before, after = np.asarray(img, np.uint8), np.asarray(out, np.uint8)
    untouched = fi.paste_alpha(plan, fi.FEATHER_FRAC) <= 0
    assert untouched.any()
    assert np.array_equal(before[untouched], after[untouched])
    # 타원 안은 실제로 바뀌었다(위 단언이 "아무것도 안 했다"로 통과하지 않게)
    assert not np.array_equal(before[~untouched], after[~untouched])


def test_backend_upscaler_hook_is_optional_and_switchable():
    class Old:                       # 옛 백엔드 — upscale 메서드가 없다
        def render(self, control, prompt, seed): return control

    class New(Old):
        def upscale(self, image, scale): return image

    assert fi.backend_upscaler(Old()) is None
    assert fi.backend_upscaler(New()) is not None
    assert fi.backend_upscaler(New(), False) is None      # 설정으로 끈 경우


def test_run_face_pass_uses_the_backend_upscaler_and_records_it():
    class Backend:
        def __init__(self): self.scales = []

        def render(self, control, prompt, seed): return control.copy()

        def upscale(self, image, scale):
            self.scales.append(scale)
            return image.resize((image.width * scale, image.height * scale), Image.NEAREST)

    from io import BytesIO
    buf = BytesIO()
    _photo().save(buf, "PNG")
    backend = Backend()
    with _fake_plan():
        res = fi.run_face_pass(buf.getvalue(), backend, seeds=(42,))
    assert backend.scales == [2]
    assert res.meta["crop_upscale"] == {"applied": True, "method": "esrgan", "k": 2, "side": 540}

    backend2 = Backend()
    with _fake_plan():
        res2 = fi.run_face_pass(buf.getvalue(), backend2, seeds=(42,), crop_upscale=False)
    assert backend2.scales == []
    assert res2.meta["crop_upscale"]["applied"] is False


def _fake_plan():
    """YuNet weights 없이도 돌게 — 검출만 고정 계획으로 대체한다."""
    from unittest import mock
    plan = _plan()
    return mock.patch.object(fi, "prepare_image",
                             lambda image, model_dir=None: (image.convert("RGB"), plan,
                                                            {"skipped_reason": None, "pose_risk": False}))


# ── 2) 사진 가장자리에서는 페이드하지 않는다 ────────────────────────────────
def test_interior_crop_still_fades_exactly_as_before():
    """크롭 상단이 사진 안쪽이면 동작이 한 줄도 바뀌지 않는다(프로덕션 컷 대부분이 여기다)."""
    plan = _plan(box=(360.0, 300.0, 180.0, 240.0))       # y0 = 300+120-270 = 150 > 0
    assert plan.crop[1] > 0
    assert fi.at_photo_edge(plan) == (False, False, False, False)
    assert fi.fade_sides(plan) == fi.crossing_sides(plan)
    old = (np.asarray(fi.feather_mask(plan, fi.FEATHER_FRAC), np.float32) / 255.0
           * fi._edge_fade(fi.EDGE_FADE_PX, fi.crossing_sides(plan)))
    assert np.array_equal(fi.composite_alpha(plan, fi.FEATHER_FRAC), old)


def test_a_crop_flush_with_the_photo_top_does_not_fade_that_side():
    """정수리가 사진 맨 위에 닿은 컷 — 이을 원본이 없는 변을 페이드하면 원본 머리가 되살아난다."""
    plan = _plan(box=(360.0, 40.0, 180.0, 240.0))         # 중심이 위 → y0 이 0 으로 clamp
    assert plan.crop[1] == 0
    assert fi.crossing_sides(plan)[1] is True             # 타원은 여전히 크롭 위로 나간다
    assert fi.at_photo_edge(plan)[1] is True
    assert fi.fade_sides(plan)[1] is False

    alpha = fi.composite_alpha(plan, fi.FEATHER_FRAC)
    cx = int((plan.ellipse[0] + plan.ellipse[2]) / 2)
    assert alpha[0, cx] == pytest.approx(1.0, abs=1e-6)   # 맨 윗줄도 생성 쪽이 온전히 들어간다
    # 옛 규칙이었다면 0 이었다
    old = fi._edge_fade(fi.EDGE_FADE_PX, fi.crossing_sides(plan))
    assert old[0, cx] == 0.0


def test_only_the_edge_touching_side_is_spared():
    """좌우·하단이 사진 안쪽이면 그쪽 페이드는 그대로다 — 규칙은 변 단위다."""
    plan = _plan(w=600, h=1536, box=(10.0, 40.0, 180.0, 240.0))     # x0·y0 둘 다 0 으로 clamp
    assert plan.crop[0] == 0 and plan.crop[1] == 0
    left, top, right, bottom = fi.at_photo_edge(plan)
    assert left and top and not right
    for i, (cross, fade) in enumerate(zip(fi.crossing_sides(plan), fi.fade_sides(plan))):
        expect = cross and not fi.at_photo_edge(plan)[i]
        assert fade is expect


# ── 3) 레시피 해시 ──────────────────────────────────────────────────────────
def test_recipe_id_is_stable_and_covers_the_constants_we_care_about():
    fields = face_recipe.recipe_fields(lora_sha256="a" * 64)
    assert fields["ellipse"] == list(fi.ELLIPSE)
    assert fields["steps"] == fi.RENDER_STEPS and fields["guidance"] == fi.RENDER_GUIDANCE
    assert fields["seeds"] == list(fi.DEFAULT_SEEDS)
    assert fields["edge_fade_px"] == fi.EDGE_FADE_PX
    assert fields["edge_fade_rule"] == "crossing_and_interior"
    assert fields["lora_sha12"] == "a" * 12          # 전체 sha 는 싣지 않는다
    assert face_recipe.recipe_id(fields) == face_recipe.recipe_id(dict(reversed(list(fields.items()))))
    assert len(face_recipe.recipe_id(fields)) == 12


@pytest.mark.parametrize("field,value", [
    ("ellipse", [-0.75, -1.45, 1.75, 1.30]),
    ("steps", 30),
    ("edge_fade_px", 0),
    ("edge_fade_rule", "crossing"),
    ("lora_sha12", "b" * 12),
    ("upscale_scope", face_recipe.UPSCALE_SCOPE_OFF),
])
def test_changing_any_recipe_constant_changes_the_hash(field, value):
    base = face_recipe.recipe_fields(lora_sha256="a" * 64)
    changed = dict(base, **{field: value})
    assert changed != base
    assert face_recipe.recipe_id(changed) != face_recipe.recipe_id(base)


def test_upscale_scope_is_the_policy_not_the_runtime():
    """'얼굴 크롭만' 과 '안 함' 은 다른 레시피다. 화면 전체 확대는 아예 값이 없다."""
    assert face_recipe.UPSCALE_SCOPE_FACE_CROP == "face_crop"
    assert face_recipe.UPSCALE_SCOPE_OFF == "off"
    scopes = {face_recipe.recipe_fields(upscale_scope=s)["upscale_scope"]
              for s in (face_recipe.UPSCALE_SCOPE_FACE_CROP, face_recipe.UPSCALE_SCOPE_OFF)}
    assert scopes == {"face_crop", "off"}
