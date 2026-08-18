"""가슴 보정 사전 게이트 (2026-08-19 오너 승인) — untuck 게이트와 같은 비대칭 규약.

근거 = 8/1~ 실측: 보정 적용 66건 중 ~31건(47%)이 회귀 판정으로 폐기 — 45~65초·이미지 콜을
쓰고 버렸다. 게이트는 값싼 판정으로 "이 컷은 이미 가슴 볼륨이 충분히 표현돼 있다"를 묻고,
**확신에 찬 adequate 만** 편집을 스킵한다. insufficient/unclear/판정실패/게이트 off 는 전부
기존 동작(편집 실행) — 게이트가 완전히 틀려도 최악이 오늘 상태다.
"""

import asyncio
import types

import pytest

from app.agents import mannequin_bust
from app.workers import mannequin_job as mj


# ---------------------------------------------------------------- 순수 판정 규칙


def test_gate_skips_only_on_confident_adequate():
    t = mannequin_bust.GATE_SKIP_CONFIDENCE
    assert mannequin_bust.gate_skips({"verdict": "adequate", "confidence": t})
    assert not mannequin_bust.gate_skips({"verdict": "adequate", "confidence": t - 0.01})
    assert not mannequin_bust.gate_skips({"verdict": "insufficient", "confidence": 1.0})
    assert not mannequin_bust.gate_skips({"verdict": "unclear", "confidence": 1.0})
    assert not mannequin_bust.gate_skips({"verdict": "gate_error", "confidence": 0.0})


def test_validate_gate_normalizes_hostile_raw():
    out = mannequin_bust.validate_gate({"verdict": "ADEQUATE!!", "confidence": 5})
    assert out == {"verdict": "unclear", "confidence": 0.0}
    out = mannequin_bust.validate_gate({"verdict": "adequate", "confidence": 0.9})
    assert out == {"verdict": "adequate", "confidence": 0.9}
    assert mannequin_bust.validate_gate(None) == {"verdict": "unclear", "confidence": 0.0}


def test_judge_gate_sends_single_image_and_model_override(monkeypatch):
    sent = {}

    async def fake_fallback(settings, prompt, images, schema, thinking_level=None, models=None):
        sent.update(prompt=prompt, images=images, models=models)
        return {"verdict": "adequate", "confidence": 0.9}, "gemini"

    monkeypatch.setattr(mannequin_bust, "analyze_with_fallback", fake_fallback)
    cut = mj.InlineImage("image/png", b"cut")
    s = types.SimpleNamespace(mannequin_bust_gate_model="gemini-3.5-flash-lite")
    out = asyncio.run(mannequin_bust.judge_gate(s, cut))
    assert out == {"verdict": "adequate", "confidence": 0.9}
    assert sent["images"] == [cut], "생성본 1장만 — 과제 1개 원칙"
    assert sent["models"] == {"gemini": "gemini-3.5-flash-lite"}
    assert "adequate" in sent["prompt"] and "insufficient" in sent["prompt"]

    sent.clear()
    asyncio.run(mannequin_bust.judge_gate(
        types.SimpleNamespace(mannequin_bust_gate_model=""), cut))
    assert sent["models"] is None


# ---------------------------------------------------------------- 워커 배선


def _bust(monkeypatch, *, gate_mode, judge=None, judge_raises=False):
    sent = {"judge_calls": 0, "edit_calls": 0, "events": []}

    class _Gemini:
        async def generate_content_image(self, model, prompt, images, size, aspect_ratio=None):
            sent["edit_calls"] += 1
            return types.SimpleNamespace(image=b"busted", mime="image/png")

    async def fake_judge(settings, cut_image):
        sent["judge_calls"] += 1
        if judge_raises:
            raise RuntimeError("gate judge down")
        return dict(judge)

    async def fake_emit(pool, job_id, et, payload):
        sent["events"].append(payload)

    monkeypatch.setattr(mannequin_bust, "judge_gate", fake_judge)
    monkeypatch.setattr(mj, "_emit", fake_emit)

    s = types.SimpleNamespace(
        mannequin_bust_pass="on", mannequin_bust_gate=gate_mode,
        mannequin_max_attempts=2, mannequin_image_size="1K",
        mannequin_aspect_ratio="2:3", model_image_high="gemini-3-pro-image",
        model_image_light="gemini-3.1-flash-image", model_text="gpt-5.4-mini")
    res = types.SimpleNamespace(image=b"cut", mime="image/png")
    out, spent = asyncio.run(mj._apply_bust_pass(
        pool=None, gemini=_Gemini(), s=s, job_id="j1", candidate="A", attempt=1,
        base_gender="women", res=res, calls_spent=0, clothing_type="top"))
    events = [e for e in sent["events"] if e.get("status") == "bust_pass"]
    return out, spent, sent, events


def test_gate_confident_adequate_skips_edit_and_budget(monkeypatch):
    out, spent, sent, events = _bust(
        monkeypatch, gate_mode="on", judge={"verdict": "adequate", "confidence": 0.95})
    assert sent["judge_calls"] == 1
    assert sent["edit_calls"] == 0, "확신에 찬 adequate 면 45~65초 편집을 건너뛴다"
    assert out.image == b"cut" and spent is False, "예산도 쓰지 않는다"
    assert len(events) == 1
    assert events[0]["outcome"] == "skipped_gate"
    assert events[0]["bust_gate"] == {"verdict": "adequate", "confidence": 0.95}


@pytest.mark.parametrize("judge", [
    {"verdict": "insufficient", "confidence": 0.99},
    {"verdict": "unclear", "confidence": 0.99},
    {"verdict": "adequate", "confidence": 0.5},
])
def test_gate_uncertain_or_insufficient_runs_edit(monkeypatch, judge):
    out, spent, sent, events = _bust(monkeypatch, gate_mode="on", judge=judge)
    assert sent["edit_calls"] == 1 and spent is True
    assert out.image == b"busted"
    assert events[0]["outcome"] == "applied"
    assert events[0]["bust_gate"]["verdict"] == judge["verdict"]


def test_gate_error_fails_open_to_edit(monkeypatch):
    out, spent, sent, events = _bust(monkeypatch, gate_mode="on", judge_raises=True)
    assert sent["edit_calls"] == 1 and spent is True
    assert events[0]["bust_gate"]["verdict"] == "gate_error"


def test_gate_off_never_judges(monkeypatch):
    out, spent, sent, events = _bust(
        monkeypatch, gate_mode="off", judge={"verdict": "adequate", "confidence": 1.0})
    assert sent["judge_calls"] == 0, "off 면 판정 콜 자체가 없다 — 기존 동작 그대로"
    assert sent["edit_calls"] == 1
    assert "bust_gate" not in events[0]


# ---------------------------------------------------------------- config 배선


def test_bust_gate_flag_wiring(monkeypatch):
    from app.config import load_settings
    for key in ("MANNEQUIN_BUST_GATE", "MANNEQUIN_BUST_GATE_MODEL"):
        monkeypatch.delenv(key, raising=False)
    s = load_settings()
    assert s.mannequin_bust_gate == "off"
    assert s.mannequin_bust_gate_model == ""

    monkeypatch.setenv("MANNEQUIN_BUST_GATE", "on")
    assert load_settings().mannequin_bust_gate == "on"
    monkeypatch.setenv("MANNEQUIN_BUST_GATE", "banana")
    assert load_settings().mannequin_bust_gate == "off"
