"""Phase 3 P0-C — Edit Session 계약(순수 정책).

계약:
  · 편집 요청은 구조화 step(-2..2)만. 자유 텍스트가 계약이 아니다.
  · edit type 과 무관한 축을 함께 바꾸는 요청은 그 이름이 거짓이므로 거부한다.
  · 미지원 타입은 지원하는 척 통과시키지 않고 CUSTOM_REVIEW_REQUIRED 로 강등(자동 PASS 불가).
  · 허용/금지 범위는 **서버가** 정한다 — 클라이언트가 잠금을 완화할 수 없다.
  · 값을 모르는 invariant 는 편집 때문에 값이 생기지 않는다(unavailable 유지).
"""

import re

import pytest

from app.services import edit_session as es

MIGRATION = ("/Users/nojeong-un/devs/wearless_studio/supabase/migrations/"
             "20260801040000_edit_sessions.sql")


def _sql():
    return open(MIGRATION, encoding="utf-8").read()


# ── edit type ────────────────────────────────────────────────────────────────

def test_unknown_edit_type_is_rejected():
    with pytest.raises(es.EditRequestError) as e:
        es.normalize_edit_type("MAKE_IT_POP")
    assert e.value.code == "unsupported_edit_type"


@pytest.mark.parametrize("t", ["BACKGROUND_ONLY", "LIGHTING_ONLY",
                               "CUSTOM_REVIEW_REQUIRED"])
def test_unsupported_types_degrade_to_custom_review(t):
    """지원하지 않는 편집을 지원하는 척 통과시키지 않는다 — 사람이 본다."""
    assert es.normalize_edit_type(t) == "CUSTOM_REVIEW_REQUIRED"


@pytest.mark.parametrize("t", es.SUPPORTED_EDIT_TYPES)
def test_supported_types_pass_through(t):
    assert es.normalize_edit_type(t) == t


def test_migration_and_code_agree_on_edit_types():
    """DB CHECK 와 코드 목록이 갈라지면 API 만 고치고 DB 가 거부한다."""
    sql_types = set(re.findall(r"'([A-Z_]+)'", re.search(
        r"edit_sessions_type_check check \(edit_type in \((.*?)\)\)", _sql(), re.S).group(1)))
    assert sql_types == set(es.EDIT_TYPES)


# ── 구조화 adjustment ────────────────────────────────────────────────────────

def test_valid_single_axis_adjustment():
    out = es.validate_adjustments("GARMENT_LENGTH_ONLY", {"garmentLengthStep": -1})
    assert out["garmentLengthStep"] == -1
    assert all(out[f] == 0 for f in es.STEP_FIELDS if f != "garmentLengthStep")


def test_unknown_field_is_rejected():
    """오타를 조용히 무시하면 '요청했는데 아무 일도 안 일어남'이 된다."""
    with pytest.raises(es.EditRequestError) as e:
        es.validate_adjustments("GARMENT_LENGTH_ONLY",
                                {"garmentLengthStep": -1, "garmetLength": -1})
    assert e.value.code == "unknown_adjustment_field"


@pytest.mark.parametrize("bad", [-3, 3, 10, -100])
def test_step_out_of_range_is_rejected(bad):
    with pytest.raises(es.EditRequestError) as e:
        es.validate_adjustments("GARMENT_LENGTH_ONLY", {"garmentLengthStep": bad})
    assert e.value.code == "step_out_of_range"


@pytest.mark.parametrize("bad", [True, 1.5, "1", None])
def test_non_integer_step_is_rejected(bad):
    with pytest.raises(es.EditRequestError) as e:
        es.validate_adjustments("GARMENT_LENGTH_ONLY", {"garmentLengthStep": bad})
    assert e.value.code == "invalid_step"


def test_adjustment_outside_the_edit_type_is_rejected():
    """GARMENT_LENGTH_ONLY 인데 소매까지 바꾸면 그 타입 이름이 거짓이 된다."""
    with pytest.raises(es.EditRequestError) as e:
        es.validate_adjustments("GARMENT_LENGTH_ONLY",
                                {"garmentLengthStep": -1, "sleeveLengthStep": 1})
    assert e.value.code == "adjustment_outside_edit_type"


def test_no_change_requested_is_rejected():
    with pytest.raises(es.EditRequestError) as e:
        es.validate_adjustments("GARMENT_LENGTH_ONLY", {"garmentLengthStep": 0})
    assert e.value.code == "no_change_requested"


def test_background_only_takes_no_step_adjustments():
    assert es.validate_adjustments("BACKGROUND_ONLY", {})["garmentLengthStep"] == 0
    with pytest.raises(es.EditRequestError) as e:
        es.validate_adjustments("BACKGROUND_ONLY", {"garmentLengthStep": 1})
    assert e.value.code == "adjustment_not_allowed_for_type"


def test_custom_review_accepts_multi_axis_but_never_auto_passes():
    out = es.validate_adjustments("CUSTOM_REVIEW_REQUIRED",
                                  {"garmentLengthStep": -1, "sleeveLengthStep": 1})
    assert out["garmentLengthStep"] == -1 and out["sleeveLengthStep"] == 1
    # 자동 PASS 금지는 Decision Engine 의 계약 — 여기서는 허용 범위가 비어 있음을 고정
    assert es.allowed_scope("CUSTOM_REVIEW_REQUIRED")["allowed"] == []


@pytest.mark.parametrize("step,ratio", [(-2, -0.16), (-1, -0.08), (1, 0.08), (2, 0.16)])
def test_target_delta_ratio_follows_the_step(step, ratio):
    r = es.target_delta_ratio("GARMENT_LENGTH_ONLY", {"garmentLengthStep": step})
    assert r == pytest.approx(ratio)


def test_target_ratio_is_none_for_types_without_a_measurable_axis():
    assert es.target_delta_ratio("BACKGROUND_ONLY", {}) is None


# ── 허용/금지 범위 ───────────────────────────────────────────────────────────

def test_allowed_scope_permits_exactly_one_axis():
    sc = es.allowed_scope("GARMENT_LENGTH_ONLY")
    assert sc["allowed"] == ["garmentLength"]
    for other in ("sleeveLength", "bodyWidth", "mannequinVolume", "background",
                  "lighting"):
        assert other in sc["forbidden"]


@pytest.mark.parametrize("t", es.EDIT_TYPES)
def test_identity_and_framing_are_forbidden_in_every_edit_type(t):
    forb = es.allowed_scope(t)["forbidden"]
    for key in es.ALWAYS_FORBIDDEN:
        assert key in forb, f"{t} 에서 {key} 가 잠기지 않았다"


def test_background_only_locks_the_product_and_mannequin():
    sc = es.allowed_scope("BACKGROUND_ONLY")
    assert sc["allowed"] == ["background"]
    for key in ("garmentLength", "sleeveLength", "mannequinVolume", "pattern",
                "mannequinIdentity", "framing"):
        assert key in sc["forbidden"]


def test_mannequin_volume_only_locks_background_and_framing():
    sc = es.allowed_scope("MANNEQUIN_VOLUME_ONLY")
    assert sc["allowed"] == ["mannequinVolume"]
    for key in ("background", "lighting", "framing", "camera", "garmentLength"):
        assert key in sc["forbidden"]


# ── locked invariants ────────────────────────────────────────────────────────

BASELINE_INV = {
    "garmentCategory": {"status": "recorded", "value": "top"},
    "pose": {"status": "unavailable", "reason": "no_structured_profile_prompt_fixed"},
}


def test_locked_invariants_keep_unknown_values_unknown():
    """편집 때문에 없던 값이 생기지 않는다."""
    inv = es.locked_invariants_for_edit(BASELINE_INV, "GARMENT_LENGTH_ONLY")
    assert inv["locks"]["pose"]["locked"] is True
    assert inv["locks"]["pose"]["baselineValue"]["status"] == "unavailable"
    assert inv["locks"]["garmentCategory"]["baselineValue"]["value"] == "top"


def test_lock_state_is_recorded_even_without_a_baseline_value():
    """값의 유무와 잠금 여부는 다른 축이다 — 몰라도 잠긴다."""
    inv = es.locked_invariants_for_edit(None, "GARMENT_LENGTH_ONLY")
    assert inv["locks"]["pattern"]["locked"] is True
    assert inv["locks"]["pattern"]["baselineValue"]["status"] == "unavailable"


def test_allowed_axis_is_marked_unlocked():
    inv = es.locked_invariants_for_edit(BASELINE_INV, "GARMENT_LENGTH_ONLY")
    assert inv["locks"]["garmentLength"]["locked"] is False


def test_client_cannot_relax_locks_through_the_payload():
    """서버가 scope 를 만든다 — 입력에 무엇이 오든 잠금 목록은 edit type 이 정한다."""
    inv = es.locked_invariants_for_edit(
        {**BASELINE_INV, "pattern": {"locked": False}}, "GARMENT_LENGTH_ONLY")
    assert inv["locks"]["pattern"]["locked"] is True


# ── 상태 전이 ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("nxt", ["running", "failed"])
def test_queued_can_start_or_fail(nxt):
    assert es.can_transition("queued", nxt)


@pytest.mark.parametrize("nxt", ["pass", "review_required", "reject", "failed"])
def test_running_reaches_terminal_states(nxt):
    assert es.can_transition("running", nxt)


@pytest.mark.parametrize("bad", ["pass", "review_required", "reject"])
def test_queued_cannot_jump_to_a_verdict(bad):
    assert not es.can_transition("queued", bad)


@pytest.mark.parametrize("term", es.TERMINAL)
def test_terminal_states_are_final(term):
    for nxt in es.STATUSES:
        assert not es.can_transition(term, nxt), f"{term} → {nxt} 가 허용됐다"


def test_assert_transition_raises_on_invalid_move():
    with pytest.raises(ValueError):
        es.assert_transition("pass", "running")


# ── migration 정적 계약 ──────────────────────────────────────────────────────

def test_migration_pins_status_values():
    sql_states = set(re.findall(r"'([a-z_]+)'", re.search(
        r"edit_sessions_status_check check \(status in \((.*?)\)\)",
        _sql(), re.S).group(1)))
    assert sql_states == set(es.STATUSES)


def test_migration_requires_completed_at_on_terminal_states():
    assert "edit_sessions_completed_check" in _sql()
    assert "completed_at is not null" in _sql()


def test_migration_caps_retries_at_one():
    """무제한 재시도는 비용 사고다 — 정책이 아니라 제약으로 막는다."""
    assert re.search(r"retry_count >= 0 and retry_count <= 1", _sql())


def test_migration_protects_referenced_baselines():
    assert "baseline_id uuid not null references public.approved_baselines (id) " \
           "on delete restrict" in _sql()


def test_migration_scopes_rls_by_project_ownership():
    sql = _sql()
    assert "alter table public.edit_sessions enable row level security;" in sql
    assert "p.user_id = (select auth.uid())" in sql


def test_migration_does_not_touch_the_jobs_kind_constraint():
    """job kind 를 늘리지 않는다 — 편집은 payload.mode 로 구분한다."""
    sql = _sql()
    assert "jobs_kind_check" not in sql
    assert "check (kind in" not in sql
