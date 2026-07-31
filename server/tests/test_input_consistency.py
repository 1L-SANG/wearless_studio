"""AG-IC 입력 사진 동일성 — 1:1 판정 정리와 묶음 집계.

판정은 앞면 vs 나머지 각 사진을 따로 묻고(_judge_pair) 합친다. 테스트도 두 층으로 나뉜다:
정리 규율(validate_pair)과 집계·부분실패 내성(judge).
"""

import asyncio

from app.agents import input_consistency


def _raw(**over):
    base = {"garment1": "꽃무늬 블라우스", "garment2": "네이비 니트",
            "verdict": "mismatch", "confidence": 0.9,
            "reason": "앞면은 꽃무늬 블라우스인데 이 사진은 네이비 니트예요"}
    base.update(over)
    return base


# ---- validate_pair: 완화 규율 ----

def test_mismatch_survives_when_confident_and_specific():
    out = input_consistency.validate_pair(_raw())
    assert out["verdict"] == "mismatch"
    assert out["reason"] == "앞면은 꽃무늬 블라우스인데 이 사진은 네이비 니트예요"


def test_low_confidence_mismatch_is_downgraded():
    """확신 없는 mismatch 는 경고를 띄울 근거가 못 된다."""
    out = input_consistency.validate_pair(_raw(confidence=0.4))
    assert out["verdict"] == "match"
    assert out["reason"] == ""


def test_threshold_is_inclusive_at_the_boundary():
    out = input_consistency.validate_pair(
        _raw(confidence=input_consistency.MIN_MISMATCH_CONFIDENCE))
    assert out["verdict"] == "mismatch"


def test_reason_is_built_from_descriptions_when_model_leaves_it_empty():
    """두 옷을 기술해 놓고 사유만 비운 회차 — 판정을 버리지 말고 문장을 조립한다."""
    out = input_consistency.validate_pair(_raw(reason=""))
    assert out["verdict"] == "mismatch"
    assert out["reason"] == "앞면은 꽃무늬 블라우스인데 이 사진은 네이비 니트예요"


def test_mismatch_without_any_evidence_is_downgraded():
    out = input_consistency.validate_pair(_raw(reason="", garment1="", garment2=""))
    assert out["verdict"] == "match"


def test_match_verdict_never_carries_reason():
    out = input_consistency.validate_pair(_raw(verdict="match"))
    assert out["reason"] == ""


def test_unknown_verdict_becomes_unclear():
    out = input_consistency.validate_pair({"verdict": "weird", "confidence": 1})
    assert out["verdict"] == "unclear"


def test_garbage_confidence_does_not_crash():
    out = input_consistency.validate_pair(_raw(confidence="high"))
    assert out["confidence"] == 0.0
    assert out["verdict"] == "match"          # 확신 0 → 완화


def test_empty_payload():
    out = input_consistency.validate_pair({})
    assert out["verdict"] == "unclear"
    assert out["confidence"] == 0.0
    assert out["reason"] == ""


# ---- judge: 집계 ----

class _FakeImg:
    pass


def _judge(pair_results, slots):
    """_judge_pair 를 결정적으로 갈아끼워 집계만 검증한다."""
    calls = iter(pair_results)

    async def fake_pair(settings, ref, cand, slot):
        nxt = next(calls)
        if isinstance(nxt, BaseException):
            raise nxt
        return nxt

    original = input_consistency._judge_pair
    input_consistency._judge_pair = fake_pair
    try:
        images = [_FakeImg() for _ in slots]
        return asyncio.run(input_consistency.judge(object(), images, slots))
    finally:
        input_consistency._judge_pair = original


_M = {"verdict": "mismatch", "confidence": 0.9, "reason": "다른 옷이에요",
      "garment1": "a", "garment2": "b"}
_OK = {"verdict": "match", "confidence": 0.95, "reason": "", "garment1": "a", "garment2": "a"}


def test_judge_skips_single_photo():
    """비교 대상이 없으면 호출 자체를 하지 않는다."""
    assert asyncio.run(input_consistency.judge(object(), [_FakeImg()], ["Front"])) is None


def test_judge_skips_on_slot_length_mismatch():
    assert asyncio.run(
        input_consistency.judge(object(), [_FakeImg(), _FakeImg()], ["Front"])) is None


def test_judge_reports_every_offending_photo_with_its_slot():
    out = _judge([_OK, _M], ["Front", "Back", "Detail"])
    assert out["verdict"] == "mismatch"
    # index 는 1-based 이고 1 번은 레퍼런스 — Detail 은 3 번이어야 한다.
    assert out["offending"] == [{"index": 3, "slot": "Detail", "reason": "다른 옷이에요"}]


def test_judge_is_match_only_when_every_pair_matches():
    out = _judge([_OK, _OK], ["Front", "Back", "Detail"])
    assert out["verdict"] == "match"
    assert out["offending"] == []


def test_one_failed_pair_does_not_lose_the_others():
    """한 장 판정이 죽어도 나머지 경고는 살아야 한다 — 관찰 축이 잡을 죽이지 않는다."""
    out = _judge([RuntimeError("boom"), _M], ["Front", "Back", "Detail"])
    assert out["verdict"] == "mismatch"
    assert [o["slot"] for o in out["offending"]] == ["Detail"]


def test_all_pairs_failing_raises():
    """전부 실패면 '이상 없음'으로 위장하지 않는다 — 호출측이 미판정으로 처리하게 올린다."""
    from app.agents.vision_llm import VisionError
    try:
        _judge([RuntimeError("a"), RuntimeError("b")], ["Front", "Back", "Detail"])
    except VisionError:
        return
    raise AssertionError("VisionError 가 올라와야 한다")


# ---- 프롬프트 규율 ----

def test_prompt_carries_the_slot_note_and_the_traps():
    prompt = input_consistency.build_prompt("Back")
    assert "BACK view" in prompt                    # 뒤태가 달라 보이는 건 정상임을 알려야 한다
    assert "magnification" in prompt
    # 색 온도 함정은 실측 오탐의 원인이었다(같은 옷이 브라운/그레이로 보임) — 구체적으로 막는다.
    assert "warmer" in prompt and "cooler" in prompt
    assert "STRUCTURAL" in prompt


def test_prompt_covers_every_slot():
    for slot in ("Front", "Back", "Detail", "Fit"):
        assert input_consistency._SLOT_NOTE[slot] in input_consistency.build_prompt(slot)


def test_config_flag_is_wired_into_load_settings(monkeypatch):
    """dataclass 기본값만 고치고 load_settings 배선을 빼면 테스트는 통과하는데 실서비스는
    옛 값으로 돈다(config.py 의 qc_score 주석에 같은 사고 기록). 로더 경로를 직접 건다."""
    from app.config import load_settings

    monkeypatch.setenv("INPUT_CONSISTENCY", "warn")
    assert load_settings().input_consistency == "warn"
    monkeypatch.setenv("INPUT_CONSISTENCY", "off")
    assert load_settings().input_consistency == "off"
    monkeypatch.setenv("INPUT_CONSISTENCY", "enforce")   # 존재하지 않는 값
    assert load_settings().input_consistency == "shadow"
    monkeypatch.delenv("INPUT_CONSISTENCY")
    assert load_settings().input_consistency == "shadow"  # 기본값 = dataclass 선언과 일치
