"""ADR-0022. Actual catalog IDs, user locks and source-bound recommendations."""
import asyncio
from collections import Counter, OrderedDict
from copy import deepcopy
from hashlib import sha256
from itertools import product as combinations
import hmac
import json
import logging
import math
import re
import time

from ..agents import color_harmony, outfit_recommender, style_affinity
from . import matching, matching_cutout, retrieval

log = logging.getLogger(__name__)
VERSION = 1
HANDOFF_KEY = "outfitRecommendationHandoff"
ROLE_TYPES = {"inner": "inner", "top": "top", "bottom": "bottom", "footwear": "shoes",
              "bag": "bag", "accessory": "accessory"}
_ROLES = {"outer": ("inner", "bottom", "footwear", "bag", "accessory"),
          "top": ("bottom", "footwear", "bag", "accessory"),
          "bottom": ("top", "footwear", "bag", "accessory"),
          "dress": ("footwear", "bag", "accessory")}
_CACHE_TTL = 120
_CACHE_MAX = 128
_cache = OrderedDict()
_inflight = {}
PROP_ROLES = ("bag", "accessory")


class OutfitSelectionError(ValueError):
    pass


def eligible_slots(clothing_type, sub_category=None):
    return _ROLES.get(clothing_type, ())


def clothing_pools(pools):
    return {role: pool for role, pool in pools.items() if role not in PROP_ROLES}


def complementary_slot(clothing_type):
    return {"outer": "bottom", "top": "bottom", "bottom": "top"}.get(clothing_type)


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def clear_cache():
    _cache.clear()


def valid_item(item, role, clothing_type, genders=None):
    if not isinstance(item, dict) or not item.get("is_active"):
        return False
    if item.get("clothing_type") != ROLE_TYPES.get(role):
        return False
    if not item.get("image_asset_id") or not item.get("image_key") or not item.get("thumb_key"):
        return False
    custom = bool(item.get("is_custom") or item.get("owner_user_id"))
    if custom and role != complementary_slot(clothing_type):
        return False
    return custom or not genders or item.get("gender") == "unisex" or item.get("gender") in genders


def category_key(item):
    return str((item or {}).get("category") or "").strip().casefold()


def normalize_selections(value, clothing_type, *, sub_category=None, legacy_match_items=None,
                         allowed_items=None, strict=False, genders=None, prune_invalid_ai=False):
    """Keep verified role IDs, with up to three accessories. User null is a durable opt-out.

    `allowed_items` must come from an owner-scoped repo query. Impossible roles are
    removed on type changes before validating their old IDs.
    """
    if value is None:
        value = {"version": VERSION, "slots": {}}
    if not isinstance(value, dict) or type(value.get("version")) is not int or value.get("version") != VERSION or not isinstance(value.get("slots"), dict):
        if strict:
            raise OutfitSelectionError("invalid_outfit_selections")
        value = {"version": VERSION, "slots": {}}
    raw = value["slots"]
    if strict and set(raw) - set(ROLE_TYPES):
        raise OutfitSelectionError("invalid_outfit_role")
    rows = {str(item["id"]): item for item in (allowed_items or []) if isinstance(item, dict) and item.get("id")}
    result = {}
    for role in eligible_slots(clothing_type, sub_category):
        if role not in raw:
            continue
        entry = raw[role]
        if not isinstance(entry, dict) or entry.get("origin") not in ("ai", "user") or "itemId" not in entry:
            if strict:
                raise OutfitSelectionError("invalid_outfit_selection")
            continue
        identifier = entry["itemId"]
        if identifier is not None and (not isinstance(identifier, str) or not identifier or len(identifier) > 128):
            if strict:
                raise OutfitSelectionError("invalid_outfit_item")
            continue
        if identifier is not None and allowed_items is not None and not valid_item(rows.get(identifier), role, clothing_type, genders):
            if prune_invalid_ai and entry["origin"] == "ai":
                identifier = None
            elif strict:
                raise OutfitSelectionError("outfit_item_unavailable")
            else:
                continue
        result[role] = {"itemId": identifier, "origin": entry["origin"]}
        if role == "accessory" and identifier:
            extras = []
            seen = {identifier}
            categories = {category_key(rows.get(identifier, {}))} if allowed_items is not None else set()
            for extra in entry.get("extraIds", []) if isinstance(entry.get("extraIds"), list) else []:
                if not isinstance(extra, str) or not extra or len(extra) > 128 or extra in seen:
                    continue
                item = rows.get(extra)
                if allowed_items is not None and (not valid_item(item, role, clothing_type, genders)
                        or category_key(item) in categories):
                    continue
                extras.append(extra)
                seen.add(extra)
                if item:
                    categories.add(category_key(item))
                if len(extras) == 2:
                    break
            if extras:
                result[role]["extraIds"] = extras
    complement = complementary_slot(clothing_type)
    if complement and complement not in raw and legacy_match_items is not None:
        selected = sorted((item for item in legacy_match_items if isinstance(item, dict) and item.get("selected")),
                          key=lambda item: item.get("selOrder") or 99)[:1]
        if selected:
            legacy = {"version": VERSION, "slots": {complement: {"itemId": str(selected[0].get("id") or ""), "origin": "user"}}}
            result.update(normalize_selections(legacy, clothing_type, allowed_items=allowed_items, strict=strict)["slots"])
    optional = value.get("optional", {})
    index = value.get("setIndex", 0)
    if not isinstance(optional, dict) or any(type(v) is not bool for v in optional.values()) or set(optional) - {"bag", "accessory"}:
        if strict:
            raise OutfitSelectionError("invalid_outfit_optional")
        optional = {}
    if type(index) is not int or not 0 <= index <= 2:
        if strict:
            raise OutfitSelectionError("invalid_outfit_set_index")
        index = 0
    return {"version": VERSION, "slots": result, "setIndex": index,
            "optional": {role: optional.get(role, False) for role in ("bag", "accessory")}}


def merge_selections(previous, incoming, clothing_type):
    old = normalize_selections(previous, clothing_type)
    if incoming is None:
        raise OutfitSelectionError("invalid_outfit_selections")
    new = normalize_selections(incoming, clothing_type, strict=True)
    updates = {role: entry for role, entry in new["slots"].items()
               if entry["origin"] == "user" or (old["slots"].get(role) or {}).get("origin") != "user"}
    return {**old, "slots": {**old["slots"], **updates},
            "setIndex": new["setIndex"] if "setIndex" in incoming else old["setIndex"],
            "optional": {**old["optional"], **incoming.get("optional", {})}}


def apply_set(previous, recommendation, clothing_type, index):
    """Explicit set application replaces direct picks but keeps user nulls/off memory."""
    selections = normalize_selections(previous, clothing_type, strict=True)
    sets = (recommendation or {}).get("sets") or []
    if type(index) is not int or not 0 <= index < len(sets):
        raise OutfitSelectionError("invalid_outfit_set_index")
    for role in eligible_slots(clothing_type):
        old = selections["slots"].get(role) or {}
        if old.get("origin") == "user" and old.get("itemId") is None:
            continue
        if role in selections["optional"] and not selections["optional"][role]:
            continue
        identifier = (prop_pick(recommendation, role, index) if role in PROP_ROLES
                      else sets[index]["slots"].get(role))
        selections["slots"][role] = {"itemId": identifier, "origin": "ai"}
    selections["setIndex"] = index
    return selections


def initial_selections(previous, recommendation, clothing_type):
    """A new recommendation starts at set zero while retaining deliberate user intent."""
    fixed = normalize_selections(previous, clothing_type)
    selected = apply_set(fixed, recommendation, clothing_type, 0) if recommendation["sets"] else fixed
    selected["slots"].update({role: entry for role, entry in fixed["slots"].items() if entry["origin"] == "user"})
    selected["setIndex"] = 0
    return selected


def toggle_optional(previous, recommendation, clothing_type, role, enabled):
    selections = normalize_selections(previous, clothing_type, strict=True)
    if role not in selections["optional"] or type(enabled) is not bool:
        raise OutfitSelectionError("invalid_outfit_optional")
    selections["optional"][role] = enabled
    old = selections["slots"].get(role) or {}
    if enabled and old.get("origin") != "user":
        identifier = prop_pick(recommendation, role, selections["setIndex"])
        selections["slots"][role] = {"itemId": identifier, "origin": "ai"}
    return selections


def prop_pick(recommendation, role, index):
    picks = ((recommendation or {}).get("props", {}).get(role) or {}).get("picks") or []
    return picks[index % len(picks)] if picks else None


def selection_snapshot(value, clothing_type):
    selections = normalize_selections(value, clothing_type, strict=True)
    return {"version": VERSION, "slots": {role: entry for role, entry in selections["slots"].items()
            if role not in selections["optional"] or selections["optional"][role]}}


def generation_snapshot(analysis, clothing_type, items, *, sub_category=None, product=None, candidates_per_role=6):
    selections = normalize_selections((analysis or {}).get("outfitSelections"), clothing_type,
        sub_category=sub_category, legacy_match_items=(analysis or {}).get("matchClothing") or [], strict=True)
    context = product_context(product or {"clothingType": clothing_type}, analysis or {})
    for role in PROP_ROLES:
        old = selections["slots"].get(role) or {}
        prop = (((analysis or {}).get("outfitRecommendation") or {}).get("props") or {}).get(role) or {}
        if selections["optional"][role] and old.get("origin") != "user" and (not old.get("itemId") or prop.get("status") == "pending"):
            picks = prop.get("picks") or deterministic_picks(
                props_candidates(items, context, role, limit=candidates_per_role), role)
            identifier = picks[selections["setIndex"] % len(picks)] if picks else None
            selections["slots"][role] = {"itemId": identifier, "origin": "ai"}
    # Disabled remembered items do not enter validation, generation or asset records.
    snapshot = selection_snapshot(selections, clothing_type)
    validated = normalize_selections(snapshot, clothing_type, allowed_items=items, strict=True,
        genders=["women"] if clothing_type == "dress" else (analysis or {}).get("targetGenders"))
    return {"version": VERSION, "slots": validated["slots"]}


async def load_items(conn, user_id, project_id):
    """Owner-scoped catalog plus the same active-cutout signal as match-candidates."""
    from .. import repo
    items = await repo.list_active_matching_items(conn, user_id, project_id)
    if not any(item.get("is_custom") for item in items):
        return items
    active = False
    try:
        active = await repo.has_active_matching_cutout_job(conn, project_id)
    except Exception as exc:
        log.warning("outfit cutout state unavailable: %s", type(exc).__name__)
    return [{**item, "has_active_cutout_job": active} if item.get("is_custom") else item for item in items]


def item_to_api(r2, item):
    return {"id": str(item["id"]), "name": item.get("name") or "", "gender": item.get("gender"),
        "thumb": r2.public_url(item["thumb_key"]), "thumbnailUrl": r2.public_url(item["thumb_key"]),
        "boardUrl": matching.board_url_for(r2, item, has_active_job=bool(item.get("has_active_cutout_job"))),
        "imageUrl": r2.public_url(item["image_key"]), "clothingType": item.get("clothing_type"),
        "category": item.get("category"), "colorName": item.get("color_name"),
        "colorGroup": item.get("color_group"), "colorBrightness": item.get("color_brightness"),
        "fit": item.get("fit"), "length": item.get("length"), "fitCategory": matching.fit_category(item),
        "selected": False, "isCustom": bool(item.get("is_custom")), "isCompatible": True,
        "cutoutStatus": matching_cutout.cutout_status_for(is_custom=bool(item.get("is_custom")),
            image_meta=item.get("image_meta"), has_active_job=bool(item.get("has_active_cutout_job")))}


def sync_legacy(analysis, selections, items, r2=None):
    """The complementary slot is the single authority for legacy selection flags."""
    role = next((role for role in ("top", "bottom") if role in selections["slots"]), None)
    selected_id = selections["slots"][role]["itemId"] if role else None
    old = (analysis or {}).get("matchClothing") or []
    by_id = {str(item["id"]): item for item in items if isinstance(item, dict) and item.get("id")}
    identifiers = list(dict.fromkeys([str(item.get("id")) for item in old if isinstance(item, dict)]
                                    + ([selected_id] if selected_id else [])))
    rebuilt = []
    for identifier in identifiers:
        item = by_id.get(identifier)
        if not item or item.get("clothing_type") not in ("top", "bottom"):
            continue
        display = item_to_api(r2, item) if r2 else {"id": identifier, "clothingType": item.get("clothing_type")}
        display["selected"] = identifier == selected_id
        if display["selected"]:
            display["selOrder"] = 1
        rebuilt.append(display)
    return {"outfitSelections": selections, "matchClothing": rebuilt,
            "matchIds": [selected_id] if selected_id else []}


def product_context(product, analysis):
    clothing_type = product.get("clothingType") or product.get("clothing_type") or "top"
    clothing_type = clothing_type if isinstance(clothing_type, str) else "top"
    colors = product.get("colors")
    colors = [color for color in colors if isinstance(color, dict)] if isinstance(colors, list) else []
    base = next((color for color in colors if color.get("isBase")), colors[0] if colors else {})
    suggestions = analysis.get("swatchSuggestions")
    suggestions = [entry for entry in suggestions if isinstance(entry, dict)] if isinstance(suggestions, list) else []
    suggested = next((entry for entry in suggestions if base.get("id") and entry.get("colorGroupId") == base["id"]),
                     suggestions[0] if suggestions else {})
    def text(value, maximum=80):
        return value[:maximum] if isinstance(value, str) else None
    materials = analysis.get("materials")
    materials = materials if isinstance(materials, list) else []
    tags = analysis.get("styleTags")
    tags = tags if isinstance(tags, list) else []
    genders = analysis.get("targetGenders")
    genders = genders if isinstance(genders, list) else []
    return {"clothingType": clothing_type, "subCategory": text(analysis.get("subCategory"), 40),
            "customCategory": text(analysis.get("customCategory"), 100), "fit": text(analysis.get("fit"), 40),
            "materials": [{"name": text(item.get("name")), "ratio": item.get("ratio")}
                          for item in materials[:8] if isinstance(item, dict) and isinstance(item.get("name"), str)
                          and (item.get("ratio") is None or type(item.get("ratio")) in (int, float)
                               and math.isfinite(item["ratio"]))],
            "styleTags": [tag[:40] for tag in tags[:24] if isinstance(tag, str)],
            "targetGenders": ["women"] if clothing_type == "dress" else [gender for gender in genders if gender in ("women", "men")],
            "colors": [{"id": text(color.get("id")), "swatchId": text(color.get("swatchId")), "name": text(color.get("name")),
                        "isBase": bool(color.get("isBase"))} for color in colors],
            "productColor": color_harmony.matching_color(text(base.get("swatchId")) or text(suggested.get("swatchId")),
                            text(base.get("name")) if base.get("swatchId") else
                            text(base.get("name")) or text(suggested.get("colorName")))}


def promotion_context_matches(signed, current):
    """Compare known product meaning; upload/group IDs and later autofill are not facts."""
    for key in ("clothingType", "subCategory", "customCategory", "fit"):
        if signed.get(key) is not None and signed[key] != current.get(key):
            return False
    for key in ("targetGenders", "styleTags"):
        if signed.get(key) and sorted(signed[key]) != sorted(current.get(key) or []):
            return False
    def color_facts(context):
        colors = context.get("colors") or []
        base_index = next((index for index, color in enumerate(colors) if color.get("isBase")), 0)
        return [(str(color.get("swatchId") or "").strip().casefold(),
                 str(color.get("name") or "").strip().casefold(), index == base_index)
                for index, color in enumerate(colors)]

    known_colors = [color for color in color_facts(signed) if color[0] or color[1]]
    current_colors = color_facts(current)

    def compatible(known, actual):
        swatch, name, base = known
        current_swatch, current_name, current_base = actual
        if base != current_base or swatch and swatch != current_swatch or name and name != current_name:
            return False
        if swatch and not name and current_name:
            groups = color_harmony.color_name_groups(current_name)
            # Free-form labels such as 모카 add no contradictory known fact.
            # A recognized different color (e.g. 레드 on gray) still conflicts.
            return not groups or swatch in groups
        return True

    # Match each signed fact once. IDs may be reassigned and unknown colors may be
    # filled, but neither may hide a removed known color (including duplicates).
    assigned = {}
    def match(index, visited):
        for candidate, actual in enumerate(current_colors):
            if candidate in visited or not compatible(known_colors[index], actual):
                continue
            visited.add(candidate)
            if candidate not in assigned or match(assigned[candidate], visited):
                assigned[candidate] = index
                return True
        return False
    if not all(match(index, set()) for index in range(len(known_colors))):
        return False
    def material_meaning(context):
        return {item["name"].strip().casefold(): item.get("ratio") for item in context.get("materials") or []
                if isinstance(item, dict) and isinstance(item.get("name"), str)}
    known_materials, current_materials = material_meaning(signed), material_meaning(current)
    if known_materials and (set(known_materials) != set(current_materials) or any(
        ratio is not None and ratio != current_materials.get(name) for name, ratio in known_materials.items())):
        return False
    return True


def _diverse_candidates(scored, fixed_item, limit, *, category_cap=False):
    """Greedy score minus 0.25 * maximum tag Jaccard overlap, color cap two.

    Reserve a seller's fixed item first; it counts toward its color cap. Unknown
    colors share one bucket, so missing metadata cannot bypass the bound.
    """
    def group(item):
        return str(item.get("color_group") or "").strip().casefold()

    def tags(item):
        return {tag.strip().casefold() for tag in item.get("style_tags") or [] if isinstance(tag, str) and tag.strip()}

    selected = [fixed_item] if fixed_item else []
    counts = Counter(group(item) for item in selected)
    categories = Counter(category_key(item) for item in selected)
    remaining = [(item, score) for item, score in scored if item is not fixed_item]
    while len(selected) < limit:
        eligible = [(item, score) for item, score in remaining if counts[group(item)] < 2
                    and (not category_cap or categories[category_key(item)] < 2)]
        if not eligible:
            break

        def priority(pair):
            item, score = pair
            candidate_tags = tags(item)
            overlap = max((len(candidate_tags & tags(chosen)) / len(candidate_tags | tags(chosen))
                           if candidate_tags | tags(chosen) else 0 for chosen in selected), default=0)
            return (-round(score - 0.25 * overlap, 9), item.get("sort_order", 0), str(item["id"]))

        item, score = min(eligible, key=priority)
        selected.append(item)
        counts[group(item)] += 1
        categories[category_key(item)] += 1
        remaining = [(other, value) for other, value in remaining if other is not item]
    return selected


def candidate_pools(items, context, selections=None, *, limit=None, roles=None, props=False):
    clothing_type = context["clothingType"]
    pools = {}
    for role in roles if roles is not None else eligible_slots(clothing_type, context.get("subCategory")):
        pool = [item for item in items if valid_item(item, role, clothing_type, context.get("targetGenders"))]
        # Existing score combines style affinity and product-color harmony; filtering
        # by each role is already done, so reuse it without the complementary filter.
        tags = context.get("styleTags") or []
        affinity = style_affinity.affinity_map()
        style_scores = [(item, retrieval._style_affinity_score(item, tags, affinity)) for item in pool]
        maximum = max((score for _, score in style_scores), default=0)
        color = context.get("productColor")
        harmony = color_harmony.harmony_map()
        scored = [(item, round(0.7 * (score / maximum if maximum else 0)
                  + (0.3 * color_harmony.outfit_color_score(color,
                     color_harmony.matching_color(item.get("color_group"), item.get("color_name"), item.get("category")),
                     clothing_type, harmony) if color else 0), 9)) for item, score in style_scores]
        ranked = [item for item, _score in sorted(scored, key=lambda pair: (-pair[1], pair[0].get("sort_order", 0), str(pair[0]["id"])))]
        fixed = ((selections or {}).get("slots") or {}).get(role) or {}
        fixed_item = (next((item for item in ranked if str(item["id"]) == fixed.get("itemId")), None)
                      if fixed.get("origin") == "user" else None)
        ranked = ([fixed_item] + [item for item in ranked if item is not fixed_item]) if fixed_item else ranked
        pools[role] = _diverse_candidates(scored, fixed_item, limit, category_cap=props and role == "accessory") if limit is not None else ranked
    return pools


def _source_binding(source_images, slots):
    slots = slots or ["Front", *(["Detail"] * (len(source_images) - 1))]
    if (not 1 <= len(source_images) <= 4 or len(source_images) != len(slots)
        or any(slot not in ("Front", "Back", "Detail", "BackDetail") for slot in slots)
        or any(not isinstance(data, bytes) or not data for data, _mime in source_images)):
        raise OutfitSelectionError("invalid_outfit_source")
    return [{"slot": slot, "sha256": sha256(data).hexdigest()} for (data, _mime), slot in zip(source_images, slots)]


def recommendation_key(settings, product, analysis, source_images, slots, items, selections):
    metadata = [{key: item.get(key) for key in ("id", "name", "fit", "length", "clothing_type", "gender", "category", "color_group", "color_name",
                  "style_tags", "is_active", "is_custom", "image_asset_id", "image_key", "thumb_key", "image_meta", "sort_order")}
                for item in sorted(items, key=lambda item: str(item.get("id")))]
    return sha256(_canonical({"model": settings.model_text_gemini_outfit,
        "promptVersion": outfit_recommender.PROMPT_VERSION, "candidatesPerRole": settings.outfit_candidates_per_role,
        "recommendationEnabled": settings.outfit_recommendation_enabled,
        "sources": _source_binding(source_images, slots), "context": product_context(product, analysis),
        "fixed": {role: entry for role, entry in selections["slots"].items() if entry["origin"] == "user"},
        "catalog": metadata})).hexdigest()


def fallback_sets(pools, fixed=None):
    """Up to three combinations from diverse shortlists, maximizing changed roles.

    Start with the first choice in each role, then maximize minimum Hamming
    distance from earlier sets. Sum of shortlist ranks and rank tuple break ties.
    Callers use candidate_pools(limit=...) so paid and free paths share selection.
    """
    pools = clothing_pools(pools)
    if not any(pools.values()):
        return []
    roles = list(pools)
    choices = []
    for role, pool in pools.items():
        identifiers = [str(item["id"]) for item in pool] or [None]
        lock = ((fixed or {}).get("slots") or {}).get(role) or {}
        if lock.get("origin") == "user" and (lock.get("itemId") is None or lock.get("itemId") in identifiers):
            identifiers = [lock["itemId"]]
        choices.append(identifiers)
    ranked = []
    for _ in range(3):
        candidates = (ranks for ranks in combinations(*(range(len(choice)) for choice in choices)) if ranks not in ranked)

        def priority(ranks):
            distance = min((sum(a != b for a, b in zip(ranks, previous)) for previous in ranked), default=0)
            return (-distance, sum(ranks), ranks)

        chosen = min(candidates, key=priority, default=None)
        if chosen is None:
            break
        ranked.append(chosen)
    return [{"slots": {role: choices[i][ranks[i]] for i, role in enumerate(roles)},
             "reason": "등록된 아이템으로 코디를 골랐어요.", "concept": f"catalog-{index + 1}"}
            for index, ranks in enumerate(ranked)]


def validate_sets(value, pools, fixed=None, *, require_concept=False, enforce_fixed=False):
    pools = clothing_pools(pools)
    if not isinstance(value, list) or not 1 <= len(value) <= 3:
        raise OutfitSelectionError("invalid_outfit_response")
    seen, concepts, valid = set(), set(), []
    variable_roles = [role for role, pool in pools.items() if len(pool) >= 2]
    for entry in value:
        if isinstance(entry, dict) and isinstance(entry.get("slots"), dict) and not require_concept:
            # Saved v1/v2 sets may contain props, but props is now their only authority.
            entry = {**entry, "slots": {role: identifier for role, identifier in entry["slots"].items() if role not in PROP_ROLES}}
        if not isinstance(entry, dict) or not isinstance(entry.get("slots"), dict) or set(entry["slots"]) != set(pools):
            raise OutfitSelectionError("invalid_outfit_response")
        slots = entry["slots"]
        for role, pool in pools.items():
            identifier = slots[role]
            if identifier is not None and (not isinstance(identifier, str) or identifier not in {str(item["id"]) for item in pool}):
                raise OutfitSelectionError("invalid_outfit_response")
            locked_empty = ((fixed or {}).get("slots", {}).get(role) or {}) == {"itemId": None, "origin": "user"}
            if pool and identifier is None and role not in ("bag", "accessory") and not locked_empty:
                raise OutfitSelectionError("incomplete_outfit_response")
            lock = ((fixed or {}).get("slots") or {}).get(role) or {}
            if enforce_fixed and lock.get("origin") == "user" and identifier != lock.get("itemId"):
                raise OutfitSelectionError("changed_fixed_outfit_selection")
        concept = entry.get("concept")
        if require_concept or "concept" in entry:
            if not isinstance(concept, str) or len(concept) > 48 or re.fullmatch(r"[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*", concept) is None:
                raise OutfitSelectionError("invalid_outfit_concept")
        reason = outfit_recommender.display_reason(entry.get("reason"))
        identity = _canonical({role: slots[role] for role in variable_roles})
        if identity in seen or concept is not None and concept in concepts:
            continue
        seen.add(identity)
        concepts.add(concept)
        valid.append({"slots": dict(slots), "reason": reason, **({"concept": concept} if concept is not None else {})})
    return valid


def recommendation_display(recommendation, pools, r2):
    out = deepcopy(recommendation)
    ids = {role: (set((out.get("props", {}).get(role) or {}).get("picks") or []) if role in PROP_ROLES
                  else {entry["slots"].get(role) for entry in out["sets"]}) for role in pools}
    out["candidates"] = {role: [{**item_to_api(r2, item), "inSets": str(item["id"]) in ids[role]}
                                for item in pool] for role, pool in pools.items()}
    out["roleStatus"] = {role: ((out.get("props", {}).get(role) or {}).get("status", "fallback")
        if role in PROP_ROLES else out["status"]) if pool else "unavailable" for role, pool in pools.items()}
    return out


def prepare_recommendation(settings, r2, product, analysis, source_images, items, *, slots=None):
    context = product_context(product, analysis)
    selections = normalize_selections(analysis.get("outfitSelections"), context["clothingType"],
                                     legacy_match_items=analysis.get("matchClothing") or [])
    key = recommendation_key(settings, product, analysis, source_images, slots, items, selections)
    pools = candidate_pools(items, context, selections)
    shortlists = candidate_pools(items, context, selections, limit=settings.outfit_candidates_per_role)
    has_front = slots is None or "Front" in slots
    status = ("pending" if has_front and settings.gemini_api_key and settings.outfit_recommendation_enabled else "fallback") if any(clothing_pools(pools).values()) else "unavailable"
    recommendation = recommendation_display({"version": VERSION, "status": status,
        "model": settings.model_text_gemini_outfit, "contextKey": key, "sets": fallback_sets(shortlists, selections),
        **({"fallbackReady": True, "pendingSince": time.time()} if status == "pending" else {})}, pools, r2)
    for role in PROP_ROLES:
        key_for_role = props_key(settings, product, analysis, source_images, slots, items, role)
        previous_rec = analysis.get("outfitRecommendation") or {}
        if (previous_rec.get("propsContextKeys") or {}).get(role) == key_for_role:
            copy_prop(recommendation, previous_rec, role)
    selections = initial_selections(selections, recommendation, context["clothingType"])
    return {"outfitRecommendation": recommendation,
            **sync_legacy({"matchClothing": recommendation["candidates"].get(complementary_slot(context["clothingType"]), [])}, selections, items, r2)}


def _cache_result(key, fields):
    result = deepcopy(fields)
    recommendation = result.get("outfitRecommendation") or {}
    base_key = recommendation.get("contextKey")
    if key == base_key and key in _cache:
        prior = _cache[key][1].get("outfitRecommendation") or {}
        for role in PROP_ROLES:
            incoming = (recommendation.get("props") or {}).get(role)
            previous = (prior.get("props") or {}).get(role)
            if previous and (incoming is None or incoming.get("status") == "pending"
                    and previous.get("status") != "pending"
                    and (recommendation.get("propsContextKeys") or {}).get(role) == (prior.get("propsContextKeys") or {}).get(role)):
                copy_prop(recommendation, prior, role)
    _cache[key] = (time.monotonic(), result)
    _cache.move_to_end(key)
    if base_key and key != base_key:
        base = deepcopy(_cache[base_key][1] if base_key in _cache else result)
        for role, prop_key in (recommendation.get("propsContextKeys") or {}).items():
            if prop_key == key:
                copy_prop(base["outfitRecommendation"], recommendation, role)
        _cache_result(base_key, base)
    while len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)


def refresh_board_urls(fields, items, r2):
    """Refresh display art without changing the recommendation key, sets or choices."""
    result = deepcopy(fields)
    by_id = {str(item["id"]): item for item in items}
    groups = [result.get("matchClothing") or [],
              *((result.get("outfitRecommendation") or {}).get("candidates") or {}).values()]
    for group in groups:
        for item in group:
            current = by_id.get(str(item.get("id")), {})
            item["boardUrl"] = matching.board_url_for(r2, current,
                has_active_job=bool(current.get("has_active_cutout_job")))
    return result


def read_fields(analysis, timeout_seconds=30, *, include_internal=False, items=None, r2=None):
    fields = {key: deepcopy(analysis[key]) for key in ("outfitRecommendation", "outfitSelections", "matchClothing", "matchIds") if key in analysis}
    recommendation = fields.get("outfitRecommendation") or {}
    if (recommendation.get("status") == "pending"
            and time.time() - recommendation.get("pendingSince", time.time()) >= timeout_seconds):
        recommendation["status"] = "fallback"
        recommendation.pop("fallbackReady", None)
        recommendation.pop("pendingSince", None)
        recommendation["roleStatus"] = {role: "fallback" if pool else "unavailable"
                                        for role, pool in recommendation.get("candidates", {}).items()}
    for role, prop in recommendation.get("props", {}).items():
        deadline = (recommendation.get("propsExpiresAt") or {}).get(role)
        if prop.get("status") == "pending" and deadline is not None and time.time() >= deadline:
            prop["status"] = "fallback" if prop.get("picks") else "unavailable"
    if not include_internal:
        for entry in recommendation.get("sets", []):
            entry.pop("concept", None)
    return refresh_board_urls(fields, items, r2) if items is not None else fields


def read_recommendation(key):
    cached = _cache.get(key)
    if cached and time.monotonic() - cached[0] < _CACHE_TTL:
        return deepcopy(cached[1])
    _cache.pop(key, None)
    return None


def start_recommendation(settings, r2, product, analysis, source_images, items, *, slots=None, semaphore=None):
    fields = prepare_recommendation(settings, r2, product, analysis, source_images, items, slots=slots)
    key = fields["outfitRecommendation"]["contextKey"]
    cached = read_recommendation(key)
    if cached is not None:
        cached = refresh_board_urls(cached, items, r2)
        clothing_type = product_context(product, analysis)["clothingType"]
        current = normalize_selections(analysis.get("outfitSelections"), clothing_type,
                                       legacy_match_items=analysis.get("matchClothing") or [])
        same_context = (analysis.get("outfitRecommendation") or {}).get("contextKey") == key
        cached["outfitSelections"] = (current if same_context else
            initial_selections(current, cached["outfitRecommendation"], clothing_type))
        cached.update(sync_legacy(cached, cached["outfitSelections"], items, r2))
        return cached
    _cache_result(key, fields)
    if fields["outfitRecommendation"]["status"] != "pending" or key in _inflight:
        return fields
    async def complete():
        async def evaluate():
            return await _recommend_once(settings, r2, product_context(product, analysis),
                normalize_selections(analysis.get("outfitSelections"), product_context(product, analysis)["clothingType"],
                    legacy_match_items=analysis.get("matchClothing") or []),
                [source_images[slots.index("Front") if slots else 0]], items, key)
        async def bounded():
            if semaphore is None:
                return await evaluate()
            async with semaphore:
                return await evaluate()
        try:
            try:
                result = await asyncio.wait_for(bounded(), timeout=settings.outfit_timeout_seconds)
            except TimeoutError:
                result = deepcopy(fields)
                recommendation = result["outfitRecommendation"]
                recommendation["status"] = "fallback"
                recommendation.pop("fallbackReady", None)
                recommendation.pop("pendingSince", None)
                recommendation["roleStatus"] = {role: "fallback" if pool else "unavailable"
                    for role, pool in recommendation["candidates"].items()}
            _cache_result(key, result)
            return result
        finally:
            _inflight.pop(key, None)
    task = asyncio.create_task(complete(), name="outfit-recommendation")
    _inflight[key] = task
    return fields


async def recommend(settings, r2, product, analysis, source_images, items, *, slots=None):
    fields = start_recommendation(settings, r2, product, analysis, source_images, items, slots=slots)
    task = _inflight.get(fields["outfitRecommendation"]["contextKey"])
    if task is not None:
        fields = deepcopy(await asyncio.shield(task))
    return fields


async def _recommend_once(settings, r2, context, fixed, source_images, items, key):
    all_pools = candidate_pools(items, context, fixed)
    pools = clothing_pools(candidate_pools(items, context, fixed, limit=settings.outfit_candidates_per_role))
    sets = fallback_sets(pools, fixed)
    status = "fallback"
    try:
        raw = await asyncio.wait_for(outfit_recommender.recommend(settings, r2, source_images, pools,
                    context=context, fixed=fixed), timeout=settings.outfit_timeout_seconds)
        sets = validate_sets(raw.get("sets") if isinstance(raw, dict) else None, pools, fixed,
                             require_concept=True, enforce_fixed=True)
        status = "ready"
    except Exception as exc:
        log.warning("outfit visual recommendation fallback: %s", type(exc).__name__)
    recommendation = recommendation_display({"version": VERSION, "status": status,
        "model": settings.model_text_gemini_outfit, "contextKey": key, "sets": sets}, all_pools, r2)
    selections = initial_selections(fixed, recommendation, context["clothingType"])
    return {"outfitRecommendation": recommendation, **sync_legacy({"matchClothing": recommendation["candidates"].get(
            complementary_slot(context["clothingType"]), [])}, selections, items, r2)}


def issue_handoff(fields, source_images, slots, secret, *, context=None, now=None):
    return issue_bound_handoff(fields, _source_binding(source_images, slots), secret, context=context, now=now)


def issue_bound_handoff(fields, source_binding, secret, *, context=None, now=None):
    if not isinstance(secret, str) or len(secret) < 16:
        raise OutfitSelectionError("outfit_handoff_unavailable")
    recommendation = deepcopy(fields["outfitRecommendation"])
    # Assets and URLs are rebuilt on promotion. Only signed catalog IDs travel.
    recommendation["candidates"] = {role: [str(item["id"]) if isinstance(item, dict) else str(item)
                                  for item in pool] for role, pool in recommendation["candidates"].items()}
    issued = int(time.time()) if now is None else now
    token = {"purpose": "outfitRecommendation", "version": VERSION, "issuedAt": issued,
             "expiresAt": issued + 86400, "contract": {"outfitRecommendation": recommendation,
             "outfitSelections": deepcopy(fields["outfitSelections"]), "context": deepcopy(context or {}), "sourceBinding": deepcopy(source_binding)}}
    return {**token, "signature": hmac.new(secret.encode(), _canonical(token), "sha256").hexdigest()}


def verify_handoff(value, secret, *, now=None):
    current = int(time.time()) if now is None else now
    if not isinstance(secret, str) or len(secret) < 16 or not isinstance(value, dict):
        raise OutfitSelectionError("invalid_outfit_handoff")
    if set(value) != {"purpose", "version", "issuedAt", "expiresAt", "contract", "signature"}:
        raise OutfitSelectionError("invalid_outfit_handoff")
    token = {key: entry for key, entry in value.items() if key != "signature"}
    if not isinstance(value["signature"], str) or re.fullmatch(r"[0-9a-f]{64}", value["signature"]) is None:
        raise OutfitSelectionError("invalid_outfit_handoff")
    try:
        signature = hmac.new(secret.encode(), _canonical(token), "sha256").hexdigest()
    except (TypeError, ValueError):
        raise OutfitSelectionError("invalid_outfit_handoff") from None
    if (token["purpose"] != "outfitRecommendation" or token["version"] != VERSION
        or type(token["issuedAt"]) is not int or type(token["expiresAt"]) is not int
        or not token["issuedAt"] <= current <= token["expiresAt"]
        or token["expiresAt"] - token["issuedAt"] != 86400
        or not isinstance(value["signature"], str)
        or not hmac.compare_digest(value["signature"], signature)):
        raise OutfitSelectionError("invalid_outfit_handoff")
    return deepcopy(token["contract"])


def source_binding_matches(contract, source_images, slots):
    return contract.get("sourceBinding") == _source_binding(source_images, slots)


def props_candidates(items, context, role, *, limit):
    if role not in PROP_ROLES:
        raise OutfitSelectionError("invalid_outfit_props_role")
    return candidate_pools(items, context, limit=limit, roles=(role,), props=True)[role]


def props_key(settings, product, analysis, source_images, slots, items, role):
    if role not in PROP_ROLES:
        raise OutfitSelectionError("invalid_outfit_props_role")
    relevant = [item for item in items if item.get("clothing_type") == ROLE_TYPES[role]
                and not item.get("is_custom") and not item.get("owner_user_id")]
    base = recommendation_key(settings, product, analysis, source_images, slots, relevant, {"slots": {}})
    return sha256(_canonical({"productKey": base, "role": role,
        "promptVersion": outfit_recommender.PROPS_PROMPT_VERSION})).hexdigest()


def deterministic_picks(pool, role):
    ordered = list(dict.fromkeys(str(item["id"]) for item in pool))
    if role == "accessory":
        by_id = {str(item["id"]): item for item in pool}
        categories, first, rest = set(), [], []
        for identifier in ordered:
            category = category_key(by_id[identifier])
            (rest if category in categories else first).append(identifier)
            categories.add(category)
        ordered = first + rest
    return ordered[:3]


def validate_picks(value, pool, role):
    identifiers = {str(item["id"]): item for item in pool}
    if (not isinstance(value, list) or not 1 <= len(value) <= 3
        or any(not isinstance(identifier, str) or identifier not in identifiers for identifier in value)
        or len(set(value)) != len(value)):
        raise OutfitSelectionError("invalid_outfit_props_response")
    if role == "accessory" and len({category_key(identifiers[i]) for i in value}) < min(
            len(value), len({category_key(item) for item in pool})):
        raise OutfitSelectionError("invalid_outfit_props_categories")
    return list(value)


def copy_prop(target, source, role):
    for field in ("props", "propsContextKeys", "propsExpiresAt"):
        if role in (source.get(field) or {}):
            target.setdefault(field, {})[role] = deepcopy(source[field][role])


def refreshed_props(recommendation):
    result = deepcopy(recommendation)
    for role, key in (result.get("propsContextKeys") or {}).items():
        cached = read_recommendation(key)
        if cached:
            copy_prop(result, cached["outfitRecommendation"], role)
    return result


def validate_props(recommendation, pools):
    """Revalidate signed/stored props without a provider call, including pending picks."""
    out = deepcopy(recommendation)
    for role, prop in out.get("props", {}).items():
        if role not in PROP_ROLES or not isinstance(prop, dict) or prop.get("status") not in ("pending", "ready", "fallback", "unavailable"):
            raise OutfitSelectionError("invalid_outfit_props_response")
        pool = pools.get(role) or []
        if not pool:
            out["props"][role] = {"status": "unavailable", "picks": []}
        else:
            validate_picks(prop.get("picks"), pool, role)
    return out


def start_props(settings, r2, product, analysis, source_images, items, role, *, slots=None, semaphore=None):
    context = product_context(product, analysis)
    key = props_key(settings, product, analysis, source_images, slots, items, role)
    pool = props_candidates(items, context, role, limit=settings.outfit_candidates_per_role)
    recommendation = deepcopy(analysis.get("outfitRecommendation"))
    if not recommendation:
        recommendation = prepare_recommendation(settings, r2, product, analysis, source_images, items, slots=slots)["outfitRecommendation"]
    base = read_recommendation(recommendation.get("contextKey"))
    if base:
        for other_role in PROP_ROLES:
            copy_prop(recommendation, base["outfitRecommendation"], other_role)
    cached = read_recommendation(key)
    known = bool(cached or (recommendation.get("propsContextKeys") or {}).get(role) == key
                 and role in recommendation.get("props", {}))
    if cached:
        copy_prop(recommendation, cached["outfitRecommendation"], role)
    elif known:
        # Persisted pending from an interrupted process becomes free fallback, never a retry.
        if recommendation["props"][role]["status"] == "pending" and key not in _inflight:
            recommendation["props"][role]["status"] = "fallback" if pool else "unavailable"
    else:
        picks = deterministic_picks(pool, role)
        pending = bool(pool and settings.outfit_recommendation_enabled and settings.gemini_api_key
                       and (slots is None or "Front" in slots))
        recommendation.setdefault("props", {})[role] = {"status": "pending" if pending else "fallback" if pool else "unavailable", "picks": picks}
        recommendation.setdefault("propsContextKeys", {})[role] = key
        recommendation.setdefault("propsExpiresAt", {})[role] = time.time() + settings.outfit_props_timeout_seconds
    recommendation = recommendation_display(recommendation, candidate_pools(items, context), r2)
    fields = {"outfitRecommendation": recommendation}
    # A signed public handoff can outlive the base cache. Restore the complete result
    # before merging independent roles, retaining the choices from the current request.
    selections = normalize_selections(analysis.get("outfitSelections"), context["clothingType"],
                                     legacy_match_items=analysis.get("matchClothing") or [])
    cache_fields = {**fields, **sync_legacy(analysis, selections, items, r2)}
    _cache_result(recommendation["contextKey"], cache_fields)
    _cache_result(key, cache_fields)
    if known or recommendation["props"][role]["status"] != "pending" or key in _inflight:
        return fields

    async def complete():
        result = deepcopy(cache_fields)
        prop = result["outfitRecommendation"]["props"][role]
        async def evaluate():
            return await outfit_recommender.recommend_props(settings, r2,
                [source_images[slots.index("Front") if slots else 0]], {role: pool}, context=context)
        async def bounded():
            if semaphore is None:
                return await evaluate()
            async with semaphore:
                return await evaluate()
        try:
            try:
                raw = await asyncio.wait_for(bounded(), timeout=settings.outfit_props_timeout_seconds)
                outfit_recommender.display_reason(raw.get("reason") if isinstance(raw, dict) else None)
                prop["picks"] = validate_picks(raw.get("picks"), pool, role)
                prop["status"] = "ready"
            except Exception as exc:
                log.warning("outfit props fallback: %s", type(exc).__name__)
                prop["status"] = "fallback"
            _cache_result(key, result)
            return result
        finally:
            _inflight.pop(key, None)
    _inflight[key] = asyncio.create_task(complete(), name="outfit-props-recommendation")
    return fields
