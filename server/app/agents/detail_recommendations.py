"""Server-owned, source-bound detail subjects from the existing AG-08 call.

Labels, reasons and featurePoints are display/ranking data, never instructions.
Regions refer to the EXIF-transposed image; hashes always bind original bytes.
"""
from copy import deepcopy
from hashlib import sha256
import hmac
import json
import math
import re
import time

PERSISTED_KEY = "detailRecommendations"
HANDOFF_KEY = "detailRecommendationsHandoff"
KINDS = ("neckline", "closure", "pocket", "waist", "cuff", "hem", "construction", "surface", "fabric", "label")
_DIRECTIONS = {"Front": "front", "Detail": "front", "Back": "back", "BackDetail": "back"}
_TTL = 86400
_STANDALONE_VALUES = ("construction", "material", "finish_quality", "readable_identity", "appearance_only")
_MODEL_FIELDS = {"kind", "sourceIndex", "region", "photoUse", "standaloneValue", "visibility", "rank", "label", "reason", "informationGroup", "featurePoints"}


class DetailRecommendationError(ValueError):
    pass


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def _text(value, limit):
    if not isinstance(value, str):
        raise DetailRecommendationError("detail_invalid_display_text")
    value = re.sub(r"[\x00-\x1f\x7f<>]", " ", value)
    value = " ".join(value.split())[:limit]
    if not value or "${" in value or "[[" in value or "]]" in value:
        raise DetailRecommendationError("detail_invalid_display_text")
    return value


def candidate_schema():
    props = {
        "kind": {"type": "string", "enum": list(KINDS)},
        "sourceIndex": {"type": "integer"},
        "region": {"type": "object", "additionalProperties": False,
                   "properties": {k: {"type": "number"} for k in ("x", "y", "w", "h")},
                   "required": ["x", "y", "w", "h"]},
        "photoUse": {"type": "string", "enum": ["standalone", "context", "unsupported"]},
        "standaloneValue": {"type": "string", "enum": list(_STANDALONE_VALUES)},
        "visibility": {"type": "string", "enum": ["clear", "uncertain"]},
        "rank": {"type": "integer"},
        "label": {"type": "string"}, "reason": {"type": "string"},
        "informationGroup": {"type": "string"},
        "featurePoints": {"type": "array", "items": {"type": "string"}},
    }
    return {"type": "array", "items": {"type": "object", "additionalProperties": False,
            "properties": props, "required": list(props)}}


def analysis_source_entries(product):
    """Same four-photo selection as publicAnalysisPhotos, then canonical slot order."""
    colors = product.get("colors") or []
    base = next((c for c in colors if c.get("isBase")), colors[0] if colors else {})
    images = [image for image in base.get("images", []) if image.get("id")]
    selected = []
    for slot in ("Front", "Back"):
        index = next((i for i, image in enumerate(images) if image.get("slot") == slot), None)
        if index is not None:
            selected.append(index)
    selected.extend(i for i in range(len(images)) if i not in selected)
    order = {"Front": 0, "Back": 1, "Detail": 2, "BackDetail": 3}
    selected = sorted(selected[:4], key=lambda i: order.get(images[i].get("slot"), 99))
    return [(images[i].get("slot") or "Front", images[i]["id"]) for i in selected]


def _binding(source_images, slots, *, max_images=4):
    if not (isinstance(source_images, list) and isinstance(slots, list)
            and 1 <= len(source_images) <= max_images and len(source_images) == len(slots)):
        raise DetailRecommendationError("detail_source_count_invalid")
    rows = []
    for index, ((data, _mime), slot) in enumerate(zip(source_images, slots)):
        if not isinstance(slot, str) or slot not in _DIRECTIONS or not isinstance(data, bytes) or not data:
            raise DetailRecommendationError("detail_source_invalid")
        rows.append({"sourceIndex": index, "sourceSlot": slot,
                     "sourceSha256": sha256(data).hexdigest(), "byteLength": len(data)})
    return rows


def _candidate(raw, binding):
    if not isinstance(raw, dict) or set(raw) != _MODEL_FIELDS:
        raise DetailRecommendationError("detail_candidate_fields_invalid")
    index = raw["sourceIndex"]
    if type(index) is not int or not 0 <= index < len(binding):
        raise DetailRecommendationError("detail_candidate_source_invalid")
    if (raw["kind"] not in KINDS or raw["photoUse"] not in ("standalone", "context", "unsupported")
            or raw["standaloneValue"] not in _STANDALONE_VALUES
            or raw["visibility"] not in ("clear", "uncertain")
            or type(raw["rank"]) is not int or not 1 <= raw["rank"] <= 20):
        raise DetailRecommendationError("detail_candidate_enum_invalid")
    region = raw["region"]
    if (not isinstance(region, dict) or set(region) != {"x", "y", "w", "h"}
            or any(type(n) not in (int, float) or not math.isfinite(n) for n in region.values())):
        raise DetailRecommendationError("detail_region_invalid")
    x, y, w, h = (region[k] for k in ("x", "y", "w", "h"))
    if not (0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 and 0 < h <= 1
            and x + w <= 1 and y + h <= 1):
        raise DetailRecommendationError("detail_region_out_of_bounds")
    group = raw["informationGroup"]
    if not isinstance(group, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,47}", group):
        raise DetailRecommendationError("detail_information_group_invalid")
    points = raw["featurePoints"]
    if not isinstance(points, list) or len(points) > 6:
        raise DetailRecommendationError("detail_feature_points_invalid")
    source = binding[index]
    candidate = {**raw, "region": dict(region), "label": _text(raw["label"], 48),
                 "reason": _text(raw["reason"], 180),
                 "featurePoints": [_text(point, 48) for point in points],
                 "sourceSlot": source["sourceSlot"], "sourceSha256": source["sourceSha256"],
                 "direction": _DIRECTIONS[source["sourceSlot"]]}
    if candidate["standaloneValue"] == "appearance_only" and candidate["photoUse"] == "standalone":
        candidate["photoUse"] = "context"
    candidate["id"] = "dt1_" + sha256(_canonical(candidate)).hexdigest()[:24]
    return candidate


def build_contract(raw, source_images, slots):
    binding = _binding(source_images, slots)
    valid = isinstance(raw, list) and len(raw) <= 20
    candidates = []
    for item in raw if valid else []:
        try:
            parsed = _candidate(item, binding)
        except DetailRecommendationError:
            continue
        if parsed["id"] not in {c["id"] for c in candidates}:
            candidates.append(parsed)
    return {"version": 1, "status": "ready" if valid and (not raw or candidates) else "unavailable",
            "candidates": sorted(candidates, key=lambda c: c["rank"]), "binding": binding}


def validate_persisted(value):
    if (not isinstance(value, dict) or set(value) != {"version", "status", "candidates", "binding"}
            or type(value["version"]) is not int or value["version"] != 1
            or value["status"] not in ("ready", "unavailable")):
        raise DetailRecommendationError("detail_contract_invalid")
    binding = value["binding"]
    if not isinstance(binding, list) or not 1 <= len(binding) <= 4:
        raise DetailRecommendationError("detail_binding_invalid")
    for index, row in enumerate(binding):
        if (not isinstance(row, dict) or set(row) != {"sourceIndex", "sourceSlot", "sourceSha256", "byteLength"}
                or type(row["sourceIndex"]) is not int or row["sourceIndex"] != index
                or not isinstance(row["sourceSlot"], str) or row["sourceSlot"] not in _DIRECTIONS
                or not isinstance(row["sourceSha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", row["sourceSha256"])
                or type(row["byteLength"]) is not int or row["byteLength"] <= 0):
            raise DetailRecommendationError("detail_binding_invalid")
    candidates = value["candidates"]
    if not isinstance(candidates, list) or len(candidates) > 20 or (value["status"] == "unavailable" and candidates):
        raise DetailRecommendationError("detail_candidates_invalid")
    ids = set()
    for item in candidates:
        if not isinstance(item, dict):
            raise DetailRecommendationError("detail_candidate_invalid")
        parsed = _candidate({k: v for k, v in item.items() if k in _MODEL_FIELDS}, binding)
        if parsed != item or item["id"] in ids:
            raise DetailRecommendationError("detail_candidate_modified")
        ids.add(item["id"])
    return deepcopy(value)


def public_summary(value):
    contract = validate_persisted(value)
    return {k: contract[k] for k in ("version", "status", "candidates")}


def source_binding_matches(value, source_images, slots):
    binding = validate_persisted(value)["binding"]
    available = _binding(source_images, slots, max_images=max(4, len(source_images)))
    return all(any(all(row[k] == source[k] for k in ("sourceSlot", "sourceSha256", "byteLength"))
                   for source in available) for row in binding)


def resolve_target(analysis, target_id, *, source_images, slots, direction):
    if target_id is None:
        return None
    if not isinstance(target_id, str) or not target_id:
        raise DetailRecommendationError("detail_target_id_invalid")
    value = validate_persisted((analysis or {}).get(PERSISTED_KEY))
    if not source_binding_matches(value, source_images, slots):
        raise DetailRecommendationError("detail_target_source_drift")
    for candidate in value["candidates"]:
        if candidate["id"] == target_id:
            if candidate["direction"] != direction:
                raise DetailRecommendationError("detail_target_direction_mismatch")
            if candidate["photoUse"] == "unsupported":
                raise DetailRecommendationError("detail_target_unsupported")
            source_index = next(i for i, ((data, _mime), slot) in enumerate(zip(source_images, slots))
                                if slot == candidate["sourceSlot"] and sha256(data).hexdigest() == candidate["sourceSha256"])
            return {**candidate, "sourceIndex": source_index}
    raise DetailRecommendationError("detail_target_unknown")


def _signature(payload, secret):
    if not isinstance(secret, str) or len(secret) < 16:
        raise DetailRecommendationError("detail_handoff_secret_unavailable")
    return hmac.new(secret.encode(), _canonical(payload), "sha256").hexdigest()


def issue_handoff(value, secret, *, now=None):
    issued = int(time.time()) if now is None else now
    payload = {"purpose": PERSISTED_KEY, "version": 1, "issuedAt": issued,
               "expiresAt": issued + _TTL, "contract": validate_persisted(value)}
    return {**payload, "signature": _signature(payload, secret)}


def verify_handoff(value, secret, *, now=None):
    current = int(time.time()) if now is None else now
    if not isinstance(value, dict) or set(value) != {"purpose", "version", "issuedAt", "expiresAt", "contract", "signature"}:
        raise DetailRecommendationError("detail_handoff_invalid")
    if (value["purpose"] != PERSISTED_KEY or type(value["version"]) is not int or value["version"] != 1
            or type(value["issuedAt"]) is not int or type(value["expiresAt"]) is not int
            or value["expiresAt"] - value["issuedAt"] != _TTL
            or value["issuedAt"] > current + 60 or value["expiresAt"] < current):
        raise DetailRecommendationError("detail_handoff_expired_or_invalid")
    signature = value["signature"]
    if not isinstance(signature, str) or not re.fullmatch(r"[a-f0-9]{64}", signature):
        raise DetailRecommendationError("detail_handoff_signature_invalid")
    try:
        expected = _signature({k: v for k, v in value.items() if k != "signature"}, secret)
    except (TypeError, ValueError) as exc:
        raise DetailRecommendationError("detail_handoff_invalid") from exc
    if not hmac.compare_digest(signature, expected):
        raise DetailRecommendationError("detail_handoff_signature_invalid")
    return validate_persisted(value["contract"])
