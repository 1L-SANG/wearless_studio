"""angle_mismatch 는 비차단(advisory) — occlusion/low_resolution/multiple_faces 만 차단.

45↔측면 각도 경계는 비결정적 vision-LLM 분류라 정상 사용자를 오탐 거절하기 쉽다
(단발 판정, temperature/seed 미고정). 실 신원 게이트는 하류 SFace 1:1 매칭이므로
각도 불일치만으로는 업로드를 막지 않고 저장하되 advisory 로만 surface 한다.
occlusion/low_resolution/multiple_faces 는 실제 품질 결함이라 차단을 유지한다.
"""

import asyncio

import app.personalization_qc as pq
from app.personalization_qc import (
    ADVISORY_QC_CODES,
    BLOCKING_QC_CODES,
    QC_CODES,
    FaceQcResult,
)


class _StubSettings:
    gemini_api_key = "stub-key"
    openai_api_key = None


def test_pass_verdict_has_no_reasons_and_passes():
    qc = FaceQcResult("pass", [])
    assert qc.passed is True
    assert qc.blocking_reasons == []
    assert qc.advisory_reasons == []


def test_angle_mismatch_alone_is_advisory_not_blocking():
    qc = FaceQcResult("reject", ["angle_mismatch"])
    assert qc.passed is True  # 각도만으로는 차단하지 않는다
    assert qc.blocking_reasons == []
    assert qc.advisory_reasons == ["angle_mismatch"]


def test_occlusion_blocks():
    qc = FaceQcResult("reject", ["occlusion"])
    assert qc.passed is False
    assert qc.blocking_reasons == ["occlusion"]
    assert qc.advisory_reasons == []


def test_low_resolution_and_multiple_faces_block():
    for code in ("low_resolution", "multiple_faces"):
        qc = FaceQcResult("reject", [code])
        assert qc.passed is False, code
        assert qc.blocking_reasons == [code]


def test_blocking_reason_wins_when_mixed_with_angle():
    qc = FaceQcResult("reject", ["angle_mismatch", "occlusion"])
    assert qc.passed is False  # 차단 사유가 하나라도 있으면 차단
    assert qc.blocking_reasons == ["occlusion"]
    assert qc.advisory_reasons == ["angle_mismatch"]


def test_code_partitions_are_disjoint_and_cover_all_codes():
    # 새 사유코드가 추가되면 반드시 blocking/advisory 중 하나로 분류돼야 한다(오분류 방지).
    assert set(BLOCKING_QC_CODES).isdisjoint(ADVISORY_QC_CODES)
    assert set(BLOCKING_QC_CODES) | set(ADVISORY_QC_CODES) == set(QC_CODES)


def test_reject_with_empty_reasons_coerces_to_occlusion_and_still_blocks(monkeypatch):
    # LLM 이 reject 인데 사유 미상([]) → occlusion 으로 수렴(no_face 흡수). occlusion 은
    # blocking 이므로 새 advisory 분리 후에도 여전히 차단돼야 한다(fail-safe 보존).
    async def fake_analyze(*_args, **_kwargs):
        return {"verdict": "reject", "reasons": []}, "stub-provider"

    monkeypatch.setattr(pq, "analyze_with_fallback", fake_analyze)
    qc = asyncio.run(
        pq.evaluate_face_qc(
            _StubSettings(), image_bytes=b"x", mime="image/jpeg", angle="front"
        )
    )
    assert qc.reasons == ["occlusion"]
    assert qc.blocking_reasons == ["occlusion"]
    assert qc.passed is False
