"""AG-06 cut-generator — 컷 생성 (스타일링·호리존·제품·거울샷). ai_agent_modules §3 AG-06.

콘티 개편(ADR-0004)의 컷 계약을 이 모듈이 서버에서 강제한다 — 병렬 백엔드 머지(94cdd50)에서
탈락했던 구(舊) agents/cut.py 의 계약을 이식(2026-07-07). 프롬프트 문장은 전부
server/prompts/cut_generate_v1.txt 의 [[섹션]]에 있다 — 코드에 규칙 문장을 하드코딩하지
않는다(프롬프트 외부화 원칙). 코드는 섹션 선택과 값 치환만 한다.

레퍼런스 계약(ADR-0004): 옷 레퍼런스(정확성 최우선) > 컷 구조(노브) > 무드 레퍼런스(조명·색감만).
배관(생성 호출·R2·재시도)은 워커가 공유하고, 이 모듈은 계약 정규화 + 프롬프트 조립 + 1콜만 담당.
"""

import asyncio
import json
import logging
import os
import re
from functools import lru_cache
from urllib.parse import urlsplit

import httpx

from ..config import Settings
from .content_roles import canonicalize_storyboard_block
from .gemini_image import GeminiImageClient, InlineImage
from .model_routing import resolve_model
from .fit_axes import build_fit_profile_block
from .prompts import _product_block, _sanitize
from . import pose_crop

_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))  # server/
_DEFAULT_PROMPT = os.path.join(_SERVER_DIR, "prompts", "cut_generate_v1.txt")
_DEFAULT_EXAMPLE_ASSETS = os.path.join(_SERVER_DIR, "app", "data", "example_assets.json")
_DEFAULT_VIRTUAL_MODELS = os.path.join(_SERVER_DIR, "app", "data", "virtual_models.json")
_EXAMPLE_FETCH_TIMEOUT = 15.0

log = logging.getLogger("wearless.cut_generator")

CUT_TYPES = ("styling", "horizon", "product", "mirror")
_PERSON_SHOTS = ("full", "medium")
_PRODUCT_SHOTS = ("ghost", "detail")
_DIRECTIONS = ("front", "side", "back")
_WORN_CUTS = ("styling", "horizon", "mirror")
_OUTER_CLOSURE_STATES = ("open", "partial", "closed")
_CUT_LABELS = {  # ${cutLabel} — 프롬프트 첫 줄의 짧은 명사구 (값이지 규칙 문장이 아님)
    "styling": "lifestyle styling cut",
    "horizon": "clean studio horizon cut",
    "product": "product-only cut",
    "mirror": "casual mirror-selfie cut",
}
_SWATCH_META = {
    "white": ("화이트", "#ffffff"),
    "gray": ("그레이", "#9a9aa1"),
    "black": ("블랙", "#15141a"),
    "ivory": ("아이보리", "#f3eee1"),
    "beige": ("베이지", "#d8c4a3"),
    "brown": ("브라운", "#7a5230"),
    "red": ("레드", "#c0392b"),
    "yellow": ("옐로우", "#e7c75c"),
    "green": ("그린", "#3f7a4f"),
    "blue": ("블루", "#2a5db0"),
    "navy": ("네이비", "#1f2a44"),
    "pink": ("핑크", "#e3a7b8"),
}


def _is_outer(clothing_type: str | None) -> bool:
    return str(clothing_type or "").strip().lower() in ("outer", "아우터")


def _normalize_detail_color_transfer(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    target_name = _sanitize(value.get("targetName") or "")[:80]
    reference_name = _sanitize(value.get("referenceName") or "")[:80]
    raw_hex = str(value.get("targetHex") or "").strip()
    target_hex = raw_hex.lower() if re.fullmatch(r"#[0-9a-fA-F]{6}", raw_hex) else None
    if not target_name:
        return None
    return {
        "targetName": target_name,
        "targetHex": target_hex,
        "referenceName": reference_name or None,
    }


def normalize_spec(raw: dict, *, clothing_type: str | None = None) -> dict:
    """프론트를 믿지 않는다 — 컷 계약(ADR-0004)을 서버에서도 강제.
    UI 정규화와 같은 규칙: mirror=방향 없음·샷 full/medium·얼굴 기본 hide,
    product=방향 front/back·샷 ghost/detail, 착용컷=front/side/back·full/medium."""
    raw = canonicalize_storyboard_block(raw)
    cut = raw.get("cutType") or raw.get("cut_type")
    if cut not in CUT_TYPES:
        raise ValueError("unknown_cut_type")
    direction = raw.get("direction")
    shot = raw.get("shot")
    face = raw.get("faceExposure") or raw.get("face_exposure")
    pose = raw.get("pose") or "auto"
    if cut == "mirror":
        direction = None
        shot = shot if shot in _PERSON_SHOTS else "full"
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
    closure = raw.get("outerClosureState") or raw.get("outer_closure_state")
    raw_color_id = raw.get("colorId")
    if raw_color_id is None:
        raw_color_id = raw.get("color_id")
    if _is_outer(clothing_type) and cut in _WORN_CUTS:
        closure = closure if closure in _OUTER_CLOSURE_STATES else "open"
    else:
        closure = None
    spec = {
        "cutType": cut,
        "direction": direction,
        "shot": shot,
        "colorId": _sanitize(raw_color_id) or None,
        "pose": _sanitize(pose)[:40] or "auto",
        "faceExposure": face,
        "matchIds": [str(m) for m in (raw.get("matchIds") or raw.get("match_ids") or [])][:2],
        "refAssetIds": [str(a) for a in (raw.get("refAssetIds") or raw.get("ref_asset_ids") or [])][:3],
        "exampleId": _sanitize(raw.get("exampleId") or raw.get("example_id") or "") or None,
        "spaceGroupId": _sanitize(raw.get("spaceGroupId") or raw.get("space_group_id") or "") or None,
        "spaceVariation": variation if variation in ("subtle", "varied") else "subtle",
        "outerClosureState": closure,
        "modelId": _sanitize(raw.get("modelId") or raw.get("model_id") or "") or None,
        # 레퍼런스 범위 (ADR-0009) — 'pose'면 예시에서 자세만 따르고, 프레이밍은 현재
        # cutType/shot이 정한다. 배경은 프롬프트 자체 배경 지시를 따른다. 미지·구버전 값은 'all'로 정규화.
        "refScope": (raw.get("refScope") or raw.get("ref_scope")) if (raw.get("refScope") or raw.get("ref_scope")) in ("all", "pose", "bg") else "all",
        # 워커가 실제 첨부 자산을 고른 뒤 붙이는 런타임 전용 정보. 저장 계약에는 포함하지 않는다.
        "_detailColorTransfer": _normalize_detail_color_transfer(raw.get("_detailColorTransfer")),
    }
    # 제품컷은 '배경만/포즈만'이 성립하지 않는다(사람·포즈 없음) — 예시는 통째 참조만 허용.
    if cut == "product" and spec["refScope"] != "all":
        spec["refScope"] = "all"
    # 같은 장소 세트 안의 예시는 '포즈 예시' 강등이 계약(2026-07) — 배경은 세트 연속성([[SPACE]])이
    # 담당하므로, refScope 없는 레거시 저장분·우회 클라이언트도 서버에서 'pose'로 강제한다.
    # (배경만도 마찬가지 — 세트의 배경 기준과 충돌하므로 포즈로 강등)
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
      · 머리가 프레임에 없는 샷 — 하의의 medium 프레이밍
    """
    if spec["faceExposure"] not in ("same", "show"):
        return False
    if spec["direction"] == "back":
        return False
    shot = spec["shot"]
    return shot == "full" or (shot == "medium" and not is_bottom)


def wants_face(cut_spec: dict, clothing_type: str | None = None) -> bool:
    """워커용 공개 판정 — 이 블록에 라이선스 얼굴을 첨부할지(첨부 전 호출).

    미상 cutType 은 **False**(예외 아님). 여기서 ValueError 를 던지면 워커의 준비 루프가
    통째로 죽어 잡 전체가 실패한다 — 현행 계약은 '미상 컷 = 그 컷만 빈 슬롯'이고,
    스펙 위반 판정은 지금처럼 generate() 경로가 담당한다.
    """
    try:
        spec = normalize_spec(cut_spec, clothing_type=clothing_type)
    except ValueError:
        return False
    return _face_fits(spec, _is_bottom(clothing_type))


def load_cut_template() -> str:
    with open(_DEFAULT_PROMPT, encoding="utf-8") as f:
        return f.read()


@lru_cache(maxsize=1)
def load_example_asset_registry() -> tuple[str | None, dict[str, dict]]:
    """서버 소유 exampleId→자산/적용성 레지스트리. 프로세스당 1회만 읽는다.

    v2는 variant URL과 ``applicableClothingTypes`` 등 생성 조건을 함께 둔다.
    구 문자열 및 variant-only dict도 v2 entry로 정규화하되 적용성 메타가 없는
    레거시 항목은 기존처럼 허용한다.
    """
    with open(_DEFAULT_EXAMPLE_ASSETS, encoding="utf-8") as f:
        raw = json.load(f)
    meta = raw.get("_meta") if isinstance(raw, dict) else {}
    assets = raw.get("assets") if isinstance(raw, dict) else {}
    if not isinstance(meta, dict) or not isinstance(assets, dict):
        return None, {}
    base_url = meta.get("defaultBaseUrl")
    if not isinstance(base_url, str) or not base_url.strip():
        base_url = None
    clean: dict[str, dict] = {}
    for example_id, value in assets.items():
        # 값 형태 3가지: 문자열(all), 구 variant dict, v2 variant+메타 dict.
        # pose variant = 배경제거(누끼) 자산 — 스파이크(2026-07-12) 결과 원본 첨부는 소품·무드가
        # 새므로(의자·가방 전이), 포즈 전용은 누끼가 정답. 앵커 확정 시 사전 1회 생성해 등록한다.
        if isinstance(value, str) and value.strip():
            clean[str(example_id)] = {"all": value.strip()}
        elif isinstance(value, dict):
            entry: dict = {
                k: v.strip() for k, v in value.items()
                if k in ("all", "pose", "bg", "thumb") and isinstance(v, str) and v.strip()
            }
            if "applicableClothingTypes" in value:
                applicable = value.get("applicableClothingTypes")
                entry["applicableClothingTypes"] = (
                    [str(item) for item in applicable if isinstance(item, str) and item]
                    if isinstance(applicable, list) else []
                )
            for field in ("cutType", "shot"):
                if isinstance(value.get(field), str) and value[field]:
                    entry[field] = value[field]
            if "direction" in value and (
                value.get("direction") is None or value.get("direction") in _DIRECTIONS
            ):
                entry["direction"] = value.get("direction")
            # 제품 생성예시는 성별 공용이라 v2 레지스트리에 명시된 null도 메타데이터다.
            # 키 자체가 없는 구 레지스트리와 구분해 그대로 보존한다.
            if "gender" in value and (
                value.get("gender") is None
                or value.get("gender") in ("women", "men")
            ):
                entry["gender"] = value.get("gender")
            if any(variant in entry for variant in ("all", "pose", "bg", "thumb")):
                clean[str(example_id)] = entry
    return base_url, clean


def example_asset_status(
    example_id: str | None,
    clothing_type: str | None,
    scope: str = "all",
) -> str:
    """예시 첨부 가능 상태를 워커가 경고 사유와 함께 구분할 수 있게 반환한다.

    반환값은 ``available | unknown | not_applicable | variant_unpublished``.
    메타가 없는 구 레지스트리는 적용성 검증만 생략해 상위 호환을 유지한다.
    """
    if not example_id:
        return "unknown"
    _default_base, assets = load_example_asset_registry()
    entry = assets.get(str(example_id))
    if not entry:
        return "unknown"
    applicable = entry.get("applicableClothingTypes")
    if applicable is not None and str(clothing_type or "") not in applicable:
        return "not_applicable"
    variant = scope if scope in ("pose", "bg") else "all"
    if not entry.get(variant):
        return "variant_unpublished"
    return "available"


def pose_direction_compatible(example_id: str | None, spec: dict) -> bool:
    """pose 전용 자산과 현재 카드의 방향 계열이 같은지 API 호출 전에 판정한다.

    mirror 카드에는 mirror 예시만 허용한다. 그 외 착용컷은 관찰 메타 direction이
    카드 레시피 direction과 정확히 같아야 한다. 레거시 레지스트리처럼 메타가 없으면
    v2 포즈 계약을 증명할 수 없으므로 fail-closed한다.
    """
    if not example_id or spec.get("cutType") not in _WORN_CUTS:
        return False
    _default_base, assets = load_example_asset_registry()
    entry = assets.get(str(example_id)) or {}
    example_cut = entry.get("cutType")
    card_cut = spec.get("cutType")
    if card_cut == "mirror" or example_cut == "mirror":
        return card_cut == example_cut == "mirror"
    return (
        example_cut in ("styling", "horizon")
        and entry.get("direction") in _DIRECTIONS
        and entry.get("direction") == spec.get("direction")
    )


@lru_cache(maxsize=1)
def load_virtual_model_registry() -> dict[str, dict]:
    """서버 소유 modelId→R2 뷰 manifest. 프로세스당 1회만 읽는다."""
    with open(_DEFAULT_VIRTUAL_MODELS, encoding="utf-8") as f:
        raw = json.load(f)
    models = raw.get("models") if isinstance(raw, dict) else {}
    if not isinstance(models, dict):
        return {}
    return {str(model_id): model for model_id, model in models.items() if isinstance(model, dict)}


def resolve_effective_model_id(
    selected_model_id, *, fallback_model_id: str | None, virtual_ids,
) -> tuple[str | None, bool]:
    """상세페이지 착용컷에 붙일 '유효' 가상모델 id (인물 일관성 — AG-06). **순수 함수.**

    호출자는 identity source == 'VIRTUAL' 일 때만 쓴다(REAL/LEGACY 는 얼굴을 별도 경로로 붙이므로
    폴백하면 인물이 이중 첨부된다). VIRTUAL 인데 선택 id 가 가상 registry(mA/mB/mC) 밖이면
    (예: facemarket off 상태의 실존 UUID) `resolve_virtual_model_assets` 가 None 을 반환해
    참조가 0장이 되고 컷마다 인물이 랜덤이 된다. 그 경우 결정적 폴백으로 전 컷 동일 인물 보장.

    - 선택이 가상모델이면 그대로 (effective=선택, substituted=False).
    - 선택이 가상 밖(실존 UUID)이거나 미선택이면 폴백 (effective=fallback, substituted=선택있었나).
    - 폴백 id 가 비었거나 registry 밖이면 폴백 불가 → 기존 동작(effective=선택, substituted=False).
    반환: (effective_id, substituted). substituted=True 면 워커가 경고 로그를 남긴다.
    """
    if selected_model_id and selected_model_id in virtual_ids:
        return selected_model_id, False
    if not fallback_model_id or fallback_model_id not in virtual_ids:
        return selected_model_id, False
    return fallback_model_id, selected_model_id is not None


def real_identity_plan(cut_type, *, wants_face: bool) -> tuple[bool, bool]:
    """REAL 소스에서 이 컷의 (실존모델 그리드 첨부?, 검증-얼굴 배지?). **순수 함수.**

    인물 일관성(AG-06/A4): 실존 모델 그리드(face_front+grid_sedcard)는 **얼굴 노출 여부와
    무관하게** 모든 착용컷(styling/horizon/mirror)에 identity 앵커로 붙인다 — VIRTUAL 경로와
    동형. 안 그러면 얼굴을 가리는 컷(mirror 기본·back)이 `wants_face=False` 라 참조 0장이 되어
    그 컷만 인물이 랜덤이 된다(REAL 은 VIRTUAL 과 달리 mB 폴백도 못 탄다).

    검증-얼굴 배지(has_identity=face_cuts 계수·26.06 고지 근거)는 얼굴이 **실제로 노출되는**
    컷에만(wants_face) 준다 — 그리드가 붙어도 얼굴을 가린 컷은 '검증 얼굴 노출'이 아니다.

    반환: (attach_grid, has_identity). has_identity 는 호출자가 실제 그리드 2장 로드 성공
    (len==2)과 다시 AND 한다(로드 실패 시 배지·앵커 동반 소거, fail-open).
    """
    attach = cut_type in _WORN_CUTS
    return attach, (attach and wants_face)


def resolve_virtual_model_assets(spec: dict) -> tuple[dict[str, str], dict[str, str]] | None:
    """정규화된 사람컷 spec의 C방식 자산(face_front, grid_sedcard)을 계약 순서로 반환.

    product 컷·modelId 미지정은 정상적인 미첨부다. 알 수 없는 modelId나 불완전한 manifest는
    경고 후 미첨부로 폴백하며, R2 바이트 로드 실패는 각 워커가 같은 방식으로 처리한다.
    """
    if spec.get("cutType") not in _WORN_CUTS or not spec.get("modelId"):
        return None
    model_id = str(spec["modelId"])
    try:
        model = load_virtual_model_registry().get(model_id)
    except (OSError, json.JSONDecodeError) as e:
        log.warning("AG-06 virtual model manifest unavailable for %s; continuing without model references: %r",
                    model_id, e)
        return None
    if not model:
        log.warning("AG-06 unknown virtual model %s; continuing without model references", model_id)
        return None
    views = model.get("views")
    if not isinstance(views, dict):
        views = {}
    resolved: list[dict[str, str]] = []
    for view_name in ("face_front", "grid_sedcard"):
        view = views.get(view_name)
        key = view.get("key") if isinstance(view, dict) else None
        mime = view.get("mime") if isinstance(view, dict) else None
        if not isinstance(key, str) or not key.strip() \
                or not isinstance(mime, str) or not mime.startswith("image/"):
            log.warning(
                "AG-06 virtual model %s missing valid %s; continuing without model references",
                model_id, view_name)
            return None
        resolved.append({"key": key, "mime": mime, "bucket": "public"})
    return resolved[0], resolved[1]


def resolve_example_asset(
    example_id: str | None, base_url: str | None = None, *, allow_default_base: bool = True,
    scope: str = "all", clothing_type: str | None = None,
) -> str | None:
    """레지스트리 항목을 절대 http(s) URL로 해석. 미등록/잘못된 URL은 v0 폴백(None).
    pose/bg는 전용 자산이 있을 때만 해석한다. 일반 사진(all)을 대신 붙이면 범위 계약이
    깨지므로, 전용 자산이 없을 때는 미첨부(None)로 닫힌다:
      pose → 누끼(인물만, 배경 제거)   bg → 빈 무대 플레이트(인물 제거, 여백 확보)"""
    if not example_id:
        return None
    default_base, assets = load_example_asset_registry()
    entry = assets.get(str(example_id)) or {}
    if clothing_type is not None \
            and example_asset_status(example_id, clothing_type, scope) != "available":
        return None
    value = entry.get(scope) if scope in ("pose", "bg") else entry.get("all")
    if not value:
        return None
    if urlsplit(value).scheme in ("http", "https"):
        resolved = value
    else:
        base = (base_url or (default_base if allow_default_base else None) or "").rstrip("/")
        if not base:
            return None
        resolved = f"{base}/{value.lstrip('/')}"
    return resolved if urlsplit(resolved).scheme in ("http", "https") else None


async def load_example_image(
    settings: Settings, example_id: str | None, scope: str = "all",
    clothing_type: str | None = None,
) -> InlineImage | None:
    """등록 예시를 Gemini용 bytes로 로드. 실패는 오류가 아니라 기존 v0 경로로 폴백한다."""
    base_url = getattr(settings, "example_asset_base_url", None)
    # placehold.co 기본값은 레지스트리 구조를 검증하기 위한 dev dummy일 뿐, prod 외부 의존성이 아니다.
    # 레지스트리에 절대 URL을 넣은 실제 자산은 환경과 무관하게 그대로 허용한다.
    url = resolve_example_asset(
        example_id, base_url, scope=scope,
        clothing_type=clothing_type,
        allow_default_base=getattr(settings, "app_env", "dev") == "dev")
    if not url:
        return None
    last_err: Exception | None = None
    for fetch_try in range(3):  # 일시 네트워크 플레이크 흡수 — pose/bg는 이 자산 없인 범위 계약이 무의미
        try:
            async with httpx.AsyncClient(timeout=_EXAMPLE_FETCH_TIMEOUT, follow_redirects=True) as client:
                res = await client.get(url)
            res.raise_for_status()
            mime = (res.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
            if not mime.startswith("image/") or not res.content:
                raise ValueError("example asset response is not an image")
            return InlineImage(mime, res.content)
        except (httpx.HTTPError, ValueError) as e:
            last_err = e
            if fetch_try < 2:
                await asyncio.sleep(0.5 * (fetch_try + 1))
    log.warning("AG-06 example asset unavailable for %s after retries: %r", example_id, last_err)
    return None


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
    if cut == "product" and shot == "detail" and _SLOT_LABEL["Detail"] not in image_manifest:
        # 메타데이터에 Detail 슬롯이 있어도 실제 자산 로드가 실패하면 manifest에
        # 이 라벨이 없다. 멀리 찍은 사진만으로 세부를 지어내지 않고 해당 컷만 실패시킨다.
        raise ValueError("detail_reference_required")
    is_bottom = _is_bottom(clothing_type)
    # 첨부 여부(has_face)와 별개로 이 컷이 얼굴을 담는 컷인지 다시 판정 — 첨부 판정과 동일 규칙.
    use_face = has_face and _face_fits(spec, is_bottom)

    def need(key: str) -> str:
        if key not in sec:
            raise ValueError(f"프롬프트 템플릿에 섹션이 없습니다: [[{key}]]")
        return sec[key]

    shot_key = shot
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
    # v0 해시 뉘앙스는 공용(all) 예시에만 남긴다. pose/bg는 전용 이미지가 없으면
    # 아무 예시도 적용하지 않는 fail-closed 계약이며, 존재하지 않는 첨부물을 언급하지 않는다.
    if spec.get("exampleId") and spec.get("refScope") == "all" \
            and cut != "product" and spec.get("direction") in (None, "front") \
            and not pose_overrides_example:
        idx = sum(ord(ch) for ch in spec["exampleId"]) % 3
        example_line = need(f"EXNUANCE:{idx}")
    # 실제 EXAMPLE 이미지가 매니페스트에 있을 때만 범위 계약을 승격한다. 이미지 로드 실패·미등록 id는
    # 위 v0 결정적 뉘앙스 경로와 완전히 동일하게 남는다.
    has_resolved_example = (
        _EXAMPLE_ALL_LABEL in image_manifest
        or _EXAMPLE_POSE_LABEL in image_manifest
        or _EXAMPLE_BG_LABEL in image_manifest)
    # 실제 이미지가 첨부되면 그 이미지가 구도/포즈의 유일한 예시다. exampleId 해시로 만든
    # 레거시 EXNUANCE까지 함께 두면 서로 다른 팔·다리 자세를 동시에 지시하게 된다.
    if has_resolved_example:
        example_line = ""
        # 실제 all/pose 이미지가 포즈를 제어할 때 POSE:auto까지 남기면 "자연스럽게
        # 고르기"와 "예시 포즈 유지"가 충돌한다. 명시 포즈와 bg-only는 별도 계약이다.
        if spec["pose"] == "auto" and spec["refScope"] in ("all", "pose"):
            pose_line = ""
    # 매니페스트 문구는 첨부 여부만 증명한다. 범위는 반드시 정규화된 spec에서 다시 가져와
    # in-space 강제(pose)를 우회하는 불가능한 all 조합을 만들지 않는다.
    # bg는 '생성하며 참고'가 아니라 '플레이트 편집' 과업으로 전환(2026-07-20 야간 실측:
    # 참고 방식은 텍스트·순서 개선을 다 해도 성공률 ~40%에서 정체 — 10회 판정).
    bg_edit_mode = has_resolved_example and spec["refScope"] == "bg"
    if has_resolved_example and not pose_overrides_example and not bg_edit_mode:
        scope_key = "REFSCOPE:all_product" if spec["refScope"] == "all" and cut == "product" \
            else f"REFSCOPE:{spec['refScope']}"
        scope_line = need(scope_key)
        if scope_line not in example_line:
            example_line = "\n".join(part for part in (example_line, scope_line) if part)
    space_line = ""
    if spec.get("spaceGroupId"):
        space_line = need("SPACE").replace("${spaceVariation}", spec["spaceVariation"])
    outer_closure_line = ""
    if _is_outer(clothing_type) and cut in _WORN_CUTS:
        closure = spec.get("outerClosureState")
        closure = closure if closure in _OUTER_CLOSURE_STATES else "open"
        outer_closure_line = "\n".join((need("OUTER_CLOSURE:guard"), need(f"OUTER_CLOSURE:{closure}")))
    detail_color_transfer_line = ""
    transfer = spec.get("_detailColorTransfer")
    if cut == "product" and shot == "detail" and transfer:
        target = transfer["targetName"]
        if transfer.get("targetHex"):
            target += f" ({transfer['targetHex']})"
        detail_color_transfer_line = (
            need("DETAIL_COLOR_TRANSFER")
            .replace("${targetColor}", target)
            .replace("${referenceColor}", transfer.get("referenceName") or "another colorway")
        )

    text = (
        need("BASE")
        # bg 편집 모드는 라벨의 'lifestyle' 뉘앙스도 제거 — 장소 단서는 첨부 캔버스뿐이어야 한다
        .replace("${cutLabel}",
                 "worn cut composed into the attached scene" if bg_edit_mode else _CUT_LABELS[cut])
        # bg 편집 모드는 컷 종류 섹션을 통째로 교체 — 경쟁할 배경 서술이 존재하지 않게 한다
        .replace("${cutSection}", need("CUT:bg_edit") if bg_edit_mode else need(f"CUT:{cut}"))
        .replace("${shotLine}", need(f"SHOT:{shot_key}"))
        .replace("${directionLine}", direction_line)
        .replace("${faceLine}", face_line)
        .replace("${poseLine}", pose_line)
        .replace("${exampleLine}", example_line)
        .replace("${outerClosureLine}", outer_closure_line)
        .replace("${spaceLine}", space_line)
        .replace("${detailColorTransferLine}", detail_color_transfer_line)
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
    # 매칭 의류가 화면에 없으면(마네킹 참조도 MATCH 첨부도 없음) v1/v2 매칭 축 제거 —
    # 없는 옷의 핏을 지시하면 모델이 하의를 지어내는 원인이 된다(마네킹 워커와 동일 가드).
    if fit_profile and _MANNEQUIN_LABEL not in image_manifest and _MATCH_LABEL not in image_manifest:
        fit_profile = {
            k: v for k, v in fit_profile.items() if k not in ("matchCut", "matchingFit")
        }
    fit_block = build_fit_profile_block(fit_profile)
    block = _product_block(product, analysis or {}, include_legacy_fit=fit_profile is None)
    return "\n\n".join(part for part in (text, fit_block, block) if part)


def _base_color(colors: list[dict]) -> dict | None:
    return next((color for color in colors if color.get("isBase")), colors[0] if colors else None)


def _color_by_id(colors: list[dict], color_id) -> dict | None:
    if color_id is None:
        return _base_color(colors)
    return next(
        (color for color in colors
         if color.get("id") is not None and str(color.get("id")) == str(color_id)),
        None,
    )


def _color_image_pairs(color: dict | None) -> list[tuple[str, str]]:
    from .mannequin import _SLOT_ORDER  # 슬롯 정렬 기준 공유
    if not color or not (color.get("images") or []):
        return []
    images = sorted(
        (color.get("images") or []),
        key=lambda image: _SLOT_ORDER.get(image.get("slot") or "", 99),
    )
    return [(image.get("slot") or "Front", image["id"]) for image in images if image.get("id")]


def color_images(product: dict, color_id: str | None) -> list[tuple[str, str]]:
    """지정 색상 이미지 목록. color_id가 None일 때만 기준 색상을 사용한다."""
    colors = product.get("colors") or []
    return _color_image_pairs(_color_by_id(colors, color_id))


def _color_prompt_meta(color: dict | None, fallback_name: str | None) -> tuple[str, str | None]:
    color = color or {}
    swatch_id = color.get("swatchId") or color.get("swatch_id")
    swatch_name, swatch_hex = _SWATCH_META.get(str(swatch_id), (None, None))
    name = color.get("name") or color.get("label") or swatch_name or fallback_name or "target color"
    raw_hex = color.get("hex") or color.get("swatchHex") or swatch_hex
    target_hex = str(raw_hex).lower() if raw_hex and re.fullmatch(r"#[0-9a-fA-F]{6}", str(raw_hex)) else None
    return _sanitize(name)[:80] or "target color", target_hex


def detail_reference_images(
    product: dict, color_id: str | None,
) -> tuple[list[tuple[str, str]], dict | None]:
    """디테일 컷의 상품 근거와 필요 시 타색→목표색 전환 정보를 고른다.

    목표 색상에 Detail이 있으면 그 색상의 기존 이미지 목록을 그대로 쓴다. color_id가
    None일 때만 기준색으로 폴백한다. 명시된 색상이 실존하지 않으면 타색 Detail로
    생성하지 않도록 invalid_color로 실패한다. 목표 색상은 있으나 Detail만 없으면 목표 색상
    일반 이미지는 유지하면서 기준색, 그 다음 전체 색상 순서로 첫 Detail을 덧붙인다.
    일반 컷의 :func:`color_images` 엄격 선택 규칙은 바꾸지 않는다.
    """
    colors = product.get("colors") or []
    target_color = _color_by_id(colors, color_id)
    if color_id is not None and target_color is None:
        raise ValueError("invalid_color")
    target_images = _color_image_pairs(target_color)
    if any(slot == "Detail" for slot, _asset_id in target_images):
        return target_images, None

    base = _base_color(colors)
    candidates = ([base] if base is not None else []) + [color for color in colors if color is not base]
    reference_color = next(
        (color for color in candidates
         if any(slot == "Detail" for slot, _asset_id in _color_image_pairs(color))),
        None,
    )
    if reference_color is None:
        return target_images, None

    reference_details = [
        pair for pair in _color_image_pairs(reference_color) if pair[0] == "Detail"
    ]
    target_name, target_hex = _color_prompt_meta(
        target_color, None if color_id is None else str(color_id),
    )
    reference_name, _reference_hex = _color_prompt_meta(
        reference_color,
        str(reference_color.get("id")) if reference_color.get("id") is not None else None,
    )
    return [*target_images, *reference_details], {
        "targetName": target_name,
        "targetHex": target_hex,
        "referenceName": reference_name,
    }


# 첨부 이미지 역할 라벨 — 전부 고정 문구(셀러 데이터 미포함, 프롬프트 인젝션 방지)
_SLOT_LABEL = {
    "Front": "PRODUCT — front view of the garment",
    "Back": "PRODUCT — back view of the garment",
    "Detail": "PRODUCT — detail close-up of the garment (texture, stitching, print)",
    "Fit": "PRODUCT — fit reference, the garment worn on a person (true length & drape)",
}
# 마네킹/매칭 첨부 라벨 — render_cut_prompt 의 매칭 핏 가드가 매니페스트에서 이 문구로
# "하의가 화면에 있는가"를 판별하므로 상수로 공유(문구 드리프트 방지).
_MANNEQUIN_LABEL = "PRODUCT — the garment worn on a mannequin (verified colors, fit and length — follow this)"
_MODEL_LABEL = "MODEL — frontal close-up of the model (identity ground truth; do NOT copy this image's pose, framing, or clothing)"
_MODEL_SHEET_LABEL = "MODEL SHEET — a 2x2 grid of four studio portraits of the SAME single person (identity reference only). Do NOT copy the grid layout, framing, poses, or clothing; the output must be one single normal photograph, never a grid"
_MATCH_LABEL = "MATCHING — the user-selected coordinating garment worn in the same outfit"
# FaceMarket 라이선스 얼굴 첨부 라벨(FM-31). 위 두 라벨의 부분문자열이 되면 matchCut 가드가
# 오발해 없는 하의를 지시하므로 'mannequin'·_MATCH_LABEL 문구를 섞지 않는다.
_FACE_LABEL = ("MODEL FACE — the licensed model's face reference: reproduce THIS person's "
               "facial identity (never copy their clothing, background or framing)")
_EXAMPLE_ALL_LABEL = "EXAMPLE REFERENCE (scope: all)"
_EXAMPLE_POSE_LABEL = "POSE CONTROL"
_EXAMPLE_BG_LABEL = "EXAMPLE REFERENCE (scope: bg)"


def build_manifest(
    prod_assets: list[dict], *, has_mannequin: bool, has_match: bool,
    mood_count: int, has_model_face: bool = False, has_model_sheet: bool = False,
    has_face: bool = False, example_scope: str | None = None,
    example_is_product: bool = False,
) -> str:
    """첨부 이미지와 동일 순서의 역할 목록.

    순서: mannequin?, virtual-model face+sheet?, *product, match?, licensed-face?,
    *mood, example?. pose의 상대 순서는 PRODUCT → MATCHING → POSE CONTROL로 고정한다.
    라이선스 얼굴은 옷 근거 뒤에 두며, 호출자는 정체성 충돌을 막기 위해
    licensed-face와 virtual-model 참조를 동시에 켜지 않는다.
    """
    lines: list[str] = []
    i = 1
    if has_mannequin:
        lines.append(f"{i}. {_MANNEQUIN_LABEL}")
        i += 1
    if has_model_face:
        lines.append(f"{i}. {_MODEL_LABEL}")
        i += 1
    if has_model_sheet:
        lines.append(f"{i}. {_MODEL_SHEET_LABEL}")
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
    if example_scope == "all" and example_is_product:
        lines.append(
            f"{i}. {_EXAMPLE_ALL_LABEL} — source of background, lighting, mood, framing and "
            "composition; never copy its garments, shoes, accessories, person, model identity or pose"
        )
    elif example_scope == "all":
        lines.append(
            f"{i}. {_EXAMPLE_ALL_LABEL} — source of background, lighting, mood, pose and "
            "framing/composition; never copy its garments, shoes, accessories or model identity"
        )
    elif example_scope == "pose":
        lines.append(
            f"{i}. {_EXAMPLE_POSE_LABEL} — transparent neutral mannequin used ONLY as a kinematic "
            "control; PRODUCT and MATCHING remain the only clothing evidence; CUT SPEC controls "
            "camera, crop, placement and background"
        )
    elif example_scope == "bg":
        # 스파이크(2026-07-12): 자산은 인물을 지운 '빈 무대 플레이트' — 포즈·의류 유출을 구조적으로 차단
        # 라벨은 명령형 + 첫 첨부(2026-07-20 파일럿): 서술형 라벨·마지막 첨부는 컷 섹션의 배경
        # 나열에 밀렸다. 워커가 bg 플레이트를 첫 이미지로 붙이므로 라벨도 맨 앞으로 재번호한다.
        bg_label = (
            f"{_EXAMPLE_BG_LABEL} — THE scene canvas (the base image being edited): insert the "
            "model into this exact scene; outside the person everything stays as in this image; "
            "it has no person, so choose the pose yourself and never copy garments, shoes or "
            "props onto the model"
        )
        renumbered = [bg_label] + [line.split(". ", 1)[1] for line in lines]
        lines = [f"{n}. {label}" for n, label in enumerate(renumbered, start=1)]
    return "\n".join(lines) or "(the seller's product photos — treat as ground truth)"


def build_prompt(
    cut_spec: dict, product: dict, *,
    analysis: dict | None = None, manifest: str | None = None, has_face: bool = False,
) -> str:
    """스펙 정규화(ValueError=unknown_cut_type) + 템플릿 렌더. manifest 미지정 시
    일반 컷은 해당 색상 상품 슬롯을, 디테일 컷은 detail_reference_images 정책의 상품 슬롯을
    첨부한다고 가정하고 동일 순서 목록을 만든다(+ has_face 면 얼굴)."""
    clothing_type = product.get("clothing_type") or product.get("clothingType") or "top"
    spec = normalize_spec(cut_spec, clothing_type=clothing_type)
    # pose-only medium은 v2 QC 결론대로 먼저 full 프레이밍으로 생성하고 generate()가
    # 결정적 body-landmark crop을 적용한다. all/bg 및 비-pose 경로는 기존 프롬프트 그대로다.
    if spec["refScope"] == "pose" and spec["shot"] == "medium":
        spec = {**spec, "shot": "full"}
    if manifest is None:
        if spec["cutType"] == "product" and spec["shot"] == "detail":
            selected_images, transfer = detail_reference_images(product, spec["colorId"])
            spec["_detailColorTransfer"] = transfer
        else:
            selected_images = color_images(product, spec["colorId"])
        prod_assets = [{"slot": slot} for slot, _id in selected_images]
        manifest = build_manifest(
            prod_assets, has_mannequin=False, has_match=False, mood_count=0,
            has_face=has_face and _face_fits(spec, _is_bottom(clothing_type)))
    return render_cut_prompt(
        load_cut_template(), spec, product, analysis or {}, clothing_type, manifest, has_face)


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
    clothing_type = product.get("clothing_type") or product.get("clothingType") or "top"
    spec = normalize_spec(cut_spec, clothing_type=clothing_type)
    crop_pose_medium = spec["refScope"] == "pose" and spec["shot"] == "medium"
    prompt = build_prompt(cut_spec, product, analysis=analysis, manifest=manifest, has_face=has_face)
    res = await gemini.generate_content_image(
        model, prompt, images, settings.mannequin_image_size,
        aspect_ratio=settings.mannequin_aspect_ratio,
    )
    if crop_pose_medium:
        return await pose_crop.crop_pose_medium(settings, res.image, res.mime)
    return res.image, res.mime
