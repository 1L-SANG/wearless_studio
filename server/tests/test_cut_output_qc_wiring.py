import asyncio

import pytest

from app.agents.cut_plan import CutPlanError
from app.agents.gemini_image import InlineImage
from app.agents.vision_llm import VisionError
from app.workers import detail_page_job as dpj
from app.workers import editor_image_job as eij
from conftest import fake_worker_app, make_settings, worker_job


class _RecordingR2:
    def __init__(self, events):
        self.events = events
        self.saved = []

    def get_bytes(self, key):
        return b"PRODUCT"

    def put_bytes(self, key, data, mime, cache=None):
        self.events.append("save")
        self.saved.append(data)

    def public_url(self, key):
        # 실제 R2.public_url 미러 — cut_done previewUrl(editor_wait_dev_spec §2-1)
        return f"https://r2.test/{key}"

    def preview_url(self, key, expires=3600):
        return f"https://r2.test/{key}"

    def delete(self, key):
        return None


def _detail_spec():
    return {
        "id": "b1",
        "cutType": "product",
        "direction": "front",
        "shot": "ghost",
        "faceExposure": None,
        "pose": "auto",
        "refScope": "all",
    }


def _run_detail_cut(
    monkeypatch, *, qc_mode="shadow", manifest="1. PRODUCT — front", events=None,
):
    events = [] if events is None else events
    captured = {}

    async def fake_generate(*_args, **_kwargs):
        events.append("generate")
        return b"INITIAL", "image/png"

    async def fake_best_of(settings, product_images, initial, generate_candidate):
        events.append("garment")
        assert initial.data == b"INITIAL"
        return InlineImage("image/png", b"CHOSEN"), {"chosenIndex": 0}, []

    async def fake_emit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(dpj.cut_generator, "generate", fake_generate)
    monkeypatch.setattr(dpj.image_qc, "best_of", fake_best_of)
    monkeypatch.setattr(dpj, "_emit", fake_emit)

    r2 = _RecordingR2(events)
    app = fake_worker_app(
        make_settings(
            gemini_api_key="x",
            r2_bucket="b",
            garment_qc_mode="off",
            cut_output_qc_mode=qc_mode,
        ),
        r2=r2,
    )
    product_image = InlineImage("image/png", b"PRODUCT")
    result = asyncio.run(dpj._gen_cuts(
        app,
        worker_job(),
        [(_detail_spec(), [product_image], manifest, False, [product_image])],
        {"clothingType": "top"},
        {"fitProfile": {"axes": {"fit": "regular"}}},
    ))
    captured.update(events=events, r2=r2, result=result)
    return captured


def test_detail_shadow_qc_observes_chosen_output_before_save(monkeypatch):
    captured = {}
    events = []

    async def recording_cut_qc(settings, plan, references, generated):
        events.append("cut-qc")
        captured.update(
            plan=plan.to_dict(), references=references, generated=generated,
        )
        return {"verdict": "FAIL", "passed": False, "gates": {}}

    monkeypatch.setattr(dpj.cut_output_qc, "verdict", recording_cut_qc)
    run = _run_detail_cut(monkeypatch, events=events)
    cut_results, assets, faces, garment_qcs, cut_qcs, page_qc, warnings = run["result"]
    assert len(cut_results) == len(assets) == 1 and faces == 0
    assert garment_qcs == [{"blockId": "b1", "chosenIndex": 0}]
    assert cut_qcs == [{
        "blockId": "b1", "verdict": "FAIL", "passed": False, "gates": {},
    }]
    assert page_qc is None
    assert warnings == []
    assert captured["generated"].data == b"CHOSEN"
    assert [reference.role for reference in captured["references"]] == ["product"]
    assert captured["plan"]["declaredFitAxes"] == ["fit"]
    assert run["r2"].saved == [b"CHOSEN"]
    assert run["events"] == ["generate", "garment", "cut-qc", "save"]


@pytest.mark.parametrize("failure", ["provider", "manifest", "plan"])
def test_detail_shadow_qc_failure_warns_but_still_saves(monkeypatch, failure):
    calls = {"verdict": 0}

    async def unavailable(*_args, **_kwargs):
        calls["verdict"] += 1
        raise VisionError("judge unavailable")

    monkeypatch.setattr(dpj.cut_output_qc, "verdict", unavailable)
    manifest = "1. PRODUCT — front"
    if failure == "manifest":
        manifest = "1. UNKNOWN — not an authority role"
    elif failure == "plan":
        monkeypatch.setattr(
            dpj.cut_plan,
            "compile_cut_plan",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(CutPlanError("bad plan")),
        )

    run = _run_detail_cut(monkeypatch, manifest=manifest)
    _cuts, assets, _faces, _garment_qcs, cut_qcs, page_qc, warnings = run["result"]
    assert len(assets) == 1
    assert run["r2"].saved == [b"CHOSEN"]
    assert cut_qcs == []
    assert page_qc is None
    assert warnings == [{"blockId": "b1", "code": "cut_output_qc_unavailable"}]
    assert calls["verdict"] == (1 if failure == "provider" else 0)


def test_detail_off_and_passthrough_never_call_cut_qc(monkeypatch):
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("cut QC must not run")

    monkeypatch.setattr(dpj.cut_output_qc, "verdict", forbidden)
    off = _run_detail_cut(monkeypatch, qc_mode="off")
    assert off["result"][4] == []

    async def fake_emit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(dpj, "_emit", fake_emit)
    app = fake_worker_app(make_settings(
        gemini_api_key="x", r2_bucket="b", cut_output_qc_mode="shadow",
    ))
    original = {"id": "seller-detail", "width": 100, "height": 200}
    passthrough = asyncio.run(dpj._gen_cuts(
        app,
        worker_job(),
        [(_detail_spec(), [InlineImage("image/png", b"PRODUCT")], "manifest",
          False, [], None, False, original)],
        {"clothingType": "top"},
        {},
    ))
    assert passthrough[1] == []
    assert passthrough[4] == []
    assert passthrough[5] is None
    assert passthrough[6] == []


def test_detail_job_persists_cut_qc_per_block(monkeypatch):
    captured = {}

    async def fake_project(conn, uid, pid):
        return {"copywriting": False}

    async def fake_storyboard(conn, pid):
        return [{"id": "b1", "source": "ai", "cutType": "product", "shot": "ghost"}]

    async def fake_product(conn, pid):
        return {"colors": [{"isBase": True, "images": [{"slot": "Front", "id": "a1"}]}]}

    async def fake_analysis(conn, pid):
        return {}

    async def fake_asset(conn, uid, asset_id):
        return {"id": asset_id, "r2_key": "product", "mime_type": "image/png"}

    async def fake_gen_cuts(*_args, **_kwargs):
        return (
            [{"blockId": "b1", "imageUrl": "/out"}],
            [{"key": "out"}],
            0,
            [],
            [{"blockId": "b1", "verdict": "PASS", "passed": True}],
            None,
            [],
        )

    def fake_assemble(*_args, **_kwargs):
        return []

    async def fake_finalize(conn, **kwargs):
        captured.update(kwargs)
        return {"editor_blocks": [], "available": 1}

    async def fake_emit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(dpj.repo, "get_project", fake_project)
    monkeypatch.setattr(dpj.repo, "get_storyboard", fake_storyboard)
    monkeypatch.setattr(dpj.repo, "get_product", fake_product)
    monkeypatch.setattr(dpj.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(dpj.repo, "get_asset_for_user", fake_asset)
    monkeypatch.setattr(dpj, "_gen_cuts", fake_gen_cuts)
    monkeypatch.setattr(dpj.page_assembler, "assemble", fake_assemble)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_success", fake_finalize)
    monkeypatch.setattr(dpj, "_emit", fake_emit)

    app = fake_worker_app(make_settings(gemini_api_key="x", r2_bucket="b"))
    asyncio.run(dpj.run_detail_page_job(app, worker_job(credits_reserved=1)))
    assert captured["metadata"]["cutQc"] == [
        {"blockId": "b1", "verdict": "PASS", "passed": True},
    ]
    assert "garmentQc" not in captured["metadata"]


def _run_editor(monkeypatch, *, qc_mode, qc_error=False):
    captured = {"events": [], "qcCalls": 0}

    async def fake_product(conn, pid):
        return {"clothingType": "top", "colors": [{
            "id": "color", "isBase": True,
            "images": [{"slot": "Front", "id": "product"}],
        }]}

    async def fake_analysis(conn, pid):
        return {"fitProfile": {"axes": {"fit": "regular"}}}

    async def fake_asset(conn, uid, asset_id):
        return {"id": asset_id, "r2_key": "product", "mime_type": "image/png"}

    async def fake_generate(*_args, **_kwargs):
        captured["events"].append("generate")
        return b"INITIAL", "image/png"

    async def fake_best_of(settings, product_images, initial, generate_candidate):
        captured["events"].append("garment")
        return InlineImage("image/png", b"CHOSEN"), {"chosenIndex": 0}, []

    async def fake_cut_qc(settings, plan, references, generated):
        captured["qcCalls"] += 1
        captured["events"].append("cut-qc")
        captured.update(
            plan=plan.to_dict(), references=references, generated=generated,
        )
        if qc_error:
            raise VisionError("judge unavailable")
        return {"verdict": "PASS", "passed": True, "gates": {}}

    async def fake_finalize(conn, **kwargs):
        captured["finalize"] = kwargs
        return {"id": "wardrobe"}

    async def fake_emit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(eij.repo, "get_product", fake_product)
    monkeypatch.setattr(eij.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(eij.repo, "get_asset_for_user", fake_asset)
    monkeypatch.setattr(eij.cut_generator, "generate", fake_generate)
    monkeypatch.setattr(eij.image_qc, "best_of", fake_best_of)
    monkeypatch.setattr(eij.cut_output_qc, "verdict", fake_cut_qc)
    monkeypatch.setattr(eij.repo, "finalize_editor_image_success", fake_finalize)
    monkeypatch.setattr(eij, "_emit", fake_emit)

    r2 = _RecordingR2(captured["events"])
    app = fake_worker_app(
        make_settings(
            gemini_api_key="x",
            r2_bucket="b",
            garment_qc_mode="off",
            cut_output_qc_mode=qc_mode,
        ),
        r2=r2,
    )
    asyncio.run(eij.run_editor_image_job(app, worker_job({
        "mode": "new",
        "colorId": "color",
        "cutType": "product",
        "direction": "front",
        "shot": "ghost",
    })))
    return captured, r2


@pytest.mark.parametrize(
    ("qc_mode", "qc_error", "expected_calls", "expected_events"),
    [
        ("shadow", False, 1, ["generate", "garment", "cut-qc", "save"]),
        ("shadow", True, 1, ["generate", "garment", "cut-qc", "save"]),
        ("off", False, 0, ["generate", "garment", "save"]),
    ],
)
def test_editor_cut_qc_is_shadow_only_and_persisted_separately(
    monkeypatch, qc_mode, qc_error, expected_calls, expected_events,
):
    captured, r2 = _run_editor(monkeypatch, qc_mode=qc_mode, qc_error=qc_error)
    metadata = captured["finalize"]["metadata"]
    assert captured["qcCalls"] == expected_calls
    assert captured["events"] == expected_events
    assert r2.saved == [b"CHOSEN"]
    assert metadata["garmentQc"] == {"chosenIndex": 0}
    if qc_mode == "shadow" and not qc_error:
        assert metadata["cutQc"] == {"verdict": "PASS", "passed": True, "gates": {}}
        assert captured["generated"].data == b"CHOSEN"
        assert [reference.role for reference in captured["references"]] == ["product"]
        assert captured["plan"]["declaredFitAxes"] == ["fit"]
        assert "warnings" not in metadata
    elif qc_mode == "shadow":
        assert "cutQc" not in metadata
        assert metadata["warnings"] == [{"code": "cut_output_qc_unavailable"}]
    else:
        assert "cutQc" not in metadata
        assert "warnings" not in metadata
