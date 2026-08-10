"""공개 `qcScores` 표면 — 클라이언트가 읽는 것은 남고, 내부 진단은 나가지 않는다.

이 스위트가 지키는 두 방향:
  · **좁히기**: 자산 id·원본 바이트 해시·디버그 URL·라우팅 정책은 공개되지 않는다.
  · **깨지 않기**: `reviewState.js` 가 실제로 읽는 필드는 전부 살아남는다.

두 번째가 없으면 이 변경은 그냥 프론트를 부수는 일이 된다.
"""

import inspect

from app.services.public_qc_projection import public_qc_scores

#: `src/features/mannequin/reviewState.js` 가 실제로 읽는 것(코드 조사로 확인).
CLIENT_READS_TOP = ("outcome", "critical_errors", "series_inconsistencies")
CLIENT_READS_HYBRID = ("mode", "applied", "needsReview", "componentsNeedingReview",
                       "failureReason", "reason")
CLIENT_READS_PROJECTION = ("mode", "ok", "reason")


def _full_snapshot():
    """워커가 실제로 쓰는 모양 — 민감 필드를 전부 포함한다."""
    return {
        "product_fidelity": 82, "physical_naturalness": 79,
        "image_quality": 88, "series_consistency": 91,
        "outcome": "auto_pass", "salvaged": False,
        "critical_errors": [], "series_inconsistencies": ["sleeve"],
        "thresholds": {"auto_pass": 80, "review": 65},
        "hybridComposite": {
            "mode": "enforce", "applied": True, "wouldApply": True,
            "needsReview": False, "componentsNeedingReview": [],
            "failureReason": None, "failureDetail": None, "failClosed": False,
            "pipelineVersion": "hc_v9", "textureTruth": "TEXTURE_TRUTH_UNCERTAIN",
            "textureSource": "direct_source_transfer",
            "textureProjection": {"mode": "enforce", "ok": True, "reason": None,
                                  "internalPlan": {"seed": 7}},
            # ↓ 전부 내부 진단 — 클라이언트에 읽는 코드가 없다
            "sourceAssets": {"front": {"assetId": "a-1", "sha256": "deadbeef"}},
            "carrierSha256": "c0ffee", "outputSha256": "f00d",
            "stripeModel": {"period_px": 24.0}, "panelMetrics": {"torso": 1},
            "deterministicMetrics": {"repeat_count_rel_err_max": 0.01},
            "carrierPreflight": {"reasons": []},
            "protectedComponentContract": {"ok": True},
            "versions": {"pipeline": "x", "warp": "y"},
            "carrierShapeFidelity": {"lineage": {"job_id": "j1"}},
            "targetPeriodPx": 24.0, "sourceCoverage": 0.97,
            "absoluteScale": {"harmonicRatio": 1.0, "enforced": False},
        },
        "structuredQC": {
            "outcome": "auto_pass", "salvaged": False, "overallDecision": "pass",
            "criticalErrors": [], "warnings": ["소매 주름"],
            "checks": [{"check": "pattern_fidelity", "status": "pass",
                        "metrics": {"deltaE": 1.2}, "debugOverlaySha256": "abc"}],
            # ↓ 내부
            "truthPackageId": "t-1", "generationOutputId": "g-1",
            "policyVersion": "p1", "pipelineLane": "fast",
            "pipelinePolicy": {"lane": "fast", "riskScore": 0.2,
                               "candidateCount": 1, "autoApproval": True},
            "sourceSemantics": {"pattern": {"assetId": "a-9"}},
            "debugAssets": [{"assetId": "d-1", "url": "/v1/assets/d-1/file"}],
            "failedRegions": [], "regenerationInstructions": ["재생성"],
            "scores": {"pattern": 0.9},
        },
        "frameLockQc": {"decision": "pass", "metrics": {"crop": [1, 2]}},
    }


# ── 좁히기 ────────────────────────────────────────────────────────────────
def test_source_asset_identity_and_hashes_do_not_ship():
    """원본 자산 id 와 원본 바이트 해시는 제품 표면에 있을 이유가 없다."""
    out = public_qc_scores(_full_snapshot())
    hybrid = out["hybridComposite"]
    for leaked in ("sourceAssets", "carrierSha256", "outputSha256",
                   "carrierShapeFidelity"):
        assert leaked not in hybrid, leaked


def test_debug_asset_urls_and_routing_policy_do_not_ship():
    """디버그 오버레이 URL 과 파이프라인 라우팅 정책은 내부 운영 정보다."""
    structured = public_qc_scores(_full_snapshot())["structuredQC"]
    for leaked in ("debugAssets", "pipelinePolicy", "pipelineLane", "sourceSemantics",
                   "truthPackageId", "generationOutputId", "policyVersion",
                   "regenerationInstructions", "scores", "failedRegions"):
        assert leaked not in structured, leaked


def test_per_check_metrics_and_overlay_hashes_do_not_ship():
    check = public_qc_scores(_full_snapshot())["structuredQC"]["checks"][0]
    assert set(check) <= {"check", "status", "reason"}, check


def test_internal_tuning_constants_do_not_ship():
    assert "thresholds" not in public_qc_scores(_full_snapshot())


def test_a_new_internal_observation_does_not_leak_by_default():
    """allowlist 이므로 나중에 누가 관측을 추가해도 공개 표면은 그대로다.

    이번 작업에서 실제로 `absoluteScale` 을 추가했다 — 그것이 새어 나가면 안 된다.
    """
    out = public_qc_scores(_full_snapshot())
    assert "absoluteScale" not in out["hybridComposite"]
    snap = _full_snapshot()
    snap["hybridComposite"]["someFutureDiagnostic"] = {"secret": 1}
    assert "someFutureDiagnostic" not in public_qc_scores(snap)["hybridComposite"]


# ── 깨지 않기 ─────────────────────────────────────────────────────────────
def test_every_field_the_client_reads_survives():
    """좁히는 것이 목적이지 계약을 깨는 것이 목적이 아니다."""
    snap = _full_snapshot()
    # 클라이언트가 읽는 사유 키는 상황에 따라만 존재한다 — **있을 때 살아남는지**가
    # 지켜야 할 불변식이다. 없는 키의 존재를 요구하면 픽스처를 시험하는 꼴이 된다.
    snap["hybridComposite"]["reason"] = "guided_period_unvalidated_harmonic"
    out = public_qc_scores(snap)
    for key in CLIENT_READS_TOP:
        assert key in out, key
    for key in CLIENT_READS_HYBRID:
        assert key in out["hybridComposite"], key
        assert out["hybridComposite"][key] == snap["hybridComposite"][key], key
    for key in CLIENT_READS_PROJECTION:
        assert key in out["hybridComposite"]["textureProjection"], key
    structured = out["structuredQC"]
    assert structured["warnings"] == ["소매 주름"]
    assert structured["criticalErrors"] == []
    assert structured["checks"][0]["check"] == "pattern_fidelity"
    assert structured["checks"][0]["status"] == "pass"


def test_the_documented_score_axes_survive():
    """`src/lib/types.js` 가 문서화한 4축 — 민감하지 않고 계약에 남아 있다."""
    out = public_qc_scores(_full_snapshot())
    for key in ("product_fidelity", "physical_naturalness", "image_quality",
                "series_consistency"):
        assert key in out, key


def test_legacy_shapes_pass_through_untouched():
    """QC 가 돌지 않은 컷은 `None` 이다 — 그 관용을 여기서 깨지 않는다."""
    assert public_qc_scores(None) is None
    assert public_qc_scores("legacy") == "legacy"
    assert public_qc_scores({}) == {}


# ── 배선 ──────────────────────────────────────────────────────────────────
def test_both_serialisation_points_use_the_projection():
    """한 곳만 좁히면 다른 곳으로 그대로 나간다."""
    from app import repo, routes
    assert "public_qc_scores(" in inspect.getsource(routes._cut_to_api)
    repo_src = inspect.getsource(repo)
    assert repo_src.count('"qcScores": public_qc_scores(') >= 1
    assert '"qcScores": qc_scores,' not in repo_src
