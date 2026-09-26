"""Deterministic wall-only studio palette; seller pixels remain garment truth."""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

POLICY = json.loads((Path(__file__).parents[1] / "data" / "horizon_background_policy.json").read_text(encoding="utf-8"))
_PROMPT = (Path(__file__).parents[2] / "prompts" / "horizon_background_v1.txt").read_text(encoding="utf-8")
MODES = frozenset({"reference", "garment-tone"})
_HEX = re.compile(r"#[0-9a-fA-F]{6}\Z")


def mode(block: dict) -> str:
    return "garment-tone" if block.get("cutType") == "horizon" and block.get("spaceGroupId") and block.get("horizonBackgroundMode") == "garment-tone" else "reference"


def normalize_block(block: dict) -> dict:
    out = dict(block)
    out.pop("_horizonBackground", None)
    out.pop("_horizonLayoutReference", None)
    out.pop("_horizonReferenceShot", None)
    out.pop("_horizonBackgroundFallback", None)
    if out.get("cutType") == "horizon":
        if "horizonBackgroundMode" in out:
            out["horizonBackgroundMode"] = mode(out)
    else:
        out.pop("horizonBackgroundMode", None)
    return out


def hex_to_oklab(value: str) -> tuple[float, float, float]:
    rgb = [int(value[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    r, g, b = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in rgb]
    l = (.4122214708*r + .5363325363*g + .0514459929*b) ** (1/3)
    m = (.2119034982*r + .6806995451*g + .1073969566*b) ** (1/3)
    s = (.0883024619*r + .2817188376*g + .6299787005*b) ** (1/3)
    return (.2104542553*l + .793617785*m - .0040720468*s,
            1.9779984951*l - 2.428592205*m + .4505937099*s,
            .0259040371*l + .7827717662*m - .808675766*s)


def _oklab_hex(lightness: float, a: float, b: float) -> str:
    l, m, s = ((lightness + .3963377774*a + .2158037573*b) ** 3,
               (lightness - .1055613458*a - .0638541728*b) ** 3,
               (lightness - .0894841775*a - 1.291485548*b) ** 3)
    rgb = (4.0767416621*l - 3.3077115913*m + .2309699292*s,
           -1.2684380046*l + 2.6097574011*m - .3413193965*s,
           -.0041960863*l - .7034186147*m + 1.707614701*s)
    values = [max(0, min(1, v)) for v in rgb]
    values = [12.92*v if v <= .0031308 else 1.055*v ** (1/2.4) - .055 for v in values]
    return "#" + "".join(f"{round(v * 255):02x}" for v in values)


def reference(reason: str) -> dict:
    return {"mode": "reference", "reason": reason, "policyVersion": POLICY["version"]}


def palette_for_observed(target: str, *, lightness_range=None) -> dict:
    """Muted hue suggestion; the example, not this swatch, owns wall luminance.

    Used only AFTER source verification in generation. The review tool may call
    this pure function with hypothetical RGB inputs, never as product evidence.
    """
    if not isinstance(target, str) or not _HEX.fullmatch(target):
        return reference("measurement-unavailable")
    lightness, a, b = hex_to_oklab(target)
    bounds = lightness_range if lightness_range is not None else [lightness, lightness]
    if (not isinstance(bounds, (list, tuple)) or len(bounds) != 2
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in bounds)
            or not 0 <= bounds[0] <= bounds[1] <= 1.001):
        return reference("measurement-unavailable")
    # Large lightness ranges are expected in ordinary folded white garments.
    # They are not grounds to disable a hue-only backdrop option. The generated
    # wall keeps the example's existing luminance and shadows (see prompt).
    band = "white" if lightness >= POLICY["whiteGarmentMinimum"] else (
        "dark" if lightness >= POLICY["darkBranchMinimum"] else "light")
    wall_l = POLICY["wallHueSwatchLightness"] + (
        -POLICY["wallHueSwatchContrastOffset"] if band in {"dark", "white"}
        else POLICY["wallHueSwatchContrastOffset"])
    chroma = math.hypot(a, b)
    hue = (math.degrees(math.atan2(b, a)) + 360) % 360
    hue = (int((hue + POLICY["hueStep"] / 2) // POLICY["hueStep"]) * POLICY["hueStep"]) % 360
    # A dark blue-gray garment can have low measured chroma but still show a
    # meaningful cool hue. The same tiny chroma on a bright white photo often
    # comes from illumination, so keep that wall neutral.
    neutral_cutoff = (POLICY["darkGarmentAchromaticThreshold"]
                      if lightness <= POLICY["darkGarmentLightnessMaximum"]
                      else POLICY["achromaticThreshold"])
    wall_c = 0 if chroma < neutral_cutoff else POLICY["subtleChroma"]
    if chroma >= POLICY["strongGarmentChroma"]:
        wall_c = POLICY["mutedChroma"]
    if POLICY["yellowHueRange"][0] <= hue <= POLICY["yellowHueRange"][1]:
        wall_c = min(wall_c, POLICY["darkenedYellowMaxChroma"])
    radians = math.radians(hue)
    wall = _oklab_hex(wall_l, wall_c * math.cos(radians), wall_c * math.sin(radians))
    return {"mode": "garment-tone", "wallHex": wall, "targetHex": target.lower(),
            "reason": "measured-color", "paletteBand": band, "policyVersion": POLICY["version"]}


def resolve(product: dict, color_id=None, *, analysis=None, sources=None) -> dict:
    """Only source-bound server measurements may supply the hue (never PATCH hex)."""
    from . import garment_color_evidence as evidence
    colors = [c for c in product.get("colors", []) if isinstance(c, dict)]
    color = next((c for c in colors if str(c.get("id")) == str(color_id)), None) if color_id is not None else next((c for c in colors if c.get("isBase")), colors[0] if colors else None)
    if color is None or color.get("id") is None:
        return reference("measurement-unavailable")
    selected_id = str(color["id"])
    clothing = product.get("clothingType") or product.get("clothing_type") or "top"
    try:
        contract = evidence.validate_contract((analysis or {}).get(evidence.PERSISTED_KEY))
        profile = next((row for row in contract["colors"] if str(row["colorId"]) == selected_id), None)
        if not profile or profile.get("status") != "ready":
            return reference("measurement-unavailable")
        selected_sources = [row for row in contract["sourceBindings"] if str(row["colorId"]) == selected_id]
        projection = {**contract, "colors": [profile], "sourceBindings": selected_sources}
        current_sources = [row for row in (sources or []) if str(row.get("colorId")) == selected_id]
        if not selected_sources or not evidence.source_binding_matches(projection, current_sources, clothing_type=clothing):
            return reference("measurement-stale")
        bounds = profile.get("lightnessRange")
        if bounds is None:
            return reference("measurement-unavailable")
        return palette_for_observed(profile["observedHex"], lightness_range=bounds)
    except (ValueError, KeyError, TypeError):
        return reference("measurement-unavailable")


def normalize_runtime(value) -> dict | None:
    if not isinstance(value, dict) or value.get("mode") != "garment-tone":
        return None
    wall = value.get("wallHex")
    if not isinstance(wall, str) or not _HEX.fullmatch(wall):
        return None
    target = value.get("targetHex")
    return {"mode": "garment-tone", "wallHex": wall.lower(),
            **({"paletteBand": value["paletteBand"]} if value.get("paletteBand") in {"light", "dark", "white"} else {}),
            "targetHex": target.lower() if isinstance(target, str) and _HEX.fullmatch(target) else None,
            "reason": "measured-color",
            "policyVersion": POLICY["version"]}


def active(block: dict) -> bool:
    """Only a selected option with a server-resolved palette changes generation."""
    return mode(block) == "garment-tone" and normalize_runtime(block.get("_horizonBackground")) is not None


def resolve_for_block(block: dict, product: dict, *, analysis=None, sources=None) -> dict:
    from . import space_set_assets

    try:
        parsed = space_set_assets.parse_space_set_group_id(block.get("spaceGroupId"))
        if not parsed or block.get("cutType") != "horizon":
            return reference("set-unsupported")
        _, registry = space_set_assets.load_space_set_registry()
        entry = registry.get(parsed[0])
    except (OSError, ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
        return reference("set-unsupported")
    if not entry or entry.get("setType") not in {"horizon-rotation", "horizon-sequence"}:
        return reference("set-unsupported")
    return resolve(product, block.get("colorId"), analysis=analysis, sources=sources)


def palettes_for_blocks(blocks: list[dict], product: dict, *, analysis=None, sources=None) -> dict[tuple, dict]:
    modes = {}
    palettes = {}
    for block in blocks:
        if not isinstance(block, dict) or block.get("source") == "mine" or block.get("cutType") != "horizon":
            continue
        group = block.get("spaceGroupId")
        selected_mode = mode(block)
        if group and group in modes and modes[group] != selected_mode:
            raise ValueError("mixed_horizon_background_modes")
        modes[group] = selected_mode
        if selected_mode == "garment-tone":
            key = (group, block.get("colorId"))
            if key not in palettes:
                palettes[key] = resolve_for_block(block, product, analysis=analysis, sources=sources)
    return palettes


def apply_runtime(block: dict, palettes: dict) -> dict:
    out = dict(block)
    out.pop("_horizonBackground", None)
    out.pop("_horizonBackgroundFallback", None)
    if mode(out) == "garment-tone":
        palette = palettes.get((out.get("spaceGroupId"), out.get("colorId")), reference("measurement-unavailable"))
        if palette.get("mode") == "garment-tone":
            out["_horizonBackground"] = palette
        else:
            # Stored intent stays unchanged. The runtime recipe must NOT retain
            # an adaptive flag, otherwise generic prompt fallbacks can tint it.
            out["horizonBackgroundMode"] = "reference"
            out["_horizonBackgroundFallback"] = palette.get("reason", "measurement-unavailable")
    return out


def loaded_sources(product: dict, color_id, assets, images) -> list[dict]:
    colors = product.get("colors") or []
    color = next((c for c in colors if str(c.get("id")) == str(color_id)), None) if color_id is not None else next((c for c in colors if c.get("isBase")), colors[0] if colors else None)
    if not color:
        return []
    rows, seen = [], set()
    for asset, image in zip(assets, images, strict=True):
        slot = asset.get("slot")
        if slot not in {"Front", "Back"} or slot in seen:
            continue
        seen.add(slot)
        rows.append({"sourceIndex": len(rows), "colorId": str(color["id"]), "slot": slot,
                     "data": image.data, "mime": image.mime})
    return rows


def decision(spec: dict) -> dict | None:
    if spec.get("_horizonBackgroundFallback"):
        return {"requested": "garment-tone", "effective": "reference",
                "reason": spec["_horizonBackgroundFallback"], "policyVersion": POLICY["version"]}
    palette = normalize_runtime(spec.get("_horizonBackground"))
    if palette:
        return {"requested": "garment-tone", "effective": "garment-tone", **palette}
    return None


def restore_failed_retry(request: dict, storyboard: list, editor_blocks: list) -> dict:
    """Only an existing empty editor slot can replay a stored horizon set member."""
    retry_id = request.get("retryBlockId")
    source = next((b for b in storyboard if isinstance(b, dict) and b.get("id") == retry_id), None)
    empty_slot = any(
        isinstance(el, dict) and el.get("type") == "image" and not el.get("src") and el.get("sourceBlockId") == retry_id
        for eb in editor_blocks if isinstance(eb, dict)
        for el in (eb.get("elements") or [])
    )
    if not source or source.get("source") != "ai" or source.get("cutType") != "horizon" or not source.get("spaceGroupId") or not empty_slot:
        raise ValueError("invalid_horizon_set_retry")
    for key in ("spaceGroupId", "exampleId", "colorId"):
        if request.get(key) != source.get(key):
            raise ValueError("invalid_horizon_set_retry")
    if mode(request) != mode(source):
        raise ValueError("invalid_horizon_set_retry")
    out = normalize_block(source)
    out.pop("_facemarket", None)
    out.update(mode="new", retryBlockId=retry_id)
    # The project-selected model is validated by the existing route gate.
    out["modelId"] = request.get("modelId")
    return out


def prompt(value) -> str:
    palette = normalize_runtime(value)
    if not palette:
        return ""
    return _PROMPT.replace("${wallHex}", palette["wallHex"]).strip()
