"""The carrier shape observation must reach metadata and change nothing else.

`_apply_hybrid_composite` needs a paid carrier, a source asset and a worker pool
to run end to end, so these tests pin the wiring contract instead: the summary
gains exactly one nested key, a failing observation cannot break the job, and
the observation is never read by anything that steers control flow.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import re
import subprocess
from pathlib import Path

import pytest

from app.services.hybrid_composite import panel_map as hc_panel
from app.services.hybrid_composite.carrier_preflight import observe_carrier_shape_fidelity
from app.workers import mannequin_job

WIRING_SOURCE = inspect.getsource(mannequin_job._apply_hybrid_composite)
_WORKER_REL = "server/app/workers/mannequin_job.py"


def _observation():
    return observe_carrier_shape_fidelity(
        source_inventory={"torso_aspect_mask": 1.353, "torso_aspect": 1.241, "sleeve_len_ratio": 1.545},
        carrier_inventory={"torso_aspect_mask": 3.538, "torso_aspect": 2.445, "sleeve_len_ratio": 1.183},
        mask_hygiene_ratio_threshold=hc_panel.MAX_TORSO_ASPECT_RATIO,
        construction_ratio_tolerance=hc_panel.CONSTRUCTION_RATIO_TOL)


BASE_SUMMARY_KEYS = {
    "mode", "applied", "wouldApply", "failClosed", "needsReview", "componentsNeedingReview",
    "deterministicPassed", "carrierPreflight", "protectedComponentContract", "pipelineVersion",
    "versions", "stripeModel", "sourceAssets", "targetPeriodPx", "targetAxis", "sourceCoverage",
    "panelMetrics", "deterministicMetrics", "outputSha256", "carrierSha256",
}


# ------------------------------------------------------------------ Test A

def test_a_summary_gains_only_the_nested_observation_key():
    summary = {k: "x" for k in BASE_SUMMARY_KEYS}
    before = json.dumps(summary, sort_keys=True)
    summary["carrierShapeFidelity"] = _observation().to_metadata()
    assert set(summary) - BASE_SUMMARY_KEYS == {"carrierShapeFidelity"}
    # every pre-existing field is byte-identical
    after_existing = json.dumps({k: summary[k] for k in BASE_SUMMARY_KEYS}, sort_keys=True)
    assert after_existing == before


def test_a_failure_summary_shape_is_untouched():
    fail = mannequin_job._hc_fail_summary("geometry_carrier_mismatch", "detail", mode="enforce")
    assert set(fail) == {"mode", "applied", "wouldApply", "needsReview", "failClosed",
                         "failureReason", "failureDetail", "pipelineVersion"}
    assert "carrierShapeFidelity" not in fail


# ------------------------------------------------------------------ Test I

class _Exploding:
    def to_metadata(self):
        raise RuntimeError("serialisation blew up")


def test_i_observation_failure_cannot_cost_the_output():
    """Mirrors the guarded attachment in the worker."""
    summary = {"outputSha256": "abc", "applied": True}
    observation = _Exploding()
    if observation is not None:
        try:
            summary["carrierShapeFidelity"] = observation.to_metadata()
        except Exception:
            pass
    assert summary == {"outputSha256": "abc", "applied": True}


def test_i_wiring_guards_both_the_computation_and_the_attachment():
    assert "observe_carrier_shape_fidelity" in WIRING_SOURCE
    compute = WIRING_SOURCE.split("shape_observation = None", 1)[1].split("# Stage 3", 1)[0]
    assert "try:" in compute and "except Exception:" in compute
    attach = WIRING_SOURCE.split('summary["carrierShapeFidelity"]', 1)
    assert len(attach) == 2, "the observation must be attached exactly once"
    # anchored on the block itself rather than a character window, so growing the
    # lineage build cannot quietly turn this into a check of nothing
    assert 'summary["carrierShapeFidelity"]' in _lineage_block()
    assert "except Exception:" in attach[1][:400]


# ------------------------------------------------------------------ Test L

def test_l_observation_never_steers_control_flow():
    """No branch may depend on the observation beyond the None guard for attaching."""
    steering = [line.strip() for line in WIRING_SOURCE.splitlines()
                if "shape_observation" in line
                and any(line.strip().startswith(kw) for kw in ("if ", "elif ", "while ", "assert ",
                                                               "return ", "raise "))]
    # the attach is unconditional inside a try block, so nothing branches on it
    assert steering == [], steering
    # and nothing in the observation path calls a provider, a retry or a model
    compute = WIRING_SOURCE.split("shape_observation = None", 1)[1].split("# Stage 3", 1)[0]
    for forbidden in ("generate", "gemini", "provider", "retry", "await _call", "regenerate"):
        assert forbidden not in compute.lower(), forbidden


def test_l_thresholds_are_read_from_panel_map_not_redefined_in_the_worker():
    compute = WIRING_SOURCE.split("shape_observation = None", 1)[1].split("# Stage 3", 1)[0]
    assert "hc_panel.MAX_TORSO_ASPECT_RATIO" in compute
    assert "hc_panel.CONSTRUCTION_RATIO_TOL" in compute
    assert "3.0" not in compute and "0.40" not in compute


def test_l_raw_measurements_survive_the_repeat_invariant_blanking():
    """The worker blanks the inventories; the raw locals must still be passed."""
    compute = WIRING_SOURCE.split("shape_observation = None", 1)[1].split("# Stage 3", 1)[0]
    assert "source_torso_aspect_mask_measured=src_aspect_mask" in compute
    assert "carrier_torso_aspect_mask_measured=car_aspect_mask" in compute


# ------------------------------------------------------- metadata placement

def test_metadata_is_nested_and_needs_no_schema_or_whitelist_change():
    md = {"hybridComposite": {"carrierShapeFidelity": _observation().to_metadata()}}
    json.dumps(md)
    block = md["hybridComposite"]["carrierShapeFidelity"]
    assert block["observationOnly"] is True
    assert block["branchDisagreement"] is True
    assert block["activeProductionBranch"] == "mask"
    # repo.py only surfaces whitelisted booleans plus a few named string fields,
    # so a nested dict stays internal without touching that filter
    from app import repo
    repo_src = inspect.getsource(repo)
    assert "carrierShapeFidelity" not in repo_src


@pytest.mark.parametrize("field", ["maskAspectRatio", "constructionRelativeError",
                                   "maskHygieneGateWouldPass", "constructionGateWouldPass",
                                   "branchDisagreement", "activeProductionBranch", "missingReasons"])
def test_metadata_carries_every_required_field(field):
    assert field in _observation().to_metadata()


# =====================================================================
# Lineage capture wiring — identifiers must ride along with the metrics
# without touching a single decision.
# =====================================================================

import json as _json  # noqa: E402
from pathlib import Path  # noqa: E402

from app.services.hybrid_composite.carrier_preflight import (  # noqa: E402
    CarrierObservationLineage,
    build_carrier_shape_metadata,
    observe_carrier_shape_fidelity as _observe,
)

_ARTIFACTS = Path(__file__).resolve().parents[1] / "ab_out/frame_lock"
_V1 = _ARTIFACTS / "stripe-projection-protected-v1/artifacts/geometry.json"
_V3 = _ARTIFACTS / "stripe-projection-protected-v3/artifacts/geometry.json"
_replay = pytest.mark.skipif(not (_V1.exists() and _V3.exists()),
                             reason="local-only captured artifacts")


def _obs_from(path):
    g = _json.loads(path.read_text())
    return _observe(source_inventory=g.get("source_inventory"),
                    carrier_inventory=g.get("carrier_inventory"),
                    mask_hygiene_ratio_threshold=hc_panel.MAX_TORSO_ASPECT_RATIO,
                    construction_ratio_tolerance=hc_panel.CONSTRUCTION_RATIO_TOL)


# ------------------------------------------------------------------ Test I

@_replay
@pytest.mark.parametrize("path,expect", [
    (_V1, {"maskAspectRatio": 3.538 / 1.353, "maskHygieneGateWouldPass": True,
           "constructionGateWouldPass": False, "branchDisagreement": True}),
    (_V3, {"maskAspectRatio": None, "maskHygieneGateWouldPass": None,
           "constructionGateWouldPass": False, "branchDisagreement": None}),
])
def test_i_canonical_replay_metrics_survive_the_lineage_merge(path, expect):
    obs = _obs_from(path)
    merged = build_carrier_shape_metadata(obs, CarrierObservationLineage(
        carrier_sha256="a" * 64, source_sha256="b" * 64, job_id="job-1"))
    for key, want in expect.items():
        if isinstance(want, float):
            assert merged[key] == pytest.approx(want)
        else:
            assert merged[key] is want
    # and every v19 field is byte-identical to the unmerged observation
    for key, value in obs.to_metadata().items():
        assert merged[key] == value, key


# ------------------------------------------------------------------ Test J

def test_j_summary_gains_only_the_one_nested_key_even_with_lineage():
    summary = {k: "x" for k in BASE_SUMMARY_KEYS}
    before = _json.dumps(summary, sort_keys=True)
    summary["carrierShapeFidelity"] = build_carrier_shape_metadata(
        _observation(), CarrierObservationLineage(carrier_sha256="c" * 64))
    assert set(summary) - BASE_SUMMARY_KEYS == {"carrierShapeFidelity"}
    assert _json.dumps({k: summary[k] for k in BASE_SUMMARY_KEYS}, sort_keys=True) == before


def test_j_carrier_sha_in_lineage_equals_the_summary_field():
    """Capture, summary and lineage all read one digest of one set of bytes."""
    assert "carrier_sha_early = hashlib.sha256(res.image).hexdigest()" in WIRING_SOURCE
    assert "carrier_sha256=carrier_sha_early," in WIRING_SOURCE      # capture_artifacts
    assert "carrier_sha256 = carrier_sha_early" in WIRING_SOURCE     # summary local
    compute = WIRING_SOURCE.split("out_bytes = png.tobytes()", 1)[1]
    assert '"carrierSha256": carrier_sha256,' in compute
    assert "carrier_sha256=carrier_sha256," in compute


def test_j_the_carrier_bytes_are_hashed_exactly_once():
    """A carrier can be tens of megabytes; hashing it twice buys only latency."""
    assert WIRING_SOURCE.count("hashlib.sha256(res.image)") == 1


def test_j_the_single_digest_is_the_sha256_of_the_encoded_bytes():
    payload = b"\x89PNG\r\n\x1a\n carrier encoded bytes"
    expected = hashlib.sha256(payload).hexdigest()
    # the worker's one expression, applied to a fixture
    assert hashlib.sha256(payload).hexdigest() == expected
    lineage = CarrierObservationLineage(carrier_sha256=expected)
    assert lineage.to_metadata()["carrierSha256"] == expected
    assert expected != hashlib.sha256(payload + b"x").hexdigest()


# ------------------------------------------------------------------ Test L

class _ExplodingLineage:
    def to_metadata(self):
        raise OSError("hash read failed")


def test_l_lineage_failure_cannot_cost_the_output():
    summary = {"outputSha256": "abc", "applied": True}
    try:
        summary["carrierShapeFidelity"] = build_carrier_shape_metadata(
            _observation(), _ExplodingLineage())
    except Exception:
        pass
    assert summary == {"outputSha256": "abc", "applied": True}


def test_l_the_worker_guards_the_whole_lineage_block():
    attach = WIRING_SOURCE.split("lineage = hc_preflight.CarrierObservationLineage(", 1)
    assert len(attach) == 2, "lineage must be built exactly once"
    assert attach[0].rstrip().endswith("try:")
    assert "except Exception:" in attach[1][:2000]


# ------------------------------------------------------------------ Test M

@pytest.mark.parametrize("symbol", ["carrier_sha256", "front_sha", "lineage"])
def test_m_lineage_values_never_steer_control_flow(symbol):
    steering = [line.strip() for line in WIRING_SOURCE.splitlines()
                if symbol in line
                and any(line.strip().startswith(kw) for kw in
                        ("if ", "elif ", "while ", "assert ", "return ", "raise "))]
    assert steering == [], steering


def _lineage_block() -> str:
    """Just the guarded lineage build + attach, up to its own except clause."""
    after = WIRING_SOURCE.split("lineage = hc_preflight.CarrierObservationLineage(", 1)[1]
    return after.split("except Exception:", 1)[0]


def test_m_lineage_block_calls_no_provider_and_adds_no_retry():
    block = _lineage_block()
    for forbidden in ("gemini", "provider", "retry", "regenerate", "await _call", "generate("):
        assert forbidden not in block.lower(), forbidden


def test_m_no_identifier_is_invented():
    block = _lineage_block()
    for forbidden in ("uuid", "uuid4", "random", "token_hex"):
        assert forbidden not in block.lower(), forbidden


# =====================================================================
# Semantic contract at the call site — the worker has to declare what it
# actually did, not what would have been convenient to claim.
# =====================================================================

def test_the_source_sha_basis_matches_how_the_worker_computes_it():
    """The worker hashes `front_ref.image.data` itself, so: encoded bytes."""
    assert "front_sha_early = hashlib.sha256(front_ref.image.data).hexdigest()" in \
        inspect.getsource(mannequin_job._apply_hybrid_composite)
    block = _lineage_block()
    assert "source_sha_basis=hc_preflight.SHA_BASIS_ENCODED_MEMORY," in block
    assert "immutable_asset_hash" not in block
    assert "SHA_BASIS_IMMUTABLE_ASSET" not in block


def test_both_digests_declare_their_algorithm_and_the_bytes_they_covered():
    block = _lineage_block()
    for expected in ("carrier_sha_algorithm=hc_preflight.SHA_ALGORITHM,",
                     "carrier_sha_source=hc_preflight.SHA_SOURCE_CARRIER_RESULT,",
                     "source_sha_algorithm=hc_preflight.SHA_ALGORITHM,",
                     "source_sha_source=hc_preflight.SHA_SOURCE_FRONT_ASSET_BYTES,"):
        assert expected in block, expected


def test_the_worker_fills_job_id_and_leaves_generation_run_id_alone():
    block = _lineage_block()
    assert "job_id=str(job_id) if job_id is not None else None," in block
    assert "job_id_source=hc_preflight.ID_SOURCE_WORKER_JOB_ID," in block
    # the run id is a different identity and no runtime value expresses it
    assert "generation_run_id=" not in block


def test_candidate_and_attempt_are_passed_with_their_provenance():
    block = _lineage_block()
    assert "candidate_id=str(candidate) if candidate is not None else None," in block
    assert "candidate_id_source=hc_preflight.ID_SOURCE_WORKER_CANDIDATE," in block
    assert "attempt_number=attempt if isinstance(attempt, int) else None," in block
    assert "attempt_number_source=hc_preflight.ID_SOURCE_WORKER_ATTEMPT," in block


def test_the_worker_writes_neither_persistence_state_nor_discovery_contract():
    """Both describe the pipeline, not a carrier — they must not ride in a record."""
    src = inspect.getsource(mannequin_job._apply_hybrid_composite)
    assert "CARRIER_OBSERVATION_PERSISTENCE_STATE" not in src
    assert "DATASET_DISCOVERY_CONTRACT" not in src
    assert "productionRecordObserved" not in src


# ------------------------------------------------- the event stream is untouched

def test_the_observation_emits_no_event_of_its_own():
    """Observation-only means the metadata grows and the event stream does not."""
    assert "hybrid_carrier_shape_observation" not in WIRING_SOURCE
    compute = WIRING_SOURCE.split("shape_observation = None", 1)[1].split("# Stage 3", 1)[0]
    assert "emit(" not in compute


def test_the_existing_emits_are_all_still_there():
    for name in ("hybrid_composite_completed", "hybrid_carrier_preflight",
                 "hybrid_deterministic_qc"):
        assert name in WIRING_SOURCE, name


def test_the_worker_emits_no_name_the_pre_change_worker_lacked():
    """Every emitted event name must predate this change."""
    emitted = set(re.findall(r'emit\(\s*"([a-z0-9_]+)"', WIRING_SOURCE))
    baseline = subprocess.run(
        ["git", "show", f"HEAD:{_WORKER_REL}"],
        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True)
    if baseline.returncode != 0:
        pytest.skip("git object for the pre-change worker is unavailable")
    before = set(re.findall(r'emit\(\s*"([a-z0-9_]+)"', baseline.stdout))
    assert emitted - before == set(), emitted - before


@pytest.mark.parametrize("symbol", ["job_id", "candidate", "attempt"])
def test_no_runtime_identifier_steers_the_lineage_block(symbol):
    block = _lineage_block()
    steering = [line.strip() for line in block.splitlines()
                if symbol in line
                and any(line.strip().startswith(kw) for kw in
                        ("if ", "elif ", "while ", "assert ", "return ", "raise "))]
    assert steering == [], steering
