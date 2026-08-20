"""베이스 충실도 QC — 계약·shadow 불변식·안전 강등.

이 QC 는 관측 전용으로 들어간다. 그래서 여기서 가장 중요한 테스트는 "잘 잡는가"가 아니라
**"아무것도 막지 않는가"** 다. 판정 품질은 실측 분포가 쌓인 뒤 캘리브레이션에서 다룬다.

실제 vision 호출은 하지 않는다(가짜 provider). 프롬프트·스키마는 문자열/구조로 검증한다.
"""
import ast
import asyncio
import pathlib

import pytest

from app.agents import mannequin_base_fidelity_qc as bf
from app.agents.gemini_image import InlineImage
from app.agents.vision_llm import VisionError
from conftest import make_settings

MJ_PATH = pathlib.Path(__file__).resolve().parents[1] / "app/workers/mannequin_job.py"
MJ_SRC = MJ_PATH.read_text(encoding="utf-8")
PROMPT = (pathlib.Path(__file__).resolve().parents[1]
          / "prompts/mannequin_base_fidelity_qc_v1.txt").read_text(encoding="utf-8")

#: 패치 전에 붙잡아 둔다 — 테스트가 monkeypatch 로 덮은 이름을 다시 부르면 무한 재귀다.
_REAL_VERDICT = bf.verdict

BASE = InlineImage("image/png", b"base-bytes")
GEN = InlineImage("image/png", b"generated-bytes")


def _func_source(name: str) -> str:
    tree = ast.parse(MJ_SRC)
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return ast.get_source_segment(MJ_SRC, node) or ""
    raise AssertionError(f"{name} not found in mannequin_job")


def _code_only(text: str) -> str:
    """주석 제거 — 산문에 등장하는 단어로 불변식을 판정하면 안 된다."""
    return "\n".join(line.split("#")[0] for line in text.splitlines())


def ok_raw(pose="pass", wear="pass"):
    return {"poseFrameMatch": {"decision": pose, "reason": "r1"},
            "wearGeometry": {"decision": wear, "reason": "r2"}}


# ── 1. 설정 / 모드 ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("off", "off"), ("shadow", "shadow"), ("enforce", "enforce"),
    ("", "off"), ("shadwo", "off"), ("SHADOW", "shadow"),
])
def test_mode_flag_parsing(raw, expected, monkeypatch):
    from app.config import load_settings
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("MANNEQUIN_BASE_FIDELITY_QC", raw)
    assert load_settings().mannequin_base_fidelity_qc == expected


def test_default_is_off_when_unset(monkeypatch):
    """기본 off — 관측 데이터 없이 어떤 환경에서도 조용히 켜지면 안 된다."""
    from app.config import load_settings
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.delenv("MANNEQUIN_BASE_FIDELITY_QC", raising=False)
    assert load_settings().mannequin_base_fidelity_qc == "off"


def test_declared_modes_match_the_settings_allowlist():
    assert bf.MODES == ("off", "shadow", "enforce")


# ── 2. shadow 는 기존 판정을 바꾸지 않는다 ───────────────────────────────────

RUN_CANDIDATE = _func_source("_run_candidate")
HELPER = _func_source("_apply_base_fidelity_qc")


def test_the_qc_only_reaches_the_decision_path_through_the_policy_function():
    """게이팅은 `base_fidelity_retry_axes` 한 곳을 통해서만 일어난다.

    이 함수가 mode·축 허용목록·skip 처리를 전부 소유한다. 판정 결과가 다른 경로로 새면
    off/shadow 에서도 조용히 막히는 사고가 난다.
    """
    code = _code_only(RUN_CANDIDATE)
    # 2026-08-19 병렬화: base_fidelity 는 _observe_generation_qc(동일성과 동시 판정)를
    # 경유하지만, 그 안에서 여전히 _apply_base_fidelity_qc 단일 헬퍼만 호출한다 —
    # "정책 함수 한 곳" 불변식은 경로가 아니라 아래 두 단언이 지킨다.
    assert "base_fidelity = await _observe_generation_qc" in code
    observer = _code_only(_func_source("_observe_generation_qc"))
    assert "_apply_base_fidelity_qc(" in observer
    assert code.count("base_fidelity_retry_axes(s, base_fidelity)") == 1
    # 점수 계산·구제 판단에는 절대 섞이지 않는다.
    for consumer in ("merge_qc_scores", "_is_better_candidate", "score_outcome"):
        for line in code.splitlines():
            if consumer in line:
                assert "base_fidelity" not in line and "bf_axes" not in line


def test_gating_helpers_do_not_know_about_this_qc():
    for name in ("gate_decision", "final_decision", "merge_qc_scores", "score_outcome"):
        src = _code_only(_func_source(name))
        assert "base_fidelity" not in src and "baseFidelity" not in src


def test_gating_state_is_observable_in_every_event():
    """이벤트만 보고 "이 컷이 게이트 대상이었나"를 알 수 있어야 한다."""
    code = _code_only(HELPER)
    assert '"baseFidelityQcGating": mode == "enforce"' in code
    assert '"baseFidelityGatedAxes"' in code
    assert 'mode not in ("shadow", "enforce")' in code


# ── 3. 베이스 이미지 없음 → skip ─────────────────────────────────────────────

def test_missing_base_image_skips_without_calling_the_provider(monkeypatch):
    called = []

    async def boom(*a, **k):
        called.append(1)
        raise AssertionError("provider must not be called without a base image")

    monkeypatch.setattr(bf, "analyze_with_fallback", boom)
    out = asyncio.run(bf.verdict(make_settings(), None, GEN))
    assert out["overall"]["decision"] == "skip"
    assert out["overall"]["reason"] == bf.SKIP_MISSING_BASE
    assert out["poseFrameMatch"]["decision"] == "skip"
    assert not called


def test_skip_result_has_the_same_shape_as_a_real_verdict(monkeypatch):
    """소비자가 skip 과 정상 결과를 분기하지 않아도 되게 shape 이 같아야 한다."""
    async def fake(*a, **k):
        return ok_raw(), "gemini"

    monkeypatch.setattr(bf, "analyze_with_fallback", fake)
    real = asyncio.run(bf.verdict(make_settings(), BASE, GEN))
    assert set(bf.skipped("x")) == set(real)
    for key in real:
        assert set(bf.skipped("x")[key]) == set(real[key]) == {"decision", "reason"}


# ── 4. 핏/이즈 메타데이터 전달 ───────────────────────────────────────────────

def test_fit_context_reads_only_existing_fields():
    ctx = bf.fit_context(
        {"name": "박시 니트", "clothing_type": "top"},
        {"subCategory": "knit", "fit": "over", "styleTags": ["casual"],
         "fitProfile": {"category": "top", "axes": {"fit": "over", "length": "long"}}})
    assert ctx["clothingType"] == "top"
    assert ctx["subCategory"] == "knit"
    assert ctx["fit"] == "over"
    assert ctx["declaredAxes"] == {"fit": "over", "length": "long"}
    assert ctx["productName"] == "박시 니트"


def test_fit_context_degrades_to_empty_without_metadata():
    assert bf.fit_context(None, None) == {}
    assert bf.fit_context({}, {}) == {}


def test_fit_context_drops_empty_values_instead_of_sending_nulls():
    ctx = bf.fit_context({"name": ""}, {"fit": None, "styleTags": []})
    assert ctx == {}


def test_fit_metadata_reaches_the_prompt(monkeypatch):
    seen = {}

    async def fake(settings, prompt, images, schema, **k):
        seen["prompt"] = prompt
        seen["images"] = images
        return ok_raw(), "gemini"

    monkeypatch.setattr(bf, "analyze_with_fallback", fake)
    asyncio.run(bf.verdict(
        make_settings(), BASE, GEN,
        product={"name": "오버핏 셔츠", "clothing_type": "top"},
        analysis={"fit": "over", "subCategory": "shirt"}))
    assert '"fit": "over"' in seen["prompt"]
    assert '"subCategory": "shirt"' in seen["prompt"]
    # 이미지 순서 계약: 베이스 먼저, 생성물 나중.
    assert seen["images"] == [BASE, GEN]


def test_worker_forwards_product_and_analysis():
    code = _code_only(HELPER)
    assert "product=product" in code and "analysis=analysis" in code


# ── 5. 관측 이벤트 ───────────────────────────────────────────────────────────

class _Emitted:
    def __init__(self):
        self.events = []

    async def __call__(self, pool, job_id, event_type, payload):
        self.events.append((event_type, payload))


def _run_helper(monkeypatch, *, mode, verdict_impl, base=BASE):
    from app.workers import mannequin_job as mj
    emitted = _Emitted()
    monkeypatch.setattr(mj, "_emit", emitted)
    monkeypatch.setattr(mj.mannequin_base_fidelity_qc, "verdict", verdict_impl)
    s = make_settings(mannequin_base_fidelity_qc=mode)
    res = type("R", (), {"mime": "image/png", "image": b"x"})()
    out = asyncio.run(mj._apply_base_fidelity_qc(
        pool=None, s=s, job_id="job-1", candidate="A", attempt=2,
        base_img=base, res=res, product={"name": "n"}, analysis={"fit": "regular"}))
    return out, emitted.events


def test_shadow_emits_a_structured_step_event(monkeypatch):
    async def fake_verdict(*a, **k):
        return bf.validate(ok_raw(pose="retry", wear="pass"))

    out, events = _run_helper(monkeypatch, mode="shadow", verdict_impl=fake_verdict)
    assert len(events) == 1
    kind, payload = events[0]
    assert kind == "step"
    assert payload["status"] == "base_fidelity_qc"
    assert payload["baseFidelityQcEnabled"] is True
    assert payload["baseFidelityQcMode"] == "shadow"
    assert payload["baseFidelityQcGating"] is False
    assert payload["candidate"] == "A" and payload["attempt"] == 2
    assert payload["poseFrameMatch"] == "retry"
    assert payload["wearGeometry"] == "pass"
    assert payload["overall"] == "retry"
    assert set(payload["reasons"]) == {"poseFrameMatch", "wearGeometry", "overall"}
    assert out["overall"]["decision"] == "retry"


def test_off_mode_neither_runs_nor_emits(monkeypatch):
    async def must_not_run(*a, **k):
        raise AssertionError("off 모드에서 판정기가 돌면 안 된다")

    out, events = _run_helper(monkeypatch, mode="off", verdict_impl=must_not_run)
    assert out is None and events == []


def test_missing_base_emits_a_skip_event(monkeypatch):
    async def real_verdict(settings, base, gen, **k):
        return await _REAL_VERDICT(settings, base, gen, **k)

    out, events = _run_helper(monkeypatch, mode="shadow",
                              verdict_impl=real_verdict, base=None)
    assert out["overall"]["reason"] == bf.SKIP_MISSING_BASE
    assert events[0][1]["overall"] == "skip"


# ── 6·7·8. 프롬프트/스키마가 대상 결함과 가드레일을 담고 있는가 ──────────────

def test_schema_exposes_exactly_the_two_axes():
    schema = bf.qc_schema()
    assert set(schema["properties"]) == {"poseFrameMatch", "wearGeometry"}
    for axis in bf.AXES:
        props = schema["properties"][axis]["properties"]
        assert props["decision"]["enum"] == ["pass", "retry"], "skip 은 모델이 고를 값이 아니다"
        assert set(schema["properties"][axis]["required"]) == {"decision", "reason"}


@pytest.mark.parametrize("phrase", [
    "frontal", "3/4", "side", "camera", "torso orientation", "shoulder orientation",
    "framing", "scale",
])
def test_prompt_covers_pose_and_frame_drift(phrase):
    assert phrase in PROMPT.lower() or phrase in PROMPT


@pytest.mark.parametrize("phrase", [
    "rear panel", "duplicated", "balloon", "detached fabric wall",
    "second outer contour", "two cuff endings", "second structure",
])
def test_prompt_covers_duplicated_and_protruding_geometry(phrase):
    assert phrase in PROMPT.lower()


@pytest.mark.parametrize("phrase", [
    "count the garment structures", "inspect each side separately",
    "inspect each arm separately", "front / back coherence",
])
def test_wear_geometry_forces_an_inspection_order(phrase):
    """v2: 통째 인상 판정 대신 구조를 세고 좌우·양팔을 따로 보게 한다."""
    assert phrase in PROMPT.lower()


def test_fit_explains_volume_not_duplication():
    """v2 의 핵심 문장. 두 거짓 음성 모두 선언된 semi_over 핏으로 중복을 설명해 버렸다."""
    assert "fit explains volume. fit does not explain duplication." in PROMPT.lower()


def test_retry_requires_localized_evidence():
    low = PROMPT.lower()
    assert "retry requires localized evidence" in low
    assert "answer \"pass\" instead of guessing" in low


@pytest.mark.parametrize("fit_word", ["relaxed", "loose", "boxy", "oversized"])
def test_prompt_protects_legitimate_wide_fits(fit_word):
    assert fit_word in PROMPT.lower()


def test_prompt_forbids_width_alone_as_a_defect():
    low = PROMPT.lower()
    assert "the garment extends far from the body, therefore retry" in low
    assert "forbidden" in low
    assert "${fitcontext}" in low, "핏 컨텍스트 주입 토큰이 사라지면 가드레일이 죽는다"


def test_prompt_stays_out_of_the_generic_qc_lane():
    """색·패턴·로고는 AG-P2 소관이다. 두 판정기가 같은 축을 다투면 안 된다."""
    low = PROMPT.lower()
    assert "a different inspector owns those" in low


def test_generation_prompt_templates_are_untouched():
    """이 작업은 생성 프롬프트를 건드리지 않는다 — 접촉 블록이 그대로 있어야 한다."""
    gen = (pathlib.Path(__file__).resolve().parents[1]
           / "prompts/mannequin_generate_v1.txt").read_text(encoding="utf-8")
    assert "GARMENT-BODY CONTACT (mandatory)" in gen
    assert "base_fidelity" not in gen


# ── 9. 안전 강등 ─────────────────────────────────────────────────────────────

def test_provider_failure_degrades_to_skip_and_never_raises(monkeypatch):
    async def boom(*a, **k):
        raise VisionError("provider down")

    out, events = _run_helper(monkeypatch, mode="shadow", verdict_impl=boom)
    assert out["overall"]["decision"] == "skip"
    assert out["overall"]["reason"] == bf.SKIP_FAILED
    assert events[0][1]["status"] == "base_fidelity_qc_failed"
    assert events[0][1]["error"] == "VisionError"


def test_unexpected_exception_also_degrades(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("something else entirely")

    out, events = _run_helper(monkeypatch, mode="shadow", verdict_impl=boom)
    assert out["overall"]["decision"] == "skip"
    assert events[0][1]["error"] == "RuntimeError"


def test_helper_swallows_everything_by_construction():
    code = _code_only(HELPER)
    assert "except Exception" in code
    assert "raise" not in code


@pytest.mark.parametrize("raw", [
    None, {}, {"poseFrameMatch": "nope"},
    {"poseFrameMatch": {"decision": "maybe", "reason": "x"}},
    {"poseFrameMatch": {"decision": "pass"}, "wearGeometry": {"decision": "pass"}},
])
def test_malformed_responses_become_skip_not_exceptions(raw):
    out = bf.validate(raw)
    assert set(out) == {"poseFrameMatch", "wearGeometry", "overall"}
    for axis in bf.AXES:
        assert out[axis]["decision"] in bf.DECISIONS


def test_overall_is_computed_in_code_not_taken_from_the_model():
    """모델이 종합을 뒤집어 보내도 코드가 정한 정책이 이긴다."""
    raw = {**ok_raw(pose="retry", wear="pass"), "overall": {"decision": "pass", "reason": "!"}}
    out = bf.validate(raw)
    assert out["overall"]["decision"] == "retry"
    assert out["overall"]["reason"] == "poseFrameMatch"


def test_overall_rules():
    assert bf.overall_decision(bf.validate(ok_raw()))["decision"] == "pass"
    assert bf.overall_decision(bf.validate(ok_raw(wear="retry")))["decision"] == "retry"
    assert bf.overall_decision(bf.skipped("x"))["decision"] == "skip"


def test_reason_is_bounded():
    out = bf.validate({"poseFrameMatch": {"decision": "pass", "reason": "y" * 5000},
                       "wearGeometry": {"decision": "pass", "reason": "z"}})
    assert len(out["poseFrameMatch"]["reason"]) <= 300


# ── 10. 인프라 부수효과 없음 ─────────────────────────────────────────────────

def test_module_touches_no_infrastructure():
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "app/agents/mannequin_base_fidelity_qc.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    for banned in ("boto3", "psycopg", "httpx", "app.r2", "app.repo"):
        assert not any(banned in m for m in imported), imported
    for banned in ("r2.", "repo.", "put_bytes", "create_job", "sam_client",
                   "segment_garment", "canonical_reference"):
        assert banned not in src


def test_helper_writes_nothing_but_events():
    code = _code_only(HELPER)
    for banned in ("repo.", "r2.", "put_bytes", "_save_cut", "create_job"):
        assert banned not in code
    assert "_emit(" in code


def test_qc_does_not_depend_on_sam():
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "app/agents/mannequin_base_fidelity_qc.py").read_text(encoding="utf-8")
    for banned in ("sam", "canonical", "cutout"):
        assert banned not in src.lower().replace("same", "").replace("assam", "")


# ── 포즈 축 enforce 배선 ─────────────────────────────────────────────────────

from app.workers.mannequin_job import base_fidelity_retry_axes, _BASE_FIDELITY_GATED_AXES  # noqa: E402


def _res(pose="pass", wear="pass"):
    return bf.validate({"poseFrameMatch": {"decision": pose, "reason": "p"},
                        "wearGeometry": {"decision": wear, "reason": "w"}})


@pytest.mark.parametrize("mode", ["off", "shadow"])
def test_non_enforce_modes_never_request_retry(mode):
    s = make_settings(mannequin_base_fidelity_qc=mode)
    assert base_fidelity_retry_axes(s, _res(pose="retry", wear="retry")) == []


def test_enforce_requests_retry_on_pose_drift():
    s = make_settings(mannequin_base_fidelity_qc="enforce")
    assert base_fidelity_retry_axes(s, _res(pose="retry")) == ["poseFrameMatch"]


def test_enforce_does_not_gate_wear_geometry_yet():
    """보정 게이트 미통과(2026-08-12) — 판정은 하되 재시도를 요구하지 않는다."""
    s = make_settings(mannequin_base_fidelity_qc="enforce")
    assert base_fidelity_retry_axes(s, _res(wear="retry")) == []
    assert "wearGeometry" not in _BASE_FIDELITY_GATED_AXES


def test_both_axes_retry_still_names_only_gated_axes():
    s = make_settings(mannequin_base_fidelity_qc="enforce")
    assert base_fidelity_retry_axes(s, _res(pose="retry", wear="retry")) == ["poseFrameMatch"]


def test_both_pass_requests_nothing():
    s = make_settings(mannequin_base_fidelity_qc="enforce")
    assert base_fidelity_retry_axes(s, _res()) == []


@pytest.mark.parametrize("reason", [bf.SKIP_FAILED, bf.SKIP_MISSING_BASE, "malformed_axis"])
def test_skip_never_becomes_retry(reason):
    """provider 실패·베이스 없음·형식 위반은 판정 못 한 것이지 불합격이 아니다."""
    s = make_settings(mannequin_base_fidelity_qc="enforce")
    assert base_fidelity_retry_axes(s, bf.skipped(reason)) == []
    assert base_fidelity_retry_axes(s, None) == []


def test_retry_uses_the_single_existing_branch():
    """축이 여러 개 걸려도 재시도 전이는 하나다 — 별도 생성 루프를 만들지 않는다."""
    code = _code_only(RUN_CANDIDATE)
    assert code.count("needs_retry = ") == 1
    assert "if needs_retry and budget_left and not salvaged:" in code
    assert 'final_decision(s, qc_scores) == "retry" or bool(bf_axes)' in code
    # 예산 판단은 기존 함수가 그대로 소유한다
    assert "has_budget_for_retry(s, calls_spent=calls_spent)" in code


def test_base_fidelity_never_generates_or_calls_sam():
    code = _code_only(_func_source("_apply_base_fidelity_qc"))
    for banned in ("generate_content_image", "sam_client", "segment_garment",
                   "_maybe_augment_with_canonical", "reserve_credits"):
        assert banned not in code


def test_retry_axes_are_emitted_for_observability():
    code = _code_only(RUN_CANDIDATE)
    assert '"baseFidelityRetryRequired"' in code and '"baseFidelityRetryAxes"' in code
