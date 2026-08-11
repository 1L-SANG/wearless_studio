"""Conditional, property-specific hard gates — which ones apply, and what they do to READY.

The general thirteen-check judge asks thirteen questions about the whole garment. That is the
right shape for "is this the same product", and it is the wrong shape for "is this the same
stripe": `patternType` is answered PASS by any two striped shirts, and the live stripe run
proved it — three samples, unanimous PASS, on a shirt whose grouped taupe-and-blue multi-
stripe had become a uniform dense pinstripe.

So a product whose Product Truth says STRIPE gets asked a second, narrower question, by a
judge that is shown a crop instead of a full body. This module decides which narrow questions
a product has earned and how their answers combine with the general one.

Two rules carry the design.

**Activation comes from Product Truth, not from the image.** Truth is the approved,
seller-confirmed classification; deriving "is this striped?" from the generated candidate
would let a candidate that erased its own stripe also erase the gate that would have caught
it. STRIPE and CHECK come straight off `patternSpec.type`. The material gates are looser —
there is no `materialSpec.type` — so they read the truth's own material vocabulary.

**An activated gate is hard, and the general judge cannot overrule it.** `generalQC = PASS`
with `stripeFidelity = FAIL` is FAIL. The composition is one-directional on purpose: the
specialized judge saw the crop, the general one did not.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

VERSION = "pattern_fidelity_gates_v1"

PASS = "PASS"
FAIL = "FAIL"
UNVERIFIABLE = "UNVERIFIABLE"
#: Only the material gates may answer this, and only about the SOURCE: "the source
#: photographs do not establish this property, so there is nothing to compare against."
#: Never available to STRIPE/CHECK — Truth already asserted the pattern is there, so a source
#: that cannot show it is an evidence failure, not an exemption.
NOT_APPLICABLE = "NOT_APPLICABLE"

#: Category gates. Unlike the pattern/material gates above, these activate from the garment
#: CATEGORY rather than from the truth's pattern class: a pair of trousers has a waistband and
#: an outseam whatever its fabric does, and a jacket has a closure whatever its pattern is.
GATE_WAIST_PLACEMENT = "waistPlacement"
GATE_LEG_SILHOUETTE = "legSilhouette"
GATE_OUTSEAM_LENGTH = "outseamLength"
GATE_CLOSURE_WAISTBAND = "closureAndWaistband"
GATE_HEM_SHAPE = "hemShape"
GATE_BODY_INTEGRATION_LOWER = "garmentBodyIntegrationLower"
GATE_FRONT_PANEL_SYMMETRY = "frontPanelSymmetry"
GATE_SKIRT_WRAP_SLIT = "skirtWrapOrSlitStructure"

GATE_CLOSURE_STATE = "closureState"
GATE_FRONT_OPENING = "frontOpeningControl"
GATE_ZIPPER_ALIGNMENT = "zipperAlignment"
GATE_COLLAR_PLACKET = "collarAndFrontPlacketIntegrity"

BOTTOM_GATES = (GATE_WAIST_PLACEMENT, GATE_LEG_SILHOUETTE, GATE_OUTSEAM_LENGTH,
                GATE_CLOSURE_WAISTBAND, GATE_HEM_SHAPE, GATE_BODY_INTEGRATION_LOWER,
                GATE_FRONT_PANEL_SYMMETRY, GATE_SKIRT_WRAP_SLIT)
OUTER_GATES = (GATE_CLOSURE_STATE, GATE_FRONT_OPENING, GATE_ZIPPER_ALIGNMENT,
               GATE_COLLAR_PLACKET)

GATE_STRIPE = "stripeFidelity"
GATE_CHECK = "checkFidelity"
GATE_SURFACE_TEXTURE = "surfaceTextureFidelity"
GATE_LACE_STRUCTURE = "laceStructureFidelity"
GATE_OPACITY = "opacityFidelity"

ALL_GATES = ((GATE_STRIPE, GATE_CHECK, GATE_SURFACE_TEXTURE, GATE_LACE_STRUCTURE,
              GATE_OPACITY) + BOTTOM_GATES + OUTER_GATES)

#: Gates that may not answer NOT_APPLICABLE.
#:
#: Every bottom gate except the wrap/slit one is strict: trousers always have a waist, a leg
#: and a hem, so "not applicable" there is a judge declining to look. `skirtWrapOrSlitStructure`
#: is the exception the brief names — a plain straight skirt genuinely has no wrap or slit, and
#: forcing a verdict on a structure the source does not have would invent a failure.
#: Outer gates are all strict: the closed-front rule is the policy, not a preference.
STRICT_GATES = frozenset({GATE_STRIPE, GATE_CHECK}
                         | set(BOTTOM_GATES) - {GATE_SKIRT_WRAP_SLIT}
                         | set(OUTER_GATES))

#: Which judge answers which gates. One provider round trip per judge, not per gate.
JUDGE_STRIPE = "stripe"
JUDGE_CHECK = "check"
JUDGE_SURFACE_TEXTURE = "surface_texture"
JUDGE_LACE_OPACITY = "lace_opacity"
JUDGE_BOTTOM = "bottom"
JUDGE_OUTER = "outer"

JUDGE_GATES: Mapping[str, tuple[str, ...]] = {
    JUDGE_STRIPE: (GATE_STRIPE,),
    JUDGE_CHECK: (GATE_CHECK,),
    JUDGE_SURFACE_TEXTURE: (GATE_SURFACE_TEXTURE,),
    JUDGE_LACE_OPACITY: (GATE_LACE_STRUCTURE, GATE_OPACITY),
    JUDGE_BOTTOM: BOTTOM_GATES,
    JUDGE_OUTER: OUTER_GATES,
}

#: Category -> gates that category always owes, regardless of pattern or material.
CATEGORY_GATES = {"bottom": BOTTOM_GATES, "outer": OUTER_GATES}

_STRIPE_TYPES = {"stripe", "pinstripe", "스트라이프", "줄무늬", "핀스트라이프"}
_CHECK_TYPES = {"check", "checked", "gingham", "tartan", "plaid", "windowpane",
                "체크", "깅엄", "타탄", "격자"}

#: Surface-relief knits: the structure IS the product, and it is the first thing a generator
#: flattens. "골지" is rib; "포인텔레"/pointelle is the openwork knit on the control blouse.
_TEXTURE_WORDS = ("rib", "ribbed", "pointelle", "waffle", "cable", "jacquard", "seersucker",
                  "crinkle", "puckered", "shirring", "plisse", "pleated", "corduroy",
                  "골지", "포인텔레", "와플", "케이블", "자카드", "시어서커", "셔링", "잔주름")
_LACE_WORDS = ("lace", "openwork", "eyelet", "crochet", "mesh", "레이스", "아일렛", "망사", "펀칭")
_SHEER_WORDS = ("sheer", "transparent", "see-through", "chiffon", "organza", "voile",
                "시스루", "투명", "시폰", "오간자")


def _text_of(*values) -> str:
    parts: list[str] = []

    def walk(value):
        if value is None:
            return
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, Mapping):
            for v in value.values():
                walk(v)
        elif isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
            for v in value:
                walk(v)
        else:
            parts.append(str(value))

    for value in values:
        walk(value)
    return " ".join(parts).lower()


def _pattern_type(truth: Mapping | None) -> str:
    spec = (truth or {}).get("patternSpec") or (truth or {}).get("pattern_spec") or {}
    if not isinstance(spec, Mapping):
        return ""
    return str(spec.get("type") or spec.get("patternType") or "").strip().lower()


def required_gates(truth: Mapping | None, *, product: Mapping | None = None,
                   category: str | None = None) -> tuple[str, ...]:
    """Approved Product Truth -> the gates this product must clear.

    `product` is consulted only for the material vocabulary, and only for the seller-confirmed
    name and category — never for the pattern class, which has an authoritative field.
    """
    gates: list[str] = list(CATEGORY_GATES.get((category or "").strip().lower(), ()))
    if not isinstance(truth, Mapping):
        return tuple(gates)
    pattern = _pattern_type(truth)
    if pattern in _STRIPE_TYPES or any(t in pattern for t in _STRIPE_TYPES):
        gates.append(GATE_STRIPE)
    elif pattern in _CHECK_TYPES or any(t in pattern for t in _CHECK_TYPES):
        gates.append(GATE_CHECK)

    garment = truth.get("garmentSpec") or truth.get("garment_spec") or {}
    material_text = _text_of(
        (garment or {}).get("materialTraits"), (garment or {}).get("subcategory"),
        (product or {}).get("name"))
    if any(word in material_text for word in _TEXTURE_WORDS):
        gates.append(GATE_SURFACE_TEXTURE)
    if any(word in material_text for word in _LACE_WORDS):
        gates.append(GATE_LACE_STRUCTURE)
    if any(word in material_text for word in _SHEER_WORDS + _LACE_WORDS):
        gates.append(GATE_OPACITY)
    return tuple(dict.fromkeys(gates))


def required_judges(gates: Iterable[str]) -> tuple[str, ...]:
    wanted = set(gates)
    return tuple(name for name, owned in JUDGE_GATES.items()
                 if wanted.intersection(owned))


def normalise_status(value, *, gate: str) -> str:
    """Unknown reads as UNVERIFIABLE, and NOT_APPLICABLE is refused to the strict gates.

    A STRIPE product whose judge answers NOT_APPLICABLE has not been exempted; it has failed
    to verify a property Truth says exists, which blocks.
    """
    text = str(value or "").strip().upper()
    if text == NOT_APPLICABLE and gate in STRICT_GATES:
        return UNVERIFIABLE
    if text in (PASS, FAIL, UNVERIFIABLE, NOT_APPLICABLE):
        return text
    return UNVERIFIABLE


def merge_gate_samples(statuses: Iterable[str], *, gate: str) -> str:
    """Fail-closed across samples, and NOT_APPLICABLE only when every sample agreed.

    Same reasoning as the general judge's merge: the observed disagreements are one-sided —
    one sample notices the stripe grouping is gone while two answer the coarse question. It
    also closes the obvious escape: a single sample cannot exempt a gate by calling it
    inapplicable while its peers were comparing.
    """
    values = [normalise_status(s, gate=gate) for s in statuses]
    if not values:
        return UNVERIFIABLE
    if FAIL in values:
        return FAIL
    if UNVERIFIABLE in values:
        return UNVERIFIABLE
    if all(v == NOT_APPLICABLE for v in values):
        return NOT_APPLICABLE
    if PASS in values and NOT_APPLICABLE in values:
        # some samples compared and some declined: the ones that compared saw enough, so the
        # property is verifiable and the declining samples were not looking hard enough
        return PASS
    return PASS


def gate_blocks(status: str) -> bool:
    """A gate blocks unless it passed or was genuinely inapplicable."""
    return status not in (PASS, NOT_APPLICABLE)


def failing_gates(results: Mapping[str, Mapping] | None) -> tuple[str, ...]:
    if not isinstance(results, Mapping):
        return ()
    return tuple(gate for gate, node in results.items()
                 if gate_blocks(str((node or {}).get("status") or UNVERIFIABLE)))


def missing_gates(required: Iterable[str], results: Mapping | None) -> tuple[str, ...]:
    """Required but never answered. Blocking: an unasked hard question is not a pass."""
    answered = set(results or {})
    return tuple(g for g in required if g not in answered)
