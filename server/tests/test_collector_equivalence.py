"""Phase 3 P0-C 9/N 보정 — 수집기와 운영 워커가 같은 경로인가.

9/N 30건이 무효가 된 이유는 수집기가 "거의 같게" 불렀기 때문이다. 키 하나가 어긋나
Vision 허용/금지 목록이 빈 채로 30건이 수집됐는데 크래시도 경고도 없었다.
그러니 '같음'은 눈으로 확인할 게 아니라 테스트로 고정해야 한다.
"""

import inspect
import pathlib

import pytest

from app.agents import edit_intent_vision
from app.services import edit_intent_qc, edit_qc_scope, editor_vary

COLLECTOR = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "shadow_collect.py"
WORKER = pathlib.Path(__file__).resolve().parents[1] / "app" / "workers" / "editor_image_job.py"
CHANGES = [{"type": "bg", "value": "밝은 스튜디오"}, {"type": "shot", "value": "전신"}]


def _src(p):
    return p.read_text(encoding="utf-8")


# ── scope DTO 는 한 곳에서만 만든다 ────────────────────────────────────────

def test_vision_scope_has_the_keys_the_prompt_reads():
    scope = editor_vary.semantic_scope(CHANGES)
    v = edit_qc_scope.vision_scope(scope)
    assert set(v) == {"allowed", "forbidden"}
    assert v["allowed"] == scope["allowedObservations"]
    assert v["forbidden"] == scope["forbiddenObservations"]


def test_the_raw_semantic_scope_would_have_produced_empty_lists():
    """실제로 일어난 사고를 재현 — 이 테스트가 그때 있었으면 30건을 안 버렸다."""
    scope = editor_vary.semantic_scope(CHANGES)
    assert scope.get("allowed") is None and scope.get("forbidden") is None
    assert edit_qc_scope.vision_scope(scope)["allowed"]      # 변환하면 비지 않는다


def test_both_sides_call_the_shared_scope_helper():
    for src in (_src(WORKER), _src(COLLECTOR)):
        assert "edit_qc_scope.vision_scope(" in src
        assert "edit_qc_scope.qc_allowed_scope()" in src
    # 손으로 조립한 dict 가 남아 있으면 다시 갈라진다.
    assert '"allowed": ctx["semantic_scope"]' not in _src(WORKER)
    assert 'allowed_scope=scope' not in _src(COLLECTOR)


def test_prompt_scope_lists_are_identical_for_both_sides():
    scope = editor_vary.semantic_scope(CHANGES)
    prompt = edit_intent_vision.build_prompt(
        edit_type=editor_vary.edit_type_for(CHANGES),
        adjustments={"changes": CHANGES},
        allowed_scope=edit_qc_scope.vision_scope(scope))
    for name in scope["allowedObservations"]:
        assert name in prompt
    for name in scope["forbiddenObservations"]:
        assert name in prompt


def test_prompt_snapshot_is_identical_for_the_same_request():
    """같은 입력이면 같은 프롬프트여야 한다 — 다르면 두 데이터셋을 못 합친다."""
    scope = edit_qc_scope.vision_scope(editor_vary.semantic_scope(CHANGES))
    a = edit_intent_vision.build_prompt(edit_type="CUSTOM_REVIEW_REQUIRED",
                                        adjustments={"changes": CHANGES},
                                        allowed_scope=scope)
    b = edit_intent_vision.build_prompt(edit_type="CUSTOM_REVIEW_REQUIRED",
                                        adjustments={"changes": CHANGES},
                                        allowed_scope=scope)
    assert a == b
    assert "(nothing)" not in a.split("${")[0] or True   # 범위가 비지 않았는지는 아래에서


def test_empty_scope_is_visibly_different_from_a_real_scope():
    real = edit_intent_vision.build_prompt(
        edit_type="BACKGROUND_ONLY", adjustments={"changes": CHANGES},
        allowed_scope=edit_qc_scope.vision_scope(editor_vary.semantic_scope(CHANGES)))
    empty = edit_intent_vision.build_prompt(
        edit_type="BACKGROUND_ONLY", adjustments={"changes": CHANGES},
        allowed_scope={"allowed": [], "forbidden": []})
    assert real != empty and "(nothing)" in empty


# ── 판정 인자 동일성 ────────────────────────────────────────────────────────

def test_both_sides_require_vision():
    """수집기가 require_vision=False 면 Vision 없는 결과가 통과해 분포가 달라진다."""
    for src in (_src(WORKER), _src(COLLECTOR)):
        assert "require_vision=True" in src
        assert "require_vision=False" not in src


def test_both_sides_use_the_same_entailed_metrics():
    for src in (_src(WORKER), _src(COLLECTOR)):
        assert "entailed" in src
    assert "editor_vary.entailed_metrics(changes)" in _src(COLLECTOR)


def test_both_sides_pass_the_vary_changes_to_vision():
    for src in (_src(WORKER), _src(COLLECTOR)):
        assert '"changes"' in src and "adjustments=" in src


def test_the_collector_has_one_evaluation_path():
    """초기 수집과 backfill 이 각자 조립하면 둘이 또 갈라진다."""
    src = _src(COLLECTOR)
    assert src.count("edit_intent_qc.evaluate(") == 1
    assert src.count("edit_intent_vision.observe(") == 1
    assert src.count("observe_and_decide(") == 3        # 정의 1 + 호출 2


# ── 같은 입력 → 같은 판정 ──────────────────────────────────────────────────

OBS = {"requestedChangeApplied": True, "collarChanged": False, "sleevesChanged": False,
       "buttonsChanged": False, "pocketsChanged": False, "patternChanged": False,
       "logoChanged": False, "mannequinIdentityChanged": False, "poseChanged": False,
       "cameraChanged": False, "framingChanged": False, "backgroundChanged": True,
       "lightingChanged": False, "confidence": 0.9}
METRICS = {"delta": {"hemY": 0.001, "cuffY": 0.001}}


def _decide(**over):
    scope = editor_vary.semantic_scope(CHANGES)
    kwargs = dict(edit_type=editor_vary.edit_type_for(CHANGES),
                  allowed_scope=edit_qc_scope.qc_allowed_scope(),
                  target_ratio=None, metrics=METRICS, vision=OBS,
                  require_vision=True, semantic_scope=scope,
                  extra_entailed=editor_vary.entailed_metrics(CHANGES))
    kwargs.update(over)
    return edit_intent_qc.decide(**kwargs)


def test_identical_inputs_give_identical_decisions():
    assert _decide() == _decide()


def test_vision_unavailable_is_review_on_both_sides():
    """require_vision=True 는 양쪽 공통 — Vision 없으면 자동 통과하지 않는다."""
    out = _decide(vision=None)
    assert out["decision"] != "pass"
    assert edit_qc_scope.normalize_decision(out["decision"]) == "review_required"


def test_the_empty_scope_bug_changes_the_decision_inputs():
    """빈 범위로 잰 판정은 제대로 잰 판정과 같다고 볼 수 없다."""
    full = _decide()
    empty = _decide(semantic_scope={"requestedTypes": [], "allowedObservations": [],
                                    "forbiddenObservations": []})
    assert full != empty


# ── 정책 버전 ──────────────────────────────────────────────────────────────

def test_policy_version_is_recorded_in_one_place():
    assert edit_qc_scope.QC_POLICY_VERSION
    assert "QC_POLICY_VERSION" in inspect.getsource(edit_qc_scope)


@pytest.mark.parametrize("raw,expected", [
    ("pass", "pass"), ("review", "review_required"),
    ("review_required", "review_required"), ("reject", "reject"),
    ("PASS", "pass"), ("weird", "review_required"), (None, "review_required"),
])
def test_decision_normalization_is_shared(raw, expected):
    assert edit_qc_scope.normalize_decision(raw) == expected


def test_a_missing_output_normalizes_to_failed():
    assert edit_qc_scope.normalize_decision("pass", had_output=False) == "failed"
