"""AG-06 cut-generator — 컷 생성 (스타일링·호리존·제품·거울샷). ai_agent_modules §3 AG-06.

콘티 개편(ADR-0004)의 컷 계약을 이 모듈이 서버에서 강제한다 — 병렬 백엔드 머지(94cdd50)에서
탈락했던 구(舊) agents/cut.py 의 계약을 이식(2026-07-07). 프롬프트 문장은 전부
server/prompts/cut_generate_v1.txt 의 [[섹션]]에 있다 — 코드에 규칙 문장을 하드코딩하지
않는다(프롬프트 외부화 원칙). 코드는 섹션 선택과 값 치환만 한다.

레퍼런스 계약(ADR-0004): 옷 레퍼런스(정확성 최우선) > 컷 구조(노브) > 무드 레퍼런스(조명·색감만).
배관(생성 호출·R2·재시도)은 워커가 공유하고, 이 모듈은 계약 정규화 + 프롬프트 조립 + 1콜만 담당.
"""

import os
import re

from ..config import Settings
from .gemini_image import GeminiImageClient, InlineImage
from .model_routing import resolve_model
from .fit_axes import build_fit_profile_block
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
    spec = {
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
        # 레퍼런스 범위 (콘티 refScope, 2026-07 섹션 개편) — 'pose'면 예시에서 포즈·구도만 따르고
        # 배경은 프롬프트 자체 배경 지시를 따른다. 미지·구버전 값은 'all'로 정규화.
        "refScope": (raw.get("refScope") or raw.get("ref_scope")) if (raw.get("refScope") or raw.get("ref_scope")) in ("all", "pose") else "all",
    }
    # 같은 장소 세트 안의 예시는 '포즈 예시' 강등이 계약(2026-07) — 배경은 세트 연속성([[SPACE]])이
    # 담당하므로, refScope 없는 레거시 저장분·우회 클라이언트도 서버에서 'pose'로 강제한다.
    if spec["spaceGroupId"] and spec["exampleId"]:
        spec["refScope"] = "pose"
    return spec


def _is_bottom(clothing_type) -> bool:
    return str(clothing_type).lower() in ("bottom", "하의")


def _face_fits(spec: dict, is_bottom: bool) -> bool:
    """정규화된 스펙 기준 — 이 컷에 라이선스 얼굴이 **실제로 담기는가**(FM-31).

    첨부(워커)와 프롬프트 지시(render_cut_prompt)가 같은 답을 쓰도록 규칙을 여기 하나만 둔다.
    갈리면 얼굴을 첨부해놓고 가리라고 지시하거나(라이선스료 낭비), 반대로 첨부 없이
    "MODEL FACE 를 보라"고 지시해 모델이 얼굴을 지어낸다.

    제외 대상:
      · faceExposure=None — product 컷(사람·신체 노출 자체가 금지, [[CUT:product]])
      · faceExposure='hide' — 셀러가 명시적으로 비식별을 골랐거나 거울샷 기본값(폰이 얼굴을 가림)
      · direction='back' — 뒷모습이라 얼굴이 프레임 밖
      · 머리가 프레임에 없는 샷 — knee/medium 의 하의 변형, close_*(가슴·허벅지 클로즈업)
    """
    if spec["faceExposure"] not in ("same", "show"):
        return False
    if spec["direction"] == "back":
        return False
    shot = spec["shot"]
    return shot == "full" or (shot in ("knee", "medium") and not is_bottom)


def wants_face(cut_spec: dict, clothing_type: str | None = None) -> bool:
    """워커용 공개 판정 — 이 블록에 라이선스 얼굴을 첨부할지(첨부 전 호출).

    미상 cutType 은 **False**(예외 아님). 여기서 ValueError 를 던지면 워커의 준비 루프가
    통째로 죽어 잡 전체가 실패한다 — 현행 계약은 '미상 컷 = 그 컷만 빈 슬롯'이고,
    스펙 위반 판정은 지금처럼 generate() 경로가 담당한다.
    """
    try:
        spec = normalize_spec(cut_spec)
    except ValueError:
        return False
    return _face_fits(spec, _is_bottom(clothing_type))


def load_cut_template() -> str:
    with open(_DEFAULT_PROMPT, encoding="utf-8") as f:
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
    clothing_type: str, image_manifest: str, has_face: bool = False,
) -> str:
    """섹션 선택 + ${토큰} 치환 + PRODUCT CONTEXT(ground truth) 자동 주입.

    has_face=True(라이선스 얼굴 첨부)면 [[FACE_REF]] 정체성 지시가 켜지고 얼굴 지시가
    [[FACE:licensed]] 로 오버라이드된다 — 기본 'same'/거울샷 'hide' 를 그대로 두면
    셀러가 라이선스료를 내고 "얼굴을 가려라"를 지시받는 자기모순이 된다.
    """
    sec = _sections(template)
    cut, shot = spec["cutType"], spec["shot"]
    is_bottom = _is_bottom(clothing_type)
    # 첨부 여부(has_face)와 별개로 이 컷이 얼굴을 담는 컷인지 다시 판정 — 첨부 판정과 동일 규칙.
    use_face = has_face and _face_fits(spec, is_bottom)

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
    if use_face:
        face_line = need("FACE:licensed")
    if spec["pose"] == "auto" or cut in ("product", "mirror"):
        pose_line = need("POSE:auto") if cut != "product" else ""
    else:
        pose_line = need("POSE:named").replace("${poseName}", _sanitize(spec["pose"]))
    # 생성예시 선택 반영 v0 — 예시 자산·꼬리표 시딩 전 과도기: id 해시로 구도 뉘앙스를
    # 결정적으로 고정(같은 예시 = 같은 뉘앙스). 실제 꼬리표 메타데이터가 오면 이 매핑을 대체한다(ADR-0004).
    # band 규칙: 뉘앙스는 정면 대역(front·거울샷)에서만 — 사이드/뒷면이면 예시는 분위기만(T1)이라
    # 정면 계열 구도 문구가 방향 지시와 충돌하지 않게 미적용.
    example_line = ""
    # 포즈를 직접 지정했고 예시가 '포즈만' 범위면 예시는 효력 상실 — 지시 충돌(POSE:named vs 예시 구도) 방지
    pose_overrides_example = spec["pose"] != "auto" and spec["refScope"] == "pose"
    if spec.get("exampleId") and cut != "product" and spec.get("direction") in (None, "front") and not pose_overrides_example:
        idx = sum(ord(ch) for ch in spec["exampleId"]) % 3
        example_line = need(f"EXNUANCE:{idx}")
        # refScope='pose' — 예시의 배경·장소를 옮기지 않도록 범위 가드를 덧붙인다 (콘티 '포즈만')
        if spec.get("refScope") == "pose":
            example_line = example_line + "\n" + need("REFSCOPE:pose")
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
        # 얼굴 미첨부면 빈 문자열 — 모든 경로에서 반드시 치환한다(미치환 시 아래 leftover
        # 가드가 ValueError → 워커가 전 컷을 빈 슬롯으로 삼켜 조용히 죽는다).
        .replace("${faceRefLine}", need("FACE_REF") if use_face else "")
        .replace("${imageManifest}", image_manifest)  # 멀티라인 — 마지막에 치환
    )
    text = re.sub(r"\n{3,}", "\n\n", text)  # 빈 라인 정리 (생략된 줄 자리)
    leftover = re.findall(r"\$\{[a-zA-Z_]+\}", text)
    if leftover:
        raise ValueError(f"프롬프트 템플릿에 해결되지 않은 토큰: {sorted(set(leftover))}")
    stray = re.findall(r"\[\[[A-Za-z0-9_:]+\]\]", text)  # 섹션 마커가 본문에 남으면 모델에 그대로 전달됨
    if stray:
        raise ValueError(f"프롬프트에 남은 섹션 마커: {sorted(set(stray))}")
    # 확정 fitProfile(마네킹 단계 산출물)을 텍스트 제약으로도 이중 전달 — 마네킹 참조 이미지와
    # 원본 상품 사진의 인상이 충돌할 때 순종률을 확보한다(컷 파이프라인 계약). 렌더는 카탈로그
    # 고정 문구만(fit_axes — 셀러 입력 미보간). 프로필이 있으면 레거시 '- Fit:' 줄은 뺀다(마네킹 동일).
    fit_profile = analysis.get("fitProfile") if isinstance(analysis, dict) else None
    if not isinstance(fit_profile, dict):
        fit_profile = None
    # 매칭 하의가 화면에 없으면(마네킹 참조도 MATCH 첨부도 없음) matchCut 지시 제거 —
    # 없는 옷의 핏을 지시하면 모델이 하의를 지어내는 원인이 된다(마네킹 워커와 동일 가드).
    if fit_profile and "matchCut" in fit_profile \
            and _MANNEQUIN_LABEL not in image_manifest and _MATCH_LABEL not in image_manifest:
        fit_profile = {k: v for k, v in fit_profile.items() if k != "matchCut"}
    fit_block = build_fit_profile_block(fit_profile)
    block = _product_block(product, analysis or {}, include_legacy_fit=fit_profile is None)
    return "\n\n".join(part for part in (text, fit_block, block) if part)


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
# 마네킹/매칭 첨부 라벨 — render_cut_prompt 의 matchCut 가드가 매니페스트에서 이 문구로
# "하의가 화면에 있는가"를 판별하므로 상수로 공유(문구 드리프트 방지).
_MANNEQUIN_LABEL = "PRODUCT — the garment worn on a mannequin (verified colors, fit and length — follow this)"
_MATCH_LABEL = "MATCH — a coordinating garment to style together in the same outfit"
# FaceMarket 라이선스 얼굴 첨부 라벨(FM-31). 위 두 라벨의 부분문자열이 되면 matchCut 가드가
# 오발해 없는 하의를 지시하므로 'mannequin'·_MATCH_LABEL 문구를 섞지 않는다.
_FACE_LABEL = ("MODEL FACE — the licensed model's face reference: reproduce THIS person's "
               "facial identity (never copy their clothing, background or framing)")


def build_manifest(prod_assets: list[dict], *, has_mannequin: bool, has_match: bool,
                   mood_count: int, has_face: bool = False) -> str:
    """images=[mannequin?, *prod(slot순), match?, face?, *mood]와 동일 순서의 역할 목록.

    얼굴은 옷 근거(마네킹·상품·매칭) **뒤**에 온다 — 옷이 최우선 근거라는 계약(ADR-0004)을
    첨부 순서로도 유지한다. has_face 기본 False = 얼굴 없는 기존 호출자 무변경.
    """
    lines: list[str] = []
    i = 1
    if has_mannequin:
        lines.append(f"{i}. {_MANNEQUIN_LABEL}")
        i += 1
    for a in prod_assets:
        lines.append(f"{i}. {_SLOT_LABEL.get(a.get('slot'), 'PRODUCT — view of the garment')}")
        i += 1
    if has_match:
        lines.append(f"{i}. {_MATCH_LABEL}")
        i += 1
    if has_face:
        lines.append(f"{i}. {_FACE_LABEL}")
        i += 1
    for _ in range(mood_count):
        lines.append(f"{i}. MOOD — reference for lighting/color/ambience ONLY (never copy its garment, person or framing)")
        i += 1
    return "\n".join(lines) or "(the seller's product photos — treat as ground truth)"


def build_prompt(
    cut_spec: dict, product: dict, *,
    analysis: dict | None = None, manifest: str | None = None, has_face: bool = False,
) -> str:
    """스펙 정규화(ValueError=unknown_cut_type) + 템플릿 렌더. manifest 미지정 시
    첨부가 '해당 색상 상품 슬롯 이미지뿐'(+ has_face 면 얼굴)이라고 가정하고 동일 순서 목록을 만든다."""
    spec = normalize_spec(cut_spec)
    clothing_type = product.get("clothing_type") or product.get("clothingType") or "top"
    if manifest is None:
        prod_assets = [{"slot": slot} for slot, _id in color_images(product, spec["colorId"])]
        manifest = build_manifest(prod_assets, has_mannequin=False, has_match=False,
                                  mood_count=0, has_face=has_face and _face_fits(spec, _is_bottom(clothing_type)))
    return render_cut_prompt(load_cut_template(), spec, product, analysis or {}, clothing_type,
                             manifest, has_face)


async def generate(
    settings: Settings,
    gemini: GeminiImageClient,
    cut_spec: dict,
    product: dict,
    images: list[InlineImage],
    *,
    analysis: dict | None = None,
    manifest: str | None = None,
    has_face: bool = False,
) -> tuple[bytes, str]:
    """컷 1개 생성. 실패 시 GeminiError 전파(호출자가 빈 슬롯 등으로 처리).
    스펙 위반(unknown cutType)은 ValueError — 조용한 styling 폴백을 하지 않는다
    (거울샷 등 신규 컷이 엉뚱한 컷으로 대체 렌더되는 회귀 방지).

    has_face=True 는 '호출자가 images 에 라이선스 얼굴을 매니페스트와 같은 자리
    (옷 근거 뒤·무드 앞)로 넣었다'는 뜻이다 — 첨부와 어긋나면 라벨이 밀린다."""
    model = resolve_model(settings, "image_high")
    prompt = build_prompt(cut_spec, product, analysis=analysis, manifest=manifest, has_face=has_face)
    res = await gemini.generate_content_image(
        model, prompt, images, settings.mannequin_image_size,
        aspect_ratio=settings.mannequin_aspect_ratio,
    )
    return res.image, res.mime
