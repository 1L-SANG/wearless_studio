"""P1 축 인지 QC 코어(agents/mannequin_fit_qc.py) — 순수 로직 계약.

허용목록(선언 축·고정 관측문구)만 프롬프트에 들어가고, 판정 결과는 정확 커버리지가 강제되며,
편집 지시는 고정 템플릿+스파이크 불변 꼬리로만 조립된다.
"""
import asyncio

import pytest

from app.agents import mannequin_fit_qc as fq
from app.agents.fit_axes import AXIS_OBSERVABLES, FIT_AXES
from app.agents.gemini_image import InlineImage
from app.agents.vision_llm import VisionError
from conftest import make_settings

PROFILE = {"category": "top", "gender": "women", "source": "seller",
           "axes": {"fit": "slim", "length": "long"}, "version": 1}


def _spec():
    return fq.declared_axis_spec(PROFILE)


def _result(axis, target, ok=True, visible=True, landmark="seen"):
    return {"axis": axis, "target": target, "pass": ok, "visible": visible,
            "observedLandmark": landmark}


# ---------- declared_axis_spec ----------

def test_declared_axis_spec_catalog_order_and_exact_observables():
    spec = _spec()
    assert [(e["axis"], e["value"]) for e in spec] == [("fit", "slim"), ("length", "long")]
    for e in spec:
        assert e["observableTarget"] == AXIS_OBSERVABLES[(e["category"], e["axis"], e["value"])]


def test_declared_axis_spec_excludes_matchcut_undeclared_and_invalid():
    p = {"category": "top", "gender": "women", "matchCut": "wide",
         "axes": {"fit": "slim", "length": None, "cut": "wide"}}  # cut 은 top 카탈로그 밖
    spec = fq.declared_axis_spec(p)
    assert [(e["axis"], e["value"]) for e in spec] == [("fit", "slim")]
    assert fq.declared_axis_spec(None) == []
    assert fq.declared_axis_spec({"category": "socks", "axes": {"fit": "slim"}}) == []
    assert fq.declared_axis_spec({"category": "top", "axes": {}}) == []


# ---------- qc_schema / build_prompt ----------

def test_qc_schema_restricts_enums_to_declared():
    schema = fq.qc_schema(_spec())
    item = schema["properties"]["axisPass"]["items"]["properties"]
    assert item["axis"]["enum"] == ["fit", "length"]
    assert item["target"]["enum"] == ["long", "slim"]
    assert "correctionPrompt" not in str(schema)  # 판정문 재주입 경로 없음


def test_build_prompt_contains_only_allowlisted_axis_spec():
    prompt = fq.build_prompt(2, True, _spec())
    for e in _spec():
        assert e["observableTarget"] in prompt
    assert "barefoot" in prompt  # 맨발 규칙 (§G 트리거 1 재발 방지)
    assert "MATCHING BOTTOM" in prompt
    assert "seller" not in prompt and "women" not in prompt  # 프로필 원문 미주입
    no_match = fq.build_prompt(1, False, _spec())
    assert "MATCHING BOTTOM" not in no_match


# ---------- validate ----------

def test_validate_requires_exact_axis_target_pairs():
    spec = _spec()
    ok = {"identityPass": True, "mismatches": [],
          "axisPass": [_result("fit", "slim"), _result("length", "long")]}
    v = fq.validate(ok, spec)
    assert v["identityPass"] is True and len(v["axisPass"]) == 2

    missing = {"identityPass": True, "mismatches": [], "axisPass": [_result("fit", "slim")]}
    extra = {"identityPass": True, "mismatches": [],
             "axisPass": [_result("fit", "slim"), _result("length", "long"),
                          _result("fit", "slim")]}
    swapped = {"identityPass": True, "mismatches": [],
               "axisPass": [_result("fit", "long"), _result("length", "slim")]}
    nonbool = {"identityPass": True, "mismatches": [],
               "axisPass": [_result("fit", "slim"), {**_result("length", "long"), "pass": "yes"}]}
    noident = {"identityPass": "true", "mismatches": [],
               "axisPass": [_result("fit", "slim"), _result("length", "long")]}
    for bad in (missing, extra, swapped, nonbool, noident, {"axisPass": "x"}):
        with pytest.raises(VisionError):
            fq.validate(bad, spec)


def test_visible_false_is_axis_failure():
    spec = _spec()
    v = fq.validate({"identityPass": True, "mismatches": [],
                     "axisPass": [_result("fit", "slim", ok=True, visible=False),
                                  _result("length", "long")]}, spec)
    failed = fq.failed_axis_specs(spec, v)
    assert [(e["axis"], e["value"]) for e in failed] == [("fit", "slim")]


# ---------- verdict (첨부 순서·오류 전파) ----------

def test_verdict_attaches_products_match_then_generated_last_without_base(monkeypatch):
    captured = {}

    async def fake_analyze(settings, prompt, images, schema):
        captured["images"] = images
        return ({"identityPass": True, "mismatches": [],
                 "axisPass": [_result("fit", "slim"), _result("length", "long")]}, "gemini")

    monkeypatch.setattr(fq, "analyze_with_fallback", fake_analyze)
    prods = [InlineImage("image/png", b"p1"), InlineImage("image/jpeg", b"p2")]
    match = InlineImage("image/png", b"mb")
    gen = InlineImage("image/png", b"gen")
    asyncio.run(fq.verdict(make_settings(), prods, gen, PROFILE, match))
    assert [i.data for i in captured["images"]] == [b"p1", b"p2", b"mb", b"gen"]  # base 없음, 생성 맨 뒤


def test_verdict_propagates_vision_error_and_requires_declared_axes(monkeypatch):
    async def boom(settings, prompt, images, schema):
        raise VisionError("down")

    monkeypatch.setattr(fq, "analyze_with_fallback", boom)
    gen = InlineImage("image/png", b"g")
    with pytest.raises(VisionError):
        asyncio.run(fq.verdict(make_settings(), [], gen, PROFILE))
    with pytest.raises(VisionError):  # 선언 축 없음 — 워커가 사전 skip 하는 계약의 방어선
        asyncio.run(fq.verdict(make_settings(), [], gen, {"category": "top", "axes": {}}))


# ---------- 편집 지시 ----------

def test_edit_instruction_templates_cover_every_axis_family():
    for category, axes in FIT_AXES.items():
        for axis in axes:
            assert (category, axis) in fq._EDIT_TEMPLATES, f"템플릿 누락: {category}.{axis}"


def test_edit_instruction_ends_with_exact_spike_tail_once():
    spec = _spec()
    text = fq.build_edit_instruction(spec)
    assert text.endswith(fq.EDIT_TAIL)
    assert text.count("Output ONE photorealistic image.") == 1
    for e in spec:
        assert e["observableTarget"] in text


def test_pants_length_edit_never_requests_footwear():
    p = {"category": "pants", "gender": "women", "axes": {"length": "below_ankle"}}
    spec = fq.declared_axis_spec(p)
    text = fq.build_edit_instruction(spec)
    assert "never add footwear" in text and "barefoot" in text
    assert "shoe" not in text.lower().replace("never add footwear", "")


# ---------- edit_improves ----------

def test_edit_improves_requires_identity_and_all_declared_axes():
    v_fail = {"identityPass": True,
              "axisPass": [_result("fit", "slim", ok=False), _result("length", "long")]}
    good = {"identityPass": True,
            "axisPass": [_result("fit", "slim"), _result("length", "long")]}
    no_identity = {"identityPass": False,
                   "axisPass": [_result("fit", "slim"), _result("length", "long")]}
    regressed = {"identityPass": True,  # 이전 통과 축(length)이 후퇴 → 채택 금지
                 "axisPass": [_result("fit", "slim"), _result("length", "long", ok=False)]}
    invisible = {"identityPass": True,
                 "axisPass": [_result("fit", "slim", visible=False), _result("length", "long")]}
    assert fq.edit_improves(v_fail, good) is True
    assert fq.edit_improves(v_fail, no_identity) is False
    assert fq.edit_improves(v_fail, regressed) is False
    assert fq.edit_improves(v_fail, invisible) is False
    assert fq.edit_improves(v_fail, {"identityPass": True, "axisPass": []}) is False
