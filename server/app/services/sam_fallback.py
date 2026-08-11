"""Should this rejected baseline be retried with canonical cutout evidence?

Baseline first, always. Primary routing is untouched: a knit still resolves to RAW and the
first generation is RAW-only. This decides one thing — when the existing QC has already asked
for a retry, whether that retry gets the canonical cutouts attached.

Three conditions, all required:

  1. the garment is knit-family, by the metadata the product already carries;
  2. the existing QC outcome already says "retry" (no new score, no new threshold);
  3. current canonical references exist, validated by the loader against today's sources.

Blouses are deliberately excluded. Canonical augmentation measurably regressed drape-dependent
blouses in the six-product A/B — it turned a scoop neckline square and pushed colour — so
"canonical exists" must never on its own pull a blouse into this path.

The fallback fires at most once per candidate. It replaces the inputs of a retry the QC had
already authorised, so it costs no extra provider call beyond the retry that was happening
anyway, and it can never trigger itself.
"""

from __future__ import annotations

from collections.abc import Mapping

#: Knit-family vocabulary, matched against the metadata the product already has. This reuses
#: the same tokens `materials.py` uses for its knit guidance rather than introducing a second,
#: divergent idea of what "knit" means. No classifier, no pixels.
_KNIT_WORDS = (
    "knit", "니트",
    "sweater", "스웨터",
    "cardigan", "가디건",
    "pullover", "풀오버",
    "jumper", "점퍼",
)

#: Categories that must never be pulled in by this rule even if they mention knit — a "knit
#: blouse" is still drape-dependent, and that is exactly the case canonical evidence hurt.
_EXCLUDED_WORDS = ("blouse", "블라우스")


def _text(*values) -> str:
    parts = []
    for v in values:
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, Mapping):
            parts.extend(str(x) for x in v.values() if isinstance(x, (str, int, float)))
        elif v is not None:
            parts.append(str(v))
    return " ".join(parts).lower()


def is_knit_family(product: Mapping | None, analysis: Mapping | None) -> bool:
    """Knit-family by existing metadata only: subCategory, customCategory, clothing type, name."""
    a, p = analysis or {}, product or {}
    haystack = _text(a.get("subCategory"), a.get("customCategory"), a.get("suggestedName"),
                     p.get("clothing_type"), p.get("clothingType"), p.get("name"))
    if any(w in haystack for w in _EXCLUDED_WORDS):
        return False
    return any(w in haystack for w in _KNIT_WORDS)


def decide(*, product, analysis, qc_says_retry: bool, canonical_refs: Mapping,
           already_used: bool) -> dict:
    """The whole decision. Returns a log-shaped verdict; `augment` is what the caller acts on.

    Deliberately ordered so the reason names the FIRST thing that disqualified it, which is
    what makes the log useful when someone asks why a garment did not get the fallback.
    """
    eligible_family = is_knit_family(product, analysis)
    verdict = {
        "samFallbackEligible": eligible_family,
        "canonicalFront": "READY" if canonical_refs.get("CanonicalFront") else "MISSING",
        "canonicalBack": "READY" if canonical_refs.get("CanonicalBack") else "MISSING",
        "samFallbackTriggered": False,
        "augment": False,
    }
    if already_used:
        verdict["samFallbackReason"] = "already_used"
    elif not eligible_family:
        verdict["samFallbackReason"] = "ineligible_family"
    elif not qc_says_retry:
        verdict["samFallbackReason"] = "baseline_accepted"
    elif not canonical_refs:
        # PENDING and FAILED are indistinguishable here on purpose: the loader returns only
        # what is READY and current, and generation must never wait for preprocessing.
        verdict["samFallbackReason"] = "canonical_missing"
    else:
        verdict.update(samFallbackReason="baseline_qc", samFallbackTriggered=True, augment=True)
    return verdict
