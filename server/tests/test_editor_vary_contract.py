"""Phase 3 P0-C 6/N — editor vary 계약(요청 검증·의미 범위·계보 스키마).

계약:
  · 판정 경로의 changes[] 는 엄격하다 — 프롬프트 조립의 관대함(cut_variator)과 분리한다.
  · 허용 범위는 **서버가** changes[] 에서 유도한다. 클라이언트가 보내거나 완화할 수 없다.
  · 기존 edit type 으로 **정확히** 표현되는 경우에만 그 type 을 쓴다. 나머지는 CUSTOM.
  · CUSTOM 은 자동 pass 하지 않지만, 보호 대상 변화를 **숨기지도 않는다**.
  · editor vary 세션은 baseline 이 없다 — source_asset_id 가 정본이고 거짓 계보를 만들지 않는다.
"""

import re

import pytest

from app.services import edit_intent_qc as qc
from app.services import edit_session as es
from app.services import editor_vary as ev
from conftest import make_settings

MIGRATION = ("/Users/nojeong-un/devs/wearless_studio/supabase/migrations/"
             "20260801060000_editor_vary_edit_lineage.sql")


def _sql():
    return open(MIGRATION, encoding="utf-8").read()


# ── 요청 검증 ────────────────────────────────────────────────────────────────

def test_known_change_types_pass():
    out = ev.validate_changes([{"type": "bg", "value": "studio"},
                               {"type": "pose", "value": "side"}])
    assert [c["type"] for c in out] == ["bg", "pose"]


def test_unknown_change_type_is_rejected():
    """프롬프트는 미상 type 을 관대하게 넘기지만, 판정 계약은 그럴 수 없다."""
    with pytest.raises(ev.VaryRequestError) as e:
        ev.validate_changes([{"type": "vibe", "value": "cool"}])
    assert e.value.code == "unknown_change_type"


def test_duplicate_change_type_is_rejected():
    with pytest.raises(ev.VaryRequestError) as e:
        ev.validate_changes([{"type": "bg", "value": "a"}, {"type": "bg", "value": "b"}])
    assert e.value.code == "duplicate_change_type"


def test_too_many_changes_is_rejected():
    with pytest.raises(ev.VaryRequestError) as e:
        ev.validate_changes([{"type": t} for t in
                             ("direction", "shot", "pose", "face", "bg")])
    assert e.value.code == "too_many_changes"


@pytest.mark.parametrize("bad", [123, {"a": 1}, []])
def test_non_string_value_is_rejected(bad):
    with pytest.raises(ev.VaryRequestError) as e:
        ev.validate_changes([{"type": "bg", "value": bad}])
    assert e.value.code == "invalid_change_value"


def test_overlong_value_is_rejected():
    with pytest.raises(ev.VaryRequestError) as e:
        ev.validate_changes([{"type": "bg", "value": "x" * 500}])
    assert e.value.code == "change_value_too_long"


def test_empty_changes_is_valid():
    assert ev.validate_changes([]) == []
    assert ev.validate_changes(None) == []


def test_change_types_match_the_prompt_builders_set():
    """판정이 아는 type 과 프롬프트가 라벨을 붙이는 type 이 갈라지면 안 된다."""
    from app.agents import cut_variator
    assert set(ev.ALLOWED_CHANGE_TYPES) == set(cut_variator._TYPE_LABEL)


# ── 의미 범위 ────────────────────────────────────────────────────────────────

def test_requested_axes_become_allowed_observations():
    sc = ev.semantic_scope(ev.validate_changes([{"type": "pose"}, {"type": "bg"}]))
    assert "poseChanged" in sc["allowedObservations"]
    assert {"backgroundChanged", "lightingChanged"} <= set(sc["allowedObservations"])


def test_unrequested_composition_stays_forbidden():
    sc = ev.semantic_scope(ev.validate_changes([{"type": "bg"}]))
    assert "cameraChanged" in sc["forbiddenObservations"]
    assert "framingChanged" in sc["forbiddenObservations"]
    assert "backgroundChanged" not in sc["forbiddenObservations"]


@pytest.mark.parametrize("field", ev.ALWAYS_LOCKED_OBSERVATIONS)
def test_garment_identity_is_locked_in_every_vary(field):
    for changes in ([], [{"type": "bg"}], [{"type": "pose"}, {"type": "shot"}]):
        sc = ev.semantic_scope(ev.validate_changes(changes))
        assert field in sc["forbiddenObservations"]
        assert field not in sc["allowedObservations"]


def test_similar_cut_allows_nothing():
    sc = ev.semantic_scope([])
    assert sc["allowedObservations"] == []


# ── edit type 매핑 ───────────────────────────────────────────────────────────

def test_background_only_request_maps_to_the_existing_edit_type():
    assert ev.edit_type_for(ev.validate_changes([{"type": "bg"}])) == "BACKGROUND_ONLY"


@pytest.mark.parametrize("changes", [
    [{"type": "pose"}], [{"type": "direction"}], [{"type": "shot"}], [{"type": "face"}],
    [{"type": "bg"}, {"type": "pose"}], [],
])
def test_everything_else_stays_custom_review(changes):
    """지원하는 척 단일 type 으로 축소하지 않는다."""
    assert ev.edit_type_for(ev.validate_changes(changes)) == "CUSTOM_REVIEW_REQUIRED"


def test_mapped_edit_types_exist_in_the_edit_session_contract():
    for changes in ([{"type": "bg"}], [{"type": "pose"}], []):
        assert ev.edit_type_for(ev.validate_changes(changes)) in es.EDIT_TYPES


# ── entailed 지표 ────────────────────────────────────────────────────────────

def test_shot_request_entails_framing_metrics():
    ent = ev.entailed_metrics(ev.validate_changes([{"type": "shot"}]))
    assert {"subjectHeight", "centerX", "centerY"} <= set(ent)


def test_bg_request_entails_background_delta():
    assert "backgroundDeltaE" in ev.entailed_metrics(
        ev.validate_changes([{"type": "bg"}]))


def test_no_request_entails_nothing():
    assert ev.entailed_metrics([]) == ()


# ── Decision Engine 결합 ─────────────────────────────────────────────────────

def _obs(**over):
    from app.agents import edit_intent_vision as eiv
    base = {f: False for f in eiv.OBSERVATION_FIELDS}
    base["requestedChangeApplied"] = True
    base.update({"confidence": 0.9, "uncertainFields": [], "evidence": []})
    base.update(over)
    return base


def _decide(changes, vision, metrics=None):
    norm = ev.validate_changes(changes)
    etype = ev.edit_type_for(norm)
    return qc.decide(
        edit_type=etype, allowed_scope=es.allowed_scope(etype), target_ratio=None,
        metrics=metrics or {"confidence": 0.9, "delta": {}}, vision=vision,
        require_vision=True, semantic_scope=ev.semantic_scope(norm),
        extra_entailed=ev.entailed_metrics(norm))


def test_requested_pose_change_is_not_a_violation():
    r = _decide([{"type": "pose"}], _obs(poseChanged=True))
    assert "pose" not in r["lockedInvariantViolations"]
    assert r["decision"] == "review_required"       # CUSTOM — 자동 pass 는 없다


def test_requested_background_change_is_not_a_violation():
    r = _decide([{"type": "bg"}], _obs(backgroundChanged=True))
    assert "background" not in r["lockedInvariantViolations"]


def test_unrequested_camera_change_is_a_violation():
    r = _decide([{"type": "bg"}], _obs(cameraChanged=True))
    assert "camera" in r["lockedInvariantViolations"] and r["decision"] == "reject"


def test_custom_review_does_not_hide_protected_garment_changes():
    """CUSTOM 은 "사람이 본다"는 뜻이지 "위반을 못 본 척한다"가 아니다."""
    r = _decide([{"type": "pose"}], _obs(patternChanged=True))
    assert "pattern" in r["lockedInvariantViolations"]
    assert r["decision"] == "reject"


@pytest.mark.parametrize("field,name", [
    ("collarChanged", "collarType"), ("logoChanged", "logo"),
    ("buttonsChanged", "buttonCount"), ("mannequinIdentityChanged", "mannequinIdentity"),
])
def test_every_protected_change_is_detected_under_custom(field, name):
    r = _decide([], _obs(**{field: True}))
    assert name in r["lockedInvariantViolations"] and r["decision"] == "reject"


def test_custom_never_auto_passes_even_when_everything_is_clean():
    r = _decide([{"type": "pose"}], _obs())
    assert r["decision"] == "review_required"


def test_vision_unavailable_under_vary_is_review_not_pass():
    r = _decide([{"type": "bg"}], None)
    assert r["decision"] == "review_required"


def test_requested_background_delta_is_not_counted_as_drift():
    r = _decide([{"type": "bg"}], _obs(backgroundChanged=True),
                metrics={"confidence": 0.9, "delta": {}, "backgroundDeltaE": 40.0})
    assert "background" not in r["lockedInvariantViolations"]


# ── migration 정적 계약 ──────────────────────────────────────────────────────

def test_migration_allows_editor_asset_sources():
    sql = _sql()
    assert "add column if not exists source_kind text not null default 'approved_baseline'" in sql
    assert "alter column baseline_id drop not null" in sql


def test_migration_forbids_invalid_source_combinations():
    sql = _sql()
    assert re.search(
        r"source_kind = 'approved_baseline' and baseline_id is not null", sql)
    assert re.search(
        r"source_kind = 'editor_asset' and source_asset_id is not null", sql)


def test_migration_does_not_backfill_a_fake_source_asset():
    """baseline 세션의 source_asset_id 를 역산해 넣으면 거짓 계보가 된다."""
    sql = _sql()
    assert "update public.edit_sessions" not in sql.lower()
    assert "역산하지 않고" in sql


def test_migration_pins_one_output_per_session_both_ways():
    sql = _sql()
    assert "generation_outputs_one_per_edit_session" in sql
    assert "edit_sessions_one_output" in sql


def test_migration_adds_wardrobe_qc_status_without_reject():
    sql = _sql()
    assert "qc_status in ('pass', 'review_required')" in sql
    assert "'reject'" not in sql, "거부된 결과는 wardrobe 에 들어오지 않는다"


def test_migration_is_append_only():
    sql = _sql().lower()
    assert "drop table" not in sql and "delete from" not in sql
    assert "drop column" not in sql


# ── flag ─────────────────────────────────────────────────────────────────────

def test_vary_flag_defaults_to_off():
    assert make_settings().editor_vary_intent_qc == "off"


def test_vary_flag_is_separate_from_mannequin_edit_flag():
    s = make_settings(mannequin_edit_intent_qc="enforce")
    assert s.editor_vary_intent_qc == "off", "한 스위치가 두 경로를 묶었다"


# ── Vision provider 실패 로그 위생 (5/N 보정) ────────────────────────────────

def test_provider_failure_log_carries_no_raw_error(monkeypatch, caplog):
    """provider 오류 메시지에는 요청 URL·쿼리(키 포함 가능)와 응답 본문이 들어 있다."""
    import asyncio

    from app.agents import vision_llm

    # mirrors the real `_call_gpt`/`_call_gemini` signature — `temperature` was added when
    # the fidelity judge needed a fixed one, and a stub that cannot accept it fails on the
    # call instead of on the property this test is about
    async def boom(settings, model, prompt, images, schema, timeout,
                   thinking_level=None, temperature=None):
        raise vision_llm.VisionError(
            "Gemini 500: https://host/v1/models?key=SECRET body=PROMPT-LEAK")

    monkeypatch.setitem(vision_llm._PROVIDERS, "gemini",
                        (boom, lambda s: "m", lambda s: "key"))
    monkeypatch.setattr(vision_llm, "_order", lambda s: ["gemini"])
    with caplog.at_level("WARNING"):
        with pytest.raises(vision_llm.VisionError):
            asyncio.run(vision_llm.analyze_with_fallback(
                make_settings(), "prompt text", [], {"type": "object"}))
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "SECRET" not in msgs and "PROMPT-LEAK" not in msgs
    assert "://" not in msgs and "prompt text" not in msgs
    rec = next(r for r in caplog.records if "provider failed" in r.getMessage())
    assert getattr(rec, "provider", None) == "gemini"
    assert getattr(rec, "error_type", None) == "VisionError"
    assert getattr(rec, "category", None) == "provider_error"


def test_failure_category_distinguishes_timeout():
    from app.agents import vision_llm
    assert vision_llm._failure_category(TimeoutError("x")) == "timeout"
    assert vision_llm._failure_category(
        vision_llm.VisionError("x")) == "provider_error"
    assert vision_llm._failure_category(ValueError("x")) == "unexpected_error"
