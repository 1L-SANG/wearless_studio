"""T3 구조 QC — 순수 로직(family 매핑·기계 종류비교·집계·스키마·검증·블라인드 프롬프트).
실 Gemini judge(async)는 키·비용 필요라 제외(캘리브/실측 세션에서 검증)."""

import pytest

from app.agents import mannequin_structure_qc as SQ
from app.agents.vision_llm import VisionError


# ─────────────── _family_of / classify_type (블라인드 기계 비교) ───────────────

def test_family_of_basic_types():
    assert SQ._family_of("t-shirt") == "top"
    assert SQ._family_of("knit sweater") == "top"
    assert SQ._family_of("hoodie") == "top"
    assert SQ._family_of("jeans") == "pants"
    assert SQ._family_of("slacks") == "pants"
    assert SQ._family_of("skirt") == "skirt"
    assert SQ._family_of("dress") == "dress"
    assert SQ._family_of("coat") == "outer"


def test_family_of_ambiguous_prefers_more_specific():
    # 겹치는 토큰 — outer 가 pants/top 을 이겨야(코트/자켓 우선)
    assert SQ._family_of("denim jacket") == "outer"      # jacket(outer) > denim(pants)
    assert SQ._family_of("shirt dress") == "dress"       # dress > shirt(top)
    assert SQ._family_of("cardigan") == "outer"


def test_family_of_short_sleeve_top_does_not_match_shorts():
    assert SQ._family_of("short sleeve shirt") == "top"
    assert SQ._family_of("short-sleeve t-shirt") == "top"
    assert SQ._family_of("denim shorts") == "pants"


def test_family_of_unknown_is_none():
    assert SQ._family_of("") is None
    assert SQ._family_of("umbrella") is None
    assert SQ._family_of(None) is None


def test_classify_type_match_mismatch_unknown():
    assert SQ.classify_type("knit sweater", "top") == {"match": True, "seenFamily": "top"}
    assert SQ.classify_type("jeans", "top") == {"match": False, "seenFamily": "pants"}
    assert SQ.classify_type("nonsense", "top") == {"match": None, "seenFamily": None}


# ─────────────── aggregate (3-state overallPass) ───────────────

def _v(type_seen="t-shirt", lower="present", prop="ok", fid="preserved", base="preserved"):
    return {"typeSeen": type_seen,
            "lowerBody": {"state": lower, "landmark": "x"},
            "proportions": {"state": prop, "landmark": "x"},
            "garmentFidelity": {"state": fid, "landmark": "x"},
            "mannequinBasePreserved": {"state": base, "landmark": "x"},
            "notes": ""}


def test_aggregate_all_pass():
    r = SQ.aggregate(_v(), "top")
    assert r["overallPass"] is True and r["failureModes"] == [] and r["unjudgeable"] == []


def test_aggregate_type_mismatch_fails():
    r = SQ.aggregate(_v(type_seen="jeans"), "top")
    assert r["overallPass"] is False and "type_misrecognition" in r["failureModes"]


def test_aggregate_missing_lower_body_fails():
    r = SQ.aggregate(_v(lower="cropped"), "top")
    assert r["overallPass"] is False and "missing_lower_body" in r["failureModes"]


def test_aggregate_proportions_and_fidelity_modes():
    r = SQ.aggregate(_v(prop="distorted", fid="altered"), "top")
    assert r["overallPass"] is False
    assert "broken_proportions" in r["failureModes"] and "garment_identity_altered" in r["failureModes"]


def test_aggregate_notvisible_is_unjudgeable_not_fail():
    r = SQ.aggregate(_v(lower="notVisible"), "top")
    assert r["overallPass"] is None            # 판정불가 — fail 아님
    assert "lowerBody" in r["unjudgeable"] and r["failureModes"] == []


def test_aggregate_fail_beats_unjudgeable():
    # 하나라도 확정 실패면 notVisible 있어도 overall False
    r = SQ.aggregate(_v(lower="notVisible", fid="altered"), "top")
    assert r["overallPass"] is False and "garment_identity_altered" in r["failureModes"]


def test_aggregate_type_unknown_is_unjudgeable():
    r = SQ.aggregate(_v(type_seen="nonsense"), "top")
    assert r["overallPass"] is None and "type" in r["unjudgeable"]


def test_aggregate_base_altered_is_advisory_not_gating():
    # base 마네킹 훼손은 modes 에 기록되나 core 통과면 overall 을 뒤집지 않음(identity 분리)
    r = SQ.aggregate(_v(base="altered"), "top")
    assert r["overallPass"] is True and "base_mannequin_altered" in r["failureModes"]


# ─────────────── schema / validate ───────────────

def test_schema_shape_no_length_axis():
    s = SQ.schema()
    props = s["properties"]
    assert "length" not in props                        # garment-only 식별불가 → 제거
    assert set(s["required"]) == {"typeSeen", "lowerBody", "proportions",
                                  "garmentFidelity", "mannequinBasePreserved", "notes"}
    assert props["lowerBody"]["properties"]["state"]["enum"] == ["present", "cropped", "notVisible"]


def test_validate_downgrades_bad_state_to_notvisible():
    raw = _v(lower="maybe")                              # enum 밖
    out = SQ.validate(raw)
    assert out["lowerBody"]["state"] == "notVisible"     # 임의 조작 방지
    assert out["typeSeen"] == "t-shirt"


def test_validate_rejects_non_dict():
    with pytest.raises(VisionError):
        SQ.validate(["not", "a", "dict"])


def test_validate_missing_axis_becomes_notvisible():
    out = SQ.validate({"typeSeen": "coat"})              # 축 누락
    assert out["proportions"]["state"] == "notVisible"


# ─────────────── build_prompt_blind (선언 미노출) ───────────────

def test_build_prompt_blind_hides_declared_and_allows_notvisible():
    p = SQ.build_prompt_blind()
    assert "typeSeen" in p and "notVisible" in p
    assert "WITHOUT any hint" in p                       # 블라인드 계약
    assert "GENERATED" in p and "SOURCE" in p and "BASE" in p
    # 기대 종류(정답)를 프롬프트에 절대 안 박음 — 축 이름만 등장
    assert "expected_family" not in p and "declared" not in p.lower()
