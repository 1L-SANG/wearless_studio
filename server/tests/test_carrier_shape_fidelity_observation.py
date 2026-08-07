"""Tests for the carrier shape-fidelity observation.

The observation exists because `build_panel_map` never evaluates both shape
readings: whichever branch fires first returns early. These tests pin that the
helper records both readings, flags the disagreement, and decides nothing.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from app.services.hybrid_composite import panel_map as hc_panel
from app.services.hybrid_composite.carrier_preflight import (
    BRANCH_CONSTRUCTION,
    BRANCH_MASK,
    BRANCH_REPEAT_INVARIANT_SKIP,
    BRANCH_UNAVAILABLE,
    CarrierShapeFidelityObservation,
    observe_carrier_shape_fidelity,
)

MASK_TOL = hc_panel.MAX_TORSO_ASPECT_RATIO
CON_TOL = hc_panel.CONSTRUCTION_RATIO_TOL

ARTIFACTS = Path(__file__).resolve().parents[1] / "ab_out/frame_lock"
V1_GEOMETRY = ARTIFACTS / "stripe-projection-protected-v1/artifacts/geometry.json"
V3_GEOMETRY = ARTIFACTS / "stripe-projection-protected-v3/artifacts/geometry.json"


def observe(src, car, **kw):
    return observe_carrier_shape_fidelity(
        source_inventory=src, carrier_inventory=car,
        mask_hygiene_ratio_threshold=MASK_TOL, construction_ratio_tolerance=CON_TOL, **kw)


# ------------------------------------------------------------------ Test A

def test_a_observation_is_pure_and_decides_nothing():
    src = {"torso_aspect_mask": 1.353, "torso_aspect": 1.241, "sleeve_len_ratio": 1.545}
    car = {"torso_aspect_mask": 3.538, "torso_aspect": 2.445, "sleeve_len_ratio": 1.183}
    before = (json.dumps(src, sort_keys=True), json.dumps(car, sort_keys=True))
    obs = observe(src, car)
    assert isinstance(obs, CarrierShapeFidelityObservation)
    assert obs.observation_only is True
    # the helper must not mutate its inputs, and it returns no verdict field
    assert (json.dumps(src, sort_keys=True), json.dumps(car, sort_keys=True)) == before
    assert not hasattr(obs, "decision")
    assert not hasattr(obs, "accepted")


# ------------------------------------------------------------------ Test B

def test_b_mask_passes_construction_fails_is_a_disagreement():
    obs = observe({"torso_aspect_mask": 1.353, "torso_aspect": 1.241},
                  {"torso_aspect_mask": 3.538, "torso_aspect": 2.445})
    assert obs.mask_hygiene_gate_would_pass is True
    assert obs.construction_gate_would_pass is False
    assert obs.branch_disagreement is True
    assert obs.active_production_branch == BRANCH_MASK


# ------------------------------------------------------------------ Test C

def test_c_both_pass_is_not_a_disagreement():
    obs = observe({"torso_aspect_mask": 1.30, "torso_aspect": 1.30},
                  {"torso_aspect_mask": 1.40, "torso_aspect": 1.40})
    assert obs.mask_hygiene_gate_would_pass is True
    assert obs.construction_gate_would_pass is True
    assert obs.branch_disagreement is False


# ------------------------------------------------------------------ Test D

def test_d_both_fail_is_not_a_disagreement():
    obs = observe({"torso_aspect_mask": 1.0, "torso_aspect": 1.0},
                  {"torso_aspect_mask": 9.0, "torso_aspect": 9.0})
    assert obs.mask_hygiene_gate_would_pass is False
    assert obs.construction_gate_would_pass is False
    assert obs.branch_disagreement is False


# ------------------------------------------------------------------ Test E

def test_e_missing_mask_data_leaves_construction_intact():
    obs = observe({"torso_aspect": 1.241}, {"torso_aspect": 2.445})
    assert obs.mask_aspect_ratio is None
    assert obs.mask_hygiene_gate_would_pass is None
    assert obs.branch_disagreement is None
    assert "torso_aspect_mask_unavailable" in obs.missing_reasons
    assert obs.construction_relative_error == pytest.approx(abs(2.445 - 1.241) / 1.241)
    assert obs.construction_gate_would_pass is False
    assert obs.active_production_branch == BRANCH_CONSTRUCTION


def test_e_repeat_invariant_blanking_is_reported_as_its_own_branch():
    """The worker blanks both mask values when the stripe repeat invariant held."""
    obs = observe({"torso_aspect_mask": None, "torso_aspect": 1.241},
                  {"torso_aspect_mask": None, "torso_aspect": 2.445})
    assert obs.active_production_branch == BRANCH_REPEAT_INVARIANT_SKIP
    assert obs.mask_aspect_ratio is None
    # the raw measurements still survive when the caller passes them in
    obs2 = observe({"torso_aspect_mask": None}, {"torso_aspect_mask": None},
                   source_torso_aspect_mask_measured=1.353,
                   carrier_torso_aspect_mask_measured=3.538)
    assert obs2.source_torso_aspect_mask == 1.353
    assert obs2.mask_aspect_ratio == pytest.approx(3.538 / 1.353)
    assert obs2.active_production_branch == BRANCH_REPEAT_INVARIANT_SKIP


# ------------------------------------------------------------------ Test F

def test_f_missing_construction_data_leaves_mask_intact():
    obs = observe({"torso_aspect_mask": 1.353}, {"torso_aspect_mask": 3.538})
    assert obs.construction_relative_error is None
    assert obs.construction_gate_would_pass is None
    assert obs.branch_disagreement is None
    assert "torso_aspect_construction_unavailable" in obs.missing_reasons
    assert obs.mask_aspect_ratio == pytest.approx(3.538 / 1.353)
    assert obs.mask_hygiene_gate_would_pass is True
    assert obs.active_production_branch == BRANCH_MASK


# ------------------------------------------------------------------ Test G

@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), "1.3", None, True])
def test_g_invalid_numeric_input_fails_closed_to_null(bad):
    obs = observe({"torso_aspect_mask": bad, "torso_aspect": bad},
                  {"torso_aspect_mask": 3.538, "torso_aspect": 2.445})
    assert obs.mask_aspect_ratio is None
    assert obs.construction_relative_error is None
    assert obs.mask_hygiene_gate_would_pass is None
    assert obs.branch_disagreement is None
    assert obs.missing_reasons


def test_g_zero_denominator_is_null_not_infinity():
    obs = observe({"torso_aspect_mask": 0.0, "torso_aspect": 0.0},
                  {"torso_aspect_mask": 3.538, "torso_aspect": 2.445})
    assert obs.mask_aspect_ratio is None
    assert obs.construction_relative_error is None
    assert "torso_aspect_mask_non_positive" in obs.missing_reasons
    assert "torso_aspect_construction_non_positive" in obs.missing_reasons


def test_g_empty_inventories_yield_unavailable_branch():
    obs = observe(None, None)
    assert obs.active_production_branch == BRANCH_UNAVAILABLE
    assert obs.branch_disagreement is None
    assert len(obs.missing_reasons) == 3


# ------------------------------------------------------------------ Test H

def test_h_metadata_is_json_serialisable_and_finite():
    obs = observe({"torso_aspect_mask": 1.353, "torso_aspect": 1.241, "sleeve_len_ratio": 1.545},
                  {"torso_aspect_mask": 3.538, "torso_aspect": 2.445, "sleeve_len_ratio": 1.183})
    md = obs.to_metadata()
    text = json.dumps(md)                       # raises if anything is unserialisable
    assert "NaN" not in text and "Infinity" not in text
    for value in md.values():
        if isinstance(value, float):
            assert math.isfinite(value)
    assert md["observationOnly"] is True
    assert md["branchDisagreement"] is True
    assert set(md) >= {"activeProductionBranch", "maskAspectRatio", "constructionRelativeError",
                       "branchDisagreement", "missingReasons"}


def test_h_null_observation_still_serialises():
    json.dumps(observe(None, None).to_metadata())


# --------------------------------------------------- sleeve length (spec D)

def test_sleeve_length_uses_the_existing_relative_error_definition():
    obs = observe({"sleeve_len_ratio": 1.545}, {"sleeve_len_ratio": 1.183})
    assert obs.sleeve_length_relative_error == pytest.approx(abs(1.183 - 1.545) / 1.545)
    assert observe({}, {"sleeve_len_ratio": 1.183}).sleeve_length_relative_error is None


# ------------------------------------------------- Test J / K canonical replay

_replay = pytest.mark.skipif(
    not (V1_GEOMETRY.exists() and V3_GEOMETRY.exists()),
    reason="local-only captured artifacts (server/ab_out is gitignored)")


@_replay
def test_j_canonical_replay_protected_v1_mask_passes_construction_fails():
    g = json.loads(V1_GEOMETRY.read_text())
    obs = observe(g["source_inventory"], g["carrier_inventory"])
    assert obs.source_torso_aspect_mask == 1.353
    assert obs.carrier_torso_aspect_mask == 3.538
    assert obs.mask_aspect_ratio == pytest.approx(3.538 / 1.353)
    assert obs.mask_hygiene_gate_would_pass is True          # 2.615 <= 3.0
    assert obs.construction_relative_error == pytest.approx(abs(2.445 - 1.241) / 1.241)
    assert obs.construction_gate_would_pass is False         # 0.970 > 0.40
    assert obs.branch_disagreement is True
    assert obs.active_production_branch == BRANCH_MASK


@_replay
def test_k_canonical_replay_protected_v3_reproduces_the_known_error():
    g = json.loads(V3_GEOMETRY.read_text())
    obs = observe(g["source_inventory"], g["carrier_inventory"])
    assert obs.construction_relative_error == pytest.approx(0.829, abs=5e-4)
    assert obs.construction_gate_would_pass is False
    assert obs.mask_aspect_ratio is None                      # v3 carries no mask pair
    assert obs.branch_disagreement is None
    assert obs.active_production_branch == BRANCH_CONSTRUCTION


# ------------------------------------------------------------------ Test L

def test_l_thresholds_come_from_production_and_are_never_redefined():
    """The helper must hold no copy of either constant."""
    import inspect

    from app.services.hybrid_composite import carrier_preflight as cp

    src = inspect.getsource(cp.observe_carrier_shape_fidelity)
    assert "3.0" not in src and "0.40" not in src and "0.4" not in src
    assert not hasattr(cp, "MAX_TORSO_ASPECT_RATIO")
    assert not hasattr(cp, "CONSTRUCTION_RATIO_TOL")
    # and the gates replicate production exactly at the boundary
    boundary = observe({"torso_aspect_mask": 1.0}, {"torso_aspect_mask": MASK_TOL})
    assert boundary.mask_hygiene_gate_would_pass is True
    over = observe({"torso_aspect_mask": 1.0}, {"torso_aspect_mask": MASK_TOL + 0.001})
    assert over.mask_hygiene_gate_would_pass is False


# =====================================================================
# Lineage capture — carrier/source SHA and generation identifiers.
# The observation is only useful later if each carrier can be deduplicated
# and joined back to its source product and run.
# =====================================================================

import hashlib  # noqa: E402
import inspect  # noqa: E402

from app.services.hybrid_composite.carrier_preflight import (  # noqa: E402
    CARRIER_OBSERVATION_LINEAGE_VERSION,
    DATASET_DISCOVERY_CONTRACT,
    REASON_CANDIDATE_ID_UNAVAILABLE,
    REASON_CARRIER_SHA_UNAVAILABLE,
    REASON_GENERATION_RUN_ID_UNAVAILABLE,
    REASON_JOB_ID_UNAVAILABLE,
    REASON_SOURCE_SHA_UNAVAILABLE,
    REPRESENTATION_REASONS,
    SHA_ALGORITHM,
    SHA_BASIS_ENCODED_MEMORY,
    SHA_BASIS_IMMUTABLE_ASSET,
    CarrierObservationLineage,
    build_carrier_shape_metadata,
)

CARRIER_FIXTURE = ARTIFACTS / "stripe-projection-protected-v1/artifacts/carrier.png"
CARRIER_COPY = ARTIFACTS / "stripe-projection-demo/carrier.png"
OTHER_CARRIER = ARTIFACTS / "stripe-projection-protected-v3/artifacts/carrier.png"


def _file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


_carriers = pytest.mark.skipif(
    not (CARRIER_FIXTURE.exists() and CARRIER_COPY.exists() and OTHER_CARRIER.exists()),
    reason="local-only captured carriers (server/ab_out is gitignored)")


# ------------------------------------------------------------------ Test A

@_carriers
def test_a_carrier_sha_matches_a_direct_hash_of_the_file_bytes():
    expected = _file_sha(CARRIER_FIXTURE)
    lineage = CarrierObservationLineage(carrier_sha256=expected,
                                        carrier_sha_basis=SHA_BASIS_ENCODED_MEMORY)
    assert lineage.to_metadata()["carrierSha256"] == expected
    assert len(expected) == 64


# ------------------------------------------------------------------ Test B

@_carriers
def test_b_the_same_carrier_at_two_paths_hashes_identically():
    assert _file_sha(CARRIER_FIXTURE) == _file_sha(CARRIER_COPY)


# ------------------------------------------------------------------ Test C

@_carriers
def test_c_a_different_carrier_hashes_differently():
    assert _file_sha(CARRIER_FIXTURE) != _file_sha(OTHER_CARRIER)


def test_c_one_flipped_byte_changes_the_hash(tmp_path):
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(b"\x89PNG carrier payload")
    b.write_bytes(b"\x89PNG carrier payloae")
    assert _file_sha(a) != _file_sha(b)


# ------------------------------------------------------------------ Test D

def test_d_source_sha_is_carried_through_verbatim():
    digest = hashlib.sha256(b"source asset bytes").hexdigest()
    md = CarrierObservationLineage(source_sha256=digest,
                                   source_sha_algorithm=SHA_ALGORITHM,
                                   source_sha_basis=SHA_BASIS_ENCODED_MEMORY).to_metadata()
    assert md["sourceSha256"] == digest
    assert md["sourceShaAlgorithm"] == SHA_ALGORITHM
    assert md["sourceShaBasis"] == SHA_BASIS_ENCODED_MEMORY


# ------------------------------------------------------------------ Test E

@_carriers
def test_e_encoded_bytes_hash_is_not_the_decoded_pixel_hash():
    """A decoded array hashes differently; the contract is encoded bytes."""
    import cv2

    encoded = _file_sha(CARRIER_FIXTURE)
    decoded = cv2.imread(str(CARRIER_FIXTURE), cv2.IMREAD_COLOR)
    pixel_hash = hashlib.sha256(decoded.tobytes()).hexdigest()
    assert encoded != pixel_hash
    md = CarrierObservationLineage(carrier_sha256=encoded,
                                   carrier_sha_basis=SHA_BASIS_ENCODED_MEMORY).to_metadata()
    assert md["carrierSha256"] == encoded
    assert md["carrierShaBasis"] in (SHA_BASIS_ENCODED_MEMORY, "encoded_file_bytes",
                                     "immutable_asset_hash")


# ------------------------------------------------------------------ Test F

def test_f_missing_carrier_source_is_null_plus_reason():
    md = CarrierObservationLineage().to_metadata()
    assert md["carrierSha256"] is None
    assert md["sourceSha256"] is None
    assert REASON_CARRIER_SHA_UNAVAILABLE in md["lineageMissingReasons"]
    assert REASON_SOURCE_SHA_UNAVAILABLE in md["lineageMissingReasons"]


# ------------------------------------------------------------------ Test G

def test_g_existing_generation_identifiers_pass_through_unchanged():
    md = CarrierObservationLineage(job_id="job-77", candidate_id="2",
                                   attempt_number=3, source_asset_id="asset-9",
                                   source_product_truth_id="truth-4").to_metadata()
    assert md["jobId"] == "job-77"
    assert md["candidateId"] == "2"
    assert md["attemptNumber"] == 3
    assert md["sourceAssetId"] == "asset-9"
    assert md["sourceProductTruthId"] == "truth-4"
    assert md["lineageVersion"] == CARRIER_OBSERVATION_LINEAGE_VERSION


# ------------------------------------------------------------------ Test H

def test_h_missing_ids_are_null_and_never_invented():
    md = CarrierObservationLineage(carrier_sha256="a" * 64, source_sha256="b" * 64).to_metadata()
    assert md["jobId"] is None
    assert md["generationRunId"] is None
    assert md["candidateId"] is None
    assert REASON_JOB_ID_UNAVAILABLE in md["lineageMissingReasons"]
    assert REASON_GENERATION_RUN_ID_UNAVAILABLE in md["lineageMissingReasons"]
    assert REASON_CANDIDATE_ID_UNAVAILABLE in md["lineageMissingReasons"]
    # no uuid-shaped filler anywhere
    assert not any(isinstance(v, str) and len(v) == 36 and v.count("-") == 4 for v in md.values())


# =====================================================================
# Semantic contract — the metadata has to mean what it says.
# =====================================================================

# ------------------------------------------------------- Test A / B: SHA basis

def test_a_hashing_bytes_ourselves_is_always_an_encoded_bytes_basis():
    """`front_ref.image.data` is hashed here, so the basis is encoded bytes."""
    payload = b"\x89PNG front asset bytes"
    digest = hashlib.sha256(payload).hexdigest()
    md = CarrierObservationLineage(
        source_sha256=digest,
        source_sha_algorithm=SHA_ALGORITHM,
        source_sha_basis=SHA_BASIS_ENCODED_MEMORY,
        source_sha_source="front_ref.image.data",
        source_asset_id="asset-9").to_metadata()
    assert md["sourceShaBasis"] == SHA_BASIS_ENCODED_MEMORY
    assert md["sourceShaSource"] == "front_ref.image.data"
    # an asset id says where the bytes came from; it is not a stored digest
    assert md["sourceAssetId"] == "asset-9"
    assert md["sourceShaBasis"] != SHA_BASIS_IMMUTABLE_ASSET


def test_b_no_production_caller_claims_an_immutable_asset_hash():
    """Reserved for a store that hands us a digest. No such store exists yet."""
    from app.services.hybrid_composite import carrier_preflight
    from app.workers import mannequin_job

    worker_src = inspect.getsource(mannequin_job._apply_hybrid_composite)
    assert "SHA_BASIS_IMMUTABLE_ASSET" not in worker_src
    assert "immutable_asset_hash" not in worker_src
    # the vocabulary itself stays, but only as a reserved constant + its contract
    pre_src = inspect.getsource(carrier_preflight)
    assert pre_src.count('"immutable_asset_hash"') == 1


# ------------------------------------------------- Test C / D / E: run identity

def test_c_job_id_and_generation_run_id_are_separate_identities():
    md = CarrierObservationLineage(job_id="job-example").to_metadata()
    assert md["jobId"] == "job-example"
    assert md["generationRunId"] is None


def test_d_a_missing_generation_run_says_so():
    md = CarrierObservationLineage(job_id="job-example").to_metadata()
    assert REASON_GENERATION_RUN_ID_UNAVAILABLE in md["lineageMissingReasons"]
    assert REASON_JOB_ID_UNAVAILABLE not in md["lineageMissingReasons"]


def test_e_a_job_id_never_leaks_into_the_run_field():
    md = CarrierObservationLineage(job_id="job-42", candidate_id="1").to_metadata()
    assert md["generationRunId"] != md["jobId"]
    assert md["generationRunId"] != md["candidateId"]
    assert md["generationRunId"] is None


def test_e_provenance_names_the_parameter_each_id_came_from():
    md = CarrierObservationLineage(
        job_id="job-1", job_id_source="worker_parameter:job_id",
        candidate_id="0", candidate_id_source="worker_parameter:candidate",
        attempt_number=1, attempt_number_source="worker_parameter:attempt").to_metadata()
    assert md["jobIdSource"] == "worker_parameter:job_id"
    assert md["candidateIdSource"] == "worker_parameter:candidate"
    assert md["attemptNumberSource"] == "worker_parameter:attempt"


# ------------------------------------------- Test F: candidate / attempt fidelity

@pytest.mark.parametrize("candidate,attempt", [("0", 1), ("3", 0), (None, None)])
def test_f_candidate_and_attempt_are_passed_through_exactly(candidate, attempt):
    md = CarrierObservationLineage(candidate_id=candidate, attempt_number=attempt).to_metadata()
    assert md["candidateId"] == candidate
    assert md["attemptNumber"] == attempt


# --------------------------------------------------------- Test G: persistence

def test_g_operational_state_is_not_frozen_into_the_runtime():
    """Deploy state and record existence are true at a moment, not in a module.

    A constant saying `deploymentPerformed: False` keeps saying it after the
    deploy, so the distinction belongs in the diagnostic artifacts instead.
    """
    from app.services.hybrid_composite import carrier_preflight

    assert not hasattr(carrier_preflight, "CARRIER_OBSERVATION_PERSISTENCE_STATE")
    src = inspect.getsource(carrier_preflight)
    for stale in ("persistencePathImplemented", "serializationVerified",
                  "productionRecordObserved", "deploymentPerformed"):
        assert stale not in src, stale


def test_g_a_record_carries_no_operational_state():
    md = build_carrier_shape_metadata(
        observe({"torso_aspect_mask": 1.35}, {"torso_aspect_mask": 3.5}),
        CarrierObservationLineage(carrier_sha256="f" * 64, job_id="job-x"))
    for stale in ("persistencePathImplemented", "serializationVerified",
                  "productionRecordObserved", "deploymentPerformed"):
        assert stale not in md, stale


# ---------------------------------------------------- Test H: dataset discovery

def test_h_dataset_discovery_points_at_the_production_record():
    contract = DATASET_DISCOVERY_CONTRACT
    assert contract["primary"] == "qc_scores.hybridComposite.carrierShapeFidelity"
    assert contract["secondary"] == "cut metadata hybridComposite.carrierShapeFidelity"
    assert contract["diagnosticSamplesJsonlIsAuthoritative"] is False


# ------------------------------------------ representation missingness per field

@pytest.mark.parametrize("field,reason", sorted(REPRESENTATION_REASONS.items()))
def test_each_absent_representation_field_reports_its_own_reason(field, reason):
    md = CarrierObservationLineage().to_metadata()
    assert reason in md["lineageMissingReasons"], field


@pytest.mark.parametrize("field,reason", sorted(REPRESENTATION_REASONS.items()))
def test_a_present_representation_field_drops_only_its_own_reason(field, reason):
    value = 1 if field == "attempt_number" else "present"
    md = CarrierObservationLineage(**{field: value}).to_metadata()
    assert reason not in md["lineageMissingReasons"]
    others = {r for f, r in REPRESENTATION_REASONS.items() if f != field}
    assert others <= set(md["lineageMissingReasons"])


def test_one_populated_field_does_not_hide_the_rest():
    """The old all-or-nothing check went silent as soon as anything was set."""
    md = CarrierObservationLineage(pattern_type="vertical_stripe").to_metadata()
    assert md["patternType"] == "vertical_stripe"
    assert md["garmentCategory"] is None
    for reason in ("garment_category_unavailable", "garment_lane_unavailable",
                   "frame_intent_unavailable", "source_type_unavailable"):
        assert reason in md["lineageMissingReasons"], reason
    assert "pattern_type_unavailable" not in md["lineageMissingReasons"]


def test_hash_and_id_reasons_survive_the_representation_change():
    md = CarrierObservationLineage(pattern_type="vertical_stripe").to_metadata()
    for reason in (REASON_CARRIER_SHA_UNAVAILABLE, REASON_SOURCE_SHA_UNAVAILABLE,
                   REASON_JOB_ID_UNAVAILABLE, REASON_GENERATION_RUN_ID_UNAVAILABLE,
                   REASON_CANDIDATE_ID_UNAVAILABLE):
        assert reason in md["lineageMissingReasons"], reason


def test_a_fully_populated_lineage_reports_nothing_missing():
    md = CarrierObservationLineage(
        carrier_sha256="a" * 64, source_sha256="b" * 64, job_id="job-1",
        generation_run_id="run-1", candidate_id="0", source_asset_id="asset-1",
        source_product_truth_id="truth-1", garment_category="top",
        garment_lane="upper", frame_intent="front", pattern_type="stripe",
        source_type="studio", image_resolution="100x200",
        generation_mode="enforce").to_metadata()
    assert md["lineageMissingReasons"] == []


# ------------------------------------------------------------------ Test K

def test_k_merged_metadata_serialises_without_nan_or_infinity():
    obs = observe({"torso_aspect_mask": 1.353, "torso_aspect": 1.241, "sleeve_len_ratio": 1.545},
                  {"torso_aspect_mask": 3.538, "torso_aspect": 2.445, "sleeve_len_ratio": 1.183})
    md = build_carrier_shape_metadata(obs, CarrierObservationLineage(
        carrier_sha256="c" * 64, source_sha256="d" * 64, generation_run_id="job-1"))
    text = json.dumps(md)
    assert "NaN" not in text and "Infinity" not in text
    for v in md.values():
        if isinstance(v, float):
            assert math.isfinite(v)


def test_k_merge_keeps_every_v19_metric_name():
    obs = observe({"torso_aspect_mask": 1.353, "torso_aspect": 1.241},
                  {"torso_aspect_mask": 3.538, "torso_aspect": 2.445})
    merged = build_carrier_shape_metadata(obs, CarrierObservationLineage())
    for key, value in obs.to_metadata().items():
        assert merged[key] == value, key


def test_k_merge_survives_a_missing_half():
    only_lineage = build_carrier_shape_metadata(None, CarrierObservationLineage(carrier_sha256="e" * 64))
    assert only_lineage["carrierSha256"] == "e" * 64
    assert only_lineage["metricMissingReasons"] == ["observation_unavailable"]
    only_obs = build_carrier_shape_metadata(observe(None, None), None)
    assert "carrierSha256" not in only_obs
    assert only_obs["lineageMissingReasons"]
