"""얼굴 패스 "6번" — 마스크 밖 latent 고정 렌더 + 벽·그림자·옷 되돌리기.

운영은 파드가 1024² 크롭 **전체**를 새로 그리고 서버가 타원 알파로 되붙인다. 그림자 있는 벽에서
목 옆 네모 조각과 반원 얼룩이 생긴다 — 붙이기 경계다.

2026-09-15 키트 10컷(v7 LoRA · seed 42)에서 사용자가 "6번"을 골랐다.
  · 운영 방식: 10컷 **전부** 조각 · 6번: 조각 없음 · 머리 잘림 없음 · 목 경계 깨끗
  · 머리 주변 벽 밝기 변화 0.0~0.1, 질감 비율 0.99~1.06
  · 옷 목둘레 변경 픽셀 prod 5.2%→1.7%, hz_d8 1.2%→0.3%
  · 신원(크롭 1024) 0.70~0.78 — 운영 방식과 같은 수준

정본은 ~/Downloads/comfy_swap_test/reference_code/mask_lock_6_reference.py 다(저장된 6번 결과와
바이트 동일로 검증됨). 이 파일은 **그 로직이 그대로 옮겨졌는지**를 잠근다 — 상수는 실측값이라
바꾸려면 키트를 다시 돌려야 한다.
"""

import importlib.util
import os
import pathlib

import cv2
import numpy as np
import pytest
from PIL import Image, ImageDraw

from app.agents import face_identity as fi
from app.agents import face_mask_lock as fml
from app.agents import face_recipe

REFERENCE = pathlib.Path(os.path.expanduser(os.getenv(
    "FACE_MASK_LOCK_REFERENCE",
    "~/Downloads/comfy_swap_test/reference_code/mask_lock_6_reference.py")))

#: 장면 색 — 벽/피부/머리/옷이 분리되게 고른다(서버 판정이 색으로 돈다).
WALL = (206, 206, 208)
SKIN = (214, 172, 148)
HAIR = (42, 34, 30)
GARMENT = (150, 150, 152)


def _plan(box=(250.0, 300.0, 120.0, 160.0), size=(600, 800)):
    return fi.plan_from_box(size[0], size[1], box, yaw_proxy=0.05, eye_dist=50.0)


def _scene(size=(600, 800), *, hair=True):
    """벽 + 머리(피부 얼굴 + 머리카락) + 턱 아래 옷. 노이즈를 약간 둔다."""
    rng = np.random.default_rng(11)
    arr = np.full((size[1], size[0], 3), WALL, np.float32) + rng.normal(0, 2.0, (size[1], size[0], 3))
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    draw = ImageDraw.Draw(img)
    bx, by, bw, bh = 250.0, 300.0, 120.0, 160.0
    if hair:
        draw.ellipse((bx - 0.3 * bw, by - 0.7 * bh, bx + 1.3 * bw, by + 0.45 * bh), fill=HAIR)
    draw.ellipse((bx - 0.05 * bw, by - 0.1 * bh, bx + 1.05 * bw, by + bh), fill=SKIN)
    draw.rectangle((bx - 0.3 * bw, by + bh, bx + 1.3 * bw, by + 1.6 * bh), fill=SKIN)   # 목
    draw.rectangle((bx - 1.2 * bw, by + 1.6 * bh, bx + 2.2 * bw, size[1]), fill=GARMENT)
    return img


def _crop_arr(orig, plan):
    return np.asarray(fi.crop_1024(orig, plan).convert("RGB"), np.float32)


# ── gen_mask ────────────────────────────────────────────────────────────────
def test_the_generation_mask_locks_the_interior_crop_edges():
    orig, plan = _scene(), _plan()
    assert fi.at_photo_edge(plan) == (False, False, False, False)
    mask = fml.gen_mask(_crop_arr(orig, plan), plan)
    px = fml.EDGE_LOCK_PX
    for name, band in (("left", mask[:, :px]), ("right", mask[:, -px:]),
                       ("top", mask[:px, :]), ("bottom", mask[-px:, :])):
        assert not band.any(), f"{name} 변이 안 잠겼다 — 그 경계가 이음매가 된다"
    assert mask.any(), "생성 영역이 통째로 사라졌다"


def test_a_side_at_the_photo_edge_keeps_its_band():
    """사진 가장자리에 붙은 변은 빼지 않는다 — 그 너머에 이을 원본이 없고, 빼면 머리만 잘린다."""
    orig = _scene()
    plan = fi.plan_from_box(600, 800, (2.0, 2.0, 120.0, 160.0), yaw_proxy=0.0, eye_dist=50.0)
    left, top, _r, _b = fi.at_photo_edge(plan)
    assert left and top, "이 기하가 사진 가장자리에 안 붙었다 — 픽스처를 고쳐야 한다"
    keep = fml.edge_lock(plan)
    px = fml.EDGE_LOCK_PX
    # 붙은 변(좌·상)은 그대로 — 안쪽 변(우·하)이 깎는 줄은 빼고 본다.
    assert keep[:-px, :px].all() and keep[:px, :-px].all()
    assert not keep[:, -px:].any() and not keep[-px:, :].any()  # 안쪽 변은 잠근다


def test_the_mask_covers_the_expected_lora_head():
    """예상 LoRA 머리(얼굴박스 ±0.35fw, −0.60fh)는 마스크 안이어야 한다 — 없으면 머리가 잘린다."""
    orig, plan = _scene(), _plan()
    mask = fml.gen_mask(_crop_arr(orig, plan), plan)
    expected = fml.expected_head(plan) & fml.control_ellipse(plan) & fml.edge_lock(plan)
    missing = expected & ~mask
    assert missing.sum() == 0, f"예상 머리 {int(missing.sum())}px 이 마스크 밖이다"


def test_the_mask_reaches_the_neck_down_to_the_collar():
    """목이 칼라 선까지 들어가야 한다 — 타원만 쓰면 칼라 위에서 잘려 꺾인다."""
    orig, plan = _scene(), _plan()
    crop = _crop_arr(orig, plan)
    mask = fml.gen_mask(crop, plan)
    neck = fml.neck_skin(crop, plan)
    assert neck.any(), "장면에 목이 안 잡혔다 — 픽스처를 고쳐야 한다"
    fx, fy, fw, fh = plan.face_box_crop
    below = np.zeros_like(mask)
    below[int(fy + fh):] = True
    # 크롭 경계 72px 은 어차피 잠긴다 — 그 밖에서 목이 얼마나 들어갔는지를 본다.
    zone = neck & below & fml.edge_lock(plan)
    assert zone.any()
    assert (mask & zone).sum() > 0.95 * zone.sum()


# ── lock_alpha ──────────────────────────────────────────────────────────────
def test_the_alpha_is_one_on_unlocked_cells_and_zero_at_the_border():
    orig, plan = _scene(), _plan()
    mask = fml.gen_mask(_crop_arr(orig, plan), plan)
    alpha = fml.lock_alpha(mask, plan)
    un = fml.unlocked_cells(mask)
    assert un.any()
    assert alpha[un].min() > 0.999, "모델이 그린 칸이 완전히 안 덮인다"
    assert alpha[0, :].max() == 0 and alpha[-1, :].max() == 0
    assert alpha[:, 0].max() == 0 and alpha[:, -1].max() == 0
    assert alpha.min() >= 0.0 and alpha.max() <= 1.0


# ── give_back_keep ──────────────────────────────────────────────────────────
def _pair(plan):
    """원본 크롭과 "렌더 결과" 크롭 — 벽을 살짝 밝히고 옷을 조금 바꾼 것."""
    orig = _scene()
    crop = _crop_arr(orig, plan)
    gen = crop.copy()
    bg = fi._backdrop_color(crop)
    wall = fi._backdropness(crop, bg) > 0.5
    gen[wall] = np.clip(gen[wall] + 2.0, 0, 255)          # 렌더가 벽을 다시 칠한다(+0.7~2.6 실측)
    return crop, gen


def test_wall_pixels_that_both_images_agree_on_are_given_back():
    plan = _plan()
    crop, gen = _pair(plan)
    keep = fml.give_back_keep(crop, gen, plan)
    bg = fi._backdrop_color(crop)
    wall = (fi._backdropness(crop, bg) > 0.9) & (fi._backdropness(gen, bg) > 0.9)
    old, _ = fml._person(crop, plan, bg, fml.OLD_PERSON_T)
    new, _ = fml._person(gen, plan, bg, fml.NEW_PERSON_T)
    person = cv2.dilate((old | new).astype(np.uint8),
                        fml._ellipse(fml.OLD_HEAD_PX + fml.PROTECT_NEW_HEAD_PX)).astype(bool)
    # 되돌리기는 _soften(침식+흐림)을 거치므로 경계 한 겹은 1 로 남는다 — 속살만 본다.
    far = cv2.erode((wall & ~person).astype(np.uint8),
                    np.ones((2 * fi.BACKDROP_ERODE_PX + 1,) * 2, np.uint8)).astype(bool)
    assert far.any()
    assert keep[far].max() < 0.01, "두 그림이 모두 벽이라는 자리가 안 돌아왔다"


def test_the_new_head_is_never_given_back():
    """새 머리·목 +15px 안은 반드시 생성본이다 — 되돌리면 얼굴이 원래 사람으로 되돌아간다."""
    plan = _plan()
    crop, gen = _pair(plan)
    keep = fml.give_back_keep(crop, gen, plan)
    protect = cv2.erode(
        (fml.head_silhouette(gen, plan) | fml.neck_skin(gen, plan)).astype(np.uint8),
        fml._ellipse(6)).astype(bool)
    assert protect.any()
    assert keep[protect].min() > 0.999


def test_the_old_persons_hair_is_never_given_back():
    """★ 턱 위 원래 사람 머리 +32px 는 되돌리지 않는다 — 없으면 옛 긴 머리 가닥이 되살아난다."""
    plan = _plan()
    crop, gen = _pair(plan)
    keep = fml.give_back_keep(crop, gen, plan)
    bg = fi._backdrop_color(crop)
    old, _ref = fml._person(crop, plan, bg, fml.OLD_PERSON_T)
    fx, fy, fw, fh = plan.face_box_crop
    above = np.zeros_like(old)
    above[:int(fy + fh)] = True
    core = cv2.erode((old & above).astype(np.uint8), fml._ellipse(6)).astype(bool)
    assert core.any()
    assert keep[core].min() > 0.999


def test_garment_below_the_chin_is_given_back():
    """턱 아래에서 두 그림 모두 옷인 자리는 원본으로 — 칼라 가장자리가 다시 그려지는 걸 막는다."""
    plan = _plan()
    crop, gen = _pair(plan)
    keep = fml.give_back_keep(crop, gen, plan)
    bg = fi._backdrop_color(crop)
    ref_o = fi._skin_reference(crop, plan)
    ref_g = fi._skin_reference(gen, plan)
    fx, fy, fw, fh = plan.face_box_crop
    below = np.zeros((fi.CROP, fi.CROP), bool)
    below[int(fy + fh):] = True
    # give_back_keep 의 (c) 조건 그대로 — 두 그림 모두 턱 아래 옷.
    both = (below
            & (fi._skinness(crop, ref_o) < 0.3) & (fi._backdropness(crop, bg) < 0.3)
            & (fi._skinness(gen, ref_g) < 0.3) & (fi._backdropness(gen, bg) < 0.3))
    garment = cv2.erode(both.astype(np.uint8),
                        np.ones((2 * fi.GARMENT_ERODE_PX + 1,) * 2, np.uint8)).astype(bool)
    assert garment.any()
    assert keep[garment].max() < 0.01


# ── composite ───────────────────────────────────────────────────────────────
def test_pixels_with_zero_alpha_are_byte_identical():
    """알파 0 자리는 원본 그대로여야 한다 — 옷·배경을 우리가 다시 그리지 않는다."""
    orig, plan = _scene(), _plan()
    crop_img = fi.crop_1024(orig, plan)
    crop = np.asarray(crop_img.convert("RGB"), np.float32)
    mask = fml.gen_mask(crop, plan)
    gen = Image.new("RGB", (fi.CROP, fi.CROP), (255, 0, 255))
    out = fml.composite(orig, gen, plan, crop_img, mask)

    alpha = fml.lock_alpha(mask, plan) * fml.give_back_keep(crop, np.asarray(gen, np.float32), plan)
    x0, y0, side = plan.crop
    a_small = cv2.resize(alpha, (side, side), interpolation=cv2.INTER_AREA)
    full = np.zeros(np.asarray(orig.convert("L")).shape, np.float32)
    full[y0:y0 + side, x0:x0 + side] = a_small
    untouched = full <= 0.0
    assert untouched.any()
    assert np.array_equal(np.asarray(out.convert("RGB"))[untouched],
                          np.asarray(orig.convert("RGB"))[untouched])


def test_the_generated_face_actually_lands():
    orig, plan = _scene(), _plan()
    crop_img = fi.crop_1024(orig, plan)
    mask = fml.gen_mask(np.asarray(crop_img.convert("RGB"), np.float32), plan)
    gen = Image.new("RGB", (fi.CROP, fi.CROP), (255, 0, 255))
    out = fml.composite(orig, gen, plan, crop_img, mask)
    cx, cy = (int(v) for v in plan.anchor_center)
    assert not np.array_equal(np.asarray(out)[cy, cx], np.asarray(orig)[cy, cx])


# ── 이식 = 정본 ─────────────────────────────────────────────────────────────
@pytest.mark.skipif(not REFERENCE.exists(), reason=f"정본 참조 없음: {REFERENCE}")
def test_the_port_matches_the_reference_bit_for_bit():
    """정본과 **같은 입력에서 같은 결과**. 상수 하나만 흘려도 여기서 깨진다."""
    spec = importlib.util.spec_from_file_location("mask_lock_6_reference", REFERENCE)
    ref = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ref)

    orig, plan = _scene(), _plan()
    crop_img = fi.crop_1024(orig, plan)
    crop = np.asarray(crop_img.convert("RGB"), np.float32)
    gen_img = Image.new("RGB", (fi.CROP, fi.CROP), (200, 120, 90))
    gen = np.asarray(gen_img, np.float32)

    assert np.array_equal(fml.gen_mask(crop, plan), ref.gen_mask(crop, plan))
    mask = ref.gen_mask(crop, plan)
    assert np.array_equal(fml.lock_alpha(mask, plan), ref.lock_alpha(mask, plan))
    assert np.array_equal(fml.give_back_keep(crop, gen, plan), ref.give_back_keep(crop, gen, plan))
    ours = np.asarray(fml.composite(orig, gen_img, plan, crop_img, mask), np.int16)
    theirs = np.asarray(ref.composite(orig, gen_img, plan, crop_img, mask), np.int16)
    assert int(np.abs(ours - theirs).max()) == 0


def test_the_constants_are_the_measured_ones():
    """실측값이다 — 바꾸려면 키트 10컷을 다시 돌려야 한다."""
    assert (fml.CELL, fml.EDGE_LOCK_PX, fml.SPILL_PX, fml.FEATHER_SIGMA) == (8, 72, 24, 10)
    assert (fml.HEAD_SIDE, fml.HEAD_TOP, fml.HEAD_BOTTOM) == (0.35, 0.60, 1.10)
    assert (fml.HEAD_MARGIN_PX, fml.NECK_DILATE) == (24, 17)
    assert (fml.PROTECT_NEW_HEAD_PX, fml.OLD_HEAD_PX, fml.OLD_HEAD_CORE_PX) == (15, 32, 9)
    assert (fml.OLD_PERSON_T, fml.NEW_PERSON_T) == (0.2, 0.4)


# ── 능력 판정 ───────────────────────────────────────────────────────────────
class _Pod:
    def __init__(self, healthz):
        self._healthz = healthz

    def render(self, control, prompt, seed, base=None, gen_mask=None):
        return control.copy()

    def supports_mask_lock(self):
        return bool(self._healthz.get("mask_lock"))


def test_an_old_pod_without_the_capability_takes_the_old_path():
    """★ 옛 파드는 모르는 필드를 조용히 무시하고 전체를 다시 그린다 — 그 결과에 잠금 합성을
    쓰면 경계가 깨진다. 그래서 능력이 없으면 기존 composite_with_meta 경로다."""
    assert fi.backend_mask_lock(_Pod({"ok": True, "code_version": "x"})) is False
    assert fi.backend_mask_lock(_Pod({"mask_lock": True})) is True
    assert fi.backend_mask_lock(_Pod({"mask_lock": True}), enabled=False) is False


def test_a_local_backend_is_judged_by_its_signature():
    assert fi.backend_mask_lock(fi.NullBackend()) is True

    class _Legacy:
        def render(self, control, prompt, seed):
            return control

    assert fi.backend_mask_lock(_Legacy()) is False


def test_the_setting_defaults_to_on():
    from conftest import make_settings

    assert make_settings(gemini_api_key="x", r2_bucket="b").face_mask_lock is True


# ── 레시피 해시 ─────────────────────────────────────────────────────────────
def test_the_recipe_hash_names_the_recipe_and_carries_its_constants():
    locked = face_recipe.recipe_fields(mask_lock=True)
    assert locked["mask_lock"] == "mask_lock_6"
    assert face_recipe.recipe_fields(mask_lock=False)["mask_lock"] is None
    assert face_recipe.recipe_id(locked) != face_recipe.recipe_id(
        face_recipe.recipe_fields(mask_lock=False))
    for key, value in (("mask_lock_cell", fml.CELL), ("mask_lock_edge_px", fml.EDGE_LOCK_PX),
                       ("mask_lock_spill_px", fml.SPILL_PX),
                       ("mask_lock_head_margin_px", fml.HEAD_MARGIN_PX),
                       ("mask_lock_old_head_px", fml.OLD_HEAD_PX),
                       ("mask_lock_old_person_t", fml.OLD_PERSON_T)):
        assert locked[key] == value


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


@pytest.mark.parametrize("base,mask,needle", [
    (_b64(Image.new("RGB", (8, 8))), None, "together"),
    ("not-base64!!", _b64(Image.new("L", (8, 8))), "base64"),
    (_b64(Image.new("RGB", (16, 16))), _b64(Image.new("L", (8, 8))), "size"),
])
def test_bad_lock_inputs_are_400(service, base, mask, needle):
    from fastapi import HTTPException

    req = service.RenderRequest(control_png="x", prompt="p", base_png=base, gen_mask_png=mask)
    with pytest.raises(HTTPException) as err:
        service._mask_lock_inputs(req, (8, 8))
    assert err.value.status_code == 400 and needle in str(err.value.detail)


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
    """torch·diffusers 는 함수 안에서만 import 한다 — API 이미지에는 없다."""
    from app.agents import face_identity_qwen

    text = pathlib.Path(face_identity_qwen.__file__).read_text(encoding="utf-8")
    head = text[: text.index("def load_base_pipeline")]
    assert "import torch" not in head and "import diffusers" not in head
    lock = text[text.index("def _mask_lock("):]
    assert lock.index("import torch") < lock.index("dtype = pipe.transformer.dtype")


def test_the_server_module_never_imports_torch():
    text = pathlib.Path(fml.__file__).read_text(encoding="utf-8")
    assert "import torch" not in text and "diffusers" not in text


# ── 파드 지원 여부 캐시 규칙 ────────────────────────────────────────────────
#
# 마스크 잠금은 **파드가 안다고 확인된 뒤에만** 켠다. 옛 파드는 그 필드를 조용히 무시하고
# 크롭 전체를 다시 그리는데, 그 결과에 잠금 알파를 쓰면 경계가 깨진다. 그래서 확인 실패는
# 안전한 쪽(False)으로 간다 — 다만 **그 실패를 기억하면 안 된다.**


class _Probe:
    """httpx.get 대역. 첫 n 번은 터지고 그 뒤로는 mask_lock 값을 돌려준다."""

    def __init__(self, *, fail_times=0, mask_lock=True):
        self.fail_times, self.mask_lock = fail_times, mask_lock
        self.calls = []

    def __call__(self, url, timeout=None):
        self.calls.append(url)
        if len(self.calls) <= self.fail_times:
            raise RuntimeError("pod still booting")
        return type("R", (), {"json": lambda _self, body={"mask_lock": self.mask_lock}: body})()


def _backend(monkeypatch, probe, url="http://pod.test/render"):
    import httpx

    from app.agents.face_identity import HttpFaceBackend

    monkeypatch.setattr(httpx, "get", probe)
    return HttpFaceBackend(url)


def test_a_definite_yes_is_asked_once(monkeypatch):
    probe = _Probe(mask_lock=True)
    backend = _backend(monkeypatch, probe)

    assert [backend.supports_mask_lock() for _ in range(3)] == [True, True, True]
    assert len(probe.calls) == 1
    assert probe.calls[0] == "http://pod.test/healthz"


def test_a_definite_no_is_also_asked_once(monkeypatch):
    """파드가 '모른다'고 **대답**한 것은 확답이다 — 매 컷 다시 물을 이유가 없다."""
    probe = _Probe(mask_lock=False)
    backend = _backend(monkeypatch, probe)

    assert [backend.supports_mask_lock() for _ in range(3)] == [False, False, False]
    assert len(probe.calls) == 1


def test_a_failed_probe_is_not_remembered(monkeypatch):
    """★ 이게 규칙의 핵심.

    파드가 뜨는 중이라 첫 확인이 실패하는 건 흔하다. 그걸 기억해 버리면 그 백엔드 인스턴스의
    남은 수명 **전체**에서 마스크 잠금이 꺼지고, 그 뒤 컷은 전부 조각 경계가 있는 옛 방식으로
    나간다 — 로그엔 probe failed 한 줄뿐이라 원인을 못 찾는다.
    """
    probe = _Probe(fail_times=2, mask_lock=True)
    backend = _backend(monkeypatch, probe)

    assert backend.supports_mask_lock() is False   # 확인 실패 → 이번 컷만 안전하게
    assert backend.supports_mask_lock() is False
    assert backend.supports_mask_lock() is True    # 파드가 뜨자마자 회복한다
    assert len(probe.calls) == 3
    assert backend.supports_mask_lock() is True and len(probe.calls) == 3  # 그 뒤엔 확답 재사용


def test_a_different_pod_is_asked_again(monkeypatch):
    """파드가 바뀌면 확답도 무효다 — 새 파드는 다른 코드일 수 있다."""
    probe = _Probe(mask_lock=True)
    backend = _backend(monkeypatch, probe)
    assert backend.supports_mask_lock() is True

    backend.url = "http://other-pod.test/render"
    assert backend.supports_mask_lock() is True
    assert probe.calls == ["http://pod.test/healthz", "http://other-pod.test/healthz"]
