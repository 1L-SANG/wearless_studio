"""Shared page-template catalog validation and deterministic skin tokens (no AI)."""
from __future__ import annotations

import json
import math
import re
from copy import deepcopy
from pathlib import Path

CATALOG_PATH = Path(__file__).parents[1] / "data" / "page_templates_v1.json"
SECTION_KINDS = ("first", "wear", "color_product", "fabric", "intro", "points", "for_you", "review", "faq", "sns")
FONTS = {"Pretendard", "Cormorant", "Roboto Mono", "Cal Sans", "Gowun Dodum", "Gowun Batang"}
FACT_PATHS = {"product.name", "product.colors[].name", "product.colors[].swatchId", "analysis.materials", "analysis.fit", "analysis.detailRecommendations.candidates[].label", "analysis.featureCopy", "analysis.mdComment", "profiles.display_name"}
COLORS = {"ink", "muted", "accentInk", "accentBg", "accentLine", "panel", "invertBg", "invertInk", "canvas", "onPhoto", "summaryBg", "pointBg"}
STYLES = {"titleKo", "labelEn", "body", "small", "num"}
LAYOUT_TYPES = {"text", "photo", "photoStack", "photoGrid", "overlay", "pointRows", "list", "factTable", "panel", "rule"}
HEX = re.compile(r"#[\da-f]{6}\Z", re.I)


def _positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def _opacity(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 1


def walk_layout(nodes):
    for node in nodes or []:
        yield node
        for key in ("nodes", "items", "captions"):
            yield from walk_layout(node.get(key))
        columns = node.get("columns") if isinstance(node.get("columns"), list) else []
        for group in [*(node.get("groups") or []), *columns, *(node.get("overlayGroups") or [])]:
            yield from walk_layout(group.get("nodes"))


def _role_pattern(role):
    return re.sub(r"\.\d+(?=\.|$)", ".n", str(role).replace("{direction}", "front"))


def text_slot_for_role(kind, role):
    pattern = _role_pattern(role)
    return next((slot for slot in (kind or {}).get("textSlots", []) if slot["copyRole"] == pattern), None)


def _validate_photo_slots(template_id, section, check):
    keys = set()
    check(section.get("kind") not in ("intro", "for_you") or not section.get("photoSlots"), f"text-only photos {template_id}.{section.get('kind')}")
    for slot in section.get("photoSlots", []):
        key = slot.get("slotKey")
        check(bool(key) and key not in keys, f"photo slot {template_id}.{key}")
        keys.add(key)
        check(slot.get("fill") in ("generate", "reuse"), f"fill {template_id}.{key}")
        check(slot.get("color") in ("base", "extra", "each", "cycle"), f"photo color {template_id}.{key}")
        count = slot.get("count")
        check(_positive(count) if isinstance(count, (int, float)) else isinstance(count, dict) and _positive(count.get("max")) and all(k == "detailPool" or _positive(v) for k, v in count.items()), f"photo count {template_id}.{key}")
        if slot.get("fill") == "reuse":
            ref = slot.get("reuseFrom") or {}
            check(((section.get("kind"), key) in (("first", "ghost"), ("fabric", "product")) and slot.get("contentRole") == "productOverview" and ref.get("kind") == "color_product" and ref.get("slot") == "front" and ref.get("color") == "base")
                  or (section.get("kind") == "sns" and ref.get("kind") == "wear" and ref.get("usage") in ("styling", "fit") and ref.get("slot") == "g" and ref.get("index") in (0, 1)), f"reuse {template_id}.{key}")
            check(slot.get("recipe") is None, f"reuse recipe {template_id}.{key}")
            continue
        recipe = slot.get("recipe") or {}
        check(section.get("kind") != "sns", f"SNS must reuse {template_id}.{key}")
        worn = recipe.get("cutType") in ("styling", "horizon")
        role = slot.get("contentRole")
        allowed = ("styling", "horizon") if role in ("hero", "benefit", "coordination", "fit") else ("product",) if role in ("productOverview", "detail") else ()
        allowed_cuts = recipe.get("allowedCutTypes") or []
        check(recipe.get("cutType") in allowed and bool(allowed_cuts) and all(v in allowed for v in allowed_cuts) and recipe.get("cutType") in allowed_cuts, f"cutType {template_id}.{key}")
        shots = ("full", "medium") if worn else ("detail",) if role == "detail" else ("ghost",)
        allowed_shots = recipe.get("allowedShots") or []
        check(recipe.get("shot") in shots and bool(allowed_shots) and all(v in shots for v in allowed_shots) and recipe.get("shot") in allowed_shots, f"shot {template_id}.{key}")
        check(recipe.get("direction") in (("front", "side", "back") if worn else ("front", "back")), f"direction {template_id}.{key}")
        check(recipe.get("set") is None or (worn and recipe.get("set") in ("styling", "rotation", "sequence")), f"set {template_id}.{key}")
        check(recipe.get("detailPool") is None or (role == "detail" and recipe.get("detailPool") in ("fabric", "points")), f"detailPool {template_id}.{key}")
        check(recipe.get("examplePrefer") in (None, "bottomClear"), f"examplePrefer {template_id}.{key}")


def validate_catalog(catalog):
    errors = []

    def check(condition, message):
        if not condition:
            errors.append(message)

    check(catalog.get("schemaVersion") == 1 and catalog.get("canvasWidth") == 1000, "schemaVersion/canvasWidth")
    check(isinstance(catalog.get("canvasColor"), str) and HEX.fullmatch(catalog["canvasColor"]), "canvas color")
    check(all(isinstance((catalog.get("swatchHex") or {}).get(key), str) and HEX.fullmatch(catalog["swatchHex"][key]) for key in catalog.get("swatchLabels") or {}), "swatch colors")
    defaults, band = catalog.get("defaults") or {}, catalog.get("firstTitleBand") or {}
    check(defaults.get("templateId") in ("emo", "show") and defaults.get("fontStyle") in ("template", "serif", "boldGothic") and defaults.get("colorMode") in ("mono", "point"), "default page style")
    check(all(isinstance(band.get(k), (int, float)) and math.isfinite(band[k]) and 0 <= band[k] <= 1 for k in ("left", "right", "top", "bottom")) and band.get("left", 1) < band.get("right", 0) and band.get("top", 1) < band.get("bottom", 0), "first title band")
    kinds = catalog.get("kinds") or {}
    check(len(kinds) == 10 and all(key in kinds for key in SECTION_KINDS), "ten section kinds")
    for key, kind in kinds.items():
        check(key in SECTION_KINDS and kind.get("kind") == key, f"kind {key}")
        check(kind.get("repeatable") is (key == "wear"), f"repeatable {key}")
        check(kind.get("menuGroup") in ("photo", "detail", "describe", "social"), f"menuGroup {key}")
        check(kind.get("storyboardTextCard") in ("only", "lead", "none"), f"text card {key}")
        check(kind.get("usages") == ["styling", "fit"] if key == "wear" else kind.get("usages") is None, f"usage {key}")
        roles = set()
        for slot in kind.get("textSlots", []):
            role = slot.get("copyRole")
            check(role not in roles, f"duplicate copyRole {key}.{role}")
            roles.add(role)
            check(slot.get("source") in ("static", "fact", "ai", "aiDraft", "seller+ai"), f"source {key}.{role}")
            check(slot.get("style") in STYLES and slot.get("color") in COLORS, f"text token {key}.{role}")
            if slot.get("source") == "fact":
                check(slot.get("factPath") in FACT_PATHS or (role == "notice" and slot.get("factPath") is None and slot.get("unavailable") is True), f"factPath {key}.{role}")
            if slot.get("source") in ("ai", "aiDraft", "seller+ai"):
                check(_positive(slot.get("maxChars")), f"maxChars {key}.{role}")
            count = slot.get("itemCount")
            if count:
                check(_positive(count.get("default")) and count.get("max", 0) >= count["default"], f"itemCount {key}.{role}")
    designs = catalog.get("designs") or {}
    for key, design in designs.items():
        kind = kinds.get(design.get("kind")) or {}
        check(design.get("id") == key and bool(kind) and isinstance(design.get("layout"), list), f"design {key}")
        name = design.get("displayName")
        check(isinstance(name, str) and bool(name.strip()) and len(name) <= 24, f"design displayName {key}")
        check(design.get("usage") in ("styling", "fit") if design.get("kind") == "wear" else design.get("usage") is None, f"design usage {key}")
        if "pickerHidden" in design:
            check(isinstance(design["pickerHidden"], bool), f"pickerHidden {key}")
        roles, photos, group_ids = set(), set(), set()
        for node in walk_layout(design.get("layout")):
            for group in [*(node.get("groups") or []), *(node.get("overlayGroups") or [])]:
                group_id = group.get("id")
                check(isinstance(group_id, str) and bool(group_id) and group_id not in group_ids, f"overlay group {key}.{group_id}")
                group_ids.add(group_id)
                check(group.get("anchor") in (None, "top", "bottom", "center"), f"overlay anchor {key}.{group_id}")
                check(group.get("band") in (None, "firstTitleBand"), f"overlay band {key}.{group_id}")
                if group.get("textColor") is not None:
                    color = group["textColor"]
                    check(isinstance(color, str) and (HEX.fullmatch(color) or color in COLORS and color != "onPhoto"), f"overlay text color {key}.{group_id}")
                if group.get("positionFallback") is not None:
                    policy = group["positionFallback"]
                    check(key in ("P01-D09", "P01-D09-G") and isinstance(policy, dict)
                          and policy.get("anchors") == ["center", "top", "bottom"]
                          and isinstance(policy.get("edgeInsetRatio"), (int, float))
                          and not isinstance(policy.get("edgeInsetRatio"), bool)
                          and math.isfinite(policy["edgeInsetRatio"]) and 0 <= policy["edgeInsetRatio"] < .5,
                          f"overlay position {key}.{group_id}")
            check(node.get("type") in LAYOUT_TYPES, f"layout {key}.{node.get('type')}")
            if node.get("scrim") is not None:
                paint = node["scrim"]
                check(node.get("type") == "overlay" and isinstance(paint, dict)
                      and isinstance(paint.get("color"), str) and HEX.fullmatch(paint["color"]) and _opacity(paint.get("opacity"))
                      and (paint.get("heightRatio") is None or _positive(paint["heightRatio"]) and paint["heightRatio"] <= 1)
                      and (paint.get("gradient") is None or isinstance(paint["gradient"], bool)), f"overlay scrim {key}")
            if node.get("fillOpacity") is not None:
                check(node.get("type") in ("panel", "list") and _opacity(node["fillOpacity"]), f"fill opacity {key}")
            if node.get("itemGap") is not None:
                value = node["itemGap"]
                check(node.get("type") == "list" and isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0, f"list item gap {key}")
            if node.get("shadow") is not None:
                shadow = node["shadow"]
                check(node.get("type") in ("panel", "list") and isinstance(shadow, dict)
                      and all(isinstance(shadow.get(prop), (int, float)) and not isinstance(shadow[prop], bool) and math.isfinite(shadow[prop]) for prop in ("x", "y", "blur"))
                      and shadow["blur"] >= 0 and isinstance(shadow.get("color"), str) and HEX.fullmatch(shadow["color"])
                      and _opacity(shadow.get("opacity")), f"shape shadow {key}")
            if node.get("singleLine") is not None:
                check(node.get("type") == "text" and isinstance(node["singleLine"], bool), f"single line {key}")
            if node.get("titleWrap") is not None:
                policy = node["titleWrap"]
                sizes = policy.get("sizes") if isinstance(policy, dict) else None
                ratio = policy.get("photoWidthRatio") if isinstance(policy, dict) else None
                check(key in ("P01-D09", "P01-D09-G") and node.get("type") == "text" and node.get("copyRole") == "name"
                      and _positive(ratio) and ratio <= 1
                      and isinstance(sizes, list) and bool(sizes) and sizes[0] == node.get("size")
                      and all(_positive(size) and size % 1 == 0
                              and (i == 0 or size < sizes[i - 1]) for i, size in enumerate(sizes))
                      and _positive(policy.get("maxLines")) and policy["maxLines"] % 1 == 0
                      and _positive(policy.get("minSizeMaxLines")) and policy["minSizeMaxLines"] % 1 == 0
                      and policy["minSizeMaxLines"] >= policy["maxLines"], f"title wrap {key}.{node.get('copyRole')}")
            if node.get("matchText") is not None:
                check(key in ("P01-D09", "P01-D09-G") and node.get("type") == "rule" and node["matchText"] == "name" and node.get("align") == "center"
                      and _positive(node.get("minW")) and _positive(node.get("maxWidthRatio")) and node["maxWidthRatio"] <= 1,
                      f"rule text width {key}")
            if node.get("copyRole"):
                check(text_slot_for_role(kind, node["copyRole"]) is not None, f"copyRole {key}.{node['copyRole']}")
                roles.add(_role_pattern(node["copyRole"]))
            if node.get("slot") and node["slot"] != "$current":
                photos.add(node["slot"])
            photos.update(node.get("slots") or [])
            for prop in ("color", "fill"):
                if node.get(prop) is not None:
                    check(node[prop] in COLORS, f"color token {key}.{node[prop]}")
            if node.get("style"):
                check(node["style"] in STYLES, f"font token {key}.{node['style']}")
            if node.get("columns") is not None and not isinstance(node["columns"], list):
                check(_positive(node["columns"]), f"columns {key}")
            for prop in ("w", "h", "size", "photoWidth", "minW", "maxW"):
                if node.get(prop) is not None:
                    check(_positive(node[prop]) or (prop == "w" and node["type"] == "rule" and node["w"] == "text"), f"geometry {key}.{prop}")
            if node.get("padding") is not None:
                pads = node["padding"] if isinstance(node["padding"], list) else [node["padding"]]
                check((not isinstance(node["padding"], list) or (len(pads) == 2 and node["type"] in ("panel", "list"))) and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0 for v in pads), f"geometry {key}.padding")
            if node.get("hug") is not None:
                check(isinstance(node["hug"], bool) and node["type"] in ("panel", "list"), f"hug {key}")
            if node.get("minW") is not None and node.get("maxW") is not None:
                check(_positive(node["minW"]) and _positive(node["maxW"]) and node["minW"] <= node["maxW"], f"hug bounds {key}")
            if node.get("hugMode") is not None:
                check(node["type"] == "list" and node["hugMode"] in ("each", "max"), f"hugMode {key}")
            if node.get("layout") is not None:
                check(node["type"] == "factTable" and node["layout"] in ("inline", "columns", "stacked"), f"table layout {key}")
            if node.get("marker") is not None:
                check(node["type"] == "list" and node["marker"] in ("dot", "square"), f"marker {key}")
            if node.get("valign") is not None:
                check(node["type"] == "pointRows" and node["valign"] == "center", f"valign {key}")
            if node.get("swatchDot") is not None:
                check(node["type"] == "text" and isinstance(node["swatchDot"], bool), f"swatchDot {key}")
            if node.get("splitParagraphs") is not None:
                check(node["type"] == "panel" and any(child.get("copyRole") == node["splitParagraphs"] for child in node.get("nodes") or []), f"splitParagraphs {key}")
            for prop in ("x", "y", "gap", "radius", "photoRadius"):
                if node.get(prop) is not None:
                    value = node[prop]
                    check(isinstance(value, (int, float)) and math.isfinite(value) and value >= 0, f"geometry {key}.{prop}")
            if node.get("aspect") not in (None, "source"):
                value = node["aspect"]
                check(isinstance(value, str) and re.fullmatch(r"\d+(?:\.\d+)?/\d+(?:\.\d+)?", value) and all(float(v) > 0 for v in value.split("/")), f"aspect {key}")
            if node.get("cropTop") is not None:
                value = node["cropTop"]
                check(isinstance(value, (int, float)) and math.isfinite(value) and 0 <= value < 1, f"cropTop {key}")
        for role in kind.get("requiredCopyRoles", []):
            check(role in roles, f"required copyRole {key}.{role}")
        for slot in kind.get("requiredPhotoSlots", []):
            check(slot in photos, f"required photo {key}.{slot}")
        check((design.get("overflow") or {}).get("type") == "photoStack", f"overflow {key}")
    ids = set()
    for template in catalog.get("templates", []):
        tid = template.get("id")
        check(tid in ("emo", "show") and tid not in ids, f"template {tid}")
        ids.add(tid)
        check(all(template.get("fonts", {}).get(key) for key in ("titleKo", "labelEn", "body")), f"required fonts {tid}")
        for ref in template.get("fonts", {}).values():
            check((ref or {}).get("font") in FONTS, f"font {tid}")
        for value in template.get("colors", {}).values():
            check(value is None or isinstance(value, str) and HEX.fullmatch(value), f"color {tid}")
        check(all(isinstance(template.get("colors", {}).get(key), str) and HEX.fullmatch(template["colors"][key]) for key in ("ink", "muted", "accentInk", "accentLine", "panel")), f"required colors {tid}")
        check(tid == "show" or not template.get("colors", {}).get("invertBg"), f"invert skin {tid}")
        keys, counts = set(), {}
        for index, section in enumerate(template.get("sections", [])):
            key, kind_key = section.get("instanceKey"), section.get("kind")
            kind = kinds.get(kind_key) or {}
            check(bool(kind), f"section kind {tid}.{key}")
            check(bool(key) and key not in keys, f"instanceKey {tid}.{key}")
            keys.add(key)
            counts[kind_key] = counts.get(kind_key, 0) + 1
            check(kind.get("repeatable") or counts[kind_key] <= 1, f"repeated kind {tid}.{kind_key}")
            if kind_key == "first":
                check(index == 0, f"first position {tid}")
            check(section.get("usage") in kind.get("usages", []) if kind_key == "wear" else section.get("usage") is None, f"section usage {tid}.{key}")
            for design_id in filter(None, [section.get("designId"), section.get("fallbackDesignId"), *(section.get("alternatives") or [])]):
                design = designs.get(design_id, {})
                check(design.get("kind") == kind_key and (kind_key != "wear" or design.get("usage") == section.get("usage")), f"design kind {tid}.{design_id}")
            check(not (section.get("block") or {}).get("invert") or tid == "show", f"invert block {tid}")
            _validate_photo_slots(tid, section, check)
        check(counts.get("first") == 1 and template.get("sections", [{}])[0].get("kind") == "first", f"one first {tid}")
        for key in SECTION_KINDS:
            placement = template.get("addPlacements", {}).get(key) or {}
            check(designs.get(placement.get("designId"), {}).get("kind") == key, f"add placement {tid}.{key}")
            for design_id in filter(None, [placement.get("fallbackDesignId"), *(placement.get("designIds") or [])]):
                check(designs.get(design_id, {}).get("kind") == key, f"add design kind {tid}.{design_id}")
            check(placement.get("after") is None or placement["after"] in keys or placement["after"] in ("lastSameKind", "lastSameUsage"), f"placement target {tid}.{key}")
            _validate_photo_slots(tid, {**placement, "kind": key}, check)
    check(len(ids) == 2, "two templates")
    check(set(catalog.get("fontStyles", {})) == {"template", "serif", "boldGothic"}, "three font styles")
    for refs in catalog.get("fontStyles", {}).values():
        for ref in refs.values():
            if ref:
                check(ref.get("font") in FONTS, f"font style {ref.get('font')}")
    for swatch in ("white", "gray", "black", "ivory", "beige", "brown", "purple", "red", "yellow", "green", "blue", "navy", "pink", "null"):
        palette = catalog.get("pointPalette", {}).get(swatch) or {}
        check(all(isinstance(palette.get(key), str) and HEX.fullmatch(palette[key]) for key in ("accentInk", "accentBg", "accentLine")), f"palette {swatch}")
    for key in ("unitsPerEm", "ascii", "uppercaseAscii", "space", "wide", "lineHeight", "titleLineHeight", "labelLineHeight", "minImageHeight", "maxImageHeight", "maxBlockHeight"):
        check(_positive(catalog.get("textMetrics", {}).get(key)), f"text metric {key}")
    if errors:
        raise ValueError("Invalid page template catalog: " + "; ".join(errors))
    return catalog


def load_catalog(path=None):
    return validate_catalog(json.loads(Path(path or CATALOG_PATH).read_text(encoding="utf-8")))


CATALOG = load_catalog()


def get_template(template_id, catalog=None):
    return next((t for t in (catalog or CATALOG)["templates"] if t["id"] == template_id), None)


def _usage_for_recipe(recipe):
    recipe = recipe or {}
    if recipe.get('cutType') == 'horizon' or recipe.get('set') in ('sequence', 'rotation'):
        return 'fit'
    if recipe.get('cutType') == 'styling' or recipe.get('set') == 'styling':
        return 'styling'
    return None


def wear_usage_for_block(block, catalog=None):
    catalog, block = catalog or CATALOG, block or {}
    if block.get('pageSectionKind') != 'wear':
        return None
    physical = _usage_for_recipe(block) or _usage_for_recipe(block.get('recipe'))
    if physical:
        return physical
    key = str(block.get('pageSectionKey') or '')
    template = get_template(key.partition('.')[0], catalog)
    original = next((section for section in (template or {}).get('sections', [])
                     if f"{template['id']}.{section['instanceKey']}" == key), {})
    recipe = next((slot['recipe'] for slot in original.get('photoSlots', []) if slot.get('recipe')), None)
    named = next((usage for usage, name in catalog['kinds']['wear']['namesByUsage'].items() if name == block.get('name')), None)
    return (_usage_for_recipe(recipe) or catalog['designs'].get(block.get('pageDesignId'), {}).get('usage')
            or block.get('pageSectionUsage') or named)


def template_section_name(block, catalog=None):
    catalog, block = catalog or CATALOG, block or {}
    kind = catalog['kinds'].get(block.get('pageSectionKind'), {})
    return kind.get('namesByUsage', {}).get(wear_usage_for_block(block, catalog)) or kind.get('name') or block.get('name')


def wear_addition_definition(template_or_id, usage):
    template = get_template(template_or_id) if isinstance(template_or_id, str) else template_or_id
    template = template or get_template(CATALOG['defaults']['templateId'])
    source = (next((section for section in template['sections'] if section['kind'] == 'wear'
                    and any((slot.get('recipe') or {}).get('set') == 'rotation' for slot in section.get('photoSlots', []))), {})
              if usage == 'fit' else template['addPlacements']['wear'])
    return {**deepcopy(source), 'kind': 'wear', 'usage': 'fit' if usage == 'fit' else 'styling'}


def section_definition(template, key, kind, usage=None, design_id=None):
    template = template or get_template(CATALOG['defaults']['templateId'])
    instance_key = str(key or "").partition('.')[2]
    section = next((s for s in template["sections"] if s["instanceKey"] == instance_key), None) or {
        **(wear_addition_definition(template, usage) if kind == 'wear' else next((s for s in template["sections"] if s["kind"] == kind), None)
           or template["addPlacements"].get(kind, {})), "instanceKey": instance_key, "kind": kind,
        **({"usage": usage} if usage else {})}
    if not design_id or design_id == section.get("designId"):
        return section
    required = {slot for node in walk_layout(CATALOG["designs"].get(design_id, {}).get("layout", []))
                for slot in ([node["slot"]] if node.get("slot") else []) + node.get("slots", [])}
    slots = list(section.get("photoSlots", []))
    for template_item in CATALOG["templates"]:
        for candidate in template_item["sections"]:
            if candidate["kind"] != kind:
                continue
            for slot in candidate.get("photoSlots", []):
                if slot["fill"] == "reuse" and slot["slotKey"] in required and not any(item["slotKey"] == slot["slotKey"] for item in slots):
                    slots.append(slot)
    return {**section, "photoSlots": slots}


def base_color_of(product):
    colors = (product or {}).get("colors") or []
    return next((color for color in colors if color.get("isBase")), None) or (colors[0] if colors else {})


def resolve_page_tokens(page_style=None, base_swatch_id=None, catalog=None):
    catalog, page_style = catalog or CATALOG, page_style or {}
    template = get_template(page_style.get("templateId"), catalog) or get_template(catalog["defaults"]["templateId"], catalog)
    font_style = page_style.get("fontStyle") if page_style.get("fontStyle") in catalog["fontStyles"] else catalog["defaults"]["fontStyle"]
    neutral = not isinstance(base_swatch_id, str) or base_swatch_id not in catalog['pointPalette'] or base_swatch_id in ('white', 'gray', 'black', 'null')
    color_mode = "point" if page_style.get("colorMode") == "point" and not neutral else "mono"
    fonts = deepcopy(template["fonts"])
    for key, override in catalog["fontStyles"][font_style].items():
        if not override:
            continue
        fonts[key].update(override)
        if font_style == "serif" and key == "labelEn":
            fonts[key].update(italic=template["fonts"][key].get("italic", False), uppercase=bool(template["fonts"][key].get("uppercase")), trackingEm=template["fonts"][key]["trackingEm"])
    colors = {**template["colors"], "canvas": catalog["canvasColor"]}
    if color_mode == "point" and not neutral:
        point = catalog["pointPalette"].get(base_swatch_id or "null", catalog["pointPalette"]["null"])
        colors.update(accentInk=point["accentInk"], accentLine=point["accentLine"])
        colors["accentBg"] = point["accentBg"]
        colors["panel"] = point["accentBg"]
    colors["accentBg"] = colors.get("accentBg") or colors["panel"]
    colors["invertInk"] = colors.get("invertInk") or catalog["overlayColors"]["light"]
    colors["summaryBg"] = colors['accentBg'] if template['id'] == 'emo' and color_mode == 'point' and not neutral else colors["canvas"]
    colors["pointBg"] = colors["summaryBg"]
    return {"template": template, "fontStyle": font_style, "colorMode": color_mode, "fonts": fonts, "colors": colors}


def section_tokens(tokens, section, catalog=None):
    catalog = catalog or CATALOG
    if not (section.get("block") or {}).get("invert") or tokens["template"]["id"] != "show":
        return tokens
    colors = {**tokens["colors"], "ink": tokens["colors"]["invertInk"], "muted": catalog["neutralColors"]["invertMuted"], "accentInk": catalog["neutralColors"]["invertMuted"], "accentLine": catalog["neutralColors"]["invertLine"], "accentBg": tokens["colors"]["invertBg"], "panel": tokens["colors"]["invertBg"], "summaryBg": tokens["colors"]["invertBg"], "pointBg": tokens["colors"]["invertBg"]}
    return {**tokens, "colors": colors}
