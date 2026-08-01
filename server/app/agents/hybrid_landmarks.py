"""hybrid composite 용 의류 기하 추출 — vision LLM 은 **좌표만** 말하고 판정은 코드가 한다.

vision 이 돌려주는 것은 정규화(0~1) 좌표와 개수뿐이다. 비율·정합·적격성 판정은 전부
deterministic 코드(`validate_geometry`, `panel_map`)가 수행한다 — LLM 숫자 하나로 gate 를
통과시키지 않는다는 규율의 연장. 이미지는 bytes(InlineImage)로 전달, URL/바이트를 이벤트에
남기지 않는다.
"""

from .gemini_image import InlineImage
from .vision_llm import analyze_with_fallback

GEOMETRY_SCHEMA = {
    "type": "object",
    "properties": {
        "garment_visible": {"type": "boolean"},
        "shoulder_l": {"type": "array", "items": {"type": "number"}},
        "shoulder_r": {"type": "array", "items": {"type": "number"}},
        "hem_l": {"type": "array", "items": {"type": "number"}},
        "hem_r": {"type": "array", "items": {"type": "number"}},
        "sleeve_l_end": {"type": "array", "items": {"type": "number"}},
        "sleeve_r_end": {"type": "array", "items": {"type": "number"}},
        "collar_box": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}},
        "placket_box": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}},
        "has_collar": {"type": "boolean"},
        "has_placket": {"type": "boolean"},
        "has_cuffs": {"type": "boolean"},
        "visible_button_count": {"type": "integer"},
        "confidence": {"type": "number"},
    },
    "required": ["garment_visible", "shoulder_l", "shoulder_r", "hem_l", "hem_r",
                 "has_collar", "has_placket", "has_cuffs", "visible_button_count",
                 "confidence"],
    "additionalProperties": False,
}

PROMPT = """You are a garment geometry annotator. Look at the single attached image of a shirt
(either a flat product photo or a mannequin wearing it) and return NORMALIZED coordinates
(x, y in 0..1 relative to image width/height) for these garment landmarks:

- shoulder_l / shoulder_r: the left/right shoulder seam points of the shirt (left = smaller x).
- hem_l / hem_r: the bottom hem corners of the shirt body.
- sleeve_l_end / sleeve_r_end: the far end (cuff) of each sleeve, if a sleeve is visible.
- collar_box: 4 corner points [TL, TR, BR, BL] tightly around the collar, if present.
- placket_box: 4 corner points [TL, TR, BR, BL] around the front button placket, if present.
- has_collar / has_placket / has_cuffs: whether each construction element is visible.
- visible_button_count: how many buttons you can actually count on the front placket.
- confidence: 0..1 — how confident you are in these coordinates overall.

Only annotate the MAIN shirt. Points must be inside the image (0..1). If a landmark is not
visible, omit that optional field. Set garment_visible=false if there is no shirt."""

MIN_GEOMETRY_CONFIDENCE = 0.55


async def extract_geometry(settings, image: InlineImage) -> dict:
    """vision 호출 1회 — 실패는 VisionError 로 전파(호출측이 typed 실패로 매핑)."""
    raw, _provider = await analyze_with_fallback(
        settings, PROMPT, [image], GEOMETRY_SCHEMA)
    return raw


def validate_geometry(raw: dict) -> tuple[dict | None, dict | None, str | None]:
    """vision 원시 출력 → (landmarks, inventory, 실패사유). 판정은 전부 여기(순수)서.

    → landmarks: panel_map 이 쓰는 정규화 점들. inventory: construction 대조용.
    실패사유가 None 이 아니면 나머지는 None.
    """
    if not isinstance(raw, dict) or not raw.get("garment_visible"):
        return None, None, "의류 미검출"
    conf = raw.get("confidence")
    if not isinstance(conf, (int, float)) or conf < MIN_GEOMETRY_CONFIDENCE:
        return None, None, f"기하 신뢰도 미달 ({conf})"

    def pt(name):
        v = raw.get(name)
        if (isinstance(v, list) and len(v) == 2
                and all(isinstance(x, (int, float)) and 0.0 <= x <= 1.0 for x in v)):
            return [float(v[0]), float(v[1])]
        return None

    lm = {}
    for name in ("shoulder_l", "shoulder_r", "hem_l", "hem_r",
                 "sleeve_l_end", "sleeve_r_end"):
        p = pt(name)
        if p:
            lm[name] = p
    for req in ("shoulder_l", "shoulder_r", "hem_l", "hem_r"):
        if req not in lm:
            return None, None, f"필수 landmark 누락: {req}"
    if not (lm["shoulder_l"][0] < lm["shoulder_r"][0]
            and lm["hem_l"][0] < lm["hem_r"][0]):
        return None, None, "좌우 반전/모순 landmark"
    if not (lm["shoulder_l"][1] < lm["hem_l"][1] and lm["shoulder_r"][1] < lm["hem_r"][1]):
        return None, None, "어깨가 밑단보다 아래 — 기하 모순"

    def box(name):
        v = raw.get(name)
        if (isinstance(v, list) and len(v) == 4
                and all(isinstance(p, list) and len(p) == 2
                        and all(isinstance(x, (int, float)) and 0.0 <= x <= 1.0 for x in p)
                        for p in v)):
            return [[float(p[0]), float(p[1])] for p in v]
        return None

    boxes = {}
    for name in ("collar_box", "placket_box"):
        b = box(name)
        if b:
            boxes[name] = b

    shoulder_w = lm["shoulder_r"][0] - lm["shoulder_l"][0]
    hem_w = lm["hem_r"][0] - lm["hem_l"][0]
    torso_h = ((lm["hem_l"][1] + lm["hem_r"][1]) - (lm["shoulder_l"][1] + lm["shoulder_r"][1])) / 2
    if shoulder_w <= 0.02 or torso_h <= 0.02:
        return None, None, "torso 치수 비현실"
    sleeve_len_ratio = None
    if "sleeve_l_end" in lm or "sleeve_r_end" in lm:
        import math
        lens = []
        for side in ("l", "r"):
            end = lm.get(f"sleeve_{side}_end")
            if end:
                sh = lm[f"shoulder_{side}"]
                lens.append(math.hypot(end[0] - sh[0], end[1] - sh[1]))
        if lens:
            sleeve_len_ratio = sum(lens) / len(lens) / torso_h

    inventory = {
        "collar": bool(raw.get("has_collar")),
        "placket": bool(raw.get("has_placket")),
        "cuffs": bool(raw.get("has_cuffs")),
        "visible_buttons": int(raw.get("visible_button_count") or 0),
        "torso_aspect": round(torso_h / ((shoulder_w + hem_w) / 2), 3),
    }
    if sleeve_len_ratio is not None:
        inventory["sleeve_len_ratio"] = round(sleeve_len_ratio, 3)
    return lm, {**inventory, "component_boxes": boxes}, None
