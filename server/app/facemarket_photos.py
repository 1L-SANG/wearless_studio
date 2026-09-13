"""One logical photo slot across new and historical enrollment records.

Choose a physical row before checking its state: an unavailable canonical row
must never reveal or approve the superseded legacy photo.
"""

import re

LEGACY_SLOT_ALIASES = {"front": "face01", "angle45": "face03", "side": "face05"}
ASSET_SOURCE_SLOTS = ("face01", "face03", "face05")


def canonical_photo_slot(slot: str) -> str:
    return LEGACY_SLOT_ALIASES.get(slot, slot)


def photo_slot_candidates(slot: str) -> tuple[str, ...]:
    canonical = canonical_photo_slot(slot)
    return (canonical,) + tuple(old for old, new in LEGACY_SLOT_ALIASES.items() if new == canonical)


def resolve_photo_rows(rows: list[dict], slots) -> list[dict]:
    by_angle = {row.get("angle"): row for row in rows}
    selected = []
    for slot in dict.fromkeys(canonical_photo_slot(slot) for slot in slots):
        row = next((by_angle[name] for name in photo_slot_candidates(slot) if name in by_angle), None)
        if row is not None:
            selected.append(row)
    return selected


def preferred_photo_predicate(photo_alias: str, enrollment_expression: str, slot: str = "face01") -> str:
    """JOIN predicate selecting at most one physical photo, including legacy rows."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", photo_alias):
        raise ValueError("invalid photo alias")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*", enrollment_expression):
        raise ValueError("invalid enrollment expression")
    names = photo_slot_candidates(slot)
    if not all(re.fullmatch(r"[a-z0-9]+", name) for name in names):
        raise ValueError("invalid photo slot")
    candidates = ", ".join(f"'{name}'" for name in names)
    canonical = names[0]
    return (
        f"{photo_alias}.enrollment_id = {enrollment_expression} "
        f"and {photo_alias}.angle in ({candidates}) "
        f"and ({photo_alias}.angle = '{canonical}' or not exists ("
        "select 1 from fm_biometric_enrollment_photos canonical_photo "
        f"where canonical_photo.enrollment_id = {enrollment_expression} "
        f"and canonical_photo.angle = '{canonical}'))"
    )
