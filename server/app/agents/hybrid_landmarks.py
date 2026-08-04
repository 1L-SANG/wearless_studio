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


AGREEMENT_MAX_DELTA = 0.06  # 두 호출의 같은 landmark 가 이보다 벌어지면 불일치(정규화 좌표)
SOURCE_AGREEMENT_SOFT_MAX_DELTA = 0.14


def merge_geometry_pair(
    a: dict,
    b: dict,
    *,
    allow_source_jitter: bool = False,
) -> tuple[dict | None, str | None]:
    """vision 이중 호출 합의 — 좌표는 중앙값(평균), 불일치는 typed 실패 사유로.

    zero-cost 평가 실측(2026-08-01): 같은 이미지에 대한 호출 간 landmark 지터가 결과를
    run 마다 다른 typed 실패로 굴렸다(anchor OK → mask 0.34 → aspect 0.25). 결정론
    파이프라인의 앞단이 흔들리면 전체가 흔들린다 — 두 호출이 서로를 검증하게 한다.
    """
    if not (isinstance(a, dict) and isinstance(b, dict)):
        return None, "기하 응답 형식 오류"
    if not (a.get("garment_visible") and b.get("garment_visible")):
        return None, "의류 미검출(이중 호출 불일치)"
    merged = dict(a)
    agreement_warnings = {}
    for key in ("shoulder_l", "shoulder_r", "hem_l", "hem_r",
                "sleeve_l_end", "sleeve_r_end"):
        va, vb = a.get(key), b.get(key)
        ok_a = isinstance(va, list) and len(va) == 2
        ok_b = isinstance(vb, list) and len(vb) == 2
        if ok_a and ok_b:
            delta = max(abs(va[0] - vb[0]), abs(va[1] - vb[1]))
            if delta > AGREEMENT_MAX_DELTA:
                if not allow_source_jitter or delta > SOURCE_AGREEMENT_SOFT_MAX_DELTA:
                    return None, f"landmark 불일치: {key}"
                agreement_warnings[key] = round(float(delta), 4)
            merged[key] = [(va[0] + vb[0]) / 2, (va[1] + vb[1]) / 2]
        elif ok_a or ok_b:
            merged[key] = va if ok_a else vb
        else:
            merged.pop(key, None)
    for key in ("collar_box", "placket_box"):
        if not isinstance(merged.get(key), list) and isinstance(b.get(key), list):
            merged[key] = b[key]
    counts = [int(x.get("visible_button_count") or 0) for x in (a, b)]
    merged["visible_button_count"] = int(round(sum(counts) / 2))
    merged["confidence"] = min(float(a.get("confidence") or 0),
                               float(b.get("confidence") or 0))
    for key in ("has_collar", "has_placket", "has_cuffs"):
        merged[key] = bool(a.get(key)) or bool(b.get(key))
    if agreement_warnings:
        merged["_agreement_warnings"] = agreement_warnings
    return merged, None


def validate_geometry(
    raw: dict, *, aspect_hw: float = 1.0,
) -> tuple[dict | None, dict | None, str | None]:
    """vision 원시 출력 → (landmarks, inventory, 실패사유). 판정은 전부 여기(순수)서.

    `aspect_hw` = 이미지 H/W. inventory 의 비율(torso_aspect·sleeve_len_ratio)은 반드시
    **물리 픽셀 비**로 계산해야 한다 — 정규화 좌표 그대로 나누면 서로 다른 종횡비의
    사진(source 3:4 vs carrier 2:3)끼리 비교가 무의미해진다(실측: 같은 셔츠가 상대 오차
    0.29 로 오판). x 차분에 1/aspect... 대신 y 를 픽셀 비율로 환산: Δy_phys = Δy·H/W·Δx 기준.

    → landmarks: panel_map 이 쓰는 정규화 점들(불변). inventory: construction 대조용(물리 비).
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

    # 물리 비 환산: x 는 그대로(폭 기준), y 차분에 aspect_hw(H/W)를 곱해 같은 단위로.
    shoulder_w = lm["shoulder_r"][0] - lm["shoulder_l"][0]
    hem_w = lm["hem_r"][0] - lm["hem_l"][0]
    torso_h = (((lm["hem_l"][1] + lm["hem_r"][1])
                - (lm["shoulder_l"][1] + lm["shoulder_r"][1])) / 2) * aspect_hw
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
                lens.append(math.hypot(end[0] - sh[0], (end[1] - sh[1]) * aspect_hw))
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
