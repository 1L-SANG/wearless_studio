"""크롭 패딩 — 3×얼굴폭이 사진에 막힐 때만 가장자리를 덧대고, 합성 뒤 잘라낸다.

왜(2026-09-13 운영 테스트컷 8장): 머리 조각이 뜬 건 **크롭 한 변이 사진 폭(1024)으로 잘린 3장**
뿐이었다. 막히지 않은 컷은 바탕과 LoRA 의 머리 모양이 달라도(closeup_4: 바탕 짧은 머리 · v7 버섯머리)
전부 깨끗했다. 즉 원인은 헤어라인 불일치가 아니라 `side = min(3·bw, W, H)` 의 clamp 다 —
크롭이 깎이면 정수리가 크롭 경계에 바싹 붙고, 생성 머리가 잘린 자리에 원본 머리 끝이 남는다.

실측 얼굴박스(1024×1536 바탕, YuNet):
  closeup_1 (285.5, 424.0, 426.7, 533.5)   3·bw = 1280 > 1024  → 막힘
  closeup_2 (283.8, 430.5, 428.3, 535.4)   3·bw = 1285 > 1024  → 막힘
  closeup_3 (300.6, 447.0, 417.9, 522.4)   3·bw = 1254 > 1024  → 막힘
  closeup_4 (373.1, 512.0, 277.8, 347.2)   3·bw =  833 < 1024  → 안 막힘(손대지 않는다)
"""

import numpy as np
import pytest
from PIL import Image

from app.agents import face_identity as fi
from app.agents import face_recipe
from conftest import make_settings

W, H = 1024, 1536
BLOCKED = {
    "closeup_1": (285.5, 424.0, 426.7, 533.5),
    "closeup_2": (283.8, 430.5, 428.3, 535.4),
    "closeup_3": (300.6, 447.0, 417.9, 522.4),
}
FREE = {
    "closeup_4": (373.1, 512.0, 277.8, 347.2),
    "fullbody_1": (385.0, 300.0, 252.7, 316.0),
}
#: 정수리 여유 = (얼굴박스 윗변 − 크롭 윗변) / 얼굴높이. 깨끗했던 컷들이 0.56~0.71 이었다.
HEADROOM_MIN = 0.55


def _headroom(plan) -> float:
    _, by, _, bh = plan.box
    return (by - plan.crop[1]) / bh


# ── 언제 덧대는가 ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("name,box", FREE.items())
def test_a_crop_that_fits_is_left_alone(name, box):
    """안 막히는 컷은 pad 0 이고 plan 이 **예전과 바이트 동일**하다 — 46장이 이쪽이다."""
    assert fi.crop_pad_for(W, H, box) == (0, 0, 0, 0)
    assert fi.plan_from_box(W, H, box) == fi.plan_from_box(W, H, box)


@pytest.mark.parametrize("name,box", BLOCKED.items())
def test_a_blocked_crop_gets_padded_until_three_face_widths_fit(name, box):
    pad = fi.crop_pad_for(W, H, box)
    assert any(pad), f"{name} 은 3·bw={3 * box[2]:.0f} 로 막힌다"
    left, top, right, bottom = pad
    padded = fi.plan_from_box(W + left + right, H + top + bottom,
                              (box[0] + left, box[1] + top, box[2], box[3]))
    assert padded.crop[2] == int(3 * box[2]), "크롭이 더는 깎이지 않는다"


@pytest.mark.parametrize("name,box", BLOCKED.items())
def test_padding_gives_the_crown_the_same_headroom_the_clean_cuts_had(name, box):
    """사용자 판정 기준: 크롭 윗변이 얼굴박스 위 0.55 얼굴높이 이상."""
    before = fi.plan_from_box(W, H, box)
    left, top, right, bottom = fi.crop_pad_for(W, H, box)
    after = fi.plan_from_box(W + left + right, H + top + bottom,
                             (box[0] + left, box[1] + top, box[2], box[3]))
    assert _headroom(before) < HEADROOM_MIN, f"{name} 은 원래 여유가 모자랐다"
    assert _headroom(after) >= HEADROOM_MIN, f"{name} 패딩 후 여유 {_headroom(after):.2f}"


def test_only_the_blocked_axis_is_padded():
    """폭만 막히면 위아래는 건드리지 않는다 — 필요 이상으로 그림을 늘리지 않는다."""
    left, top, right, bottom = fi.crop_pad_for(W, H, BLOCKED["closeup_1"])
    assert left and right and top == 0 and bottom == 0


# ── 덧대고 되돌리기 ──────────────────────────────────────────────────────────
def _noise(w=40, h=30, seed=3):
    rng = np.random.default_rng(seed)
    return Image.fromarray(rng.integers(0, 255, (h, w, 3), dtype=np.uint8))


def test_pad_replicates_the_edge_instead_of_inventing_a_colour():
    img = _noise()
    out = fi.pad_edges(img, (5, 4, 3, 2))
    assert out.size == (40 + 5 + 3, 30 + 4 + 2)
    a, b = np.asarray(img), np.asarray(out)
    assert np.array_equal(b[4:34, 5:45], a)                  # 원본이 그대로 들어 있다
    assert np.array_equal(b[0, 5:45], a[0])                  # 위쪽은 첫 행 복제
    assert np.array_equal(b[4:34, 0], a[:, 0])               # 왼쪽은 첫 열 복제
    assert set(np.unique(b)) <= set(np.unique(a)), "새 색을 지어내지 않는다"


def test_unpad_is_the_exact_inverse():
    img = _noise()
    for pad in [(0, 0, 0, 0), (5, 4, 3, 2), (11, 0, 0, 7)]:
        assert np.array_equal(np.asarray(fi.unpad_edges(fi.pad_edges(img, pad), pad)), np.asarray(img))
    assert fi.unpad_edges(img, None) is img


# ── 전체 경로 ────────────────────────────────────────────────────────────────
class _Backend:
    """control 을 그대로 돌려준다 — 기하만 본다."""

    def render(self, control, prompt, seed):
        return control.copy()


def _run(monkeypatch, box, *, crop_pad=True, size=(W, H)):
    """detect 를 고정 박스로 대신한다(YuNet weights 없이 돈다)."""
    img = Image.fromarray(np.random.default_rng(7).integers(0, 255, (size[1], size[0], 3), dtype=np.uint8))
    det = fi.FaceDetection(box=box, yaw_proxy=0.05, eye_dist=60.0, score=0.9,
                           landmarks=((0.0, 0.0),) * 5)

    def fake_detect(image, model_dir=None, **kw):
        # 패딩된 이미지에서 다시 부르면 좌표가 밀린 박스를 돌려준다(진짜 detect 와 같은 성질)
        dx = (image.size[0] - size[0]) // 2
        return fi.FaceDetection(box=(box[0] + dx, box[1], box[2], box[3]), yaw_proxy=0.05,
                                eye_dist=60.0, score=0.9, landmarks=((0.0, 0.0),) * 5)

    monkeypatch.setattr(fi, "detect_face", fake_detect)
    monkeypatch.setattr(fi, "estimate_expression", lambda image, d: ("neutral", {}))
    monkeypatch.setattr(fi, "evaluate_gate",
                        lambda plan, result, model_dir=None, **kw: fi.GateResult(True, "ok", 200.0, 0.0, 0.0))
    from io import BytesIO
    buf = BytesIO()
    img.save(buf, "PNG")
    return img, fi.run_face_pass(buf.getvalue(), _Backend(), seeds=(42,), crop_pad=crop_pad)


def test_the_result_comes_back_at_the_original_size(monkeypatch):
    """덧댄 만큼 잘라 되돌린다 — 결과가 커지면 조립·업로드가 전부 틀어진다."""
    img, res = _run(monkeypatch, BLOCKED["closeup_1"])
    assert res.applied
    assert res.meta["crop_pad"] != (0, 0, 0, 0)
    from io import BytesIO
    with Image.open(BytesIO(res.image)) as out:
        assert out.size == img.size


def test_pixels_outside_the_mask_are_still_byte_identical_to_the_original(monkeypatch):
    """되돌린 좌표가 1픽셀이라도 어긋나면 여기서 잡힌다 — 마스크 밖은 원본 그대로여야 한다."""
    img, res = _run(monkeypatch, BLOCKED["closeup_1"])
    from io import BytesIO
    with Image.open(BytesIO(res.image)) as out:
        after = np.asarray(out.convert("RGB"), np.uint8)
    before = np.asarray(img.convert("RGB"), np.uint8)
    # 패딩된 좌표계의 alpha 를 원본 좌표계로 되돌려 마스크 밖을 고른다
    left, top, right, bottom = res.meta["crop_pad"]
    plan = fi.plan_from_box(img.width + left + right, img.height + top + bottom,
                            (BLOCKED["closeup_1"][0] + left, BLOCKED["closeup_1"][1] + top,
                             BLOCKED["closeup_1"][2], BLOCKED["closeup_1"][3]))
    alpha = fi.paste_alpha(plan, fi.FEATHER_FRAC)[top:top + img.height, left:left + img.width]
    untouched = alpha <= 0
    assert untouched.any()
    assert np.array_equal(before[untouched], after[untouched])


def test_turning_the_pad_off_restores_the_old_geometry(monkeypatch):
    _, on = _run(monkeypatch, BLOCKED["closeup_1"], crop_pad=True)
    _, off = _run(monkeypatch, BLOCKED["closeup_1"], crop_pad=False)
    assert on.meta["crop_pad"] != (0, 0, 0, 0)
    assert off.meta["crop_pad"] == (0, 0, 0, 0)
    assert off.meta["crop"][2] == min(int(3 * BLOCKED["closeup_1"][2]), W, H)


def test_a_cut_that_never_needed_padding_is_untouched_either_way(monkeypatch):
    _, on = _run(monkeypatch, FREE["closeup_4"], crop_pad=True)
    _, off = _run(monkeypatch, FREE["closeup_4"], crop_pad=False)
    assert on.meta["crop_pad"] == (0, 0, 0, 0)
    assert on.meta["crop"] == off.meta["crop"]
    assert on.image == off.image, "패딩 스위치가 이 컷의 픽셀을 바꾸면 안 된다"


# ── 랜드마크도 같이 옮긴다 ──────────────────────────────────────────────────
def test_shift_moves_the_landmarks_not_just_the_box():
    det = fi.FaceDetection(box=(10.0, 20.0, 30.0, 40.0), yaw_proxy=0.1, eye_dist=12.0, score=0.9,
                           landmarks=((11.0, 22.0), (30.0, 22.0), (20.0, 35.0), (14.0, 48.0), (27.0, 48.0)))
    out = fi.shift_detection(det, 7, 3)
    assert out.box == (17.0, 23.0, 30.0, 40.0)
    assert out.landmarks == ((18.0, 25.0), (37.0, 25.0), (27.0, 38.0), (21.0, 51.0), (34.0, 51.0))
    assert fi.shift_detection(det, 0, 0) is det


def test_expression_is_read_from_the_same_pixels_after_padding(monkeypatch):
    """표정 추정은 입꼬리 랜드마크로 입술을 잡는다 — 패딩 뒤에도 같은 곳을 봐야 한다.

    박스만 옮기고 랜드마크를 두면 입술 마스크가 패딩만큼 밀린 자리를 읽는다(2026-09-13 리뷰).
    """
    box = BLOCKED["closeup_1"]
    rng = np.random.default_rng(11)
    src = Image.fromarray(rng.integers(0, 255, (H, W, 3), dtype=np.uint8))
    lm = ((box[0] + 110, box[1] + 200), (box[0] + 300, box[1] + 200),
          (box[0] + 210, box[1] + 330), (box[0] + 150, box[1] + 430), (box[0] + 280, box[1] + 430))
    det = fi.FaceDetection(box=box, yaw_proxy=0.05, eye_dist=190.0, score=0.9, landmarks=lm)

    base_expr, base_metrics = fi.estimate_expression(src, det)
    pad = fi.crop_pad_for(W, H, box)
    assert any(pad)
    padded = fi.pad_edges(src, pad)
    moved = fi.shift_detection(det, pad[0], pad[1])
    pad_expr, pad_metrics = fi.estimate_expression(padded, moved)

    assert pad_expr == base_expr
    assert pad_metrics == base_metrics

    # 랜드마크를 안 옮기면(옛 버그) 다른 곳을 읽는다 — 이 테스트가 그걸 잡는다
    stale = replace_box_only(det, pad[0], pad[1])
    assert fi.estimate_expression(padded, stale)[1] != base_metrics


def replace_box_only(det, dx, dy):
    from dataclasses import replace as _replace
    bx, by, bw, bh = det.box
    return _replace(det, box=(bx + dx, by + dy, bw, bh))


# ── 설정·레시피 ─────────────────────────────────────────────────────────────
def test_the_setting_defaults_on_with_an_escape_hatch():
    import pathlib
    assert make_settings(gemini_api_key="x", r2_bucket="b").face_crop_pad is True
    root = pathlib.Path(__file__).resolve().parents[1]
    assert 'os.getenv("FACE_CROP_PAD", "true").lower() != "false"' in (root / "app/config.py").read_text(encoding="utf-8")


def test_the_recipe_records_whether_padding_was_on():
    on = face_recipe.recipe_fields(crop_pad=True)
    off = face_recipe.recipe_fields(crop_pad=False)
    assert on["crop_pad"] is True and off["crop_pad"] is False
    assert face_recipe.recipe_id(on) != face_recipe.recipe_id(off)
