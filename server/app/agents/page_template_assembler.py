"""Taxonomy v4 layout interpreter. Keep in parity with pageTemplates/assemble.js."""
from __future__ import annotations

import math
import re
from types import SimpleNamespace

from .content_roles import is_worn_crop_source

from .page_templates import (
    CATALOG, base_color_of, get_template, resolve_page_tokens, section_definition,
    section_tokens, template_section_name, text_slot_for_role,
)


def _round(value):
    return math.floor(value + .5)


def _string(value):
    return "" if value is None else str(value)


def _number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else 0
    except (ValueError, TypeError):
        return 0


def _fork(ctx, **values):
    return SimpleNamespace(**{**vars(ctx), **values})


def _rgba(color, opacity):
    channels = ", ".join(str(int(color[index:index + 2], 16)) for index in (1, 3, 5))
    return f"rgba({channels}, {opacity:g})"


def _shape_paint(node):
    paint = {"opacity": node["fillOpacity"]} if node.get("fillOpacity") is not None else {}
    if node.get("shadow"):
        shadow = node["shadow"]
        paint["boxShadow"] = f"{shadow['x']:g}px {shadow['y']:g}px {shadow['blur']:g}px {_rgba(shadow['color'], shadow['opacity'])}"
    return paint


def _same_panel_surface(node, fill, surface_color):
    opacity = node.get("fillOpacity") if node.get("fillOpacity") is not None else 1
    return fill and fill.lower() == (surface_color or "").lower() and opacity == 1 and not node.get("shadow")


def _render_scrim(scrim, x, y, width, height):
    if not scrim:
        return []
    h = height * scrim.get("heightRatio", 1)
    paint = ({"fill": f"linear-gradient(180deg, {_rgba(scrim['color'], 0)} 0%, {_rgba(scrim['color'], scrim['opacity'])} 100%)"}
             if scrim.get("gradient") else {"fill": scrim["color"], "opacity": scrim["opacity"]})
    return [{"type": "shape", "shape": "rect", "x": x, "y": y + height - h, "w": width, "h": h, "radius": 0, **paint}]


def _local_slot(card, section):
    key = _string((card or {}).get("slotKey"))
    prefix = section["instanceKey"] + "."
    return key[len(prefix):] if key.startswith(prefix) else key


def _matches(key, prefix):
    return bool(key and (prefix == "*" or key == prefix or key.startswith(prefix + ".") or re.fullmatch(re.escape(prefix) + r"\d+", key)))


def _color_name(color, catalog):
    return _string(color.get("name") or color.get("label") or catalog["swatchLabels"].get(color.get("swatchId")))


def _role_for(role, ctx):
    return re.sub(r"\.n(?=\.|$)", f".{ctx.index or 0}", _string(role)).replace("{direction}", (ctx.card or {}).get("direction") or "front")


def _text_units(text, metrics):
    return sum(metrics["space"] if char.isspace() else metrics["uppercaseAscii"] if "A" <= char <= "Z" else metrics["ascii"] if ord(char) < 128 else metrics["wide"] for char in text)


def _text_width(text, size, metrics, tracking):
    # CSS includes final-character spacing. Integer hundredths avoid ceil drift.
    return max(0, (_text_units(text, metrics) * size * 100 + len(text) * _round(tracking * 100) * metrics["unitsPerEm"]) / (metrics["unitsPerEm"] * 100))


def _light_swatch(hex_color):
    rgb = int(hex_color[1:], 16)
    return .2126 * ((rgb >> 16) & 255) + .7152 * ((rgb >> 8) & 255) + .0722 * (rgb & 255) > 225


def wrap_text(text, width, size, metrics=None, tracking=0):
    metrics = metrics or CATALOG["textMetrics"]
    budget = max(size / metrics["unitsPerEm"], width)
    lines = []
    for paragraph in _string(text).split("\n"):
        line, spaces = "", ""
        for token in re.findall(r"\s+|\S+", paragraph):
            if token.isspace():
                spaces += token
                continue
            if line and _text_width(line + spaces + token, size, metrics, tracking) > budget:
                lines.append(line)
                line = token
            else:
                line += spaces + token
            spaces = ""
        lines.append(line + spaces)
    return lines


def measure_text_width(text, width, size, metrics=None, tracking=0):
    metrics = metrics or CATALOG["textMetrics"]
    return math.ceil(min(width, max(0, *(_text_width(line, size, metrics, tracking) for line in wrap_text(text, width, size, metrics, tracking)))))


def estimate_text_height(text, width, size, line_height, metrics=None, tracking=0):
    return max(1, len(wrap_text(text, width, size, metrics, tracking))) * line_height


def _title_paragraphs(text, width, size, metrics, tracking, natural_breaks):
    """Keep explicit rows/spaces; split only oversized words at ] / ( at 38."""
    paragraphs = []
    for paragraph in text.split("\n"):
        spans = []
        for match in re.finditer(r"\S+", paragraph):
            word, cuts = match.group(), [0]
            if natural_breaks and _text_width(word, size, metrics, tracking) > width:
                cuts.extend(i for i in range(1, len(word)) if word[i - 1] == "]" or word[i] == "(")
            cuts.append(len(word))
            spans.extend([match.start() + cuts[i - 1], match.start() + cuts[i]] for i in range(1, len(cuts)))
        if not spans:
            spans.append([0, len(paragraph)])
        spans[0][0], spans[-1][1] = 0, len(paragraph)
        paragraphs.append((paragraph, spans))
    return paragraphs


def _balanced_title_lines(paragraphs, width, size, metrics, tracking, max_lines):
    best, best_score = None, None

    def visit(paragraph_index, start, lines, widths):
        nonlocal best, best_score
        if paragraph_index == len(paragraphs):
            score = (len(lines), max(widths) - min(widths), max(widths))
            if best_score is None or score < best_score:
                best, best_score = lines, score
            return
        if len(lines) + len(paragraphs) - paragraph_index > max_lines:
            return
        text, spans = paragraphs[paragraph_index]
        for end in range(start, len(spans)):
            line = text[spans[start][0]:spans[end][1]]
            line_width = _round(_text_width(line, size, metrics, tracking) * 100)
            if line_width > width * 100:
                break
            last = end == len(spans) - 1
            visit(paragraph_index + 1 if last else paragraph_index, 0 if last else end + 1,
                  [*lines, line], [*widths, line_width])

    visit(0, 0, [], [])
    return best


def _layout_product_title(text, width, policy, metrics, tracking_em):
    floor = policy["sizes"][-1]
    for size in policy["sizes"]:
        tracking = _round(tracking_em * size * 100) / 100
        paragraphs = _title_paragraphs(text, width, size, metrics, tracking, size == floor)
        lines = _balanced_title_lines(paragraphs, width, size, metrics, tracking, policy["maxLines"])
        if lines is None and size == floor:
            lines = _balanced_title_lines(paragraphs, width, size, metrics, tracking, policy["minSizeMaxLines"])
        if lines is not None:
            return size, lines
        if size != floor:
            continue
        # Impossible inputs retain all words at the floor, with no invented
        # internal break, ellipsis, or silently discarded remainder.
        lines = []
        for paragraph, spans in paragraphs:
            start = 0
            for end in range(1, len(spans)):
                if _text_width(paragraph[spans[start][0]:spans[end][1]], size, metrics, tracking) > width:
                    lines.append(paragraph[spans[start][0]:spans[end - 1][1]])
                    start = end
            lines.append(paragraph[spans[start][0]:spans[-1][1]])
        return size, lines


def _padding(node):
    value = node.get("padding") or 0
    return value if isinstance(value, list) else (value, value)


def korean_count(count):
    names = ("", "한", "두", "세", "네", "다섯", "여섯", "일곱", "여덟", "아홉", "열")
    return names[count] if 1 <= count <= 10 else _string(count)


def _materials_text(analysis, product):
    values = []
    for item in analysis.get("materials") or product.get("materials") or []:
        if not isinstance(item, dict):
            values.append(_string(item))
        elif item.get("name"):
            ratio = item.get("ratio")
            values.append(item["name"] + (f" {_string(ratio)}%" if ratio is not None else ""))
    return ", ".join(value for value in values if value)


def _fact_text(role, ctx):
    product, analysis, catalog, card = ctx.product, ctx.analysis, ctx.catalog, ctx.card or {}
    colors = product.get("colors") or []
    selected = next((color for color in colors if card.get("colorId") is not None and _string(color.get("id")) == _string(card["colorId"])), None)
    selected = (selected or {}) if card.get("colorId") is not None else (colors[ctx.index or 0] if len(colors) > (ctx.index or 0) else {})
    fit = catalog["fitLabels"].get(analysis.get("fit")) or _string(analysis.get("fit"))
    if role == "name":
        return _string(product.get("name"))
    if role == "colors":
        return ", ".join(filter(None, (_color_name(color, catalog) for color in colors)))
    if ctx.kind["kind"] == "color_product" and role == "title":
        return f"{korean_count(len(colors))} 가지 색이에요"
    if re.fullmatch(r"colors\.\d+\.name|colorName\.\d+", role):
        return _color_name(selected, catalog)
    if re.fullmatch(r"colors\.\d+\.en", role):
        name = _color_name(selected, catalog)
        return name if re.fullmatch(r"[A-Za-z][A-Za-z\s-]*", name) else ""
    if role == "mix":
        return _materials_text(analysis, product)
    if role == "who":
        return _string(ctx.profile.get("display_name")) or "MD"
    if role == "handle":
        return _string(ctx.profile.get("handle"))
    if role == "facts":
        facts = [("COLOR", _fact_text("colors", ctx)), ("FABRIC", _materials_text(analysis, product)), ("FIT", fit)]
        return "\n".join(f"{label}  {value}" for label, value in facts if value)
    if re.fullmatch(r"items\.\d+\.name", role):
        if (card.get("pointItem") or {}).get("kind") == "wornCrop":
            return _string(card["pointItem"].get("label"))
        candidates = (analysis.get("detailRecommendations") or {}).get("candidates") or []
        return _string(next((item.get("label") for item in candidates if card.get("detailTargetId") is not None and _string(item.get("id")) == _string(card["detailTargetId"])), None))
    # Unavailable notice / size / transparency facts remain hidden.
    return ""


def _content_for(node, ctx):
    if node.get("literal") is not None:
        text = _string(node["literal"]).replace("{number}", str((ctx.index or 0) + 1).zfill(2)).replace("{initial}", _string(ctx.profile.get("display_name"))[:1] or "MD")
        return text, None, None
    role = _role_for(node.get("copyRole"), ctx)
    slot = text_slot_for_role(ctx.kind, role)
    if slot is None:
        raise ValueError(f"Unknown copyRole {ctx.kind['kind']}.{role}")
    text = ""
    if slot["source"] == "static":
        text = node.get("default", "Fit" if role == "label" and ctx.section.get("usage") == "fit" else slot["default"])
    elif slot["source"] == "fact":
        text = _fact_text(role, ctx)
    elif not ctx.copywriting and slot["source"] == "seller+ai":
        text = _string(ctx.analysis.get("mdComment"))
    elif ctx.copywriting:
        text = ctx.copy.get(role) or ""
        if re.fullmatch(r"items\.\d+\.desc", role):
            name = _fact_text(role[:-5] + ".name", ctx)
            part = next((item.get("desc") for item in ctx.analysis.get("partDescriptionCopy") or [] if isinstance(item, dict) and item.get("point") == name), None)
            if node.get("copyPolicy") == "partOnly":
                lines = _string(part).replace("\r\n", "\n").replace("\r", "\n").split("\n")
                # Saved one-line copy stays available to legacy layouts only.
                text = "\n".join(lines) if len(lines) == 2 and all(line.strip() for line in lines) else ""
            else:
                legacy = (next((item.get("desc") for item in ctx.analysis.get("featureCopy") or [] if isinstance(item, dict) and item.get("point") == name), None)
                          if ((ctx.card or {}).get("pointItem") or {}).get("kind") != "wornCrop" else None)
                text = part or legacy or text
    if getattr(ctx, "paragraph_role", None) == role:
        text = ctx.paragraph_text
    return _string(text), slot, role


def _render_text(node, x, y, width, ctx):
    text, slot, role = _content_for(node, ctx)
    if node.get("hideEmpty") and not text:
        return [], 0
    if node.get("onlyExtraColor") and (not (ctx.card or {}).get("colorId") or _string(ctx.card["colorId"]) == _string(ctx.base_color.get("id"))):
        return [], 0
    token = node.get("style") or (slot or {}).get("style") or "body"
    font_key = "body" if token == "small" else "labelEn" if token == "num" else token
    font = {**ctx.tokens["fonts"][font_key], **({"weight": 300} if node.get("weight") == "light" else {}),
            **({"trackingEm": node["trackingEm"]} if node.get("trackingEm") is not None else {})}
    metrics = ctx.catalog["textMetrics"]
    uppercase = (slot or {}).get("uppercase") or (font.get("uppercase") and (slot or {}).get("kind") != "fact")
    if uppercase:
        text = text.upper()
    if node.get("numberPrefix") and text:
        text = f"{(ctx.index or 0) + 1}. {text}"
    if node.get("singleLine"):
        text = re.sub(r"\s+", " ", text).strip()
    layout_width = max(1, width - (18 if node.get("swatchDot") else 0))
    size = node.get("size") or 25
    fallback = (slot or {}).get("default") or ""
    display = text or (fallback.upper() if uppercase else fallback)
    if node.get("singleLine"):
        display = re.sub(r"\s+", " ", display).strip()
    title_wrap = node.get("titleWrap") if ctx.design_id in ("P01-D09", "P01-D09-G") and role == "name" else None
    if title_wrap:
        layout_width = min(layout_width, ctx.photo_width * title_wrap["photoWidthRatio"])
        size, lines = _layout_product_title(display, layout_width, title_wrap, metrics, font.get("trackingEm") or 0)
        display = "\n".join(lines)
        if text:
            text = display
        align = node.get("align") or ctx.align
        x += (width - layout_width) / 2 if align == "center" else width - layout_width if align == "right" else 0
        width = layout_width
    elif display and node.get("singleLine"):
        units = _text_units(display, metrics) + len(display) * (font.get("trackingEm") or 0) * metrics["unitsPerEm"]
        size = min(size, max(1, math.floor(layout_width * metrics["unitsPerEm"] / max(1, units))))
        while size > 1 and _text_width(display, size, metrics, _round((font.get("trackingEm") or 0) * size * 100) / 100) > layout_width:
            size -= 1
    elif display:
        longest = max(1, *(_text_units(word, metrics) + len(word) * (font.get("trackingEm") or 0) * metrics["unitsPerEm"] for word in re.split(r"\s+", display)))
        size = min(size, max(1, math.floor(layout_width * ctx.catalog["textMetrics"]["unitsPerEm"] / longest)))
    factor = metrics["titleLineHeight"] if font_key == "titleKo" else metrics["labelLineHeight"] if font_key == "labelEn" else metrics["lineHeight"]
    line_height = math.ceil(size * node["lineHeight"] if node.get("lineHeight") is not None else size * factor / metrics["unitsPerEm"])
    color = node.get("color") or (slot or {}).get("color") or "ink"
    if not node.get("color") and font_key == "titleKo":
        color = "accentInk" if ctx.tokens["template"]["id"] != "show" and ctx.tokens["colorMode"] == "point" else "ink"
    if not node.get("color") and font_key == "labelEn" and ctx.tokens["template"]["id"] == "guide":
        color = "accentInk" if ctx.kind["kind"] == "first" else "ink"
    style = {"font": font["font"], "weight": font["weight"], "italic": bool(font.get("italic")), "size": size,
             "tracking": _round((font.get("trackingEm") or 0) * size * 100) / 100,
             "lineHeight": line_height, "align": node.get("align") or ctx.align,
             "color": ctx.on_photo if color == "onPhoto" else ctx.tokens["colors"][color]}
    bare_overlay = bool(getattr(ctx, "overlay_group_key", None) and not getattr(ctx, "opaque_background", False))
    fixed_color = getattr(ctx, "fixed_overlay_color", None)
    auto_contrast = bare_overlay and not fixed_color
    if bare_overlay:
        style["color"] = fixed_color or ctx.on_photo
    if ctx.tokens["template"]["id"] == "guide" and ctx.tokens["colorMode"] == "point" and ctx.kind["kind"] == "intro" and color == "muted":
        style["color"] = ctx.catalog["neutralColors"]["panelMuted"]
    height = estimate_text_height(text or display, layout_width, size, line_height, metrics, style["tracking"])
    element = {"type": "text", "x": x, "y": y, "w": width, "h": height, "text": text, "style": style}
    if title_wrap or node.get("singleLine"):
        element["keepWords"] = True
    if auto_contrast:
        element["_overlayGroupKey"] = ctx.overlay_group_key
    if role:
        element.update(sourceBlockId=ctx.source_id, copyRole=role, textSlotVersion=1)
        if not text:
            element["placeholder"] = (display if title_wrap else node.get("default") or slot["default"]) or "내용을 입력해 주세요"
        if slot["source"] == "aiDraft" or (slot["source"] == "seller+ai" and not ctx.analysis.get("mdComment")):
            element["aiDraft"] = True
    if getattr(ctx, "paragraph_role", None) == role and role is not None:
        element.update(paragraphIndex=ctx.paragraph_index, paragraphCount=ctx.paragraph_count)
    if node.get("swatchDot") and text:
        colors = ctx.product.get("colors") or []
        selected = colors[ctx.index or 0] if len(colors) > (ctx.index or 0) else {}
        fill = ctx.catalog["swatchHex"].get(selected.get("swatchId"))
        if fill:
            text_w = measure_text_width(text, width - 18, size, metrics, style["tracking"])
            group_w = text_w + 18
            start = x + ((width - group_w) / 2 if style["align"] == "center" else width - group_w if style["align"] == "right" else 0)
            element.update(x=start + 18, w=text_w)
            style["align"] = "left"
            return [{"type": "shape", "shape": "circle", "x": start, "y": y + (line_height - 10) / 2,
                     "w": 10, "h": 10, "fill": fill, "radius": 999,
                     **({"stroke": ctx.tokens["colors"]["accentLine"], "strokeWidth": 1} if _light_swatch(fill) else {})}, element], height
    return [element], height


def _resolve_photo(slot_key, ctx):
    own = next((card for card in ctx.photos if _matches(_local_slot(card, ctx.section), slot_key)), None)
    if own:
        return own
    slot = next((item for item in ctx.section.get("photoSlots", []) if item["slotKey"] == slot_key), None)
    if not slot or slot["fill"] != "reuse":
        return None
    ref = slot["reuseFrom"]
    original = next((section for section in ctx.tokens["template"]["sections"] if section["kind"] == ref["kind"]
                     and (not ref.get("usage") or section.get("usage") == ref["usage"])), None)
    original_key = ctx.tokens["template"]["id"] + "." + original["instanceKey"] if original else None
    original_run = [card for card in ctx.storyboard if card.get("pageSectionKey") == original_key]
    # A first-page ghost is a reference to one explicitly ghost-mode product
    # run. Missing/mixed modes and another same-kind instance never substitute.
    if ref["kind"] == "color_product" and slot_key == "ghost":
        whole_cuts = [card for card in original_run if card.get("source") == "ai"
                      and card.get("cutType") == "product" and card.get("shot") == "ghost"]
        if not whole_cuts or any(card.get("productPresentation") != "ghost" for card in whole_cuts):
            return None
    candidates = []
    for card in original_run:
        if card.get("source") == "text" or card.get("similarSourceBlockId") or card.get("pageSectionKind") != ref["kind"]:
            continue
        if ref.get("color") == "base" and _string(card.get("colorId")) != _string(ctx.base_color.get("id")):
            continue
        if ref["kind"] == "color_product":
            ai_front = (card.get("source") == "ai" and card.get("cutType") == "product"
                        and card.get("shot") == "ghost" and card.get("direction") == "front")
            own_images = card.get("ownImages")
            mine_front = (card.get("source") == "mine" and card.get("direction") in (None, "front")
                          and isinstance(own_images, list) and bool(own_images)
                          and isinstance(own_images[0], str) and bool(own_images[0]))
            if not ai_front and not (slot_key == "product" and mine_front):
                continue
        section = {"instanceKey": ".".join(_string(card.get("pageSectionKey")).split(".")[1:])}
        if _matches(_local_slot(card, section), ref["slot"]):
            candidates.append(card)
    index = ref.get("index") or 0
    candidates = [card for card in candidates if not ref.get("usage") or card.get("pageSectionUsage") == ref["usage"] or card["pageSectionKey"] == original_key]
    return candidates[index] if len(candidates) > index else None


def _select_cards(node, ctx):
    if node.get("slot") == "$current":
        return [ctx.card]
    if node.get("slots"):
        return [_resolve_photo(slot, ctx) for slot in node["slots"]]
    # Curated wildcards include loose/similar photos in place; legacy layouts
    # keep their existing overflow behavior for photos without slot keys.
    if node.get("slot") == "*" and (node["type"] == "photoFlow" or node.get("includeLoose") or ctx.catalog["designs"][ctx.design_id].get("choiceAxes")):
        return ctx.photos
    return [card for card in ctx.photos if _matches(_local_slot(card, ctx.section), node.get("slot") or "*")]


def _natural_photo(node):
    return node.get("fit") == "contain" and node.get("aspect") == "source"


def _known_image_size(meta):
    return _number(meta.get("width")) > 0 and _number(meta.get("height")) > 0 and meta.get("dimensionsKnown") is not False


def _natural_image_size(node, meta, catalog):
    if _known_image_size(meta):
        return _number(meta["width"]), _number(meta["height"])
    return tuple(map(float, (node.get("fallbackAspect") or catalog["textMetrics"]["fallbackAspect"]).split("/")))


def _cover_image_size(card, meta, product):
    def known(value):
        return (isinstance(value, dict) and value.get("dimensionsKnown") is not False
                and all(isinstance(value.get(key), (int, float)) and not isinstance(value[key], bool)
                        and math.isfinite(value[key]) and value[key] > 0 for key in ("width", "height")))

    card = card or {}
    own_image = (card.get("ownImages") or [None])[0]
    if known(meta) and (card.get("source") != "mine" or meta.get("imageUrl") == own_image):
        return meta["width"], meta["height"]
    if card.get("source") == "mine":
        image = next((image for color in product.get("colors") or [] for image in color.get("images") or []
                      if image.get("src") == own_image and known(image)), None)
        if image:
            return image["width"], image["height"]
    return None


def _image_height(node, width, card, ctx):
    meta = ctx.cuts.get((card or {}).get("id")) or {}
    if _natural_photo(node):
        sw, sh = _natural_image_size(node, meta, ctx.catalog)
        item = (card or {}).get("pointItem") or {}
        region = item["cropRegion"] if item.get("kind") == "wornCrop" else None
        # A derived photo's natural aspect belongs to its selected region.
        return max(1, _round(width * sh * (region[3] if region else 1) / (sw * (region[2] if region else 1))))
    ratio = node.get("aspect")
    if not ratio or ratio == "source":
        ratio = f"{meta['width']}/{meta['height']}" if _number(meta.get("width")) > 0 and _number(meta.get("height")) > 0 else ctx.catalog["textMetrics"]["fallbackAspect"]
    w, h = map(float, ratio.split("/"))
    height = _round(width * h / w)
    return max(_round(width * ctx.catalog["textMetrics"]["minImageHeight"] / 880), min(_round(width * ctx.catalog["textMetrics"]["maxImageHeight"] / 880), height))


def _render_photo(node, x, y, width, ctx):
    card = ctx.card if node.get("slot") == "$current" else _resolve_photo(node.get("slot"), ctx)
    meta = ctx.cuts.get((card or {}).get("id")) or {}
    slot = next((item for item in ctx.section.get("photoSlots", []) if _matches(_local_slot(card, ctx.section), item["slotKey"]) or item["slotKey"] == node.get("slot")), None) or {}
    src = ((card.get("ownImages") or [None])[0] if card.get("source") == "mine" else meta.get("imageUrl")) if card else None
    height = _image_height(node, width, card, ctx)
    slot_key = card["slotKey"] if card and card.get("pageSectionKey") == ctx.key and card.get("slotKey") else ctx.section["instanceKey"] + "." + (f"extra{(ctx.index or 0) + 1}" if node.get("slot") == "$current" else _string(node.get("slot")))
    if getattr(ctx, "target_slot", None):
        slot_key = ctx.section["instanceKey"] + "." + ctx.target_slot
    element = {"type": "image", "x": x, "y": y, "w": width, "h": height, "src": src or None, "radius": node.get("radius") or 0,
               "slotKey": slot_key, "roleHint": "custom" if (card or {}).get("source") == "mine" else slot.get("contentRole") or (card or {}).get("contentRole") or "coordination"}
    if card:
        element["sourceBlockId"] = card["id"]
        if card.get("cutType"):
            element["cutType"] = card["cutType"]
        if card.get("pageSectionKey") == ctx.key:
            ctx.used.add(card["id"])
    if node.get("cropTop") and node.get("fit") != "contain":
        sw = _number(meta.get("width")) if _number(meta.get("width")) > 0 else 2
        sh = _number(meta.get("height")) if _number(meta.get("height")) > 0 else 3
        scale = max(width / sw, height / (sh * (1 - node["cropTop"])))
        iw, ih = max(width, _round(sw * scale)), max(height, _round(sh * scale))
        element["crop"] = {"iw": iw, "ih": ih, "ox": _round((iw - width) / 2), "oy": min(ih - height, _round(sh * scale * node["cropTop"]))}
    cover_size = _cover_image_size(card, meta, ctx.product) if node.get("fit") == "cover" else None
    if node.get("fit") == "cover" and (cover_size or ((card or {}).get("pointItem") or {}).get("kind") == "wornCrop"):
        item = (card or {}).get("pointItem") or {}
        worn = item.get("kind") == "wornCrop"
        # Ordinary unknown photos use native center-cover; ROI fallback is wear 3:4.
        sw, sh = cover_size or (3, 4)
        rx, ry, rw, rh = item["cropRegion"] if worn else (0, 0, 1, 1)
        # Explicit fixed cover may upscale; natural legacy nodes keep their caps.
        scale = max(width / (sw * rw), height / (sh * rh))
        iw, ih = max(width, _round(sw * scale)), max(height, _round(sh * scale))
        element["crop"] = {"iw": iw, "ih": ih,
                           "ox": max(0, min(iw - width, _round((rx + rw / 2) * iw - width / 2))),
                           "oy": max(0, min(ih - height, _round((ry + rh / 2) * ih - height / 2)))}
        if worn:
            element.update(pointItemKind="wornCrop", bodyPart=item["bodyPart"])
    elif ((card or {}).get("pointItem") or {}).get("kind") == "wornCrop":
        item = card["pointItem"]
        rx, ry, rw, rh = item["cropRegion"]
        sw, sh = (_natural_image_size(node, meta, ctx.catalog) if _natural_photo(node) else
                  (_number(meta.get("width")) if _number(meta.get("width")) > 0 else 2,
                   _number(meta.get("height")) if _number(meta.get("height")) > 0 else 3))
        dimensions_known = _known_image_size(meta)
        scale = min(max(width / (sw * rw), height / (sh * rh)), width / (sw * .3),
                    1 if dimensions_known else math.inf)
        # Keep undersized native images small instead of undoing the pixel cap.
        iw, ih = _round(sw * scale), _round(sh * scale)
        element["crop"] = {"iw": iw, "ih": ih,
            "ox": max(0, min(iw - width, _round((rx + rw / 2) * iw - width / 2))),
            "oy": max(0, min(ih - height, _round((ry + rh / 2) * ih - height / 2)))}
        element.update(pointItemKind="wornCrop", bodyPart=item["bodyPart"])
    failure = ctx.failed_cuts.get((card or {}).get("id"))
    if not src and failure:
        element.update(genFailed=True, genFailReason=failure["reason"], genFailRetryable=failure["retryable"])
        if failure.get("sourceBlockId") is not None:
            element["genFailSourceBlockId"] = failure["sourceBlockId"]
    return [element], height, card


def _overlay_boxes(bare, group_key, x, y, width, height, metrics, ignore_blank=False):
    lines = []
    for element_index, el in enumerate(bare):
        for line_index, text in enumerate(wrap_text(el.get("text") or el.get("placeholder"), el["w"], el["style"]["size"], metrics, el["style"]["tracking"])):
            text_w = 0 if ignore_blank and not text.strip() else measure_text_width(text, el["w"], el["style"]["size"], metrics, el["style"]["tracking"])
            left = el["x"] + ((el["w"] - text_w) / 2 if el["style"]["align"] == "center" else el["w"] - text_w if el["style"]["align"] == "right" else 0)
            lines.append({"key": f"{group_key}:{element_index}:{line_index}", "text": text,
                          "region": {"left": (left - x) / width, "top": (el["y"] + line_index * el["style"]["lineHeight"] - y) / height,
                                     "right": (left + text_w - x) / width, "bottom": (el["y"] + (line_index + 1) * el["style"]["lineHeight"] - y) / height}})
    return {"region": {"left": (min(el["x"] for el in bare) - x) / width,
                       "top": (min(el["y"] for el in bare) - y) / height,
                       "right": (max(el["x"] + el["w"] for el in bare) - x) / width,
                       "bottom": (max(el["y"] + el["h"] for el in bare) - y) / height},
            "lines": lines}


def _region_plan(boxes):
    return {"region": boxes["region"], "lines": [{k: v for k, v in line.items() if k != "text"} for line in boxes["lines"]]}


def _render_groups(groups, x, y, width, height, ctx, photo):
    elements = []
    for group in groups or []:
        group_key = f"{ctx.key}:{photo['slotKey']}:{group['id']}"
        colors = ctx.cuts.get(photo.get("sourceBlockId"), {}).get("overlayTextColors")
        measured = colors.get(group_key) if isinstance(colors, dict) else None
        fixed_color = ctx.tokens["colors"].get(group.get("textColor")) or group.get("textColor")
        on_photo = fixed_color or (measured if measured in ctx.catalog["overlayColors"].values() else ctx.catalog["overlayColors"]["light"])
        gx = group.get("x") or 0
        result, h = _render_nodes(group.get("nodes"), x + gx, 0, min(group.get("w") or width, width - gx),
                                  _fork(ctx, on_photo=on_photo, overlay_group_key=group_key, fixed_overlay_color=fixed_color, opaque_background=False, surface_color=None, photo_width=width))
        top = min(0, *(el["y"] for el in result))
        bottom = max(0, *(el["y"] + el["h"] for el in result))
        band = ctx.catalog[group["band"]] if group.get("band") else None
        if group.get("anchor") == "center":
            gy = y + height * ((band["top"] + band["bottom"]) / 2 if band else .5) - (top + bottom) / 2
        else:
            gy = y + (max(0, height - (group.get("y") or 0) - h) if group.get("anchor") == "bottom" else group.get("y") or 0)
        policy = group.get("positionFallback") if ctx.design_id in ("P01-D09", "P01-D09-G") else None
        offsets = {}
        if policy:
            inset = max(0, min(height * policy["edgeInsetRatio"], (height - (bottom - top)) / 2))
            offsets = {"center": gy, "top": y + inset - top, "bottom": y + height - inset - bottom}
            positions = ctx.cuts.get(photo.get("sourceBlockId"), {}).get("overlayTextPositions")
            measured_position = positions.get(group_key) if isinstance(positions, dict) else None
            gy = offsets.get(measured_position, gy) if isinstance(measured_position, str) else gy
        placed = [{**el, "y": el["y"] + gy} for el in result]
        bare = [el for el in placed if el.get("_overlayGroupKey") == group_key]
        boxes = _overlay_boxes(bare, group_key, x, y, width, height, ctx.catalog["textMetrics"], bool(policy)) if bare else None
        line_boxes = boxes["lines"] if boxes else []
        if bare and photo.get("sourceBlockId") and ctx.overlay_regions is not None:
            plan = {"sourceBlockId": photo["sourceBlockId"], "key": group_key,
                    "frame": {"w": photo["w"], "h": photo["h"], **({"crop": photo["crop"]} if photo.get("crop") else {})},
                    **_region_plan(boxes)}
            if policy:
                raw_bare = [el for el in result if el.get("_overlayGroupKey") == group_key]
                plan["positionCandidates"] = [{"position": position, **_region_plan(_overlay_boxes(
                    [{**el, "y": el["y"] + offsets[position]} for el in raw_bare], group_key, x, y, width, height, ctx.catalog["textMetrics"], True))}
                    for position in policy["anchors"]]
            ctx.overlay_regions.append(plan)
        measured_lines = ctx.cuts.get(photo.get("sourceBlockId"), {}).get("overlayTextLines") or {}
        if not isinstance(measured_lines, dict):
            measured_lines = {}
        for element_index, el in enumerate(bare):
            lines = [line for line in line_boxes if line["key"].startswith(f"{group_key}:{element_index}:")]
            if lines and all(isinstance(measured_lines.get(line["key"]), dict) and measured_lines[line["key"]].get("color") in ctx.catalog["overlayColors"].values() for line in lines):
                el["contrastLines"] = [{"text": line["text"], "color": on_photo if policy else measured_lines[line["key"]]["color"], "shade": measured_lines[line["key"]].get("shade") is True} for line in lines]
                el["contrastFor"] = {"text": el["text"], "x": el["x"], "y": el["y"], "w": el["w"], "style": dict(el["style"])}
        elements.extend({k: v for k, v in el.items() if k != "_overlayGroupKey"} for el in placed)
    return elements


def _render_node(node, x, y, width, ctx):
    if node.get("whenTemplate") and node["whenTemplate"] != ctx.tokens["template"]["id"]:
        return [], 0
    if node.get("firstItemOnly") and getattr(ctx, "paragraph_index", 0) > 0:
        return [], 0
    x += node.get("x") or 0
    available_width = width - (node.get("x") or 0)
    requested = (getattr(ctx, "last_text_width", None) or 0) if node.get("w") == "text" else node.get("w") or width
    width = min(width if node["type"] == "list" else requested, width - (node.get("x") or 0))
    kind = node["type"]
    if kind == "factTable" and node.get("layout") and node["layout"] != "inline":
        return _render_fact_table(node, x, y, width, ctx)
    if kind in ("text", "factTable"):
        return _render_text(node, x, y, width, ctx)
    if kind == "photo":
        elements, h, _ = _render_photo(node, x, y, width, ctx)
        return elements, h
    if kind == "rule":
        if not width:
            return [], 0
        align = ctx.align if node.get("align") == "template" else node.get("align")
        if align == "center":
            x += (available_width - width) / 2
        height = node.get("h") or 2
        return [{"type": "shape", "shape": "rect", "x": x, "y": y, "w": width, "h": height,
                 "fill": ctx.on_photo if node.get("color") == "onPhoto" else ctx.tokens["colors"][node.get("color") or "accentLine"], "radius": 0}], height
    if kind == "panel":
        if node.get("splitParagraphs"):
            return _render_paragraphs(node, x, y, available_width, ctx)
        pad_y, requested_pad_x = _padding(node)
        fill = ctx.tokens["colors"].get(node.get("fill"))
        same_surface = _same_panel_surface(node, fill, getattr(ctx, "surface_color", None))
        pad_x = 0 if same_surface else requested_pad_x
        child_ctx = _fork(ctx, opaque_background=getattr(ctx, "opaque_background", False) or bool(node.get("fill")),
                          surface_color=fill or getattr(ctx, "surface_color", None))

        def children(box_x, box_width):
            if node.get("columns"):
                columns = [_render_nodes(col.get("nodes"), box_x + pad_x + col["x"], y + pad_y,
                                         max(1, min(col["w"], box_width - 2 * pad_x - col["x"])), child_ctx) for col in node["columns"]]
                return [el for result, _ in columns for el in result], max(0, *(h for _, h in columns))
            return _render_nodes(node.get("nodes"), box_x + pad_x, y + pad_y, max(1, box_width - 2 * pad_x), child_ctx)

        width = min(width, node.get("maxW") or width)
        elements, height = children(x, width)
        if node.get("hug"):
            width = min(width, max(node.get("minW") or 0, _content_width(elements, x + pad_x) + pad_x * 2))
            align = ctx.align if node.get("align") == "template" else node.get("align") or "left"
            x += available_width - width if align == "right" else (available_width - width) / 2 if align == "center" else 0
            elements, height = children(x, width)
        height += pad_y * 2
        if node.get("fill") and not same_surface:
            elements.insert(0, {"type": "shape", "shape": "rect", "x": x, "y": y, "w": width, "h": height, "fill": ctx.tokens["colors"][node["fill"]], "radius": node.get("radius") or 0, **_shape_paint(node)})
        return elements, height
    if kind == "overlay":
        elements, height, card = _render_photo(node, x, y, width, ctx)
        return elements + _render_scrim(node.get("scrim"), x, y, width, height) + _render_groups(node.get("groups"), x, y, width, height, ctx, elements[0]), height
    if kind == "list":
        pattern = _string(node.get("countRole") or node.get("copyRole"))
        index_pattern = re.compile("^" + re.escape(pattern).replace(r"\.n", r"\.(\d+)") + "$")
        indexes = [int(match[1]) for key in ctx.copy if (match := index_pattern.fullmatch(key))]
        count = min(node.get("maxCount") or node["count"], max(node["count"], *(index + 1 for index in indexes))) if indexes else node["count"]
        cursor, elements = y, []
        max_width = min(node.get("maxW") or node.get("w") or width, width)
        items = [{**child, "x": (child.get("x") or 0) + 22} for child in node.get("items") or []] if node.get("marker") else node.get("items")
        item = {"type": "panel", "padding": node.get("padding") or 0, "fill": node.get("fill"), "fillOpacity": node.get("fillOpacity"), "shadow": node.get("shadow"), "radius": node.get("radius"), "nodes": items,
                "hug": node.get("hug"), "minW": node.get("minW"), "maxW": max_width}
        shared_width = 0
        if node.get("hug") and node.get("hugMode") == "max":
            for index in range(count):
                result, _ = _render_node(item, 0, 0, max_width, _fork(ctx, index=index))
                fill = ctx.tokens["colors"].get(node.get("fill"))
                same_surface = _same_panel_surface(node, fill, getattr(ctx, "surface_color", None))
                pad_x = 0 if same_surface else _padding(node)[1]
                box_width = result[0]['w'] if fill and not same_surface and result else max(node.get('minW') or 0, *(el['x'] + el['w'] + pad_x for el in result))
                shared_width = max(shared_width, box_width)
        for index in range(count):
            align = ("right" if index % 2 else "left") if node.get("alternate") else node.get("align") or "left"
            result, height = _render_node({**item, "nodes": items, "align": align,
                                          "minW": shared_width or node.get("minW"), "maxW": shared_width or max_width}, x, cursor, width, _fork(ctx, index=index))
            if node.get("marker") and height:
                first = next((el for el in result if el["type"] == "text"), None)
                if first:
                    first_line = wrap_text(first.get("text") or first.get("placeholder"), first["w"], first["style"]["size"], ctx.catalog["textMetrics"], first["style"]["tracking"])[0]
                    text_w = measure_text_width(first_line, first["w"], first["style"]["size"], ctx.catalog["textMetrics"], first["style"]["tracking"])
                    text_start = first["x"] + ((first["w"] - text_w) / 2 if first["style"]["align"] == "center" else first["w"] - text_w if first["style"]["align"] == "right" else 0)
                    result.insert(0, {"type": "shape", "shape": "circle" if node["marker"] == "dot" else "rect", "x": text_start - 22,
                                      "y": first["y"] + (first["style"]["lineHeight"] - 6) / 2, "w": 6, "h": 6,
                                      "fill": ctx.tokens["colors"]["ink"], "radius": 999 if node["marker"] == "dot" else 0})
            elements.extend(result)
            item_gap = node["itemGap"] if node.get("itemGap") is not None else node.get("gap") or 0
            cursor += height + (item_gap if index < count - 1 else 0)
        return elements, cursor - y
    raise ValueError(f"Unsupported nested layout {kind}")


def _render_nodes(nodes, x, y, width, ctx):
    cursor, elements = y, []
    matching_rules = []
    last_text_width = getattr(ctx, "last_text_width", None)
    for node in nodes or []:
        result, height = _render_node(node, x, cursor, width, _fork(ctx, last_text_width=last_text_width))
        elements.extend(result)
        if node["type"] == "rule" and node.get("matchText") and result:
            matching_rules.append((node, result[0]))
        last = next((el for el in reversed(result) if el["type"] == "text"), None)
        if last:
            last_text_width = measure_text_width(last.get("text"), last["w"], last["style"]["size"], ctx.catalog["textMetrics"], last["style"]["tracking"])
        if not node.get("absolute") and height:
            cursor += height + (node.get("gap") or 0)
    # Both forward (upper rule) and backward (lower rule) references use name,
    # regardless of the label/tagline/colors surrounding it.
    for node, element in matching_rules:
        text = next((el for el in elements if el.get("copyRole") == node["matchText"]), None)
        if text is None:
            continue
        rule_width = min(width * node["maxWidthRatio"], max(node["minW"], measure_text_width(
            text.get("text") or text.get("placeholder"), text["w"], text["style"]["size"], ctx.catalog["textMetrics"], text["style"]["tracking"])))
        element["x"] += (element["w"] - rule_width) / 2
        element["w"] = rule_width
    return elements, max(cursor - y, *(el["y"] + el["h"] - y for el in elements), 0)


def _content_width(elements, x):
    return max(0, *(el["x"] - x + (measure_text_width(el.get("text") or el.get("placeholder"), el["w"], el["style"]["size"], CATALOG["textMetrics"], el["style"]["tracking"]) if el["type"] == "text" else el["w"]) for el in elements))


def _render_paragraphs(node, x, y, width, ctx):
    role = node["splitParagraphs"]
    source = next(child for child in node["nodes"] if child.get("copyRole") == role)
    paragraphs = [text.strip() for text in re.split(r"\n+", _content_for(source, ctx)[0]) if text.strip()]
    parts = paragraphs[:2] + (["\n".join(paragraphs[2:])] if len(paragraphs) > 2 else []) if paragraphs else [""]
    cursor, elements = y, []
    for index, text in enumerate(parts):
        result, height = _render_node({**node, "x": 0, "splitParagraphs": None}, x, cursor, width,
                                     _fork(ctx, paragraph_role=role, paragraph_text=text, paragraph_index=index, paragraph_count=len(parts)))
        elements.extend(result)
        cursor += height + (12 if index < len(parts) - 1 else 0)
    return elements, cursor - y


def _render_fact_table(node, x, y, width, ctx):
    rows = [row.split("  ", 1) for row in _fact_text("facts", ctx).split("\n") if row]
    cursor, elements = y, []
    for index, (label, value) in enumerate(rows):
        stacked = node["layout"] == "stacked"
        align = (ctx.align if node.get("align") == "template" else node.get("align") or ctx.align) if stacked else "left"
        lab, lh = _render_text({"literal": label.upper(), "style": "body", "size": _round((node.get("size") or 25) * .72), "color": "muted", "align": align},
                               x, cursor, width if stacked else 150, ctx)
        val, vh = _render_text({"literal": value, "style": "body", "size": node.get("size") or 25, "align": align},
                               x + (0 if stacked else 150), cursor + (lh + 4 if stacked else 0), width if stacked else width - 150, ctx)
        for part, result in (("label", lab), ("value", val)):
            elements.extend({**el, "sourceBlockId": ctx.source_id, "copyRole": "facts", "textSlotVersion": 1, "factLabel": label, "factPart": part} for el in result)
        cursor += (lh + 4 + vh if stacked else max(lh, vh)) + ((24 if stacked else 20) if index < len(rows) - 1 else 0)
    if rows and node["layout"] == "columns":
        elements.append({"type": "shape", "shape": "rect", "x": x, "y": cursor, "w": width, "h": 1, "fill": ctx.tokens["colors"]["accentLine"], "radius": 0})
        cursor += 1
    return elements, cursor - y


def _photo_group_key(card):
    return f"space:{card['spaceGroupId']}" if (card or {}).get("spaceGroupId") else "color:" + _string((card or {}).get("colorId"))


def _contiguous_photo_groups(cards):
    groups = []
    for card in cards:
        if groups and _photo_group_key(groups[-1][0]) == _photo_group_key(card):
            groups[-1].append(card)
        else:
            groups.append([card])
    return groups


def select_frame_photos(cards, count):
    """Existing photos only; disjoint original groups never merge after selection."""
    groups = _contiguous_photo_groups(cards)

    def medium(group):
        return [card for card in group if card.get("shot") == "medium"]

    winner = next((group for group in groups if len(medium(group)) >= count), None)
    if winner is None:
        winner = max(groups, key=lambda group: len(medium(group)), default=[])
    seen, frame = set(), []
    for card in [*medium(winner), *(card for card in winner if card.get("shot") == "full"), *cards]:
        if len(frame) >= count:
            break
        if card["id"] in seen:
            continue
        seen.add(card["id"])
        frame.append(card)
    unused = [card for card in winner if card["id"] not in seen]
    remaining_groups = [remaining for group in groups if (remaining := [card for card in group if card["id"] not in seen])]
    background = next((card for card in unused if card.get("shot") == "full"), None) or (unused[0] if unused else frame[0] if frame else None)
    return {"frame": frame, "remaining": [card for group in remaining_groups for card in group], "remainingGroups": remaining_groups, "background": background}


def _render_frame_fact(text, role, x, y, width, ctx):
    if not text:
        return [], 0
    frame = ctx.catalog["frameStyle"]
    style = {"font": ctx.tokens["fonts"]["body"]["font"], "size": frame["textSize"], "weight": frame["textWeight"], "italic": False,
             "tracking": _round(frame["textTrackingEm"] * frame["textSize"] * 100) / 100, "lineHeight": frame["textLineHeight"],
             "align": "center", "color": ctx.tokens["colors"][frame["ink"]]}
    height = estimate_text_height(text, width, style["size"], style["lineHeight"], ctx.catalog["textMetrics"], style["tracking"])
    return [{"type": "text", "x": x, "y": y, "w": width, "h": height, "text": text, "style": style,
             "sourceBlockId": ctx.source_id, "copyRole": role, "textSlotVersion": 1}], height


def _render_frame_band(selection, y, ctx):
    if not selection["frame"]:
        return [], 0
    frame = ctx.catalog["frameStyle"]
    card_x, card_y = (ctx.catalog["canvasWidth"] - frame["cardWidth"]) / 2, y + frame["bandPadding"]
    photo_x, photo_width = card_x + frame["paddingX"], frame["cardWidth"] - frame["paddingX"] * 2
    cursor = card_y + frame["paddingTop"]
    content, head_height = _render_frame_fact(_string(ctx.product.get("name")), "name", photo_x, cursor, photo_width, ctx)
    cursor += head_height + (frame["photoTop"] if head_height else 0)
    for index, card in enumerate(selection["frame"]):
        elements, height, _ = _render_photo({"type": "photo", "slot": "$current", "fit": "contain", "aspect": "source", "fallbackAspect": frame["fallbackAspect"]},
                                           photo_x, cursor, photo_width, _fork(ctx, card=card, index=ctx.photos.index(card)))
        content.extend(elements)
        cursor += height + (frame["photoGap"] if index + 1 < len(selection["frame"]) else 0)
    shop_name = ctx.shop_name
    if shop_name:
        cursor += frame["footerGap"]
        foot, foot_height = _render_frame_fact(shop_name, "who", photo_x, cursor, photo_width, ctx)
        content.extend(foot)
        cursor += foot_height
    card_height = cursor + frame["paddingBottom"] - card_y
    height = card_height + frame["bandPadding"] * 2
    # Reusing a backdrop does not consume a remaining original photograph.
    backdrop, _, _ = _render_photo({"type": "photo", "slot": "$current", "aspect": "4/5"}, 0, y, ctx.catalog["canvasWidth"],
                                   _fork(ctx, card=selection["background"], index=ctx.photos.index(selection["background"]), used=set()))
    return [{**backdrop[0], "h": height, "blur": frame["blur"]},
            {"type": "shape", "shape": "rect", "x": card_x, "y": card_y, "w": frame["cardWidth"], "h": card_height,
             "fill": ctx.tokens["colors"][frame["paper"]], "radius": 0}, *content], height


def _photo_flow_rows(node, cards):
    def group_key(card):
        if node.get("groupBy") == "none":
            return "all"
        return _photo_group_key(card)

    rows, start = [], 0
    while start < len(cards):
        end = start + 1
        while end < len(cards) and group_key(cards[end]) == group_key(cards[start]):
            end += 1
        index, row = start, 0
        while index < end:
            remaining = end - index
            count = min(remaining, 2 if node["pattern"] == "twoColumn" or remaining <= 2 or row % 2 else 1)
            rows.append((index, cards[index:index + count]))
            index, row = index + count, row + 1
        start = end
    return rows


def _render_repeated(node, cards, ctx, x, y, width):
    if node["type"] in ("photoGrid", "photoFlow"):
        columns = len(cards) if node["type"] == "photoFlow" else min(node["columns"], ctx.grid_card_count)
        gap = node.get("gap") or 0
        cell_w = math.floor((width - gap * (columns - 1)) / columns)
        start_x = x + ((width - (len(cards) * cell_w + gap * (len(cards) - 1))) / 2 if len(cards) < columns else 0)
        cells = []
        for index, card in enumerate(cards):
            color_index = next((i for i, color in enumerate(ctx.product.get("colors") or []) if _string(color.get("id")) == _string((card or {}).get("colorId"))), index)
            item = _fork(ctx, card=card, index=color_index if node["type"] == "photoGrid" and ctx.kind["kind"] == "color_product" else getattr(ctx, "photo_indexes", {}).get((card or {}).get("id"), ctx.index + index), target_slot=node["slots"][ctx.index + index] if node.get("slots") else None)
            px = start_x + index * (cell_w + gap)
            photo, ph, _ = _render_photo({"type": "photo", "slot": "$current", "aspect": node.get("aspect")}, px, y, cell_w, item)
            captions, ch = _render_nodes(node.get("captions"), px, y + ph + 16, cell_w, item)
            overlays = _render_groups(node.get("overlayGroups"), px, y, cell_w, ph, item, photo[0])
            cells.append((photo + overlays + captions, ph + (16 + ch if ch else 0)))
        return [el for elements, _ in cells for el in elements], max(0, *(h for _, h in cells))
    card = cards[0]
    if node["type"] == "photoStack":
        item = _fork(ctx, card=card, index=getattr(ctx, "photo_indexes", {}).get((card or {}).get("id"), ctx.index))
        if ctx.kind["kind"] == "color_product" and ctx.catalog["designs"][ctx.design_id].get("choiceAxes"):
            item.index = next((i for i, color in enumerate(ctx.product.get("colors") or [])
                               if _string(color.get("id")) == _string((card or {}).get("colorId"))), ctx.index)
        return _render_nodes(node.get("items"), x, y, width, item)
    pad, gap = node.get("padding") or 0, node.get("gap") or 0
    photo_w = min(node["photoWidth"], width - 2 * pad)
    reverse = node.get("alternate") and ctx.index % 2
    photo_x = x + pad + (width - 2 * pad - photo_w if reverse else 0)
    text_x = x + pad + (0 if reverse else photo_w + gap)
    photo, ph, _ = _render_photo({"type": "photo", "slot": "$current", "aspect": "1/1", "radius": node.get("photoRadius")}, photo_x, y + pad, photo_w, _fork(ctx, card=card))
    copy, ch = _render_nodes(node.get("items"), text_x, y + pad, width - 2 * pad - photo_w - gap, _fork(ctx, card=card))
    height = max(ph, ch) + pad * 2
    offset = max(0, (ph - ch) / 2) if node.get("valign") == "center" else 0
    background = [{"type": "shape", "shape": "rect", "x": x, "y": y, "w": width, "h": height, "fill": ctx.tokens["colors"][node["fill"]], "radius": 0}] if node.get("fill") else []
    return background + photo + [{**el, "y": el["y"] + offset} for el in copy], height


def point_photos_for_run(run, storyboard):
    photos = [card for card in run if card.get("source") != "text"]
    lead = next((card for card in run if card.get("source") == "text" and isinstance(card.get("pointItems"), list)), None)
    if lead is None:
        return photos
    by_id = {card["id"]: card for card in storyboard}
    selected = []
    for index, item in enumerate(lead["pointItems"]):
        source = by_id.get(item.get("sourceBlockId"))
        if not source:
            continue
        if item["kind"] == "detail" and source.get("source") == "ai" and source.get("shot") == "detail" and source.get("pageSectionKey") == lead["pageSectionKey"]:
            selected.append(source)
        elif item["kind"] == "wornCrop" and is_worn_crop_source(source):
            selected.append({**source, "pageSectionKey": lead["pageSectionKey"], "pointItem": item,
                "slotKey": lead["pageSectionKey"].split(".", 1)[1] + ".detail" + str(index + 1)})
    selected_ids = {card["id"] for card in selected}
    return selected + [card for card in photos if card["id"] not in selected_ids]


def assemble_v4(storyboard, cut_results, copy_results, product, copywriting, *, analysis=None,
                page_style=None, base_swatch_id=None, failed_cuts=None, license_notice=None, profile=None, shop_name=None, overlay_regions=None):
    from .page_assembler import build_auto_blocks

    catalog, analysis = CATALOG, analysis or {}
    if not storyboard or any(card.get("taxonomyVersion") != 4 for card in storyboard):
        raise ValueError("Expected a taxonomy v4 storyboard")
    template_id = _string(storyboard[0].get("pageSectionKey")).split(".")[0]
    if get_template(template_id) is None:
        raise ValueError("Unknown page template")
    style = page_style or analysis.get("pageStyle") or {**catalog["defaults"], "templateId": template_id}
    tokens = resolve_page_tokens(style, base_swatch_id if base_swatch_id is not None else base_color_of(product).get("swatchId"))
    if tokens["template"]["id"] != template_id:
        raise ValueError("Mixed page templates")
    runs, seen = [], set()
    for card in storyboard:
        key = card.get("pageSectionKey")
        if not _string(key).startswith(template_id + "."):
            raise ValueError("Mixed page templates")
        if runs and runs[-1][0]["pageSectionKey"] == key:
            runs[-1].append(card)
        else:
            if key in seen:
                raise ValueError("Non-contiguous page section")
            seen.add(key)
            runs.append([card])
    cuts = {cut["blockId"]: cut for cut in cut_results or []}
    copies = {copy["blockId"]: {item["role"]: item["text"] for item in copy.get("texts") or []} for copy in copy_results or []}
    blocks = []
    for run in runs:
        first, key = run[0], run[0]["pageSectionKey"]
        kind = catalog["kinds"].get(first.get("pageSectionKind"))
        section = section_definition(tokens["template"], key, first.get("pageSectionKind"), first.get("pageSectionUsage"), first.get("pageDesignId"))
        design_id = first.get("pageDesignId") or section.get("designId")
        photos = point_photos_for_run(run, storyboard)
        if not photos and section.get("fallbackDesignId"):
            design_id = section["fallbackDesignId"]
        design = catalog["designs"].get(design_id) or {}
        if not kind or design.get("kind") != first.get("pageSectionKind") or any(card.get("pageSectionKind") != first.get("pageSectionKind") or card.get("pageDesignId") != first.get("pageDesignId") for card in run):
            raise ValueError("Invalid page section design")
        render_section = section if design.get("invert") is None else {**section, "block": {**section.get("block", {}), "invert": design["invert"]}}
        section_style = section_tokens(tokens, render_section)
        ctx = SimpleNamespace(catalog=catalog, storyboard=storyboard, product=product, analysis=analysis, profile=profile or {},
                              shop_name=shop_name.strip() if isinstance(shop_name, str) else "", copywriting=copywriting,
                              key=key, section=section, kind=kind, cuts=cuts, copy=copies.get(first["id"]) or {}, source_id=first["id"], base_color=base_color_of(product),
                              tokens=section_style, photos=photos, used=set(), failed_cuts=failed_cuts or {}, align=design.get("align") or tokens["template"]["align"],
                              on_photo=catalog["overlayColors"]["light"], index=0, card=None, overlay_regions=overlay_regions, design_id=design_id,
                              surface_color=tokens["colors"]["invertBg"] if (render_section.get("block") or {}).get("invert") else section_style["colors"][design.get("surface") or "canvas"])
        pad = design.get("padding") or 0
        width = catalog["canvasWidth"] - pad * 2
        y, elements, part = design.get("top") or 0, [], 0
        last_text_width = None

        def flush(bottom=None):
            nonlocal y, elements, part
            if not elements:
                return
            block_id = f"b{len(blocks)}"
            height = max(y, *(el["y"] + el["h"] for el in elements)) + ((design.get("bottom") or 0) if bottom is None else bottom)
            blocks.append({"id": block_id, "name": template_section_name(first), "kind": first.get("sectionRole") or "info", "pageSectionKey": key,
                           "pageSectionKind": first["pageSectionKind"], "pageDesignId": design_id,
                           "bg": tokens["colors"]["invertBg"] if (render_section.get("block") or {}).get("invert") else section_style["colors"][design.get("surface") or "canvas"],
                           "h": height, "elements": [{"id": f"{block_id}e{index}", **el} for index, el in enumerate(elements)]})
            elements, y, part = [], 0, part + 1

        for node in design["layout"]:
            if node.get("whenTemplate") and node["whenTemplate"] != tokens["template"]["id"]:
                continue
            if node["type"] == "photoFrame":
                selection = select_frame_photos(_select_cards(node, ctx), node["count"])
                if any(el["type"] == "image" for el in elements):
                    flush()
                frame, frame_height = _render_frame_band(selection, y, ctx)
                elements.extend(frame)
                y += frame_height
                rows = []
                for group in selection["remainingGroups"]:
                    rows.extend([[card] for card in group] if node["pattern"] == "column" else [cards for _, cards in _photo_flow_rows({**node, "groupBy": "none"}, group)])
                remainder_node = ({"type": "photoStack", "items": [{"type": "photo", "slot": "$current", "aspect": node["aspect"], "radius": 16}, *node["captions"]]}
                                  if node["pattern"] == "column" else {**node, "type": "photoFlow"})
                photo_indexes = {card["id"]: index for index, card in enumerate(ctx.photos)}
                if rows:
                    y += catalog["frameStyle"]["remainderGap"]
                    flush()
                for row_index, cards in enumerate(rows):
                    if row_index:
                        flush()
                    result, height = _render_repeated(remainder_node, cards, _fork(ctx, photo_indexes=photo_indexes, index=photo_indexes[cards[0]["id"]]), pad, y, width)
                    elements.extend(result)
                    y += height
                    if row_index + 1 < len(rows):
                        y += tokens["template"]["spacing"]["photoGap"]
                if rows and node["pattern"] != "column":
                    y += node.get("gap") or 0
            elif node["type"] == "photoFlow":
                rows = _photo_flow_rows(node, _select_cards(node, ctx))
                if any(el["type"] == "image" for el in elements):
                    flush()
                for row_index, (index, cards) in enumerate(rows):
                    if row_index:
                        flush()
                    result, height = _render_repeated(node, cards, _fork(ctx, index=index), pad, y, width)
                    elements.extend(result)
                    y += height
                    if row_index + 1 < len(rows):
                        y += tokens["template"]["spacing"]["photoGap"]
                if rows:
                    y += node.get("gap") or 0
            elif node["type"] in ("photoStack", "pointRows", "photoGrid"):
                selected = _select_cards(node, ctx)
                step = node["columns"] if node["type"] == "photoGrid" else 1
                if any(el["type"] == "image" for el in elements):
                    flush()
                for index in range(0, len(selected), step):
                    if index:
                        flush(0 if node.get("itemGap") is not None else None)
                    result, height = _render_repeated(node, selected[index:index + step], _fork(ctx, index=index, grid_card_count=len(selected)), pad, y, width)
                    elements.extend(result)
                    y += height
                    if index + step < len(selected):
                        y += node["itemGap"] if node.get("itemGap") is not None else tokens["template"]["spacing"]["photoGap"]
                if selected:
                    y += node.get("gap") or 0
            else:
                inset = (design.get("contentInset") or 0) if node["type"] in ("text", "factTable", "rule") else 0
                result, height = _render_node(node, pad + inset, y, width - inset * 2, _fork(ctx, last_text_width=last_text_width))
                elements.extend(result)
                last = next((el for el in reversed(result) if el["type"] == "text"), None)
                if last:
                    last_text_width = measure_text_width(last.get("text"), last["w"], last["style"]["size"], catalog["textMetrics"], last["style"]["tracking"])
                if height:
                    y += height + (node.get("gap") or 0)
        for card in [card for card in photos if card["id"] not in ctx.used]:
            flush()
            result, height, _ = _render_photo({"type": "photo", "slot": "$current", "aspect": design["overflow"]["aspect"]}, pad, 0, width, _fork(ctx, card=card, index=part))
            elements.extend(result)
            y = height + (design["overflow"].get("gap") or 0)
        flush()
        if blocks and blocks[-1].get("pageSectionKey") == key:
            block = blocks[-1]
            gap = tokens["template"]["spacing"]["sectionGap" if photos else "textSectionGap"]
            if block["bg"].lower() != catalog["canvasColor"].lower():
                block["elements"].append({"id": f"{block['id']}e{len(block['elements'])}", "type": "shape", "shape": "rect",
                                          "x": 0, "y": block["h"], "w": catalog["canvasWidth"], "h": gap,
                                          "fill": catalog["canvasColor"], "radius": 0, "locked": True, "pageSectionGap": True})
            block["h"] += gap
    auto = build_auto_blocks(product, start_index=len(blocks), license_notice=license_notice)
    block_id = f"b{len(blocks) + len(auto)}"
    return blocks + auto + [{"id": block_id, "name": "공통 주의", "kind": "common-notice", "auto": True, "bg": catalog["canvasColor"], "h": 120,
                             "elements": [{"id": block_id + "e0", "type": "text", "x": 60, "y": 40, "w": 880, "h": 40, "text": catalog["commonNotice"],
                                           "style": {"font": "Pretendard", "size": 20, "color": tokens["colors"]["muted"], "align": "center"}}]}]


def collect_overlay_regions(storyboard, cut_results, copy_results, product, copywriting, **kwargs):
    """Bare-text boxes from the exact same layout and copy used by assembly."""
    regions = []
    assemble_v4(storyboard, cut_results, copy_results, product, copywriting, overlay_regions=regions, **kwargs)
    return regions
