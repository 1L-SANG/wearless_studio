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


def test_tone_leaves_hair_alone():
    """★ 보정량은 **목 피부**를 기준으로 잰다. 그걸 머리카락에까지 걸면 머리색이 같이 밝아진다
    — 2026-09-21 뒷모습 컷이 이음선 1.6 으로 제일 잘 맞았는데도 머리가 갈색기를 띠었다."""
    bg_color = np.array([BG, BG, BG], np.float32)
    out = np.full((200, 200, 3), BG, np.float32)
    out[60:140, 60:100] = 40.0                        # 머리카락(어둡다)
    out[60:140, 100:140] = 150.0                      # 피부
    shifted = angle.apply_tone(out, np.array([12.0, 12.0, 12.0]), bg_color)
    assert abs(shifted[100, 120] - (150.0 + 12.0)).max() < 0.5      # 피부는 보정된다
    assert abs(shifted[100, 70] - 40.0).max() < 0.5                 # 머리카락은 그대로다
    assert abs(shifted[5, 5] - BG).max() < 0.5                      # 배경도 그대로다


def test_tone_ramps_between_hair_and_skin_without_a_hard_edge():
    """머리카락과 피부 사이 밝기는 섞어서 건다 — 딱 잘라내면 경계선이 보인다."""
    bg_color = np.array([BG, BG, BG], np.float32)
    mid = (angle.TONE_HAIR_MAX + angle.TONE_SKIN_MIN) / 2.0
    out = np.full((50, 50, 3), mid, np.float32)
    shifted = angle.apply_tone(out, np.array([10.0, 10.0, 10.0]), bg_color)
    applied = float(shifted[25, 25].mean() - mid)
    assert 3.0 < applied < 7.0                        # 절반쯤 걸린다


def test_composite_records_the_tone_shift_for_review(no_face):
    base = studio_cut()
    p = angle.plan(base, direction="back")
    out = np.zeros((angle.CROP_PX, angle.CROP_PX, 3), np.uint8)
    out[:, :] = (160, 150, 145)
    meta: dict = {}
    angle.composite(p, out, meta=meta)
    assert "toneShift" in meta


def test_swap_refuses_a_reference_whose_lighting_is_hopeless(no_face, monkeypatch):
    """보정을 **끝낸 뒤에도** 이음선 차이가 남으면 컷을 버린다.

    2026-09-23 오너: "베이스 컷이 나오면 거기에 사진 톤을 맞춰야지". 그래서 보정 전
    차이가 크다는 것만으로는 안 버린다 — 맞춰 보고, 그래도 안 맞으면 버린다.
    여기서는 링 표본을 고정해 보정이 통하지 않는 사진을 흉내 낸다.
    """
    hopeless = (np.array([200.0, 200.0, 200.0]), np.array([90.0, 90.0, 90.0]))
    monkeypatch.setattr(angle, "tone_means", lambda *_a, **_k: hopeless)
    photos = angle.AnglePhotos(back=png(np.full((300, 300, 3), 120, np.uint8)))
    with pytest.raises(angle.AngleSwapUnavailable) as err:
        asyncio.run(angle.swap(png(studio_cut()), "image/png", direction="back",
                               photos=photos, backend=FakeBackend()))
    assert err.value.reason == "tone_off"


def test_a_big_lighting_gap_is_matched_by_gain_instead_of_being_dropped(no_face, monkeypatch):
    """조명이 크게 어긋나도 보정으로 이음선이 맞으면 컷은 나간다.

    2026-09-23 운영: 새 통일 촬영 베이스와 등록 옆모습 사진이 [22.9, 33.0, 42.6] 으로
    벌어져 옆모습 컷이 통째로 tone_off 로 버려졌다. 덧셈으로는 못 맞추는 크기라
    비율(gain)로 맞춘다.
    """
    calls = {"n": 0}

    def means(*_a, **_k):
        calls["n"] += 1
        # 첫 호출 = 보정 전(크게 어긋남), 그 뒤 = 보정 후(맞음)
        return ((np.array([200.0, 190.0, 180.0]), np.array([160.0, 150.0, 135.0]))
                if calls["n"] == 1
                else (np.array([200.0, 190.0, 180.0]), np.array([197.0, 188.0, 179.0])))

    monkeypatch.setattr(angle, "tone_means", means)
    photos = angle.AnglePhotos(back=png(np.full((300, 300, 3), 120, np.uint8)))
    data, mime = asyncio.run(angle.swap(png(studio_cut()), "image/png", direction="back",
                                        photos=photos, backend=FakeBackend()))
    assert mime == "image/png" and data


def test_small_gaps_still_use_the_tuned_additive_path():
    """작은 차이는 예전 그대로 덧셈이다 — 2026-09-20 실측(7~12)이 그 범위에서 튜닝됐다."""
    assert angle.TONE_GAIN_FROM > 12.0
    assert angle.TONE_GAIN_MIN < 1.0 < angle.TONE_GAIN_MAX


def test_gain_matches_the_head_to_the_base_neck():
    neck = np.array([200.0, 190.0, 180.0])
    head = np.array([160.0, 150.0, 135.0])
    gain = angle.tone_gain(neck, head)
    matched = head * gain
    assert np.allclose(matched, neck, atol=1.0)
    # 한계 밖은 잘라 낸다 — 억지로 맞추지 않고 잔차로 판단한다.
    clipped = angle.tone_gain(np.array([250.0, 250.0, 250.0]), np.array([20.0, 20.0, 20.0]))
    assert float(clipped.max()) == pytest.approx(angle.TONE_GAIN_MAX, abs=1e-5)


# ── RunPod Serverless 백엔드 ─────────────────────────────────────────────────
#
# 파드와 표면이 같다(upload → run) — graph() 가 만드는 워크플로 JSON 은 그대로 들어간다.
# 다른 건 어디에 올리는가뿐이다: 파드는 /upload/image, 서버리스는 요청 본문의 images 배열.

class FakeServerlessHTTP:
    """/run 으로 넣고 /status 로 받는 대역. statuses 를 여러 개 주면 폴링을 흉내낸다."""

    def __init__(self, statuses, status=200, run_body=None):
        self.statuses = list(statuses)
        self.status = status
        self.sent = None
        self.run_body = run_body if run_body is not None else {"id": "job1"}
        self.status_calls = 0

    def post(self, path, json=None):
        self.sent = (path, json)
        return SimpleNamespace(status_code=self.status, json=lambda: self.run_body)

    def get(self, path):
        self.status_calls += 1
        body = self.statuses[0] if len(self.statuses) == 1 else self.statuses.pop(0)
        return SimpleNamespace(status_code=200, json=lambda: body)


def serverless(final, status=200, statuses=None, run_body=None):
    backend = angle.ServerlessBackend.__new__(angle.ServerlessBackend)
    backend.base = "https://api.runpod.ai/v2/ep1"
    backend._client = FakeServerlessHTTP(statuses or [final], status, run_body)
    backend._images = []
    backend._timeout = 30.0
    backend._poll = 0.0
    return backend


def one_pixel_png_b64():
    import base64

    stream = BytesIO()
    Image.fromarray(np.full((4, 4, 3), 7, np.uint8)).save(stream, "PNG")
    return base64.b64encode(stream.getvalue()).decode()


def test_serverless_sends_the_workflow_and_the_images_in_one_request():
    backend = serverless({"status": "COMPLETED",
                          "output": {"images": [{"type": "base64", "data": one_pixel_png_b64()}]}})
    backend.upload(np.full((8, 8, 3), 3, np.uint8), "angle_crop.png")
    backend.upload(np.full((8, 8), 255, np.uint8), "angle_mask.png", "L")
    out = backend.run({"14": {"class_type": "SaveImage"}})
    path, payload = backend._client.sent
    # ★ /runsync 는 90초쯤에서 잘린다(2026-09-21 실측: 세 컷 모두 93초에 IN_PROGRESS).
    #   한 컷은 190초 안팎이라 **비동기로 넣고 폴링**해야 한다.
    assert path == "/run"
    assert payload["input"]["workflow"] == {"14": {"class_type": "SaveImage"}}
    # 이름은 워크플로가 참조하는 그 이름이어야 한다 — 어긋나면 ComfyUI 가 입력을 못 찾는다.
    assert [i["name"] for i in payload["input"]["images"]] == ["angle_crop.png", "angle_mask.png"]
    assert all(i["image"] for i in payload["input"]["images"])
    assert out.shape == (4, 4, 3)
    # 다음 호출에 이전 이미지가 딸려 가면 안 된다.
    assert backend._images == []


@pytest.mark.parametrize("body,status,reason", [
    ({"status": "FAILED", "error": "worker died"}, 200, "backend_error"),
    ({"status": "TIMED_OUT"}, 200, "backend_error"),
    ({"status": "CANCELLED"}, 200, "backend_error"),
    ({}, 500, "backend_error"),
    ({"status": "COMPLETED", "output": {"images": []}}, 200, "graph_error"),
    ({"status": "COMPLETED", "output": {"images": [{"type": "s3_url", "data": "https://x"}]}},
     200, "backend_error"),                                   # S3 업로드는 안 켠다
])
def test_serverless_failures_never_return_someone_elses_head(body, status, reason):
    backend = serverless(body, status)
    with pytest.raises(angle.AngleSwapUnavailable) as err:
        backend.run({"14": {}})
    assert err.value.reason == reason


def test_reference_photo_is_shrunk_before_it_goes_on_the_wire():
    """서버리스 요청에는 크기 상한이 있다(/runsync 20MB). 아이폰 원본을 그대로 실으면
    큰 사진 하나로 요청이 막힌다."""
    big = np.full((6048, 8064, 3), 120, np.uint8)
    small = angle.shrink(big)
    assert max(small.shape[:2]) == angle.REFERENCE_MAX_PX
    assert small.shape[0] / small.shape[1] == pytest.approx(6048 / 8064, rel=0.01)
    # 이미 작으면 그대로 둔다(같은 객체).
    tiny = np.full((300, 200, 3), 9, np.uint8)
    assert angle.shrink(tiny) is tiny


def test_spec_prefers_serverless_over_the_pod():
    photos = angle.AnglePhotos(back=b"b")
    base = dict(face_angle_swap_enabled=True, face_angle_seed=42,
                face_angle_backend_url="https://pod.example", face_angle_backend_token="t")
    with_endpoint = angle.spec_from(
        SimpleNamespace(face_angle_endpoint_id="ep1", face_runpod_api_key="k", **base), photos)
    assert isinstance(with_endpoint.backend, angle.ServerlessBackend)
    assert with_endpoint.backend.base.endswith("/v2/ep1")
    # 엔드포인트가 없으면 파드로 — 서버리스를 세우기 전·검증 중의 다리.
    pod = angle.spec_from(
        SimpleNamespace(face_angle_endpoint_id=None, face_runpod_api_key="k", **base), photos)
    assert isinstance(pod.backend, angle.ComfyBackend)
    # 엔드포인트만 있고 키가 없으면 서버리스로 못 간다 — 파드로 떨어진다.
    keyless = angle.spec_from(
        SimpleNamespace(face_angle_endpoint_id="ep1", face_runpod_api_key=None, **base), photos)
    assert isinstance(keyless.backend, angle.ComfyBackend)


def test_serverless_waits_through_queue_and_progress():
    """★ 한 컷은 190초 안팎이다. 큐·진행 중을 만나면 끝날 때까지 다시 묻는다."""
    done = {"status": "COMPLETED",
            "output": {"images": [{"type": "base64", "data": one_pixel_png_b64()}]}}
    backend = serverless(None, statuses=[{"status": "IN_QUEUE"}, {"status": "IN_PROGRESS"}, done])
    out = backend.run({"14": {}})
    assert out.shape == (4, 4, 3)
    assert backend._client.status_calls == 3


def test_serverless_gives_up_when_the_job_never_finishes():
    """제한 시간을 넘기면 마지막 상태로 실패시킨다 — 영원히 기다리면 잡이 lease 를 잃는다."""
    backend = serverless(None, statuses=[{"status": "IN_PROGRESS"}])
    backend._timeout = 0.0
    with pytest.raises(angle.AngleSwapUnavailable) as err:
        backend.run({"14": {}})
    assert err.value.reason == "backend_error"


def test_serverless_needs_a_job_id_to_poll():
    backend = serverless(None, run_body={})
    with pytest.raises(angle.AngleSwapUnavailable) as err:
        backend.run({"14": {}})
    assert err.value.reason == "backend_error"


def test_http_timeout_is_per_request_not_per_job():
    """한 값으로 묶으면 폴링 한 번이 컷 전체 시간을 기다린다 — 요청과 잡의 제한은 다르다."""
    backend = angle.ServerlessBackend("ep1", "k", timeout=1800.0)
    assert backend._timeout == 1800.0
    assert backend._client.timeout.read == 60.0


# ── 톤 보정 경계 (2026-09-21) ───────────────────────────────────────────────
#
# 45degree_view/4.png 실측: 보정량 27.5 를 2px 흐림으로 가렸더니 목에 직각으로 잘린
# 밝기 계단이 생겼다(가중치 경사 × 보정량 = 화소당 40.1). 게이트(35.0)는 "톤이 너무
# 어긋나면 버린다"만 보므로 통과한 컷에서도 그 계단이 남는다.
def test_a_small_shift_keeps_the_old_feather():
    """한 자리 보정은 예전 그대로여야 한다 — 옆모습 실측이 1.06 이었다."""
    assert angle.tone_feather(None) == angle.TONE_FEATHER
    assert angle.tone_feather(np.array([1.06, 0.92, 0.55])) < 2.5


def test_a_big_shift_widens_the_feather():
    """뒷모습 실측 27.5 · 32.0 — 2px 로는 못 가린다."""
    assert angle.tone_feather(np.array([27.49, 24.94, 14.11])) > 10.0
    assert angle.tone_feather(np.array([31.95, 31.32, 28.7])) > 12.0


def test_the_feather_grows_with_the_shift():
    previous = 0.0
    for magnitude in (0.0, 5.0, 15.0, 27.5, 35.0):
        value = angle.tone_feather(np.array([magnitude, 0.0, 0.0]))
        assert value >= previous
        previous = value


def test_the_feather_is_capped():
    """상한이 없으면 목 보정이 얼굴·옷까지 번진다."""
    assert angle.tone_feather(np.array([200.0, 0.0, 0.0])) == angle.TONE_FEATHER_MAX
    # 게이트 최대치에서도 상한 아래여야 한다 — 상한에 먼저 닿으면 비례가 무의미해진다.
    assert angle.tone_feather(np.array([angle.TONE_MAX_SHIFT, 0.0, 0.0])) < angle.TONE_FEATHER_MAX


def test_the_sign_of_the_shift_does_not_change_the_feather():
    """운영 실패분은 [-35.34, -7.54, 3.85] 였다 — 음수도 같은 크기로 봐야 한다."""
    assert (angle.tone_feather(np.array([-27.5, 0.0, 0.0]))
            == angle.tone_feather(np.array([27.5, 0.0, 0.0])))


def test_a_wider_feather_flattens_the_boundary_step():
    """★ 고치려는 그 계단이 실제로 줄어드는가 — 합성 경계로 직접 잰다."""
    raw = np.zeros((200, 200), np.float32)
    raw[:, 100:] = 1.0                      # 목 한가운데서 뚝 끊기는 가중치
    shift = 27.5

    def step(feather):
        weight = cv2.GaussianBlur(raw, (0, 0), feather)
        gx = cv2.Sobel(weight, cv2.CV_32F, 1, 0, ksize=3)
        return float(np.abs(gx).max()) * shift

    assert step(angle.tone_feather(np.array([shift, 0.0, 0.0]))) < step(angle.TONE_FEATHER) / 3
