import asyncio
import hashlib
from types import SimpleNamespace

import pytest

from app.agents import face_identity, real_horizon_neck_repair as neck
from app.agents.gemini_image import InlineImage
from app.workers import detail_page_job as dpj
from app.workers import editor_image_job as eij
from conftest import fake_worker_app, make_settings, worker_job
from test_cut_output_qc_wiring import _RecordingR2, _qc_result
from test_real_horizon_neck_repair import IDENTITY, REAL, png


def run_detail(monkeypatch, *, cut="horizon", model=REAL, attached=True,
               outcome="applied", enabled=True, generation_model="gpt-image-2",
               duplicate=False, best_of=None, qc=None, repair=None):
    events, edits, generated = [], [], []
    qwen_bytes = png(color="gray")
    api_bytes = png((120, 180), "black")

    class Client:
        async def generate_content_image(self, model, prompt, images, size, **kwargs):
            events.append("gpt")
            generated.append(model)
            return SimpleNamespace(image=png(), mime="image/png")

        async def edit_image(self, model, prompt, source, **kwargs):
            events.append("sunburst")
            edits.append((model, source, kwargs))
            return SimpleNamespace(image=api_bytes, mime="image/png")

    async def qwen(settings, image, mime, spec, *, outcome, **kwargs):
        events.append("qwen")
        outcome["face_pass"] = run_detail_outcome
        outcome["face_recipe"] = "qwen-recipe"
        return qwen_bytes, mime

    run_detail_outcome = outcome

    async def emit(*args, **kwargs):
        return None

    monkeypatch.setattr(face_identity, "apply_face_pass", qwen)
    monkeypatch.setattr(face_identity, "identity_score", lambda *_a, **_k: 0.9)
    monkeypatch.setattr(dpj, "_emit", emit)
    if best_of:
        monkeypatch.setattr(dpj.image_qc, "best_of", best_of)
    if qc:
        monkeypatch.setattr(dpj.cut_output_qc, "verdict", qc)
    if repair:
        monkeypatch.setattr(dpj.cut_generator, "repair", repair)
    r2 = _RecordingR2(events)
    app = fake_worker_app(make_settings(
        gemini_api_key="test", r2_bucket="b", face_identity_enabled=True,
        model_detail_cut=generation_model, garment_qc_mode="off",
        cut_output_qc_mode="repair" if qc else "off",
        real_horizon_neck_repair_enabled=enabled,
    ), r2=r2, gemini=Client())
    spec = {
        "id": "b1", "source": "ai", "cutType": cut, "modelId": model, "direction": "front",
        "shot": "ghost" if cut == "product" else "full",
        "faceExposure": "same", "pose": "auto", "refScope": "all",
    }
    source = InlineImage("image/png", png())
    prepared = [(spec, [source], "1. PRODUCT — front", attached, [source],
                 None, False, None, None, attached)]
    if duplicate:
        prepared.append(({**spec, "id": "b2"}, *prepared[0][1:]))
    result = asyncio.run(dpj._gen_cuts(
        app, worker_job(), prepared, {"clothingType": "top"}, {},
        face_identity_spec=IDENTITY,
    ))
    return SimpleNamespace(result=result, saved=r2.saved, events=events, edits=edits,
                           generated=generated, qwen=qwen_bytes, api=api_bytes)


def test_detail_runs_gpt_then_qwen_then_one_sunburst_and_saves_the_raw_result(monkeypatch):
    run = run_detail(monkeypatch)
    assert run.events == ["gpt", "qwen", "sunburst", "save"]
    assert run.generated == ["gpt-image-2"]
    assert run.edits[0][1].data == run.qwen
    assert run.saved == [run.api]
    asset = run.result[1][0]
    assert asset["sha256"] == hashlib.sha256(run.api).hexdigest()
    assert (asset["width"], asset["height"]) == (120, 180)
    assert asset["metadata"]["face_pass"] == "applied"
    assert asset["metadata"]["face_recipe"] == "qwen-recipe"
    assert asset["metadata"]["neck_repair"]["applied"]
    assert asset["metadata"]["facemarket_real_derived"]


@pytest.mark.parametrize("kwargs", [
    {"cut": "styling"}, {"cut": "mirror"}, {"cut": "product"},
    {"model": "mA", "attached": False}, {"outcome": "skipped:too_small"},
    {"enabled": False}, {"generation_model": "gemini-3-pro-image"},
])
def test_other_creation_paths_do_not_call_sunburst(monkeypatch, kwargs):
    run = run_detail(monkeypatch, **kwargs)
    assert run.edits == []
    assert "neck_repair" not in run.result[1][0]["metadata"]


def test_duplicate_cuts_share_one_repaired_asset_and_one_request(monkeypatch):
    run = run_detail(monkeypatch, duplicate=True)
    assert len(run.edits) == len(run.saved) == len(run.result[1]) == 1
    assert len(run.result[0]) == 2
    assert run.result[0][0]["imageUrl"] == run.result[0][1]["imageUrl"]


def test_sunburst_rejection_keeps_qwen_bytes_outcome_and_warns(monkeypatch):
    real_score = neck._validate

    def reject(*args):
        _, metadata = real_score(*args)
        return "identity_low", metadata

    monkeypatch.setattr(neck, "_validate", reject)
    run = run_detail(monkeypatch)
    assert len(run.edits) == 1
    assert run.saved == [run.qwen]
    meta = run.result[1][0]["metadata"]
    assert meta["face_pass"] == "applied"
    assert not meta["neck_repair"]["applied"]
    assert {"code": "real_horizon_neck_repair_unavailable", "blockId": "b1"} in run.result[6]


def test_only_the_final_accepted_stage2_gets_neck_repair(monkeypatch):
    stage2 = png(color="blue")
    qc_inputs = []

    async def qc(settings, plan, references, image):
        qc_inputs.append(image.data)
        return _qc_result("framingCrop") if len(qc_inputs) == 1 else _qc_result()

    async def repair(settings, client, spec, product, source, **kwargs):
        kwargs["face_pass_outcome"].update(face_pass="applied", face_recipe="stage2-recipe")
        return stage2, "image/png"

    run = run_detail(monkeypatch, qc=qc, repair=repair)
    assert run.edits[0][1].data == stage2
    assert len(run.edits) == 1
    assert run.saved == [run.api]
    assert run.result[1][0]["metadata"]["face_recipe"] == "stage2-recipe"


def test_candidate_selection_uses_the_chosen_qwen_outcome(monkeypatch):
    selected = png(color="red")

    async def best_of(settings, product_images, initial, generate_candidate):
        await generate_candidate()
        # 선택은 initial이다. 마지막 후보의 얼굴 패스 결과로 적용 조건을 정하지 않는다.
        return initial, {"chosenIndex": 0}, []

    calls = 0
    real_generate = dpj.cut_generator.generate

    async def generate(*args, **kwargs):
        nonlocal calls
        image, mime = await real_generate(*args, **kwargs)
        calls += 1
        if calls > 1:
            kwargs["face_pass_outcome"]["face_pass"] = "skipped:too_small"
            return selected, mime
        return image, mime

    monkeypatch.setattr(dpj.cut_generator, "generate", generate)
    run = run_detail(monkeypatch, best_of=best_of)
    assert len(run.generated) == 2 and len(run.edits) == 1
    assert run.edits[0][1].data == run.qwen


@pytest.mark.parametrize("cut,expected_edits", [("horizon", 1), ("styling", 0), ("mirror", 0)])
def test_editor_new_real_cut_repairs_only_horizon_before_private_storage(monkeypatch, cut, expected_edits):
    original, edited = png(), png((120, 180), "gray")
    events, captured, edits, caches = [], {}, [], []
    license_row = {
        "id": "license", "model_id": REAL, "status": "active", "model_status": "verified",
        "current_enrollment_id": "enrollment", "match_policy_version": "v1",
    }

    class R2(_RecordingR2):
        def get_bytes(self, key):
            return original

        def put_bytes(self, key, data, mime, cache=None):
            caches.append(cache)
            super().put_bytes(key, data, mime, cache)

    class Client:
        async def generate_content_image(self, model, prompt, images, size, **kwargs):
            events.append("gpt")
            assert model == "gpt-image-2"
            return SimpleNamespace(image=original, mime="image/png")

        async def edit_image(self, model, prompt, source, **kwargs):
            events.append("sunburst")
            edits.append(source.data)
            return SimpleNamespace(image=edited, mime="image/png")

    async def value(*args, **kwargs):
        return None

    async def project(*args):
        return {}

    async def product(*args):
        return {"clothingType": "top", "colors": [
            {"id": "base", "isBase": True, "images": [{"id": "front", "slot": "Front"}]},
        ]}

    async def asset(*args):
        return {"mime_type": "image/png", "r2_key": "front"}

    async def license(*args, **kwargs):
        return license_row

    async def references(*args, **kwargs):
        return [
            {"key": "face", "mime": "image/png", "bucket": "face"},
            {"key": "grid", "mime": "image/png", "bucket": "face"},
        ]

    async def lora(*args):
        return {"lora_r2_key": "lora", "trigger_token": "ohwx man"}

    async def qwen(settings, image, mime, spec, *, outcome, **kwargs):
        events.append("qwen")
        outcome["face_pass"] = "applied"
        return original, mime

    async def finalize(conn, **kwargs):
        captured.update(kwargs)
        return {"id": "output"}

    monkeypatch.setattr(eij, "_emit", value)
    monkeypatch.setattr(eij.repo, "get_project", project)
    monkeypatch.setattr(eij.repo, "get_product", product)
    monkeypatch.setattr(eij.repo, "get_analysis", project)
    monkeypatch.setattr(eij.repo, "get_asset_for_user", asset)
    monkeypatch.setattr(eij.repo, "lock_facemarket_writer_boundary", value)
    monkeypatch.setattr(eij.repo, "finalize_editor_image_success", finalize)
    monkeypatch.setattr(eij.facemarket, "wait_for_holder", value)
    monkeypatch.setattr(eij.facemarket, "resolve_model_license", license)
    monkeypatch.setattr(eij.facemarket, "verify_license", value)
    monkeypatch.setattr(eij.facemarket, "verify_license_local", lambda *_a, **_k: None)
    monkeypatch.setattr(eij.identity_source, "resolve_real_model_assets", references)
    monkeypatch.setattr(eij.identity_source, "resolve_enabled_lora", lora)
    monkeypatch.setattr(eij.identity_source, "enrollment_reference_faces", value)
    monkeypatch.setattr(face_identity, "with_references", lambda *_a, **_k: IDENTITY)
    monkeypatch.setattr(face_identity, "apply_face_pass", qwen)
    monkeypatch.setattr(face_identity, "identity_score", lambda *_a, **_k: 0.9)
    r2 = R2(events)
    app = fake_worker_app(make_settings(
        gemini_api_key="test", r2_bucket="public", face_identity_enabled=True,
        model_editor_cut="gpt-image-2", cut_output_qc_mode="off",
    ), r2=r2, gemini=Client())
    app.state.r2_face = r2
    payload = {
        "mode": "new", "cutType": cut, "modelId": REAL, "shot": "full",
        "direction": "front", "faceExposure": "same", "pose": "auto",
        "refScope": "all", "_facemarket": {"modelId": REAL, "licenseId": "license"},
    }
    asyncio.run(eij.run_editor_image_job(app, worker_job(payload)))
    assert len(edits) == expected_edits
    assert r2.saved == [edited if expected_edits else original]
    assert captured["image"]["metadata"]["facemarket_real_derived"]
    if cut != "mirror":
        assert captured["image"]["metadata"]["face_pass"] == "applied"
    assert caches == [eij.PRIVATE_NO_STORE]
    assert captured["charge"] == 1
    if expected_edits:
        assert events == ["gpt", "qwen", "sunburst", "save"]
        assert captured["image"]["metadata"]["neck_repair"]["applied"]
    else:
        assert "neck_repair" not in captured["image"]["metadata"]
