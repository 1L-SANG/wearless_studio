from types import SimpleNamespace

import numpy as np
from PIL import Image

from app.agents import face_identity as fi


def _plan():
    return fi.plan_from_box(260, 300, (90.0, 48.0, 80.0, 100.0), yaw_proxy=0.0, eye_dist=40.0)


def _tone_images():
    original = np.full((300, 260, 3), (92, 92, 98), np.uint8)
    current = original.copy()
    # Face/reference skin.
    original[48:148, 90:170] = (222, 176, 148)
    current[48:148, 90:170] = (222, 176, 148)
    # Body reference below y + 1.6h.
    original[212:292, 70:190] = (242, 188, 158)
    current[212:292, 70:190] = (242, 188, 158)
    # Current neck drift, already changed by the face pass/repair.
    current[150:195, 98:162] = (202, 154, 132)
    return Image.fromarray(original), Image.fromarray(current)


def test_prepare_tone_context_uses_body_reference_without_retaining_original_rgb():
    from app.agents import face_tone

    original, current = _tone_images()
    context = face_tone.prepare_tone_context(original, current, _plan())

    assert context.reference == "body"
    assert context.body_ratio >= 0.05
    assert context.target_lab is not None and len(context.target_lab) == 3
    assert context.raw_changed.dtype == bool
    assert context.raw_changed.any()
    assert "raw_changed" not in repr(context)
    assert not hasattr(context, "original")
    assert not hasattr(context, "current")


def test_prepare_tone_context_falls_back_to_original_face_neck_skin():
    from app.agents import face_tone

    original, current = _tone_images()
    arr = np.asarray(original).copy()
    arr[212:292, 70:190] = (30, 80, 210)
    context = face_tone.prepare_tone_context(Image.fromarray(arr), current, _plan())

    assert context.reference == "face_neck"
    assert context.body_ratio < 0.05
    assert context.target_lab is not None


def test_apply_tone_changes_only_support_pixels_and_caps_shift():
    from app.agents import face_tone

    original, current = _tone_images()
    context = face_tone.prepare_tone_context(original, current, _plan())
    final_arr = np.asarray(current).copy()
    final_arr[152:192, 100:160] = (196, 148, 126)
    final = Image.fromarray(final_arr)
    base = np.asarray(original.crop((70, 120, 190, 240))).copy()
    repair_crop = SimpleNamespace(base=Image.fromarray(base), box=(70, 120, 120))

    fixed, meta, support = face_tone.apply_tone(final, context, repair_crop)

    fixed_arr = np.asarray(fixed)
    assert meta["ok"] is True
    assert meta["reference"] == "body"
    assert meta["support_pixels"] >= 200
    assert abs(meta["applied"][0]) <= 12.0
    assert max(abs(v) for v in meta["applied"][1:]) <= 2.0
    assert support.dtype == bool and support.shape == final_arr.shape[:2]
    assert np.array_equal(fixed_arr[~support], final_arr[~support])
    assert np.any(fixed_arr[support] != final_arr[support])


def test_apply_tone_no_support_is_exact_noop():
    from app.agents import face_tone

    original, current = _tone_images()
    context = face_tone.prepare_tone_context(original, original.copy(), _plan())
    final = original.copy()
    repair_crop = SimpleNamespace(base=original.crop((70, 120, 190, 240)), box=(70, 120, 120))

    fixed, meta, support = face_tone.apply_tone(final, context, repair_crop)

    assert meta["ok"] is False
    assert meta["reason"] == "empty_support"
    assert not support.any()
    assert np.array_equal(np.asarray(fixed), np.asarray(final))


def test_skin_mask_empty_when_reference_missing(monkeypatch):
    from app.agents import face_tone

    image = Image.fromarray(np.zeros((300, 260, 3), np.uint8))
    monkeypatch.setattr(fi, "_skin_reference", lambda *_args, **_kwargs: None)

    mask = face_tone.skin_mask(image, _plan())

    assert mask.shape == (300, 260)
    assert mask.dtype == bool
    assert not mask.any()


def test_finish_repair_keeps_pixels_outside_seam_and_tone_support_exact():
    from app.agents import face_seam_repair as seam, face_tone

    original, current = _tone_images()
    pixels = np.asarray(current).copy()
    pixels[50:125, 100:160] = (202, 154, 132)
    current = Image.fromarray(pixels)
    context = SimpleNamespace(tone_context=face_tone.prepare_tone_context(original, current, _plan()))
    crop = seam.RepairCrop(
        current=current.crop((70, 120, 190, 240)), box=(70, 120, 120),
        face_box=(20, -72, 80, 100), final_size=current.size,
        base=original.crop((70, 120, 190, 240)),
        skin=face_tone.skin_mask(current, _plan())[120:240, 70:190],
    )
    fixed, meta, support = seam.finish_repair(
        current, context, crop, seam.RepairPlan("Repair neckline", damage_polygons=[[[200, 450], [800, 450], [800, 850], [200, 850]]], composition_polygons=[[[180, 430], [820, 430], [820, 870], [180, 870]]]), crop.current, tone_enabled=True,
    )
    assert meta["tone"]["ok"] is True
    assert meta["seam_outside_changed"] == meta["outside_changed"] == 0
    changed = np.any(np.asarray(fixed) != np.asarray(current), axis=2)
    assert changed[:120].any()  # Tone also covers changed facial skin above the neck ROI.
    assert np.array_equal(np.asarray(fixed)[~support], np.asarray(current)[~support])


def test_face_tone_fix_config_default_and_env(monkeypatch):
    from app.config import load_settings

    monkeypatch.delenv("FACE_TONE_FIX", raising=False)
    assert load_settings().face_tone_fix == "off"
    monkeypatch.setenv("FACE_TONE_FIX", "on")
    assert load_settings().face_tone_fix == "on"
