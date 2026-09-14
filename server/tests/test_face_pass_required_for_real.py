"""셀러는 "라이선스 확인 중"·"켜는 중"을 몰라야 하고, 준비가 안 됐으면 **원본이 나가면 안 된다**.

2026-09-14 제품 결정. 두 갈래다.

① 라이선스 확인(holder)은 셀러 요청 경로에서 빠진다 — 라우트는 DB 게이트만 하고, VC 확인은
   잡이 wait_for_holder 뒤에 한다. fail-closed 는 그대로: 확인 못 하면 컷을 안 내보내고 환불한다.
② 얼굴 파드가 상한까지 안 뜨면 그 컷은 **실패**다. 원본은 provider 가 그린 남의 얼굴이라,
   그게 나가면 셀러는 산 적 없는 얼굴을 받고 라이선스·정산이 거짓이 된다.
"""

import asyncio
import pathlib

import pytest

from app.agents import cut_generator, face_identity
from app.workers import detail_page_job as dpj
from app.workers import editor_image_job as eij
from conftest import fake_worker_app, make_settings, worker_job
from test_cut_input_authority import (
    REAL_CATEGORY, REAL_ENROLLMENT_ID, REAL_LICENSE_ID, REAL_MODEL_ID,
    _TrackingR2, _patch_editor_common,
)


def _source(module):
    return pathlib.Path(module.__file__).read_text(encoding="utf-8")


# ── ① 셀러 요청 경로에서 holder 가 빠졌는가 ──────────────────────────────────
def test_the_job_waits_for_the_holder_before_it_verifies():
    """라우트가 확인을 안 하므로 잡이 해야 한다 — 그리고 기다린 **뒤**에 물어야 의미가 있다."""
    for module, entry in ((eij, "async def run_editor_image_job"),
                          (dpj, "async def run_detail_page_job")):
        body = _source(module)[_source(module).index(entry):]
        assert body.index("facemarket.wait_for_holder(app)") < body.index("facemarket.verify_license("), module.__name__


def test_the_seller_never_sees_the_holder_wording():
    """홀더가 켜지는 중이라는 건 우리 인프라 사정이다 — 잡 실패는 일반 문구로 나간다."""
    for module in (eij, dpj):
        text = _source(module)
        assert '_INTERNAL_FAILURE_CODES = {"holder_starting", "holder_unavailable"}' in text, module.__name__
    # 라우트는 아예 holder 를 안 부르므로 그 코드를 만들 일이 없다
    from app import routes

    assert "await facemarket.verify_license(" not in _source(routes)


# ── ② 파드 미준비 컷은 원본으로 나가지 않는다 ───────────────────────────────
def test_the_generator_does_not_swallow_the_face_pass_failure():
    """cut_generator 가 삼키면 원본이 그대로 나간다 — 예외를 감싸는 try 가 없어야 한다."""
    text = _source(cut_generator)
    for chunk in text.split("await face_identity.apply_face_pass(")[1:]:
        head = chunk[:400]
        assert "except" not in head, "얼굴 패스 호출 직후에 예외를 삼키면 안 된다"


def test_a_cut_without_a_lora_row_never_reaches_the_face_pass():
    """가상 모델·LoRA 없는 실존 모델은 그대로다 — 이 PR 은 그 경로를 건드리지 않는다.

    근거 행(fm_model_loras)이 없으면 spec 자체가 만들어지지 않아 apply_face_pass 가 아예
    안 불린다. 그래서 apply_face_pass 안의 새 규칙(파드 미준비 = 실패)은 **실존 LoRA 컷에만**
    걸린다 — 가상모델 JSON 경로는 이미 삭제됐다(_face_identity_spec docstring).
    """
    settings = make_settings(gemini_api_key="x", r2_bucket="b", face_identity_enabled=True)
    assert cut_generator._face_identity_spec(
        settings, {"cutType": "horizon", "shot": "full"}, "top", None) is None


def test_the_detail_worker_does_not_retry_a_cut_that_cannot_get_its_face():
    """재시도하면 Gemini 호출을 또 태우고 같은 상한을 다시 기다린다 — 그 컷을 비운다(미차감)."""
    text = _source(dpj)
    assert "except face_identity.FacePassUnavailable as e:" in text
    branch = text.split("except face_identity.FacePassUnavailable as e:")[1][:600]
    assert '"status": "cut_failed"' in branch
    assert "return None" in branch


def test_the_editor_job_refunds_when_the_face_pass_cannot_run(monkeypatch):
    """실존 컷이 얼굴을 못 받으면 잡 실패 + 예약 해제. 출력도 정산도 없다."""
    captured = {"settlement": 0}
    _patch_editor_common(monkeypatch, captured)

    async def fake_resolve(conn, model_id, *, license_id=None, **_kwargs):
        return {
            "id": REAL_LICENSE_ID, "model_id": REAL_MODEL_ID, "model_status": "verified",
            "status": "active", "current_enrollment_id": REAL_ENROLLMENT_ID,
            "match_policy_version": "policy-v1", "unit_price": 10,
        }

    async def passing_verify(app, row, **kwargs):
        return None

    async def fake_refs(conn, model_id, *, enrollment_id, evidence_version):
        return [{"key": "face-front", "mime": "image/png", "bucket": "face"},
                {"key": "face-grid", "mime": "image/png", "bucket": "face"}]

    async def no_face(*a, **kw):
        raise face_identity.FacePassUnavailable("pod_not_ready")

    async def fake_failure(conn, **kwargs):
        captured["failure"] = kwargs
        return {"status": "failed"}

    async def forbidden_success(*a, **kw):
        raise AssertionError("얼굴을 못 받은 컷을 성공으로 종결하면 안 된다")

    async def fake_settlement(*a, **kw):
        captured["settlement"] += 1

    monkeypatch.setattr(eij.facemarket, "resolve_model_license", fake_resolve)
    monkeypatch.setattr(eij.facemarket, "verify_license", passing_verify)
    async def already_up(app, **kwargs):
        return True

    monkeypatch.setattr(eij.facemarket, "wait_for_holder", already_up)
    monkeypatch.setattr(eij.identity_source, "resolve_real_model_assets", fake_refs)
    monkeypatch.setattr(eij.cut_generator, "generate", no_face)
    monkeypatch.setattr(eij.repo, "finalize_editor_image_failure", fake_failure)
    monkeypatch.setattr(eij.repo, "finalize_editor_image_success", forbidden_success)
    monkeypatch.setattr(eij.facemarket, "record_license_settlement", fake_settlement)

    app = fake_worker_app(
        make_settings(gemini_api_key="x", r2_bucket="b", facemarket_enabled=True),
        r2=_TrackingR2())
    app.state.r2_face = _TrackingR2()
    app.state.fm_chain = object()

    asyncio.run(eij.run_editor_image_job(app, worker_job({
        "mode": "new", "cutType": "horizon", "shot": "full",
        "modelId": REAL_MODEL_ID, "brandUseCategory": REAL_CATEGORY,
        "_facemarket": {"modelId": REAL_MODEL_ID, "licenseId": REAL_LICENSE_ID},
    }, credits_reserved=7)))

    assert captured["settlement"] == 0
    assert captured["failure"]["reserved"] == 7, "예약을 풀어야 크레딧이 돌아간다"
    # 셀러에게는 일반 실패 문구 — 파드·홀더 사정은 화면에 안 나간다
    assert "파드" not in captured["failure"]["message"]
    assert "라이선스" not in captured["failure"]["message"]


def test_the_wait_budget_is_separate_and_longer_than_the_ordinary_one():
    """새 호스트 콜드스타트 실측 549초 — 일반 대기(300)로는 못 기다린다."""
    settings = make_settings(gemini_api_key="x", r2_bucket="b")
    assert settings.face_pass_real_wait_seconds == 600
    assert settings.face_pass_real_wait_seconds > settings.face_pass_wait_seconds
    assert face_identity.FACE_PASS_REAL_WAIT_SECONDS_DEFAULT == 600


def test_refund_comes_from_releasing_the_reservation():
    """환불 경로 근거: 실패 종결이 charge=0 으로 예약을 정산한다(= 돌려준다)."""
    from app import repo

    text = pathlib.Path(repo.__file__).read_text(encoding="utf-8")
    block = text[text.index("async def _finalize_job_failure"):][:1400]
    assert "_settle_credits(" in block and "charge=0" in block
    for name in ("finalize_editor_image_failure", "finalize_detail_page_failure"):
        body = text[text.index(f"async def {name}"):][:900]
        assert '"reserved": reserved' in body, name
