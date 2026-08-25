"""얼굴 QC 각도 판정 — front vs turned 거친 2버킷만 차단.

45˚ vs 90˚(45도 vs 측면)는 비결정적 vision-LLM 분류라 정상 사용자를 오탐 거절하기 쉬워
아예 구분하지 않는다. angle45·side 는 둘 다 'turned' 라 서로 넣어도 통과. 대신 정면↔돌린얼굴
불일치(측면 칸에 정면 등)만 angle_mismatch 로 차단한다. 방향 판정은 LLM 에게 슬롯 대조를
시키지 않고 실제 방향(front/turned)만 물은 뒤 파이썬에서 결정론적으로 대조한다.
occlusion/low_resolution/multiple_faces 는 실제 품질 결함이라 항상 차단.
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


def _stub_vision(monkeypatch, orientation, defects=()):
    async def fake_analyze(*_args, **_kwargs):
        return {"orientation": orientation, "defects": list(defects)}, "stub-provider"

    monkeypatch.setattr(pq, "analyze_with_fallback", fake_analyze)


def _qc(angle, orientation, defects=(), monkeypatch=None):
    _stub_vision(monkeypatch, orientation, defects)
    return asyncio.run(
        pq.evaluate_face_qc(_StubSettings(), image_bytes=b"x", mime="image/jpeg", angle=angle)
    )


def test_pass_verdict_has_no_reasons_and_passes():
    qc = FaceQcResult("pass", [])
    assert qc.passed is True
    assert qc.blocking_reasons == []


def test_angle_mismatch_is_blocking():
    qc = FaceQcResult("reject", ["angle_mismatch"])
    assert qc.passed is False  # 각도 불일치는 이제 차단
    assert qc.blocking_reasons == ["angle_mismatch"]


def test_occlusion_low_res_multiple_faces_block():
    for code in ("occlusion", "low_resolution", "multiple_faces"):
        qc = FaceQcResult("reject", [code])
        assert qc.passed is False, code
        assert qc.blocking_reasons == [code]


def test_code_partitions_are_disjoint_and_cover_all_codes():
    # 새 사유코드가 추가되면 반드시 blocking/advisory 중 하나로 분류돼야 한다(오분류 방지).
    assert set(BLOCKING_QC_CODES).isdisjoint(ADVISORY_QC_CODES)
    assert set(BLOCKING_QC_CODES) | set(ADVISORY_QC_CODES) == set(QC_CODES)


def test_front_slot_accepts_front(monkeypatch):
    qc = _qc("front", "front", monkeypatch=monkeypatch)
    assert qc.passed is True
    assert qc.reasons == []


def test_front_slot_rejects_turned(monkeypatch):
    qc = _qc("front", "turned", monkeypatch=monkeypatch)
    assert qc.passed is False
    assert qc.reasons == ["angle_mismatch"]


def test_turned_slots_reject_front(monkeypatch):
    # 정면 사진을 45도/측면 칸에 넣으면 거절 — 바로 이 케이스가 사용자가 신고한 버그.
    for slot in ("angle45", "side"):
        qc = _qc(slot, "front", monkeypatch=monkeypatch)
        assert qc.passed is False, slot
        assert qc.reasons == ["angle_mismatch"], slot


def test_45_and_side_are_interchangeable(monkeypatch):
    # 45도/측면은 둘 다 'turned' — 서로 넣어도 통과(오탐 없음).
    for slot in ("angle45", "side"):
        qc = _qc(slot, "turned", monkeypatch=monkeypatch)
        assert qc.passed is True, slot
        assert qc.reasons == [], slot


def test_defect_blocks_even_when_orientation_matches(monkeypatch):
    qc = _qc("front", "front", defects=["occlusion"], monkeypatch=monkeypatch)
    assert qc.passed is False
    assert "occlusion" in qc.reasons


def test_defect_and_angle_both_reported(monkeypatch):
    qc = _qc("side", "front", defects=["low_resolution"], monkeypatch=monkeypatch)
    assert qc.passed is False
    assert set(qc.reasons) == {"low_resolution", "angle_mismatch"}
