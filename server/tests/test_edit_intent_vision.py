"""Phase 3 P0-C 5/N — Vision 의미 관찰과 Decision Engine 결합.

계약:
  · Vision 은 **관찰만** 한다. decision/verdict 류 필드는 무시가 아니라 **스키마가 거부**한다.
  · 관찰 불가는 false 가 아니라 null — false 는 "확인했고 안 바뀌었다"로 읽힌다.
  · 최종 판정은 edit_intent_qc.decide 만 만든다.
  · Vision 장애는 review_required 다. reject·환불이 아니다.
  · 결과 1개당 Vision 1회. 재시도하면 새 결과에만 1회.
"""

import asyncio
import contextlib
import types

import numpy as np
import pytest

from app import repo
from app.agents import edit_intent_vision as eiv
from app.agents.gemini_image import InlineImage
from app.agents.vision_llm import VisionError
from app.services import edit_intent_qc as qc
from app.services import edit_session as es
from app.workers import mannequin_job as mj
from conftest import make_settings

LENGTH_SCOPE = es.allowed_scope("GARMENT_LENGTH_ONLY")


def _obs(**over):
    base = {f: False for f in eiv.OBSERVATION_FIELDS}
    base["requestedChangeApplied"] = True
    base.update({"confidence": 0.9, "uncertainFields": [], "evidence": []})
    base.update(over)
    return base


# ── 스키마 / 검증 ───────────────────────────────────────────────────────────

def test_schema_forbids_extra_properties_and_requires_every_observation():
    sc = eiv.schema()
    assert sc["additionalProperties"] is False
    for f in eiv.OBSERVATION_FIELDS:
        assert f in sc["properties"] and sc["properties"][f]["type"] == ["boolean", "null"]
        assert f in sc["required"]
    assert "confidence" in sc["required"]


def test_schema_has_no_decision_field():
    props = set(eiv.schema()["properties"])
    for banned in eiv.FORBIDDEN_FIELDS:
        assert banned not in props


def test_valid_observation_normalises():
    out = eiv.validate(_obs(collarChanged=None, evidence=["left cuff higher"]))
    assert out["collarChanged"] is None
    assert "collarChanged" in out["uncertainFields"], "null 은 불확실 목록에 들어간다"
    assert out["evidence"] == ["left cuff higher"]
    assert out["confidence"] == 0.9


def test_decision_field_in_the_response_is_rejected():
    """무시하지 않는다 — 무시하면 다음 사람이 그 필드를 쓰기 시작한다."""
    with pytest.raises(VisionError) as e:
        eiv.validate({**_obs(), "decision": "pass"})
    assert "판정 필드" in str(e.value)


@pytest.mark.parametrize("field", ["verdict", "approved", "score",
                                   "regenerationInstructions"])
def test_other_verdict_shaped_fields_are_rejected(field):
    with pytest.raises(VisionError):
        eiv.validate({**_obs(), field: "x"})


def test_unknown_field_is_rejected():
    with pytest.raises(VisionError):
        eiv.validate({**_obs(), "sleeveColourChanged": True})


def test_missing_field_is_rejected():
    raw = _obs()
    del raw["collarChanged"]
    with pytest.raises(VisionError) as e:
        eiv.validate(raw)
    assert "누락" in str(e.value)


@pytest.mark.parametrize("bad", ["true", 1, 0.5, []])
def test_non_boolean_observation_is_rejected(bad):
    """타입이 어긋난 관찰은 '모르겠다'보다 나쁘다 — null 로 눕히지 않고 거부한다."""
    with pytest.raises(VisionError):
        eiv.validate(_obs(collarChanged=bad))


@pytest.mark.parametrize("bad", ["0.9", None, True, {}])
def test_bad_confidence_is_rejected(bad):
    with pytest.raises(VisionError):
        eiv.validate(_obs(confidence=bad))


@pytest.mark.parametrize("bad", [1.7, -0.2, 100.0])
def test_confidence_out_of_range_is_rejected(bad):
    """클램프하지 않는다 — 1.7 을 1.0 으로 접으면 "매우 확신"이라는 거짓 신호가 된다."""
    with pytest.raises(VisionError):
        eiv.validate(_obs(confidence=bad))


@pytest.mark.parametrize("ok", [0.0, 0.5, 1.0])
def test_confidence_in_range_is_kept_exactly(ok):
    assert eiv.validate(_obs(confidence=ok))["confidence"] == ok


def test_evidence_is_capped_and_trimmed():
    out = eiv.validate(_obs(evidence=["x" * 300, "a", "b", "c", "d", "e"]))
    assert len(out["evidence"]) <= 4
    assert all(len(e) <= 100 for e in out["evidence"])


def test_unknown_uncertain_field_name_is_rejected():
    """조용히 버리면 "모델이 무엇을 모른다고 했는지"가 사라진다."""
    with pytest.raises(VisionError):
        eiv.validate(_obs(uncertainFields=["collarChanged", "nonsense"]))


@pytest.mark.parametrize("bad", [[1], [None], [{"a": 1}]])
def test_non_string_evidence_is_rejected(bad):
    with pytest.raises(VisionError):
        eiv.validate(_obs(evidence=bad))


def test_schema_declares_the_same_bounds_as_the_validator():
    sc = eiv.schema()
    assert sc["properties"]["confidence"]["minimum"] == 0
    assert sc["properties"]["confidence"]["maximum"] == 1
    assert sc["properties"]["uncertainFields"]["items"]["enum"] == list(
        eiv.OBSERVATION_FIELDS)
    assert sc["properties"]["evidence"]["maxItems"] == eiv._EVIDENCE_MAX
    assert sc["properties"]["evidence"]["items"]["maxLength"] == eiv._EVIDENCE_LEN


def test_malformed_response_is_rejected():
    with pytest.raises(VisionError):
        eiv.validate("not a dict")


def test_failure_meta_keeps_no_raw_provider_text():
    meta = eiv.failure_meta(VisionError("Gemini 500: https://host/x?key=SECRET body..."))
    flat = str(meta)
    assert "SECRET" not in flat and "://" not in flat
    assert meta["status"] == "provider_error" and meta["errorType"] == "VisionError"


def test_prompt_names_the_image_roles_and_forbids_verdicts():
    p = eiv.build_prompt(edit_type="GARMENT_LENGTH_ONLY",
                         adjustments={"garmentLengthStep": -1},
                         allowed_scope=LENGTH_SCOPE, source_ref_count=2)
    assert "IMAGE 1 = APPROVED BASELINE" in p and "IMAGE 2 = EDITED RESULT" in p
    assert "GARMENT_LENGTH_ONLY" in p
    assert "null is not false" in p
    assert "Do NOT output any approval, verdict" in p


# ── Vision 호출 (fallback 재사용) ───────────────────────────────────────────

def test_observe_sends_baseline_first_then_edited_then_sources(monkeypatch):
    seen = {}

    async def fake_call(settings, prompt, images, schema, thinking_level=None):
        seen["images"] = images
        seen["schema"] = schema
        return _obs(), "gemini"

    monkeypatch.setattr(eiv, "analyze_with_fallback", fake_call)
    base = InlineImage("image/png", b"base")
    edited = InlineImage("image/png", b"edit")
    refs = [InlineImage("image/png", b"r1"), InlineImage("image/png", b"r2")]
    obs, meta = asyncio.run(eiv.observe(
        make_settings(), baseline=base, edited=edited, edit_type="GARMENT_LENGTH_ONLY",
        adjustments={"garmentLengthStep": -1}, allowed_scope=LENGTH_SCOPE,
        source_refs=refs))
    assert [i.data for i in seen["images"]] == [b"base", b"edit", b"r1", b"r2"]
    assert meta["provider"] == "gemini" and meta["status"] == "ok"
    assert meta["promptVersion"] == eiv.PROMPT_VERSION
    assert isinstance(meta["latencyMs"], int)
    assert obs["requestedChangeApplied"] is True


def test_observe_propagates_provider_failure(monkeypatch):
    async def boom(*a, **k):
        raise VisionError("Gemini 503")

    monkeypatch.setattr(eiv, "analyze_with_fallback", boom)
    with pytest.raises(VisionError):
        asyncio.run(eiv.observe(
            make_settings(), baseline=InlineImage("image/png", b"b"),
            edited=InlineImage("image/png", b"e"), edit_type="GARMENT_LENGTH_ONLY",
            adjustments={}, allowed_scope=LENGTH_SCOPE))


# ── Decision 결합 ───────────────────────────────────────────────────────────

GOOD_METRICS = {"confidence": 0.9, "delta": {"hemY": -0.08}}


def _decide(vision, metrics=None, edit_type="GARMENT_LENGTH_ONLY", ratio=-0.08):
    return qc.decide(edit_type=edit_type, allowed_scope=es.allowed_scope(edit_type),
                     target_ratio=ratio, metrics=metrics or GOOD_METRICS,
                     vision=vision, require_vision=True)


def test_quant_pass_plus_vision_pass_is_pass():
    assert _decide(_obs())["decision"] == "pass"


def test_locked_change_seen_by_vision_is_reject():
    r = _decide(_obs(collarChanged=True))
    assert r["decision"] == "reject"
    assert "collarType" in r["lockedInvariantViolations"]


def test_low_vision_confidence_goes_to_review():
    r = _decide(_obs(confidence=0.2))
    assert r["decision"] == "review_required"
    assert r["checks"]["visionTrusted"] is False


def test_vision_unavailable_goes_to_review_not_pass():
    r = _decide(None)
    assert r["decision"] == "review_required"
    assert r["checks"]["visionAvailable"] is False


def test_requested_change_not_seen_is_reject_when_measurement_agrees():
    r = _decide(_obs(requestedChangeApplied=False),
                metrics={"confidence": 0.9, "delta": {"hemY": 0.0}})
    assert r["decision"] == "reject"
    assert r["requestedChangeSatisfied"] is False


def test_conflict_between_measurement_and_vision_goes_to_review():
    r = _decide(_obs(requestedChangeApplied=False))     # 측정은 됐다고 함
    assert r["decision"] == "review_required"
    assert r["checks"]["visionConflict"] is True


def test_null_observation_is_not_treated_as_unchanged():
    """null 을 false 처럼 쓰면 잠긴 항목이 바뀌었는데 통과한다."""
    # 워커는 항상 validate 를 거친 관찰을 넘긴다 — 그게 null 을 불확실 목록에 넣는다
    r = _decide(eiv.validate(_obs(collarChanged=None)))
    assert "collarType" not in r["lockedInvariantViolations"]
    assert r["decision"] == "pass"      # 다른 신호가 없으면 통과 — 다만 위반으로 세지 않는다
    assert "collarChanged" in r["checks"]["visionUncertainFields"]


def test_custom_review_required_stays_review_even_with_a_clean_observation():
    r = _decide(_obs(), edit_type="CUSTOM_REVIEW_REQUIRED", ratio=None)
    assert r["decision"] == "review_required"


@pytest.mark.parametrize("edit_type,field,expect", [
    ("BACKGROUND_ONLY", "backgroundChanged", "pass"),
    ("BACKGROUND_ONLY", "collarChanged", "reject"),
    ("LIGHTING_ONLY", "lightingChanged", "pass"),
    ("LIGHTING_ONLY", "patternChanged", "reject"),
    ("SLEEVE_LENGTH_ONLY", "sleevesChanged", "pass"),
    ("MANNEQUIN_VOLUME_ONLY", "backgroundChanged", "reject"),
    ("GARMENT_LENGTH_ONLY", "sleevesChanged", "reject"),
])
def test_per_edit_type_semantic_policy(edit_type, field, expect):
    ratio = es.target_delta_ratio(edit_type, {es._TYPE_FIELD.get(edit_type, "x"): -1}) \
        if edit_type in es._TYPE_FIELD else None
    metrics = {"confidence": 0.9,
               "delta": {"hemY": -0.08, "cuffY": -0.08, "bodyWidth": -0.08}}
    r = qc.decide(edit_type=edit_type, allowed_scope=es.allowed_scope(edit_type),
                  target_ratio=ratio, metrics=metrics, vision=_obs(**{field: True}),
                  require_vision=True)
    if expect == "reject":
        assert r["decision"] == "reject", r["lockedInvariantViolations"]
    else:
        assert r["decision"] != "reject"


# ── 워커: 호출 횟수·실패 정책·preflight ─────────────────────────────────────

def _png(v=235):
    import cv2
    img = np.full((600, 400, 3), v, np.uint8)
    img[80:480, 120:280] = (120, 120, 120)
    ok, buf = cv2.imencode(".png", img)
    return buf.tobytes()


BASE_PNG, EDIT_PNG = _png(), _png(236)


class _Conn:
    async def commit(self):
        return None


class _Pool:
    def connection(self):
        @contextlib.asynccontextmanager
        async def _cm():
            yield _Conn()

        return _cm()


BASELINE = {"id": "base-1", "baseline_cut_id": "cut-1", "output_id": "out-base",
            "generation_run_id": "run-base", "cut_client_id": "A-3",
            "locked_invariants": {}}


def _run_worker(monkeypatch, *, session_status="queued", transition_error=None,
                vision_error=None, qc_decision="pass", flag="enforce",
                session_job_id="j1", baseline=BASELINE):
    seen = {"gemini": 0, "vision": 0, "failed": [], "success": [], "events": []}

    class _Gemini:
        async def generate_content_image(self, *a, **k):
            seen["gemini"] += 1
            return types.SimpleNamespace(image=EDIT_PNG, mime="image/png",
                                         latency_ms=1, usage=None)

    class _R2:
        def get_bytes(self, key):
            return BASE_PNG

        def put_bytes(self, *a, **k):
            return None

        def delete(self, key):
            return None

    async def get_product(conn, pid):
        return {"name": "t", "clothing_type": "top", "fit": "regular", "colors": []}

    async def get_analysis(conn, pid):
        return {}

    async def get_active_baseline(conn, pid):
        return baseline

    async def get_edit_session(conn, sid):
        return {"id": sid, "baseline_id": "base-1", "status": session_status,
                "job_id": session_job_id, "allowed_scope": None,
                "locked_invariants": {}}

    async def update_edit_session(conn, **kw):
        if transition_error and kw.get("status") == "running":
            raise transition_error
        return {"id": "s", "status": kw.get("status")}

    async def get_parent(conn, uid, pid):
        return {"id": "A-3", "asset_id": "a", "r2_key": "k", "mime_type": "image/png"}

    async def noop(conn, **kw):
        return None

    async def finalize_success(conn, **kw):
        seen["success"].append(kw)
        return {"cuts": kw["candidates"], "available": 1}

    async def finalize_failure(conn, **kw):
        seen["failed"].append(kw)
        return True

    for name, fn in (("get_product", get_product), ("get_analysis", get_analysis),
                     ("get_active_baseline", get_active_baseline),
                     ("get_edit_session", get_edit_session),
                     ("update_edit_session", update_edit_session),
                     ("get_mannequin_edit_parent", get_parent),
                     ("insert_generation_run", noop), ("update_generation_run", noop),
                     ("update_generation_run_prompt_key", noop),
                     ("set_edit_session_prompt", noop),
                     ("finalize_mannequin_success", finalize_success),
                     ("finalize_mannequin_failure", finalize_failure)):
        monkeypatch.setattr(repo, name, fn)

    async def emit(pool, job_id, kind, payload):
        seen["events"].append(payload)

    monkeypatch.setattr(mj, "_emit", emit)

    async def fake_observe(settings, **kw):
        seen["vision"] += 1
        if vision_error:
            raise vision_error
        return _obs(), {"provider": "gemini", "promptVersion": eiv.PROMPT_VERSION,
                        "latencyMs": 5, "imageCount": 2, "status": "ok"}

    monkeypatch.setattr(mj.edit_intent_vision, "observe", fake_observe)

    def fake_eval(**kw):
        return {"decision": qc_decision, "requestedChangeSatisfied": True,
                "requestedChangeMeasurements": {}, "unexpectedChanges": [],
                "lockedInvariantViolations": [], "regenerationInstructions":
                ["fix"] if qc_decision == "reject" else [], "checks": {}, "metrics": {}}

    monkeypatch.setattr(mj.edit_intent_qc, "evaluate", fake_eval)
    settings = make_settings(r2_bucket="b", generation_run_log="shadow",
                             mannequin_edit_intent_qc=flag)
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        settings=settings, pool=_Pool(), r2=_R2(), gemini=_Gemini()))
    job = {"id": "j1", "user_id": "u1", "project_id": "p1", "lease_token": "t",
           "credits_reserved": 2,
           "payload": {"mode": "edit", "editType": "GARMENT_LENGTH_ONLY",
                       "adjustments": {"garmentLengthStep": -1},
                       "editSessionId": "sess-1"}}
    asyncio.run(mj.run_mannequin_job(app, job))
    return seen


def test_one_vision_call_per_result(monkeypatch):
    seen = _run_worker(monkeypatch)
    assert seen["gemini"] == 1 and seen["vision"] == 1


def test_retry_gets_its_own_vision_call(monkeypatch):
    seen = _run_worker(monkeypatch, qc_decision="reject")
    assert seen["gemini"] == seen["vision"], "결과마다 정확히 1회여야 한다"
    assert seen["gemini"] <= 2, "재시도 상한을 넘겼다"


def test_terminal_session_blocks_provider_and_vision(monkeypatch):
    seen = _run_worker(monkeypatch, session_status="pass")
    assert seen["gemini"] == 0 and seen["vision"] == 0
    assert seen["failed"][0]["metadata"]["error"] == "edit_session_not_runnable"


def test_transition_failure_blocks_provider_and_vision(monkeypatch):
    seen = _run_worker(monkeypatch,
                       transition_error=repo.InvalidEditTransition("nope"))
    assert seen["gemini"] == 0 and seen["vision"] == 0
    assert seen["failed"][0]["metadata"]["error"] == "edit_session_not_runnable"


def test_db_failure_during_preflight_blocks_provider(monkeypatch):
    seen = _run_worker(monkeypatch, transition_error=RuntimeError("db down"))
    assert seen["gemini"] == 0 and seen["vision"] == 0
    assert seen["failed"][0]["metadata"]["error"] == "edit_session_unavailable"


def test_session_bound_to_another_job_blocks_provider(monkeypatch):
    seen = _run_worker(monkeypatch, session_job_id="other-job")
    assert seen["gemini"] == 0 and seen["vision"] == 0
    assert seen["failed"][0]["metadata"]["error"] == "edit_session_job_mismatch"


def test_baseline_mismatch_blocks_provider(monkeypatch):
    seen = _run_worker(monkeypatch, baseline={**BASELINE, "id": "base-other"})
    assert seen["gemini"] == 0 and seen["vision"] == 0


def test_vision_failure_does_not_block_delivery_or_refund(monkeypatch):
    """장애만으로 reject·환불하지 않는다 — 판정은 review 로 내려간다."""
    seen = _run_worker(monkeypatch, vision_error=VisionError("Gemini 503"),
                       qc_decision="review_required")
    assert seen["vision"] == 1
    assert seen["success"], "Vision 장애가 출고를 막았다"
    assert not seen["failed"]


def test_events_expose_only_a_summary(monkeypatch):
    seen = _run_worker(monkeypatch)
    ev = [e for e in seen["events"] if e.get("status") == "edit_intent_qc"]
    assert ev and set(ev[0]) <= {"status", "attempt", "decision", "unexpectedChanges",
                                 "lockedInvariantViolations", "requestedChangeSatisfied",
                                 "visionStatus"}
    assert ev[0]["visionStatus"] == "ok"
    flat = str(seen["events"])
    assert "IMAGE 1" not in flat and "://" not in flat, "프롬프트·URL 이 이벤트에 샜다"


def test_qc_result_stores_normalised_observation_and_meta(monkeypatch):
    seen = _run_worker(monkeypatch)
    es_arg = seen["success"][0]["edit_session"]
    vision = es_arg["qc_result"]["vision"]
    assert vision["meta"]["provider"] == "gemini"
    assert vision["meta"]["promptVersion"] == eiv.PROMPT_VERSION
    assert set(vision["observation"]) == set(eiv.OBSERVATION_FIELDS) | {
        "confidence", "uncertainFields", "evidence"}


# ── 두 파이프라인의 요청 형식 (9/N — 실수집에서 발견) ──────────────────────

def test_vary_changes_reach_the_prompt():
    """`{"changes": [...]}` 를 못 알아보면 vary 요청이 프롬프트에 한 줄도 안 실린다."""
    from app.agents.edit_intent_vision import build_prompt
    p = build_prompt(edit_type="BACKGROUND_ONLY",
                     adjustments={"changes": [{"type": "bg", "value": "밝은 스튜디오"}]},
                     allowed_scope={"allowed": [], "forbidden": []})
    assert "bg: 밝은 스튜디오" in p


def test_mannequin_step_shape_still_works():
    from app.agents.edit_intent_vision import build_prompt
    p = build_prompt(edit_type="LENGTH_ONLY", adjustments={"length": -2},
                     allowed_scope={"allowed": [], "forbidden": []})
    assert "length -2 step" in p


def test_vary_changes_do_not_crash_on_odd_shapes():
    from app.agents.edit_intent_vision import _describe_adjustments
    assert _describe_adjustments({"changes": [None, {}, {"type": "bg"}]}) == ["bg"]
    assert _describe_adjustments({"changes": []}) == []
    assert _describe_adjustments({}) == []
    assert _describe_adjustments(None) == []


def test_change_values_are_length_bounded_in_the_prompt():
    from app.agents.edit_intent_vision import _describe_adjustments
    out = _describe_adjustments({"changes": [{"type": "bg", "value": "가" * 500}]})
    assert len(out[0]) <= 130


def test_the_worker_passes_vary_changes_to_vision():
    import inspect
    from app.workers import editor_image_job
    src = inspect.getsource(editor_image_job)
    assert 'adjustments={"changes": ctx.get("changes") or []}' in src
    assert "adjustments={}," not in src
