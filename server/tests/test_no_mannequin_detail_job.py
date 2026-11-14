"""Exercise preparation, confirmed generation and saved variant using local IO fakes."""

import asyncio
from hashlib import sha256

import pytest

from storyboard_v4_cases import v4_blocks

from app.agents.confirmed_gpt_prompt import compile_confirmed_gpt_prompt
from app.workers import detail_page_job as worker
from conftest import fake_worker_app, make_settings, worker_job
from test_confirmed_gpt_runtime import _contract, _directing, _png
from test_cut_input_authority import _patch_detail_terminal
from test_detail_incomplete_cuts_partial import _R2


def _run(monkeypatch, *, selected_mannequin, missing_mannequin_asset=False, color_id="base"):
    captured = {"generation": [], "calls": []}
    source = _png()
    target = _png("blue")
    analysis = {"selectedModelId": "mF", "fit": "regular",
                "confirmedGptProductEvidence": _contract(source)}
    block = {"id": "front", "source": "ai", "cutType": "styling", "shot": "medium",
             "direction": "front", "colorId": color_id, "exampleId": "test-example",
             "refScope": "all", "pose": "auto"}

    async def project(*args):
        return {"copywriting": False, **({"selected_mannequin_id": "A-1"} if selected_mannequin else {})}

    async def storyboard(*args):
        return v4_blocks([block])

    async def product(*args):
        return {"clothingType": "top", "colors": [{"id": "base", "isBase": True,
                "images": [{"slot": "Front", "id": "product"}]},
                {"id": "blue", "images": [{"slot": "Front", "id": "blue-product"}]}]}

    async def get_analysis(*args):
        return analysis

    async def mannequins(*args):
        return [{"candidate": "A", "version": 1, "asset_id": "mannequin",
                 "active_asset_id": "tone-mannequin"}]

    async def asset(conn, uid, asset_id):
        if missing_mannequin_asset and asset_id == "tone-mannequin":
            return None
        return {"id": asset_id, "r2_key": f"k/{asset_id}", "mime_type": "image/png"}

    async def project_asset(conn, uid, pid, asset_id):
        return await asset(conn, uid, asset_id)

    async def failure(conn, **kwargs):
        captured["failure"] = kwargs
        return True

    async def create_asset(conn, **kwargs):
        captured["master_asset"] = kwargs
        return {"id": kwargs["asset_id"]}

    async def example(*args, **kwargs):
        return worker.InlineImage("image/png", b"example")

    async def generate(settings, gemini, spec, product, images, **kwargs):
        assert set(kwargs) == {"confirmed_prompt_input"}
        prompt_input = kwargs["confirmed_prompt_input"]
        captured["calls"].append("render")
        captured["render_spec"] = spec
        captured["generation"].append((list(images), compile_confirmed_gpt_prompt(prompt_input), prompt_input))
        return b"fake-generated-cut", "image/png"

    async def recolor(settings, gemini, master, photograph, plan, **kwargs):
        captured["calls"].append("color")
        captured["color"] = (master, photograph, plan)
        return b"fake-blue-cut", "image/png"

    for name, fake in (("get_project", project), ("get_storyboard", storyboard),
                       ("get_product", product), ("get_analysis", get_analysis),
                       ("list_mannequin_cuts", mannequins), ("get_asset_for_user", asset),
                       ("get_project_asset_for_user", project_asset),
                       ("create_asset", create_asset),
                       ("finalize_detail_page_failure", failure)):
        monkeypatch.setattr(worker.repo, name, fake)
    monkeypatch.setattr(worker.cut_generator, "load_virtual_model_registry", lambda: {"mF": {}})
    monkeypatch.setattr(worker.cut_generator, "load_example_asset_registry", lambda: (None, {
        "test-example": {"cutType": "styling", "direction": "front", "all": "example.png"}}))
    monkeypatch.setattr(worker.cut_generator, "example_asset_status", lambda *args: "available")
    monkeypatch.setattr(worker.cut_generator, "load_example_image", example)
    monkeypatch.setattr(worker.confirmed_gpt_runtime, "bind_confirmed_gpt_directing", lambda *args, **kw: _directing())
    refs = [{"key": f"k/{name}", "mime": "image/png", "byteLength": len(name.encode()),
             "sha256": sha256(name.encode()).hexdigest()} for name in ("face", "body")]
    monkeypatch.setattr(worker.cut_generator, "resolve_confirmed_gpt_direction_sheets", lambda spec: refs)
    real_gen_cuts = worker._gen_cuts
    _patch_detail_terminal(monkeypatch, captured)

    async def gen_cuts(app, job, prepared, product, analysis, **kwargs):
        captured["prepared"] = prepared
        return await real_gen_cuts(app, job, prepared, product, analysis, **kwargs)

    monkeypatch.setattr(worker, "_gen_cuts", gen_cuts)
    monkeypatch.setattr(worker.cut_generator, "generate", generate)
    monkeypatch.setattr(worker.garment_color, "apply", recolor)
    r2 = _R2()
    r2.objects.update({f"k/{name}": (data, "image/png", None) for name, data in (
        ("product", source), ("blue-product", target), ("face", b"face"), ("body", b"body"),
        ("mannequin", b"unadjusted"), ("tone-mannequin", b"selected-tone"))})
    app = fake_worker_app(make_settings(gemini_api_key="fake", r2_bucket="fake",
                          detail_cut_max_attempts=1, cut_output_qc_mode="off", page_output_qc_mode="off"), r2=r2)
    asyncio.run(worker.run_detail_page_job(app, worker_job({"modelId": "mF"}, credits_reserved=19)))
    return captured


@pytest.mark.parametrize("selected_mannequin", [False, True])
def test_front_styling_confirmed_cut_is_generated_and_variant_is_saved(monkeypatch, selected_mannequin):
    captured = _run(monkeypatch, selected_mannequin=selected_mannequin)

    assert "failure" not in captured
    assert len(captured["generation"]) == 1
    images, rendered, prompt_input = captured["generation"][0]
    assert prompt_input.scope.selected_mannequin is selected_mannequin
    prepared = captured["prepared"][0]
    assert prepared[8].prompt_input is prompt_input
    assert len(images) == (5 if selected_mannequin else 4)
    assert [i.data for i in images[:2]] == ([b"selected-tone", b"face"] if selected_mannequin else [b"face", b"body"])
    assert images[-1].data == b"example"
    if selected_mannequin:
        assert "SELECTED MANNEQUIN GARMENT-LOCAL COLOR AND FIT AUTHORITY:" in rendered
    else:
        assert "MANNEQUIN" not in rendered
        assert prompt_input.sold_product_fit == "regular"
    assets = captured["finalize"]["cut_assets"]
    assert len(assets) == 1
    assert assets[0]["metadata"]["generation"]["confirmedGptVariant"] == (
        "selected_mannequin_v1" if selected_mannequin else "product_evidence_v2")


@pytest.mark.parametrize("selected_mannequin", [False, True])
def test_extra_color_uses_confirmed_base_render_before_color_application(monkeypatch, selected_mannequin):
    captured = _run(monkeypatch, selected_mannequin=selected_mannequin, color_id="blue")

    assert "failure" not in captured
    assert captured["calls"] == ["render", "color"]
    assert len(captured["generation"]) == 1
    assert captured["render_spec"]["colorId"] == "base"
    images, rendered, prompt_input = captured["generation"][0]
    assert prompt_input.scope.selected_mannequin is selected_mannequin
    assert len(images) == (5 if selected_mannequin else 4)
    assert [image.data for image in images[:2]] == (
        [b"selected-tone", b"face"] if selected_mannequin else [b"face", b"body"])
    assert images[-1].data == b"example"
    assert all(image.data != _png("blue") for image in images)
    prepared = captured["prepared"][0]
    assert [image.data for image in prepared[4]] == [_png()]
    assert prepared[8].prompt_input is prompt_input
    if selected_mannequin:
        assert "SELECTED MANNEQUIN GARMENT-LOCAL COLOR AND FIT AUTHORITY:" in rendered
    else:
        assert "MANNEQUIN" not in rendered
        assert "MANNEQUIN" not in prepared[2]
        assert prompt_input.sold_product_fit == "regular"
    master, photograph, plan = captured["color"]
    assert master.data == b"fake-generated-cut"
    assert photograph.data == _png("blue")
    assert (plan.mode, plan.render_color_id, plan.target_color_id) == ("base_then_color", "base", "blue")
    assets = captured["finalize"]["cut_assets"]
    assert len(assets) == 1
    asset = assets[0]
    assert asset["sha256"] == sha256(b"fake-blue-cut").hexdigest()
    assert asset["metadata"]["generation"]["recipe"]["colorId"] == "blue"
    assert asset["metadata"]["generation"]["confirmedGptVariant"] == (
        "selected_mannequin_v1" if selected_mannequin else "product_evidence_v2")
    execution = asset["metadata"]["colorExecution"]
    assert execution["renderColorId"] == "base" and execution["targetColorId"] == "blue"
    assert execution["masterSha256"] == sha256(b"fake-generated-cut").hexdigest()


def test_unavailable_selected_mannequin_still_fails_without_changing_authority(monkeypatch):
    captured = _run(monkeypatch, selected_mannequin=True, missing_mannequin_asset=True)
    assert captured["generation"] == []
    assert "finalize" not in captured
    assert captured["prepared"][0].failure["reason"] == "confirmed_gpt_packet_invalid"
