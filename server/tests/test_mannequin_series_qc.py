"""D축 시리즈 일관성 QC 순수 로직 (DB·네트워크 없음)."""
import asyncio

import pytest

from app.agents import mannequin_series_qc as sq
from app.agents.gemini_image import InlineImage
from app.agents.vision_llm import VisionError
from conftest import make_settings


def _cut(candidate, version):
    return {"candidate": candidate, "version": version}


# ── 비교 대상 선택 ────────────────────────────────────────────────────────────

def test_selects_latest_version_per_candidate():
    """구버전에 앵커링하면 셀러가 이미 갈아치운 나쁜 컷에 새 컷을 맞추게 된다."""
    picked = sq.select_reference_cuts([_cut("A", 1), _cut("A", 3), _cut("A", 2)])
    assert picked == [_cut("A", 3)]


def test_caps_reference_count():
    """list_mannequin_cuts 는 전 버전을 무제한 반환한다 — cap 없으면 이미지·비용이 무한 증가."""
    cuts = [_cut(c, 1) for c in "ABCDE"]
    assert len(sq.select_reference_cuts(cuts)) == sq.MAX_REFERENCE_CUTS
    assert len(sq.select_reference_cuts(cuts, limit=2)) == 2


def test_empty_and_malformed_inputs():
    assert sq.select_reference_cuts([]) == []
    assert sq.select_reference_cuts(None) == []
    assert sq.select_reference_cuts([{"version": 2}]) == []  # candidate 없음 → 제외


# ── 스키마·검증 ───────────────────────────────────────────────────────────────

def test_schema_is_strict_and_complete():
    s = sq.schema()
    assert set(s["required"]) == set(s["properties"])
    assert s["additionalProperties"] is False


def test_validate_clamps_range():
    """0-100 은 스키마로 못 건다(_to_gemini_schema 가 minimum/maximum 을 버린다) → 코드 강제."""
    assert sq.validate({"consistency": 150, "inconsistencies": []})["consistency"] == 100
    assert sq.validate({"consistency": -20, "inconsistencies": []})["consistency"] == 0
    assert sq.validate({"consistency": 77.9, "inconsistencies": []})["consistency"] == 77


def test_validate_cleans_reasons():
    out = sq.validate({"consistency": 60, "inconsistencies": ["배경이 더 따뜻함", "   ", ""]})
    assert out["inconsistencies"] == ["배경이 더 따뜻함"]


@pytest.mark.parametrize("junk", [None, "high", True, {}])
def test_validate_rejects_non_numeric(junk):
    """bool 은 int 서브클래스라 True 가 1점으로 새면 안 된다."""
    with pytest.raises(VisionError):
        sq.validate({"consistency": junk, "inconsistencies": []})


# ── 프롬프트 규율 ─────────────────────────────────────────────────────────────

def test_prompt_is_blinded_and_scoped():
    p = sq.build_prompt(2)
    # 어느 쪽이 새 컷인지, 무엇이 정답인지 알려주지 않는다.
    for leak in ("new cut", "newest", "target", "correct version", "should match"):
        assert leak not in p.lower()
    # 의류 자체를 판정 대상에서 배제 — 그건 A축(image_qc) 소관이다.
    assert "garments are NOT what you are judging" in p
    assert "3 e-commerce studio photos" in p  # references 2 + 새 컷 1


def test_prompt_lists_setup_aspects():
    p = sq.build_prompt(1)
    for aspect in ("camera angle", "background color", "lighting direction", "margins"):
        assert aspect in p


# ── judge 스킵 계약 ───────────────────────────────────────────────────────────

def test_judge_returns_none_without_references():
    """첫 컷은 비교 대상이 없다 — 판정 스킵이지 0점이 아니다."""
    out = asyncio.run(sq.judge(make_settings(), InlineImage("image/png", b"x"), []))
    assert out is None
