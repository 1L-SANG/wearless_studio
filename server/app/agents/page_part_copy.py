"""Two-line page-part descriptions and seller sentences in one text completion.

Seller dictionary/validation remain owned by feature_copy. Every selected page part
is freshly written under its own prompt rules, including labels in that dictionary.
"""

from itertools import groupby
import logging
from pathlib import Path
import re
import unicodedata

from . import feature_copy
from .page_template_assembler import point_photos_for_run
from .prompts import _sanitize

log = logging.getLogger(__name__)
MAX_LINE_CHARS = 22
_PROMPT_FILE = Path(__file__).resolve().parents[2] / "prompts" / "page_part_copy_v1.txt"
# Photos and part labels establish construction, never performance or tactile feel.
# Reuse all seller bans, with everyday paraphrases of unverified claims added here.
_PART_BANNED = (*feature_copy._BANNED, *feature_copy._HYPE,
                "따뜻", "시원", "쾌적", "땀", "신축", "탄성", "텐션", "촉감", "보드라",
                "보들", "부들", "따듯", "따스", "맨살", "바람", "물기",
                "까슬", "편안", "편하", "편해", "늘어나", "늘어나는", "늘어남", "늘어짐",
                "늘어지", "피부", "자극", "비침", "비치지", "비치지않", "비치않",
                "건조", "수축", "변형", "견고", "튼튼", "내구", "가벼운무게", "체형보정",
                "체형을보정", "누구나", "하루종일", "바람을막", "바람차단", "바람막",
                "물을막", "물차단", "물이안", "물이새지", "물이스며들지", "젖지않")
_END = re.compile(r"(?:어요|아요|해요|여요|혀요|려요|돼요|예요|에요|워요|져요|춰요|켜요|와요|봐요|나요|줘요|둬요)\.?$")
_PLAIN_LINE = re.compile(r"[가-힣A-Za-z0-9 ,·()/\-]+")


def copy_schema():
    """The response groups cannot collide even when the point strings are equal."""
    return {"type": "object", "additionalProperties": False,
            "properties": {field: feature_copy.copy_schema()["properties"]["items"]
                           for field in ("featureCopy", "partDescriptionCopy")},
            "required": ["featureCopy", "partDescriptionCopy"]}


def build_prompt(seller_labels, page_labels, product, analysis):
    template = _PROMPT_FILE.read_text(encoding="utf-8").replace("{{MAX_LINE_CHARS}}", str(MAX_LINE_CHARS))
    facts = feature_copy._facts_block(product, analysis)
    sellers = "\n".join(f"- {_sanitize(label)}" for label in seller_labels if _sanitize(label))
    parts = "\n".join(f"- {_sanitize(label)}" for label in page_labels if _sanitize(label))
    return f"{template}\n\n{facts}\n\nSELLER HIGHLIGHTS (data):\n{sellers}\n\nPAGE PARTS (data):\n{parts}"


def validate_parts(items, labels):
    """Reject entire invalid rows; never trim a sentence, fold lines, or use a dictionary."""
    if not isinstance(items, list):
        return {}
    wanted = {}
    for label in unique_labels(labels):
        for key in (_sanitize(label), label):
            if key:
                wanted.setdefault(key, label)
    out = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("point"), str):
            continue
        point, desc = wanted.get(item["point"]), item.get("desc")
        if point is None or not isinstance(desc, str):
            continue
        # Explicit LF is the only permitted control character. Keep the exact line
        # break for the renderer; tabs, CR, bidi/zero-width controls and markup fail.
        if any((unicodedata.category(char).startswith("C") and char != "\n")
               or char in "<>`" for char in desc):
            continue
        lines = desc.split("\n")
        if len(lines) != 2 or any(not line or line != line.strip()
                                  or len(line) > MAX_LINE_CHARS for line in lines):
            continue
        if not lines[0].endswith(("로", "라")) or not _END.search(lines[1]):
            continue
        if not _PLAIN_LINE.fullmatch(lines[0]) or not _PLAIN_LINE.fullmatch(lines[1].removesuffix(".")):
            continue
        if any(not re.search(r"[가-힣]", line) for line in lines):
            continue
        # Spaces must not turn a banned claim into an accepted synonym.
        compact = re.sub(r"\s+", "", desc).casefold()
        if any(re.sub(r"\s+", "", word).casefold() in compact for word in _PART_BANNED):
            continue
        out[point] = desc
    return out


async def generate(settings, seller_labels, page_labels, product, analysis, *, timeout_seconds=None):
    """Return separate arrays using at most one existing complete_json invocation.

    The shared provider client's established fallback policy is unchanged. This
    wrapper adds no correction/retry call and no part-description fallback sentence.
    """
    sellers, parts = unique_labels(seller_labels or []), unique_labels(page_labels or [])
    if not parts:
        return {"featureCopy": await feature_copy.generate(
            settings, sellers, product, analysis, timeout_seconds=timeout_seconds), "partDescriptionCopy": []}
    seller_hits = {label: feature_copy.lookup(label) for label in sellers}
    misses = [label for label, desc in seller_hits.items() if desc is None]
    part_hits = {}
    if misses or parts:
        try:
            raw, _provider = await feature_copy.complete_json(
                settings, build_prompt(misses, parts, product, analysis), copy_schema(),
                timeout_seconds=timeout_seconds)
            if isinstance(raw, dict):
                seller_rows = raw.get("featureCopy")
                if isinstance(seller_rows, list):
                    seller_hits.update(feature_copy.validate({"items": seller_rows}, misses))
                part_hits = validate_parts(raw.get("partDescriptionCopy"), parts)
        except Exception as error:  # Copy must never gate the detail-page job.
            log.warning("page part copy generation failed: %r", error)
    return {"featureCopy": [{"point": label, "desc": seller_hits[label]} for label in sellers if seller_hits.get(label)],
            "partDescriptionCopy": [{"point": label, "desc": part_hits[label]} for label in parts if part_hits.get(label)]}


def unique_labels(labels):
    return list(dict.fromkeys(label for label in labels
                              if isinstance(label, str) and label.strip()))


def part_labels(storyboard, analysis):
    candidates = (analysis.get("detailRecommendations") or {}).get("candidates") or []
    labels = []
    for _key, cards in groupby(storyboard, key=lambda card: card.get("pageSectionKey")):
        run = list(cards)
        if not run or run[0].get("pageSectionKind") != "points":
            continue
        # Use the same selection as the card renderer: removed sources disappear,
        # explicit point items come first, and remaining detail photos follow.
        for card in point_photos_for_run(run, storyboard):
            item = card.get("pointItem") or {}
            if item.get("kind") == "wornCrop":
                labels.append(item.get("label"))
            elif card.get("detailTargetId") is not None:
                labels.append(next((candidate.get("label") for candidate in candidates
                                    if isinstance(candidate, dict)
                                    and str(candidate.get("id")) == str(card["detailTargetId"])), None))
    return unique_labels(labels)


def partition_copy(groups, seller_labels, page_labels, analysis):
    """Merge only requested output into its owning field; failures keep saved copy."""
    patch = {}
    for field, labels in (("featureCopy", seller_labels), ("partDescriptionCopy", page_labels)):
        wanted = set(labels)
        items = groups.get(field) if isinstance(groups, dict) else []
        if not isinstance(items, list):
            continue
        selected = [item for item in items or [] if isinstance(item, dict)
                    and isinstance(item.get("point"), str) and item["point"] in wanted
                    and isinstance(item.get("desc"), str) and item["desc"].strip()]
        if selected:
            patch[field] = feature_copy.merge_stored(analysis.get(field), selected)
    return patch
