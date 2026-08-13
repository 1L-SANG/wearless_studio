"""Which references a mannequin generation is given — RAW, or RAW plus a canonical Front.

Small on purpose. This is a routing decision, not a classifier: two categories, one extra
attachment, and a fallback that is always safe.

What the six-product A/B showed (2026-08-11, one generation per arm, no QC, no correction):

* **Structured shirts gain.** The striped shirt and the collar/placket shirt both lost their
  second stripe colour when generated from the raw photos alone — the blue washed out and the
  fabric read as a single beige — and both recovered it when a background-free canonical Front
  was attached. Nothing else regressed.
* **Drape-sensitive tops lose.** On 4ff2132f the extra reference turned a round scoop neckline
  into a square one and pushed dusty mauve toward rust; on the lace top it saturated the pink
  and moved the empire seam. Both are changes to product identity, not to finish.

The asymmetry is not surprising after the fact: a shirt's identity lives in flat, front-facing
things — collar, placket, buttons, a printed stripe — which a flattened cut-out shows clearly.
A blouse's identity lives in how cloth falls, and a garment lying flat on a canvas is exactly
the evidence that misrepresents that.

The sample is six products. It decides a default, not a law, and RAW is the fallback for every
case that is not confidently a shirt.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

VERSION = "generation_input_strategy_v1"

STRATEGY_RAW = "RAW"
STRATEGY_AUGMENTED = "AUGMENTED"

CATEGORY_SHIRT = "SHIRT"
CATEGORY_BLOUSE = "BLOUSE"
CATEGORY_UNKNOWN = "UNKNOWN"

#: Drape-sensitive vocabulary. Checked FIRST and it wins: a "셔링 블라우스" contains neither
#: ambiguity nor a shirt, and a garment that is both collared and shirred is exactly the case
#: where the safe answer is RAW.
_BLOUSE_WORDS = (
    "blouse", "블라우스",
    "puff", "퍼프", "shirring", "셔링", "gather", "gathered", "셔르링",
    "frill", "ruffle", "프릴", "러플", "flare", "플레어", "peplum", "페플럼",
    "lace", "레이스", "sheer", "시스루", "chiffon", "시폰", "organza", "오간자",
    "empire", "엠파이어", "smocking", "스모킹", "drape", "드레이프",
    "square neck", "스퀘어넥", "off shoulder", "오프숄더", "boat neck", "보트넥",
    "cami", "캐미", "슬리브리스", "tie neck", "타이넥", "핀턱", "pintuck",
)

#: Structured, front-facing-component vocabulary.
_SHIRT_WORDS = (
    "shirt", "셔츠", "남방", "overshirt", "오버셔츠", "shacket", "셔켓",
    "oxford", "옥스포드", "denim shirt", "데님셔츠",
)

#: Construction facts that make a top shirt-like even when the name says nothing useful.
#: BOTH are required — a collar alone is a polo or a blouse with a collar, and buttons alone
#: are a henley or a cardigan.
_SHIRT_STRUCTURE = ("COLLAR", "BUTTONS")


@dataclass(frozen=True)
class InputStrategy:
    """`strategy` is the only thing the worker should branch on."""

    strategy: str
    category: str
    reason: str
    canonical_available: bool = False
    version: str = VERSION

    @property
    def use_canonical(self) -> bool:
        return self.strategy == STRATEGY_AUGMENTED

    def as_event(self) -> dict:
        return {"strategy": self.strategy, "category": self.category, "reason": self.reason,
                "canonicalAvailable": self.canonical_available, "version": self.version}


def _text(*values) -> str:
    parts: list[str] = []

    def walk(v):
        if v is None:
            return
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, Mapping):
            for x in v.values():
                walk(x)
        elif isinstance(v, Iterable) and not isinstance(v, (bytes, bytearray)):
            for x in v:
                walk(x)
        else:
            parts.append(str(v))

    for value in values:
        walk(value)
    return " ".join(parts).lower()


def classify_garment(truth: Mapping | None, product: Mapping | None = None) -> str:
    """Approved Product Truth (+ the seller-confirmed name) -> SHIRT / BLOUSE / UNKNOWN.

    Only an APPROVED truth is consulted for structure. A draft has not been confirmed by
    anyone, and this decision changes what the generator is shown; UNKNOWN routes to RAW, so
    refusing to guess costs nothing.
    """
    approved = isinstance(truth, Mapping) and truth.get("status") == "approved"
    garment = (truth or {}).get("garmentSpec") or (truth or {}).get("garment_spec") or {}
    name_text = _text((product or {}).get("name"),
                      (garment or {}).get("subcategory") if approved else None)

    if any(word in name_text for word in _BLOUSE_WORDS):
        return CATEGORY_BLOUSE
    if any(word in name_text for word in _SHIRT_WORDS):
        return CATEGORY_SHIRT
    if approved:
        flags = {str(f).upper() for f in (garment.get("structureFlags") or [])}
        if all(f in flags for f in _SHIRT_STRUCTURE):
            return CATEGORY_SHIRT
    return CATEGORY_UNKNOWN


def resolve(truth: Mapping | None, product: Mapping | None = None, *,
            canonical_available: bool = False) -> InputStrategy:
    """The whole router.

    AUGMENTED requires two independent yeses: the garment is confidently a shirt, AND a
    canonical reference actually exists. Missing evidence is never an error and never reaches
    the seller — it is simply the RAW path, which is what production does today.
    """
    category = classify_garment(truth, product)
    if category != CATEGORY_SHIRT:
        return InputStrategy(
            STRATEGY_RAW, category,
            "drape-sensitive top — raw Product Truth only" if category == CATEGORY_BLOUSE
            else "category not confidently a structured shirt — raw is the safe default",
            canonical_available)
    if not canonical_available:
        return InputStrategy(STRATEGY_RAW, category,
                             "structured shirt but no usable canonical reference",
                             False)
    return InputStrategy(STRATEGY_AUGMENTED, category,
                         "structured shirt with a usable canonical reference",
                         True)


#: The manifest line the extra attachment gets. Describes what the image IS; it never names a
#: defect, because an instruction to fix something is a different experiment from an extra
#: piece of evidence.
def _canonical_line(view: str) -> str:
    return (
        f"clean source-preserving {view} garment reference — the SAME product's {view.lower()} "
        "with its background removed. Its pixels are the original product photograph, not a "
        "redrawing. PROPORTION AND CONSTRUCTION EVIDENCE, NEVER A SILHOUETTE TEMPLATE: read "
        "the garment's proportions, construction, pattern and colour from it. It shows the "
        "garment lying flat, so its outer contour is a flat-lay contour — do NOT trace that "
        "contour literally as the final worn silhouette. Worn on a body the same garment wraps "
        "around the torso, so its outline there must differ."
    )


CANONICAL_MANIFEST_LINE = _canonical_line("Front")
CANONICAL_BACK_MANIFEST_LINE = _canonical_line("Back")
