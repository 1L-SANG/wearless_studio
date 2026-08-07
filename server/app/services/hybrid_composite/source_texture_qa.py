"""Where a run's source-texture evidence goes, and what shape it takes.

The existing QA dump writes `carrier.png` and `geometry.json` straight into the
root of the artifact directory. Candidate B overwrites candidate A, and the next
job overwrites both — which is how a real investigation lost the exact ROIs that
produced sourcePeriodPx 30.0 for one candidate and 15.0 for the next.

Everything here is diagnostic. It writes only under an explicitly configured QA
directory, never to R2 or the database, and every write is best-effort: a failure
to record evidence must never change what the pipeline decides.

This module holds paths and payload shapes only — no measurement happens here, so
recording cannot alter what is being recorded.
"""

from __future__ import annotations

import hashlib
import json
import logging
import pathlib
from typing import Any, Mapping

log = logging.getLogger(__name__)

CONTEXT_SCHEMA = "source_texture_context_qa_v1"
PROVENANCE_SCHEMA = "source_texture_candidate_provenance_v1"

#: `find_period_guided` returns only its winning (axis, period, score). The losing
#: candidates and their scores exist inside the scorer loop and are dropped. They
#: are worth having for the harmonic work, but obtaining them would mean running
#: the scorer a second time or changing the detector — both out of scope here, and
#: the second run would be new CV work rather than an observation of the real one.
GUIDED_CANDIDATES_UNAVAILABLE = "current_runtime_does_not_expose_candidate_scores"


#: What makes one worker execution distinct from the next one for the same job.
#: Candidate letters come from a literal ("A", "B") and attempt numbers from
#: range(1, ...), so both reset to their first value when a job is requeued after
#: a lease expiry. Without a third identifier, execution #2 of job J writes over
#: execution #1's candidate-A/attempt-1 evidence.
#:
#: The lease token is the identity the runtime already mints per claim
#: (worker_id:uuid4, repo.claim_next_job). Only its digest goes on disk: the raw
#: token is what fences job finalisation, and a short hash is enough to separate
#: two executions.
EXECUTION_SCOPE_LEASE_TOKEN = "lease_token_sha256"
EXECUTION_SCOPE_NONE = "unscoped"
UNSCOPED_EXECUTION = "execution-unscoped"


def execution_scope(lease_token: Any) -> tuple[str, str]:
    """(path segment, scope type). Never writes the raw lease token."""
    if not lease_token:
        return UNSCOPED_EXECUTION, EXECUTION_SCOPE_NONE
    digest = hashlib.sha256(str(lease_token).encode()).hexdigest()[:12]
    return f"execution-{digest}", EXECUTION_SCOPE_LEASE_TOKEN


def candidate_dir(root: str, job_id: Any, candidate: Any, attempt: Any,
                  lease_token: Any = None) -> pathlib.Path:
    """<root>/<job>/<execution>/candidate-<X>/attempt-<N>/ — one leaf per execution.

    Every identifier is in the path, so two candidates of one job, two attempts of
    one candidate, and two executions of one job cannot land on the same file.
    """
    scope, _ = execution_scope(lease_token)
    return (pathlib.Path(root) / _safe(job_id) / _safe(scope)
            / f"candidate-{_safe(candidate)}" / f"attempt-{_safe(attempt)}")


def context_dir(root: str, job_id: Any, lease_token: Any = None) -> pathlib.Path:
    """Run-level: the one source reading every candidate of THIS execution shares.

    Scoped the same way. A requeued job resolves its own context, and overwriting
    the previous execution's snapshot would erase the very comparison this exists
    to support.
    """
    scope, _ = execution_scope(lease_token)
    return (pathlib.Path(root) / _safe(job_id) / _safe(scope)
            / "source_texture" / "context")


def _safe(value: Any) -> str:
    """Path-safe identifier. Never let a stray separator escape the QA root."""
    text = "unknown" if value is None else str(value)
    keep = [c if (c.isalnum() or c in "-_.") else "_" for c in text]
    return ("".join(keep) or "unknown")[:120]


def write_json(path: pathlib.Path, payload: Mapping) -> str | None:
    """Best-effort JSON write. Returns an error string instead of raising.

    Callers are inside the generation path; an unwritable QA directory must cost
    a log line, not a candidate.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=1,
                                   sort_keys=True, default=str))
        return None
    except Exception as exc:
        log.warning("source texture QA write skipped: %r", exc)
        return repr(exc)[:300]


def context_payload(context, cache, *, job_id: Any, lease_token: Any = None) -> dict:
    """The run-scoped reading. Absent values stay null and say why."""
    scope, scope_type = execution_scope(lease_token)
    if context is None:
        return {
            "schema": CONTEXT_SCHEMA, "jobId": _null_or(job_id),
            "executionScope": scope, "executionScopeType": scope_type,
            "contextId": None,
            "contextMissingReason": "source texture context was not resolved for this run",
            "computeCounts": dict(getattr(cache, "compute_counts", {}) or {}),
            "reuseCounts": dict(getattr(cache, "reuse_counts", {}) or {}),
            "periodDisagreements": getattr(cache, "period_disagreements", 0),
        }
    return {
        "schema": CONTEXT_SCHEMA,
        "jobId": _null_or(job_id),
        "executionScope": scope,
        "executionScopeType": scope_type,
        "contextId": context.context_id(),
        "sourceSha256": context.source_sha256,
        "sourceRoi": list(context.source_torso_roi),
        "sourcePeriodPx": round(float(context.source_period_px), 4),
        "sourcePeriodSource": context.source_period_source,
        "sourceModelConfidence": _round(context.source_model_confidence),
        "garmentAxis": context.garment_axis,
        "sourceTorsoSpanPx": round(float(context.torso_span_px), 3),
        "patternModel": {
            "slot": context.pattern_model_slot,
            "assetId": context.pattern_model_asset_id,
            "sha256": context.pattern_model_sha256,
            "roi": list(context.pattern_model_roi) if context.pattern_model_roi else None,
        },
        "detailValidationOk": context.detail_validation_ok,
        "extractorVersion": context.extractor_version,
        "computeCounts": dict(getattr(cache, "compute_counts", {}) or {}),
        "reuseCounts": dict(getattr(cache, "reuse_counts", {}) or {}),
        "periodDisagreements": getattr(cache, "period_disagreements", 0),
    }


def provenance_payload(**fields) -> dict:
    """One candidate/attempt execution.

    Source-only and candidate-dependent numbers are named apart on purpose:
    `sourcePeriodPx` is a property of the product photo and must match across
    candidates, while `targetPeriodPx` is derived from this carrier and must not.
    """
    scan = dict(fields.get("scan") or {})
    guided = dict(fields.get("guided") or {})
    guided.setdefault("candidates", None)
    if guided.get("candidates") is None:
        guided["candidatesMissingReason"] = GUIDED_CANDIDATES_UNAVAILABLE
    scope, scope_type = execution_scope(fields.get("lease_token"))
    return {
        "schema": PROVENANCE_SCHEMA,
        "jobId": _null_or(fields.get("job_id")),
        "executionScope": scope,
        "executionScopeType": scope_type,
        "candidate": _null_or(fields.get("candidate")),
        "attempt": fields.get("attempt"),
        "contextId": fields.get("context_id"),

        "sourceSha256": fields.get("source_sha256"),
        "carrierSha256": fields.get("carrier_sha256"),

        # --- source-only: identical for every candidate of this run
        "sourceRoi": _list_or(fields.get("source_roi")),
        "sourcePeriodPx": _round(fields.get("source_period_px"), 4),
        "sourcePeriodSource": fields.get("source_period_source"),
        "sourceModelConfidence": _round(fields.get("source_model_confidence")),
        "sourceTorsoSpanPx": _round(fields.get("source_torso_span_px"), 3),

        # --- candidate-dependent: expected to differ between candidates
        "carrierTargetSpanPx": _round(fields.get("carrier_target_span_px"), 3),
        "repeatCount": _round(fields.get("repeat_count"), 3),
        "targetPeriodPx": _round(fields.get("target_period_px"), 3),

        "projectionDecision": fields.get("projection_decision"),
        "projectionConfidence": _round(fields.get("projection_confidence")),
        "projectionConfidenceComponents": {
            "sourceModelConfidence": _round(_metric(fields, "sourceModelConfidence")),
            "repeatConfidence": _round(_metric(fields, "repeatConfidence")),
            "periodConfidence": _round(_metric(fields, "periodConfidence")),
            "scaleConfidence": _round(_metric(fields, "scaleConfidence")),
        },

        "scan": {
            "attempted": bool(scan.get("attempted")),
            "success": scan.get("success"),
            "periodPx": _round(scan.get("periodPx"), 4),
            "confidence": _round(scan.get("confidence")),
            "axis": scan.get("axis"),
            "failureReason": scan.get("failureReason"),
        },
        "guided": {
            "attempted": bool(guided.get("attempted")),
            "selectedPeriodPx": _round(guided.get("selectedPeriodPx"), 4),
            "selectedScore": _round(guided.get("selectedScore")),
            "candidates": guided.get("candidates"),
            "candidatesMissingReason": guided.get("candidatesMissingReason"),
        },

        "failureReason": fields.get("failure_reason"),
        "failureDetail": fields.get("failure_detail"),

        # never claim more than a local file proves
        "qaCaptureImplemented": True,
        "productionPersistenceImplemented": False,
    }


def _metric(fields: Mapping, key: str):
    metrics = fields.get("projection_metrics") or {}
    return metrics.get(key) if isinstance(metrics, Mapping) else None


def _round(value, digits: int = 4):
    return round(float(value), digits) if isinstance(value, (int, float)) else None


def _list_or(value):
    return list(value) if isinstance(value, (list, tuple)) else None


def _null_or(value):
    return None if value is None else str(value)
