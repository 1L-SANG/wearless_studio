"""One source reading per run, shared by every candidate.

Production logged sourcePeriodPx 30.0 for candidate A and 15.0 for candidate B
from the identical source SHA in one job. The source garment did not change; the
worker re-read it per candidate, and two Vision landmark calls on the same photo
disagree enough to move the source torso ROI, which moves the measured period.

These tests pin the reading to the run, not the candidate. They deliberately do
NOT assert anything about which period is correct — that is a later patch.
"""
from __future__ import annotations

import ast
import asyncio
import inspect

import pytest

from app.services.hybrid_composite import source_texture_context as stc
from app.workers import mannequin_job

WORKER_SRC = inspect.getsource(mannequin_job)
HYBRID_SRC = inspect.getsource(mannequin_job._apply_hybrid_composite)


def _cache():
    return stc.SourceTextureContextCache()


def _fill(cache, *, period=20.649, roi=(750, 1080, 2205, 2680), axis="vertical",
          source_sha="f91cbac5" + "0" * 56, conf=0.8238):
    cache.record_source(source_sha256=source_sha, source_landmarks={"shoulder_l": [0.31, 0.28]},
                        source_inventory={"torso_aspect": 1.239},
                        source_component_boxes_norm={}, pattern_model_slot="Detail",
                        pattern_model_asset_id="005fed36", pattern_model_sha256="cb5913a8",
                        pattern_model_roi=(1080, 800, 1400, 1120), detail_validation_ok=True)
    cache.record_period(source_torso_roi=roi, garment_axis=axis, source_period_px=period,
                        source_period_source=stc.PERIOD_FROM_FRONT_SCAN,
                        source_model_confidence=conf, torso_span_px=float(roi[2] - roi[0]))
    return cache


# ------------------------------------------------------- A: computed once

def test_a_a_slot_is_computed_once_per_run():
    cache = _cache()
    calls = []

    async def compute():
        calls.append(1)
        return {"front_model": "m", "guided_anchor": None, "source_torso_roi": (0, 0, 10, 10)}

    async def main():
        for _ in range(5):
            await cache.get(stc.SLOT_SOURCE_PERIOD, compute)

    asyncio.run(main())
    assert len(calls) == 1
    assert cache.compute_counts[stc.SLOT_SOURCE_PERIOD] == 1
    assert cache.reuse_counts[stc.SLOT_SOURCE_PERIOD] == 4
    assert cache.compute_count == 1


def test_a_a_failed_reading_is_not_re_rolled():
    """Re-measuring after a failure is the drift this patch removes."""
    cache = _cache()
    calls = []

    async def compute():
        calls.append(1)
        return {"failure": "기하 추출 실패: VisionError"}

    async def main():
        first, reused_1 = await cache.get(stc.SLOT_SOURCE_GEOMETRY, compute)
        second, reused_2 = await cache.get(stc.SLOT_SOURCE_GEOMETRY, compute)
        return first, second, reused_1, reused_2

    first, second, r1, r2 = asyncio.run(main())
    assert len(calls) == 1
    assert first == second and first["failure"]
    assert (r1, r2) == (False, True)


# --------------------------------------- B/C: candidates and retries reuse

def test_b_candidates_and_retries_share_one_context():
    cache = _fill(_cache())
    seen = [cache.context() for _ in range(3)]     # A, B, retry
    ids = {c.context_id() for c in seen}
    assert len(ids) == 1, "every candidate must read the same context id"
    assert len({c.source_sha256 for c in seen}) == 1
    assert len({c.source_torso_roi for c in seen}) == 1
    assert len({c.source_period_px for c in seen}) == 1
    assert len({c.source_period_source for c in seen}) == 1


def test_b_the_production_divergence_cannot_recur_through_the_cache():
    """30.0 then 15.0 from one source: the second write must not take effect."""
    cache = _fill(_cache(), period=30.0)
    cache.record_period(source_torso_roi=(700, 1000, 2200, 2600), garment_axis="vertical",
                        source_period_px=15.0, source_period_source=stc.PERIOD_FROM_GUIDED,
                        source_model_confidence=0.654, torso_span_px=1500.0)
    ctx = cache.context()
    assert ctx.source_period_px == 30.0, "first reading wins"
    assert cache.period_disagreements == 1, "and the disagreement is counted, not hidden"


def test_c_context_id_changes_when_the_source_changes():
    a = _fill(_cache()).context()
    b = _fill(_cache(), source_sha="a" * 64).context()
    assert a.context_id() != b.context_id()


def test_c_context_id_is_stable_across_equal_readings():
    assert _fill(_cache()).context().context_id() == _fill(_cache()).context().context_id()


# ------------------------------------------------------- D: immutability

def test_d_context_is_frozen():
    ctx = _fill(_cache()).context()
    with pytest.raises(Exception):
        ctx.source_period_px = 15.0


def test_d_nested_mappings_cannot_be_mutated_by_a_candidate():
    ctx = _fill(_cache()).context()
    with pytest.raises(TypeError):
        ctx.source_landmarks["shoulder_l"] = [0.0, 0.0]
    with pytest.raises(TypeError):
        ctx.source_inventory["torso_aspect"] = 99.0


def test_d_a_later_edit_to_the_recorded_dict_cannot_reach_the_context():
    """The worker keeps editing what it handed over — `src_inv` loses its component
    boxes to a `.pop()` on the next line, so the record must be a snapshot."""
    cache = _cache()
    live_landmarks = {"shoulder_l": [0.31, 0.28]}
    live_inventory = {"torso_aspect": 1.239, "component_boxes": {"collar_box": [[0, 0]]}}
    cache.record_source(source_sha256="f" * 64, source_landmarks=live_landmarks,
                        source_inventory=live_inventory,
                        source_component_boxes_norm=live_inventory["component_boxes"],
                        pattern_model_slot="Detail", pattern_model_asset_id="x",
                        pattern_model_sha256="y", pattern_model_roi=(1, 2, 3, 4),
                        detail_validation_ok=True)
    cache.record_period(source_torso_roi=(0, 0, 10, 10), garment_axis="vertical",
                        source_period_px=20.649,
                        source_period_source=stc.PERIOD_FROM_FRONT_SCAN,
                        source_model_confidence=0.82, torso_span_px=10.0)
    before = cache.context()
    live_landmarks["shoulder_l"] = [9.9, 9.9]          # what the worker does next
    live_inventory.pop("component_boxes")
    after = cache.context()
    assert after.source_landmarks["shoulder_l"] == (0.31, 0.28)
    assert "component_boxes" in after.source_inventory
    assert after.context_id() == before.context_id()


def test_d_context_survives_candidate_processing_unchanged():
    cache = _fill(_cache())
    before = cache.context()
    snapshot = (before.context_id(), before.source_period_px, tuple(before.source_torso_roi))
    for _ in range(2):                       # candidate A, then B
        ctx = cache.context()
        _ = ctx.to_metadata()
    after = cache.context()
    assert (after.context_id(), after.source_period_px,
            tuple(after.source_torso_roi)) == snapshot


# ------------------------------- E: candidate-specific geometry stays separate

def test_e_no_candidate_specific_value_is_stored_on_the_context():
    fields = set(stc.SourceTextureContext.__dataclass_fields__)
    for leaked in ("carrier_landmarks", "carrier_inventory", "target_span_px",
                   "target_period_px", "carrier_sha256", "projection_plan",
                   "candidate", "attempt"):
        assert leaked not in fields, leaked


def test_e_target_geometry_is_still_computed_per_candidate():
    """carrier span and target period must stay outside the memo."""
    for expr in ("t_torso_span = hc_scale.carrier_torso_span(",
                 "target_period_px = hc_scale.target_period_px("):
        assert expr in HYBRID_SRC, expr
    memo = HYBRID_SRC.split("async def _read_source_period():", 1)[1].split("src_period,", 1)[0]
    for leaked in ("carrier_torso_span", "target_period_px", "car_lm", "carrier_bgr"):
        assert leaked not in memo, leaked


# ------------------------------------------- wiring: production passes the cache

def test_the_run_creates_exactly_one_cache():
    assert WORKER_SRC.count("hc_source_ctx.SourceTextureContextCache()") == 2, \
        "one for the run, one private fallback for direct callers"
    run_src = inspect.getsource(mannequin_job.run_mannequin_job)
    assert run_src.count("SourceTextureContextCache()") == 1


def test_both_hybrid_call_sites_pass_the_run_cache():
    cand_src = inspect.getsource(mannequin_job._run_candidate)
    assert cand_src.count("source_texture_cache=source_texture_cache") == 2, \
        "main and salvage paths must share the run cache"
    run_src = inspect.getsource(mannequin_job.run_mannequin_job)
    assert "source_texture_cache=source_texture_cache" in run_src


def test_the_production_path_never_silently_recomputes():
    """The fallback exists for direct callers only; production always supplies one."""
    tree = ast.parse(inspect.getsource(mannequin_job._run_candidate).lstrip())
    passes = [n for n in ast.walk(tree) if isinstance(n, ast.keyword)
              and n.arg == "source_texture_cache"]
    assert len(passes) == 2
    for kw in passes:
        assert isinstance(kw.value, ast.Name) and kw.value.id == "source_texture_cache"


def test_the_source_landmark_pair_is_read_inside_the_memo():
    memo = HYBRID_SRC.split("async def _read_source_geometry():", 1)[1] \
                     .split("src_geometry, src_geometry_reused", 1)[0]
    assert memo.count("hybrid_landmarks.extract_geometry(s, front_ref.image)") == 2
    # and nowhere else in the hybrid stage
    assert HYBRID_SRC.count("extract_geometry(s, front_ref.image)") == 2


def test_the_carrier_landmark_pair_is_still_per_candidate():
    assert HYBRID_SRC.count("extract_geometry(s, car_img)") == 2
    memo = HYBRID_SRC.split("async def _read_source_geometry():", 1)[1] \
                     .split("src_geometry, src_geometry_reused", 1)[0]
    assert "car_img" not in memo


# --------------------------------------------- no policy / threshold change

def test_no_threshold_moved_by_this_patch():
    from app.services.hybrid_composite import carrier_preflight as cp
    from app.services.hybrid_composite import texture_projection as tp
    assert tp.MIN_CONFIDENCE == 0.62
    assert cp.MAX_CAPE_HEM_TO_SHOULDER == 1.35
    assert "0.5" in HYBRID_SRC.split("front_scan_ok", 1)[1][:120], "scan gate unchanged"


def test_failure_routing_is_unchanged():
    """Same reasons, same order — the period memo did not move above preflight."""
    assert HYBRID_SRC.index("carrier_preflight_rejected") < HYBRID_SRC.index(
        "async def _read_source_period():")
    # the reasons themselves are still raised from the same places
    assert "stripe_model_low_confidence" in HYBRID_SRC
    assert "panel_landmarks_invalid" in HYBRID_SRC
    # and protected-component evaluation still precedes the period reading
    assert HYBRID_SRC.index("evaluate_protected_components") < HYBRID_SRC.index(
        "async def _read_source_period():")


def test_the_patch_adds_no_provider_call():
    memo_g = HYBRID_SRC.split("async def _read_source_geometry():", 1)[1] \
                       .split("src_geometry, src_geometry_reused", 1)[0]
    memo_p = HYBRID_SRC.split("async def _read_source_period():", 1)[1] \
                       .split("src_period, src_period_reused", 1)[0]
    for forbidden in ("generate_content_image", "gemini", "regenerate"):
        assert forbidden not in (memo_g + memo_p).lower(), forbidden


def test_cape_and_qa_capture_untouched_by_this_patch():
    """Guarded because those patches land separately.

    This asserts P1 did not disturb them, not that they exist — so on a tree
    without them there is nothing to check, and this commit must still stand
    on its own.
    """
    from app.services.hybrid_composite import carrier_preflight as cp

    cape_present = hasattr(cp, "SHOULDER_FROM_ARMHOLE")
    qa_present = "_capture_rejected_carrier" in WORKER_SRC
    if not (cape_present or qa_present):
        pytest.skip("cape / QA-capture patches are not in this tree")
    if cape_present:
        for symbol in ("SHOULDER_FROM_ARMHOLE", "SHOULDER_COLLAR_MIN_RATIO",
                       "_armhole_span", "_collar_span"):
            assert hasattr(cp, symbol), symbol
        assert "shoulderWidthSource" in inspect.getsource(cp._geometry_metrics)
    if qa_present:
        # P1 memoises the source read; it must not have absorbed the QA capture
        memo = HYBRID_SRC.split("async def _read_source_geometry():", 1)[1] \
                         .split("src_geometry, src_geometry_reused", 1)[0]
        assert "_capture_rejected_carrier" not in memo
