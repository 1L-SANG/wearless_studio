import asyncio
import base64
from dataclasses import asdict
from io import BytesIO
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from app.agents import face_identity as fi


def _png(image: Image.Image) -> bytes:
    buf = BytesIO()
    image.save(buf, "PNG")
    return buf.getvalue()


def _settings(mode="on"):
    return SimpleNamespace(
        face_seam_repair=mode,
        face_seam_repair_model="gpt-image-2.5-sunburst",
        analysis_timeout_seconds=30.0,
        openai_api_key="sk-test",
    )


def _image(size=(220, 260)):
    arr = np.full((size[1], size[0], 3), (132, 126, 118), np.uint8)
    arr[80:150, 88:132] = (228, 184, 158)
    arr[150:198, 70:150] = (96, 96, 102)
    arr[164:178, 70:88] = (245, 245, 245)
    return Image.fromarray(arr)


def _context(image=None):
    from app.agents import face_seam_repair as sr

    image = image or _image()
    plan = fi.plan_from_box(image.width, image.height, (86.0, 78.0, 50.0, 70.0), yaw_proxy=0.0, eye_dist=24.0)
    return sr.FaceSeamContext(plan=plan, crop_pad=(0, 0, 0, 0), references=((1.0, 0.0),), model_dir=None)


def _repair_plan():
    from app.agents.face_seam_repair import RepairPlan

    return RepairPlan(
        has_defect=True,
        instruction="Repair the broken collar seam.",
        damage_polygons=[[(70, 164), (92, 164), (92, 178), (70, 178)]],
        composition_polygons=[[(60, 150), (156, 150), (156, 205), (60, 205)]],
        align=True,
        feather=7,
    )


def test_repair_plan_is_dataclass_json_shape():
    data = asdict(_repair_plan())
    assert data["has_defect"] is True
    assert data["feather"] == 7


def test_capture_repair_context_preserves_aligned_base_pixels():
    from app.agents import face_seam_repair as sr
    image = _image()
    context = _context(image)
    padded = Image.new("RGB", (image.width + 20, image.height + 28))
    padded.paste(image, (7, 11))
    padded_plan = SimpleNamespace(box=(93, 89, 50, 70))
    raw = sr.capture_repair_context(padded, padded_plan, crop_pad=(7, 11, 13, 17))
    crop = sr.prepare_repair_crop(image, sr.FaceSeamContext(**raw))
    assert np.array_equal(np.asarray(crop.current), np.asarray(crop.base))
    assert crop.base.size != image.size
    assert "PIL" not in repr(sr.FaceSeamContext(**raw))
    raw["base_crop_box"] = (0, 0, 16)
    with pytest.raises(sr.SeamRepairUnavailable, match="base_crop_mismatch"):
        sr.prepare_repair_crop(image, sr.FaceSeamContext(**raw))


@pytest.fixture
def visual_calls(monkeypatch):
    from app.agents import face_seam_vision as vision
    async def plan(*args, **kwargs):
        return _repair_plan()
    async def verify(*args, **kwargs):
        return True
    monkeypatch.setattr(vision, "plan_repair", plan)
    monkeypatch.setattr(vision, "verify_repair", verify)


def test_build_masks_make_transparent_edit_region_and_preserve_rear_collar_margin():
    from app.agents import face_seam_repair as sr

    crop = sr.prepare_repair_crop(_image(), _context())
    masks = sr.build_repair_masks(crop, _repair_plan())
    rgba = np.asarray(masks.api_mask_rgba)
    assert rgba[170, 80, 3] == 0
    assert rgba[90, 110, 3] == 255
    assert masks.composition_mask[170, 80]
    assert masks.composition_mask[154, 145]


def test_build_masks_rejects_uncontained_damage_after_alpha_support():
    from app.agents import face_seam_repair as sr

    crop = sr.prepare_repair_crop(_image(), _context())
    bad = sr.RepairPlan(
        True,
        "Repair.",
        [[(70, 164), (92, 164), (92, 178), (70, 178)]],
        [[(100, 190), (120, 190), (120, 210), (100, 210)]],
        True,
        7,
    )
    with pytest.raises(sr.SeamRepairUnavailable, match="damage_outside_composition"):
        sr.build_repair_masks(crop, bad)


def test_align_generated_crop_recovers_synthetic_shift_inside_composition_mask():
    from app.agents import face_seam_repair as sr

    cur = np.full((96, 96, 3), 20, np.uint8)
    cur[38:58, 36:56] = 220
    generated = np.roll(cur, 4, axis=1)
    mask = np.zeros((96, 96), bool)
    mask[30:66, 28:68] = True
    aligned = np.asarray(sr.align_generated_crop(Image.fromarray(cur), Image.fromarray(generated), mask, enabled=True))
    before = np.abs(generated[38:58, 36:56].astype(int) - cur[38:58, 36:56].astype(int)).mean()
    after = np.abs(aligned[38:58, 36:56].astype(int) - cur[38:58, 36:56].astype(int)).mean()
    assert after < before


def test_call_sunburst_edit_uses_one_native_png_and_rgba_mask(monkeypatch):
    from app.agents import face_seam_repair as sr

    calls = []

    async def fake_post(url, *, headers, data, files, timeout):
        calls.append((url, headers, data, files, timeout))
        out = _png(Image.new("RGB", (1024, 1024), (1, 2, 3)))
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "data": [{"b64_json": base64.b64encode(out).decode()}],
                "usage": {
                    "input_tokens": 3,
                    "output_tokens": 5,
                    "total_tokens": 8,
                    "bad": "no",
                    "input_tokens_details": {"text_tokens": 1, "image_tokens": 2, "secret": 99},
                    "output_tokens_details": {"image_tokens": 5, "secret": 100},
                },
            },
        )

    recorded = []
    monkeypatch.setattr(sr.image_usage, "record", lambda **kw: recorded.append({**kw, "stage": sr.image_usage._ctx.get().stage}))
    crop = sr.prepare_repair_crop(_image(), _context())
    masks = sr.build_repair_masks(crop, _repair_plan())
    result = asyncio.run(sr.call_sunburst_edit(_settings(), "prompt", crop, masks, http_post=fake_post))
    assert result.image.size == (1024, 1024)
    url, headers, data, files, _timeout = calls[0]
    assert url.endswith("/v1/images/edits")
    assert headers == {"Authorization": "Bearer sk-test"}
    assert data == {"model": "gpt-image-2.5-sunburst", "prompt": "prompt", "size": "1024x1024", "quality": "high", "output_format": "png", "n": "1"}
    assert [name for name, _payload in files] == ["image[]", "mask"]
    assert files[0][1][2] == "image/png"
    assert files[1][1][2] == "image/png"
    assert recorded[0]["usage"] == {
        "input_tokens": 3,
        "output_tokens": 5,
        "total_tokens": 8,
        "input_tokens_details": {"text_tokens": 1, "image_tokens": 2},
        "output_tokens_details": {"image_tokens": 5},
    }
    assert recorded[0]["stage"] == "face_seam_repair"


def test_call_sunburst_edit_records_billable_malformed_200(monkeypatch):
    from app.agents import face_seam_repair as sr

    async def fake_post(*_args, **_kwargs):
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "data": [],
                "usage": {
                    "input_tokens": 4,
                    "total_tokens": 4,
                    "input_tokens_details": {"cached_tokens": 1, "bad": 2},
                    "bad": "secret",
                },
            },
        )

    recorded = []
    monkeypatch.setattr(sr.image_usage, "record", lambda **kw: recorded.append({**kw, "stage": sr.image_usage._ctx.get().stage}))
    crop = sr.prepare_repair_crop(_image(), _context())
    masks = sr.build_repair_masks(crop, _repair_plan())
    with pytest.raises(sr.SeamRepairUnavailable):
        asyncio.run(sr.call_sunburst_edit(_settings(), "prompt", crop, masks, http_post=fake_post))
    assert recorded == [{
        "model": "gpt-image-2.5-sunburst",
        "image_size": "1024x1024",
        "usage": {"input_tokens": 4, "total_tokens": 4, "input_tokens_details": {"cached_tokens": 1}},
        "latency_ms": recorded[0]["latency_ms"],
        "has_image": False,
        "stage": "face_seam_repair",
    }]


def test_call_sunburst_edit_rejects_non_1024_image_and_static_http_reason():
    from app.agents import face_seam_repair as sr

    async def fake_small_image(*_args, **_kwargs):
        out = _png(Image.new("RGB", (512, 1024), (1, 2, 3)))
        return SimpleNamespace(status_code=200, json=lambda: {"data": [{"b64_json": base64.b64encode(out).decode()}]})

    crop = sr.prepare_repair_crop(_image(), _context())
    masks = sr.build_repair_masks(crop, _repair_plan())
    with pytest.raises(sr.SeamRepairUnavailable, match="openai_bad_response"):
        asyncio.run(sr.call_sunburst_edit(_settings(), "prompt", crop, masks, http_post=fake_small_image))

    async def fake_429(*_args, **_kwargs):
        return SimpleNamespace(status_code=429, text="sk-secret insufficient credits")

    with pytest.raises(sr.SeamRepairUnavailable, match="openai_http_429"):
        asyncio.run(sr.call_sunburst_edit(_settings(), "prompt", crop, masks, http_post=fake_429))


def test_repair_modes_fail_closed_and_identity_rollback(visual_calls):
    from app.agents import face_seam_repair as sr

    qwen = _png(_image())

    async def fake_edit(_settings, _prompt, crop, _masks):
        edited = np.asarray(crop.current).copy()
        edited[160:190, 65:155] = (120, 120, 130)
        return sr.SunburstResult(Image.fromarray(edited), {
            "input_tokens": 3,
            "output_tokens": 5,
            "total_tokens": 8,
            "input_tokens_details": {"text_tokens": 1, "image_tokens": 2},
            "output_tokens_details": {"image_tokens": 5},
        }, 3)

    base = fi.FacePassResult(qwen, "image/png", True, {"identity": 0.8})
    out = asyncio.run(sr.repair_after_face_pass(_settings("off"), base, _context()))
    assert out is base
    assert out.image == qwen and out.meta == {"identity": 0.8}
    out = asyncio.run(sr.repair_after_face_pass(_settings("shadow"), fi.FacePassResult(qwen, "image/png", True, {"identity": 0.8}), _context(), edit=fake_edit, identity_score=lambda _im: 0.8))
    assert out.image == qwen and out.meta["face_seam"]["attempted"] is True and out.meta["face_seam"]["reason"] == "shadow"
    assert out.meta["face_seam"]["cost_usd"] == 0.000171
    scores = iter([0.8, 0.779999])
    out = asyncio.run(sr.repair_after_face_pass(_settings("on"), fi.FacePassResult(qwen, "image/png", True, {"identity": 0.8}), _context(), edit=fake_edit, identity_score=lambda _im: next(scores)))
    assert out.image == qwen and out.meta["face_seam"]["reason"] == "identity_drop"
    scores = iter([0.8, 0.78])
    out = asyncio.run(sr.repair_after_face_pass(_settings("on"), fi.FacePassResult(qwen, "image/png", True, {"identity": 0.8}), _context(), edit=fake_edit, identity_score=lambda _im: next(scores)))
    assert out.image != qwen and out.meta["face_seam"]["accepted"] is True
    scores = iter([0.8, 0.79])
    out = asyncio.run(sr.repair_after_face_pass(_settings("on"), fi.FacePassResult(qwen, "image/png", True, {"identity": 0.8}), _context(), edit=fake_edit, identity_score=lambda _im: next(scores)))
    assert out.image != qwen and out.meta["face_seam"]["accepted"] is True


def test_repair_rejects_nonfinite_identity_and_shadow_png_mismatch(monkeypatch, visual_calls):
    from app.agents import face_seam_repair as sr

    qwen = _png(_image())

    async def fake_edit(_settings, _prompt, crop, _masks):
        edited = np.asarray(crop.current).copy()
        edited[160:190, 65:155] = (120, 120, 130)
        return sr.SunburstResult(Image.fromarray(edited), {"total_tokens": 1}, 3)

    scores = iter([float("nan"), 0.8])
    out = asyncio.run(sr.repair_after_face_pass(_settings("on"), fi.FacePassResult(qwen, "image/png", True, {"identity": 0.8}), _context(), edit=fake_edit, identity_score=lambda _im: next(scores)))
    assert out.image == qwen
    assert out.meta["face_seam"]["reason"] == "identity_missing"
    assert "nan" not in repr(out.meta).lower()

    monkeypatch.setattr(sr.np, "array_equal", lambda *_args, **_kwargs: False)
    out = asyncio.run(sr.repair_after_face_pass(_settings("shadow"), fi.FacePassResult(qwen, "image/png", True, {"identity": 0.8}), _context(), edit=fake_edit, identity_score=lambda _im: 0.8))
    assert out.image == qwen
    assert out.meta["face_seam"]["reason"] == "repair_unavailable"


def test_provider_errors_keep_qwen_without_secret_metadata(visual_calls):
    from app.agents import face_seam_repair as sr

    async def bad_edit(*_args, **_kwargs):
        raise RuntimeError("sk-live secret https://signed.example/body")

    qwen = _png(_image())
    out = asyncio.run(sr.repair_after_face_pass(_settings("on"), fi.FacePassResult(qwen, "image/png", True, {"identity": 0.8}), _context(), edit=bad_edit))
    assert out.image == qwen
    assert out.meta["face_seam"]["reason"] == "repair_unavailable"
    assert "sk-live" not in repr(out.meta)
    assert "signed.example" not in repr(out.meta)


def test_apply_face_pass_records_seam_outcome_and_preserves_recipe(monkeypatch):
    async def fake_wait(*_args, **_kwargs):
        return ""

    def fake_run(*_args, **_kwargs):
        return fi.FacePassResult(
            _png(_image()),
            "image/png",
            True,
            {"identity": 0.8, "crop_upscale": {"applied": False}, "mask_lock": False, "skin_finish": "prod"},
            context={"plan": _context().plan, "crop_pad": (0, 0, 0, 0), "references": None, "model_dir": None},
        )

    async def fake_repair(_settings, result, _context=None, **kwargs):
        return fi.FacePassResult(result.image + b"x", result.mime, result.applied, {**result.meta, "face_seam": {"accepted": True}})

    monkeypatch.setattr(fi, "wait_for_backend", fake_wait)
    monkeypatch.setattr(fi, "resolve_backend", lambda *_args: object())
    monkeypatch.setattr(fi, "run_face_pass", fake_run)
    from app.agents import face_seam_repair as sr
    monkeypatch.setattr(sr, "repair_after_face_pass", fake_repair)
    outcome = {}
    image, mime = asyncio.run(fi.apply_face_pass(_settings("on"), b"input", "image/png", fi.FaceIdentitySpec("x"), outcome=outcome))
    assert image.endswith(b"x") and mime == "image/png"
    assert outcome["face_pass"] == "applied"
    assert outcome["face_recipe"]
    assert outcome["face_seam"]["accepted"] is True


def test_apply_face_pass_off_does_not_import_seam_or_change_outcome(monkeypatch):
    async def fake_wait(*_args, **_kwargs):
        return ""

    def fake_run(*_args, **kwargs):
        assert "capture_seam_context" not in kwargs
        return fi.FacePassResult(_png(_image()), "image/png", True, {"identity": 0.8, "skin_finish": "prod"})

    monkeypatch.setattr(fi, "wait_for_backend", fake_wait)
    monkeypatch.setattr(fi, "resolve_backend", lambda *_args: object())
    monkeypatch.setattr(fi, "run_face_pass", fake_run)
    from app.agents import face_seam_repair as sr
    monkeypatch.setattr(sr, "repair_after_face_pass", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("off called seam repair")))
    outcome = {}
    image, mime = asyncio.run(fi.apply_face_pass(_settings("off"), b"input", "image/png", fi.FaceIdentitySpec("x"), outcome=outcome))
    assert image and mime == "image/png"
    assert outcome["face_pass"] == "applied"
    assert "face_recipe" in outcome
    assert "face_seam" not in outcome


def test_worker_asset_metadata_copies_face_seam_outcome():
    root = __import__("pathlib").Path(__file__).resolve().parents[1]
    for rel in ("app/workers/detail_page_job.py", "app/workers/editor_image_job.py"):
        text = (root / rel).read_text(encoding="utf-8")
        assert '"face_seam": face_pass_outcome["face_seam"]' in text
    assert '"face_recipe": face_pass_outcome["face_recipe"]' in (root / "app/workers/editor_image_job.py").read_text(encoding="utf-8")

class _SeamCaptureBackend:
    def render(self, control, prompt, seed):
        return control.copy()


def _run_face_pass_for_seam_capture(monkeypatch, *, helper):
    from app.agents import face_seam_repair as sr

    image = _image((600, 800))
    det = fi.FaceDetection(
        box=(250.0, 260.0, 130.0, 170.0),
        yaw_proxy=0.04,
        eye_dist=64.0,
        score=0.95,
        landmarks=((0.0, 0.0),) * 5,
    )
    monkeypatch.setattr(fi, "detect_face", lambda *_args, **_kwargs: det)
    monkeypatch.setattr(fi, "estimate_expression", lambda *_args, **_kwargs: ("neutral", {}))
    monkeypatch.setattr(fi, "evaluate_gate", lambda *_args, **_kwargs: fi.GateResult(True, "ok", 120.0, 0.0, 0.0, identity=0.91))
    monkeypatch.setattr(fi, "neck_offset_meta", lambda *_args, **_kwargs: 0.123)
    monkeypatch.setattr(sr, "capture_repair_context", helper, raising=False)
    return fi.run_face_pass(
        _png(image),
        _SeamCaptureBackend(),
        seeds=(7,),
        references=((1.0, 0.0),),
        model_dir="/tmp/model-dir",
        crop_pad=True,
        capture_seam_context=True,
    )


def test_run_face_pass_capture_seam_context_uses_helper_result(monkeypatch):
    calls = []

    def helper(original, plan, *, crop_pad, references, model_dir, neck_offset):
        calls.append((original.size, plan.box, crop_pad, references, model_dir, neck_offset))
        return {
            "plan": plan,
            "crop_pad": crop_pad,
            "references": references,
            "model_dir": model_dir,
            "neck_offset": neck_offset,
            "base_crop": "sentinel-crop",
            "base_crop_box": (1, 2, 3),
        }

    result = _run_face_pass_for_seam_capture(monkeypatch, helper=helper)

    assert result.applied is True
    assert result.context["base_crop"] == "sentinel-crop"
    assert result.context["base_crop_box"] == (1, 2, 3)
    assert result.context["neck_offset"] == 0.123
    assert calls and calls[0][2] == result.meta["crop_pad"]
    assert calls[0][3] == ((1.0, 0.0),)
    assert calls[0][4] == "/tmp/model-dir"


def test_run_face_pass_capture_seam_context_failure_keeps_applied_result(monkeypatch):
    def helper(*_args, **_kwargs):
        raise RuntimeError("helper failed with private image data")

    result = _run_face_pass_for_seam_capture(monkeypatch, helper=helper)

    assert result.applied is True
    assert result.context is None
    assert result.meta["reason"] == "ok"
    assert result.image


def test_visual_rejection_never_replaces_qwen(monkeypatch, visual_calls):
    from app.agents import face_seam_repair as sr, face_seam_vision as vision
    calls = []
    async def edit(_settings, _prompt, crop, _masks):
        calls.append("edit")
        return sr.SunburstResult(Image.new("RGB", crop.current.size, "red"), None, 1)
    async def reject(_settings, crop, candidate, **kwargs):
        calls.append("verify")
        assert candidate.size == crop.current.size
        return False
    monkeypatch.setattr(vision, "verify_repair", reject)
    qwen = _png(_image())
    base = fi.FacePassResult(qwen, "image/png", True, {})
    out = asyncio.run(sr.repair_after_face_pass(_settings(), base, _context(), edit=edit, identity_score=lambda _: .8))
    assert out.image == qwen and out.applied
    assert out.meta["face_seam"]["reason"] == "visual_reject"
    assert calls == ["edit", "verify"]
