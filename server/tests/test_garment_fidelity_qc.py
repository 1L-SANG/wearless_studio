"""Vision LLM 의미 동일성 QC — 판정→권한 유도, 교정문 구체성, 컷 소비 권한.

여기서 잠그는 것은 "모델이 무엇을 말했는가"가 아니라 **모델의 말에서 무엇을 유도했는가**다.
provider 호출은 전부 목킹한다 — 이 계층의 계약은 순수하다.
"""
import asyncio

import pytest

from app.agents import garment_fidelity_qc as agent
from app.agents.gemini_image import InlineImage
from app.services import garment_fidelity_authority as gfa
from app.services import garment_fidelity_pass as gf_pass
from app.services.mannequin_cut_authority import (
    evaluate_mannequin_cut_authority,
    resolve_billable_charge,
)

ALL_PASS = {c: {"status": "PASS", "evidence": "same"} for c in gfa.CHECKS}


def verdict(**overrides) -> dict:
    checks = {c: dict(v) for c, v in ALL_PASS.items()}
    for name, status in overrides.items():
        checks[name] = {"status": status, "evidence": f"{name} evidence"}
    return {
        "decision": "PASS",
        "checks": checks,
        "failureReasons": [],
        "correctionInstruction": "",
        "confidence": 0.9,
        "promptVersion": agent.PROMPT_VERSION,
    }


# ── 판정 유도 ────────────────────────────────────────────────────────────────

def test_all_checks_pass_allows():
    d = gfa.decide(verdict())
    assert (d.allowed, d.decision) == (True, "PASS")


def test_hard_fail_blocks_even_when_the_model_says_pass():
    """모델의 decision 필드는 권한이 아니다 — 체크가 권한이다."""
    raw = verdict(neckline="FAIL")
    raw["decision"] = "PASS"
    d = gfa.decide(raw)
    assert d.allowed is False
    assert d.decision == "FAIL"
    assert d.model_decision == "PASS"
    assert d.model_agreed is False
    assert d.failed_checks == ("neckline",)


@pytest.mark.parametrize("check", sorted(gfa.HARD_CHECKS))
def test_every_hard_check_blocks_on_fail_and_on_unverifiable(check):
    assert gfa.decide(verdict(**{check: "FAIL"})).allowed is False
    assert gfa.decide(verdict(**{check: "UNVERIFIABLE"})).allowed is False


def test_pattern_layout_is_soft():
    """배치는 드레이프·원근으로 정당하게 움직인다. 존재(patternType)는 hard 로 남는다."""
    d = gfa.decide(verdict(patternLayout="UNVERIFIABLE"))
    assert d.allowed is True
    assert d.soft_issues == ("patternLayout:UNVERIFIABLE",)
    assert gfa.decide(verdict(patternType="UNVERIFIABLE")).allowed is False


def test_unverifiable_and_fail_are_reported_apart():
    d = gfa.decide(verdict(neckline="FAIL", sleeveConstruction="UNVERIFIABLE"))
    assert d.decision == "FAIL"
    assert d.failed_checks == ("neckline",)
    assert d.unverifiable_checks == ("sleeveConstruction",)


def test_missing_verdict_is_not_a_pass():
    d = gfa.decide(None)
    assert d.allowed is False
    assert d.decision == "UNVERIFIABLE"
    assert set(d.unverifiable_checks) == gfa.HARD_CHECKS


def test_unknown_status_degrades_to_unverifiable_not_to_pass():
    raw = verdict()
    raw["checks"]["neckline"] = {"status": "probably fine", "evidence": ""}
    assert gfa.decide(raw).allowed is False


def test_missing_check_key_blocks():
    raw = verdict()
    del raw["checks"]["sleeveConstruction"]
    assert gfa.decide(raw).allowed is False


# ── 교정문 ──────────────────────────────────────────────────────────────────

def test_correction_names_the_failed_properties():
    raw = verdict(neckline="FAIL", protectedComponents="FAIL")
    text = gfa.correction_instruction(raw)
    assert "neckline" in text
    assert "buttons" in text
    assert "Preserve the mannequin" in text


def test_correction_lists_what_must_not_change():
    text = gfa.correction_instruction(verdict(neckline="FAIL"))
    assert "already correct" in text
    assert "baseColor" in text


def test_correction_is_empty_when_nothing_failed():
    assert gfa.correction_instruction(verdict()) == ""


def test_vague_model_instruction_is_dropped():
    raw = verdict(neckline="FAIL")
    raw["correctionInstruction"] = "Improve garment fidelity."
    text = gfa.correction_instruction(raw)
    assert "Improve garment fidelity" not in text
    assert "neckline" in text          # 계약은 여전히 지켜진다


def test_specific_model_instruction_is_kept():
    raw = verdict(neckline="FAIL")
    raw["correctionInstruction"] = "Restore the square neckline with two straight straps."
    assert "square neckline" in gfa.correction_instruction(raw)


@pytest.mark.parametrize("text", [
    "", "Improve garment fidelity.", "Make it look more like the reference.",
    "Be more accurate to the source.", "Fix it.",
])
def test_is_vague_rejects_property_free_sentences(text):
    assert gfa.is_vague(text) is True


def test_is_vague_accepts_a_named_property():
    assert gfa.is_vague("Restore the empire seam under the bust.") is False


# ── 응답 검증 ────────────────────────────────────────────────────────────────

def test_validate_fills_missing_checks_with_unverifiable():
    out = agent.validate({"decision": "PASS", "checks": {}, "failureReasons": [],
                          "correctionInstruction": "", "confidence": 1.0})
    assert set(out["checks"]) == set(gfa.CHECKS)
    assert all(v["status"] == "UNVERIFIABLE" for v in out["checks"].values())
    assert gfa.decide(out).allowed is False


def test_validate_clamps_confidence_and_stamps_prompt_version():
    out = agent.validate({"decision": "PASS", "checks": {}, "failureReasons": [],
                          "correctionInstruction": "", "confidence": 7})
    assert out["confidence"] == 1.0
    assert out["promptVersion"] == agent.PROMPT_VERSION


def test_manifest_numbering_matches_attachment_order():
    refs = [agent.SourceRef("Front", InlineImage("image/png", b"a")),
            agent.SourceRef("Detail", InlineImage("image/png", b"b"))]
    manifest = agent.build_manifest(refs)
    assert "1. SOURCE FRONT" in manifest
    assert "2. SOURCE DETAIL" in manifest
    assert "3. GENERATED" in manifest


def test_sources_are_ordered_front_back_detail():
    refs = [agent.SourceRef("Detail", InlineImage("image/png", b"d")),
            agent.SourceRef("Back", InlineImage("image/png", b"b")),
            agent.SourceRef("Front", InlineImage("image/png", b"f"))]
    assert [r.slot for r in agent.order_sources(refs)] == ["Front", "Back", "Detail"]


def test_schema_declares_every_check():
    props = agent.schema()["properties"]["checks"]["properties"]
    assert set(props) == set(gfa.CHECKS)


# ── 다중 샘플 합의 ────────────────────────────────────────────────────────────

def test_one_failing_sample_fails_the_check():
    """다수결이 아니라 fail-closed.

    실측(2026-08-10 frozen replay): 스트라이프 재디자인은 FAIL/PASS/PASS, 골지→새틴은
    PASS/PASS/FAIL 로 갈렸다. 다수결이었으면 둘 다 출고됐다. 한 번이라도 본 결함은
    두 번의 못 봄으로 취소되지 않는다.
    """
    merged = agent.merge_samples([verdict(), verdict(), verdict(neckline="FAIL")])
    assert merged["checks"]["neckline"]["status"] == "FAIL"
    assert gfa.decide(merged).allowed is False


def test_unanimous_pass_stays_pass():
    merged = agent.merge_samples([verdict(), verdict(), verdict()])
    assert gfa.decide(merged).allowed is True
    assert merged["sampleAgreement"] is True


def test_unverifiable_does_not_outrank_fail():
    merged = agent.merge_samples([verdict(neckline="UNVERIFIABLE"), verdict(neckline="FAIL")])
    assert merged["checks"]["neckline"]["status"] == "FAIL"


def test_merge_records_each_sample_status_for_audit():
    merged = agent.merge_samples([verdict(), verdict(neckline="FAIL")])
    assert merged["checks"]["neckline"]["sampleStatuses"] == ["PASS", "FAIL"]
    assert merged["sampleAgreement"] is False


def test_merge_keeps_the_evidence_of_the_sample_that_saw_it():
    a = verdict()
    b = verdict(neckline="FAIL")
    b["checks"]["neckline"]["evidence"] = "square in source, round in generated"
    merged = agent.merge_samples([a, b])
    assert merged["checks"]["neckline"]["evidence"] == "square in source, round in generated"


def test_merge_takes_the_lowest_confidence():
    a, b = verdict(), verdict()
    a["confidence"], b["confidence"] = 0.9, 0.4
    assert agent.merge_samples([a, b])["confidence"] == 0.4


def test_the_judge_is_asked_at_temperature_zero():
    """샘플이 아니라 판정이 되려면 온도가 고정돼야 한다."""
    assert agent.JUDGE_TEMPERATURE == 0.0


# ── 패스 오케스트레이션 ───────────────────────────────────────────────────────

def run(coro):
    return asyncio.run(coro)


def _img(tag: bytes) -> InlineImage:
    return InlineImage("image/png", tag)


def test_off_writes_nothing():
    async def judge(_):
        raise AssertionError("off 인데 호출됐다")
    assert run(gf_pass.run_fidelity_pass(
        mode="off", candidate=_img(b"c0"), judge_fn=judge)) is None


def test_shadow_judges_once_and_never_corrects():
    calls = []

    async def judge(img):
        calls.append(img)
        return verdict(neckline="FAIL"), "gemini"

    async def correct(_instruction, _image):
        raise AssertionError("shadow 인데 교정이 나갔다")

    out = run(gf_pass.run_fidelity_pass(
        mode="shadow", candidate=_img(b"c0"), judge_fn=judge, correct_fn=correct))
    assert (out.vision_calls, out.correction_calls) == (1, 0)
    assert out.as_scores()["mode"] == "shadow"
    assert out.as_scores()["allowed"] is False


def test_enforce_corrects_once_and_rejudges():
    seen = []

    async def judge(img):
        seen.append(img.data)
        return (verdict() if img.data == b"c1" else verdict(neckline="FAIL")), "gemini"

    async def correct(instruction, _image):
        assert "neckline" in instruction
        return _img(b"c1")

    out = run(gf_pass.run_fidelity_pass(
        mode="enforce", candidate=_img(b"c0"), judge_fn=judge, correct_fn=correct))
    assert seen == [b"c0", b"c1"]
    assert (out.vision_calls, out.correction_calls) == (2, 1)
    assert out.allowed is True
    assert [a.label for a in out.attempts] == ["C0", "C1"]


def test_correction_runs_at_most_once_even_if_it_stays_broken():
    corrections = []

    async def judge(_image):
        return verdict(neckline="FAIL"), "gemini"

    async def correct(_instruction, _image):
        corrections.append(1)
        return _img(b"c1")

    out = run(gf_pass.run_fidelity_pass(
        mode="enforce", candidate=_img(b"c0"), judge_fn=judge, correct_fn=correct))
    assert len(corrections) == 1
    assert out.allowed is False
    assert out.as_scores()["decision"] == "FAIL"


def test_denied_correction_leaves_the_first_verdict_standing():
    async def judge(_image):
        return verdict(neckline="FAIL"), "gemini"

    async def correct(_instruction, _image):
        return None                     # 예산 거부

    out = run(gf_pass.run_fidelity_pass(
        mode="enforce", candidate=_img(b"c0"), judge_fn=judge, correct_fn=correct))
    assert out.correction_calls == 0
    assert out.allowed is False
    assert out.as_scores()["correctionDeniedReason"] == "correction_unavailable"


def test_the_last_verdict_wins_even_when_it_is_worse():
    """C1 이 더 나빠도 출고되는 이미지는 C1 이다 — 판정과 출고본이 갈리면 안 된다."""
    async def judge(img):
        return (verdict(neckline="FAIL") if img.data == b"c1"
                else verdict(sleeveConstruction="FAIL")), "gemini"

    async def correct(_instruction, _image):
        return _img(b"c1")

    out = run(gf_pass.run_fidelity_pass(
        mode="enforce", candidate=_img(b"c0"), judge_fn=judge, correct_fn=correct))
    assert out.final.label == "C1"
    assert out.as_scores()["failedChecks"] == ["neckline"]


def test_judge_failure_is_recorded_as_errored_not_as_pass():
    async def judge(_image):
        raise RuntimeError("provider down")

    out = run(gf_pass.run_fidelity_pass(
        mode="enforce", candidate=_img(b"c0"), judge_fn=judge))
    scores = out.as_scores()
    assert scores["errored"] is True
    assert scores["allowed"] is False


def test_correction_prompt_carries_the_instruction_and_the_manifest():
    manifest = gf_pass.build_correction_manifest(["Front", "Detail"])
    prompt = gf_pass.render_correction_prompt("- restore the square neckline", manifest)
    assert "1. CURRENT CUT" in prompt
    assert "2. SOURCE FRONT" in prompt
    assert "restore the square neckline" in prompt
    assert "${" not in prompt


# ── 컷 소비 권한 · 청구 ───────────────────────────────────────────────────────

def _scores(mode: str, **overrides) -> dict:
    node = {"version": gfa.VERSION, "mode": mode, "errored": False,
            "decision": "PASS", "allowed": True}
    node.update(overrides)
    return {"outcome": "auto_pass", "garmentFidelityQc": node}


def test_enforce_fail_blocks_cut_consumption():
    a = evaluate_mannequin_cut_authority(_scores("enforce", decision="FAIL", allowed=False))
    assert a.allowed is False
    assert a.reason == gfa.REASON_SEMANTIC_FAIL


def test_enforce_unverifiable_blocks_cut_consumption():
    a = evaluate_mannequin_cut_authority(
        _scores("enforce", decision="UNVERIFIABLE", allowed=False))
    assert a.allowed is False
    assert a.reason == gfa.REASON_SEMANTIC_UNVERIFIABLE


def test_enforce_but_not_measured_blocks():
    a = evaluate_mannequin_cut_authority(
        _scores("enforce", errored=True, decision="UNVERIFIABLE", allowed=False))
    assert a.allowed is False
    assert a.reason == gfa.REASON_SEMANTIC_NOT_MEASURED


def test_shadow_never_blocks():
    assert evaluate_mannequin_cut_authority(
        _scores("shadow", decision="FAIL", allowed=False)).allowed is True


def test_legacy_cut_without_the_key_is_still_allowed():
    assert evaluate_mannequin_cut_authority({"outcome": "auto_pass"}).allowed is True


def test_a_cut_records_the_mode_it_ran_under():
    """플래그를 올린다고 과거 shadow 컷이 소급해서 막히면 안 된다."""
    assert evaluate_mannequin_cut_authority(
        _scores("shadow", decision="FAIL", allowed=False)).allowed is True


def test_a_blocked_candidate_is_not_billable():
    blocked = {"qc_scores": _scores("enforce", decision="FAIL", allowed=False)}
    charge = resolve_billable_charge([blocked], 2)
    assert (charge.charge, charge.consumable) == (0, 0)


def test_a_passing_candidate_is_billable():
    ok = {"qc_scores": _scores("enforce")}
    charge = resolve_billable_charge([ok], 2)
    assert (charge.charge, charge.consumable) == (2, 1)
