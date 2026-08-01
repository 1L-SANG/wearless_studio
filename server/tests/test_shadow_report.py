"""Phase 3 P0-C 9/N — shadow 집계.

집계는 임계값 결정의 입력이라 여기서 틀리면 잘못된 임계값이 운영에 간다. 특히:
  · 두 파이프라인을 섞지 않는다(입력 분포도 실패 모드도 다르다)
  · 표본이 모자라면 숫자를 만들지 않고 insufficient_data 로 말한다
  · 기계 pass 를 사람이 거절한 건은 반드시 false pass 후보로 잡힌다
"""

from datetime import datetime, timedelta

import pytest

from app import shadow_report as sr

T0 = datetime(2026, 8, 1, 0, 0, 0)


def row(**kw):
    base = {"id": "s", "edit_type": "BACKGROUND_ONLY", "status": "pass",
            "source_kind": "editor_asset", "created_at": T0,
            "completed_at": T0 + timedelta(seconds=12), "output_id": "o",
            "edit_qc_result": {}, "review_decision": None,
            "has_pattern_or_logo": False}
    base.update(kw)
    return base


def qc(**kw):
    base = {"decision": "pass", "requestedChangeSatisfied": True,
            "unexpectedChanges": [], "lockedInvariantViolations": [],
            "metrics": {"delta": {}}, "vision": {"meta": {"status": "ok"}}}
    base.update(kw)
    return base


# ── 파이프라인 분리 ─────────────────────────────────────────────────────────

def test_pipelines_never_mix():
    rows = [row(source_kind="editor_asset"), row(source_kind="approved_baseline")]
    out = sr.report(rows)
    assert out["samplesByPipeline"] == {"mannequin_edit": 1, "editor_vary": 1}
    assert set(out["pipelines"]) == {"mannequin_edit", "editor_vary"}


def test_pipeline_is_decided_by_source_not_edit_type():
    """같은 edit_type 이 두 파이프라인에 다 나온다 — source_kind 만 구분자다."""
    assert sr.pipeline_of(row(source_kind="editor_asset")) == "editor_vary"
    assert sr.pipeline_of(row(source_kind="approved_baseline")) == "mannequin_edit"
    assert sr.pipeline_of(row(source_kind=None)) == "mannequin_edit"


def test_an_empty_pipeline_reports_zero_not_a_crash():
    out = sr.report([row(source_kind="editor_asset")])
    assert out["pipelines"]["mannequin_edit"]["samples"] == 0
    assert out["pipelines"]["mannequin_edit"]["verdict"]["status"] == "insufficient_data"


# ── 표본 부족 ───────────────────────────────────────────────────────────────

def test_small_samples_are_marked_insufficient():
    out = sr.report([row() for _ in range(5)])["pipelines"]["editor_vary"]
    assert out["decisionRates"]["sufficient"] is False
    assert out["verdict"]["status"] != "enforce_candidate"
    assert any("표본 부족" in b for b in out["verdict"]["blockers"])


def test_no_rows_yield_no_invented_numbers():
    out = sr.report([])
    for p in out["pipelines"].values():
        assert p["samples"] == 0
        assert p["decisionRates"]["rates"] == {}
        assert p["metricDistributions"] == {}
        assert p["visionConfidence"]["n"] == 0
        assert p["latencySeconds"]["n"] == 0


def test_metric_distribution_reports_n_per_axis():
    rows = [row(edit_qc_result=qc(metrics={"delta": {"hemY": i / 100}}))
            for i in range(10)]
    md = sr.report(rows)["pipelines"]["editor_vary"]["metricDistributions"]
    assert md["hemY"]["n"] == 10 and md["hemY"]["sufficient"] is False
    assert md["hemY"]["min"] == 0.0 and md["hemY"]["max"] == pytest.approx(0.09)


def test_booleans_are_not_counted_as_metric_values():
    rows = [row(edit_qc_result=qc(metrics={"delta": {"flag": True, "hemY": 0.1}}))]
    md = sr.report(rows)["pipelines"]["editor_vary"]["metricDistributions"]
    assert "flag" not in md and md["hemY"]["n"] == 1


# ── confusion ───────────────────────────────────────────────────────────────

def test_machine_pass_rejected_by_a_human_is_a_false_pass_candidate():
    rows = [row(status="pass", review_decision="rejected")]
    c = sr.report(rows)["pipelines"]["editor_vary"]["confusion"]
    assert c["falsePassCandidates"] == 1
    assert c["matrix"]["pass"]["rejected"] == 1


def test_review_required_accepted_is_over_review_not_a_failure():
    rows = [row(status="review_required", review_decision="accepted")]
    c = sr.report(rows)["pipelines"]["editor_vary"]["confusion"]
    assert c["overReview"] == 1 and c["falsePassCandidates"] == 0


def test_unreviewed_rows_are_not_graded():
    rows = [row(status="review_required") for _ in range(4)]
    p = sr.report(rows)["pipelines"]["editor_vary"]
    assert p["confusion"]["graded"] == 0
    assert p["userReview"]["reviewRequired"] == 4 and p["userReview"]["unreviewed"] == 4
    assert p["userReview"]["acceptedRate"] is None


def test_accepted_rate_uses_only_decided_rows():
    rows = ([row(status="review_required", review_decision="accepted")] * 3
            + [row(status="review_required", review_decision="rejected")]
            + [row(status="review_required")] * 6)
    u = sr.report(rows)["pipelines"]["editor_vary"]["userReview"]
    assert u["reviewed"] == 4 and u["acceptedRate"] == pytest.approx(0.75)


# ── Vision ──────────────────────────────────────────────────────────────────

def test_missing_vision_is_unavailable_not_ok():
    assert sr.vision_status(row(edit_qc_result={})) == "unavailable"
    assert sr.vision_status(row(edit_qc_result=qc(vision={}))) == "unavailable"
    assert sr.vision_status(row(edit_qc_result=qc(
        vision={"meta": {"status": "timeout"}}))) == "timeout"


def test_vision_unavailability_rate_counts_every_non_ok_status():
    rows = [row(edit_qc_result=qc(vision={"meta": {"status": s}}))
            for s in ("ok", "ok", "timeout", "provider_error")]
    v = sr.report(rows)["pipelines"]["editor_vary"]["visionAvailability"]
    assert v["unavailableRate"] == pytest.approx(0.5)


def test_measurement_vision_conflict_is_counted():
    rows = [row(edit_qc_result=qc(requestedChangeSatisfied=True,
                                  vision={"meta": {"status": "ok"},
                                          "observation": {"requestedChangeApplied": False}}))]
    c = sr.report(rows)["pipelines"]["editor_vary"]["measurementVisionConflict"]
    assert c["conflicts"] == 1 and c["rate"] == 1.0


def test_conflict_ignores_rows_without_usable_vision():
    rows = [row(edit_qc_result=qc(vision={"meta": {"status": "timeout"},
                                          "observation": {"requestedChangeApplied": False}}))]
    c = sr.report(rows)["pipelines"]["editor_vary"]["measurementVisionConflict"]
    assert c["considered"] == 0 and c["rate"] is None


def test_null_observation_is_not_a_conflict():
    """bool|null 계약 — null 은 '모른다'지 '아니다'가 아니다."""
    rows = [row(edit_qc_result=qc(vision={"meta": {"status": "ok"},
                                          "observation": {"requestedChangeApplied": None}}))]
    assert sr.report(rows)["pipelines"]["editor_vary"][
        "measurementVisionConflict"]["conflicts"] == 0


# ── 위반·비용·latency ───────────────────────────────────────────────────────

def test_violations_are_counted_per_axis():
    rows = [row(edit_qc_result=qc(lockedInvariantViolations=["collarChanged"],
                                  unexpectedChanges=["hemY", "cuffY"])),
            row(edit_qc_result=qc(lockedInvariantViolations=["collarChanged"]))]
    v = sr.report(rows)["pipelines"]["editor_vary"]["violations"]
    assert v["lockedInvariantViolations"]["collarChanged"]["count"] == 2
    assert v["lockedInvariantViolations"]["collarChanged"]["rate"] == 1.0
    assert v["unexpectedChanges"]["hemY"]["count"] == 1


def test_cost_is_zero_until_unit_prices_are_given():
    rows = [row() for _ in range(3)]
    prov = sr.report(rows)["pipelines"]["editor_vary"]["provider"]
    assert prov["estimatedUsd"] == 0.0 and prov["unitPricesProvided"] is False
    priced = sr.report(rows, image_usd=0.1)["pipelines"]["editor_vary"]["provider"]
    assert priced["estimatedUsd"] == pytest.approx(0.3)
    assert priced["unitPricesProvided"] is True


def test_latency_skips_unfinished_sessions():
    rows = [row(), row(completed_at=None)]
    lat = sr.report(rows)["pipelines"]["editor_vary"]["latencySeconds"]
    assert lat["n"] == 1 and lat["p50"] == 12.0


# ── verdict ─────────────────────────────────────────────────────────────────

def _many(n, **kw):
    return [row(**kw) for _ in range(n)]


def test_a_single_false_pass_blocks_enforce_even_with_many_samples():
    rows = _many(60, status="pass", review_decision="accepted", edit_qc_result=qc())
    rows += [row(status="pass", review_decision="rejected", edit_qc_result=qc())]
    v = sr.report(rows)["pipelines"]["editor_vary"]["verdict"]
    assert v["enforceReady"] is False and v["status"] == "shadow_only"
    assert any("거절한 사례" in b for b in v["blockers"])


def test_high_vision_unavailability_blocks_enforce():
    rows = _many(40, status="pass", review_decision="accepted",
                 edit_qc_result=qc(vision={"meta": {"status": "ok"}}))
    rows += _many(20, status="pass", review_decision="accepted",
                  edit_qc_result=qc(vision={"meta": {"status": "timeout"}}))
    v = sr.report(rows)["pipelines"]["editor_vary"]["verdict"]
    assert v["enforceReady"] is False
    assert any("Vision 미가용률" in b for b in v["blockers"])


def test_enforce_candidate_needs_both_sample_floors():
    rows = _many(60, status="pass", review_decision="accepted", edit_qc_result=qc())
    v = sr.report(rows)["pipelines"]["editor_vary"]["verdict"]
    assert v["status"] == "enforce_candidate" and v["enforceReady"] is True


def test_unreviewed_bulk_never_reaches_enforce_candidate():
    """사람이 아무도 안 본 데이터는 아무리 많아도 검증 표본이 아니다."""
    v = sr.report(_many(500, status="pass", edit_qc_result=qc()))[
        "pipelines"]["editor_vary"]["verdict"]
    assert v["status"] == "insufficient_data"


def test_vision_unavailable_alone_blocks_enforce():
    """Vision 이 없으면 자동 통과 금지 — 표본이 아무리 많아도."""
    v = sr.report(_many(80, status="pass", review_decision="accepted"))[
        "pipelines"]["editor_vary"]["verdict"]
    assert v["enforceReady"] is False
    assert any("Vision 미가용률" in b for b in v["blockers"])


# ── 수집 쿼리 계약 ──────────────────────────────────────────────────────────

def _script():
    return open("/Users/nojeong-un/devs/wearless_studio/server/scripts/shadow_report.py",
                encoding="utf-8").read()


def test_collection_is_read_only():
    src = _script()
    assert "conn.read_only = True" in src
    lowered = src.lower()
    for w in ("insert into", "update public.", "delete from", "alter table", "drop "):
        assert w not in lowered, f"쓰기 구문 {w!r} 이 수집 스크립트에 있다"


def test_collection_never_selects_provider_raw():
    src = _script()
    for w in ("prompt_r2_key", "prompt_sha256", "raw_response", "provider_raw"):
        assert w not in src


def test_collection_takes_latest_user_decision():
    assert "order by re.created_at desc, re.id desc" in _script()
