"""얼굴 패스를 못 한 컷은 실패한다 — 그때 셀러가 보는 문구.

일반 문구("이미지 생성 중 오류가 발생했어요")로 끝내면 셀러는 두 가지를 모른다:
왜 안 나왔는지, 그리고 **돈은 어떻게 됐는지**. 크레딧은 예약이 풀려 돌아가는데(환불 경로는
repo._finalize_job_failure 가 charge=0 으로 정산) 화면에 그 말이 없으면 문의가 된다.

동시에 파드·라이선스 같은 우리 인프라 사정은 화면에 나가면 안 된다(2026-09-14 제품 결정).
사유(pod_not_ready · backend_error · gate_failed · skipped_yaw)는 로그와 원장에만 남는다.
"""

import asyncio
import pathlib

import pytest

from app.agents import face_identity
from app.workers import editor_image_job as eij
from conftest import fake_worker_app, make_settings, worker_job
from test_cut_input_authority import (
    REAL_CATEGORY, REAL_ENROLLMENT_ID, REAL_LICENSE_ID, REAL_MODEL_ID,
    _TrackingR2, _patch_editor_common,
)


def test_the_code_and_wording_are_their_own():
    code, message = eij._FACE_PASS_FAILURE
    assert code == "face_pass_unavailable" and code != eij._GENERIC_FAILURE[0]
    assert "크레딧" in message and "차감되지 않" in message
    # 인프라 사정은 안 나간다
    for word in ("파드", "라이선스", "pod", "holder", "LoRA"):
        assert word not in message


def test_the_generic_path_is_untouched():
    """다른 실패는 그대로 일반 문구 — 이 변경은 얼굴 패스 실패에만 걸린다."""
    assert eij._seller_facing_failure("holder_starting", "x") == eij._GENERIC_FAILURE
    assert eij._seller_facing_failure("mannequin_quality_failed", "품질") == (
        "mannequin_quality_failed", "품질")


def test_the_job_fails_with_that_code_and_refunds(monkeypatch):
    """실존 컷이 얼굴을 못 받으면 잡 실패 + 예약 해제 + 전용 코드/문구."""
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

    async def already_up(app, **kwargs):
        return True

    async def fake_refs(conn, model_id, *, enrollment_id, evidence_version):
        return [{"key": "face-front", "mime": "image/png", "bucket": "face"}]

    async def gate_failed(*a, **kw):
        raise face_identity.FacePassUnavailable("gate_failed")

    async def fake_failure(conn, **kwargs):
        captured["failure"] = kwargs
        return {"status": "failed"}

    async def forbidden_success(*a, **kw):
        raise AssertionError("얼굴을 못 받은 컷을 성공으로 종결하면 안 된다")

    async def fake_settlement(*a, **kw):
        captured["settlement"] += 1

    monkeypatch.setattr(eij.facemarket, "resolve_model_license", fake_resolve)
    monkeypatch.setattr(eij.facemarket, "verify_license", passing_verify)
    monkeypatch.setattr(eij.facemarket, "wait_for_holder", already_up)
    monkeypatch.setattr(eij.identity_source, "resolve_real_model_assets", fake_refs)
    monkeypatch.setattr(eij.cut_generator, "generate", gate_failed)
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
    assert captured["failure"]["reserved"] == 7
    assert captured["failure"]["code"] == "face_pass_unavailable"
    assert "크레딧" in captured["failure"]["message"]
    assert "gate_failed" not in captured["failure"]["message"]


def test_every_unavailable_reason_reaches_the_same_seller_wording():
    """사유가 넷이어도 셀러 문구는 하나다 — 화면에서 우리 사정이 갈라지면 안 된다."""
    text = pathlib.Path(eij.__file__).read_text(encoding="utf-8")
    assert "isinstance(e, face_identity.FacePassUnavailable)" in text
    branch = text.split("isinstance(e, face_identity.FacePassUnavailable)")[1][:400]
    assert "_FACE_PASS_FAILURE" in branch
    # 사유는 로그로만
    assert "log.warning" in branch


def test_the_detail_page_still_empties_the_cut_without_charging():
    """상세페이지는 잡을 죽이지 않고 그 컷만 비운다 — 이 PR 이 그 규칙을 바꾸지 않는다."""
    from app.workers import detail_page_job as dpj

    text = pathlib.Path(dpj.__file__).read_text(encoding="utf-8")
    branch = text.split("except face_identity.FacePassUnavailable as e:")[1][:600]
    assert '"status": "cut_failed"' in branch and "return None" in branch
