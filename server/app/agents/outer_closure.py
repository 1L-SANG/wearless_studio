"""아우터 열림 높이와 서버가 판정한 앞 여밈을 읽는다."""

from .prompts import _sanitize


STATES = ("closed", "openToChest", "partial", "openToWaist", "open")
DEFAULT = "open"
EXPOSED = frozenset(STATES) - {"closed"}
KINDS = ("zipper", "button", "snap", "belt", "none", "unknown")
HARDWARE = KINDS[:4]
SLOTS = ("Front", "Back", "Detail", "BackDetail")


def normalize_state(value) -> str:
    return value if isinstance(value, str) and value in STATES else DEFAULT


def front_closure_of(analysis) -> dict:
    fallback = {"version": 1, "kind": "unknown", "range": "graded", "secondary": None,
                "evidenceSlots": [], "note": None}
    raw = analysis.get("frontClosure") if isinstance(analysis, dict) else None
    if not isinstance(raw, dict) or not fallback.keys() <= raw.keys():
        return fallback
    kind, closure_range, secondary = raw["kind"], raw["range"], raw["secondary"]
    slots, note = raw["evidenceSlots"], raw["note"]
    if type(raw["version"]) is not int or raw["version"] != 1 or kind not in KINDS:
        return fallback
    allowed_ranges = {"zipper": ("graded",), "unknown": ("graded",), "belt": ("binary",),
                      "none": (None,), "button": ("graded", "binary"), "snap": ("graded", "binary")}
    if closure_range not in allowed_ranges[kind]:
        return fallback
    if secondary is not None and (secondary not in HARDWARE or secondary == kind):
        return fallback
    if kind in ("none", "unknown") and secondary is not None:
        return fallback
    if not isinstance(slots, list) or any(slot not in SLOTS for slot in slots):
        return fallback
    if kind == "unknown" and slots:
        return fallback
    if note is not None and (not isinstance(note, str) or len(note) > 80):
        return fallback
    return {"version": 1, "kind": kind, "range": closure_range, "secondary": secondary,
            "evidenceSlots": list(dict.fromkeys(slots)), "note": _sanitize(note) or None}


def effective_state(requested, front_closure) -> str:
    state = normalize_state(requested)
    if front_closure.get("kind") == "none":
        return "open"
    if front_closure.get("range") == "binary":
        return "closed" if state in ("closed", "openToChest", "partial") else "open"
    return state
