"""컷 생성 잡 헬퍼 (ADR-0004) — 콘티 블록 스펙 정규화 + 프롬프트 렌더.

레퍼런스 계약(ADR-0004): 옷 레퍼런스(정확성 최우선) > 컷 구조(노브) > 무드 레퍼런스(조명·색감만).
프롬프트 문장은 전부 server/prompts/cut_generate_v1.txt 의 [[섹션]]에 있다 — 코드에 규칙 문장을
하드코딩하지 않는다(프롬프트 외부화 원칙, prompts.py와 동일). 코드는 섹션 선택과 값 치환만 한다.
"""

import os
import re

from ..config import Settings
from .prompts import _product_block, _sanitize

_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))  # server/
_DEFAULT_PROMPT = os.path.join(_SERVER_DIR, "prompts", "cut_generate_v1.txt")

CUT_TYPES = ("styling", "horizon", "product", "mirror")
_PERSON_SHOTS = ("full", "knee", "medium", "close")
_PRODUCT_SHOTS = ("ghost", "hanger", "flatlay")
_DIRECTIONS = ("front", "side", "back")
_CUT_LABELS = {  # ${cutLabel} — 프롬프트 첫 줄의 짧은 명사구 (값이지 규칙 문장이 아님)
    "styling": "lifestyle styling cut",
    "horizon": "clean studio horizon cut",
    "product": "product-only cut",
    "mirror": "casual mirror-selfie cut",
}


def normalize_spec(raw: dict) -> dict:
    """프론트를 믿지 않는다 — 컷 계약(ADR-0004)을 서버에서도 강제.
    UI(onTab 정규화)와 같은 규칙: mirror=방향 없음·샷 full/knee·얼굴 기본 hide,
    product=방향 front/back·샷 ghost/hanger/flatlay, 사람컷=front/side/back·full~close."""
    cut = raw.get("cutType") or raw.get("cut_type")
    if cut not in CUT_TYPES:
        raise ValueError("unknown_cut_type")
    direction = raw.get("direction")
    shot = raw.get("shot")
    face = raw.get("faceExposure") or raw.get("face_exposure")
    pose = raw.get("pose") or "auto"
    if cut == "mirror":
        direction = None
        shot = shot if shot in ("full", "knee") else "full"
        face = "show" if face == "show" else "hide"
        pose = "auto"  # 거울 셀피 구도 자동 (ADR-0004)
    elif cut == "product":
        direction = direction if direction in ("front", "back") else "front"
        shot = shot if shot in _PRODUCT_SHOTS else "ghost"
        face = None
    else:  # styling · horizon
        direction = direction if direction in _DIRECTIONS else "front"
        shot = shot if shot in _PERSON_SHOTS else "full"
        face = face if face in ("same", "show", "hide") else "same"
    variation = raw.get("spaceVariation") or raw.get("space_variation")
    return {
        "cutType": cut,
        "direction": direction,
        "shot": shot,
        "colorId": _sanitize(raw.get("colorId") or raw.get("color_id") or "") or None,
        "pose": _sanitize(pose)[:40] or "auto",
        "faceExposure": face,
        "matchIds": [str(m) for m in (raw.get("matchIds") or raw.get("match_ids") or [])][:2],
        "refAssetIds": [str(a) for a in (raw.get("refAssetIds") or raw.get("ref_asset_ids") or [])][:3],
        "exampleId": _sanitize(raw.get("exampleId") or raw.get("example_id") or "") or None,
        "spaceGroupId": _sanitize(raw.get("spaceGroupId") or raw.get("space_group_id") or "") or None,
        "spaceVariation": variation if variation in ("subtle", "varied") else "subtle",
    }


def load_cut_template(settings: Settings) -> str:
    path = settings.cut_prompt_file or _DEFAULT_PROMPT
    if not os.path.isabs(path):  # 상대경로는 server/ 기준 (CWD 의존 제거)
        path = os.path.join(_SERVER_DIR, path)
    with open(path, encoding="utf-8") as f:
        return f.read()


_SECTION_RE = re.compile(r"^\[\[([A-Z_]+(?::[a-z0-9_]+)?)\]\]", re.M)


def _sections(template: str) -> dict[str, str]:
    """[[NAME]] / [[NAME:key]] 섹션 파싱 — 다음 섹션 헤더 전까지가 본문."""
    out: dict[str, str] = {}
    matches = list(_SECTION_RE.finditer(template))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(template)
        out[m.group(1)] = template[m.end():end].strip()
    return out


def render_cut_prompt(
    template: str, spec: dict, product: dict, analysis: dict,
    clothing_type: str, image_manifest: str,
) -> str:
    """섹션 선택 + ${토큰} 치환 + PRODUCT CONTEXT(ground truth) 자동 주입."""
    sec = _sections(template)
    cut, shot = spec["cutType"], spec["shot"]
    is_bottom = str(clothing_type).lower() in ("bottom", "하의")

    def need(key: str) -> str:
        if key not in sec:
            raise ValueError(f"프롬프트 템플릿에 섹션이 없습니다: [[{key}]]")
        return sec[key]

    shot_key = shot if shot in _PRODUCT_SHOTS or shot == "full" else f"{shot}_{'bottom' if is_bottom else 'top'}"
    if cut == "mirror":
        face_line = need("FACE:hide_mirror") if spec["faceExposure"] != "show" else need("FACE:show")
        direction_line = ""
    elif cut == "product":
        face_line = ""
        direction_line = need(f"DIR:{spec['direction']}_product")
    else:
        face_line = need(f"FACE:{spec['faceExposure']}")
        direction_line = need(f"DIR:{spec['direction']}")
    if spec["pose"] == "auto" or cut in ("product", "mirror"):
        pose_line = need("POSE:auto") if cut != "product" else ""
    else:
        pose_line = need("POSE:named").replace("${poseName}", _sanitize(spec["pose"]))
    # 생성예시 선택 반영 v0 — 예시 자산·꼬리표 시딩 전 과도기: id 해시로 구도 뉘앙스를
    # 결정적으로 고정(같은 예시 = 같은 뉘앙스). 실제 꼬리표 메타데이터가 오면 이 매핑을 대체한다(ADR-0004).
    example_line = ""
    if spec.get("exampleId") and cut != "product":
        idx = sum(ord(ch) for ch in spec["exampleId"]) % 3
        example_line = need(f"EXNUANCE:{idx}")
    space_line = ""
    if spec.get("spaceGroupId"):
        space_line = need("SPACE").replace("${spaceVariation}", spec["spaceVariation"])

    text = (
        need("BASE")
        .replace("${cutLabel}", _CUT_LABELS[cut])
        .replace("${cutSection}", need(f"CUT:{cut}"))
        .replace("${shotLine}", need(f"SHOT:{shot_key}"))
        .replace("${directionLine}", direction_line)
        .replace("${faceLine}", face_line)
        .replace("${poseLine}", pose_line)
        .replace("${exampleLine}", example_line)
        .replace("${spaceLine}", space_line)
        .replace("${imageManifest}", image_manifest)  # 멀티라인 — 마지막에 치환
    )
    text = re.sub(r"\n{3,}", "\n\n", text)  # 빈 라인 정리 (생략된 줄 자리)
    leftover = re.findall(r"\$\{[a-zA-Z_]+\}", text)
    if leftover:
        raise ValueError(f"프롬프트 템플릿에 해결되지 않은 토큰: {sorted(set(leftover))}")
    stray = re.findall(r"\[\[[A-Za-z0-9_:]+\]\]", text)  # 섹션 마커가 본문에 남으면 모델에 그대로 전달됨
    if stray:
        raise ValueError(f"프롬프트에 남은 섹션 마커: {sorted(set(stray))}")
    block = _product_block(product, analysis)
    return f"{text}\n\n{block}" if block else text


def color_images(product: dict, color_id: str | None) -> list[tuple[str, str]]:
    """지정 색상(없으면 기준 색상) 이미지의 (slot, asset_id) 목록 — mannequin.base_color_images와 동형."""
    from .mannequin import _SLOT_ORDER  # 슬롯 정렬 기준 공유
    colors = product.get("colors") or []
    chosen = next((c for c in colors if color_id and c.get("id") == color_id), None)
    if chosen is None or not (chosen.get("images") or []):
        chosen = next((c for c in colors if c.get("isBase")), colors[0] if colors else None)
    if not chosen:
        return []
    images = sorted((chosen.get("images") or []), key=lambda im: _SLOT_ORDER.get(im.get("slot") or "", 99))
    return [(im.get("slot") or "Front", im["id"]) for im in images if im.get("id")]


# 첨부 이미지 역할 라벨 — 전부 고정 문구(셀러 데이터 미포함, 프롬프트 인젝션 방지)
_SLOT_LABEL = {
    "Front": "PRODUCT — front view of the garment",
    "Back": "PRODUCT — back view of the garment",
    "Detail": "PRODUCT — detail close-up of the garment (texture, stitching, print)",
    "Fit": "PRODUCT — fit reference, the garment worn on a person (true length & drape)",
}


def build_manifest(prod_assets: list[dict], *, has_mannequin: bool, has_match: bool, mood_count: int) -> str:
    """images=[mannequin?, *prod(slot순), match?, *mood]와 동일 순서의 역할 목록."""
    lines: list[str] = []
    i = 1
    if has_mannequin:
        lines.append(f"{i}. PRODUCT — the garment worn on a mannequin (verified colors, fit and length — follow this)")
        i += 1
    for a in prod_assets:
        lines.append(f"{i}. {_SLOT_LABEL.get(a.get('slot'), 'PRODUCT — view of the garment')}")
        i += 1
    if has_match:
        lines.append(f"{i}. MATCH — a coordinating garment to style together in the same outfit")
        i += 1
    for _ in range(mood_count):
        lines.append(f"{i}. MOOD — reference for lighting/color/ambience ONLY (never copy its garment, person or framing)")
        i += 1
    return "\n".join(lines)


def prompt_version(settings: Settings) -> str:
    return settings.cut_prompt_version
