"""합성 — 민무늬 배경에서 **바탕을 보존**한다(뜨는 띠 · 목 아래 이중 테두리).

운영 컷에서 두 가지가 보였다.
  ① 뜨는 띠   — 링 전역 보정이 얹힌 생성 배경이 바탕 배경과 미세하게 달라 머리 둘레로 후광이 진다.
  ② 이중 테두리 — 생성된 옷이 바탕 옷보다 커서 **바탕의 배경 위에** 덧칠된다. 바탕=배경·생성=옷이라
               ①의 규칙("둘 다 배경")에 걸리지 않는다.

여기 장면은 그 둘을 일부러 만든 합성본이다(사람 사진은 레포에 없다):
  바탕 = 민무늬 회색 + 머리(피부색 타원) + 턱 아래 옷(회색 사각형)
  생성 = 같은 크롭에서 얼굴색을 바꾸고 · 배경만 8 어둡게 하고 · 옷을 더 크게 그린 것

지키는 선 넷 — 하나라도 깨지면 이 변경은 되돌려야 한다:
  · 알파 0 영역(옷·나머지)은 원본과 **바이트 동일**
  · 타원 안 피부는 **켜기 전과 동일** (22.B 목 색이동 재발 금지 — 여기서는 색을 새로 만들지 않는다)
  · 조건부 grain 은 그대로 붙는다
  · 띠·유령은 눈에 띄게 줄어든다
"""

import cv2
import numpy as np
import pytest
from PIL import Image, ImageDraw

from app.agents import face_identity as fi

BACKDROP = (200, 200, 200)
SKIN = (214, 172, 148)
GARMENT = (150, 150, 152)
HAIR = (42, 34, 30)
#: 배경만 이만큼 어둡게 — 링(타원 테두리)은 머리를 주로 표본하므로 전역 보정이 이걸 못 지운다.
#: 운영 실측 color_shift 11.3 회차의 축소판이다.
GEN_BACKDROP_DROP = 8.0


def _scene():
    """바탕 600×800 + 그 얼굴 박스 계획. 배경에 약한 노이즈를 둔다(grain 조건이 살아 있게)."""
    rng = np.random.default_rng(11)
    arr = np.full((800, 600, 3), BACKDROP, np.float32)
    arr += rng.normal(0.0, 2.0, arr.shape)
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    box = (250.0, 300.0, 120.0, 160.0)
    draw = ImageDraw.Draw(img)
    bx, by, bw, bh = box
    draw.ellipse((bx - 0.2 * bw, by - 0.5 * bh, bx + 1.2 * bw, by + bh), fill=SKIN)   # 머리
    draw.rectangle((bx - 0.6 * bw, by + bh + 10, bx + 1.6 * bw, 800), fill=GARMENT)   # 턱 아래 옷
    plan = fi.plan_from_box(600, 800, box, yaw_proxy=0.05, eye_dist=50.0)
    return img, plan


def _generated(orig: Image.Image, plan: fi.FacePlan) -> Image.Image:
    """같은 크롭에서: 얼굴색 바꾸고 · 배경만 어둡게 · 옷을 더 크게(유령)."""
    crop = fi.crop_1024(orig, plan)
    arr = np.asarray(crop.convert("RGB"), np.float32)
    bg = fi._backdrop_color(arr)
    arr = arr - GEN_BACKDROP_DROP * fi._backdropness(arr, bg)[..., None]
    gen = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    draw = ImageDraw.Draw(gen)
    fx, fy, fw, fh = plan.face_box_crop
    draw.ellipse((fx - 0.2 * fw, fy - 0.5 * fh, fx + 1.2 * fw, fy + fh), fill=(206, 166, 146))
    # 바탕 옷보다 넓고 높게 — 바탕의 **배경** 위로 번진다(이중 테두리의 실체)
    draw.rectangle((fx - 1.0 * fw, fy + fh - 0.10 * fh, fx + 2.0 * fw, fi.CROP), fill=(144, 144, 148))
    return gen


def _zones(orig: Image.Image, plan: fi.FacePlan):
    """측정 구역 — 전부 **바탕 기준**이라 keep on/off 가 같은 픽셀을 본다."""
    up = np.asarray(fi.crop_1024(orig, plan).convert("RGB"), np.float32)
    bg = fi._backdrop_color(up)
    x0, y0, side = plan.crop
    alpha = fi.paste_alpha(plan, fi.FEATHER_FRAC)
    base_bg = np.zeros(alpha.shape, np.float32)
    base_bg[y0:y0 + side, x0:x0 + side] = np.asarray(
        Image.fromarray(fi._backdropness(up, bg)).resize((side, side), Image.BILINEAR), np.float32)
    band = (alpha > 0.02) & (alpha < 0.98) & (base_bg > 0.5)       # 전이대 ∩ 바탕이 배경 = 띠+유령
    skin = np.zeros(alpha.shape, bool)
    ell = np.asarray(fi.ellipse_mask(plan)) > 127
    small = np.asarray(Image.fromarray((ell * 255).astype(np.uint8)).resize((side, side), Image.NEAREST))
    skin[y0:y0 + side, x0:x0 + side] = small > 127
    # 턱선 **위** 로 자른다 — 타원 하단은 옷까지 내려가고, 그 옷은 새 규칙이 일부러 지키는 자리다.
    fx, fy, fw, fh = plan.face_box_crop
    chin = int(y0 + (fy + fh) * side / fi.CROP)
    rows = np.arange(alpha.shape[0])[:, None] < chin
    skin &= (base_bg < 0.2) & (alpha > 0.5) & rows                 # 타원 안 턱 위 비배경 = 얼굴·머리
    # 경계 램프를 뺀 **속살**만 본다. 배경 판정은 부드러운 램프라 머리 실루엣 바로 안쪽에서는
    # 값이 조금 깎일 수 있다(실측 1~2). 여기서 잠그는 건 "얼굴 속은 손대지 않는다" 다.
    skin = cv2.erode(skin.astype(np.uint8), np.ones((13, 13), np.uint8)).astype(bool)
    return alpha, band, skin


def _band_ratio(orig: Image.Image, result: Image.Image, band: np.ndarray) -> float:
    o = np.asarray(orig.convert("RGB"), np.float32)
    r = np.asarray(result.convert("RGB"), np.float32)
    d = np.abs(fi._luma(r) - fi._luma(o))
    return float((d[band] >= 3).mean())


def test_the_backdrop_band_all_but_disappears():
    """민무늬 배경 위 띠·유령이 문턱 아래로 내려간다 — 끄면 그대로 남는다(시험이 헛돌지 않게)."""
    orig, plan = _scene()
    gen = _generated(orig, plan)
    _alpha, band, _skin = _zones(orig, plan)
    assert band.sum() > 5000, "띠 구역이 비면 이 시험은 아무것도 안 본다"

    off = fi.composite(orig, gen, plan, keep_backdrop=False)
    on = fi.composite(orig, gen, plan, keep_backdrop=True)
    before, after = _band_ratio(orig, off, band), _band_ratio(orig, on, band)
    assert before > 0.20, f"켜기 전 띠가 이미 없다 — 장면이 문제를 재현하지 못한다({before:.3f})"
    assert after <= 0.02, f"띠 ≥3 비율 {after:.3f} — 문턱 0.02 초과"
    assert after < before / 5


def test_pixels_outside_the_mask_are_byte_identical():
    """알파 0 영역(옷·나머지)은 어느 쪽으로도 안 바뀐다."""
    orig, plan = _scene()
    gen = _generated(orig, plan)
    alpha, _band, _skin = _zones(orig, plan)
    outside = alpha <= 0.0
    assert outside.any()
    o = np.asarray(orig.convert("RGB"))
    for keep in (False, True):
        r = np.asarray(fi.composite(orig, gen, plan, keep_backdrop=keep).convert("RGB"))
        assert np.array_equal(r[outside], o[outside]), keep


def test_skin_is_not_touched_by_the_new_rule():
    """★ 22.B 재발 금지 — 이 변경은 색을 새로 만들지 않는다. 피부·머리는 켜기 전과 **같은 값**이다.

    22.B(국소 차분 필드)는 링 잔차를 줄였지만 목·턱을 노랗게 과보정해 기각됐다(목 색이동 38.7→65.6).
    여기서 바뀌는 건 알파뿐이고, 알파가 깎이는 자리는 '바탕이 배경' 또는 '턱 아래 비피부'뿐이라
    타원 안 피부는 판정에서 아예 빠진다.
    """
    orig, plan = _scene()
    gen = _generated(orig, plan)
    _alpha, _band, skin = _zones(orig, plan)
    assert skin.sum() > 5000
    off = np.asarray(fi.composite(orig, gen, plan, keep_backdrop=False).convert("RGB"), np.int16)
    on = np.asarray(fi.composite(orig, gen, plan, keep_backdrop=True).convert("RGB"), np.int16)
    assert np.abs(on[skin] - off[skin]).max() == 0


def test_conditional_grain_survives():
    """grain 은 알파를 타고 들어간다 — 알파를 깎다가 조건부 grain 을 잃으면 안 된다.

    장면은 노이즈 원본이다(민무늬가 아니라 keep 이 거의 1). 민무늬 배경에서는 보존된 배경이
    원본 입자를 그대로 갖고 오므로 고주파가 안 죽고 grain 조건 자체가 안 선다 — 그건 설계대로다.
    """
    rng = np.random.default_rng(7)
    orig = Image.fromarray(rng.integers(0, 256, size=(800, 600, 3), dtype=np.uint8))
    plan = fi.plan_from_box(600, 800, (250.0, 300.0, 120.0, 160.0), yaw_proxy=0.05, eye_dist=50.0)
    flat = Image.new("RGB", (fi.CROP, fi.CROP), (150, 150, 150))
    _, off = fi.composite_with_meta(orig, flat, plan, keep_backdrop=False)
    _, on = fi.composite_with_meta(orig, flat, plan, keep_backdrop=True)
    assert off["grain_applied"] is True and on["grain_applied"] is True
    assert on["hf_std_after_grain"] > on["hf_std_result"]
    _, forced_off = fi.composite_with_meta(orig, flat, plan, keep_backdrop=True, grain=False)
    assert forced_off["grain_applied"] is False and "hf_std_after_grain" not in forced_off


def test_meta_says_how_much_was_kept():
    """무엇이 왜 보존됐는지 메타에 남는다 — 안 남기면 "왜 이 컷만 다른가" 를 못 되짚는다."""
    orig, plan = _scene()
    _, meta = fi.composite_with_meta(orig, _generated(orig, plan), plan, keep_backdrop=True)
    assert 0.0 < meta["keep_backdrop_frac"] <= 1.0
    assert 0.0 < meta["keep_garment_frac"] <= 1.0
    _, off = fi.composite_with_meta(orig, _generated(orig, plan), plan, keep_backdrop=False)
    assert "keep_backdrop_frac" not in off


def test_the_keep_mask_never_raises_the_alpha():
    """보존은 **깎기만** 한다 — 알파가 커지면 마스크 밖 불변 계약이 깨진다."""
    orig, plan = _scene()
    up = np.asarray(fi.crop_1024(orig, plan).convert("RGB"), np.float32)
    gen = np.asarray(_generated(orig, plan).convert("RGB"), np.float32)
    keep, _meta = fi.keep_mask(up, gen, plan)
    assert keep.shape == (fi.CROP, fi.CROP)
    assert keep.min() >= 0.0 and keep.max() <= 1.0


def test_the_base_persons_hair_below_the_chin_is_not_protected():
    """★ 턱 아래 "피부가 아닌 것" 을 전부 지키면 **바탕 인물의 머리카락**까지 지킨다.

    실제 룩북 컷(coor_1 — 바탕 머리가 턱선 아래로 내려온 사진)에서 그것 때문에 남의 머리 가닥이
    남아 SFace 가 0.701 → 0.659 로 떨어졌다. 머리색을 얼굴 박스 위쪽에서 뽑아 옷 판정에서 뺀다.
    옷이 머리색과 비슷하면 그 자리는 보호를 잃을 뿐이라 안전한 방향으로 진다(= 지금 동작).
    """
    orig, plan = _scene()
    draw = ImageDraw.Draw(orig)
    bx, by, bw, bh = plan.box
    draw.ellipse((bx - 0.2 * bw, by - 0.5 * bh, bx + 1.2 * bw, by + 0.35 * bh), fill=HAIR)  # 정수리
    strand = (bx + 1.0 * bw, by + 0.9 * bh, bx + 1.25 * bw, by + 1.9 * bh)                  # 턱 아래로
    draw.rectangle(strand, fill=HAIR)
    gen = _generated(orig, plan)
    up = np.asarray(fi.crop_1024(orig, plan).convert("RGB"), np.float32)
    keep, meta = fi.keep_mask(up, np.asarray(gen.convert("RGB"), np.float32), plan)
    assert meta.get("keep_hair_excluded") is True, "머리색을 못 뽑았다 — 가드가 안 걸린다"

    s = fi.CROP / plan.crop[2]
    def _crop_box(b):
        return (int((b[0] - plan.crop[0]) * s), int((b[1] - plan.crop[1]) * s),
                int((b[2] - plan.crop[0]) * s), int((b[3] - plan.crop[1]) * s))
    hx0, hy0, hx1, hy1 = _crop_box(strand)
    hair_keep = keep[hy0 + 8:hy1 - 8, hx0 + 8:hx1 - 8]
    assert hair_keep.size and hair_keep.min() > 0.9, "바탕 머리 가닥을 지키면 남의 머리가 남는다"

    gx0, gy0, gx1, gy1 = _crop_box((bx - 0.5 * bw, by + 1.6 * bh, bx + 0.5 * bw, by + 2.2 * bh))
    garment_keep = keep[max(0, gy0):min(fi.CROP, gy1), max(0, gx0):min(fi.CROP, gx1)]
    assert garment_keep.size and garment_keep.max() < 0.1, "옷은 여전히 지켜야 한다"


def test_a_face_box_at_the_photo_edge_still_produces_a_mask():
    """크롭이 사진 경계에 물려 얼굴 박스가 1024 밖으로 삐져나가도 볼 표본 인덱스가 안 깨진다."""
    orig, _ = _scene()
    plan = fi.plan_from_box(600, 800, (2.0, 2.0, 120.0, 160.0), yaw_proxy=0.0, eye_dist=50.0)
    assert plan is not None
    up = np.asarray(fi.crop_1024(orig, plan).convert("RGB"), np.float32)
    keep, meta = fi.keep_mask(up, up.copy(), plan)
    assert keep.shape == (fi.CROP, fi.CROP) and 0.0 <= keep.min() <= keep.max() <= 1.0
    assert "keep_backdrop_frac" in meta


@pytest.mark.parametrize("feather", [0.12, 0.25])
def test_old_path_stays_available_for_rollback(feather):
    """keep_backdrop=False 는 옛 경로 그대로 — 되돌릴 자리가 한 줄이어야 한다."""
    orig, plan = _scene()
    gen = _generated(orig, plan)
    a = fi.composite(orig, gen, plan, feather=feather, keep_backdrop=False, grain=False)
    b = fi.composite(orig, gen, plan, feather=feather, keep_backdrop=True, grain=False)
    assert not np.array_equal(np.asarray(a), np.asarray(b))
