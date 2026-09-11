"""보정(repair) 뒤에도 얼굴 패스가 돌고, QC 는 그 최종본을 보며, 채택 이미지와 face_pass 메타가 짝이 맞는다.

실측(2026-09-11 잡 6c270b84 경로 점검): cut_output_qc 의 EDIT_STAGE1 보정은 cut_generator.repair 로 1차
결과를 다시 그리는데 그 뒤에 얼굴 패스가 없었다 — 보정을 채택하면 얼굴이 gpt-image 로 되돌아간다. 또
face_pass_outcome 한 dict 를 모든 generate 호출이 덮어써서, 후보(best_of)·재생성이 있으면 원장의 face_pass
는 **마지막으로 그린 이미지**의 결과였지 채택된 이미지의 결과가 아니었다.
"""

import asyncio
import types

from app.agents import cut_generator, face_identity
from app.agents.gemini_image import InlineImage
from app.workers import detail_page_job as dpj
from conftest import fake_worker_app, make_settings, worker_job
from test_cut_output_qc_wiring import _RecordingR2, _qc_result

SPEC = face_identity.FaceIdentitySpec("facemarket/loras/m/v1.safetensors", "ohwx man")
REAL = "22222222-2222-4222-8222-222222222222"


# ── 1. cut_generator.repair 가 얼굴 패스를 돈다 ──
class _Gemini:
    async def generate_content_image(self, model, prompt, images, size, **kw):
        return types.SimpleNamespace(image=b"EDITED", mime="image/png")


def test_repair_runs_the_face_pass_on_the_edited_image(monkeypatch):
    calls = []

    async def fake_pass(settings, image, mime, spec, *, outcome=None, url_provider=None, **kw):
        calls.append((image, spec))
        outcome["face_pass"] = "applied"
        return b"EDITED+FACE", "image/png"

    monkeypatch.setattr(cut_generator.face_identity, "apply_face_pass", fake_pass)
    settings = make_settings(gemini_api_key="x", r2_bucket="b", face_identity_enabled=True)
    spec = {"id": "b1", "cutType": "horizon", "direction": "front", "shot": "full",
            "faceExposure": "same", "pose": "auto", "modelId": REAL}
    outcome: dict = {}
    out = asyncio.run(cut_generator.repair(
        settings, _Gemini(), spec, {"clothingType": "top"}, InlineImage("image/png", b"STAGE1"),
        qc_corrections=("fix",), face_identity_spec=SPEC, face_pass_outcome=outcome))
    assert out == (b"EDITED+FACE", "image/png")
    assert calls == [(b"EDITED", SPEC)] and outcome == {"face_pass": "applied"}


def test_repair_without_a_spec_is_unchanged(monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("근거가 없으면 얼굴 패스를 부르지 않는다")

    monkeypatch.setattr(cut_generator.face_identity, "apply_face_pass", boom)
    settings = make_settings(gemini_api_key="x", r2_bucket="b", face_identity_enabled=True)
    spec = {"id": "b1", "cutType": "horizon", "direction": "front", "shot": "full",
            "faceExposure": "same", "pose": "auto", "modelId": REAL}
    out = asyncio.run(cut_generator.repair(
        settings, _Gemini(), spec, {"clothingType": "top"}, InlineImage("image/png", b"STAGE1"),
        qc_corrections=("fix",)))
    assert out == (b"EDITED", "image/png")


# ── 2. 워커: 채택 이미지 ↔ face_pass 메타 ──
def _worn_spec():
    return {"id": "b1", "cutType": "horizon", "direction": "front", "shot": "full",
            "faceExposure": "same", "pose": "auto", "refScope": "all", "modelId": REAL}


def _run(monkeypatch, *, outcomes, best_of=None, cut_qc=None, repair=None, qc_mode="repair"):
    """generate 호출마다 대본(outcomes)의 다음 face_pass 결과를 outcome 에 적고 다른 바이트를 돌려준다."""
    script = list(outcomes)
    generated = []

    async def fake_generate(settings, gemini, cut_spec, product, images, **kw):
        n = len(generated)
        data = f"GEN{n}".encode()
        generated.append(data)
        sink = kw.get("face_pass_outcome")
        if sink is not None and script:
            sink["face_pass"] = script.pop(0)
        return data, "image/png"

    async def default_best_of(settings, product_images, initial, generate_candidate):
        return initial, None, []

    async def fake_emit(*_a, **_k):
        return None

    monkeypatch.setattr(dpj.cut_generator, "generate", fake_generate)
    monkeypatch.setattr(dpj.image_qc, "best_of", best_of or default_best_of)
    monkeypatch.setattr(dpj, "_emit", fake_emit)
    if cut_qc is not None:
        monkeypatch.setattr(dpj.cut_output_qc, "verdict", cut_qc)
    if repair is not None:
        monkeypatch.setattr(dpj.cut_generator, "repair", repair)
    r2 = _RecordingR2([])
    app = fake_worker_app(make_settings(
        gemini_api_key="x", r2_bucket="b", model_image_high="gemini-3-pro-image",
        model_detail_cut="gpt-image-2-2026-04-21", garment_qc_mode="off",
        cut_output_qc_mode=qc_mode, face_identity_enabled=True), r2=r2)
    product_image = InlineImage("image/png", b"PRODUCT")
    prepared = (_worn_spec(), [product_image], "1. PRODUCT — front", True, [product_image],
                None, False, None, None, True)          # real_identity_attached=True
    result = asyncio.run(dpj._gen_cuts(
        app, worker_job(), [prepared], {"clothingType": "top"}, {"fitProfile": {"axes": {"fit": "regular"}}},
        face_identity_spec=SPEC))
    return {"result": result, "saved": r2.saved, "generated": generated,
            "asset_meta": result[1][0]["metadata"] if result[1] else None}


def test_accepted_repair_saves_the_face_passed_edit_and_its_outcome(monkeypatch):
    seen = {}

    async def fake_cut_qc(settings, plan, references, generated):
        seen.setdefault("qc_inputs", []).append(generated.data)
        return _qc_result("framingDirectionFacePose") if generated.data == b"GEN0" else _qc_result()

    async def fake_repair(settings, gemini, cut_spec, product, source, *, qc_corrections, **kw):
        seen["repair_kwargs"] = kw
        kw["face_pass_outcome"]["face_pass"] = "applied"          # 보정본에 얼굴 패스가 돌았다
        return b"EDITED+FACE", "image/png"

    run = _run(monkeypatch, outcomes=["skipped:yaw"], cut_qc=fake_cut_qc, repair=fake_repair)
    assert seen["repair_kwargs"]["face_identity_spec"] is SPEC
    assert seen["qc_inputs"] == [b"GEN0", b"EDITED+FACE"]       # QC 는 얼굴 패스가 끝난 최종본을 본다
    assert run["saved"] == [b"EDITED+FACE"]
    assert run["asset_meta"]["face_pass"] == "applied"           # 채택본(보정)의 결과
    assert run["result"][4][0]["repair"]["finalSource"] == "stage2"


def test_rejected_repair_keeps_stage1_and_its_outcome(monkeypatch):
    async def fake_cut_qc(settings, plan, references, generated):
        return (_qc_result("framingDirectionFacePose") if generated.data == b"GEN0"
                else _qc_result("framingDirectionFacePose", "modelIdentity"))

    async def fake_repair(settings, gemini, cut_spec, product, source, *, qc_corrections, **kw):
        kw.get("face_pass_outcome", {})["face_pass"] = "applied"
        return b"REGRESSED", "image/png"

    run = _run(monkeypatch, outcomes=["skipped:yaw"], cut_qc=fake_cut_qc, repair=fake_repair)
    assert run["saved"] == [b"GEN0"]
    assert run["asset_meta"]["face_pass"] == "skipped:yaw"       # 보정본의 applied 가 새어 들어오지 않는다
    assert run["result"][4][0]["repair"]["finalSource"] == "stage1"


def test_regenerated_repair_records_the_regenerations_outcome(monkeypatch):
    async def fake_cut_qc(settings, plan, references, generated):
        return _qc_result("garmentConstruction") if generated.data == b"GEN0" else _qc_result()

    run = _run(monkeypatch, outcomes=["applied", "fallback:gate_failed"], cut_qc=fake_cut_qc)
    assert run["result"][4][0]["repair"]["route"] == "REGENERATE_FROM_SCRATCH"
    assert run["saved"] == [b"GEN1"]
    assert run["asset_meta"]["face_pass"] == "fallback:gate_failed"


def test_chosen_candidate_keeps_its_own_outcome_not_the_last_generated(monkeypatch):
    async def picking_best_of(settings, product_images, initial, generate_candidate):
        first = await generate_candidate()
        await generate_candidate()                                  # 마지막으로 그린 건 이쪽
        return first, {"chosenIndex": 1}, []

    run = _run(monkeypatch, outcomes=["applied", "skipped:yaw", "applied"],
               best_of=picking_best_of, qc_mode="off")
    assert run["saved"] == [b"GEN1"]
    assert run["asset_meta"]["face_pass"] == "skipped:yaw"


# ── 3. 에디터 새 컷도 같은 규칙(후보 중 채택본의 결과) ──
def test_editor_chosen_candidate_keeps_its_own_outcome(monkeypatch):
    from app.workers import editor_image_job as eij
    from test_cut_input_authority import (
        REAL_CATEGORY, REAL_ENROLLMENT_ID, REAL_LICENSE_ID, REAL_MODEL_ID, _TrackingR2, _patch_editor_common,
    )

    captured = {"settlements": 0}
    _patch_editor_common(monkeypatch, captured)
    script = ["applied", "skipped:yaw", "applied"]
    generated = []

    async def fake_generate(settings, gemini, cut_spec, product, images, **kw):
        data = f"GEN{len(generated)}".encode(); generated.append(data)
        kw["face_pass_outcome"]["face_pass"] = script.pop(0)
        return data, "image/png"

    async def picking_best_of(settings, product_images, initial, generate_candidate):
        first = await generate_candidate()
        await generate_candidate()
        return first, {"chosenIndex": 1}, []

    async def fake_real_refs(conn, selected_model_id, **_kwargs):
        return [{"key": "face-front", "mime": "image/png", "bucket": "face"},
                {"key": "face-sheet", "mime": "image/png", "bucket": "face"}]

    async def fake_license(conn, selected_model_id, *, license_id=None, **_kwargs):
        return {"id": license_id, "model_id": selected_model_id, "status": "active",
                "model_status": "verified", "current_enrollment_id": REAL_ENROLLMENT_ID,
                "match_policy_version": "policy-v1", "unit_price": 10}

    async def noop(*_a, **_k):
        return None

    async def fake_lora(conn, model_id):
        return {"lora_r2_key": "facemarket/loras/m/v1.safetensors", "bucket": "face", "trigger_token": "ohwx man"}

    monkeypatch.setattr(eij.cut_generator, "generate", fake_generate)
    monkeypatch.setattr(eij.image_qc, "best_of", picking_best_of)
    monkeypatch.setattr(eij.identity_source, "resolve_real_model_assets", fake_real_refs)
    monkeypatch.setattr(eij.identity_source, "resolve_enabled_lora", fake_lora)
    monkeypatch.setattr(eij.facemarket, "resolve_model_license", fake_license)
    monkeypatch.setattr(eij.facemarket, "verify_license", noop)
    monkeypatch.setattr(eij.facemarket, "verify_license_local", lambda *_a, **_k: None)
    monkeypatch.setattr(eij.repo, "lock_facemarket_writer_boundary", noop)
    monkeypatch.setattr(face_identity, "reference_embeddings", lambda images, model_dir=None: ())

    public_r2 = _TrackingR2()
    app = fake_worker_app(make_settings(gemini_api_key="x", r2_bucket="b", facemarket_enabled=True,
                                        face_identity_enabled=True), r2=public_r2)
    app.state.r2_face = _TrackingR2()
    asyncio.run(eij.run_editor_image_job(app, worker_job({
        "mode": "new", "cutType": "horizon", "shot": "full", "direction": "front",
        "faceExposure": "show", "modelId": REAL_MODEL_ID, "brandUseCategory": REAL_CATEGORY,
        "_facemarket": {"modelId": REAL_MODEL_ID, "licenseId": REAL_LICENSE_ID},
    })))
    assert generated == [b"GEN0", b"GEN1", b"GEN2"]
    assert captured["finalize"]["image"]["metadata"]["face_pass"] == "skipped:yaw"   # GEN1 의 결과
