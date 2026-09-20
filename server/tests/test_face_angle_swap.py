import asyncio
from io import BytesIO
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from PIL import Image

from app.agents import face_angle_swap as angle
from app.agents import face_identity

BG = 232
SIZE = (400, 600)          # (width, height)
FACE_BOX = (140.0, 90.0, 60.0, 100.0)   # 머리(130~270) 왼쪽에 있는 얼굴 = 코가 화면 왼쪽


def studio_cut(*, garment=70, shadow=True):
    """합성 호리존 컷 — 단색 배경 · 어두운 머리 · 좁은 목 · 옷 · 벽 그림자."""
    a = np.full((SIZE[1], SIZE[0], 3), BG, np.uint8)
    if shadow:
        a[60:330, 250:340] = BG - 14                              # 벽 그림자(사람 아님)
    yy, xx = np.ogrid[:SIZE[1], :SIZE[0]]
    head = ((xx - 200) / 70.0) ** 2 + ((yy - 150) / 95.0) ** 2 <= 1
    a[head] = 40                                                  # 머리카락
    a[240:300, 170:230] = 150                                     # 목(피부)
    a[300:, 60:340] = garment                                     # 옷
    return a


def png(array):
    stream = BytesIO()
    Image.fromarray(array).save(stream, "PNG")
    return stream.getvalue()


class FakeBackend:
    def __init__(self, fill=(255, 0, 0)):
        self.uploads: list[str] = []
        self.workflow: dict | None = None
        self.fill = fill

    def upload(self, array, name, mode="RGB"):
        self.uploads.append(name)
        return name

    def run(self, workflow):
        self.workflow = workflow
        out = np.zeros((angle.CROP_PX, angle.CROP_PX, 3), np.uint8)
        out[:, :] = self.fill
        return out


@pytest.fixture
def no_face(monkeypatch):
    monkeypatch.setattr(face_identity, "detect_face", lambda *_a, **_k: None)


def test_foreground_drops_the_wall_shadow_and_keeps_the_person():
    fg = angle.foreground(studio_cut())
    assert fg[150, 200] and fg[400, 200]        # 머리 · 옷
    assert not fg[100, 300]                     # 그림자
    assert not fg[10, 10]                       # 배경


def test_hair_blob_is_the_dark_top_blob_and_stops_at_the_neck():
    hair = angle.hair_blob(studio_cut(garment=30))   # 어두운 옷이 목덜미에서 붙는 상황
    assert hair[150, 200]
    assert not hair[400, 200]                        # 옷까지 번지지 않는다
    assert int(np.where(hair.any(axis=1))[0].max()) < 300


def test_trim_at_neck_keeps_a_hair_only_blob_whole():
    blob = np.zeros((200, 200), bool)
    yy, xx = np.ogrid[:200, :200]
    blob[((xx - 100) / 60.0) ** 2 + ((yy - 100) / 70.0) ** 2 <= 1] = True
    assert angle.trim_at_neck(blob).sum() == blob.sum()


def test_head_region_for_a_side_cut_follows_the_face_box(monkeypatch):
    monkeypatch.setattr(face_identity, "detect_face", lambda *_a, **_k: SimpleNamespace(box=FACE_BOX))
    core, nose_right = angle.head_region(studio_cut(), direction="side")
    assert core[150, 200] and nose_right is False        # 얼굴 박스가 머리 왼쪽에 있다
    assert not core[400, 200]                            # 옷은 아니다


def test_head_region_for_a_back_cut_needs_no_face(no_face):
    core, nose_right = angle.head_region(studio_cut(), direction="back")
    assert core[150, 200] and nose_right is None
    assert not core[400, 200]


def test_head_region_without_a_face_on_a_side_cut_fails(no_face):
    with pytest.raises(angle.AngleSwapUnavailable) as err:
        angle.head_region(studio_cut(), direction="side")
    assert err.value.reason == "no_face"


def test_garment_region_covers_only_below_the_head(no_face):
    core, _ = angle.head_region(studio_cut(), direction="back")
    garment = angle.garment_region(studio_cut(), core)
    assert garment[400, 200] and not garment[150, 200]
    assert not (garment & core).any()


def test_grow_hair_widens_the_head_for_new_hair(no_face):
    core, _ = angle.head_region(studio_cut(), direction="back")
    assert angle.grow_hair(core, "back").sum() > angle.grow_hair(core, "side").sum() > core.sum()


def test_crop_box_is_square_inside_the_image_and_holds_the_head(no_face):
    core, _ = angle.head_region(studio_cut(), direction="back")
    left, top, side = angle.crop_box(core, SIZE)
    assert 0 <= left and 0 <= top and left + side <= SIZE[0] and top + side <= SIZE[1]
    ys, xs = np.where(core)
    assert left <= xs.mean() <= left + side and top <= ys.mean() <= top + side


def test_plan_makes_a_1024_crop_and_mask_and_rejects_an_empty_photo(no_face):
    p = angle.plan(studio_cut(), direction="back")
    assert p.crop1k.shape == (angle.CROP_PX, angle.CROP_PX, 3)
    assert p.mask1k.shape == (angle.CROP_PX, angle.CROP_PX) and p.mask1k.any()
    assert p.metadata["headPx"] > 0 and p.metadata["garmentPx"] > 0
    assert 0 < p.metadata["maskCropPct"] < 100 and p.nose_right is None
    with pytest.raises(angle.AngleSwapUnavailable) as err:
        angle.plan(np.full((SIZE[1], SIZE[0], 3), BG, np.uint8), direction="back")
    assert err.value.reason == "no_person"


def test_side_cut_facing_is_read_from_the_face_box(monkeypatch):
    monkeypatch.setattr(face_identity, "detect_face",
                        lambda *_a, **_k: SimpleNamespace(box=(230.0, 90.0, 70.0, 100.0)))
    assert angle.plan(studio_cut(), direction="side").nose_right is True
    monkeypatch.setattr(face_identity, "detect_face", lambda *_a, **_k: SimpleNamespace(box=FACE_BOX))
    assert angle.plan(studio_cut(), direction="side").nose_right is False


@pytest.mark.parametrize("direction,nose_right,slot,data", [
    ("back", None, angle.SLOT_BACK, b"back"),
    ("side", True, angle.SLOT_SIDE_NOSE_RIGHT, b"right"),
    ("side", False, angle.SLOT_SIDE_NOSE_LEFT, b"left"),
])
def test_photo_slot_follows_the_cut_direction(direction, nose_right, slot, data):
    photos = angle.AnglePhotos(side_nose_left=b"left", side_nose_right=b"right", back=b"back")
    assert photos.for_direction(direction, nose_right) == (data, slot)
    assert angle.AnglePhotos().for_direction(direction, nose_right)[0] is None


def test_graph_uses_2511_with_bfs_lora_and_the_mask():
    wf = angle.graph("crop.png", "ref.png", "mask.png", angle.prompt_for("back"), seed=7)
    assert wf["1"]["inputs"]["unet_name"] == angle.UNET
    assert wf["16"]["inputs"]["lora_name"] == angle.BFS_LORA
    # 2026-09-20 육안 판정: 1.0. 합격한 옆 4컷·뒤 4컷이 전부 이 강도다(1.2 는 머리가 부푼다).
    assert wf["16"]["inputs"]["strength_model"] == angle.BFS_STRENGTH == 1.0
    assert wf["5"]["inputs"]["image"] == "mask.png" and wf["15"]["inputs"]["image"] == "ref.png"
    assert wf["11"]["class_type"] == "SetLatentNoiseMask"
    assert wf["12"]["inputs"]["seed"] == 7 and wf["12"]["inputs"]["steps"] == angle.STEPS
    assert "No face is visible" in wf["8"]["inputs"]["prompt"]
    assert "side profile" in angle.prompt_for("side")


def test_composite_changes_the_head_only_and_keeps_the_garment(no_face):
    base = studio_cut()
    p = angle.plan(base, direction="back")
    out = np.zeros((angle.CROP_PX, angle.CROP_PX, 3), np.uint8)
    out[:, :] = (255, 0, 0)
    final = angle.composite(p, out)
    assert final.shape == base.shape
    inner = cv2.erode(p.garment.astype(np.uint8), np.ones((9, 9), np.uint8)).astype(bool)
    assert (final[inner] == base[inner]).all()                  # 옷 안쪽은 원본 그대로
    assert not (final[p.head] == base[p.head]).all()            # 머리는 바뀐다
    left, top, side = p.box
    far = ~(p.head | p.garment)
    far[top:top + side, left:left + side] = False
    assert (final[far] == base[far]).all()                      # 크롭 밖은 한 픽셀도 안 바뀐다
    # 옷 경계 2px 만 섞는 계약 — 바뀐 옷 픽셀은 옷 영역의 3% 미만
    assert 0 <= angle.changed_garment_px(p, final) < 0.03 * int(p.garment.sum())


def test_swap_uploads_crop_reference_mask_and_returns_png(no_face):
    backend = FakeBackend()
    photos = angle.AnglePhotos(back=png(np.full((300, 300, 3), 120, np.uint8)))
    outcome: dict = {}
    data, mime = asyncio.run(angle.swap(png(studio_cut()), "image/png", direction="back",
                                        photos=photos, backend=backend, outcome=outcome))
    assert mime == "image/png"
    assert backend.uploads == ["angle_crop.png", "angle_ref.png", "angle_mask.png"]
    with Image.open(BytesIO(data)) as opened:
        assert opened.size == SIZE and opened.format == "PNG"
    assert outcome["angle_swap"] == "applied"
    assert outcome["angle_swap_meta"]["slot"] == angle.SLOT_BACK
    assert outcome["angle_swap_meta"]["direction"] == "back"
    assert "garmentChangedPx" in outcome["angle_swap_meta"]


def test_swap_fails_loudly_without_a_photo_or_when_the_backend_breaks(no_face):
    with pytest.raises(angle.AngleSwapUnavailable) as err:
        asyncio.run(angle.swap(png(studio_cut()), "image/png", direction="back",
                               photos=angle.AnglePhotos(), backend=FakeBackend()))
    assert err.value.reason == "no_angle_photo"

    class Broken(FakeBackend):
        def run(self, workflow):
            raise RuntimeError("boom")

    photos = angle.AnglePhotos(back=png(np.full((10, 10, 3), 9, np.uint8)))
    with pytest.raises(angle.AngleSwapUnavailable) as err:
        asyncio.run(angle.swap(png(studio_cut()), "image/png", direction="back",
                               photos=photos, backend=Broken()))
    assert err.value.reason == "backend_error"


def test_swap_rejects_front_cuts(no_face):
    with pytest.raises(ValueError):
        asyncio.run(angle.swap(png(studio_cut()), "image/png", direction="front",
                               photos=angle.AnglePhotos(back=b"x"), backend=FakeBackend()))


# ── 피부톤 보정 ───────────────────────────────────────────────────────────────
#
# 등록자 사진과 베이스 컷의 조명이 다르면 목 이음선에서 밝기가 튄다. 2026-09-20 실측
# (보정 없음): 오른쪽 옆 9.2 · 왼쪽 옆 12.3 · 뒤 7.1. 얼굴 패스의 운영 컷은 보정 후 0.1~2.9 라
# 옆·뒤만 3~4배 어긋난 채 나가고 있었다.

def test_tone_shift_measures_the_gap_between_new_head_and_original_neck(no_face):
    base = studio_cut()
    p = angle.plan(base, direction="back")
    left, top, side = p.box
    out = p.base[top:top + side, left:left + side].astype(np.float32).copy()
    # 머리 안쪽을 통째로 +20 밝게 = 참고 사진이 베이스보다 밝은 상황.
    out[p.head[top:top + side, left:left + side]] += 20.0
    shift = angle.tone_shift(p, out, p.box)
    assert shift is not None
    # 되돌리는 방향이어야 한다(= 음수), 크기는 그 차이에 가깝다.
    assert all(v < 0 for v in shift) and 15.0 < abs(shift).max() < 25.0


def test_tone_shift_is_none_without_enough_skin_to_sample(no_face):
    """표본이 적으면 그 평균은 목이 아니라 잡음이다 — 그럴 땐 보정하지 않는다."""
    base = studio_cut(garment=70)
    p = angle.plan(base, direction="back")
    left, top, side = p.box
    dark = np.zeros((side, side, 3), np.float32)      # 피부 밝기 범위에 아무것도 없다
    assert angle.tone_shift(p, dark, p.box) is None


def test_tone_sampling_ignores_the_wall_shadow(no_face):
    """★ 밝기 창만으로 고르면 벽 그림자가 피부로 들어온다 — 2026-09-20 합성 컷에서 바깥 링
    평균이 199(그림자)로 나왔다. 전경 마스크로 먼저 잘라야 목만 남는다."""
    base = studio_cut()                               # 그림자 218 · 벽 232 · 목 150
    p = angle.plan(base, direction="back")
    left, top, side = p.box
    out = p.base[top:top + side, left:left + side].astype(np.float32).copy()
    out[p.head[top:top + side, left:left + side]] += 20.0
    shift = angle.tone_shift(p, out, p.box)
    # 그림자(218)를 표본했다면 목(150)과의 차이가 섞여 +29 쯤이 나온다(그때 실제 값).
    assert shift is None or abs(shift).max() < 25.0


def test_tone_is_applied_to_the_person_and_never_to_the_background():
    """★ 머리 마스크는 머리카락 여유분만큼 배경 위로 넘어간다. 전역으로 걸면 벽에 머리 모양
    자국이 남는다(2026-09-20 실측에서 세 컷 모두 그랬다)."""
    bg_color = np.array([BG, BG, BG], np.float32)
    out = np.full((200, 200, 3), BG, np.float32)
    out[60:140, 60:140] = 150.0                        # 사람(피부)
    shifted = angle.apply_tone(out, np.array([-9.0, -9.0, -9.0]), bg_color)
    assert abs(shifted[100, 100] - (150.0 - 9.0)).max() < 0.5      # 사람은 보정된다
    assert abs(shifted[5, 5] - BG).max() < 0.5                     # 배경은 그대로다


def test_composite_records_the_tone_shift_for_review(no_face):
    base = studio_cut()
    p = angle.plan(base, direction="back")
    out = np.zeros((angle.CROP_PX, angle.CROP_PX, 3), np.uint8)
    out[:, :] = (160, 150, 145)
    meta: dict = {}
    angle.composite(p, out, meta=meta)
    assert "toneShift" in meta


def test_swap_refuses_a_reference_whose_lighting_is_hopeless(no_face, monkeypatch):
    """보정량이 게이트를 넘으면 컷을 버린다 — 억지로 붙이면 목만 물든 사람이 나간다.
    얼굴 패스의 lighting_off(GATE_COLOR_MAX 35.0)와 같은 자리·같은 값이다."""
    from app.agents import face_identity

    assert angle.TONE_MAX_SHIFT == face_identity.GATE_COLOR_MAX
    monkeypatch.setattr(angle, "tone_shift", lambda *_a, **_k: np.array([99.0, 0.0, 0.0]))
    photos = angle.AnglePhotos(back=png(np.full((300, 300, 3), 120, np.uint8)))
    with pytest.raises(angle.AngleSwapUnavailable) as err:
        asyncio.run(angle.swap(png(studio_cut()), "image/png", direction="back",
                               photos=photos, backend=FakeBackend()))
    assert err.value.reason == "tone_off"
