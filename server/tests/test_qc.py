"""qc 2-레짐 판정 회귀 테스트 (2026-07-04 캘리브레이션 — scripts/qc_calibrate.py 실측 근거).

핵심 회귀: 흰옷·호리존 같은 저대비 정상 이미지를 유령으로 오판하지 않으면서(모노톤 스와치 실존),
유령·크롭·저해상 실패모드는 계속 잡는다. crop_zoom(프레임 꽉 채운 상반신 줌)은 픽셀 기하로
원리적 구분 불가 — AG-P2(의미 검수) 몫이라 여기서 다루지 않는다.
"""

import io

from PIL import Image, ImageDraw

from app.services import qc

BG_WHITE = (248, 246, 244)


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _rgba_png(img: Image.Image, *, background_alpha: int = 255) -> bytes:
    rgba = img.convert("RGBA")
    alpha = Image.new("L", rgba.size, background_alpha)
    rgba.putalpha(alpha)
    return _png(rgba)


def _figure(
    size=(700, 1050),
    bg=BG_WHITE,
    fill=(60, 70, 120),
    outline=None,
    body_bottom=0.97,
) -> Image.Image:
    """전신 실루엣 근사 — 세로 기둥(머리~발). outline 주면 저대비 흰옷형(윤곽선만 전경)."""
    w, h = size
    img = Image.new("RGB", size, bg)
    d = ImageDraw.Draw(img)
    d.rectangle(
        [int(w * 0.40), int(h * 0.06), int(w * 0.60), int(h * body_bottom)],
        fill=fill,
        outline=outline,
        width=6,
    )
    return img


def _white_solid() -> Image.Image:
    # 몸통은 배경과 diff<28(저대비), 윤곽선·음영만 전경 — 흰옷 정상의 실측 프로파일(fg≈0.01) 재현
    return _figure(fill=(233, 231, 229), outline=(205, 203, 201))


def _blend_bg(img: Image.Image, alpha: float) -> Image.Image:
    bg = Image.new("RGB", img.size, BG_WHITE)
    return Image.blend(bg, img, alpha)


def test_white_low_contrast_solid_passes():
    r = qc.evaluate_mannequin_qc(_png(_white_solid()))
    assert r.verdict == "pass", r.reasons


def test_white_ghost_caught():
    r = qc.evaluate_mannequin_qc(_png(_blend_bg(_white_solid(), 0.2)))
    assert r.verdict == "retry"
    assert "ghost_or_artifact" in r.reasons


def test_colored_solid_passes():
    r = qc.evaluate_mannequin_qc(_png(_figure()))
    assert r.verdict == "pass", r.reasons


def test_colored_ghost_caught():
    r = qc.evaluate_mannequin_qc(_png(_blend_bg(_figure(), 0.25)))
    assert r.verdict == "retry"
    assert "ghost_or_artifact" in r.reasons


def test_missing_lower_body_caught():
    r = qc.evaluate_mannequin_qc(_png(_figure(body_bottom=0.80)))
    assert r.verdict == "retry"
    assert "missing_lower_body" in r.reasons


def test_too_small_caught():
    r = qc.evaluate_mannequin_qc(_png(_figure(size=(400, 600))))
    assert r.verdict == "retry"
    assert "too_small" in r.reasons


def test_bad_aspect_caught():
    r = qc.evaluate_mannequin_qc(_png(_figure(size=(900, 900))))
    assert r.verdict == "retry"
    assert "bad_aspect_ratio" in r.reasons


def test_rgb_canvas_with_sheer_looking_color_passes_alpha_check():
    """시스루 표현은 RGB 색으로 배경과 섞여 보여도 픽셀 자체는 불투명이다."""
    img = _figure(fill=(228, 224, 222), outline=(180, 176, 174))
    r = qc.evaluate_canvas_alpha_qc(_png(img))
    assert r.verdict == "pass"
    assert r.metrics["hasAlphaSource"] is False


def test_opaque_rgba_canvas_passes_alpha_check():
    r = qc.evaluate_canvas_alpha_qc(_rgba_png(_figure(), background_alpha=255))
    assert r.verdict == "pass"
    assert r.metrics["hasAlphaSource"] is True
    assert r.metrics["transparentPixelCount"] == 0


def test_transparent_background_is_rejected_even_when_subject_is_opaque():
    img = _figure().convert("RGBA")
    alpha = Image.new("L", img.size, 0)
    d = ImageDraw.Draw(alpha)
    w, h = img.size
    d.rectangle([int(w * 0.40), int(h * 0.06), int(w * 0.60), int(h * 0.97)], fill=255)
    img.putalpha(alpha)

    r = qc.evaluate_canvas_alpha_qc(_png(img))
    assert r.verdict == "retry"
    assert r.reasons == ["transparent_canvas"]
    assert r.metrics["transparentPixelCount"] > 0
    assert r.metrics["alphaMin"] == 0


def test_semitransparent_garment_pixels_are_rejected_not_mistaken_for_sheerness():
    """Native garment sheerness must already be composited into RGB, never encoded as alpha."""
    img = _figure().convert("RGBA")
    alpha = Image.new("L", img.size, 255)
    d = ImageDraw.Draw(alpha)
    w, h = img.size
    d.rectangle([int(w * 0.40), int(h * 0.25), int(w * 0.60), int(h * 0.60)], fill=160)
    img.putalpha(alpha)

    r = qc.evaluate_canvas_alpha_qc(_png(img))
    assert r.verdict == "retry"
    assert r.reasons == ["transparent_canvas"]


def test_full_mannequin_qc_reports_transparent_canvas_before_rgb_flattening():
    img = _figure().convert("RGBA")
    img.putalpha(Image.new("L", img.size, 128))
    r = qc.evaluate_mannequin_qc(_png(img))
    assert r.verdict == "retry"
    assert r.reasons == ["transparent_canvas"]
