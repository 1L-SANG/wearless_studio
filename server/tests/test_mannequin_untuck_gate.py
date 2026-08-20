"""untuck 사전 게이트 계약 (2026-08-19 오너 승인).

배경: untuck 패스는 "검출 불안정" 실측(mannequin_untuck 모듈 주석 5항) 때문에 무조건
1회 돌았다 — 이미 빠져 있는 컷에도 40~60초짜리 이미지 편집 콜이 나간다. 게이트는 그
비용을 줄이되 **비대칭**이어야 한다:

    확신에 찬 "이미 빠져 있음"(untucked, confidence >= 임계) → 편집 스킵 (비용 절약)
    tucked / unclear / 판정 실패 / 게이트 off               → 오늘과 동일 (편집 실행)

즉 게이트가 잘못 죽어도(판정 불가·모델 미존재·오류) 최악이 "오늘 상태"다. 검출 불안정
이력과 충돌하지 않는 이유: 불안정이 문제였던 방향은 "tuck 을 놓쳐 편집을 안 하는" 쪽인데,
그 방향으로는 높은 확신을 요구하고 나머지 전부를 편집 실행으로 쓰러뜨린다.
"""

import asyncio
import types

import pytest

from app.agents import mannequin_untuck
from app.services.qc import QcResult
from app.workers import mannequin_job as mj


# ---------------------------------------------------------------- 순수 판정 규칙


def test_gate_skips_only_on_confident_untucked():
    t = mannequin_untuck.GATE_SKIP_CONFIDENCE
    assert mannequin_untuck.gate_skips({"verdict": "untucked", "confidence": t})
    assert mannequin_untuck.gate_skips({"verdict": "untucked", "confidence": 1.0})
    assert not mannequin_untuck.gate_skips({"verdict": "untucked", "confidence": t - 0.01}), \
        "임계 미만 확신은 스킵 근거가 못 된다"
    assert not mannequin_untuck.gate_skips({"verdict": "tucked", "confidence": 1.0})
    assert not mannequin_untuck.gate_skips({"verdict": "unclear", "confidence": 1.0})
    assert not mannequin_untuck.gate_skips({"verdict": "gate_error", "confidence": 0.0})


def test_validate_gate_normalizes_hostile_raw():
    """모델이 스키마를 벗어나도 안전한 shape 로 눕는다 — 벗어난 값은 전부 '편집 실행' 쪽."""
    out = mannequin_untuck.validate_gate({"verdict": "UNTUCKED!!", "confidence": 5})
    assert out["verdict"] == "unclear", "모르는 verdict 는 unclear 로 — 스킵 불가 쪽"
    assert out["confidence"] == 0.0
    out = mannequin_untuck.validate_gate({"verdict": "untucked", "confidence": "0.9"})
    assert out["confidence"] == 0.0, "숫자가 아니면 0 — 문자열 숫자를 믿지 않는다"
    out = mannequin_untuck.validate_gate({"verdict": "untucked", "confidence": 0.92})
    assert out == {"verdict": "untucked", "confidence": 0.92}
    out = mannequin_untuck.validate_gate(None)
    assert out == {"verdict": "unclear", "confidence": 0.0}


def test_judge_gate_sends_single_image_and_model_override(monkeypatch):
    """판정 입력은 생성본 1장뿐 — 상품/매칭을 섞으면 과제가 흐려진다(편집 패스와 같은 원칙).
    전용 모델 설정이 있으면 gemini 오버라이드로 전달된다."""
    sent = {}

    async def fake_fallback(settings, prompt, images, schema, thinking_level=None, models=None):
        sent.update(prompt=prompt, images=images, schema=schema, models=models)
        return {"verdict": "untucked", "confidence": 0.9}, "gemini"

    monkeypatch.setattr(mannequin_untuck, "analyze_with_fallback", fake_fallback)
    s = types.SimpleNamespace(mannequin_untuck_gate_model="gemini-3.5-flash-lite")
    cut = mj.InlineImage("image/png", b"cut")
    out = asyncio.run(mannequin_untuck.judge_gate(s, cut))
    assert out == {"verdict": "untucked", "confidence": 0.9}
    assert sent["images"] == [cut], "이미지 1장 — 생성본만"
    assert sent["models"] == {"gemini": "gemini-3.5-flash-lite"}
    assert "untucked" in sent["prompt"] and "tucked" in sent["prompt"]

    sent.clear()
    s = types.SimpleNamespace(mannequin_untuck_gate_model="")
    asyncio.run(mannequin_untuck.judge_gate(s, cut))
    assert sent["models"] is None, "전용 모델 미설정이면 정본 텍스트 모델 그대로"


# ---------------------------------------------------------------- postpass 배선


def _postpass(monkeypatch, *, gate_mode, judge=None, judge_raises=False):
    """_apply_untuck_postpass 구동 — 게이트 판정과 편집 콜을 따로 계수한다."""
    sent = {"judge_calls": 0, "edit_calls": 0, "events": []}

    class _Gemini:
        async def generate_content_image(self, model, prompt, images, size, aspect_ratio=None):
            sent["edit_calls"] += 1
            return types.SimpleNamespace(image=b"untucked", mime="image/png")

    async def fake_judge(settings, cut_image):
        sent["judge_calls"] += 1
        if judge_raises:
            raise RuntimeError("gate model unavailable")
        return dict(judge)

    async def fake_emit(pool, job_id, et, payload):
        sent["events"].append(payload)

    monkeypatch.setattr(mannequin_untuck, "judge_gate", fake_judge)
    monkeypatch.setattr(mj, "_emit", fake_emit)
    monkeypatch.setattr(mj.qc, "evaluate_canvas_alpha_qc", lambda data: QcResult("pass"))

    s = types.SimpleNamespace(
        mannequin_untuck_pass="on", mannequin_untuck_gate=gate_mode,
        mannequin_max_attempts=2, mannequin_image_size="1K",
        mannequin_aspect_ratio="2:3", model_image_high="gemini-3-pro-image",
        model_image_light="gemini-3.1-flash-image", model_text="gpt-5.4-mini")
    res = types.SimpleNamespace(image=b"cut", mime="image/png")
    out = asyncio.run(mj._apply_untuck_postpass(
        pool=None, gemini=_Gemini(), s=s, job_id="j1", candidate="A",
        generation_attempts=1, res=res,
        match_img=mj.InlineImage("image/png", b"bottom"), clothing_type="top"))
    events = [e for e in sent["events"] if e.get("status") == "untuck_pass"]
    assert len(events) == 1, "untuck_pass 이벤트는 정확히 1회"
    return out, sent, events[0]


def test_gate_confident_untucked_skips_edit(monkeypatch):
    out, sent, ev = _postpass(
        monkeypatch, gate_mode="on",
        judge={"verdict": "untucked", "confidence": 0.95})
    assert sent["judge_calls"] == 1
    assert sent["edit_calls"] == 0, "확신에 찬 untucked 면 40초짜리 편집을 건너뛴다"
    assert out.image == b"cut", "원본 그대로 출고"
    assert ev["untuck_outcome"] == "skipped_gate"
    assert ev["untuck_calls"] == 0
    assert ev["untuck_gate"] == {"verdict": "untucked", "confidence": 0.95}


@pytest.mark.parametrize("judge", [
    {"verdict": "tucked", "confidence": 0.99},
    {"verdict": "unclear", "confidence": 0.99},
    {"verdict": "untucked", "confidence": 0.5},
])
def test_gate_uncertain_or_tucked_runs_edit(monkeypatch, judge):
    out, sent, ev = _postpass(monkeypatch, gate_mode="on", judge=judge)
    assert sent["judge_calls"] == 1
    assert sent["edit_calls"] == 1, "스킵 조건 미달이면 오늘과 동일 — 편집 실행"
    assert out.image == b"untucked"
    assert ev["untuck_outcome"] == "applied"
    assert ev["untuck_gate"]["verdict"] == judge["verdict"]


def test_gate_error_fails_open_to_edit(monkeypatch):
    out, sent, ev = _postpass(monkeypatch, gate_mode="on", judge_raises=True)
    assert sent["edit_calls"] == 1, "게이트가 죽어도 최악은 '오늘 상태' — 편집은 나간다"
    assert out.image == b"untucked"
    assert ev["untuck_outcome"] == "applied"
    assert ev["untuck_gate"]["verdict"] == "gate_error"


def test_gate_off_never_judges(monkeypatch):
    out, sent, ev = _postpass(monkeypatch, gate_mode="off",
                              judge={"verdict": "untucked", "confidence": 1.0})
    assert sent["judge_calls"] == 0, "off 면 판정 콜 자체가 없다 — 기존 동작 그대로"
    assert sent["edit_calls"] == 1
    assert "untuck_gate" not in ev


# ---------------------------------------------------------------- config 배선


def test_untuck_gate_flag_wiring(monkeypatch):
    from app.config import load_settings
    for key in ("MANNEQUIN_UNTUCK_GATE", "MANNEQUIN_UNTUCK_GATE_MODEL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    s = load_settings()
    assert s.mannequin_untuck_gate == "off", "기본 off — 켜는 건 manifest 의 배포 결정"
    assert s.mannequin_untuck_gate_model == ""

    monkeypatch.setenv("MANNEQUIN_UNTUCK_GATE", "on")
    monkeypatch.setenv("MANNEQUIN_UNTUCK_GATE_MODEL", "gemini-3.5-flash-lite")
    s = load_settings()
    assert s.mannequin_untuck_gate == "on"
    assert s.mannequin_untuck_gate_model == "gemini-3.5-flash-lite"

    monkeypatch.setenv("MANNEQUIN_UNTUCK_GATE", "banana")
    s = load_settings()
    assert s.mannequin_untuck_gate == "off", "모르는 값은 안전값 off 로"
