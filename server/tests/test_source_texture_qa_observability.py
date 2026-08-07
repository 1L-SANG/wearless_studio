"""Evidence that survives the next candidate.

The existing QA dump writes flat filenames into the artifact root, so candidate B
overwrote candidate A and the next job overwrote both. A real investigation into
sourcePeriodPx 30.0 (candidate A) vs 15.0 (candidate B) could not be replayed
because the exact ROIs behind those numbers were gone by the time anyone looked.

These tests pin the layout and the payload. They do not assert anything about
which period is correct — this patch only records.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from app.services.hybrid_composite import source_texture_context as stc
from app.services.hybrid_composite import source_texture_qa as qa
from app.workers import mannequin_job

HYBRID_SRC = inspect.getsource(mannequin_job._apply_hybrid_composite)
WORKER_SRC = inspect.getsource(mannequin_job)


def _context_cache(period=20.649, roi=(750, 1080, 2205, 2680)):
    cache = stc.SourceTextureContextCache()
    cache.record_source(source_sha256="f9" + "1" * 62,
                        source_landmarks={"shoulder_l": [0.31, 0.28]},
                        source_inventory={"torso_aspect": 1.239},
                        source_component_boxes_norm={}, pattern_model_slot="Detail",
                        pattern_model_asset_id="005fed36", pattern_model_sha256="cb5913a8",
                        pattern_model_roi=(1080, 800, 1400, 1120), detail_validation_ok=True)
    cache.record_period(source_torso_roi=roi, garment_axis="vertical",
                        source_period_px=period,
                        source_period_source=stc.PERIOD_FROM_FRONT_SCAN,
                        source_model_confidence=0.8238,
                        torso_span_px=float(roi[2] - roi[0]))
    return cache


def _prov(candidate, attempt, *, target_period, target_span, ctx_id, period=20.649):
    return qa.provenance_payload(
        job_id="job-1", candidate=candidate, attempt=attempt, context_id=ctx_id,
        source_sha256="f9" + "1" * 62, carrier_sha256=f"c{candidate}{attempt}" + "0" * 61,
        source_roi=(750, 1080, 2205, 2680), source_period_px=period,
        source_period_source=stc.PERIOD_FROM_FRONT_SCAN, source_model_confidence=0.8238,
        source_torso_span_px=1455.0,
        carrier_target_span_px=target_span, repeat_count=1455.0 / period,
        target_period_px=target_period,
        projection_decision="ok", projection_confidence=0.72,
        projection_metrics={"sourceModelConfidence": 0.8238, "repeatConfidence": 1.0,
                            "periodConfidence": 0.98, "scaleConfidence": 0.82},
        scan={"attempted": True, "success": True, "periodPx": period,
              "confidence": 0.8238, "axis": "vertical", "failureReason": None},
        guided={"attempted": False, "selectedPeriodPx": None, "selectedScore": None,
                "candidates": None})


# ------------------------------------------------------------ A: serialization

def test_a_provenance_serialises_and_keeps_one_naming_convention():
    p = _prov("A", 1, target_period=13.78, target_span=675.0, ctx_id="ctx")
    json.dumps(p)
    assert p["schema"] == qa.PROVENANCE_SCHEMA
    flat = [k for k in p if k not in ("scan", "guided", "projectionConfidenceComponents")]
    assert all("_" not in k for k in flat), f"camelCase only: {flat}"
    for nested in ("scan", "guided", "projectionConfidenceComponents"):
        assert all("_" not in k for k in p[nested]), nested


def test_a_context_payload_serialises():
    payload = qa.context_payload(_context_cache().context(), _context_cache(),
                                 job_id="job-1", lease_token="web-1:lease")
    json.dumps(payload)
    assert payload["schema"] == qa.CONTEXT_SCHEMA
    assert payload["sourcePeriodSource"] in ("front_scan", "guided")


def test_a_a_missing_context_says_why_instead_of_inventing_values():
    payload = qa.context_payload(None, stc.SourceTextureContextCache(), job_id="job-1")
    assert payload["contextId"] is None
    assert payload["contextMissingReason"]


def test_a_no_false_persistence_claim():
    p = _prov("A", 1, target_period=13.78, target_span=675.0, ctx_id="ctx")
    assert p["qaCaptureImplemented"] is True
    assert p["productionPersistenceImplemented"] is False


# --------------------------------------------------------- B: no overwriting

def test_b_each_candidate_and_attempt_gets_its_own_leaf(tmp_path):
    runs = [("A", 1), ("A", 2), ("B", 1)]
    paths = [qa.candidate_dir(str(tmp_path), "job-1", c, a, "web-1:lease") for c, a in runs]
    assert len(set(paths)) == 3, "candidate/attempt must not share a leaf"
    for (c, a), leaf in zip(runs, paths):
        assert leaf.name == f"attempt-{a}" and leaf.parent.name == f"candidate-{c}"


# ------------------------- B2: the same job, executed twice (lease recovery)

def test_b_a_requeued_job_does_not_overwrite_the_previous_execution(tmp_path):
    """Candidate letters come from a literal and attempts from range(1, ...), so
    both reset when `recover_stale_leases` requeues a job. Only the lease token
    distinguishes execution #2 from execution #1."""
    first = qa.candidate_dir(str(tmp_path), "job-1", "A", 1, "web-1:aaaa-1111")
    second = qa.candidate_dir(str(tmp_path), "job-1", "A", 1, "web-9:bbbb-2222")
    assert first != second, "a requeued execution must not reuse the leaf"

    qa.write_json(first / "source_texture" / "provenance.json",
                  _prov("A", 1, target_period=13.78, target_span=675.0, ctx_id="ctx-1"))
    before = (first / "source_texture" / "provenance.json").read_bytes()
    qa.write_json(second / "source_texture" / "provenance.json",
                  _prov("A", 1, target_period=7.17, target_span=712.3, ctx_id="ctx-2"))
    assert (first / "source_texture" / "provenance.json").read_bytes() == before


def test_b_the_context_snapshot_is_execution_scoped_too(tmp_path):
    a = qa.context_dir(str(tmp_path), "job-1", "web-1:aaaa-1111")
    b = qa.context_dir(str(tmp_path), "job-1", "web-9:bbbb-2222")
    assert a != b
    qa.write_json(a / "source_texture_context.json", {"contextId": "one"})
    qa.write_json(b / "source_texture_context.json", {"contextId": "two"})
    assert json.loads((a / "source_texture_context.json").read_text())["contextId"] == "one"


def test_b_the_execution_scope_never_writes_the_raw_lease_token(tmp_path):
    token = "web-4242:11111111-2222-3333-4444-555555555555"
    scope, scope_type = qa.execution_scope(token)
    assert token not in scope and "4242" not in scope
    assert scope_type == qa.EXECUTION_SCOPE_LEASE_TOKEN
    leaf = qa.candidate_dir(str(tmp_path), "job-1", "A", 1, token)
    assert token not in str(leaf)
    p = qa.provenance_payload(job_id="job-1", candidate="A", attempt=1, lease_token=token)
    assert token not in json.dumps(p)
    assert p["executionScopeType"] == qa.EXECUTION_SCOPE_LEASE_TOKEN


def test_b_the_scope_is_deterministic_and_named_for_what_it_is():
    a, _ = qa.execution_scope("web-1:same")
    b, _ = qa.execution_scope("web-1:same")
    assert a == b, "same execution must resolve to the same directory"
    # it is a lease token, not a generation run id — do not claim otherwise
    src = inspect.getsource(qa)
    assert "generationRunId" not in src and "generation_run_id" not in src


def test_b_a_missing_lease_token_is_labelled_unscoped_not_faked():
    scope, scope_type = qa.execution_scope(None)
    assert scope == qa.UNSCOPED_EXECUTION
    assert scope_type == qa.EXECUTION_SCOPE_NONE


def test_b_writing_b_leaves_a_byte_identical(tmp_path):
    written = {}
    for c, a, tp, ts in (("A", 1, 13.78, 675.0), ("A", 2, 14.10, 690.0), ("B", 1, 7.17, 712.3)):
        leaf = (qa.candidate_dir(str(tmp_path), "job-1", c, a, "web-1:lease")
                / "source_texture" / "provenance.json")
        assert qa.write_json(leaf, _prov(c, a, target_period=tp, target_span=ts,
                                         ctx_id="ctx-same")) is None
        written[(c, a)] = leaf
    first_a1 = written[("A", 1)].read_bytes()
    # re-write B afterwards; A must be untouched
    qa.write_json(written[("B", 1)], _prov("B", 1, target_period=7.17, target_span=712.3,
                                           ctx_id="ctx-same"))
    assert written[("A", 1)].read_bytes() == first_a1
    assert len({p.read_bytes() for p in written.values()}) == 3


def test_b_two_jobs_do_not_collide(tmp_path):
    a = qa.candidate_dir(str(tmp_path), "job-1", "A", 1, "web-1:lease")
    b = qa.candidate_dir(str(tmp_path), "job-2", "A", 1, "web-1:lease")
    assert a != b


def test_b_identifiers_cannot_escape_the_qa_root(tmp_path):
    leaf = qa.candidate_dir(str(tmp_path), "../../etc", "A/../B", 1, "../evil")
    assert leaf.is_relative_to(tmp_path)


# ------------------------------------------- C: same context across candidates

def test_c_all_candidates_report_the_same_context(tmp_path):
    cache = _context_cache()
    ctx_id = cache.context().context_id()
    payloads = []
    for c, a, tp, ts in (("A", 1, 13.78, 675.0), ("A", 2, 14.10, 690.0), ("B", 1, 7.17, 712.3)):
        leaf = (qa.candidate_dir(str(tmp_path), "job-1", c, a, "web-1:lease")
                / "source_texture" / "provenance.json")
        qa.write_json(leaf, _prov(c, a, target_period=tp, target_span=ts, ctx_id=ctx_id))
        payloads.append(json.loads(leaf.read_text()))
    assert len({p["contextId"] for p in payloads}) == 1
    assert len({p["sourceSha256"] for p in payloads}) == 1
    assert len({p["sourcePeriodPx"] for p in payloads}) == 1
    assert len({tuple(p["sourceRoi"]) for p in payloads}) == 1
    assert len({p["sourcePeriodSource"] for p in payloads}) == 1


# ------------------------------------------- D: source vs candidate separation

def test_d_target_metrics_differ_per_candidate_while_source_holds(tmp_path):
    cache = _context_cache()
    ctx_id = cache.context().context_id()
    a = _prov("A", 1, target_period=13.78, target_span=675.0, ctx_id=ctx_id)
    b = _prov("B", 1, target_period=7.17, target_span=712.3, ctx_id=ctx_id)
    assert a["sourcePeriodPx"] == b["sourcePeriodPx"]
    assert a["targetPeriodPx"] != b["targetPeriodPx"]
    assert a["carrierTargetSpanPx"] != b["carrierTargetSpanPx"]


def test_d_no_ambiguous_field_name_merges_the_two():
    p = _prov("A", 1, target_period=13.78, target_span=675.0, ctx_id="ctx")
    for bad in ("sourceTargetPeriod", "periodPx", "period", "sourceTarget"):
        assert bad not in p, bad
    assert "sourcePeriodPx" in p and "targetPeriodPx" in p


def test_d_confidence_components_are_preserved():
    p = _prov("A", 1, target_period=13.78, target_span=675.0, ctx_id="ctx")
    comps = p["projectionConfidenceComponents"]
    assert set(comps) == {"sourceModelConfidence", "repeatConfidence",
                          "periodConfidence", "scaleConfidence"}
    assert comps["periodConfidence"] == 0.98


# --------------------------------------------------- E: disabled = no writes

def test_e_no_qa_directory_means_no_write(tmp_path, monkeypatch):
    monkeypatch.delenv("HYBRID_COMPOSITE_ARTIFACT_DIR", raising=False)
    flush = HYBRID_SRC.split("def qa_flush():", 1)[1].split("artifact_state = {", 1)[0]
    assert 'os.getenv("HYBRID_COMPOSITE_ARTIFACT_DIR")' in flush
    assert "if not root:" in flush and "return" in flush
    assert list(tmp_path.iterdir()) == []


def test_e_the_existing_env_var_is_reused_not_replaced():
    assert WORKER_SRC.count('os.getenv("HYBRID_COMPOSITE_ARTIFACT_DIR")') >= 2
    assert "SOURCE_TEXTURE_QA_DIR" not in WORKER_SRC, "no second QA env var"


# ----------------------------------------------------- F: write-error isolated

def test_f_an_unwritable_path_is_reported_not_raised():
    err = qa.write_json(Path("/proc/nonexistent-qa/provenance.json"), {"a": 1})
    assert err is not None


def test_f_flush_swallows_everything_and_changes_no_decision():
    flush = HYBRID_SRC.split("def qa_flush():", 1)[1].split("artifact_state = {", 1)[0]
    assert "try:" in flush and "except Exception" in flush
    for forbidden in ("return await fail", "raise ", "retry"):
        assert forbidden not in flush, forbidden


def test_f_qa_state_never_steers_control_flow():
    steering = [ln.strip() for ln in HYBRID_SRC.splitlines()
                if ("qa_written" in ln or "qa_provenance" in ln or "qa_flush" in ln)
                and ln.strip().startswith(("if ", "elif ", "while ", "return ", "raise ",
                                           "assert "))]
    assert steering == [], steering


# ------------------------------------------------------- G: no provider calls

def test_g_recording_adds_no_provider_or_cv_work():
    """Parsed, not grepped — the module documents the scorer it must not call."""
    import ast

    tree = ast.parse(inspect.getsource(qa))
    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            called.add(f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", ""))
        if isinstance(node, ast.Await):
            called.add("<await>")
    imported = {a.name.split(".")[0] for n in ast.walk(tree)
                if isinstance(n, ast.Import) for a in n.names}
    imported |= {(n.module or "").split(".")[0] for n in ast.walk(tree)
                 if isinstance(n, ast.ImportFrom)}
    assert "<await>" not in called
    for name in called:
        for forbidden in ("gemini", "extract_", "find_period", "generate", "imencode",
                          "imdecode", "imwrite"):
            assert forbidden not in name.lower(), f"{name} looks like {forbidden}"
    assert not ({"cv2", "numpy"} & imported), imported


def test_g_the_flush_only_reads_values_the_run_already_has():
    flush = HYBRID_SRC.split("def qa_flush():", 1)[1].split("artifact_state = {", 1)[0]
    for forbidden in ("await ", "extract_", "find_period", "imencode", "imdecode"):
        assert forbidden not in flush, forbidden


def test_g_guided_candidates_are_null_with_an_honest_reason():
    """The scorer keeps its losing candidates internal; a second run is not an
    observation of the first, so they are recorded as unavailable."""
    p = _prov("A", 1, target_period=13.78, target_span=675.0, ctx_id="ctx")
    assert p["guided"]["candidates"] is None
    assert p["guided"]["candidatesMissingReason"] == qa.GUIDED_CANDIDATES_UNAVAILABLE
    from app.services.hybrid_composite import stripe_model
    guided_src = inspect.getsource(stripe_model.find_period_guided)
    assert "return best" in guided_src, "scorer still returns only its winner"


# --------------------------------------------- prior patches left alone

def test_the_flat_dump_is_still_present():
    """Not removed — other debugging still uses it; P2 only adds a scoped layout."""
    assert "_dump_composite_artifacts" in WORKER_SRC
    assert 'd / "geometry.json"' in WORKER_SRC


def test_p1_context_semantics_untouched():
    cache = _context_cache()
    assert cache.compute_count == 0          # records are not computations
    ctx = cache.context()
    with pytest.raises(Exception):
        ctx.source_period_px = 1.0


def test_no_threshold_or_policy_symbol_added_by_p2():
    qa_src = inspect.getsource(qa)
    for forbidden in ("MIN_CONFIDENCE", "MAX_CAPE", "threshold", "reject", "fail_closed"):
        assert forbidden not in qa_src, forbidden
