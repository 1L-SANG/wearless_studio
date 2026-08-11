"""Comparative garment-fidelity QC — the semantic verdict, from a vision LLM.

Front / Back / Detail of the real product go in as ground truth, the generated mannequin cut
goes in last, and the model answers thirteen named checks with a status and an observation.
`services/garment_fidelity_authority` turns that into a decision; this module only obtains it.

Two deliberate choices:

* **The prompt is a file** (`prompts/garment_fidelity_qc_v1.txt`), like every other prompt in
  this codebase, so a wording change is reviewable as a diff and versioned by
  `PROMPT_VERSION`. The version travels with the verdict — a stored QC result that cannot say
  which prompt produced it cannot be re-compared later.
* **`validate` distrusts the model.** A status outside the enum becomes UNVERIFIABLE rather
  than being dropped; a missing check becomes UNVERIFIABLE rather than absent. The authority
  layer treats both as blocking, so a malformed response cannot buy a pass by omission.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Sequence
from dataclasses import dataclass

from ..services.garment_fidelity_authority import CHECKS, decide, normalise_status
from .gemini_image import InlineImage
from .vision_llm import VisionError, analyze_with_fallback

_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))  # server/
_PROMPT_FILE = os.path.join(_SERVER_DIR, "prompts", "garment_fidelity_qc_v1.txt")

PROMPT_VERSION = "garment_fidelity_qc_v1"

#: How each source slot is described to the judge. Fixed strings — seller text never lands here.
_SLOT_LABEL = {
    "Front": "SOURCE FRONT — the product photographed from the front. Ground truth for "
             "silhouette, neckline, closure, pattern layout and overall construction.",
    "Back": "SOURCE BACK — the product photographed from the back. Ground truth for back "
            "construction and pattern continuity.",
    "Detail": "SOURCE DETAIL — a close-up of the product. Ground truth for fabric appearance, "
              "colour, pattern pitch and trims.",
    "Fit": "SOURCE FIT REFERENCE — the product worn by a person. Ground truth for length and "
           "drape only; its colour and pattern are distorted by lighting.",
}
_SLOT_FALLBACK = "SOURCE photo of the product — ground truth."
_GENERATED_LABEL = (
    "GENERATED — the mannequin image under test. This is the ONLY image being judged."
)


@dataclass(frozen=True)
class SourceRef:
    """One ground-truth photo and the role it plays. Mirrors `ProductReference` without bytes ties."""

    slot: str
    image: InlineImage
    asset_id: str | None = None


#: The order the judge sees the sources in. Deliberately the human reading order — front, then
#: back, then the close-up — and NOT `product_reference.PATTERN_SOURCE_PRIORITY`, which puts the
#: macro shot first because a pattern extractor wants pitch before shape. A comparative judge
#: needs the whole garment established before it is shown a crop of it.
SOURCE_ORDER: tuple[str, ...] = ("Front", "Back", "Detail", "Fit")


def order_sources(refs: Sequence[SourceRef]) -> tuple[SourceRef, ...]:
    def rank(ref: SourceRef) -> int:
        try:
            return SOURCE_ORDER.index(ref.slot)
        except ValueError:
            return len(SOURCE_ORDER)
    return tuple(sorted(refs, key=rank))


#: Close-ups the judge is shown. Sellers upload as many detail shots as they like — the
#: control product has three — and every one of them is another full-resolution image in a
#: comparison prompt. One close-up establishes the fabric; the rest dilute attention across
#: crops of the same weave while the whole-garment questions (neckline, hem, silhouette) get
#: answered from a shrinking share of it. The generator still receives all of them; this
#: limit is the JUDGE's, and it exists because the judge is answering different questions.
MAX_DETAIL_SOURCES = 1


def select_qc_sources(refs: Sequence[SourceRef]) -> tuple[SourceRef, ...]:
    """Ordered, de-duplicated, and capped at one Detail. Applied inside `judge` so every
    caller — worker, harness, replay — asks the same question of the same evidence."""
    selected: list[SourceRef] = []
    seen_assets: set[str] = set()
    details = 0
    for ref in order_sources(refs):
        if ref.asset_id and ref.asset_id in seen_assets:
            continue
        if ref.slot == "Detail":
            details += 1
            if details > MAX_DETAIL_SOURCES:
                continue
        if ref.asset_id:
            seen_assets.add(ref.asset_id)
        selected.append(ref)
    return tuple(selected)


def build_manifest(refs: Sequence[SourceRef]) -> str:
    """Numbered roles, in the order the images are actually sent. A manifest that disagrees
    with the attachment order is worse than none — it relabels the evidence."""
    lines = ["IMAGE MANIFEST (in attachment order):"]
    for i, ref in enumerate(refs, start=1):
        lines.append(f"{i}. {_SLOT_LABEL.get(ref.slot, _SLOT_FALLBACK)}")
    lines.append(f"{len(refs) + 1}. {_GENERATED_LABEL}")
    return "\n".join(lines)


def load_template() -> str:
    with open(_PROMPT_FILE, encoding="utf-8") as f:
        return f.read()


def build_prompt(refs: Sequence[SourceRef], template: str | None = None) -> str:
    text = (template if template is not None else load_template())
    return text.replace("${imageManifest}", build_manifest(refs))


def _check_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["PASS", "FAIL", "UNVERIFIABLE"]},
            "evidence": {
                "type": "string",
                "description": "What you saw in the source and what you saw in the generated image.",
            },
        },
        "required": ["status", "evidence"],
        "additionalProperties": False,
    }


def schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "decision": {"type": "string", "enum": ["PASS", "FAIL", "UNVERIFIABLE"]},
            "checks": {
                "type": "object",
                "properties": {name: _check_schema() for name in CHECKS},
                "required": list(CHECKS),
                "additionalProperties": False,
            },
            "failureReasons": {"type": "array", "items": {"type": "string"}},
            "correctionInstruction": {"type": "string"},
            "confidence": {"type": "number"},
        },
        "required": ["decision", "checks", "failureReasons", "correctionInstruction", "confidence"],
        "additionalProperties": False,
    }


def validate(raw: dict) -> dict:
    """Coerce the response into the contract. Anything unreadable degrades to UNVERIFIABLE.

    Degrading rather than raising is the point: an unreadable `neckline` must still reach the
    authority layer as a blocking hard gate. Raising here would land in the caller's
    `except` and produce "QC errored", which is a different — and weaker — story than
    "the model did not answer the neckline question".
    """
    if not isinstance(raw, dict):
        raise VisionError(f"garment_fidelity_qc: dict 아님 {type(raw)}")
    raw_checks = raw.get("checks") if isinstance(raw.get("checks"), dict) else {}
    checks: dict[str, dict] = {}
    for name in CHECKS:
        node = raw_checks.get(name) if isinstance(raw_checks.get(name), dict) else {}
        checks[name] = {
            "status": normalise_status(node.get("status")),
            "evidence": str(node.get("evidence") or "")[:400],
        }
    reasons = [str(r)[:200] for r in (raw.get("failureReasons") or []) if str(r or "").strip()]
    confidence = raw.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        confidence = None
    else:
        confidence = max(0.0, min(1.0, float(confidence)))
    return {
        "decision": normalise_status(raw.get("decision")),
        "checks": checks,
        "failureReasons": reasons[:20],
        "correctionInstruction": str(raw.get("correctionInstruction") or "")[:1500],
        "confidence": confidence,
        "promptVersion": PROMPT_VERSION,
    }


def _sniff_mime(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"\x89PNG":
        return "image/png"
    return "image/png"


def as_inline(data: bytes, mime: str | None = None) -> InlineImage:
    return InlineImage(mime or _sniff_mime(data), data)


#: Sampling temperature for the judge. Zero, and not by taste.
#:
#: The first frozen-artifact run and the second disagreed on three of ten cases with byte-
#: identical inputs — including the flagship stripe redesign, which came back FAIL and then
#: PASS. At the provider default (~1.0) this call is a sample, and a sample is not a gate.
#: Extraction work can afford that variance; a decision that grants READY cannot.
JUDGE_TEMPERATURE = 0.0

#: How many independent verdicts one judgement is made of. More than one because temperature
#: zero reduces variance without abolishing it, and because the residual disagreement is not
#: random noise — it is the model attending to different parts of the garment each time.
DEFAULT_SAMPLES = 3


def merge_samples(verdicts: Sequence[dict]) -> dict:
    """Several verdicts -> one, fail-closed per check.

    A check is PASS only if EVERY sample called it PASS. If any sample saw a FAIL, the check
    is FAIL; otherwise if any saw UNVERIFIABLE, it is UNVERIFIABLE.

    Majority vote was the obvious alternative and is wrong here. The failure that costs money
    is a redesign that ships, and the observed disagreements were one-sided: a sample that
    noticed the missing rib texture, against two that did not look at the texture at all. A
    check that any careful look failed is not rescued by two less careful ones.

    The cost is a higher false-rejection rate, which is the direction this gate is allowed to
    be wrong in: a blocked cut is a regeneration, a shipped redesign is a returned order.
    """
    if not verdicts:
        raise VisionError("garment_fidelity_qc: 샘플이 없습니다.")
    checks: dict[str, dict] = {}
    for name in CHECKS:
        statuses = [(v.get("checks") or {}).get(name, {}).get("status") for v in verdicts]
        if "FAIL" in statuses:
            status = "FAIL"
        elif "UNVERIFIABLE" in statuses or None in statuses:
            status = "UNVERIFIABLE"
        else:
            status = "PASS"
        # keep the evidence from a sample that actually saw the problem, not the first one
        evidence = next(
            ((v.get("checks") or {}).get(name, {}).get("evidence", "")
             for v in verdicts
             if (v.get("checks") or {}).get(name, {}).get("status") == status
             and (v.get("checks") or {}).get(name, {}).get("evidence")),
            "")
        checks[name] = {"status": status, "evidence": evidence,
                        "sampleStatuses": statuses}
    reasons: list[str] = []
    for v in verdicts:
        for r in v.get("failureReasons") or []:
            if r not in reasons:
                reasons.append(r)
    instructions = [v.get("correctionInstruction") or "" for v in verdicts]
    confidences = [v.get("confidence") for v in verdicts
                   if isinstance(v.get("confidence"), (int, float))]
    # Per-sample outcome is DERIVED from that sample's checks, not read off its `decision`
    # field. The authority layer does not trust that field, and neither should the agreement
    # statistic — a sample that failed `neckline` and then wrote "PASS" disagreed with a
    # sample that passed everything, whatever the two `decision` strings say.
    sample_decisions = [decide(v).decision for v in verdicts]
    merged_decision = ("FAIL" if "FAIL" in sample_decisions
                       else "UNVERIFIABLE" if "UNVERIFIABLE" in sample_decisions else "PASS")
    return {
        "decision": merged_decision,
        "checks": checks,
        "failureReasons": reasons[:20],
        # the longest specific instruction carries the most named properties
        "correctionInstruction": max(instructions, key=len) if instructions else "",
        "confidence": (min(confidences) if confidences else None),
        "promptVersion": PROMPT_VERSION,
        "samples": len(verdicts),
        "sampleDecisions": sample_decisions,
        "modelSelfDecisions": [v.get("decision") for v in verdicts],
        "sampleAgreement": len(set(sample_decisions)) == 1,
    }


async def judge_once(
    settings, *, sources: Sequence[SourceRef], generated: InlineImage,
    thinking_level: str = "medium",
) -> tuple[dict, str]:
    """One provider call -> (validated verdict, provider name).

    `thinking_level` defaults to medium rather than the global low: the cheap tier is tuned for
    extraction, and this call is a comparison across thirteen properties in up to five images.

    Provider selection follows `ANALYSIS_MODEL_ORDER` instead of pinning Gemini the way the
    blinded structural judge does. That judge is an evaluator whose scores are compared across
    runs, so reproducibility beats availability; this one is a production gate, and a gate that
    cannot reach one provider should ask the other rather than block the cut.
    """
    refs = select_qc_sources(sources)
    if not refs:
        raise VisionError("garment_fidelity_qc: 원본 참조가 없습니다.")
    images = [ref.image for ref in refs]
    images.append(generated)
    raw, provider = await analyze_with_fallback(
        settings, build_prompt(refs), images, schema(),
        thinking_level=thinking_level, temperature=JUDGE_TEMPERATURE)
    return validate(raw), provider


async def judge(
    settings, *, sources: Sequence[SourceRef], generated: InlineImage,
    thinking_level: str = "medium", samples: int = DEFAULT_SAMPLES,
) -> tuple[dict, str]:
    """`samples` independent verdicts, merged fail-closed -> (verdict, provider).

    The samples run concurrently: they are independent by construction, and a gate that adds
    three round trips in series to every candidate is a gate someone turns off.

    If some samples fail at the provider, the survivors still decide. If they ALL fail, the
    error propagates — an unmeasured candidate must reach the caller as unmeasured.
    """
    n = max(1, int(samples))
    results = await asyncio.gather(
        *(judge_once(settings, sources=sources, generated=generated,
                     thinking_level=thinking_level) for _ in range(n)),
        return_exceptions=True)
    ok = [r for r in results if not isinstance(r, BaseException)]
    if not ok:
        raise next(r for r in results if isinstance(r, BaseException))
    verdicts = [v for v, _ in ok]
    provider = ok[0][1]
    if len(verdicts) == 1:
        merged = dict(verdicts[0])
        merged.update({"samples": 1,
                       "sampleDecisions": [decide(merged).decision],
                       "modelSelfDecisions": [merged.get("decision")],
                       "sampleAgreement": True})
        return merged, provider
    return merge_samples(verdicts), provider
