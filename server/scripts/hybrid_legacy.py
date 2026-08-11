"""Replay the PRE-CHANGE wording inside the experiment, without touching production.

Why this exists: `--contact-ab` compares two prompts that both already carry the new knit
material guidance, and its two SAM arms both already carry the new cutout wording. So it
measures the contact block's *incremental* effect on top of the other two changes — not
"production before" versus "production after". This module restores the old wording at
runtime so the whole-change comparison is possible.

Direction of the restoration matters. Production is the single source of the NEW text; this
file stores only the DELTA that was added, and asserts that delta is still present in
production before removing it. If someone edits the knit guidance again, the assertion fires
and the legacy arm refuses to run — which is the right failure. A replay that silently stops
reverting anything would report "the change did nothing" for a run that compared a prompt
against itself.

Nothing here is imported by production code. The flags live in `scripts/hybrid_*` only.
"""

from __future__ import annotations

from app.agents import materials as _mat
from scripts import hybrid_branches as hb

#: The exact sentences this change ADDED to `materials.OVERRIDE_BLOCK["knit"]`. Removing them
#: reproduces the pre-change block verbatim.
KNIT_OVERRIDE_ADDITION = (
    " Knit is a soft, deformable fabric: it settles onto the body it is worn on and takes "
    "that body's three-dimensional volume, rather than holding the flat rectangular shape it "
    "has lying on a table. Its thickness is the thickness of the yarn and the loft of the "
    "stitch, not distance standing away from the body."
)

#: The exact sentence this change ADDED to `materials._knit_cue()`.
KNIT_CUE_ADDITION = (
    " Knit yields to the body underneath: it drapes over the shoulders and around the torso "
    "and takes their volume, while keeping the product's own fit and ease exactly as they are."
)

_ADDITIONS = {"knitOverride": KNIT_OVERRIDE_ADDITION, "knitCue": KNIT_CUE_ADDITION}


def assert_in_sync() -> None:
    """Fail loudly if production drifted away from the delta recorded here."""
    drifted = []
    if KNIT_OVERRIDE_ADDITION not in _mat.OVERRIDE_BLOCK["knit"]:
        drifted.append("materials.OVERRIDE_BLOCK['knit']")
    if KNIT_CUE_ADDITION not in _mat._knit_cue(True):
        drifted.append("materials._knit_cue()")
    if drifted:
        raise RuntimeError(
            "hybrid_legacy is out of sync with production wording: "
            + ", ".join(drifted)
            + ". Update the recorded delta before running a legacy arm — otherwise the "
              "legacy arm silently equals the new arm and the A/B reports a false null.")


def legacy_prompt(prompt: str) -> tuple[str, dict]:
    """The prompt as production rendered it BEFORE this change.

    Returns (text, applied) where `applied` records which reversions actually fired, so a run
    can never claim it tested the knit wording on a garment that carries none.
    """
    assert_in_sync()
    text = hb.strip_contact_block(prompt)          # raises when the block is missing
    applied = {"contactBlock": True}
    for name, addition in _ADDITIONS.items():
        applied[name] = addition in text
        if applied[name]:
            text = text.replace(addition, "")
    return text, applied


#: Exactly what each branch name uses. Written into results.json so no arm's wording has to
#: be inferred from its name later. `_nocontact` arms are NOT legacy: they keep the new knit
#: guidance and the new cutout wording, and drop only the contact block.
BRANCH_WORDING = {
    "baseline":           ("new", "new", None),
    "sam2":               ("new", "new", "new"),
    "baseline_nocontact": ("absent", "new", None),
    "sam2_nocontact":     ("absent", "new", "new"),
    "legacy_baseline":    ("absent", "legacy", None),
    "legacy_sam2":        ("absent", "legacy", "legacy"),
}

_CONTACT = {"new": "present — GARMENT-BODY CONTACT block",
            "absent": "absent — block removed at runtime"}
_KNIT = {"new": "new — knit takes the body's volume, fit and ease preserved",
         "legacy": "legacy — 'soft body-following drape' only"}
_CUTOUT = {"new": "new — proportions + construction, never the contour",
           "legacy": "legacy — DETAIL EVIDENCE ONLY"}


def wording_profile(branch: str, *, applied: dict | None = None) -> dict:
    """What this arm actually used — written into results.json next to the image."""
    contact, knit, cutout = BRANCH_WORDING[branch]
    knit_text = _KNIT[knit]
    if knit == "legacy" and applied is not None and not (applied.get("knitOverride")
                                                         or applied.get("knitCue")):
        knit_text += " (no knit wording in this prompt — garment carries no knit material, "\
                     "so this arm is identical to the new arm on that axis)"
    return {"contactBlock": _CONTACT[contact],
            "knitGuidance": knit_text,
            "cutoutWording": _CUTOUT[cutout] if cutout else "n/a — no cutout attached"}
