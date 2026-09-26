"""Representative photo color, never calibrated fabric color.

The optional observer supplies interior patches, not RGB. Uniform background
mistaken for fabric cannot be detected by color statistics. Thresholds are
initial conservative heuristics; source ownership remains model-dependent.
"""
from collections import Counter
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import hmac
import json
import math
import re
from statistics import median
import time

from PIL import Image, ImageCms, ImageDraw, ImageOps

PERSISTED_KEY = "garmentColorEvidence"
RAW_KEY = "garmentColorRegions"
HANDOFF_KEY = "garmentColorEvidenceHandoff"
_PURPOSE = "wearless:garment-color-evidence:v1"
_TTL = 86400
_TYPES = ("top", "bottom", "outer", "dress")
MAX_IMAGE_PIXELS = 40_000_000
_MAX_PIXELS = MAX_IMAGE_PIXELS
_REASONS = {"measured", "insufficient_evidence", "invalid_region", "unreliable_source",
            "invalid_image", "invalid_color_profile", "insufficient_pixels",
            "transparency", "mixed_pixels", "patch_disagreement", "view_disagreement"}


def regions_schema():
    props = {
        "sourceIndex": {"type": "integer", "minimum": 0, "maximum": 7},
        "clothingType": {"type": "string", "enum": [*_TYPES, "uncertain"]},
        "certainty": {"type": "string", "enum": ["high", "uncertain"]},
        "colorStructure": {"type": "string", "enum": ["solid", "patterned", "multicolor", "uncertain"]},
        "lighting": {"type": "string", "enum": ["neutral", "cast", "uncertain"]},
        "material": {"type": "string", "enum": ["matte", "glossy", "sheer", "uncertain"]},
        "polygons": {"type": "array", "minItems": 0, "maxItems": 6, "items": {
            "type": "array", "minItems": 3, "maxItems": 12, "items": {
                "type": "array", "minItems": 2, "maxItems": 2,
                "items": {"type": "number", "minimum": 0, "maximum": 1}}}},
    }
    return {"type": "array", "maxItems": 8, "items": {"type": "object", "additionalProperties": False,
            "properties": props, "required": list(props)}}


def _number(value, low=0, high=1):
    return type(value) in (float, int) and math.isfinite(value) and low <= value <= high


def _frame(data):
    try:
        with Image.open(BytesIO(data)) as raw:
            if raw.width * raw.height > _MAX_PIXELS:
                raise ValueError("invalid_image")
            orientation = raw.getexif().get(274, 1)
            profile = raw.info.get("icc_profile")
            im = ImageOps.exif_transpose(raw)
            alpha = im.getchannel("A") if im.mode in ("RGBA", "LA", "PA") else Image.new("L", im.size, 255)
            if im.mode == "P" and "transparency" in im.info:
                alpha = im.convert("RGBA").getchannel("A")
            changed = orientation not in (None, 1)
            if profile:
                try:
                    source_profile = ImageCms.ImageCmsProfile(BytesIO(profile))
                    color = im.convert("RGB") if im.mode == "RGBA" else im
                    converted = ImageCms.profileToProfile(color, source_profile, ImageCms.createProfile("sRGB"), outputMode="RGB")
                    changed = True
                    im = converted
                except Exception as exc:
                    raise ValueError("invalid_color_profile") from exc
            elif im.mode not in ("RGB", "RGBA", "L", "LA", "P", "1"):
                raise ValueError("invalid_color_profile")
            im = im.convert("RGBA")
            im.putalpha(alpha)
            im.load()
            return im, changed
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("invalid_image") from exc


def normalize_for_vision(data: bytes, mime: str):
    """Same EXIF-upright/sRGB frame as sampling; malformed inputs remain nonfatal."""
    try:
        im, changed = _frame(data)
        if not changed:
            return data, mime
        output = BytesIO()
        # Fresh image drops the original ICC/EXIF, avoiding double conversion.
        clean = Image.new("RGBA", im.size)
        clean.paste(im)
        clean.save(output, format="PNG")
        return output.getvalue(), "image/png"
    except (ValueError, OSError):
        return data, mime


def _cross(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _intersects(a, b, c, d):
    return _cross(a, b, c) * _cross(a, b, d) <= 0 and _cross(c, d, a) * _cross(c, d, b) <= 0


def _polygons(value):
    if not isinstance(value, list) or not 2 <= len(value) <= 6:
        raise ValueError("invalid_region")
    boxes = []
    for polygon in value:
        if (not isinstance(polygon, list) or not 3 <= len(polygon) <= 12
                or any(not isinstance(p, list) or len(p) != 2 or not all(_number(x) for x in p) for p in polygon)):
            raise ValueError("invalid_region")
        crosses = [_cross(polygon[i - 2], polygon[i - 1], polygon[i]) for i in range(len(polygon))]
        if (len(set(tuple(p) for p in polygon)) != len(polygon)
                or not (all(x > 1e-9 for x in crosses) or all(x < -1e-9 for x in crosses))):
            raise ValueError("invalid_region")
        count = len(polygon)
        for i in range(count):
            for j in range(i + 1, count):
                if j == i + 1 or (i == 0 and j == count - 1):
                    continue
                if _intersects(polygon[i], polygon[(i + 1) % count], polygon[j], polygon[(j + 1) % count]):
                    raise ValueError("invalid_region")
        area = abs(sum(polygon[i - 1][0] * p[1] - p[0] * polygon[i - 1][1] for i, p in enumerate(polygon))) / 2
        xs, ys = zip(*polygon)
        box = (min(xs), min(ys), max(xs), max(ys))
        if area < .001 or box[2] - box[0] < .025 or box[3] - box[1] < .025:
            raise ValueError("invalid_region")
        # Conservative: bounding boxes must not overlap; rectangles are preferred.
        if any(min(box[2], b[2]) > max(box[0], b[0]) and min(box[3], b[3]) > max(box[1], b[1]) for b in boxes):
            raise ValueError("invalid_region")
        boxes.append(box)
    return value


def _lab(rgb):
    r, g, b = [(v / 255 / 12.92 if v / 255 <= .04045 else ((v / 255 + .055) / 1.055) ** 2.4) for v in rgb]
    l = (.4122214708*r + .5363325363*g + .0514459929*b) ** (1/3)
    m = (.2119034982*r + .6806995451*g + .1073969566*b) ** (1/3)
    s = (.0883024619*r + .2817188376*g + .6299787005*b) ** (1/3)
    return (.2104542553*l + .793617785*m - .0040720468*s,
            1.9779984951*l - 2.428592205*m + .4505937099*s,
            .0259040371*l + .7827717662*m - .808675766*s)


def _distance(a, b):
    return math.sqrt(sum((x-y)**2 for x, y in zip(a, b)))


def _color_distance(a, b):
    # Wrinkles change lightness much more than hue. Keep chromatic distance at
    # full weight and retain unweighted lightness separately for wall safety.
    return math.sqrt((.35 * (a[0]-b[0]))**2 + (a[1]-b[1])**2 + (a[2]-b[2])**2)


def _rgb_median(pixels):
    return tuple(round(median(p[i] for p in pixels)) for i in range(3))


def _quantile(values, fraction):
    return sorted(values)[min(len(values)-1, int((len(values)-1) * fraction))]


def _patch(im, polygon, cap):
    cx, cy = [sum(p[i] for p in polygon) / len(polygon) for i in range(2)]
    points = [((cx + .8*(x-cx)) * (im.width-1), (cy + .8*(y-cy)) * (im.height-1)) for x, y in polygon]
    mask = Image.new("L", im.size)
    ImageDraw.Draw(mask).polygon(points, fill=255)
    bounds = mask.getbbox()
    if not bounds:
        raise ValueError("insufficient_pixels")
    x0, y0, x1, y1 = bounds
    step = max(1, math.ceil(math.sqrt((x1-x0)*(y1-y0)/cap)))
    data, masked = im.load(), mask.load()
    samples = [data[x, y] for y in range(y0, y1, step) for x in range(x0, x1, step) if masked[x, y]][:cap]
    opaque = [p[:3] for p in samples if p[3] >= 250]
    if len(opaque) < .7 * len(samples):
        raise ValueError("transparency")
    if len(opaque) < 64:
        raise ValueError("insufficient_pixels")
    hist = Counter(tuple(v // 8 for v in p) for p in opaque)
    labs = {key: _lab(tuple(min(255, v*8+4) for v in key)) for key in hist}
    seeds = [key for key, _ in hist.most_common(8)]
    center = max(seeds, key=lambda key: sum(count for other, count in hist.items() if _color_distance(labs[key], labs[other]) <= .04))
    selected = {key for key in hist if _color_distance(labs[center], labs[key]) <= .04}
    fraction = sum(hist[key] for key in selected) / len(opaque)
    # A stripe, wash, normal fold or highlight can make the dominant cluster
    # less than 60% of a valid fabric patch. Its overall median is still a
    # useful coarse backdrop hue; never turn ordinary texture into no option.
    pixels = ([p for p in opaque if tuple(v // 8 for v in p) in selected]
              if fraction >= .6 else opaque)
    rgb = _rgb_median(pixels)
    lab = _lab(rgb)
    # The representative RGB trims sparse shade/highlight tails, but its safety
    # range/spread use all opaque pixels (robust 10-90%), not a narrowed cluster.
    actual_labs = [_lab(p) for p in opaque]
    return {"rgb": rgb, "lab": lab, "spread": _quantile([_distance(p, lab) for p in actual_labs], .9),
            "range": [_quantile([p[0] for p in actual_labs], .1), _quantile([p[0] for p in actual_labs], .9)],
            "outliers": 1-fraction}


def _measure(source, row, clothing_type):
    if not isinstance(row, dict) or set(row) != set(regions_schema()["items"]["properties"]):
        raise ValueError("invalid_region")
    if (row["colorStructure"] not in {"solid", "patterned", "multicolor", "uncertain"}
            or row["lighting"] not in {"neutral", "cast", "uncertain"}
            or row["material"] not in {"matte", "glossy", "sheer", "uncertain"}):
        raise ValueError("invalid_region")
    if row["clothingType"] != clothing_type or row["certainty"] != "high":
        raise ValueError("unreliable_source")
    polygons = _polygons(row["polygons"])
    im, _ = _frame(source["data"])
    im.thumbnail((512, 512), Image.Resampling.LANCZOS)
    patches = [_patch(im, p, 16000 // len(polygons)) for p in polygons]
    # Disagreement raises the reported uncertainty; it does not make the
    # selected photo's broad color family unknowable.
    return {"patches": patches, "lab": _lab(_rgb_median([p["rgb"] for p in patches])),
            "spread": max(_distance(a["lab"], b["lab"]) for a in patches for b in patches),
            "range": [min(p["range"][0] for p in patches), max(p["range"][1] for p in patches)]}


def _bindings(sources):
    if not isinstance(sources, list) or len(sources) > 8:
        raise ValueError("garment_color_sources_invalid")
    result = []
    indices, identities = set(), set()
    for source in sources:
        if (not isinstance(source, dict) or type(source.get("sourceIndex")) is not int or not 0 <= source["sourceIndex"] <= 7
                or not isinstance(source.get("colorId"), str) or not 1 <= len(source["colorId"]) <= 128
                or source.get("slot") not in ("Front", "Back") or not isinstance(source.get("data"), bytes) or not source["data"]):
            raise ValueError("garment_color_sources_invalid")
        index, identity = source["sourceIndex"], (source["colorId"], source["slot"])
        if index in indices or identity in identities:
            raise ValueError("garment_color_sources_invalid")
        indices.add(index); identities.add(identity)
        result.append({"sourceIndex": index, "colorId": source["colorId"], "slot": source["slot"],
                       "sourceSha256": sha256(source["data"]).hexdigest(), "byteLength": len(source["data"])})
    return result


def build_contract(raw_regions, sources, *, clothing_type):
    if clothing_type not in _TYPES:
        raise ValueError("garment_color_clothing_type_invalid")
    bindings = _bindings(sources)
    known = {s["sourceIndex"] for s in sources}
    rows = {}
    if isinstance(raw_regions, list):
        for row in raw_regions[:16]:
            index = row.get("sourceIndex") if isinstance(row, dict) else None
            if type(index) is int and index in known:
                rows[index] = row if index not in rows else None
    colors = []
    for color_id in dict.fromkeys(s["colorId"] for s in sources):
        selected = [s for s in sources if s["colorId"] == color_id]
        color = {"colorId": color_id, "status": "unavailable", "reason": "insufficient_evidence",
                 "sourceIndices": [s["sourceIndex"] for s in selected]}
        try:
            if any(rows.get(s["sourceIndex"]) is None for s in selected):
                raise ValueError("insufficient_evidence")
            views = [_measure(s, rows[s["sourceIndex"]], clothing_type) for s in selected]
            view_delta = max(_distance(a["lab"], b["lab"]) for a in views for b in views)
            # Front and back may legitimately have different panels. Keep a
            # photo-derived approximate tone and report its spread below.
            patches = [p for v in views for p in v["patches"]]
            rgb = _rgb_median([p["rgb"] for p in patches])
            spread = max([view_delta, *[v["spread"] for v in views], *[p["spread"] for p in patches]])
            margin = .015 if min(rgb) >= 248 else .005
            color.update(status="ready", reason="measured", observedHex="#" + "".join(f"{v:02X}" for v in rgb),
                         spread=round(spread, 6), uncertainty=round(min(1., .05 + spread*5 + max(p["outliers"] for p in patches)*.5), 6),
                         lightnessRange=[round(max(0., min(p["range"][0] for p in patches)-margin), 6),
                                         round(min(1., max(p["range"][1] for p in patches)+margin), 6)])
        except ValueError as exc:
            color["reason"] = str(exc) if str(exc) in _REASONS else "invalid_image"
        colors.append(color)
    return validate_contract({"version": 1, "clothingType": clothing_type, "sourceBindings": bindings, "colors": colors})


def validate_contract(value):
    invalid = ValueError("garment_color_contract_invalid")
    if (not isinstance(value, dict) or set(value) != {"version", "clothingType", "sourceBindings", "colors"}
            or type(value["version"]) is not int or value["version"] != 1 or value["clothingType"] not in _TYPES
            or not isinstance(value["sourceBindings"], list) or len(value["sourceBindings"]) > 8
            or not isinstance(value["colors"], list) or len(value["colors"]) > 4):
        raise invalid
    indices, identities, by_color = set(), set(), {}
    for row in value["sourceBindings"]:
        if (not isinstance(row, dict) or set(row) != {"sourceIndex", "colorId", "slot", "sourceSha256", "byteLength"}
                or type(row["sourceIndex"]) is not int or not 0 <= row["sourceIndex"] <= 7
                or not isinstance(row["colorId"], str) or not 1 <= len(row["colorId"]) <= 128
                or row["slot"] not in ("Front", "Back") or not isinstance(row["sourceSha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", row["sourceSha256"])
                or type(row["byteLength"]) is not int or row["byteLength"] <= 0):
            raise invalid
        identity = (row["colorId"], row["slot"])
        if row["sourceIndex"] in indices or identity in identities:
            raise invalid
        indices.add(row["sourceIndex"]); identities.add(identity)
        by_color.setdefault(row["colorId"], set()).add(row["sourceIndex"])
    seen = set()
    base = {"colorId", "status", "reason", "sourceIndices"}
    ready = {"observedHex", "spread", "uncertainty", "lightnessRange"}
    for row in value["colors"]:
        if not isinstance(row, dict) or not isinstance(row.get("colorId"), str) or row["colorId"] in seen:
            raise invalid
        seen.add(row["colorId"])
        if (row.get("status") not in ("ready", "unavailable") or not isinstance(row.get("reason"), str)
                or row["reason"] not in _REASONS or not isinstance(row.get("sourceIndices"), list)
                or any(type(i) is not int for i in row["sourceIndices"])
                or len(set(row["sourceIndices"])) != len(row["sourceIndices"])
                or set(row["sourceIndices"]) != by_color.get(row["colorId"])):
            raise invalid
        if row["status"] == "ready":
            bounds = row.get("lightnessRange")
            if (set(row) != base | ready or row["reason"] != "measured"
                    or not isinstance(row.get("observedHex"), str) or not re.fullmatch(r"#[0-9A-F]{6}", row["observedHex"])
                    or not _number(row.get("spread"), 0, 1.5) or not _number(row.get("uncertainty"))
                    or not isinstance(bounds, list) or len(bounds) != 2 or not all(_number(n) for n in bounds) or bounds[0] > bounds[1]):
                raise invalid
        elif set(row) != base or row["reason"] == "measured":
            raise invalid
    if seen != set(by_color):
        raise invalid
    return deepcopy(value)


def public_summary(value):
    # Eligibility is derived from today's palette policy, never signed/stored as
    # measurement truth. A usable photo color can still have no safe wall color.
    from . import horizon_background

    value = validate_contract(value)
    colors = []
    for row in value["colors"]:
        palette = (horizon_background.palette_for_observed(row["observedHex"], lightness_range=row["lightnessRange"])
                   if row["status"] == "ready" else horizon_background.reference("measurement-unavailable"))
        color = {k: row[k] for k in ("colorId", "status", "reason", "observedHex") if k in row}
        color.update(backgroundStatus="ready" if palette["mode"] == "garment-tone" else "reference",
                     backgroundReason=palette["reason"], backgroundPolicyVersion=palette["policyVersion"])
        colors.append(color)
    return {"version": 1, "clothingType": value["clothingType"], "colors": colors}


def source_binding_matches(value, sources, clothing_type=None):
    try:
        value = validate_contract(value)
        if not value["sourceBindings"]:
            return False
        if clothing_type is not None and clothing_type != value["clothingType"]:
            return False
        current = _bindings(sources)
        key = lambda row: (row["colorId"], row["slot"], row["sourceSha256"], row["byteLength"])
        # Indices are analysis-ROI provenance, not runtime identity.
        return Counter(map(key, value["sourceBindings"])) == Counter(map(key, current))
    except (ValueError, TypeError, KeyError):
        return False


def _browser_stable(value):
    # Guest handoffs pass through JSON.stringify, which writes 0.0/1.0 as 0/1.
    # Normalize only equal whole-number representations; never round fractions.
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {key: _browser_stable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_browser_stable(item) for item in value]
    return value


def _canonical(value):
    try:
        return json.dumps(_browser_stable(value), sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    except (ValueError, TypeError) as exc:
        raise ValueError("garment_color_handoff_invalid") from exc


def _sign(value, secret):
    if not isinstance(secret, str) or len(secret) < 16:
        raise ValueError("garment_color_handoff_secret_required")
    return hmac.new(secret.encode(), _canonical(value), "sha256").hexdigest()


def issue_handoff(value, secret, now=None):
    issued = int(time.time() if now is None else now)
    payload = {"purpose": _PURPOSE, "version": 1, "issuedAt": issued,
               "expiresAt": issued + _TTL, "contract": validate_contract(value)}
    return {**payload, "signature": _sign(payload, secret)}


def verify_handoff(envelope, secret, now=None):
    current = int(time.time() if now is None else now)
    if not isinstance(envelope, dict) or set(envelope) != {"purpose", "version", "issuedAt", "expiresAt", "contract", "signature"}:
        raise ValueError("garment_color_handoff_invalid")
    if (envelope["purpose"] != _PURPOSE or type(envelope["version"]) is not int or envelope["version"] != 1
            or type(envelope["issuedAt"]) is not int or type(envelope["expiresAt"]) is not int
            or envelope["expiresAt"] - envelope["issuedAt"] != _TTL
            or envelope["issuedAt"] > current + 60 or envelope["expiresAt"] < current):
        raise ValueError("garment_color_handoff_expired_or_invalid")
    payload = {k: v for k, v in envelope.items() if k != "signature"}
    if (not isinstance(envelope["signature"], str) or not re.fullmatch(r"[0-9a-f]{64}", envelope["signature"])
            or not hmac.compare_digest(envelope["signature"], _sign(payload, secret))):
        raise ValueError("garment_color_handoff_signature_invalid")
    return validate_contract(envelope["contract"])
