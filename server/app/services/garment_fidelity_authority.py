"""Semantic garment fidelity — the decision, kept away from the model that produced it.

The judgement "is the generated garment still the same product?" is a *semantic* one. Pixel
statistics cannot answer it: a stripe rendered at the wrong pitch and a stripe rendered under
a fold differ by more in L2 than a square neckline differs from a round one. That is why the
verdict now comes from a vision LLM (`agents/garment_fidelity_qc.py`).

But a verdict is not authority. This module is the part that decides, and it is pure: no
provider, no database, no bytes. Three rules it exists to enforce.

**The model's own `decision` field never grants READY.** It is recorded, and it is compared,
but the effective decision is derived here from the per-check statuses. A model that fails
`neckline` and then writes `"decision": "PASS"` does not get to ship the cut.

**A hard gate that is UNVERIFIABLE blocks exactly like a FAIL.** "I could not see the sleeve"
is not permission to sell the cut; it is the absence of the evidence the gate exists to
require. Only `patternLayout` is soft — pattern *placement* legitimately moves with drape and
perspective, so an unverifiable layout is not evidence of a redesign. Pattern *presence*
(`patternType`) stays hard.

**A correction instruction has to name properties.** "Improve garment fidelity" tells the
image model nothing and produces a re-roll, which is how a bounded budget gets spent on noise.
`correction_instruction` is therefore built HERE from the failed checks, and the model's own
sentence is appended only when it survives `is_vague`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

VERSION = "garment_fidelity_authority_v1"

PASS = "PASS"
FAIL = "FAIL"
UNVERIFIABLE = "UNVERIFIABLE"
STATUSES = (PASS, FAIL, UNVERIFIABLE)

#: Every check the vision judge answers, in report order.
CHECKS: tuple[str, ...] = (
    "garmentIdentity",
    "category",
    "baseColor",
    "silhouette",
    "neckline",
    "sleeveConstruction",
    "majorConstruction",
    "protectedComponents",
    "patternType",
    "patternLayout",
    "materialAppearance",
    "lengthAndProportion",
    "generationArtifacts",
    "garmentBodyIntegration",
)

#: Checks whose failure — or whose unverifiability — must not become READY.
HARD_CHECKS: frozenset[str] = frozenset(CHECKS) - {"patternLayout"}

#: What the image model has to be told to restore, per property. Fixed strings: the seller's
#: own text never reaches a generation prompt through this path.
_CORRECTION_TEMPLATE = {
    "garmentIdentity": "restore the source garment's identity — this must be the same individual product",
    "category": "restore the source garment category",
    "baseColor": "restore the source base colour of the garment body",
    "silhouette": "restore the source silhouette and fit class",
    "neckline": "restore the source neckline shape and finish",
    "sleeveConstruction": "restore the source sleeve length class and sleeve construction",
    "majorConstruction": "restore the source seams, panel lines, darts, shirring and pleats",
    "protectedComponents": "restore the source buttons, placket, pockets, collar, cuffs and trims, "
                           "and remove any component that the source does not have",
    "patternType": "restore the source pattern class",
    "patternLayout": "restore the source pattern scale, direction and placement",
    "materialAppearance": "restore the source fabric appearance",
    "lengthAndProportion": "restore the source hem length and body proportions",
    "generationArtifacts": "render the garment as physically coherent cloth — no fusion into the "
                           "mannequin, no melted or duplicated construction",
    "garmentBodyIntegration": "render the garment as actually WORN — it must wrap and conform to "
                              "the mannequin's body, with natural contact at the neckline, "
                              "shoulders, armholes and torso. Remove any pasted, floating, "
                              "flat-sheet or superimposed appearance",
}

_PRESERVE_HEADER = (
    "Preserve the mannequin, the pose, the camera, the framing, the background and the lighting "
    "exactly as they are in image 1."
)
_FAIL_HEADER = "The garment in image 1 differs from the source product. Required corrections:"
_UNVERIFIABLE_HEADER = (
    "The garment in image 1 could not be verified against the source product. "
    "Re-render these properties so they are clearly visible and match the source:"
)

#: Sentences that name nothing. A correction made of these is a re-roll wearing a hat.
_VAGUE_MARKERS = (
    "look more like",
    "looks more like",
    "closer to the reference",
    "match the reference",
    "match the source better",
    "more faithful",
    "improve fidelity",
    "improve garment fidelity",
    "improve the fidelity",
    "improve accuracy",
    "be more accurate",
    "more accurate to the source",
    "fix the garment",
    "make it better",
    "higher quality",
)

#: A specific instruction has to mention at least one garment property by name.
_PROPERTY_WORDS = (
    "neckline", "neck", "collar", "sleeve", "cuff", "seam", "dart", "shirring", "pleat",
    "panel", "button", "placket", "zip", "zipper", "pocket", "hem", "length", "colour",
    "color", "stripe", "check", "pattern", "print", "embroidery", "eyelet", "lace",
    "rib", "knit", "satin", "sheer", "denim", "silhouette", "empire", "bust", "waist",
    "shoulder", "tie", "logo", "graphic", "fabric", "texture", "trim", "yoke", "gather",
)


def normalise_status(value) -> str:
    """Whatever the provider said, read as one of the three. Unknown reads as UNVERIFIABLE.

    Not as FAIL: an unparseable status is missing evidence, and calling it a redesign would
    put a fabricated failure reason into a correction prompt.
    """
    text = str(value or "").strip().upper()
    return text if text in STATUSES else UNVERIFIABLE


def check_status(verdict: Mapping | None, check: str) -> str:
    checks = (verdict or {}).get("checks")
    node = checks.get(check) if isinstance(checks, Mapping) else None
    if not isinstance(node, Mapping):
        return UNVERIFIABLE
    return normalise_status(node.get("status"))


def check_evidence(verdict: Mapping | None, check: str) -> str:
    checks = (verdict or {}).get("checks")
    node = checks.get(check) if isinstance(checks, Mapping) else None
    if not isinstance(node, Mapping):
        return ""
    return str(node.get("evidence") or "").strip()


def is_vague(instruction: str | None) -> bool:
    """Would this instruction tell an image model which property to change? If not, it is vague.

    Two ways to be vague: saying one of the known non-sentences, or naming no garment property
    at all. Both produce the same failure in practice — the editor re-rolls the whole garment.
    """
    text = str(instruction or "").strip().lower()
    if not text:
        return True
    if any(marker in text for marker in _VAGUE_MARKERS):
        return True
    return not any(word in text for word in _PROPERTY_WORDS)


@dataclass(frozen=True)
class FidelityDecision:
    """The derived verdict. `allowed` is the only thing authority should ever read."""

    allowed: bool
    decision: str
    model_decision: str
    failed_checks: tuple[str, ...] = ()
    unverifiable_checks: tuple[str, ...] = ()
    soft_issues: tuple[str, ...] = ()
    failure_reasons: tuple[str, ...] = ()
    confidence: float | None = None
    model_agreed: bool = True
    #: property-specific hard gates that were required of this product and how they answered.
    #: Empty on every product that activated none, which is most of them.
    required_gates: tuple[str, ...] = ()
    failed_gates: tuple[str, ...] = ()
    gate_statuses: tuple[tuple[str, str], ...] = ()
    version: str = VERSION

    def as_scores(self) -> dict:
        """The shape stored on `mannequin_cuts.qc_scores.garmentFidelityQc`. No bytes, no prompt."""
        return {
            "version": self.version,
            "decision": self.decision,
            "modelDecision": self.model_decision,
            "modelAgreed": self.model_agreed,
            "allowed": self.allowed,
            "failedChecks": list(self.failed_checks),
            "unverifiableChecks": list(self.unverifiable_checks),
            "softIssues": list(self.soft_issues),
            "failureReasons": list(self.failure_reasons),
            "confidence": self.confidence,
            "requiredGates": list(self.required_gates),
            "failedGates": list(self.failed_gates),
            "gateStatuses": dict(self.gate_statuses),
        }


def _confidence(verdict: Mapping | None) -> float | None:
    raw = (verdict or {}).get("confidence")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return max(0.0, min(1.0, float(raw)))


def decide(verdict: Mapping | None) -> FidelityDecision:
    """Per-check statuses -> may this candidate become READY.

    A missing verdict is not a pass. The caller decides whether the gate is enforced at all
    (`shadow` records and does not block); what it must never do is read "no verdict" as
    permission, because that is how an unmeasured cut shipped before.
    """
    if not isinstance(verdict, Mapping):
        return FidelityDecision(
            allowed=False, decision=UNVERIFIABLE, model_decision=UNVERIFIABLE,
            unverifiable_checks=tuple(sorted(HARD_CHECKS)), model_agreed=False)

    failed: list[str] = []
    unverifiable: list[str] = []
    soft: list[str] = []
    for check in CHECKS:
        status = check_status(verdict, check)
        if status == PASS:
            continue
        if check in HARD_CHECKS:
            (failed if status == FAIL else unverifiable).append(check)
        else:
            soft.append(f"{check}:{status}")

    if failed:
        decision = FAIL
    elif unverifiable:
        decision = UNVERIFIABLE
    else:
        decision = PASS

    model_decision = normalise_status((verdict or {}).get("decision"))
    reasons = tuple(
        str(r).strip()[:200]
        for r in ((verdict or {}).get("failureReasons") or [])
        if str(r or "").strip()
    )
    return FidelityDecision(
        allowed=decision == PASS,
        decision=decision,
        model_decision=model_decision,
        failed_checks=tuple(failed),
        unverifiable_checks=tuple(unverifiable),
        soft_issues=tuple(soft),
        failure_reasons=reasons,
        confidence=_confidence(verdict),
        model_agreed=model_decision == decision,
    )


def combine(general: FidelityDecision, *, required: Iterable[str] = (),
            gate_results: Mapping[str, Mapping] | None = None) -> FidelityDecision:
    """General verdict + property-specific gate answers -> the verdict that decides READY.

    One-directional, and that is the point of the whole patch: a passing general judge cannot
    rescue a failed specialized gate. The general judge looked at a full-body frame and said
    the stripe was fine; the stripe judge looked at a crop of the same shirt and counted the
    repeats. When they disagree, the one that could see wins.

    A required gate with no answer blocks too. "We meant to ask" is not an answer, and it is
    the shape a wiring bug takes.
    """
    required = tuple(dict.fromkeys(required))
    if not required:
        return general
    results = dict(gate_results or {})
    statuses: dict[str, str] = {}
    for gate in required:
        node = results.get(gate)
        statuses[gate] = str((node or {}).get("status") or UNVERIFIABLE)
    failed = tuple(g for g in required if statuses[g] not in (PASS, "NOT_APPLICABLE"))

    if failed:
        # FAIL when any gate actually contradicted the source; UNVERIFIABLE when the gates
        # only failed to establish anything. The distinction drives the correction: a
        # contradiction names a property to fix, an unverifiable does not.
        gate_decision = FAIL if any(statuses[g] == FAIL for g in failed) else UNVERIFIABLE
    else:
        gate_decision = PASS

    decision = general.decision
    if gate_decision == FAIL:
        decision = FAIL
    elif gate_decision == UNVERIFIABLE and decision == PASS:
        decision = UNVERIFIABLE

    return FidelityDecision(
        allowed=decision == PASS,
        decision=decision,
        model_decision=general.model_decision,
        failed_checks=general.failed_checks,
        unverifiable_checks=general.unverifiable_checks,
        soft_issues=general.soft_issues,
        failure_reasons=general.failure_reasons + tuple(
            f"{g}: {statuses[g].lower()}" for g in failed),
        confidence=general.confidence,
        model_agreed=general.model_agreed,
        required_gates=required,
        failed_gates=failed,
        gate_statuses=tuple(sorted(statuses.items())),
    )


#: What the image model must be told to restore, per specialized gate.
_GATE_CORRECTION = {
    "stripeFidelity": "restore the source stripe design — its stripe widths, spacing, "
                      "grouping, repeat density and stripe colours",
    "checkFidelity": "restore the source check design — its cell size, line thickness, line "
                     "colours, spacing and grouping",
    "surfaceTextureFidelity": "restore the source fabric's surface structure — its rib, "
                              "pointelle, waffle, pucker or shirring relief, as real cloth "
                              "structure and not as a printed effect",
    "laceStructureFidelity": "restore the source openwork structure — the hole size, spacing "
                             "and motif shape of the real lace or eyelet",
    "opacityFidelity": "restore the source fabric's opacity and the way light passes through it",
}


def gate_correction_lines(decision: FidelityDecision,
                          gate_results: Mapping[str, Mapping] | None = None) -> list[str]:
    lines = []
    results = dict(gate_results or {})
    for gate in decision.failed_gates:
        bullet = f"- {_GATE_CORRECTION.get(gate, f'restore the source {gate}')}"
        observed = str((results.get(gate) or {}).get("correctionInstruction") or "").strip()
        if observed and not is_vague(observed):
            bullet += f" ({observed[:300]})"
        lines.append(bullet)
    return lines


def correction_instruction(verdict: Mapping | None, decision: FidelityDecision | None = None,
                           gate_results: Mapping[str, Mapping] | None = None) -> str:
    """Name the failed properties, name the passed ones, and say what not to touch.

    Built from the checks rather than copied from the model, so the guarantee holds even when
    the model returns a useless sentence. The model's own sentence is appended only when it
    survives `is_vague` — it often carries the specific colour or shape name that the fixed
    templates cannot know.
    """
    decision = decision or decide(verdict)
    if decision.allowed:
        return ""

    gate_lines = gate_correction_lines(decision, gate_results)
    lines = [_PRESERVE_HEADER, ""]
    if decision.failed_checks or gate_lines:
        lines.append(_FAIL_HEADER)
        for check in decision.failed_checks:
            evidence = check_evidence(verdict, check)
            bullet = f"- {_CORRECTION_TEMPLATE[check]}"
            if evidence:
                bullet += f" ({evidence[:220]})"
            lines.append(bullet)
        lines.extend(gate_lines)
    if decision.unverifiable_checks:
        if decision.failed_checks:
            lines.append("")
        lines.append(_UNVERIFIABLE_HEADER)
        for check in decision.unverifiable_checks:
            lines.append(f"- {_CORRECTION_TEMPLATE[check]}")

    preserved = [c for c in CHECKS if check_status(verdict, c) == PASS]
    if preserved:
        lines.append("")
        lines.append(
            "These properties are already correct — do not alter them: "
            + ", ".join(preserved) + "."
        )

    model_line = str((verdict or {}).get("correctionInstruction") or "").strip()
    if model_line and not is_vague(model_line):
        lines.append("")
        lines.append("Observed detail: " + model_line[:600])
    return "\n".join(lines)


@dataclass(frozen=True)
class CandidateAuthority:
    """Whether this candidate may be consumed, and what it would cost to say yes."""

    allowed: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)


REASON_SEMANTIC_FAIL = "garment_fidelity_failed"
REASON_SEMANTIC_UNVERIFIABLE = "garment_fidelity_unverifiable"
REASON_SEMANTIC_NOT_MEASURED = "garment_fidelity_enforced_but_not_measured"


def authority_reason(scores: Mapping | None, *, mode: str) -> str | None:
    """`qc_scores.garmentFidelityQc` -> the reason this cut may not be consumed, or None.

    `mode` is the rollout flag as it was AT THE TIME the cut ran, which is why it is stored on
    the snapshot rather than re-read from settings: flipping the flag must not retroactively
    block cuts that were produced when the gate was observational.
    """
    if mode != "enforce":
        return None
    if not isinstance(scores, Mapping):
        return REASON_SEMANTIC_NOT_MEASURED
    if scores.get("errored") is True:
        return REASON_SEMANTIC_NOT_MEASURED
    decision = normalise_status(scores.get("decision"))
    if decision == PASS:
        return None
    return REASON_SEMANTIC_FAIL if decision == FAIL else REASON_SEMANTIC_UNVERIFIABLE
