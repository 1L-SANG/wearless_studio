import asyncio

import pytest

from app.agents.gemini_image import InlineImage
from app.agents.vision_llm import VisionError
from app.workers import detail_page_job as dpj
from app.workers import editor_image_job as eij
from conftest import fake_worker_app, make_settings, worker_job


def _qc(verdict, number):
    return {
        "verdict": verdict,
        "mismatches": [] if verdict == "pass" else [f"logo-{number}"],
        "correctionPrompt": None,
    }


class _CapturingR2:
    def __init__(self):
        self.saved = []

    def get_bytes(self, key):
        return b"PRODUCT"

    def put_bytes(self, key, data, mime):
        self.saved.append((data, mime))

    def delete(self, key):
        return None


def _patch_qc(monkeypatch, module, verdict_names, *, picked=None, picker_error=False, events=None):
    verdicts = list(verdict_names)
    calls = {"verdict": 0, "picker": 0}

    async def fake_verdict(settings, product_images, generated):
        calls["verdict"] += 1
        if events is not None:
            events.append("garment")
        assert [image.data for image in product_images] == [b"PRODUCT"]
        return _qc(verdicts.pop(0), calls["verdict"])

    async def fake_pick(settings, product_images, candidates):
        calls["picker"] += 1
        assert [image.data for image in product_images] == [b"PRODUCT"]
        assert [image.data for image in candidates] == [
            f"IMG{i}".encode() for i in range(1, len(candidates) + 1)
        ]
        if picker_error:
            raise VisionError("picker down")
        return {"chosenIndex": picked, "reason": "best logo"}

    monkeypatch.setattr(module.image_qc, "verdict", fake_verdict)
    monkeypatch.setattr(module.image_qc, "pick_best", fake_pick)
    return calls


def _run_detail(
    monkeypatch,
    *,
    mode,
    verdicts,
    picked=None,
    picker_error=False,
    bg=False,
    scene_verdicts=None,
    product_reference=True,
):
    events = []
    scene_results = list(scene_verdicts or [])
    calls = _patch_qc(
        monkeypatch,
        dpj,
        verdicts,
        picked=picked,
        picker_error=picker_error,
        events=events,
    )
    generated_inputs = []

    async def fake_generate(settings, gemini, spec, product, images, **kwargs):
        generated_inputs.append([image.data for image in images])
        events.append("generate")
        return f"IMG{len(generated_inputs)}".encode(), "image/png"

    async def fake_scene(settings, plate, generated):
        events.append("scene")
        assert plate.data == b"PLATE"
        verdict = scene_results.pop(0) if scene_results else "pass"
        return _qc(verdict, 0)

    async def fake_emit(pool, job_id, event_type, payload):
        return None

    monkeypatch.setattr(dpj.cut_generator, "generate", fake_generate)
    monkeypatch.setattr(dpj.image_qc, "scene_verdict", fake_scene)
    monkeypatch.setattr(dpj, "_emit", fake_emit)

    r2 = _CapturingR2()
    app = fake_worker_app(make_settings(
        gemini_api_key="x",
        r2_bucket="b",
        garment_qc_mode=mode,
        garment_qc_extra_candidates=2,
    ), r2=r2)
    product_image = InlineImage("image/png", b"PRODUCT" if product_reference else b"MANNEQUIN")
    if bg:
        images = [InlineImage("image/png", b"PLATE"), product_image]
        manifest = "1. EXAMPLE REFERENCE (scope: bg)\n2. PRODUCT"
        spec = {"id": "b1", "cutType": "styling", "refScope": "bg"}
    else:
        images = [product_image]
        manifest = "1. PRODUCT"
        spec = {"id": "b1", "cutType": "product"}

    result = asyncio.run(dpj._gen_cuts(
        app,
        worker_job(),
        [(spec, images, manifest, False, [product_image] if product_reference else [])],
        {"clothingType": "top"},
        {},
    ))
    return result, r2, calls, generated_inputs, events


def _run_editor(
    monkeypatch,
    *,
    mode,
    verdicts,
    picked=None,
    picker_error=False,
    bg=False,
    scene_verdicts=None,
):
    events = []
    scene_results = list(scene_verdicts or [])
    calls = _patch_qc(
        monkeypatch,
        eij,
        verdicts,
        picked=picked,
        picker_error=picker_error,
        events=events,
    )
    captured = {}
    generated_inputs = []

    async def fake_product(conn, project_id):
        return {
            "clothingType": "top",
            "colors": [{
                "id": "col1",
                "isBase": True,
                "images": [{"slot": "Front", "id": "a1"}],
            }],
        }

    async def fake_analysis(conn, project_id):
        return {}

    async def fake_asset(conn, user_id, asset_id):
        return {"id": asset_id, "r2_key": f"k/{asset_id}", "mime_type": "image/png"}

    async def fake_generate(settings, gemini, spec, product, images, **kwargs):
        generated_inputs.append([image.data for image in images])
        events.append("generate")
        return f"IMG{len(generated_inputs)}".encode(), "image/png"

    async def fake_finalize(conn, **kwargs):
        captured.update(kwargs)
        return {"id": "wardrobe-1"}

    async def fake_emit(pool, job_id, event_type, payload):
        return None

    async def fake_example(settings, example_id, scope="all", clothing_type=None):
        return InlineImage("image/png", b"PLATE")

    async def fake_scene(settings, plate, generated):
        events.append("scene")
        assert plate.data == b"PLATE"
        verdict = scene_results.pop(0) if scene_results else "pass"
        return _qc(verdict, 0)

    monkeypatch.setattr(eij.repo, "get_product", fake_product)
    monkeypatch.setattr(eij.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(eij.repo, "get_asset_for_user", fake_asset)
    monkeypatch.setattr(eij.cut_generator, "generate", fake_generate)
    monkeypatch.setattr(eij.repo, "finalize_editor_image_success", fake_finalize)
    monkeypatch.setattr(eij, "_emit", fake_emit)
    monkeypatch.setattr(eij.image_qc, "scene_verdict", fake_scene)
    if bg:
        monkeypatch.setattr(eij.cut_generator, "example_asset_status", lambda *_args: "available")
        monkeypatch.setattr(eij.cut_generator, "load_example_image", fake_example)

    r2 = _CapturingR2()
    app = fake_worker_app(make_settings(
        gemini_api_key="x",
        r2_bucket="b",
        garment_qc_mode=mode,
        garment_qc_extra_candidates=2,
    ), r2=r2)
    payload = {
        "mode": "new",
        "colorId": "col1",
        "contentRole": "productOverview",
        "cutType": "product",
        "direction": "front",
        "shot": "ghost",
    }
    if bg:
        payload.update({
            "contentRole": "coordination",
            "cutType": "styling",
            "exampleId": "ex-bg",
            "refScope": "bg",
        })

    asyncio.run(eij.run_editor_image_job(app, worker_job(payload)))
    return captured, r2, calls, generated_inputs, events


_CASES = [
    pytest.param("bestof", ["pass"], None, False, 1, 0, b"IMG1", None, id="pass-immediate"),
    pytest.param("bestof", ["retry", "pass"], None, False, 2, 1, b"IMG2", None,
                 id="retry-then-pass"),
    pytest.param("bestof", ["retry", "retry", "retry"], 1, False, 3, 1, b"IMG2", None,
                 id="all-retry-picker"),
    pytest.param("bestof", ["retry", "retry", "retry"], None, True, 3, 0, b"IMG1",
                 "garment_qc_picker_unavailable", id="picker-unavailable"),
    pytest.param("shadow", ["retry"], None, False, 1, 0, b"IMG1", None, id="shadow"),
    pytest.param("off", [], None, False, 1, None, b"IMG1", None, id="off"),
]


@pytest.mark.parametrize(
    "mode,verdicts,picked,picker_error,generate_count,chosen_index,saved,warning_code",
    _CASES,
)
def test_detail_worker_garment_bestof_modes(
    monkeypatch,
    mode,
    verdicts,
    picked,
    picker_error,
    generate_count,
    chosen_index,
    saved,
    warning_code,
):
    result, r2, calls, generated_inputs, _events = _run_detail(
        monkeypatch,
        mode=mode,
        verdicts=verdicts,
        picked=picked,
        picker_error=picker_error,
    )
    cut_results, _assets, _faces, garment_qcs, warnings = result

    assert len(cut_results) == 1
    assert len(generated_inputs) == generate_count
    assert all(inputs == [b"PRODUCT"] for inputs in generated_inputs)
    assert r2.saved[-1][0] == saved
    assert calls["verdict"] == len(verdicts)
    assert calls["picker"] == (1 if mode == "bestof" and verdicts and all(
        verdict == "retry" for verdict in verdicts) else 0)
    if mode == "off":
        assert garment_qcs == []
    else:
        assert garment_qcs[0]["chosenIndex"] == chosen_index
        assert len(garment_qcs[0]["candidates"]) == len(verdicts)
    assert ([warning["code"] for warning in warnings] == [warning_code]
            if warning_code else warnings == [])


@pytest.mark.parametrize(
    "mode,verdicts,picked,picker_error,generate_count,chosen_index,saved,warning_code",
    _CASES,
)
def test_editor_worker_garment_bestof_modes(
    monkeypatch,
    mode,
    verdicts,
    picked,
    picker_error,
    generate_count,
    chosen_index,
    saved,
    warning_code,
):
    captured, r2, calls, generated_inputs, _events = _run_editor(
        monkeypatch,
        mode=mode,
        verdicts=verdicts,
        picked=picked,
        picker_error=picker_error,
    )

    assert len(generated_inputs) == generate_count
    assert all(inputs == [b"PRODUCT"] for inputs in generated_inputs)
    assert r2.saved[-1][0] == saved
    assert calls["verdict"] == len(verdicts)
    assert calls["picker"] == (1 if mode == "bestof" and verdicts and all(
        verdict == "retry" for verdict in verdicts) else 0)
    if mode == "off":
        assert "garmentQc" not in captured["metadata"]
    else:
        assert captured["metadata"]["garmentQc"]["chosenIndex"] == chosen_index
        assert len(captured["metadata"]["garmentQc"]["candidates"]) == len(verdicts)
    warning_codes = [warning["code"] for warning in captured["metadata"].get("warnings", [])]
    assert warning_codes == ([warning_code] if warning_code else [])


def test_detail_bg_scene_qc_precedes_garment_qc(monkeypatch):
    _result, _r2, _calls, _inputs, events = _run_detail(
        monkeypatch,
        mode="bestof",
        verdicts=["retry", "pass"],
        bg=True,
    )
    assert events == ["generate", "scene", "garment", "generate", "scene", "garment"]


def test_editor_bg_scene_qc_precedes_garment_qc(monkeypatch):
    _captured, _r2, _calls, _inputs, events = _run_editor(
        monkeypatch,
        mode="bestof",
        verdicts=["retry", "pass"],
        bg=True,
    )
    assert events == ["generate", "scene", "garment", "generate", "scene", "garment"]



@pytest.mark.parametrize("worker", ["detail", "editor"])
def test_bg_scene_rejected_extra_candidate_never_reaches_garment_or_picker_pool(
    monkeypatch,
    worker,
):
    runner = _run_detail if worker == "detail" else _run_editor
    result = runner(
        monkeypatch,
        mode="bestof",
        verdicts=["retry"],
        picked=0,
        bg=True,
        scene_verdicts=["pass", "retry", "retry", "retry"],
    )
    output, r2, calls, generated_inputs, events = result

    assert len(generated_inputs) == 4
    assert calls == {"verdict": 1, "picker": 1}
    assert events == [
        "generate", "scene", "garment",
        "generate", "scene",
        "generate", "scene",
        "generate", "scene",
    ]
    assert r2.saved[-1][0] == b"IMG1"
    if worker == "detail":
        warnings = output[4]
    else:
        warnings = output["metadata"]["warnings"]
    assert any(warning["code"] == "garment_qc_candidate_generation_failed"
               for warning in warnings)


def test_detail_mannequin_only_fails_open_without_self_comparison(monkeypatch):
    result, r2, calls, generated_inputs, _events = _run_detail(
        monkeypatch,
        mode="bestof",
        verdicts=[],
        product_reference=False,
    )
    _cuts, _assets, _faces, garment_qcs, warnings = result

    assert generated_inputs == [[b"MANNEQUIN"]]
    assert calls == {"verdict": 0, "picker": 0}
    assert r2.saved[-1][0] == b"IMG1"
    assert garment_qcs == []
    assert [warning["code"] for warning in warnings] == [
        "garment_qc_product_reference_unavailable",
    ]
