"""동일인 검사(identity_low 게이트)는 **기준 임베딩이 전달될 때만** 돈다 — 그 전달이 빠져 있었다.

실측(2026-09-11 잡 6c270b84, applied 3컷): tries[*].identity 가 전부 None. apply_face_pass 가 run_face_pass 에
references 를 넘기지 않아 evaluate_gate 가 신원 비교를 건너뛰었다 — 얼굴이 바뀌었는지 기하·색만 보고 통과시켰다.
기준 = 선택된 실제 모델의 승인된 face_front(비공개 face 버킷, 컷 생성 입력에 이미 들어가는 그 사진).
"""

import asyncio
import types
from dataclasses import replace

import numpy as np
import pytest

from app.agents import face_identity as fi
from conftest import make_settings

SPEC = fi.FaceIdentitySpec("facemarket/loras/m/v1.safetensors", "ohwx man",
                           backend_url="https://pod-8000.proxy.runpod.net/render")
HEALTH = "https://pod-8000.proxy.runpod.net/healthz"
EMB_A = tuple(np.linspace(0.1, 1.0, 128).astype(np.float32).tolist())


@pytest.fixture(autouse=True)
def _reset():
    for memo in (fi._ready_seen, fi._recent_failure, fi._fallback_events):
        memo.clear()
    fi._last_fallback_alert = None
    yield
    for memo in (fi._ready_seen, fi._recent_failure, fi._fallback_events):
        memo.clear()


def _settings(**kw):
    base = {"gemini_api_key": "x", "r2_bucket": "b", "face_identity_enabled": True,
            "face_pass_wait_seconds": 0}
    base.update(kw)
    return make_settings(**base)


# ── 1. spec.references → run_face_pass(references=...) ──
def test_apply_face_pass_forwards_the_spec_references_to_the_gate(monkeypatch):
    fi._ready_seen[HEALTH] = 1e18
    monkeypatch.setattr(fi, "resolve_backend", lambda s, spec: object())
    seen = {}

    def fake_run(image, backend, expression, **kw):
        seen.update(kw)
        return fi.FacePassResult(b"FACE", "image/png", True, {"tries": [{"gate": "ok"}]})

    monkeypatch.setattr(fi, "run_face_pass", fake_run)
    spec = replace(SPEC, references=(EMB_A,))
    asyncio.run(fi.apply_face_pass(_settings(), b"ORIG", "image/png", spec, outcome={}))
    assert seen["references"] == (EMB_A,)


def test_run_face_pass_hands_references_to_evaluate_gate(monkeypatch):
    """run_face_pass 안에서도 게이트까지 그대로 간다(중간에 떨어뜨리지 않는다)."""
    plan = fi.plan_from_box(600, 800, (250.0, 300.0, 120.0, 160.0), yaw_proxy=0.05, eye_dist=50.0)
    from PIL import Image
    img = Image.new("RGB", (600, 800), (120, 110, 100))
    monkeypatch.setattr(fi, "_decode", lambda b: img)
    monkeypatch.setattr(fi, "prepare_image", lambda image, model_dir=None: (img, plan, {"skipped_reason": None, "pose_risk": False}))
    monkeypatch.setattr(fi, "build_control", lambda original, plan, *, crop=None: img)
    monkeypatch.setattr(fi, "composite_with_meta",
                        lambda original, generated, plan, feather=0.0, crop=None: (img, {}))
    gates = []

    def fake_gate(plan, result, model_dir=None, *, references=None, color_ring_mean=None):
        gates.append(references)
        return fi.GateResult(True, "ok", 200.0, 0.0, 0.0, identity=0.81, color_max=0.5)

    monkeypatch.setattr(fi, "evaluate_gate", fake_gate)
    backend = types.SimpleNamespace(render=lambda control, prompt, seed: img)
    res = fi.run_face_pass(b"x", backend, references=(EMB_A,))
    assert res.applied and gates == [(EMB_A,)]
    assert res.meta["tries"][0]["identity"] == 0.81


# ── 2. 승인된 기준 사진 → 임베딩 ──
def test_reference_embeddings_come_from_the_approved_face_front(monkeypatch):
    det = fi.FaceDetection(box=(10.0, 10.0, 100.0, 120.0), landmarks=[(30.0, 40.0)] * 5, score=0.99,
                           yaw_proxy=0.0, eye_dist=40.0)
    from PIL import Image
    monkeypatch.setattr(fi, "_decode", lambda b: Image.new("RGB", (200, 200)))
    monkeypatch.setattr(fi, "detect_face", lambda image, model_dir=None, **k: det if image.size == (200, 200) else None)
    monkeypatch.setattr(fi, "face_embedding", lambda image, det, model_dir=None: np.asarray(EMB_A, np.float32))
    refs = fi.reference_embeddings([b"face_front"], model_dir=None)
    assert refs == (EMB_A,)
    spec = fi.with_references(SPEC, [b"face_front"], model_dir=None)
    assert spec.references == (EMB_A,) and spec.lora_path == SPEC.lora_path


def test_reference_without_a_detectable_face_leaves_references_empty(monkeypatch):
    monkeypatch.setattr(fi, "_decode", lambda b: (_ for _ in ()).throw(ValueError("not an image")))
    assert fi.reference_embeddings([b"garbage"], model_dir=None) == ()
    assert fi.with_references(SPEC, [b"garbage"], model_dir=None).references is None


# ── 3. 상세페이지 워커가 실제로 붙인다 ──
def test_detail_worker_attaches_reference_embeddings_to_the_lora_spec(monkeypatch):
    from app.agents import identity_source
    from app.workers import detail_page_job as dpj
    from test_detail_page_license_face import (
        FACE_BYTES, CURRENT_FACE_KEY, _app, _license_row, _patch_inputs, _patch_snapshot_success,
        _snapshot_job,
    )

    captured = {}
    _patch_inputs(monkeypatch, captured, project={"copywriting": False, "facemarket_license_id": "later-lock"})

    async def fake_gen(settings, gemini, cut_spec, product, images, **kw):
        captured["spec"] = kw.get("face_identity_spec")
        return b"IMG", "image/png"

    monkeypatch.setattr(dpj.cut_generator, "generate", fake_gen)
    row = _license_row()
    app, _ = _app(row)
    app.state.settings = replace(app.state.settings, face_identity_enabled=True)
    _patch_snapshot_success(monkeypatch, row)

    async def fake_lora(conn, model_id):
        return {"lora_r2_key": "facemarket/loras/m/v1.safetensors", "lora_sha256": "ab" * 32,
                "bucket": "face", "trigger_token": "ohwx man"}

    monkeypatch.setattr(identity_source, "resolve_enabled_lora", fake_lora)
    embedded = []

    def fake_embed(images, model_dir=None):
        embedded.append(list(images))
        return (EMB_A,)

    monkeypatch.setattr(fi, "reference_embeddings", fake_embed)

    asyncio.run(dpj.run_detail_page_job(app, _snapshot_job()))

    assert embedded == [[FACE_BYTES]]                       # face_front 한 장(그리드는 기준이 아니다)
    assert captured["spec"].references == (EMB_A,)
    assert app.state.r2_face.gets.count(CURRENT_FACE_KEY) == 1   # 이미 읽은 바이트를 재사용한다


# ── 4. 에디터 워커(새 컷·변형 컷)도 같은 기준을 붙인다 ──
def _editor_lora_row():
    return {"lora_r2_key": "facemarket/loras/m/v1.safetensors", "lora_sha256": "ab" * 32,
            "bucket": "face", "trigger_token": "ohwx man"}


def test_editor_new_cut_attaches_reference_embeddings(monkeypatch):
    from app.workers import editor_image_job as eij
    from conftest import fake_worker_app, make_settings, worker_job
    from test_cut_input_authority import (
        REAL_CATEGORY, REAL_ENROLLMENT_ID, REAL_LICENSE_ID, REAL_MODEL_ID, _TrackingR2, _patch_editor_common,
    )

    captured = {"settlements": 0}
    _patch_editor_common(monkeypatch, captured)

    async def fake_generate(settings, gemini, cut_spec, product, images, **kw):
        captured["spec"] = kw.get("face_identity_spec")
        return b"OUTPUT", "image/png"

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
        return _editor_lora_row()

    embedded = []

    def fake_embed(images, model_dir=None):
        embedded.append(list(images))
        return (EMB_A,)

    monkeypatch.setattr(eij.cut_generator, "generate", fake_generate)
    monkeypatch.setattr(eij.identity_source, "resolve_real_model_assets", fake_real_refs)
    monkeypatch.setattr(eij.identity_source, "resolve_enabled_lora", fake_lora)
    monkeypatch.setattr(eij.facemarket, "resolve_model_license", fake_license)
    monkeypatch.setattr(eij.facemarket, "verify_license", noop)
    monkeypatch.setattr(eij.facemarket, "verify_license_local", lambda *_a, **_k: None)
    monkeypatch.setattr(eij.repo, "lock_facemarket_writer_boundary", noop)
    monkeypatch.setattr(fi, "reference_embeddings", fake_embed)

    app = fake_worker_app(make_settings(gemini_api_key="x", r2_bucket="b", facemarket_enabled=True,
                                        face_identity_enabled=True), r2=_TrackingR2())
    app.state.r2_face = _TrackingR2()
    asyncio.run(eij.run_editor_image_job(app, worker_job({
        "mode": "new", "cutType": "horizon", "shot": "full", "direction": "front",
        "faceExposure": "show", "modelId": REAL_MODEL_ID, "brandUseCategory": REAL_CATEGORY,
        "_facemarket": {"modelId": REAL_MODEL_ID, "licenseId": REAL_LICENSE_ID},
    })))
    assert embedded == [[b"face-front"]]
    assert captured["spec"].references == (EMB_A,)


def test_editor_vary_attaches_reference_embeddings(monkeypatch):
    from app.workers import editor_image_job as eij
    from conftest import fake_worker_app, make_settings, worker_job
    from test_lora_profile_wiring import CATEGORY, LICENSE_ID, MODEL_ID

    captured = {}

    async def fake_get_asset(conn, uid, aid):
        return {"id": aid, "r2_key": "k/source-real", "mime_type": "image/png",
                "metadata": {"facemarket_real_derived": True, "cut_type": "horizon"}}

    async def fake_provenance(conn, uid, aid):
        return {"real_derived": True, "cut_type": "horizon",
                "facemarket": {"modelId": MODEL_ID, "licenseId": LICENSE_ID}}

    async def fake_generate(settings, gemini, source, changes, cut_type, **kwargs):
        captured["spec"] = kwargs.get("face_identity_spec")
        return b"VARIED", "image/png"

    async def fake_resolve(conn, model_id, *, license_id=None, **kwargs):
        return {"id": LICENSE_ID, "model_id": MODEL_ID, "unit_price": None,
                "current_enrollment_id": "33333333-3333-4333-8333-333333333333",
                "match_policy_version": "policy-v1"}

    async def fake_real_refs(conn, model_id, *, enrollment_id, evidence_version):
        captured["assets_lookup"] = (enrollment_id, evidence_version)
        return [{"key": "face-front", "mime": "image/png", "bucket": "face"},
                {"key": "face-sheet", "mime": "image/png", "bucket": "face"}]

    async def fake_lora(conn, model_id):
        return _editor_lora_row()

    async def noop(*_a, **_k):
        return None

    async def _done(value):
        return value

    embedded = []

    def fake_embed(images, model_dir=None):
        embedded.append(list(images))
        return (EMB_A,)

    class _FaceR2:
        def __init__(self):
            self.reads = []

        def get_bytes(self, key):
            self.reads.append(key)
            return key.encode()

    monkeypatch.setattr(eij.repo, "get_asset_for_user", fake_get_asset)
    monkeypatch.setattr(eij.repo, "get_asset_facemarket_provenance", fake_provenance)
    monkeypatch.setattr(eij.cut_variator, "generate", fake_generate)
    monkeypatch.setattr(eij.facemarket, "resolve_model_license", fake_resolve)
    monkeypatch.setattr(eij.facemarket, "verify_license", noop)
    monkeypatch.setattr(eij.facemarket, "verify_license_local", lambda *a, **k: None)
    monkeypatch.setattr(eij.repo, "lock_facemarket_writer_boundary", noop)
    monkeypatch.setattr(eij.repo, "finalize_editor_image_success", lambda conn, **kw: _done({"id": "w1"}))
    monkeypatch.setattr(eij, "_emit", noop)
    monkeypatch.setattr(eij.identity_source, "resolve_enabled_lora", fake_lora)
    monkeypatch.setattr(eij.identity_source, "resolve_real_model_assets", fake_real_refs)
    monkeypatch.setattr(fi, "reference_embeddings", fake_embed)

    app = fake_worker_app(make_settings(gemini_api_key="x", r2_bucket="b", facemarket_enabled=True,
                                        face_identity_enabled=True))
    face_r2 = _FaceR2()
    app.state.r2_face = face_r2
    asyncio.run(eij.run_editor_image_job(app, worker_job({
        "mode": "vary", "source": {"src": "/v1/assets/a1/file", "cutType": "styling"}, "changes": [],
        "brandUseCategory": CATEGORY, "_facemarket": {"modelId": MODEL_ID, "licenseId": LICENSE_ID},
    })))
    assert captured["assets_lookup"] == ("33333333-3333-4333-8333-333333333333", "policy-v1")
    assert face_r2.reads == ["face-front"]                  # face_front 만 읽는다
    assert embedded == [[b"face-front"]]
    assert captured["spec"].references == (EMB_A,)
