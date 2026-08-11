"""Generate -> semantic QC -> targeted correction -> semantic QC, as one reusable pass.

The loop itself is small; what it is worth having as a module is that the QA harness and the
production worker run the SAME loop. A harness that reimplements the sequence proves that the
harness works. This one takes the provider calls as arguments — `judge_fn` obtains a verdict,
`correct_fn` produces a corrected image — so the worker can bind them to the budget-gated,
lease-owning production calls while the harness binds them to its own accounting, and neither
copy of the control flow can drift from the other.

Three rules the loop encodes:

* **Correction runs at most once.** Not "until it passes": a bounded image budget has exactly
  one TARGETED_CORRECTION slot, and a second correction would either exceed it or silently
  steal the regeneration slot. `correct_fn` returning None (budget denied, provider failed) is
  a normal outcome, not an error — the pass ends with the C0 verdict standing.
* **The last verdict is the verdict.** If C1 comes back worse than C0, the authority reads C1,
  because C1 is the image that would ship. There is no "best of" here; picking the better
  verdict while shipping the other image is exactly how an unmeasured cut gets sold.
* **A QC failure is not a pass.** If `judge_fn` raises, the snapshot records `errored: True`,
  and `garment_fidelity_authority.authority_reason` turns that into a block under enforce.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from . import garment_fidelity_authority as gfa

_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))  # server/
_CORRECTION_PROMPT_FILE = os.path.join(
    _SERVER_DIR, "prompts", "garment_fidelity_correction_v1.txt")

CORRECTION_PROMPT_VERSION = "garment_fidelity_correction_v1"

MODE_OFF = "off"
MODE_SHADOW = "shadow"
MODE_ENFORCE = "enforce"

LABEL_BASE = "C0"
LABEL_CORRECTED = "C1"


def load_correction_template() -> str:
    with open(_CORRECTION_PROMPT_FILE, encoding="utf-8") as f:
        return f.read()


def render_correction_prompt(instruction: str, manifest: str,
                             template: str | None = None) -> str:
    text = template if template is not None else load_correction_template()
    return (text.replace("${imageManifest}", manifest)
                .replace("${correctionInstruction}", instruction))


def build_correction_manifest(slots: Sequence[str]) -> str:
    """1 = the cut being edited, 2.. = the source photos in the order they are attached."""
    label = {
        "Front": "SOURCE FRONT photo of the product — authority for silhouette, neckline, "
                 "closure and construction",
        "Back": "SOURCE BACK photo of the product — authority for back construction",
        "Detail": "SOURCE DETAIL close-up of the product — authority for fabric, colour and "
                  "pattern pitch",
        "Fit": "SOURCE FIT reference (worn by a person) — length and drape only",
    }
    lines = ["1. CURRENT CUT — the mannequin photo to edit"]
    for i, slot in enumerate(slots, start=2):
        lines.append(f"{i}. {label.get(slot, 'SOURCE photo of the product')}")
    return "\n".join(lines)


@dataclass(frozen=True)
class FidelityAttempt:
    """One judged candidate. `image` is what was judged, so a report can show the two apart."""

    label: str
    image: Any = None
    verdict: dict | None = None
    decision: gfa.FidelityDecision | None = None
    provider: str | None = None
    errored: bool = False
    error: str | None = None
    correction_instruction: str = ""
    #: property-specific gate answers for this candidate, keyed by gate id
    gate_results: dict = field(default_factory=dict)
    gate_error: str | None = None

    def as_dict(self) -> dict:
        return {
            "label": self.label,
            "errored": self.errored,
            "error": self.error,
            "provider": self.provider,
            "decision": (self.decision.decision if self.decision
                         else gfa.UNVERIFIABLE),
            "modelDecision": (self.decision.model_decision if self.decision
                              else gfa.UNVERIFIABLE),
            "modelAgreed": bool(self.decision.model_agreed) if self.decision else False,
            "allowed": bool(self.decision.allowed) if self.decision else False,
            "failedChecks": list(self.decision.failed_checks) if self.decision else [],
            "unverifiableChecks": (list(self.decision.unverifiable_checks)
                                   if self.decision else []),
            "softIssues": list(self.decision.soft_issues) if self.decision else [],
            "failureReasons": list(self.decision.failure_reasons) if self.decision else [],
            "confidence": self.decision.confidence if self.decision else None,
            "checks": (self.verdict or {}).get("checks") or {},
            "correctionInstruction": self.correction_instruction,
            "promptVersion": (self.verdict or {}).get("promptVersion"),
            "requiredGates": (list(self.decision.required_gates) if self.decision else []),
            "failedGates": (list(self.decision.failed_gates) if self.decision else []),
            "gateStatuses": (dict(self.decision.gate_statuses) if self.decision else {}),
            "gateResults": self.gate_results,
            "gateError": self.gate_error,
        }


@dataclass
class FidelityPassResult:
    mode: str
    attempts: list[FidelityAttempt] = field(default_factory=list)
    #: judgements made — one per candidate judged
    vision_calls: int = 0
    #: provider requests behind those judgements. Not the same number: one judgement is
    #: several independent samples merged fail-closed, and the cost report has to say so.
    vision_provider_calls: int = 0
    correction_calls: int = 0
    correction_denied_reason: str | None = None

    @property
    def final(self) -> FidelityAttempt | None:
        return self.attempts[-1] if self.attempts else None

    @property
    def final_image(self):
        return self.final.image if self.final else None

    @property
    def allowed(self) -> bool:
        """Under shadow this is advisory; the authority layer only reads the stored mode."""
        final = self.final
        return bool(final and final.decision and final.decision.allowed)

    def as_scores(self) -> dict:
        """`qc_scores.garmentFidelityQc`. Carries the mode it ran under — the authority layer
        must judge a cut by the contract it was made under, not by today's flag."""
        final = self.final
        errored = bool(final.errored) if final else True
        base = {
            "version": gfa.VERSION,
            "mode": self.mode,
            "errored": errored,
            "visionCalls": self.vision_calls,
            "visionProviderCalls": self.vision_provider_calls,
            "correctionCalls": self.correction_calls,
            "correctionDeniedReason": self.correction_denied_reason,
            "attempts": [a.as_dict() for a in self.attempts],
            "correctionPromptVersion": CORRECTION_PROMPT_VERSION,
        }
        if final and final.decision and not errored:
            base.update({
                "decision": final.decision.decision,
                "modelDecision": final.decision.model_decision,
                "modelAgreed": final.decision.model_agreed,
                "allowed": final.decision.allowed,
                "failedChecks": list(final.decision.failed_checks),
                "unverifiableChecks": list(final.decision.unverifiable_checks),
                "failureReasons": list(final.decision.failure_reasons),
                "confidence": final.decision.confidence,
                "correctionInstruction": final.correction_instruction,
                "requiredGates": list(final.decision.required_gates),
                "failedGates": list(final.decision.failed_gates),
                "gateStatuses": dict(final.decision.gate_statuses),
            })
        else:
            base.update({
                "decision": gfa.UNVERIFIABLE,
                "modelDecision": gfa.UNVERIFIABLE,
                "modelAgreed": False,
                "allowed": False,
                "failedChecks": [],
                "unverifiableChecks": sorted(gfa.HARD_CHECKS),
                "failureReasons": [],
                "confidence": None,
                "correctionInstruction": "",
            })
        return base


async def _judge_once(judge_fn, image, label: str, *, required_gates=(),
                      specialized_fn=None) -> FidelityAttempt:
    try:
        verdict, provider = await judge_fn(image)
    except Exception as exc:                      # noqa: BLE001 — recorded, never swallowed
        return FidelityAttempt(label=label, image=image, errored=True,
                               error=f"{type(exc).__name__}: {str(exc)[:200]}")
    decision = gfa.decide(verdict)
    gate_results: dict = {}
    gate_error = None
    if required_gates and specialized_fn is not None:
        try:
            gate_results = await specialized_fn(image) or {}
        except Exception as exc:                  # noqa: BLE001
            # A required gate that could not be asked is not a gate that passed. Recorded as
            # an error AND left unanswered, so `combine` blocks on the missing answer.
            gate_error = f"{type(exc).__name__}: {str(exc)[:200]}"
    decision = gfa.combine(decision, required=required_gates, gate_results=gate_results)
    return FidelityAttempt(
        label=label, image=image, verdict=verdict, decision=decision, provider=provider,
        gate_results=gate_results, gate_error=gate_error,
        correction_instruction=gfa.correction_instruction(verdict, decision, gate_results))


async def run_fidelity_pass(
    *,
    mode: str,
    candidate,
    judge_fn: Callable[[Any], Awaitable[tuple[dict, str]]],
    correct_fn: Callable[[str, Any], Awaitable[Any]] | None = None,
    emit: Callable[[dict], Awaitable[None]] | None = None,
    required_gates: Sequence[str] = (),
    specialized_fn: Callable[[Any], Awaitable[dict]] | None = None,
) -> FidelityPassResult | None:
    """Judge the candidate; under enforce, correct once and judge again.

    Returns None when the gate is off, which is the caller's signal to write nothing — an
    absent snapshot and a snapshot saying "off" are different facts about a cut.
    """
    if mode not in (MODE_SHADOW, MODE_ENFORCE):
        return None
    result = FidelityPassResult(mode=mode)

    first = await _judge_once(judge_fn, candidate, LABEL_BASE,
                              required_gates=required_gates, specialized_fn=specialized_fn)
    result.attempts.append(first)
    result.vision_calls += 1
    result.vision_provider_calls += int((first.verdict or {}).get("samples") or 1)
    if emit:
        await emit({"status": "garment_fidelity_qc", "attempt": LABEL_BASE,
                    **{k: v for k, v in first.as_dict().items() if k != "checks"}})

    if mode != MODE_ENFORCE or first.errored or (first.decision and first.decision.allowed):
        return result
    if correct_fn is None:
        result.correction_denied_reason = "no_correction_path"
        return result

    corrected = await correct_fn(first.correction_instruction, candidate)
    if corrected is None:
        # Budget denied or the provider failed. The C0 verdict stands and still blocks.
        result.correction_denied_reason = result.correction_denied_reason or "correction_unavailable"
        return result
    result.correction_calls += 1

    second = await _judge_once(judge_fn, corrected, LABEL_CORRECTED,
                               required_gates=required_gates, specialized_fn=specialized_fn)
    result.attempts.append(second)
    result.vision_calls += 1
    result.vision_provider_calls += int((second.verdict or {}).get("samples") or 1)
    if emit:
        await emit({"status": "garment_fidelity_qc", "attempt": LABEL_CORRECTED,
                    **{k: v for k, v in second.as_dict().items() if k != "checks"}})
    return result
