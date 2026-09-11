"""워커 → 프롬프트/얼굴 패스 배선 — fm_model_loras 행이 실제로 생성 호출에 실리는가.

값이 없을 때 **키 자체를 생략**하는 것이 계약이다(프롬프트 바이트 동일 + 기존 목 스텁 무회귀).
행 조회·프롬프트 블록 자체는 test_fm_model_loras / test_cut_generator_body_profile 이 본다.
"""

import asyncio

import pytest

from app.agents import cut_variator, face_identity
from app.agents.gemini_image import InlineImage
from app.workers import detail_page_job as dpj
from app.workers import editor_image_job as eij
from conftest import fake_worker_app, make_settings, worker_job

MODEL_ID = "44444444-4444-4444-4444-444444444444"
LICENSE_ID = "66666666-6666-6666-6666-666666666666"
CATEGORY = "fashion"
_HAIR = {"hairLength": "short", "hairColor": "black", "hairTexture": "straight"}
_FACE = {"faceShape": "oval", "jawLine": "defined"}
_SPEC = face_identity.FaceIdentitySpec("facemarket/loras/m/v1.safetensors", "ohwx man")


# ── 상세페이지 _gen_cuts — REAL 착장 컷에만, 값 있을 때만 ──
def _styling_block():
    return {"id": "b1", "cutType": "styling", "direction": "front", "shot": "full",
            "faceExposure": "same", "pose": "auto", "refScope": "all"}


def _run_gen_cuts(monkeypatch, *, real_attached, **profile_kwargs):
    captured = {}

    async def fake_generate(settings, *_args, **kwargs):
        captured["kwargs"] = kwargs
        return b"IMG", "image/png"

    async def fake_emit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(dpj.cut_generator, "generate", fake_generate)
    monkeypatch.setattr(dpj, "_emit", fake_emit)

    app = fake_worker_app(make_settings(
        gemini_api_key="x", r2_bucket="b", garment_qc_mode="off", cut_output_qc_mode="off"))
    product_image = InlineImage("image/png", b"PRODUCT")
    prepared = (_styling_block(), [product_image], "1. MODEL FACE", False, [product_image],
                None, False, None, None, real_attached)
    asyncio.run(dpj._gen_cuts(
        app, worker_job(), [prepared], {"clothingType": "top"},
        {"fitProfile": {"axes": {"fit": "regular"}}}, **profile_kwargs))
    return captured["kwargs"]


def test_gen_cuts_passes_lora_profiles_on_real_identity(monkeypatch):
    kwargs = _run_gen_cuts(
        monkeypatch, real_attached=True,
        body_profile={"heightBucket": "m_180_185"},
        hair_profile=_HAIR, face_shape_profile=_FACE, face_identity_spec=_SPEC)
    assert kwargs["hair_profile"] == _HAIR
    assert kwargs["face_shape_profile"] == _FACE
    assert kwargs["face_identity_spec"] is _SPEC
    assert kwargs["body_profile"] == {"heightBucket": "m_180_185"}


def test_gen_cuts_omits_keys_when_no_lora_row(monkeypatch):
    # 켜진 LoRA 행이 없으면 워커가 전부 None 을 넘긴다 → 키 자체가 없어야 한다.
    kwargs = _run_gen_cuts(monkeypatch, real_attached=True)
    assert "hair_profile" not in kwargs
    assert "face_shape_profile" not in kwargs
    assert "face_identity_spec" not in kwargs


def test_gen_cuts_omits_keys_when_identity_not_attached(monkeypatch):
    # VIRTUAL·NONE 소스 — LoRA 값이 있어도 붙이지 않는다(body_profile 과 같은 게이트).
    kwargs = _run_gen_cuts(
        monkeypatch, real_attached=False,
        body_profile={"heightBucket": "m_180_185"},
        hair_profile=_HAIR, face_shape_profile=_FACE, face_identity_spec=_SPEC)
    assert {"hair_profile", "face_shape_profile", "face_identity_spec", "body_profile"}.isdisjoint(kwargs)


def test_gen_cuts_default_call_is_unchanged(monkeypatch):
    """인자 4개를 더했어도 기존 호출(위치인자만)은 그대로 돈다."""
    kwargs = _run_gen_cuts(monkeypatch, real_attached=True)
    assert kwargs["manifest"] == "1. MODEL FACE"


# ── cut_variator — 변형 컷 얼굴 패스 훅 ──
class _Res:
    image, mime = b"VARIED", "image/png"


class _Gemini:
    async def generate_content_image(self, *_args, **_kwargs):
        return _Res()


def _vary(monkeypatch, *, cut_type, enabled, spec):
    calls = []

    async def fake_pass(settings, image, mime, s, **kw):
        calls.append((image, s))
        return b"FACED", mime

    monkeypatch.setattr(cut_variator.face_identity, "apply_face_pass", fake_pass)
    settings = make_settings(gemini_api_key="x", r2_bucket="b", face_identity_enabled=enabled)
    out = asyncio.run(cut_variator.generate(
        settings, _Gemini(), InlineImage("image/png", b"SRC"), [], cut_type,
        face_identity_spec=spec))
    return out, calls


@pytest.mark.parametrize("cut_type", ["styling", "horizon", "mirror"])
def test_vary_applies_face_pass_on_worn_cuts(monkeypatch, cut_type):
    out, calls = _vary(monkeypatch, cut_type=cut_type, enabled=True, spec=_SPEC)
    assert out == (b"FACED", "image/png")
    assert calls == [(b"VARIED", _SPEC)]


@pytest.mark.parametrize("cut_type,enabled,spec", [
    ("product", True, _SPEC),   # 사람이 없는 컷
    ("detail", True, _SPEC),
    ("styling", False, _SPEC),  # 플래그 off
    ("styling", True, None),    # LoRA 근거 없음(가상모델·미등록)
])
def test_vary_skips_face_pass(monkeypatch, cut_type, enabled, spec):
    out, calls = _vary(monkeypatch, cut_type=cut_type, enabled=enabled, spec=spec)
    assert out == (b"VARIED", "image/png")
    assert calls == []


# ── 에디터 워커(vary) — 켜진 행을 실제로 읽어 cut_variator 로 넘기는가 ──
def _run_editor_vary(monkeypatch, lora_row):
    captured = {}

    async def fake_get_asset(conn, uid, aid):
        return {"id": aid, "r2_key": "k/source-real", "mime_type": "image/png",
                "metadata": {"facemarket_real_derived": True, "cut_type": "horizon"}}

    async def fake_provenance(conn, uid, aid):
        return {"real_derived": True, "cut_type": "horizon",
                "facemarket": {"modelId": MODEL_ID, "licenseId": LICENSE_ID}}

    async def fake_generate(settings, gemini, source, changes, cut_type, **kwargs):
        captured["kwargs"] = kwargs
        return b"VARIED", "image/png"

    async def fake_resolve(conn, model_id, *, license_id=None, **kwargs):
        return {"id": LICENSE_ID, "model_id": MODEL_ID, "unit_price": None}

    async def fake_lora(conn, model_id):
        captured["lookup"] = model_id
        return lora_row

    async def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(eij.repo, "get_asset_for_user", fake_get_asset)
    monkeypatch.setattr(eij.repo, "get_asset_facemarket_provenance", fake_provenance)
    monkeypatch.setattr(eij.cut_variator, "generate", fake_generate)
    monkeypatch.setattr(eij.facemarket, "resolve_model_license", fake_resolve)
    monkeypatch.setattr(eij.facemarket, "verify_license", noop)
    monkeypatch.setattr(eij.facemarket, "verify_license_local", lambda *a, **k: None)
    monkeypatch.setattr(eij.repo, "lock_facemarket_writer_boundary", noop)
    monkeypatch.setattr(eij.repo, "finalize_editor_image_success",
                        lambda conn, **kw: _done({"id": "w1"}))
    monkeypatch.setattr(eij, "_emit", noop)
    monkeypatch.setattr(eij.identity_source, "resolve_enabled_lora", fake_lora)

    app = fake_worker_app(make_settings(
        gemini_api_key="x", r2_bucket="b", facemarket_enabled=True, face_identity_enabled=True))
    asyncio.run(eij.run_editor_image_job(app, worker_job({
        "mode": "vary",
        "source": {"src": "/v1/assets/a1/file", "cutType": "styling"},
        "changes": [],
        "brandUseCategory": CATEGORY,
        "_facemarket": {"modelId": MODEL_ID, "licenseId": LICENSE_ID},
    })))
    return captured


async def _done(value):
    return value


def test_editor_vary_passes_lora_spec_from_db(monkeypatch):
    captured = _run_editor_vary(monkeypatch, {
        "lora_r2_key": "facemarket/loras/m/v1.safetensors", "trigger_token": "ohwx man"})
    assert captured["lookup"] == MODEL_ID
    assert captured["kwargs"]["face_identity_spec"] == _SPEC


def test_editor_vary_omits_spec_without_enabled_row(monkeypatch):
    captured = _run_editor_vary(monkeypatch, None)
    assert captured["lookup"] == MODEL_ID
    assert "face_identity_spec" not in captured["kwargs"]
    assert "ref_bg" in captured["kwargs"]  # 기존 인자는 그대로


# ── 얼굴 패스 결과 기록(자산 메타 + job_events) ──
def test_gen_cuts_passes_the_outcome_sink(monkeypatch):
    """워커가 dict 를 주고 에이전트가 결과를 적는다 — generate() 반환값을 늘리지 않는 이유는
    그 함수를 목(mock)으로 바꿔 쓰는 테스트가 많아서다."""
    kwargs = _run_gen_cuts(
        monkeypatch, real_attached=True, hair_profile=_HAIR, face_identity_spec=_SPEC)
    assert isinstance(kwargs["face_pass_outcome"], dict)


def test_gen_cuts_has_no_sink_without_a_lora(monkeypatch):
    kwargs = _run_gen_cuts(monkeypatch, real_attached=True)
    assert "face_pass_outcome" not in kwargs


def test_worker_writes_face_pass_into_metadata_and_events():
    """셀러 화면은 그대로다 — 원장에만 남는다."""
    import pathlib

    for path in ("app/workers/editor_image_job.py", "app/workers/detail_page_job.py"):
        text = pathlib.Path(path).read_text(encoding="utf-8")
        assert '"face_pass": face_pass_outcome["face_pass"]' in text, path
        assert '"status": "face_pass"' in text, path
