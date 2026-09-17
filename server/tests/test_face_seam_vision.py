import asyncio
from types import SimpleNamespace

import pytest
from PIL import Image


def _crop(*, base=True):
    return SimpleNamespace(
        current=Image.new("RGB", (80, 60), (100, 100, 100)),
        base=Image.new("RGB", (80, 60), (90, 90, 95)) if base else None,
    )


def _settings(model="gpt-5.4"):
    return SimpleNamespace(face_seam_vision_model=model, analysis_timeout_seconds=12.0, openai_api_key="sk-test")


def _plan_response():
    return {
        "observations": ["image-left collar binding has a doubled rib segment below the jaw"],
        "neck_collar_gap": True,
        "instruction": "Repair the image-left neck/collar seam while preserving the original rib binding width.",
        "damage_polygons": [[{"x": 250, "y": 600}, {"x": 430, "y": 600}, {"x": 430, "y": 780}, {"x": 250, "y": 780}]],
        "composition_polygons": [[{"x": 200, "y": 560}, {"x": 520, "y": 560}, {"x": 520, "y": 830}, {"x": 200, "y": 830}]],
        "align": False,
    }


def test_plan_repair_calls_gpt_with_two_1024_neck_crops_and_returns_pixel_plan(monkeypatch):
    from app.agents import face_seam_vision as sv
    from app.agents import vision_llm

    seen = {}

    async def fake_call(settings, model, prompt, images, schema, timeout, **kwargs):
        seen.update(model=model, prompt=prompt, images=images, schema=schema, timeout=timeout, kwargs=kwargs)
        kwargs["metadata"].update(requested_model="sk-private", returned_model="sk-private", usage={"total_tokens": 7, "bad": "secret"})
        return _plan_response()

    monkeypatch.setattr(vision_llm, "_call_gpt", fake_call)
    metadata = {}
    plan = asyncio.run(sv.plan_repair(_settings(), _crop(), metadata=metadata))

    assert seen["model"] == "gpt-5.4"
    assert seen["timeout"] == 180.0
    assert seen["kwargs"]["reasoning_effort"] == "high"
    assert seen["kwargs"]["image_detail"] == "high"
    assert len(seen["images"]) == 2
    assert all(im.mime == "image/png" for im in seen["images"])
    assert all(Image.open(__import__("io").BytesIO(im.data)).size == (1024, 1024) for im in seen["images"])
    assert "binding width" in seen["prompt"]
    assert plan.instruction.startswith("Repair the image-left")
    assert plan.damage_polygons == [[(20.0, 36.0), (34.4, 36.0), (34.4, 46.8), (20.0, 46.8)]]
    assert plan.composition_polygons == [[(16.0, 33.6), (41.6, 33.6), (41.6, 49.8), (16.0, 49.8)]]
    assert plan.align is False
    assert plan.feather == 7
    assert list(getattr(plan, "observations")) == ["image-left collar binding has a doubled rib segment below the jaw"]
    assert getattr(plan, "neck_collar_gap") is True
    assert metadata == {"duration_ms": metadata["duration_ms"], "requested_model": "gpt-5.4", "usage": {"total_tokens": 7}}


def test_plan_repair_rejects_missing_base_and_malicious_invalid_polygon(monkeypatch):
    from app.agents import face_seam_vision as sv
    from app.agents import vision_llm
    from app.agents.face_seam_repair import SeamRepairUnavailable

    with pytest.raises(SeamRepairUnavailable, match="vision_base_missing"):
        asyncio.run(sv.plan_repair(_settings(), _crop(base=False)))

    async def fake_call(*_args, **_kwargs):
        bad = _plan_response()
        bad["damage_polygons"] = [[{"x": "sk-secret", "y": 500}, {"x": 1, "y": 2}, {"x": 3, "y": 4}]]
        return bad

    monkeypatch.setattr(vision_llm, "_call_gpt", fake_call)
    with pytest.raises(SeamRepairUnavailable, match="vision_bad_response") as exc:
        asyncio.run(sv.plan_repair(_settings(), _crop()))
    assert "sk-secret" not in str(exc.value)


def test_verify_repair_rejects_width_double_pattern_and_records_boolean_metadata(monkeypatch):
    from app.agents import face_seam_vision as sv
    from app.agents import vision_llm

    async def fake_call(*_args, **kwargs):
        kwargs["metadata"].update(returned_model="gpt-5.4", usage={"input_tokens": 2, "bad": "secret"})
        return {
            "remaining_defects": ["candidate changes original binding width and leaves double rib pattern"],
            "garment_geometry_preserved": False,
            "skin_texture_preserved": True,
            "seamless": False,
            "uncertain": False,
        }

    monkeypatch.setattr(vision_llm, "_call_gpt", fake_call)
    metadata = {}
    ok = asyncio.run(sv.verify_repair(_settings(), _crop(), Image.new("RGB", (80, 60), (120, 120, 120)), metadata=metadata))
    assert ok is False
    assert metadata["garment_geometry_preserved"] is False
    assert metadata["skin_texture_preserved"] is True
    assert metadata["seamless"] is False
    assert metadata["uncertain"] is False
    assert metadata["remaining_defect_count"] == 1
    assert "remaining_defects" not in metadata


def test_provider_exception_is_fixed_reason_without_error_body(monkeypatch):
    from app.agents import face_seam_vision as sv
    from app.agents import vision_llm
    from app.agents.face_seam_repair import SeamRepairUnavailable

    async def fake_call(*_args, **_kwargs):
        raise RuntimeError("sk-live secret response body")

    monkeypatch.setattr(vision_llm, "_call_gpt", fake_call)
    with pytest.raises(SeamRepairUnavailable, match="vision_provider_error") as exc:
        asyncio.run(sv.plan_repair(_settings(), _crop(), metadata={}))
    assert "sk-live" not in str(exc.value)


def test_face_seam_vision_model_config_env(monkeypatch):
    from app.config import load_settings

    monkeypatch.delenv("FACE_SEAM_VISION_MODEL", raising=False)
    assert load_settings().face_seam_vision_model == "gpt-5.4"
    monkeypatch.setenv("FACE_SEAM_VISION_MODEL", "gpt-test")
    assert load_settings().face_seam_vision_model == "gpt-test"


def test_automatic_polygons_cannot_edit_unrelated_chest_or_background():
    from app.agents import face_seam_vision as sv
    from app.agents.face_seam_repair import SeamRepairUnavailable
    crop = SimpleNamespace(current=Image.new("RGB", (1000, 1000)), face_box=(350, 100, 200, 300))
    raw = _plan_response()
    raw["damage_polygons"] = [[{"x": 0, "y": 800}, {"x": 150, "y": 800}, {"x": 150, "y": 1000}, {"x": 0, "y": 1000}]]
    with pytest.raises(SeamRepairUnavailable, match="vision_polygon_outside_neck"):
        sv._validate_plan(raw, crop)
