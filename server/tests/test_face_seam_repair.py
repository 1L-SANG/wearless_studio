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
    plan = fi.plan_from_box(image.width, image.height, (86.0, 48.0, 50.0, 70.0), yaw_proxy=0.0, eye_dist=24.0)
    mask = np.zeros((1024, 1024), bool)
    fx, fy, fw, fh = plan.face_box_crop
    mask[max(0, int(fy)):min(1024, int(fy+1.5*fh)), max(0, int(fx)):min(1024, int(fx+fw))] = True
    raw = sr.capture_repair_context(image, plan, gen_mask=mask, references=((1.0, 0.0),))
    return sr.FaceSeamContext(**raw)


def _repair_plan():
    from app.agents.face_seam_repair import RepairPlan
    return RepairPlan("Repair the broken collar seam.")


def test_band_crop_and_aligned_neck_context():
    from app.agents import face_seam_repair as sr
    image = _image()
    ctx = _context(image)
    crop = sr.prepare_repair_crop(image, ctx)
    wide = sr.prepare_repair_crop(image, ctx, gap=True)
    assert crop.current.width < wide.current.width
    assert np.array_equal(np.asarray(crop.current), np.asarray(crop.base))
    assert ctx.base_crop.size != image.size
    assert ctx.band_bounds[1] >= int(ctx.plan.box[1] + .55*ctx.plan.box[3])
    assert "PIL" not in repr(ctx)
    with pytest.raises(sr.SeamRepairUnavailable, match="generation_mask_missing"):
        sr.capture_repair_context(image, ctx.plan)
    from dataclasses import replace
    with pytest.raises(sr.SeamRepairUnavailable, match="base_crop_mismatch"):
        sr.prepare_repair_crop(image, replace(ctx, base_crop_box=(0, 0, 16)))


def test_blob_removes_thin_edges_keeps_qwen_changes_and_preserves_outside():
    from app.agents import face_seam_repair as sr
    size = 575
    cur = np.full((size, size, 3), 80, np.uint8)
    base = cur.copy()
    cur[400:450, 350:400] = 140  # Qwen changes must remain in the repair region.
    generated = cur.copy()
    generated[250:330, 200:280] = 230  # Real repair blob.
    generated[210:360, 430:433] = 255  # Thin shifted edge echo.
    crop = sr.RepairCrop(Image.fromarray(cur), (0, 0, size), (200, 50, 175, 150), (size, size),
                         base=Image.fromarray(base), skin=np.zeros((size, size), bool))
    alpha, support, stats = sr.blob_region(generated.astype(np.float32), crop)
    assert alpha[285, 240] == 1 and alpha[425, 370] > 0
    assert alpha[230, 432] == 0 and stats["thin_removed_px"] > 0
    assert not support[:132].any() and not support[:, :14].any()
    final, meta, full_support = sr.composite_repair(crop.current, crop, _repair_plan(), Image.fromarray(generated))
    assert meta["outside_changed"] == 0
    assert np.array_equal(np.asarray(final)[~full_support], cur[~full_support])
    assert not hasattr(sr, "align_generated_crop") and not hasattr(sr, "build_repair_masks")


def test_blob_protects_upper_face_skin_but_keeps_collar_and_empty_region_safe():
    from app.agents import face_seam_repair as sr
    size = 575
    cur = np.full((size, size, 3), 50, np.uint8)
    skin = np.zeros((size, size), bool)
    skin[140:190, 220:280] = True
    crop = sr.RepairCrop(Image.fromarray(cur), (0, 0, size), (200, 50, 175, 150), (size, size), base=Image.fromarray(cur), skin=skin)
    changed = np.full_like(cur, 200, dtype=np.float32)
    alpha, support, _ = sr.blob_region(changed, crop)
    assert alpha[160, 250] == 0 and alpha[160, 320] > 0
    assert alpha[80, 320] == 0
    empty_alpha, empty_support, _ = sr.blob_region(cur.astype(np.float32), crop)
    assert not empty_support.any() and not empty_alpha.any()


@pytest.fixture
def visual_calls(monkeypatch):
    from app.agents import face_seam_vision as vision
    async def plan(*args, **kwargs):
        return _repair_plan()
    async def verify(*args, **kwargs):
        return True
    monkeypatch.setattr(vision, "plan_repair", plan)
    monkeypatch.setattr(vision, "verify_repair", verify)


def test_call_sunburst_edit_uses_only_one_1024_crop_without_mask(monkeypatch):
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
    result = asyncio.run(sr.call_sunburst_edit(_settings(), "prompt", crop, http_post=fake_post))
    assert result.image.size == (1024, 1024)
    url, headers, data, files, _timeout = calls[0]
    assert url.endswith("/v1/images/edits")
    assert headers == {"Authorization": "Bearer sk-test"}
    assert data == {"model": "gpt-image-2.5-sunburst", "prompt": "prompt", "size": "1024x1024", "quality": "high", "output_format": "png", "n": "1"}
    assert [name for name, _payload in files] == ["image[]"]
    assert files[0][1][2] == "image/png"
    assert Image.open(BytesIO(files[0][1][1])).size == (1024, 1024)
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
    with pytest.raises(sr.SeamRepairUnavailable):
        asyncio.run(sr.call_sunburst_edit(_settings(), "prompt", crop, http_post=fake_post))
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
    with pytest.raises(sr.SeamRepairUnavailable, match="openai_bad_response"):
        asyncio.run(sr.call_sunburst_edit(_settings(), "prompt", crop, http_post=fake_small_image))

    async def fake_429(*_args, **_kwargs):
        return SimpleNamespace(status_code=429, text="sk-secret insufficient credits")

    with pytest.raises(sr.SeamRepairUnavailable, match="openai_http_429"):
        asyncio.run(sr.call_sunburst_edit(_settings(), "prompt", crop, http_post=fake_429))


def test_repair_modes_fail_closed_and_identity_rollback(visual_calls):
    from app.agents import face_seam_repair as sr

    qwen = _png(_image())

    async def fake_edit(_settings, _prompt, crop):
        edited = np.asarray(crop.current).copy()
        edited[:] = (120, 120, 130)
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

    async def fake_edit(_settings, _prompt, crop):
        edited = np.asarray(crop.current).copy()
        edited[:] = (120, 120, 130)
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

    def helper(original, plan, *, crop_pad, references, model_dir, neck_offset, gen_mask, current, tone_enabled):
        assert gen_mask is not None and gen_mask.shape == (1024, 1024)
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
    assert result.meta["mask_lock"] is False
    assert result.meta["seam_mask_source"] == "reconstructed"
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


def test_visual_rejection_is_metadata_only(monkeypatch, visual_calls):
    from app.agents import face_seam_repair as sr, face_seam_vision as vision
    calls = []
    async def edit(_settings, _prompt, crop):
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
    assert out.image != qwen and out.applied
    assert out.meta["face_seam"]["accepted"] is True
    assert out.meta["face_seam"]["visual_check"]["passed"] is False
    assert calls == ["edit", "verify"]


def test_visual_audit_exception_cannot_veto_valid_repair(monkeypatch, visual_calls):
    from app.agents import face_seam_repair as sr, face_seam_vision as vision
    async def edit(_settings, _prompt, crop):
        return sr.SunburstResult(Image.new("RGB", (1024, 1024), (20, 20, 20)), None, 1)
    async def unavailable(*args, **kwargs):
        raise RuntimeError("sk-secret")
    monkeypatch.setattr(vision, "verify_repair", unavailable)
    qwen = _png(_image())
    out = asyncio.run(sr.repair_after_face_pass(_settings(), fi.FacePassResult(qwen, "image/png", True, {}), _context(), edit=edit, identity_score=lambda _: .8))
    assert out.image != qwen and out.meta["face_seam"]["accepted"] is True
    assert out.meta["face_seam"]["visual_check"] == {"reason": "unavailable"}
    assert "sk-secret" not in repr(out.meta)
