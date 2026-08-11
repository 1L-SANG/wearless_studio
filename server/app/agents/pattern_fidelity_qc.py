"""Property-specific judges — one narrow question each, asked of a crop instead of a body.

The general thirteen-check judge and these judges differ in two ways that matter, and only
those two:

* **What they are shown.** The general judge sees whole photographs, because "is this the same
  product" is a whole-garment question. These judges are shown crops at native resolution,
  because "is this the same stripe" is answered in the six pixels the full frame throws away.
  `services/pattern_evidence` makes the crops; nothing here measures them.

* **What they are asked.** Not "does the pattern match" — that is the question the general
  judge already answered PASS on a shirt whose stripe had been replaced. These prompts
  enumerate the properties a stripe or a grid or a rib actually has (density, spacing, relative
  width, colour, grouping) and require a separate observation for each.

Everything else is deliberately identical to the general judge: same temperature, same sample
count, same fail-closed merge. The live stripe returned PASS three times out of three, so more
samples were never going to fix it — the evidence and the question were wrong, not the vote.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Sequence
from dataclasses import dataclass, field

from ..services import pattern_fidelity_gates as gates
from .garment_fidelity_qc import JUDGE_TEMPERATURE, as_inline
from .gemini_image import InlineImage
from .vision_llm import VisionError, analyze_with_fallback

_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))  # server/

PROMPT_VERSION = "pattern_fidelity_qc_v1"

#: Same as the general judge. Kept as an explicit constant rather than imported so that
#: changing one does not silently change the other — they are calibrated separately.
DEFAULT_SAMPLES = 3


@dataclass(frozen=True)
class Evidence:
    """One attachment and the sentence that tells the model what it is looking at."""

    label: str
    image: InlineImage


@dataclass(frozen=True)
class JudgeSpec:
    name: str
    prompt_file: str
    properties: tuple[str, ...]
    #: field name in the response -> gate id it decides
    status_fields: dict = field(default_factory=dict)
    allow_not_applicable: bool = False
    #: gate id -> the properties that gate is derived from. A judge that owns two gates must
    #: keep them apart: the lace judge answers openwork AND opacity, and deriving both from
    #: all six properties made a failed hole-size block the opacity gate and vice versa —
    #: two independent facts collapsed into one verdict.
    gate_properties: dict = field(default_factory=dict)

    def properties_for(self, gate: str) -> tuple[str, ...]:
        return tuple(self.gate_properties.get(gate) or self.properties)

    def gate_of(self, prop: str) -> str:
        """Which gate this property decides. Falls back to the judge's first gate.

        Needed once a judge owns more than one gate: the bottom judge owns eight, and seven of
        them refuse NOT_APPLICABLE while `skirtWrapOrSlitStructure` allows it. Normalising
        every property against one arbitrary owning gate would strip that allowance.
        """
        for gate, props in self.gate_properties.items():
            if prop in props:
                return gate
        return next(iter(self.status_fields.values()))


SPECS: dict[str, JudgeSpec] = {
    gates.JUDGE_STRIPE: JudgeSpec(
        name=gates.JUDGE_STRIPE,
        prompt_file="pattern_fidelity_stripe_v1.txt",
        properties=("presence", "orientation", "density", "spacing", "relativeWidth",
                    "dominantColors", "repeatScale", "character", "consistency",
                    "perceivedAsSameDesign"),
        status_fields={"overallStatus": gates.GATE_STRIPE},
        allow_not_applicable=False,
    ),
    gates.JUDGE_CHECK: JudgeSpec(
        name=gates.JUDGE_CHECK,
        prompt_file="pattern_fidelity_check_v1.txt",
        properties=("presence", "horizontalSpacing", "verticalSpacing", "cellScale",
                    "lineThickness", "lineColorCombination", "density", "regularity"),
        status_fields={"overallStatus": gates.GATE_CHECK},
        allow_not_applicable=False,
    ),
    gates.JUDGE_SURFACE_TEXTURE: JudgeSpec(
        name=gates.JUDGE_SURFACE_TEXTURE,
        prompt_file="pattern_fidelity_surface_texture_v1.txt",
        properties=("structurePresence", "structureType", "structureScale", "structureDepth",
                    "surfaceFinish"),
        status_fields={"overallStatus": gates.GATE_SURFACE_TEXTURE},
        allow_not_applicable=True,
    ),
    gates.JUDGE_LACE_OPACITY: JudgeSpec(
        name=gates.JUDGE_LACE_OPACITY,
        prompt_file="pattern_fidelity_lace_opacity_v1.txt",
        properties=("openworkPresence", "openworkType", "openworkScale", "motifShape",
                    "showThroughEvidence", "opacityLevel", "lightTransmission"),
        status_fields={"laceStructureStatus": gates.GATE_LACE_STRUCTURE,
                       "opacityStatus": gates.GATE_OPACITY},
        allow_not_applicable=True,
        gate_properties={
            gates.GATE_LACE_STRUCTURE: ("openworkPresence", "openworkType",
                                        "openworkScale", "motifShape"),
            gates.GATE_OPACITY: ("showThroughEvidence", "opacityLevel",
                                 "lightTransmission"),
        },
    ),
}



def _one_to_one(pairs):
    """gate id == property name == "<name>Status" field. Keeps the 8 bottom gates independent."""
    return (tuple(p for _g, p in pairs),
            {f"{p}Status": g for g, p in pairs},
            {g: (p,) for g, p in pairs})


_B = [(gates.GATE_WAIST_PLACEMENT, "waistPlacement"),
      (gates.GATE_LEG_SILHOUETTE, "legSilhouette"),
      (gates.GATE_OUTSEAM_LENGTH, "outseamLength"),
      (gates.GATE_CLOSURE_WAISTBAND, "closureAndWaistband"),
      (gates.GATE_HEM_SHAPE, "hemShape"),
      (gates.GATE_BODY_INTEGRATION_LOWER, "garmentBodyIntegrationLower"),
      (gates.GATE_FRONT_PANEL_SYMMETRY, "frontPanelSymmetry"),
      (gates.GATE_SKIRT_WRAP_SLIT, "skirtWrapOrSlitStructure")]
_BP, _BS, _BG = _one_to_one(_B)

_O = [(gates.GATE_CLOSURE_STATE, "closureState"),
      (gates.GATE_FRONT_OPENING, "frontOpeningControl"),
      (gates.GATE_ZIPPER_ALIGNMENT, "zipperAlignment"),
      (gates.GATE_COLLAR_PLACKET, "collarAndFrontPlacketIntegrity")]
_OP, _OS, _OG = _one_to_one(_O)

SPECS[gates.JUDGE_BOTTOM] = JudgeSpec(
    name=gates.JUDGE_BOTTOM, prompt_file="bottom_fidelity_qc_v1.txt",
    properties=_BP, status_fields=_BS, gate_properties=_BG,
    # only skirtWrapOrSlitStructure may actually use it; `normalise_status` strips it from
    # the seven strict gates, so offering it in the schema costs nothing.
    allow_not_applicable=True)

SPECS[gates.JUDGE_OUTER] = JudgeSpec(
    name=gates.JUDGE_OUTER, prompt_file="outer_fidelity_qc_v1.txt",
    properties=_OP, status_fields=_OS, gate_properties=_OG,
    allow_not_applicable=False)


def load_template(spec: JudgeSpec) -> str:
    with open(os.path.join(_SERVER_DIR, "prompts", spec.prompt_file), encoding="utf-8") as f:
        return f.read()


def build_manifest(evidence: Sequence[Evidence]) -> str:
    lines = ["IMAGE MANIFEST (in attachment order):"]
    for i, item in enumerate(evidence, start=1):
        lines.append(f"{i}. {item.label}")
    return "\n".join(lines)


def build_prompt(spec: JudgeSpec, evidence: Sequence[Evidence],
                 template: str | None = None) -> str:
    text = template if template is not None else load_template(spec)
    return text.replace("${imageManifest}", build_manifest(evidence))


def _statuses(spec: JudgeSpec) -> list[str]:
    base = ["PASS", "FAIL", "UNVERIFIABLE"]
    return base + ["NOT_APPLICABLE"] if spec.allow_not_applicable else base


def schema(spec: JudgeSpec) -> dict:
    prop_schema = {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": _statuses(spec)},
            "sourceObservation": {"type": "string"},
            "generatedObservation": {"type": "string"},
        },
        "required": ["status", "sourceObservation", "generatedObservation"],
        "additionalProperties": False,
    }
    props = {name: dict(prop_schema) for name in spec.properties}
    for field_name in spec.status_fields:
        props[field_name] = {"type": "string", "enum": _statuses(spec)}
    props["evidence"] = {"type": "string"}
    props["correctionInstruction"] = {"type": "string"}
    props["confidence"] = {"type": "number"}
    return {
        "type": "object",
        "properties": props,
        "required": list(spec.properties) + list(spec.status_fields)
                    + ["evidence", "correctionInstruction", "confidence"],
        "additionalProperties": False,
    }


def validate(spec: JudgeSpec, raw: dict) -> dict:
    """Coerce into the contract; unreadable degrades to UNVERIFIABLE, which blocks.

    The per-property statuses are normalised against the OWNING GATE, so a strict gate cannot
    be exempted by a property answering NOT_APPLICABLE.
    """
    if not isinstance(raw, dict):
        raise VisionError(f"pattern_fidelity_qc[{spec.name}]: dict 아님 {type(raw)}")
    properties = {}
    for name in spec.properties:
        node = raw.get(name) if isinstance(raw.get(name), dict) else {}
        properties[name] = {
            "status": gates.normalise_status(node.get("status"), gate=spec.gate_of(name)),
            "sourceObservation": str(node.get("sourceObservation") or "")[:400],
            "generatedObservation": str(node.get("generatedObservation") or "")[:400],
        }
    statuses = {}
    for field_name, gate_id in spec.status_fields.items():
        declared = gates.normalise_status(raw.get(field_name), gate=gate_id)
        # The model's own overall field is not authority, exactly as in the general judge:
        # a response that FAILs `density` and then writes overallStatus PASS is a FAIL.
        # Derived from the gate's OWN properties only.
        derived = gates.merge_gate_samples(
            [properties[n]["status"] for n in spec.properties_for(gate_id)], gate=gate_id)
        # The DERIVED value wins. The model's own `<gate>Status` field is a self-report and is
        # not authority — the same rule the general judge's `decision` field lives under. It
        # may only make the verdict STRICTER: a model that ticks PASS on the summary field
        # while failing the property it summarises does not get the pass, and one that admits
        # FAIL there is believed even if its property answers looked fine.
        # Merging the two as if they were peer samples let a declared PASS overwrite a derived
        # NOT_APPLICABLE, which is how a skirt with no wrap silently became "wrap verified".
        status = derived if not gates.gate_blocks(declared) else gates.merge_gate_samples(
            [declared, derived], gate=gate_id)
        statuses[gate_id] = {"declared": declared, "derived": derived, "status": status}
    confidence = raw.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        confidence = None
    else:
        confidence = max(0.0, min(1.0, float(confidence)))
    return {
        "judge": spec.name,
        "properties": properties,
        "gateStatuses": statuses,
        "evidence": str(raw.get("evidence") or "")[:800],
        "correctionInstruction": str(raw.get("correctionInstruction") or "")[:1200],
        "confidence": confidence,
        "promptVersion": PROMPT_VERSION,
    }


def merge_samples(spec: JudgeSpec, verdicts: Sequence[dict]) -> dict:
    """Fail-closed per property and per gate, same rule as the general judge."""
    if not verdicts:
        raise VisionError(f"pattern_fidelity_qc[{spec.name}]: 샘플이 없습니다.")
    properties = {}
    for name in spec.properties:
        sample_statuses = [(v["properties"].get(name) or {}).get("status") for v in verdicts]
        merged = gates.merge_gate_samples(sample_statuses, gate=spec.gate_of(name))
        picked = next((v["properties"][name] for v in verdicts
                       if (v["properties"].get(name) or {}).get("status") == merged), {})
        properties[name] = {
            "status": merged,
            "sourceObservation": picked.get("sourceObservation", ""),
            "generatedObservation": picked.get("generatedObservation", ""),
            "sampleStatuses": sample_statuses,
        }
    gate_statuses = {}
    for gate_id in spec.status_fields.values():
        sample_statuses = [(v["gateStatuses"].get(gate_id) or {}).get("status")
                           for v in verdicts]
        gate_statuses[gate_id] = {
            "status": gates.merge_gate_samples(sample_statuses, gate=gate_id),
            "sampleStatuses": sample_statuses,
        }
    instructions = [v.get("correctionInstruction") or "" for v in verdicts]
    confidences = [v.get("confidence") for v in verdicts
                   if isinstance(v.get("confidence"), (int, float))]
    return {
        "judge": spec.name,
        "properties": properties,
        "gateStatuses": gate_statuses,
        "evidence": " | ".join(v.get("evidence") or "" for v in verdicts)[:1200],
        "correctionInstruction": max(instructions, key=len) if instructions else "",
        "confidence": min(confidences) if confidences else None,
        "promptVersion": PROMPT_VERSION,
        "samples": len(verdicts),
    }


async def _judge_once(settings, spec: JudgeSpec, evidence: Sequence[Evidence],
                      thinking_level: str) -> tuple[dict, str]:
    raw, provider = await analyze_with_fallback(
        settings, build_prompt(spec, evidence), [e.image for e in evidence], schema(spec),
        thinking_level=thinking_level, temperature=JUDGE_TEMPERATURE)
    return validate(spec, raw), provider


async def judge(settings, spec: JudgeSpec, evidence: Sequence[Evidence], *,
                thinking_level: str = "medium",
                samples: int = DEFAULT_SAMPLES) -> tuple[dict, str]:
    if not evidence:
        raise VisionError(f"pattern_fidelity_qc[{spec.name}]: 증거 이미지가 없습니다.")
    n = max(1, int(samples))
    results = await asyncio.gather(
        *(_judge_once(settings, spec, evidence, thinking_level) for _ in range(n)),
        return_exceptions=True)
    ok = [r for r in results if not isinstance(r, BaseException)]
    if not ok:
        raise next(r for r in results if isinstance(r, BaseException))
    verdicts = [v for v, _ in ok]
    return merge_samples(spec, verdicts), ok[0][1]


async def run_gates(settings, *, required: Sequence[str], source_front: bytes | None,
                    source_detail: bytes | None, generated: InlineImage,
                    thinking_level: str = "medium",
                    samples: int = DEFAULT_SAMPLES) -> tuple[dict, int]:
    """Required gates -> `{gate: {...}}`, plus the number of provider requests it took.

    One judge answers all the gates it owns, so a lace/sheer product costs one round trip for
    two gates rather than two for one each. Judges run concurrently: they share no state, and
    serialising them would add a visible stall to every patterned product.

    A judge that fails leaves its gates UNANSWERED rather than answering them optimistically.
    `garment_fidelity_authority.combine` blocks on a missing required gate, so a provider
    outage degrades to "blocked", never to "passed".
    """
    from ..services import pattern_evidence as pe

    judges = gates.required_judges(required)
    if not judges:
        return {}, 0
    crops = pe.generated_crops(generated.data)
    detail_crop = (pe.source_center_crop(source_detail, name="source_detail_crop")
                   if source_detail else None)
    evidence = build_evidence(
        source_front=source_front, source_detail=source_detail,
        source_detail_crop=detail_crop.data if detail_crop else None,
        generated_full=generated, generated_crops=crops)

    async def one(judge_name: str):
        spec = SPECS[judge_name]
        verdict, provider = await judge(settings, spec, evidence,
                                        thinking_level=thinking_level, samples=samples)
        return judge_name, verdict, provider

    outcomes = await asyncio.gather(*(one(j) for j in judges), return_exceptions=True)
    results: dict = {}
    calls = 0
    wanted = set(required)
    for outcome in outcomes:
        if isinstance(outcome, BaseException):
            continue
        judge_name, verdict, provider = outcome
        calls += int(verdict.get("samples") or 1)
        for gate_id, node in verdict["gateStatuses"].items():
            if gate_id not in wanted:
                continue
            results[gate_id] = {
                "status": node["status"],
                "sampleStatuses": node.get("sampleStatuses"),
                "judge": judge_name,
                "provider": provider,
                "confidence": verdict.get("confidence"),
                "evidence": verdict.get("evidence"),
                "correctionInstruction": verdict.get("correctionInstruction"),
                "properties": verdict.get("properties"),
                "promptVersion": verdict.get("promptVersion"),
                "evidenceCrops": [c.as_dict() for c in crops]
                                 + ([detail_crop.as_dict()] if detail_crop else []),
            }
    return results, calls


def build_evidence(*, source_front: bytes | None, source_detail: bytes | None,
                   source_detail_crop: bytes | None, generated_full: InlineImage,
                   generated_crops: Sequence) -> list[Evidence]:
    """Assemble the attachment list in the order the manifest will describe.

    Sources first so the ground truth is established before the thing under test, and within
    each side the wide shot before the crop, so the model knows what the crop is a crop OF.
    """
    items: list[Evidence] = []
    if source_front is not None:
        items.append(Evidence(
            "SOURCE FRONT — the real product, full view. Ground truth.",
            as_inline(source_front)))
    if source_detail is not None:
        items.append(Evidence(
            "SOURCE DETAIL — close-up of the real fabric. Ground truth for the pattern's "
            "colours, widths and spacing.", as_inline(source_detail)))
    if source_detail_crop is not None:
        items.append(Evidence(
            "SOURCE DETAIL CROP — the same close-up at native resolution, so the individual "
            "lines are resolvable.", as_inline(source_detail_crop)))
    items.append(Evidence(
        "GENERATED FULL — the mannequin image under test, whole frame.", generated_full))
    for crop in generated_crops:
        label = {
            "generated_garment_crop":
                "GENERATED GARMENT CROP — the worn garment from the image under test. "
                "JUDGE THE PATTERN FROM THIS.",
            "generated_torso_detail_crop":
                "GENERATED TORSO CROP — the chest of the image under test at native "
                "resolution. This is the closest look you have at the generated fabric.",
        }.get(crop.name, f"GENERATED CROP ({crop.name})")
        items.append(Evidence(label, as_inline(crop.data)))
    return items
