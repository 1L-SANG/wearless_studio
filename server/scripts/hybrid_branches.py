"""Branch prompts for the hybrid experiment. Text only — no provider, no I/O.

The one thing this file exists to get right is what a SAM cutout IS allowed to be.

An earlier experiment told the model the cutout was "the clearest view of the garment's
outline and proportions". That is silhouette authority, and it produced exactly the failure
this run is built to catch: the flat cut-out shape survives into the render and the garment
ends up standing in front of the mannequin instead of on it. So here the cutout is demoted, in
writing, to pattern, component and proportion evidence, and the prompt says outright that its
outline must NOT be preserved.

The middle position is deliberate. "Detail evidence only" was the previous wording and it threw
away the one thing a clean cutout is genuinely better at — reading how the garment's parts
relate in size — while "the clearest view of the outline and proportions" handed over silhouette
authority and produced the flat-panel render. Proportions yes, contour no.

Authority split, stated in every branch so the arms differ only by evidence:
  base mannequin  -> pose, body, camera, framing, background, lighting
  RAW photographs -> garment identity, silhouette, construction, length
  SAM cutout      -> pattern / trims / components / texture / proportions — never the contour
"""

from __future__ import annotations

from collections.abc import Sequence

#: Slot roles shared by both branches. Detail is never geometry — a macro close-up magnifies
#: everything in it, and a model that reads it as geometry draws the pattern too large.
SLOT_ROLE = {
    "Front": "PRIMARY FRONT GEOMETRY evidence — front silhouette, garment length, neckline and "
             "collar, placket, buttons, pockets, front seams, and where the pattern sits.",
    "Back": "PRIMARY REAR GEOMETRY evidence — rear silhouette, rear length, yoke and back "
            "seams, rear sleeve construction, and how the pattern continues around the back.",
    "Detail": "MATERIAL / PATTERN / COMPONENT evidence ONLY — weave, knit structure, pattern "
              "scale RELATIVE TO THE WHOLE GARMENT IN THE FRONT PHOTO, ribbing, pucker, lace, "
              "stitching, the look of the buttons. NEVER geometry, and never a reason to draw "
              "the pattern larger than the Front photo shows it.",
}

CUTOUT_ROLE = (
    "PROPORTION AND CONSTRUCTION EVIDENCE, NEVER A SILHOUETTE TEMPLATE — the same photograph "
    "with its background removed, which makes the pattern, buttons, placket, pockets, trims, "
    "seams, fabric texture and the garment's relative proportions easier to read. Its outer "
    "contour is the shape of a garment lying flat and must NOT be traced into the result."
)

#: The wording as it stood BEFORE the contact change — kept verbatim so a legacy arm can
#: reproduce the old SAM behaviour. Experiment-only; production never reads it.
LEGACY_CUTOUT_ROLE = (
    "DETAIL EVIDENCE ONLY — the same photograph with its background removed, which makes the "
    "pattern, buttons, placket, pockets, trims, seams and fabric texture easier to read."
)

_WORN = """HOW THE GARMENT MUST SIT ON THE BODY — this is not optional.

The garment must look PHYSICALLY WORN by the mannequin. It wraps around the torso and turns
around the sides. It follows the chest and the waist. The neckline sits ON the neck, the
shoulder seams sit ON the shoulders, the armholes meet the arms, the hem hangs from the body
with its own weight. The fabric compresses and tensions where the body pushes into it, and
falls into folds where it does not.

It must NOT look pasted, floating, hovering, detached or superimposed. It must NOT read as a
flat two-dimensional sheet placed in front of the torso. There must be no gap and no hard cut
line between the garment and the body where they should be touching."""

_CUTOUT_RULES = """HOW TO USE THE ISOLATED CUTOUTS — read this twice.

The cutouts are evidence about the FABRIC, THE FITTINGS AND THE PROPORTIONS. Use them to see the
pattern clearly, to count and place the buttons, to read the placket, the pockets, the trims,
the seams and the texture, and to judge how the garment's parts relate in size to each other.

What they are NOT is a shape to trace.

Do NOT paste a cutout onto the mannequin.
Do NOT preserve a cutout's outline, silhouette or flat shape.
Do NOT treat a cutout as a template to align the garment to.
Do NOT keep a cutout as a flat two-dimensional sheet anywhere in the image.

A cutout is a photograph of a garment lying flat. The garment you are drawing is being worn by
a body, so its shape MUST differ from the cutout's shape. If your result has the cutout's
outline in it, you have made the wrong image."""

#: Pre-change version of the block above: fabric and fittings only, no proportions, and no
#: "not a shape to trace" line. Experiment-only.
_LEGACY_CUTOUT_RULES = """HOW TO USE THE ISOLATED CUTOUTS — read this twice.

The cutouts are evidence about the FABRIC AND THE FITTINGS, nothing else. Use them to see the
pattern clearly, to count and place the buttons, to read the placket, the pockets, the trims,
the seams and the texture.

Do NOT paste a cutout onto the mannequin.
Do NOT preserve a cutout's outline, silhouette or flat shape.
Do NOT treat a cutout as a template to align the garment to.
Do NOT keep a cutout as a flat two-dimensional sheet anywhere in the image.

A cutout is a photograph of a garment lying flat. The garment you are drawing is being worn by
a body, so its shape MUST differ from the cutout's shape. If your result has the cutout's
outline in it, you have made the wrong image."""

_AUTHORITY_HEAD = ("AUTHORITY OF EACH IMAGE — read this before anything else.\n\n"
                   "The BASE MANNEQUIN owns the body, the pose, the camera and the light.\n"
                   "The ORIGINAL PHOTOGRAPHS own the garment's identity, silhouette, "
                   "construction and length.\n")
_AUTHORITY_CUTOUT = ("The ISOLATED CUTOUTS own no silhouette authority. They are evidence "
                     "about the fabric, the fittings and the proportions — never a shape to "
                     "trace.")
_LEGACY_AUTHORITY_CUTOUT = ("The ISOLATED CUTOUTS own nothing about shape. They are detail "
                            "evidence only.")

_IDENTITY = """PRESERVE THE REAL PRODUCT — this is not a redesign.

Do not invent or remove buttons, pockets, zips or trims. Do not change the neckline, the sleeve
type, the sleeve length class or the garment's length class. Do not change the pattern's
orientation and do not widen a fine pattern. Do not flatten a defining textile structure such
as rib, knit, pucker or lace. Do not add decorative details.

Use ALL of the attached views together. If a Back photograph exists, it — not your imagination
— decides the rear construction and how the pattern continues around the body. If a Detail
photograph exists, it decides the texture and the trims."""


_BOTTOM_EMPHASIS = """THIS GARMENT IS A BOTTOM — trousers, shorts or a skirt. Extra care here:

The WAISTBAND must sit where this product's waist belongs and must sit ON the hips, not float
above them. Shape the crotch and the hips as cloth over a body: the fabric wraps the seat and
the thighs and takes their volume. The LEG SHAPE — wide, straight, slim, tapered, flared — and
the LENGTH CLASS — cropped, ankle, full, mini, midi, maxi — are product facts and must match
the source. The HEM width must be the source's, and the legs must hang with their own weight.
Keep the waistband, fly, button, zip, belt loops and drawcord exactly as the source has them.
Left and right must stay coherent — no twisted centre line, no legs of different width."""

_OUTER_CLOSED = """THIS GARMENT IS OUTERWEAR — present it CLOSED.

If the garment has a zip, show it zipped all the way up. If it has buttons or snaps, show them
fastened. Do not leave the front hanging open. Do not show the mannequin's bare torso or any
inner layer through a front opening, and do not invent an inner top, tee or shirt that the
source photographs do not contain.

The closed state must look natural and wearable — the closure line runs straight, the two
front panels meet evenly, the collar and placket keep their shape, and the fabric still falls
like cloth rather than being stretched shut.

The one exception: if the source garment genuinely has NO closure anywhere — no zip, no
buttons, no snaps — then an open front is its real construction and you should keep it."""


#: Markers that fence the GARMENT-BODY CONTACT block in
#: `server/prompts/mannequin_generate_v1.txt`. They exist so the block can be removed from a
#: rendered prompt to produce the CONTROL arm of the A/B — production always ships it.
CONTACT_START = "<!-- GARMENT_BODY_CONTACT_BLOCK_START -->"
CONTACT_END = "<!-- GARMENT_BODY_CONTACT_BLOCK_END -->"


def has_contact_block(prompt: str) -> bool:
    return CONTACT_START in prompt and CONTACT_END in prompt


def strip_contact_block(prompt: str) -> str:
    """The prompt as it read BEFORE the contact block — the control arm.

    Raises if the markers are absent: a control arm that silently equals the treatment arm
    would report "no effect" for a run that never tested anything.
    """
    if not has_contact_block(prompt):
        raise ValueError("prompt carries no GARMENT_BODY_CONTACT markers — "
                         "cannot build the control arm from it")
    head, _, rest = prompt.partition(CONTACT_START)
    _, _, tail = rest.partition(CONTACT_END)
    return f"{head.rstrip()}\n{tail.lstrip()}"


def category_rules(category: str) -> str:
    """Extra prompt rules a category always owes, independent of branch."""
    c = (category or "").strip().lower()
    if c == "bottom":
        return _BOTTOM_EMPHASIS
    if c == "outer":
        return _OUTER_CLOSED
    return ""


def manifest(slots: Sequence[str], cutout_slots: Sequence[str] = (),
             *, legacy_cutout: bool = False) -> str:
    role = LEGACY_CUTOUT_ROLE if legacy_cutout else CUTOUT_ROLE
    lines = ["ATTACHED IMAGES, in order:",
             "1. BASE MANNEQUIN — the authority for body, pose, camera, framing, crop, "
             "background and lighting. Keep all of them identical."]
    i = 2
    for s in slots:
        lines.append(f"{i}. ORIGINAL {s.upper()} photograph — {SLOT_ROLE[s]}")
        i += 1
    for s in cutout_slots:
        lines.append(f"{i}. ISOLATED {s.upper()} CUTOUT — {role}")
        i += 1
    return "\n".join(lines)


def baseline_prompt(base_prompt: str, category: str = "") -> str:
    """Branch A. The production prompt plus category presentation rules.

    The category rules are NOT a branch variable — the closed-front policy and the bottom
    shaping rules are what the pipeline wants from every candidate, so both branches get them.
    Keeping them out of the baseline would have made the comparison measure the rules instead
    of the evidence.
    """
    rules = category_rules(category)
    return f"{rules}\n\n{base_prompt}" if rules else base_prompt


def sam_augmented_prompt(base_prompt: str, slots: Sequence[str],
                         cutout_slots: Sequence[str], category: str = "",
                         *, legacy_cutout: bool = False) -> str:
    """Branch B. Same production prompt, plus evidence roles, cutout rules and wearing rules.

    `legacy_cutout=True` restores the pre-change cutout wording so a legacy arm can measure
    the whole change rather than its last increment. Experiment-only.
    """
    return "\n\n".join([x for x in [
        manifest(slots, cutout_slots, legacy_cutout=legacy_cutout),
        _AUTHORITY_HEAD + (_LEGACY_AUTHORITY_CUTOUT if legacy_cutout else _AUTHORITY_CUTOUT),
        _LEGACY_CUTOUT_RULES if legacy_cutout else _CUTOUT_RULES,
        _WORN,
        _IDENTITY,
        category_rules(category),
        base_prompt,
    ] if x])


#: Appended to a correction when garmentBodyIntegration is the thing that failed. The generic
#: per-property template says "render it as worn"; this says what that means concretely.
BODY_INTEGRATION_CORRECTION = (
    "The garment currently reads as a flat sheet placed in front of the mannequin rather than "
    "worn by it. Re-render it so the cloth wraps around the torso and turns around the sides, "
    "the neckline sits on the neck, the shoulder seams sit on the shoulders, the armholes meet "
    "the arms, and the fabric compresses where the body pushes into it. Remove any gap or hard "
    "cut line between the garment and the body. Keep the garment's colour, pattern, trims and "
    "construction exactly as they are — only how it sits on the body changes."
)
