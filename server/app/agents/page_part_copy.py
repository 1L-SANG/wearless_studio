"""Descriptions for the actual point cards, separate from seller feature copy."""

from itertools import groupby

from . import feature_copy
from .page_template_assembler import point_photos_for_run


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


def partition_copy(items, seller_labels, page_labels, analysis):
    """Merge only requested output into its owning field; failures keep saved copy."""
    patch = {}
    for field, labels in (("featureCopy", seller_labels), ("partDescriptionCopy", page_labels)):
        wanted = set(labels)
        selected = [item for item in items or [] if isinstance(item, dict)
                    and isinstance(item.get("point"), str) and item["point"] in wanted
                    and isinstance(item.get("desc"), str) and item["desc"].strip()]
        if selected:
            patch[field] = feature_copy.merge_stored(analysis.get(field), selected)
    return patch
