"""보정(repair) 뒤에도 얼굴 패스가 돌고, QC 는 그 최종본을 보며, 채택 이미지와 face_pass 메타가 짝이 맞는다.

실측(2026-09-11 잡 6c270b84 경로 점검): cut_output_qc 의 EDIT_STAGE1 보정은 cut_generator.repair 로 1차
결과를 다시 그리는데 그 뒤에 얼굴 패스가 없었다 — 보정을 채택하면 얼굴이 gpt-image 로 되돌아간다. 또
face_pass_outcome 한 dict 를 모든 generate 호출이 덮어써서, 재생성이 있으면 원장의 face_pass
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


def _run(monkeypatch, *, outcomes, cut_qc=None, repair=None, qc_mode="repair"):
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

    async def fake_emit(*_a, **_k):
        return None

    monkeypatch.setattr(dpj.cut_generator, "generate", fake_generate)
    monkeypatch.setattr(dpj, "_emit", fake_emit)
    if cut_qc is not None:
        monkeypatch.setattr(dpj.cut_output_qc, "verdict", cut_qc)
    if repair is not None:
        monkeypatch.setattr(dpj.cut_generator, "repair", repair)
    r2 = _RecordingR2([])
    app = fake_worker_app(make_settings(
        gemini_api_key="x", r2_bucket="b", model_image_high="gemini-3-pro-image",
        model_detail_cut="gpt-image-2-2026-04-21",
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
    assert run["generated"] == [b"GEN0"]
    assert seen["repair_kwargs"]["face_identity_spec"] is SPEC
    assert seen["qc_inputs"] == [b"GEN0", b"EDITED+FACE"]       # QC 는 얼굴 패스가 끝난 최종본을 본다
    assert run["saved"] == [b"EDITED+FACE"]
    assert run["asset_meta"]["face_pass"] == "applied"           # 채택본(보정)의 결과
    assert run["result"][3][0]["repair"]["finalSource"] == "stage2"


def test_rejected_repair_keeps_stage1_and_its_outcome(monkeypatch):
    async def fake_cut_qc(settings, plan, references, generated):
        return (_qc_result("framingDirectionFacePose") if generated.data == b"GEN0"
                else _qc_result("framingDirectionFacePose", "modelIdentity"))

    async def fake_repair(settings, gemini, cut_spec, product, source, *, qc_corrections, **kw):
        kw.get("face_pass_outcome", {})["face_pass"] = "applied"
        return b"REGRESSED", "image/png"

    run = _run(monkeypatch, outcomes=["skipped:yaw"], cut_qc=fake_cut_qc, repair=fake_repair)
    assert run["generated"] == [b"GEN0"]
    assert run["saved"] == [b"GEN0"]
    assert run["asset_meta"]["face_pass"] == "skipped:yaw"       # 보정본의 applied 가 새어 들어오지 않는다
    assert run["result"][3][0]["repair"]["finalSource"] == "stage1"


def test_regenerated_repair_records_the_regenerations_outcome(monkeypatch):
    async def fake_cut_qc(settings, plan, references, generated):
        return _qc_result("garmentConstruction") if generated.data == b"GEN0" else _qc_result()

    run = _run(monkeypatch, outcomes=["applied", "fallback:gate_failed"], cut_qc=fake_cut_qc)
    assert run["generated"] == [b"GEN0", b"GEN1"]
    assert run["result"][3][0]["repair"]["route"] == "REGENERATE_FROM_SCRATCH"
    assert run["saved"] == [b"GEN1"]
    assert run["asset_meta"]["face_pass"] == "fallback:gate_failed"


def test_unavailable_repair_keeps_stage1_and_its_face_outcome_after_one_attempt(monkeypatch):
    seen = {"qc": [], "repair": []}

    async def qc(settings, plan, references, generated):
        seen["qc"].append(generated.data)
        return _qc_result("framingDirectionFacePose")

    async def repair(settings, gemini, cut_spec, product, source, **kwargs):
        seen["repair"].append(source.data)
        kwargs["face_pass_outcome"]["face_pass"] = "applied"
        raise TimeoutError("stage2 response unavailable")

    run = _run(monkeypatch, outcomes=["skipped:yaw"], cut_qc=qc, repair=repair)
    assert run["generated"] == seen["qc"] == seen["repair"] == [b"GEN0"]
    assert run["saved"] == [b"GEN0"]
    assert run["asset_meta"]["face_pass"] == "skipped:yaw"
    assert run["result"][3][0]["repair"] == {
        "attempted": True, "route": "EDIT_STAGE1", "accepted": False,
        "finalSource": "stage1", "error": "repair_unavailable",
    }
    assert run["result"][5] == [{"blockId": "b1", "code": "cut_output_qc_repair_unavailable"}]
