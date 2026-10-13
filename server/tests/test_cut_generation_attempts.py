"""Initial cut generation stays single; current QC owns every further attempt."""

import asyncio
from types import SimpleNamespace

import pytest

from app.agents.gemini_image import InlineImage
from app.agents.vision_llm import VisionError
from app.workers import detail_page_job as dpj
from app.workers import editor_image_job as eij
from conftest import fake_worker_app, make_settings, worker_job
from test_cut_output_qc_wiring import _qc_result


class _CapturingR2:
    def __init__(self):
        self.saved = []

    def get_bytes(self, key):
        return b"PRODUCT"

    def put_bytes(self, key, data, mime, cache=None):
        self.saved.append(data)

    def public_url(self, key):
        return f"https://r2.test/{key}"

    def preview_url(self, key, expires=3600):
        return f"https://r2.test/{key}"

    def delete(self, key):
        return None


def _patch_generation(monkeypatch, module, *, scene_verdicts=()):
    captured = {"events": [], "inputs": [], "qcInputs": []}
    scene_results = list(scene_verdicts)

    async def generate(settings, gemini, spec, product, images, **kwargs):
        captured["inputs"].append([image.data for image in images])
        captured["events"].append("generate")
        return f"IMG{len(captured['inputs'])}".encode(), "image/png"

    async def scene(settings, plate, generated):
        assert plate.data == b"PLATE"
        captured["events"].append("scene")
        verdict = scene_results.pop(0) if scene_results else "pass"
        if isinstance(verdict, Exception):
            raise verdict
        return {"verdict": verdict, "mismatches": [] if verdict == "pass" else ["location"],
                "correctionPrompt": None}

    async def cut_qc(settings, plan, references, generated):
        captured["events"].append("cut-qc")
        captured["qcInputs"].append(generated.data)
        return _qc_result()

    async def forbidden_garment_judge(*args, **kwargs):
        raise AssertionError("new cuts must not run the retired garment candidate judge")

    async def emit(*args, **kwargs):
        return None

    monkeypatch.setattr(module.cut_generator, "generate", generate)
    monkeypatch.setattr(module.image_qc, "scene_verdict", scene)
    monkeypatch.setattr(module.image_qc, "verdict", forbidden_garment_judge)
    monkeypatch.setattr(module.cut_output_qc, "verdict", cut_qc)
    monkeypatch.setattr(module, "_emit", emit)
    return captured


def _run_detail(monkeypatch, *, qc_mode="off", scene_verdicts=(), cached_meta=None,
                product_reference=True):
    captured = _patch_generation(monkeypatch, dpj, scene_verdicts=scene_verdicts)
    product_image = InlineImage("image/png", b"PRODUCT" if product_reference else b"MANNEQUIN")
    images = [product_image]
    spec = {"id": "b1", "cutType": "product", "direction": "front", "shot": "ghost"}
    manifest = "1. PRODUCT \u2014 front"
    if scene_verdicts:
        images.insert(0, InlineImage("image/png", b"PLATE"))
        spec.update(cutType="styling", shot="full", refScope="bg", faceExposure="same", pose="auto")
        manifest = "1. EXAMPLE REFERENCE (scope: bg)\n2. PRODUCT \u2014 front"

    checkpoint_store = None
    if cached_meta is not None:
        async def for_cut(*args, **kwargs):
            return SimpleNamespace(base=SimpleNamespace(meta={}),
                                   final=SimpleNamespace(image=b"CACHED", mime="image/png", meta=cached_meta))

        checkpoint_store = SimpleNamespace(for_cut=for_cut)
    r2 = _CapturingR2()
    app = fake_worker_app(make_settings(gemini_api_key="x", r2_bucket="b", cut_output_qc_mode=qc_mode), r2=r2)
    captured["result"] = asyncio.run(dpj._gen_cuts(
        app, worker_job(), [(spec, images, manifest, False, [product_image] if product_reference else [])],
        {"clothingType": "top"}, {}, cut_checkpoints=checkpoint_store,
    ))
    captured["r2"] = r2
    return captured


def _run_editor(monkeypatch, *, qc_mode="off", scene_verdicts=()):
    captured = _patch_generation(monkeypatch, eij, scene_verdicts=scene_verdicts)

    async def product(conn, project_id):
        return {"clothingType": "top", "colors": [{"id": "col1", "isBase": True,
                "images": [{"slot": "Front", "id": "a1"}]}]}

    async def analysis(conn, project_id):
        return {}

    async def asset(conn, user_id, asset_id):
        return {"id": asset_id, "r2_key": f"k/{asset_id}", "mime_type": "image/png"}

    async def finalize(conn, **kwargs):
        captured["finalize"] = kwargs
        return {"id": "wardrobe-1"}

    async def fail(conn, **kwargs):
        captured["failure"] = kwargs
        return True

    async def example(settings, example_id, scope="all", clothing_type=None):
        return InlineImage("image/png", b"PLATE")

    monkeypatch.setattr(eij.repo, "get_product", product)
    monkeypatch.setattr(eij.repo, "get_analysis", analysis)
    monkeypatch.setattr(eij.repo, "get_asset_for_user", asset)
    monkeypatch.setattr(eij.repo, "finalize_editor_image_success", finalize)
    monkeypatch.setattr(eij.repo, "finalize_editor_image_failure", fail)
    payload = {"mode": "new", "colorId": "col1", "contentRole": "productOverview",
               "cutType": "product", "direction": "front", "shot": "ghost"}
    if scene_verdicts:
        monkeypatch.setattr(eij.cut_generator, "example_asset_status", lambda *args: "available")
        monkeypatch.setattr(eij.cut_generator, "load_example_image", example)
        payload.update(contentRole="coordination", cutType="styling", shot="full",
                       faceExposure="same", pose="auto", exampleId="ex-bg", refScope="bg")
    r2 = _CapturingR2()
    app = fake_worker_app(make_settings(gemini_api_key="x", r2_bucket="b", cut_output_qc_mode=qc_mode), r2=r2)
    asyncio.run(eij.run_editor_image_job(app, worker_job(payload)))
    captured["r2"] = r2
    return captured


@pytest.mark.parametrize("worker", ["detail", "editor"])
@pytest.mark.parametrize("qc_mode", ["off", "shadow", "repair"])
def test_new_cut_generates_once_when_current_qc_does_not_request_repair(monkeypatch, worker, qc_mode):
    run = (_run_detail if worker == "detail" else _run_editor)(monkeypatch, qc_mode=qc_mode)
    assert run["inputs"] == [[b"PRODUCT"]]
    assert run["r2"].saved == [b"IMG1"]
    assert run["qcInputs"] == ([] if qc_mode == "off" else [b"IMG1"])
    if worker == "detail":
        assert "garmentQc" not in run["result"][1][0]["metadata"]
        assert len(run["result"]) == 6
        assert run["result"][5] == []
    else:
        assert "garmentQc" not in run["finalize"]["metadata"]
        assert run["finalize"]["charge"] == 1


@pytest.mark.parametrize("worker", ["detail", "editor"])
def test_scene_mismatch_remains_the_only_extra_generation_before_current_cut_qc(monkeypatch, worker):
    run = (_run_detail if worker == "detail" else _run_editor)(
        monkeypatch, qc_mode="shadow", scene_verdicts=["retry", "pass"],
    )
    assert run["inputs"] == [[b"PLATE", b"PRODUCT"]] * 2
    assert run["events"] == ["generate", "scene", "generate", "scene", "cut-qc"]
    assert run["qcInputs"] == [b"IMG2"]
    assert run["r2"].saved == [b"IMG2"]


@pytest.mark.parametrize("worker", ["detail", "editor"])
def test_scene_retries_stop_at_current_limit_without_saving_a_rejected_cut(monkeypatch, worker):
    run = (_run_detail if worker == "detail" else _run_editor)(
        monkeypatch, scene_verdicts=["retry", "retry", "retry"],
    )
    assert len(run["inputs"]) == 3
    assert run["qcInputs"] == []
    assert run["r2"].saved == []
    if worker == "detail":
        assert run["result"][:2] == ([], [])
    else:
        assert "finalize" not in run
        assert run["failure"]["reserved"] == 1


@pytest.mark.parametrize("worker", ["detail", "editor"])
def test_unavailable_optional_scene_judge_keeps_initial_output_and_current_qc(monkeypatch, worker):
    run = (_run_detail if worker == "detail" else _run_editor)(
        monkeypatch, qc_mode="shadow", scene_verdicts=[VisionError("judge unavailable")],
    )
    assert run["inputs"] == [[b"PLATE", b"PRODUCT"]]
    assert run["events"] == ["generate", "scene", "cut-qc"]
    assert run["qcInputs"] == [b"IMG1"]
    assert run["r2"].saved == [b"IMG1"]
    if worker == "editor":
        assert run["finalize"]["metadata"]["warnings"] == [{"code": "scene_qc_unavailable"}]


def test_legacy_final_checkpoint_metadata_does_not_restore_candidate_generation(monkeypatch):
    legacy = {"garmentQc": {"chosenIndex": 2, "candidates": [{"verdict": "retry"}] * 3},
              "cutQc": _qc_result(), "warnings": [], "neckRepair": None, "facePass": {}}
    run = _run_detail(monkeypatch, qc_mode="repair", cached_meta=legacy)
    assert run["inputs"] == run["qcInputs"] == []
    assert run["r2"].saved == [b"CACHED"]
    assert run["result"][3] == [{"blockId": "b1", **legacy["cutQc"]}]
    assert "garmentQc" not in run["result"][1][0]["metadata"]
    assert run["result"][5] == []


def test_mannequin_only_input_does_not_start_retired_self_comparison(monkeypatch):
    run = _run_detail(monkeypatch, product_reference=False)
    assert run["inputs"] == [[b"MANNEQUIN"]]
    assert run["r2"].saved == [b"IMG1"]
    assert run["result"][3:] == ([], None, [])
