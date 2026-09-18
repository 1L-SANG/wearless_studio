import asyncio
import inspect
from types import SimpleNamespace

import pytest
from PIL import Image


FALLBACK = (
    "Inside the editable area, repair any visible compositing seam, notch, step, torn-looking fragment, stray fabric shard or misaligned collar/neckline edge where the neck meets the garment, so the neck outline, skin and garment edge are continuous and natural. Preserve the same garment design, collar or neckline shape, stripes or pattern, knit, rib or denim texture, stitching, neck proportions and shadows."
)


def _crop(*, base=True):
    return SimpleNamespace(
        current=Image.new("RGB", (80, 60), (100, 100, 100)),
        base=Image.new("RGB", (80, 60), (90, 90, 95)) if base else None,
        face_box=(20.0, 10.0, 30.0, 20.0),
    )


def _settings(model="gpt-5.4"):
    return SimpleNamespace(face_seam_vision_model=model, analysis_timeout_seconds=12.0, openai_api_key="sk-test")


def _poly(offset=0):
    return [[[250 + offset, 600], [430 + offset, 600], [430 + offset, 780], [250 + offset, 780]]]


def _look_response(**overrides):
    raw = {
        "garment": "faded denim shirt with rear collar and stitching",
        "defects": [
            {"what": "rear right collar edge is cut and floating", "where": "image-right back neck"},
            {"what": "background-colored gap separates neck skin from fixed collar", "where": "image-left lower neck"},
        ],
        "edit_instruction": "Repair the rear right collar edge and image-left neck gap. Reconstruct continuous denim collar contact. Preserve faded denim stitching and open shirt shape.",
        "neck_collar_gap": True,
        "damage_polygons": _poly(),
    }
    raw.update(overrides)
    return raw


def _composition_response(**overrides):
    raw = {"composition_polygons": [[[200, 560], [520, 560], [520, 830], [200, 830]]]}
    raw.update(overrides)
    return raw


def test_plan_repair_returns_description_and_damage_polygons(monkeypatch):
    from app.agents import face_seam_vision as sv
    from app.agents import vision_llm

    seen = {}

    async def fake_call(settings, model, prompt, images, schema, timeout, **kwargs):
        seen.update(model=model, prompt=prompt, images=images, schema=schema, timeout=timeout, kwargs=kwargs)
        kwargs["metadata"].update(requested_model="sk-private", returned_model="sk-private", usage={"prompt_tokens": 7, "bad": "secret"})
        return _look_response()

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
    schema_text = repr(seen["schema"])
    assert "damage_polygons" in schema_text
    assert "composition_polygons" not in schema_text
    assert "align" not in schema_text
    assert "neck_collar_gap" in schema_text
    assert "prefixItems" not in schema_text
    assert "'items': {'type': 'number'" in schema_text
    assert "The person differs, but the garment in image 2 is the ground truth" in seen["prompt"]
    assert "background-colored gap" in seen["prompt"]
    assert "white cloth fragment" in seen["prompt"]
    assert "local redraw unit" in seen["prompt"]
    assert "nearby undamaged connecting context" in seen["prompt"]
    assert "local background-colored missing wedge" in seen["prompt"]
    assert "natural asymmetric perspective" in seen["prompt"]
    assert "Do not place damage_polygons above y=358" in seen["prompt"]
    assert "jaw/face skin above y=495" in seen["prompt"]
    assert "face x=237..659" in seen["prompt"]
    assert "object.__setattr__" not in inspect.getsource(sv._make_plan)
    assert plan.instruction.startswith("Repair the rear right collar edge")
    assert list(getattr(plan, "observations")) == [
        "image-right back neck: rear right collar edge is cut and floating",
        "image-left lower neck: background-colored gap separates neck skin from fixed collar",
    ]
    assert getattr(plan, "neck_collar_gap") is True
    assert getattr(plan, "garment", "") == "faded denim shirt with rear collar and stitching"
    assert getattr(plan, "damage_polygons") == _poly()
    assert metadata == {"duration_ms": metadata["duration_ms"], "requested_model": "gpt-5.4", "usage": {"prompt_tokens": 7}}


def test_plan_repair_uses_exact_fallback_instruction_when_no_defects(monkeypatch):
    from app.agents import face_seam_vision as sv
    from app.agents import vision_llm

    async def fake_call(*_args, **_kwargs):
        return _look_response(defects=[], edit_instruction="This should not be used.", neck_collar_gap=False, damage_polygons=[])

    monkeypatch.setattr(vision_llm, "_call_gpt", fake_call)
    plan = asyncio.run(sv.plan_repair(_settings(), _crop(), metadata={}))
    assert plan.instruction == FALLBACK
    assert list(getattr(plan, "observations")) == []
    assert getattr(plan, "neck_collar_gap") is False
    assert getattr(plan, "damage_polygons") == []


def test_plan_repair_rejects_missing_base_malformed_boolean_and_unsafe_polygon(monkeypatch):
    from app.agents import face_seam_vision as sv
    from app.agents import vision_llm
    from app.agents.face_seam_repair import SeamRepairUnavailable

    with pytest.raises(SeamRepairUnavailable, match="vision_base_missing"):
        asyncio.run(sv.plan_repair(_settings(), _crop(base=False)))

    async def bad_bool(*_args, **_kwargs):
        return _look_response(neck_collar_gap="yes")

    monkeypatch.setattr(vision_llm, "_call_gpt", bad_bool)
    with pytest.raises(SeamRepairUnavailable, match="vision_bad_response"):
        asyncio.run(sv.plan_repair(_settings(), _crop()))

    async def bad_poly(*_args, **_kwargs):
        return _look_response(damage_polygons=[[["sk-secret", 1], [2, 3], [4, 5]]])

    monkeypatch.setattr(vision_llm, "_call_gpt", bad_poly)
    with pytest.raises(SeamRepairUnavailable, match="vision_bad_response") as exc:
        asyncio.run(sv.plan_repair(_settings(), _crop()))
    assert "sk-secret" not in str(exc.value)


def test_plan_composition_returns_strict_composition_polygons(monkeypatch):
    from app.agents import face_seam_vision as sv
    from app.agents import vision_llm

    plan = SimpleNamespace(
        instruction="Repair the denim collar seam.",
        observations=["image-right back neck: cut collar"],
        neck_collar_gap=False,
        garment="denim shirt",
        damage_polygons=_poly(),
    )
    seen = {}

    async def fake_call(settings, model, prompt, images, schema, timeout, **kwargs):
        seen.update(model=model, prompt=prompt, images=images, schema=schema, timeout=timeout, kwargs=kwargs)
        kwargs["metadata"].update(usage={"completion_tokens": 3, "bad": "secret"})
        return _composition_response()

    monkeypatch.setattr(vision_llm, "_call_gpt", fake_call)
    metadata = {}
    out = asyncio.run(sv.plan_composition(_settings(), _crop(), Image.new("RGB", (80, 60), (120, 120, 120)), plan, metadata=metadata))

    assert out == [[[200, 560], [520, 560], [520, 830], [200, 830]]]
    assert len(seen["images"]) == 2
    assert "original damage_polygons" in seen["prompt"]
    assert "continuous repair unit" in seen["prompt"]
    assert "same-frame neck repair crops" in seen["prompt"]
    assert "small drift" in seen["prompt"]
    assert "input crop coordinate system" in seen["prompt"]
    assert "Do not place composition_polygons above y=358" in seen["prompt"]
    assert "face x=237..659" in seen["prompt"]
    schema_text = repr(seen["schema"])
    assert "composition_polygons" in schema_text
    assert "damage_polygons" not in schema_text
    assert metadata["usage"] == {"completion_tokens": 3}


def test_plan_composition_allows_empty_region_and_rejects_malformed(monkeypatch):
    from app.agents import face_seam_vision as sv
    from app.agents import vision_llm
    from app.agents.face_seam_repair import SeamRepairUnavailable

    plan = SimpleNamespace(instruction="Repair.", observations=[], neck_collar_gap=False, garment="shirt", damage_polygons=[])

    async def empty_call(*_args, **_kwargs):
        return {"composition_polygons": []}

    monkeypatch.setattr(vision_llm, "_call_gpt", empty_call)
    assert asyncio.run(sv.plan_composition(_settings(), _crop(), Image.new("RGB", (80, 60)), plan)) == []

    async def bad_call(*_args, **_kwargs):
        return {"composition_polygons": [[[-1, 1], [2, 3], [4, 5]]]}

    monkeypatch.setattr(vision_llm, "_call_gpt", bad_call)
    with pytest.raises(SeamRepairUnavailable, match="vision_bad_response"):
        asyncio.run(sv.plan_composition(_settings(), _crop(), Image.new("RGB", (80, 60)), plan))


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
