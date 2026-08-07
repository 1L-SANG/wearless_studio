"""Is this environment able to produce a controlled QA dataset — before a job is spent?

P3.5 found zero exact production replays in the whole history, and the reason was not that
the guided path never ran. It ran twice. What was missing was the torso ROI, because
`qa_flush` only writes when `HYBRID_COMPOSITE_ARTIFACT_DIR` is set, and in production it
is not. A QA run started with that variable unset spends provider calls and produces the
same unreplayable evidence again.

So this checks the environment rather than trusting it, and it is read-only: it inspects
config and module surfaces, never runs a job, never calls a provider, never writes except
the one temp file that proves the artifact directory is writable.

`ready()` is all-or-nothing on purpose — a dataset run with three of four captures is not
three quarters of a dataset, it is an unreplayable one.
"""
from __future__ import annotations

import dataclasses
import inspect
import os
from dataclasses import dataclass, field
from pathlib import Path

from . import image_budget
from .hybrid_composite import source_texture_context as hc_source_ctx
from .hybrid_composite import source_texture_qa as hc_source_qa
from .hybrid_composite import source_texture_resolver as hc_resolver
from .hybrid_composite import stripe_model as hc_stripe

ARTIFACT_DIR_ENV = "HYBRID_COMPOSITE_ARTIFACT_DIR"
_PROBE_NAME = ".controlled_qa_preflight_probe"


@dataclass
class PreflightReport:
    checks: dict = field(default_factory=dict)
    blocking_reasons: list = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return all(bool(v) for v in self.checks.values()) and not self.blocking_reasons

    def as_dict(self) -> dict:
        return {**self.checks, "ready": self.ready,
                "blockingReasons": list(self.blocking_reasons)}


def _artifact_dir_state(root: str | None) -> tuple[bool, bool, str | None]:
    """→ (set, writable, why not). Writability is proven by writing, not by stat bits."""
    if not root:
        return False, False, (
            f"{ARTIFACT_DIR_ENV} is unset — source torso ROI, source texture context and "
            "guided provenance would not reach disk, which is exactly why the two "
            "production guided runs of 2026-08-01 cannot be replayed")
    path = Path(root)
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / _PROBE_NAME
        probe.write_text("ok")
        probe.unlink()
    except Exception as exc:
        return True, False, f"{ARTIFACT_DIR_ENV}={root} is not writable: {exc!r}"
    return True, True, None


def _context_fields() -> set:
    return {f.name for f in dataclasses.fields(hc_source_ctx.SourceTextureContext)}


def _has_param(fn, name) -> bool:
    try:
        return name in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


def check(*, artifact_dir: str | None = None) -> PreflightReport:
    """Read the environment and report whether a dataset run is worth starting."""
    root = artifact_dir if artifact_dir is not None else os.getenv(ARTIFACT_DIR_ENV)
    dir_set, dir_writable, dir_reason = _artifact_dir_state(root)

    report = PreflightReport()
    report.checks = {
        # the budget this patch added — persistent because it lives on the job row
        "providerBudgetPersistent": (
            image_budget.METADATA_KEY == "imageBudget"
            and image_budget.MAX_TOTAL == 3),
        "imageCallCap": image_budget.MAX_TOTAL,
        "qaArtifactDirSet": dir_set,
        "qaArtifactDirWritable": dir_writable,
        # P1 — the run-level source reading. `source_torso_roi` is checked by name
        # because it is the one field whose absence made every historical guided run
        # unreplayable; the recorders take **fields, so the context is where it shows.
        "sourceTextureContextCapture": (
            hasattr(hc_source_ctx, "CONTEXT_VERSION")
            and hasattr(hc_source_ctx, "SLOT_SOURCE_PERIOD")
            and "source_torso_roi" in _context_fields()),
        # P2 — one directory per candidate/attempt under a lease-derived execution scope,
        # so a requeue cannot overwrite the previous execution's evidence
        "candidateAttemptExecutionScopeCapture": (
            _has_param(hc_source_qa.candidate_dir, "lease_token")
            and hasattr(hc_source_qa, "EXECUTION_SCOPE_LEASE_TOKEN")),
        # P3 — the losing guided candidates, and the multi-ROI shadow reading
        "guidedCandidateCapture": _has_param(hc_stripe.find_period_guided, "collect"),
        "shadowMultiRoiCapture": (
            hasattr(hc_resolver, "build_roi_family") and hasattr(hc_resolver, "resolve")),
    }
    if dir_reason:
        report.blocking_reasons.append(dir_reason)
    for name, ok in report.checks.items():
        if name in ("imageCallCap", "qaArtifactDirSet", "qaArtifactDirWritable"):
            continue
        if not ok:
            report.blocking_reasons.append(f"{name} is not available in this build")
    return report


def require(*, artifact_dir: str | None = None) -> PreflightReport:
    """Same check, but refuses to let a caller treat a partial environment as a dataset."""
    report = check(artifact_dir=artifact_dir)
    if not report.ready:
        raise RuntimeError(
            "controlled QA dataset run is not ready: " + "; ".join(report.blocking_reasons))
    return report
