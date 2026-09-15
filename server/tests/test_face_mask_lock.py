"""마스크 밖 latent 고정 — 붙이기 경계(네모 조각)를 없앤다.

지금까지 파드는 1024² 크롭 **전체**를 새로 그리고 서버가 타원 알파로 되붙였다. 그림자 있는
배경에서 목 옆에 직사각형 조각이 남고 hz_d7 에서 그림자가 얼룩졌다 — 붙이기 경계다.

2026-09-15 실험(v7 · seed 42 · 5컷, A100): 디노이즈 매 스텝마다 머리 마스크 밖 latent 를 원본으로
되돌리면 **5/5 컷에서 조각이 사라졌고** 신원은 ±0.02 안이었다
  sc_1 0.755→0.744 · sc_2 0.674→0.685 · sc_5 0.693→0.700 · hz_d7 0.726→0.705 · prod_gpt 0.757→0.754
머리 밖 |생성 − 크롭| 8.2~24.9 → 1.1~3.3, 렌더 시간 동일(~62s).

여기서 잠그는 계약 넷:
  · 생성 마스크는 **사진 안쪽 크롭 변에서 72px 을 뺀다**(그 띠가 잠겨야 경계 이음매가 없다).
    사진 가장자리에 붙은 변은 빼지 않는다 — 이을 원본이 없고 빼면 머리만 잘린다.
  · 되붙이기 알파는 다시 그린 자리 위에서 1, 사진 안쪽 크롭 경계에서 0.
  · 알파 0 인 픽셀은 원본과 **바이트 동일**.
  · 파드가 못 하면(healthz 에 mask_lock 없음) 옛 경로를 탄다 — 옛 파드는 모르는 필드를 무시하고
    전체를 다시 그리므로, 그 결과에 잠금 알파를 쓰면 경계가 깨진다.
"""

import numpy as np
import pytest
from PIL import Image

from app.agents import face_identity as fi
from app.agents import face_recipe


def _plan(box=(250.0, 300.0, 120.0, 160.0), size=(600, 800)):
    return fi.plan_from_box(size[0], size[1], box, yaw_proxy=0.05, eye_dist=50.0)


def _scene(size=(600, 800)):
    rng = np.random.default_rng(3)
    return Image.fromarray(rng.integers(0, 256, size=(size[1], size[0], 3), dtype=np.uint8))


# ── 생성 마스크 ─────────────────────────────────────────────────────────────
def test_the_generation_mask_locks_the_interior_crop_edges():
    plan = _plan()
    assert fi.at_photo_edge(plan) == (False, False, False, False)
    mask = np.asarray(fi.generation_mask(plan))
    px = fi.MASK_LOCK_EDGE_PX
    for name, band in (("left", mask[:, :px]), ("right", mask[:, -px:]),
                       ("top", mask[:px, :]), ("bottom", mask[-px:, :])):
        assert band.max() == 0, f"{name} 변이 안 잠겼다 — 그 경계가 이음매가 된다"
    assert mask.max() == 255, "생성 영역이 통째로 사라졌다"


def test_a_side_at_the_photo_edge_keeps_its_band():
    """사진 가장자리에 붙은 변은 빼지 않는다 — 그 너머에 원본이 없다(at_photo_edge)."""
    plan = fi.plan_from_box(600, 800, (2.0, 2.0, 120.0, 160.0), yaw_proxy=0.0, eye_dist=50.0)
    left, top, _right, _bottom = fi.at_photo_edge(plan)
    assert left and top, "이 기하가 사진 가장자리에 안 붙었다 — 픽스처를 고쳐야 한다"
    binary = np.asarray(fi.binary_mask(plan))
    mask = np.asarray(fi.generation_mask(plan))
    px = fi.MASK_LOCK_EDGE_PX
    # 붙은 변(좌·상)은 원래 마스크 그대로
    assert np.array_equal(mask[:, :px], binary[:, :px])
    assert np.array_equal(mask[:px, :], binary[:px, :])
    # 안쪽 변(우·하)은 잠긴다
    assert mask[:, -px:].max() == 0 and mask[-px:, :].max() == 0


def test_the_mask_is_a_subset_of_the_binary_mask():
    """잠금은 **빼기만** 한다 — 없던 생성 영역을 만들면 학습 조건과 달라진다."""
    plan = _plan()
    binary = np.asarray(fi.binary_mask(plan)) > 0
    mask = np.asarray(fi.generation_mask(plan)) > 0
    assert not (mask & ~binary).any()


# ── 되붙이기 알파 ───────────────────────────────────────────────────────────
def test_the_alpha_is_one_where_the_pod_actually_redrew():
    plan = _plan()
    gen_mask = fi.generation_mask(plan)
    alpha = fi.mask_lock_alpha(plan, gen_mask)
    lat = fi.CROP // fi.MASK_LOCK_CELL_PX
    cells = np.asarray(gen_mask.resize((lat, lat), Image.BOX), np.float32) > 0
    unlocked = np.asarray(Image.fromarray((cells * 255).astype(np.uint8), "L")
                          .resize((fi.CROP, fi.CROP), Image.NEAREST)) > 0
    assert unlocked.any()
    assert alpha[unlocked].min() > 0.999, "다시 그린 자리가 완전히 안 덮인다"


def test_the_alpha_is_zero_at_the_interior_crop_border():
    plan = _plan()
    alpha = fi.mask_lock_alpha(plan, fi.generation_mask(plan))
    assert alpha[0, :].max() == 0 and alpha[-1, :].max() == 0
    assert alpha[:, 0].max() == 0 and alpha[:, -1].max() == 0


def test_the_alpha_never_leaves_the_unit_range():
    plan = _plan()
    alpha = fi.mask_lock_alpha(plan, fi.generation_mask(plan))
    assert alpha.min() >= 0.0 and alpha.max() <= 1.0


# ── 합성 ────────────────────────────────────────────────────────────────────
def test_pixels_with_zero_alpha_are_byte_identical():
    """알파 0 자리는 원본 그대로여야 한다 — 옷·배경을 우리가 다시 그리지 않는다."""
    orig, plan = _scene(), _plan()
    crop = fi.crop_1024(orig, plan)
    gen = Image.new("RGB", (fi.CROP, fi.CROP), (255, 0, 255))     # 눈에 띄는 색
    gen_mask = fi.generation_mask(plan)
    out, meta = fi.composite_locked(orig, gen, plan, crop=crop, gen_mask=gen_mask)

    alpha = fi.mask_lock_alpha(plan, gen_mask)
    x0, y0, side = plan.crop
    import cv2

    a_small = cv2.resize(alpha, (side, side), interpolation=cv2.INTER_AREA)
    full = np.zeros(np.asarray(orig.convert("L")).shape, np.float32)
    full[y0:y0 + side, x0:x0 + side] = a_small
    untouched = full <= 0.0
    assert untouched.any()
    assert np.array_equal(np.asarray(out.convert("RGB"))[untouched],
                          np.asarray(orig.convert("RGB"))[untouched])
    assert meta["mask_lock"] is True
    # 링 색보정은 쓰지 않는다 — 머리 밖이 이미 원본이라 맞출 차이가 없다(실험 없이 얹지 않는다).
    assert meta["color_shift"] is None


def test_the_generated_face_actually_lands():
    orig, plan = _scene(), _plan()
    crop = fi.crop_1024(orig, plan)
    gen = Image.new("RGB", (fi.CROP, fi.CROP), (255, 0, 255))
    out, _ = fi.composite_locked(orig, gen, plan, crop=crop, gen_mask=fi.generation_mask(plan))
    cx, cy = (int(v) for v in plan.anchor_center)
    assert not np.array_equal(np.asarray(out)[cy, cx], np.asarray(orig)[cy, cx])


# ── 능력 판정 ───────────────────────────────────────────────────────────────
class _Pod:
    """HttpFaceBackend 대역 — healthz 응답만 흉내 낸다."""

    def __init__(self, healthz):
        self._healthz = healthz
        self.sent = []

    def render(self, control, prompt, seed, base=None, gen_mask=None):
        self.sent.append({"base": base, "gen_mask": gen_mask})
        return control.copy()

    def supports_mask_lock(self):
        return bool(self._healthz.get("mask_lock"))


def test_an_old_pod_without_the_capability_takes_the_old_path():
    """★ 옛 파드는 모르는 필드를 조용히 무시하고 전체를 다시 그린다 — 그 결과에 잠금 알파를
    쓰면 머리 밖이 생성본인 채로 남아 경계가 깨진다. 그래서 능력이 없으면 옛 경로다."""
    assert fi.backend_mask_lock(_Pod({"ok": True, "code_version": "x"})) is False
    assert fi.backend_mask_lock(_Pod({"mask_lock": True})) is True


def test_the_switch_can_turn_it_off():
    assert fi.backend_mask_lock(_Pod({"mask_lock": True}), enabled=False) is False


def test_a_local_backend_is_judged_by_its_signature():
    assert fi.backend_mask_lock(fi.NullBackend()) is True

    class _Legacy:
        def render(self, control, prompt, seed):
            return control

    assert fi.backend_mask_lock(_Legacy()) is False


def test_the_setting_defaults_to_on():
    from conftest import make_settings

    settings = make_settings(gemini_api_key="x", r2_bucket="b")
    assert settings.face_mask_lock is True


# ── 레시피 해시 ─────────────────────────────────────────────────────────────
def test_the_recipe_hash_separates_locked_cuts():
    """잠근 컷은 아예 다른 산물이다 — 픽셀로 못 되짚으니 해시가 갈라져야 한다."""
    locked = face_recipe.recipe_id(face_recipe.recipe_fields(mask_lock=True))
    plain = face_recipe.recipe_id(face_recipe.recipe_fields(mask_lock=False))
    assert locked != plain
    fields = face_recipe.recipe_fields(mask_lock=True)
    for key, value in (("mask_lock_edge_px", fi.MASK_LOCK_EDGE_PX),
                       ("mask_lock_cell_px", fi.MASK_LOCK_CELL_PX),
                       ("mask_lock_dilate_px", fi.MASK_LOCK_DILATE_PX),
                       ("mask_lock_blur_sigma", fi.MASK_LOCK_BLUR_SIGMA),
                       ("mask_lock_fade_px", fi.MASK_LOCK_FADE_PX)):
        assert fields[key] == value


# ── 파드 서비스 계약 ────────────────────────────────────────────────────────
def _b64(img):
    import base64
    from io import BytesIO

    buf = BytesIO()
    img.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


@pytest.fixture
def service():
    import face_render_service as svc

    return svc


def test_the_service_advertises_the_capability(service):
    assert service.healthz()["mask_lock"] is True


def test_half_the_pair_is_a_400(service):
    from fastapi import HTTPException

    req = service.RenderRequest(control_png="x", prompt="p",
                                base_png=_b64(Image.new("RGB", (8, 8))))
    with pytest.raises(HTTPException) as err:
        service._mask_lock_inputs(req, (8, 8))
    assert err.value.status_code == 400 and "together" in str(err.value.detail)


def test_an_undecodable_png_is_a_400(service):
    from fastapi import HTTPException

    req = service.RenderRequest(control_png="x", prompt="p",
                                base_png="not-base64!!", gen_mask_png=_b64(Image.new("L", (8, 8))))
    with pytest.raises(HTTPException) as err:
        service._mask_lock_inputs(req, (8, 8))
    assert err.value.status_code == 400


def test_a_size_mismatch_is_a_400(service):
    from fastapi import HTTPException

    req = service.RenderRequest(control_png="x", prompt="p",
                                base_png=_b64(Image.new("RGB", (16, 16))),
                                gen_mask_png=_b64(Image.new("L", (8, 8))))
    with pytest.raises(HTTPException) as err:
        service._mask_lock_inputs(req, (8, 8))
    assert err.value.status_code == 400 and "size" in str(err.value.detail)


def test_no_fields_means_no_lock(service):
    req = service.RenderRequest(control_png="x", prompt="p")
    assert service._mask_lock_inputs(req, (8, 8)) == (None, None)


def test_a_valid_pair_comes_back_in_the_right_modes(service):
    req = service.RenderRequest(control_png="x", prompt="p",
                                base_png=_b64(Image.new("RGB", (8, 8))),
                                gen_mask_png=_b64(Image.new("L", (8, 8))))
    base, gen_mask = service._mask_lock_inputs(req, (8, 8))
    assert base.mode == "RGB" and gen_mask.mode == "L"


def test_the_pod_module_keeps_the_no_torch_guard():
    """torch·diffusers 는 render 안에서만 import 한다 — API 이미지에는 없다."""
    import pathlib

    from app.agents import face_identity_qwen

    text = pathlib.Path(face_identity_qwen.__file__).read_text(encoding="utf-8")
    head = text[: text.index("def load_base_pipeline")]
    assert "import torch" not in head and "import diffusers" not in head
    # _mask_lock 도 함수 안에서만 import
    lock = text[text.index("def _mask_lock("):]
    assert lock.index("import torch") < lock.index("dtype = pipe.transformer.dtype")
