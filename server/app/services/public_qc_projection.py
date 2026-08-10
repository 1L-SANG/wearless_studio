"""공개 `qcScores` 투영 — 클라이언트가 실제로 읽는 것만 내보낸다.

왜 필요한가
-----------
`qcScores` 는 지금까지 워커 내부 QC 스냅샷을 **가공 없이** 통째로 내보냈다. 그 안에는
원본 자산 id 와 원본 바이트의 SHA-256, QC 오버레이 이미지의 자산 id 와 `/v1/assets/{id}/file`
URL, 파이프라인 라우팅 정책(lane·riskScore·모듈·후보수·해상도·자동승인), truth package id,
generation output id 가 들어 있다. 클라이언트는 그중 아무것도 읽지 않는다.

같은 저장소가 **다른 경로에서는 이미 좁히고 있다**: 공개 실패 상세는 5개 키만
(`repo._public_hybrid`), 내부 계보 행조차 metric allowlist 를 통과시킨다
(`mannequin_job.safe_hc`). 성공 경로의 공개 페이로드가 그 두 곳보다 넓다는 것이 모순이다.

무엇을 좁히고 무엇을 두는가
---------------------------
민감하고 쓰이지 않는 것이 모여 있는 두 하위 구조(`hybridComposite`, `structuredQC`)를
**allowlist** 로 바꾸고, 최상위에서는 내부 튜닝 상수(`thresholds`)만 뺀다. 최상위 점수
4축은 `src/lib/types.js` 가 문서화하고 있어 값이 민감하지 않은 채로 계약에 남아 있으므로
그대로 둔다 — 좁히는 것이 목적이지 계약을 깨는 것이 목적이 아니다.

새 진단 필드는 **기본적으로 새어 나가지 않는다**. allowlist 이므로 나중에 누가
`hybridComposite` 에 관측을 추가해도 공개 표면은 변하지 않는다(예: `absoluteScale`).
"""

from __future__ import annotations

PUBLIC_QC_PROJECTION_VERSION = "public_qc_projection_v1"

#: 최상위에서 제거하는 키 — 내부 캘리브레이션 상수. 클라이언트에 읽는 코드가 없다.
_TOP_LEVEL_DROP = frozenset({"thresholds"})

#: `hybridComposite` 에서 공개하는 키. reviewState.js 가 읽는 것 + 사유 표시에 필요한 것.
_HYBRID_ALLOW = frozenset({
    "mode", "applied", "wouldApply", "needsReview", "componentsNeedingReview",
    "failureReason", "failureDetail", "reason", "failClosed", "pipelineVersion",
    "textureTruth", "textureSource",
})

#: `hybridComposite.textureProjection` 에서 공개하는 키.
_TEXTURE_PROJECTION_ALLOW = frozenset({"mode", "ok", "reason"})

#: `structuredQC` 에서 공개하는 키.
_STRUCTURED_ALLOW = frozenset({
    "outcome", "salvaged", "overallDecision", "criticalErrors", "warnings",
})

#: `structuredQC.checks[]` 에서 공개하는 키 — 판정과 이름만. `metrics`·오버레이 해시는 뺀다.
_CHECK_ALLOW = frozenset({"check", "status", "reason"})


def _project_checks(checks) -> list:
    if not isinstance(checks, list):
        return []
    out = []
    for entry in checks:
        if isinstance(entry, dict):
            out.append({k: v for k, v in entry.items() if k in _CHECK_ALLOW})
    return out


#: 잡 이벤트 페이로드에서 지우는 키 — 어느 깊이에 있든 지운다.
#:
#: 왜 여기만 allowlist 가 아닌가: 이벤트 타입이 수십 가지고 각각 모양이 다르다.
#: allowlist 로 하면 새 진단 이벤트가 전부 빈 껍데기로 나가서 운영 가치가 사라진다.
#: 대신 **민감한 이름의 집합**을 재귀로 지우고, 워커가 실제로 내보내는 키를 전수로
#: 훑어 이 목록 밖의 민감 키가 없는지 시험으로 확인한다(`test_job_event_projection`).
_EVENT_SENSITIVE_KEYS = frozenset({
    # 원본 자산 정체성과 원본 바이트 해시
    "source_asset_id", "sourceAssetId", "source_sha256", "sourceSha256",
    "sourceAssets", "asset_id", "assetId", "sha256",
    "carrier_sha256", "carrierSha256", "output_sha256", "outputSha256",
    "debugOverlaySha256", "front_sha256", "carrierShapeFidelity",
    # 아래 다섯은 완전성 시험이 잡아냈다 — 처음 손으로 적은 목록에서 빠져 있었다.
    "base_asset_id", "baseAssetId", "debug_asset_id", "debugAssetId",
    "pattern_model_asset_id", "patternModelAssetId",
    "pattern_model_sha256", "patternModelSha256",
    # 프롬프트 해시는 사용자 정보는 아니지만 내부 지문이다 — 제품 표면에 있을 이유가 없다.
    "promptSha256", "prompt_sha256",
    # 파이프라인 라우팅 정책 — 내부 운영 정보
    "pipelinePolicy", "pipeline_policy", "pipelineLane", "pipeline_lane",
    "riskScore", "risk_score", "modelTier", "model_tier", "generationPath",
    "generation_path", "truthPackageId", "truth_package_id",
    "generationOutputId", "generation_output_id",
})


def _scrub(value, depth: int = 0):
    """민감 키를 재귀로 지운 사본. 깊이 상한은 순환/과도한 중첩 방어."""
    if depth > 12:
        return value
    if isinstance(value, dict):
        return {k: _scrub(v, depth + 1) for k, v in value.items()
                if k not in _EVENT_SENSITIVE_KEYS}
    if isinstance(value, (list, tuple)):
        return [_scrub(v, depth + 1) for v in value]
    return value


def public_job_event_payload(payload):
    """잡 이벤트 → SSE 로 내보낼 페이로드.

    `qcScores` 를 좁혀 놓고 이벤트 스트림으로 같은 것을 흘리면 좁힌 의미가 없다.
    이벤트는 **기록은 그대로 두고**(내부 진단 가치가 있다) 나가는 자리에서만 좁힌다.
    """
    return _scrub(payload)


def public_qc_scores(qc_scores):
    """내부 QC 스냅샷 → 공개 페이로드. dict 가 아니면 그대로 돌려준다(legacy 관용)."""
    if not isinstance(qc_scores, dict):
        return qc_scores

    out = {k: v for k, v in qc_scores.items() if k not in _TOP_LEVEL_DROP}

    hybrid = qc_scores.get("hybridComposite")
    if isinstance(hybrid, dict):
        narrowed = {k: v for k, v in hybrid.items() if k in _HYBRID_ALLOW}
        projection = hybrid.get("textureProjection")
        if isinstance(projection, dict):
            narrowed["textureProjection"] = {
                k: v for k, v in projection.items() if k in _TEXTURE_PROJECTION_ALLOW}
        out["hybridComposite"] = narrowed
    elif "hybridComposite" in qc_scores:
        out["hybridComposite"] = hybrid

    structured = qc_scores.get("structuredQC")
    if isinstance(structured, dict):
        narrowed = {k: v for k, v in structured.items() if k in _STRUCTURED_ALLOW}
        if "checks" in structured:
            narrowed["checks"] = _project_checks(structured.get("checks"))
        out["structuredQC"] = narrowed
    elif "structuredQC" in qc_scores:
        out["structuredQC"] = structured

    return out
