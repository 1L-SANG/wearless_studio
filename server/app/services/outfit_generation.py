"""Frozen outfit selections and authoritative catalog images for generation."""
from copy import deepcopy
import asyncio
import hashlib

from .. import repo
from ..agents.gemini_image import InlineImage
from . import matching, outfit

EXTRA_ROLES = ("inner", "footwear", "bag", "accessory")
ROLE_TYPES = {"inner": "inner", "footwear": "shoes", "bag": "bag", "accessory": "accessory"}
ROLE_LABELS = {
    "inner": "OUTFIT INNER — the selected actual inner garment; preserve its type, color, neckline and design",
    "footwear": "OUTFIT FOOTWEAR — the selected actual shoes; preserve their shape, color and design where visible",
    "bag": "OUTFIT BAG — the selected actual bag; preserve its shape, color and design where visible",
    "accessory": "OUTFIT ACCESSORY — the selected actual accessory; preserve its shape, color and design where visible",
}


class OutfitReferenceError(ValueError):
    def __init__(self, role):
        self.role = role
        super().__init__(f"outfit_reference_unavailable:{role}")


def freeze(value, clothing_type: str) -> dict | None:
    """Keep only IDs and provenance. None denotes a pre-outfit legacy generation."""
    if value is None:
        return None
    complement = matching.complementary_type(clothing_type)
    allowed = {"footwear", "bag", "accessory"}
    if clothing_type == "outer":
        allowed.add("inner")
    if complement:
        allowed.add(complement)
    if not isinstance(value, dict) or value.get("version") != 1 or not isinstance(value.get("slots"), dict):
        raise OutfitReferenceError("snapshot")
    slots = {}
    for role, slot in value["slots"].items():
        if role not in allowed or not isinstance(slot, dict):
            raise OutfitReferenceError(role)
        identifier, origin = slot.get("itemId"), slot.get("origin")
        if (identifier is not None and (not isinstance(identifier, str) or not identifier.strip())) or origin not in ("ai", "user"):
            raise OutfitReferenceError(role)
        slots[role] = {"itemId": identifier, "origin": origin}
    return {"version": 1, "slots": slots}


def from_payload(payload: dict, analysis: dict, clothing_type: str) -> dict | None:
    value = payload.get("outfitSelectionsSnapshot") if "outfitSelectionsSnapshot" in payload else (
        outfit.selection_snapshot(analysis["outfitSelections"], clothing_type) if analysis.get("outfitSelections") else None)
    return freeze(value, clothing_type)


def complement_ids(snapshot, clothing_type: str, legacy_ids=()) -> list[str]:
    if snapshot is None:
        return list(legacy_ids or ())[:1]
    role = matching.complementary_type(clothing_type)
    selected = (snapshot.get("slots") or {}).get(role) or {}
    return [selected["itemId"]] if selected.get("itemId") else []


def with_complement(snapshot, clothing_type: str, matching_ids, *, origin=None):
    if snapshot is None:
        return None
    out = deepcopy(snapshot)
    role = matching.complementary_type(clothing_type)
    if role:
        old = out["slots"].get(role) or {}
        out["slots"][role] = {"itemId": next(iter(matching_ids or ()), None),
                              "origin": origin or old.get("origin") or "user"}
    return out


def for_cut(snapshot, clothing_type: str, spec: dict):
    """A card override replaces its one complementary garment, never other roles."""
    matches = list(spec.get("matchIds") or spec.get("match_ids") or ())[:2 if snapshot is None else 1]
    if snapshot is None or matches or spec.get("matchIdsOrigin") == "user":
        return with_complement(snapshot, clothing_type, matches), matches
    return deepcopy(snapshot), complement_ids(snapshot, clothing_type)


def ordered_images(outfit_images, matching_images):
    return ([image for role, image in outfit_images if role == "inner"]
            + list(matching_images)
            + [image for role, image in outfit_images if role != "inner"])


async def resolve_images(app, *, snapshot, clothing_type, user_id, project_id,
                         mannequin_only=False, reference_assets=None,
                         resolved_assets=None) -> list[tuple[str, InlineImage]]:
    """Catalog joins prove seed/public or current-project ownership; client assets are unused."""
    selected = freeze(snapshot, clothing_type)
    slots = (selected or {}).get("slots") or {}
    roles = ("inner",) if mannequin_only else EXTRA_ROLES
    wanted = [(role, slots[role]["itemId"]) for role in roles
              if slots.get(role, {}).get("itemId")]
    complement = matching.complementary_type(clothing_type)
    complement_id = slots.get(complement, {}).get("itemId")
    if not wanted and not complement_id:
        return []
    assets = []
    expected = {row["role"]: row for row in reference_assets or ()}
    async with app.state.pool.connection() as conn:
        rows = await repo.list_active_matching_items(conn, user_id, project_id)
        catalog = {str(row["id"]): row for row in rows}
        if complement_id:
            row = catalog.get(complement_id)
            if (not row or row.get("is_active") is not True
                    or row.get("clothing_type") != complement
                    or not row.get("image_asset_id") or not row.get("image_key")):
                raise OutfitReferenceError(complement)
        for role, identifier in wanted:
            row = catalog.get(identifier)
            if (not row or row.get("is_active") is not True
                    or row.get("clothing_type") != ROLE_TYPES[role]
                    or row.get("is_custom") or row.get("owner_user_id")
                    or not row.get("image_asset_id") or not row.get("image_key")):
                raise OutfitReferenceError(role)
            binding = expected.get(role)
            if binding and (binding.get("itemId") != identifier
                            or binding.get("assetId") != str(row["image_asset_id"])):
                raise OutfitReferenceError(role)
            asset = await repo.get_asset_for_user(conn, user_id, row["image_asset_id"])
            if (not asset or asset.get("source") != "seed"
                    or asset.get("r2_key") != row["image_key"]):
                raise OutfitReferenceError(role)
            assets.append((role, identifier, str(row["image_asset_id"]), asset))
    images = []
    for role, identifier, asset_id, asset in assets:
        try:
            data = await asyncio.to_thread(app.state.r2.get_bytes, asset["r2_key"])
            if not data:
                raise ValueError("empty outfit image")
            digest = hashlib.sha256(data).hexdigest()
            if expected.get(role) and expected[role].get("imageSha256") != digest:
                raise OutfitReferenceError(role)
            images.append((role, InlineImage(asset["mime_type"], data)))
            if resolved_assets is not None:
                resolved_assets.append({"role": role, "itemId": identifier,
                                        "assetId": asset_id, "imageSha256": digest})
        except Exception as exc:
            raise OutfitReferenceError(role) from exc
    return images
