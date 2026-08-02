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

# manifest 없는 리포트는 distribution_only 라 판정 플래그가 닫힌다(9/N 정책).
# 판정 **로직** 을 보는 테스트는 신뢰 가능한 manifest 를 준 상태로 검사한다.
TRUSTED = {"validForCalibration": True}



def row(**kw):
    """기계 판정의 정본은 edit_qc_result.decision 이라, status 와 따로 준다."""
    status = kw.pop("status", "pass")
    base = {"id": "s", "edit_type": "BACKGROUND_ONLY", "status": status,
            "source_kind": "editor_asset", "created_at": T0,
            "completed_at": T0 + timedelta(seconds=12), "output_id": "o",
            "edit_qc_result": qc(decision=status), "review_decision": None,
            "image_calls": 1, "vision_calls": 1}
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
    assert out["samplesByPipeline"] == {"mannequin_edit": 1, "editor_vary": 1,
                                        "unknown": 0}
    assert set(out["pipelines"]) == {"mannequin_edit", "editor_vary", "unknown"}


def test_pipeline_is_decided_by_source_not_edit_type():
    """같은 edit_type 이 두 파이프라인에 다 나온다 — source_kind 만 구분자다."""
    assert sr.pipeline_of(row(source_kind="editor_asset")) == "editor_vary"
    assert sr.pipeline_of(row(source_kind="approved_baseline")) == "mannequin_edit"
    assert sr.pipeline_of(row(source_kind=None)) == "unknown"
    assert sr.pipeline_of(row(source_kind="bogus")) == "unknown"


def test_an_empty_pipeline_reports_zero_not_a_crash():
    out = sr.report([row(source_kind="editor_asset")], manifest=TRUSTED)
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
    md = sr.report(rows, manifest=TRUSTED)["pipelines"]["editor_vary"]["metricDistributions"]
    assert md["hemY"]["n"] == 10 and md["hemY"]["sufficient"] is False
    assert md["hemY"]["min"] == 0.0 and md["hemY"]["max"] == pytest.approx(0.09)


def test_booleans_are_not_counted_as_metric_values():
    rows = [row(edit_qc_result=qc(metrics={"delta": {"flag": True, "hemY": 0.1}}))]
    md = sr.report(rows, manifest=TRUSTED)["pipelines"]["editor_vary"]["metricDistributions"]
    assert "flag" not in md and md["hemY"]["n"] == 1


# ── confusion ───────────────────────────────────────────────────────────────

def test_machine_pass_rejected_by_a_human_is_a_false_pass_candidate():
    rows = [row(status="pass", review_decision="rejected")]
    c = sr.report(rows, manifest=TRUSTED)["pipelines"]["editor_vary"]["confusion"]
    assert c["falsePassCandidates"] == 1
    assert c["matrix"]["pass"]["rejected"] == 1


def test_review_required_accepted_is_over_review_not_a_failure():
    rows = [row(status="review_required", review_decision="accepted")]
    c = sr.report(rows, manifest=TRUSTED)["pipelines"]["editor_vary"]["confusion"]
    assert c["overReview"] == 1 and c["falsePassCandidates"] == 0


def test_unreviewed_rows_are_not_graded():
    rows = [row(status="review_required") for _ in range(4)]
    p = sr.report(rows, manifest=TRUSTED)["pipelines"]["editor_vary"]
    assert p["confusion"]["graded"] == 0
    assert p["userReview"]["reviewRequired"] == 4 and p["userReview"]["unreviewed"] == 4
    assert p["userReview"]["acceptedRate"] is None


def test_accepted_rate_uses_only_decided_rows():
    rows = ([row(status="review_required", review_decision="accepted")] * 3
            + [row(status="review_required", review_decision="rejected")]
            + [row(status="review_required")] * 6)
    u = sr.report(rows, manifest=TRUSTED)["pipelines"]["editor_vary"]["userReview"]
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
    v = sr.report(rows, manifest=TRUSTED)["pipelines"]["editor_vary"]["visionAvailability"]
    assert v["unavailableRate"] == pytest.approx(0.5)


def test_measurement_vision_conflict_is_counted():
    rows = [row(edit_qc_result=qc(requestedChangeSatisfied=True,
                                  vision={"meta": {"status": "ok"},
                                          "observation": {"requestedChangeApplied": False}}))]
    c = sr.report(rows, manifest=TRUSTED)["pipelines"]["editor_vary"]["measurementVisionConflict"]
    assert c["conflicts"] == 1 and c["rate"] == 1.0


def test_conflict_ignores_rows_without_usable_vision():
    rows = [row(edit_qc_result=qc(vision={"meta": {"status": "timeout"},
                                          "observation": {"requestedChangeApplied": False}}))]
    c = sr.report(rows, manifest=TRUSTED)["pipelines"]["editor_vary"]["measurementVisionConflict"]
    assert c["considered"] == 0 and c["rate"] is None


def test_null_observation_is_not_a_conflict():
    """bool|null 계약 — null 은 '모른다'지 '아니다'가 아니다."""
    rows = [row(edit_qc_result=qc(vision={"meta": {"status": "ok"},
                                          "observation": {"requestedChangeApplied": None}}))]
    assert sr.report(rows, manifest=TRUSTED)["pipelines"]["editor_vary"][
        "measurementVisionConflict"]["conflicts"] == 0


# ── 위반·비용·latency ───────────────────────────────────────────────────────

def test_violations_are_counted_per_axis():
    rows = [row(edit_qc_result=qc(lockedInvariantViolations=["collarChanged"],
                                  unexpectedChanges=["hemY", "cuffY"])),
            row(edit_qc_result=qc(lockedInvariantViolations=["collarChanged"]))]
    v = sr.report(rows, manifest=TRUSTED)["pipelines"]["editor_vary"]["violations"]
    assert v["lockedInvariantViolations"]["collarChanged"]["count"] == 2
    assert v["lockedInvariantViolations"]["collarChanged"]["rate"] == 1.0
    assert v["unexpectedChanges"]["hemY"]["count"] == 1


def test_cost_is_zero_until_unit_prices_are_given():
    rows = [row(vision_calls=0) for _ in range(3)]
    prov = sr.report(rows, manifest=TRUSTED)["pipelines"]["editor_vary"]["provider"]
    assert prov["estimatedUsd"] == 0.0 and prov["unitPricesProvided"] is False
    priced = sr.report(rows, image_usd=0.1)["pipelines"]["editor_vary"]["provider"]
    assert priced["estimatedUsd"] == pytest.approx(0.3)
    assert priced["unitPricesProvided"] is True


def test_latency_skips_unfinished_sessions():
    rows = [row(), row(completed_at=None)]
    lat = sr.report(rows, manifest=TRUSTED)["pipelines"]["editor_vary"]["latencySeconds"]
    assert lat["n"] == 1 and lat["p50"] == 12.0


# ── verdict ─────────────────────────────────────────────────────────────────

def _many(n, **kw):
    return [row(**kw) for _ in range(n)]


def test_a_single_false_pass_blocks_enforce_even_with_many_samples():
    # readiness 기준은 blinded fidelity 라벨이다(운영 reviewDecision 아님).
    rows = _many(60, edit_qc_result=qc(decision="pass"), human_label="fidelity_pass")
    rows += [row(edit_qc_result=qc(decision="pass"), human_label="fidelity_fail")]
    v = sr.report(rows, manifest=TRUSTED)["pipelines"]["editor_vary"]["verdict"]
    assert v["enforceReady"] is False and v["status"] == "shadow_only"
    assert any("false pass 1건" in b for b in v["blockers"])


def test_high_vision_unavailability_blocks_enforce():
    rows = _many(40, status="pass", review_decision="accepted",
                 edit_qc_result=qc(vision={"meta": {"status": "ok"}}))
    rows += _many(20, status="pass", review_decision="accepted",
                  edit_qc_result=qc(vision={"meta": {"status": "timeout"}}))
    v = sr.report(rows, manifest=TRUSTED)["pipelines"]["editor_vary"]["verdict"]
    assert v["enforceReady"] is False
    assert any("Vision 미가용률" in b for b in v["blockers"])


def test_enforce_candidate_needs_both_sample_floors():
    rows = _many(60, edit_qc_result=qc(decision="pass"), human_label="fidelity_pass")
    v = sr.report(rows, manifest=TRUSTED)["pipelines"]["editor_vary"]["verdict"]
    assert v["status"] == "enforce_candidate" and v["enforceReady"] is True


def test_unreviewed_bulk_never_reaches_enforce_candidate():
    """사람이 아무도 안 본 데이터는 아무리 많아도 검증 표본이 아니다."""
    v = sr.report(_many(500, edit_qc_result=qc(decision="pass")), manifest=TRUSTED)[
        "pipelines"]["editor_vary"]["verdict"]
    assert v["status"] == "insufficient_data"


def test_vision_unavailable_alone_blocks_enforce():
    """Vision 이 없으면 자동 통과 금지 — 표본이 아무리 많아도."""
    v = sr.report(_many(80, human_label="fidelity_pass",
                        edit_qc_result=qc(decision="pass", vision={})))[
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


# ── 9/N 보정: 판정 정본·unknown·edit type·라벨 커버리지 ─────────────────────

def test_machine_decision_comes_from_qc_not_workflow_status():
    """status 를 판정으로 쓰면 provider 실패가 reject 로 둔갑한다."""
    r = row(status="failed", edit_qc_result=qc(decision="pass"))
    assert sr.machine_decision(r) == "pass"
    assert sr.workflow_status(r) == "failed"


def test_a_sample_without_output_is_failed_not_a_decision():
    r = row(output_id=None, edit_qc_result={"error": "generation_failed"})
    assert sr.machine_decision(r) == "failed"


def test_an_unknown_decision_is_review_not_pass():
    assert sr.machine_decision(row(edit_qc_result=qc(decision="weird"))) == "review_required"
    assert sr.machine_decision(row(edit_qc_result={})) == "review_required"


def test_review_and_review_required_normalize_together():
    assert sr.machine_decision(row(edit_qc_result=qc(decision="review"))) == "review_required"


def test_unknown_pipeline_is_reported_not_absorbed():
    out = sr.report([row(source_kind=None, id="x1"), row(source_kind="editor_asset")])
    assert out["samplesByPipeline"]["unknown"] == 1
    assert out["samplesByPipeline"]["mannequin_edit"] == 0
    assert "x1" in out["unknownPipelineSamples"]
    assert out["pipelines"]["unknown"]["verdict"]["enforceReady"] is False


def test_custom_review_required_is_excluded_from_enforce():
    rows = _many(60, edit_type="CUSTOM_REVIEW_REQUIRED", review_decision="accepted",
                 edit_qc_result=qc())
    p = sr.report(rows, manifest=TRUSTED)["pipelines"]["editor_vary"]
    assert p["enforceEligibleSamples"] == 0
    detail = p["byEditTypeDetail"]["CUSTOM_REVIEW_REQUIRED"]
    assert detail["enforceEligible"] is False
    assert any("자동 통과 대상이 아닌" in b for b in detail["verdict"]["blockers"])


def test_six_background_only_are_not_made_sufficient_by_custom_samples():
    """섞으면 30건처럼 보인다 — 그게 9/N 리포트가 착시를 준 지점이다."""
    rows = ([row(edit_type="BACKGROUND_ONLY") for _ in range(6)]
            + [row(edit_type="CUSTOM_REVIEW_REQUIRED") for _ in range(24)])
    p = sr.report(rows, manifest=TRUSTED)["pipelines"]["editor_vary"]
    assert p["samples"] == 30
    assert p["enforceEligibleSamples"] == 6
    bg = p["byEditTypeDetail"]["BACKGROUND_ONLY"]
    assert bg["samples"] == 6 and bg["decisionRates"]["sufficient"] is False
    assert bg["verdict"]["status"] == "insufficient_data"


def test_each_edit_type_reports_its_own_metrics():
    rows = [row(edit_type="BACKGROUND_ONLY",
                edit_qc_result=qc(metrics={"delta": {"hemY": 0.1}})),
            row(edit_type="CUSTOM_REVIEW_REQUIRED",
                edit_qc_result=qc(metrics={"delta": {"hemY": 0.9}}))]
    d = sr.report(rows, manifest=TRUSTED)["pipelines"]["editor_vary"]["byEditTypeDetail"]
    assert d["BACKGROUND_ONLY"]["metricDistributions"]["hemY"]["max"] == 0.1
    assert d["CUSTOM_REVIEW_REQUIRED"]["metricDistributions"]["hemY"]["max"] == 0.9


def test_pass_samples_without_labels_block_enforce():
    """라벨 없는 pass 는 false pass 0 이 아니라 미측정이다."""
    rows = _many(60, edit_qc_result=qc(decision="pass"))
    v = sr.report(rows, manifest=TRUSTED)["pipelines"]["editor_vary"]["byEditTypeDetail"][
        "BACKGROUND_ONLY"]["verdict"]
    assert v["enforceReady"] is False
    assert any("human label" in b or "표본 부족" in b for b in v["blockers"])


def test_zero_pass_samples_cannot_be_enforce_ready():
    rows = _many(60, edit_qc_result=qc(decision="review_required"),
                 review_decision="accepted")
    v = sr.report(rows, manifest=TRUSTED)["pipelines"]["editor_vary"]["byEditTypeDetail"][
        "BACKGROUND_ONLY"]["verdict"]
    assert v["enforceReady"] is False
    assert any("pass 표본 0건" in b for b in v["blockers"])


def test_human_label_coverage_counts_pass_samples_separately():
    rows = (_many(3, edit_qc_result=qc(decision="pass"), human_label="fidelity_pass")
            + _many(2, edit_qc_result=qc(decision="pass"))
            + _many(5, edit_qc_result=qc(decision="review_required"),
                    review_decision="accepted"))
    h = sr.report(rows, manifest=TRUSTED)["pipelines"]["editor_vary"]["humanLabels"]
    assert h["passSamples"] == 5 and h["passLabeled"] == 3
    assert h["passCoverage"] == pytest.approx(0.6)


def test_provider_cost_uses_attempted_counters():
    rows = [row(image_calls=2, vision_calls=3)]     # 재시도 포함
    prov = sr.report(rows, image_usd=0.1, vision_usd=0.01)["pipelines"][
        "editor_vary"]["provider"]
    assert prov["imageCalls"] == 2 and prov["visionCalls"] == 3
    assert prov["estimatedUsd"] == pytest.approx(0.23)


def test_failed_samples_still_cost_money():
    rows = [row(output_id=None, edit_qc_result={"error": "x"}, image_calls=1,
                vision_calls=0)]
    prov = sr.report(rows, image_usd=0.1)["pipelines"]["editor_vary"]["provider"]
    assert prov["imageCalls"] == 1 and prov["estimatedUsd"] == pytest.approx(0.1)


# ── pattern/logo 는 스키마가 없으면 unknown ────────────────────────────────

@pytest.mark.parametrize("analysis", [
    {}, {"clothingType": "top", "styleTags": ["basic"]},
    {"materials": [{"name": "아크릴"}]},
])
def test_missing_pattern_schema_is_unknown_not_false(analysis):
    assert sr.pattern_or_logo(row(analysis=analysis)) == "unknown"


@pytest.mark.parametrize("value", [None, False, "", "none", "solid", [], {}])
def test_empty_pattern_values_are_false(value):
    assert sr.pattern_or_logo(row(analysis={"pattern": value})) == "false"


@pytest.mark.parametrize("value", ["stripe", ["stripe"], {"type": "stripe"}, True])
def test_structured_pattern_values_are_true(value):
    assert sr.pattern_or_logo(row(analysis={"pattern": value})) == "true"


def test_a_logo_asset_list_counts():
    assert sr.pattern_or_logo(row(analysis={"logoAssets": ["a"]})) == "true"
    assert sr.pattern_or_logo(row(analysis={"logoAssets": []})) == "false"


def test_pattern_axis_shows_unknown_in_the_report():
    out = sr.report([row(analysis={"clothingType": "top"}) for _ in range(3)])
    assert out["pipelines"]["editor_vary"]["byPatternOrLogo"] == {"unknown": 3}


# ── manifest 연결 ───────────────────────────────────────────────────────────

def test_an_invalid_manifest_marks_the_whole_report_unusable():
    out = sr.report([row()], manifest={"validForCalibration": False,
                                       "invalidReasons": ["empty_allowed_scope"]})
    assert out["calibrationUsable"] is False
    assert out["calibrationBlockedReasons"] == ["empty_allowed_scope"]


def test_a_valid_manifest_does_not_add_a_block_flag():
    out = sr.report([row()], manifest={"validForCalibration": True})
    assert "calibrationUsable" not in out


# ── calibration confusion (9/N 최종 보정) ──────────────────────────────────
# 운영 reviewDecision(사용자 행동)과 blinded fidelity 라벨(측정값)을 섞지 않는다.

def _bg(n, **kw):
    return [row(edit_type="BACKGROUND_ONLY", **kw) for _ in range(n)]


def test_pass_with_fidelity_fail_is_a_false_pass():
    rows = [row(edit_qc_result=qc(decision="pass"), human_label="fidelity_fail")]
    cal = sr.calibration_confusion(rows)
    assert cal["falsePass"] == 1 and cal["falsePassMeasured"] is True
    assert cal["matrix"]["pass"]["fidelity_fail"] == 1


def test_one_false_pass_blocks_enforce():
    rows = _bg(59, edit_qc_result=qc(decision="pass"), human_label="fidelity_pass")
    rows += _bg(1, edit_qc_result=qc(decision="pass"), human_label="fidelity_fail")
    v = sr.report(rows, manifest=TRUSTED)["pipelines"]["editor_vary"]["byEditTypeDetail"][
        "BACKGROUND_ONLY"]["verdict"]
    assert v["enforceReady"] is False
    assert any("false pass 1건" in b for b in v["blockers"])


def test_blinded_labels_count_toward_calibration_graded():
    rows = _bg(30, edit_qc_result=qc(decision="pass"), human_label="fidelity_pass")
    cal = sr.report(rows, manifest=TRUSTED)["pipelines"]["editor_vary"]["calibrationConfusion"]
    assert cal["graded"] == 30 and cal["sufficient"] is True


def test_production_review_decisions_alone_cannot_satisfy_readiness():
    """reviewDecision 30건은 사용자 행동이지 fidelity 측정이 아니다."""
    rows = _bg(30, edit_qc_result=qc(decision="pass"), review_decision="accepted")
    p = sr.report(rows, manifest=TRUSTED)["pipelines"]["editor_vary"]["byEditTypeDetail"]["BACKGROUND_ONLY"]
    assert p["confusion"]["graded"] == 30          # 운영 표는 채워지지만
    assert p["calibrationConfusion"]["graded"] == 0   # 캘리브레이션 표는 비어 있다
    assert p["verdict"]["enforceReady"] is False
    assert p["verdict"]["basis"] == "calibration_confusion"


def test_zero_pass_labels_means_unmeasured_not_zero():
    rows = _bg(30, edit_qc_result=qc(decision="pass"))
    cal = sr.report(rows, manifest=TRUSTED)["pipelines"]["editor_vary"]["calibrationConfusion"]
    assert cal["falsePass"] is None and cal["falsePassMeasured"] is False
    assert cal["matrix"]["pass"]["unlabeled"] == 30


def test_over_review_and_false_reject_candidates_are_counted():
    rows = [row(edit_qc_result=qc(decision="review_required"), human_label="fidelity_pass"),
            row(edit_qc_result=qc(decision="reject"), human_label="fidelity_pass")]
    cal = sr.calibration_confusion(rows)
    assert cal["overReviewCandidates"] == 1 and cal["falseRejectCandidates"] == 1


def test_label_coverage_is_reported_per_decision():
    rows = (_bg(2, edit_qc_result=qc(decision="pass"), human_label="fidelity_pass")
            + _bg(2, edit_qc_result=qc(decision="pass"))
            + _bg(1, edit_qc_result=qc(decision="reject")))
    cov = sr.calibration_confusion(rows)["labelCoverageByDecision"]
    assert cov["pass"] == pytest.approx(0.5) and cov["reject"] == 0.0
    assert cov["failed"] is None


def test_custom_stays_excluded_from_enforce_even_when_fully_labeled():
    rows = [row(edit_type="CUSTOM_REVIEW_REQUIRED", edit_qc_result=qc(decision="pass"),
                human_label="fidelity_pass") for _ in range(60)]
    d = sr.report(rows, manifest=TRUSTED)["pipelines"]["editor_vary"]["byEditTypeDetail"][
        "CUSTOM_REVIEW_REQUIRED"]
    assert d["calibrationConfusion"]["graded"] == 60
    assert d["verdict"]["enforceReady"] is False
    assert any("자동 통과 대상이 아닌" in b for b in d["verdict"]["blockers"])


def test_a_fully_labeled_clean_background_only_set_reaches_enforce_candidate():
    rows = _bg(40, edit_qc_result=qc(decision="pass"), human_label="fidelity_pass")
    v = sr.report(rows, manifest=TRUSTED)["pipelines"]["editor_vary"]["byEditTypeDetail"][
        "BACKGROUND_ONLY"]["verdict"]
    assert v["enforceReady"] is True and v["status"] == "enforce_candidate"
