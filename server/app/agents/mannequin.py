"""AG-04 마네킹 생성 — 입력 조립 헬퍼 (순수 함수, DB/IO 없음).

워커(workers/mannequin_job.py)가 이 헬퍼로 "무엇을 로드/생성할지"를 정한다:
- 성별 베이스 선택 · 핏 프로필 spec · 기준 색상 이미지 asset id · 메인 매칭의류(하의) id.
실제 바이트 로드·R2 저장·DB 쓰기는 워커/repo가 한다 (ai_agent_modules §3 AG-04).
"""

from .prompts import MannequinPromptContext

# 기준 색상 이미지 정렬 순서 (common_data_contract §4 AngleSlot)
_SLOT_ORDER = {"Front": 0, "Back": 1, "Detail": 2, "Fit": 3}


def select_base_gender(analysis: dict) -> str:
    """분석의 targetGenders로 남/여 베이스 결정. 남성 단독일 때만 'men', 그 외(혼합·비어있음·여성)
    는 'women' (MVP 결정적 규칙)."""
    genders = {str(g).lower() for g in (analysis.get("targetGenders") or [])}
    men_tokens = {"men", "male", "남성", "남"}
    if genders and genders <= men_tokens:  # 전부 남성 토큰
        return "men"
    return "women"


def generation_spec(analysis: dict) -> dict | None:
    """단일 마네킹 생성 spec. fitProfile이 없거나 형태가 아니면 프로필 블록 없이 생성한다."""
    profile = (analysis or {}).get("fitProfile")
    return profile if isinstance(profile, dict) else None


def effective_fit_profile(analysis: dict, has_match_image: bool) -> dict | None:
    """워커가 프롬프트에 쓸 최종 fit profile. 매칭 하의 이미지가 없는 잡에선 matchCut 을
    제거한다 — 화면에 없는 옷의 핏을 지시하면 모델이 하의를 지어내는 원인이 된다."""
    profile = generation_spec(analysis)
    if profile and not has_match_image and "matchCut" in profile:
        profile = {k: v for k, v in profile.items() if k != "matchCut"}
    return profile


_TOP_TYPES = {"top", "outer", "dress", "상의", "아우터", "원피스"}


def slim_variant_profile(profile: dict | None, clothing_type: str, gender: str) -> dict:
    """후보 B(슬림 변형)용 fit profile — A/B 이원 생성(정핏/슬림핏, 원 UI 계약).
    기존 프로필이 있으면 핏 축만 slim 으로 덮고, 없으면 의류 종류에 맞는 최소 프로필을 만든다.
    카탈로그(fit_axes)에 없는 category/값은 렌더에서 조용히 스킵되므로 안전하다."""
    if isinstance(profile, dict):
        axes = dict(profile.get("axes") or {})
        axes["cut" if profile.get("category") == "pants" else "fit"] = "slim"
        return {**profile, "axes": axes}
    if (clothing_type or "").lower() in _TOP_TYPES:
        return {"category": "top", "gender": gender, "axes": {"fit": "slim"}}
    return {"category": "pants", "gender": gender, "axes": {"cut": "slim"}}


def base_color_images(product: dict) -> list[tuple[str, str]]:
    """기준 색상(ColorGroup.isBase, 없으면 colors[0]) 이미지의 (slot, asset_id) 목록 (slot 순서).
    slot ∈ Front/Back/Detail/Fit. Front 필수는 입력 검증에서 거른다(나머지는 선택)."""
    colors = product.get("colors") or []
    base = next((c for c in colors if c.get("isBase")), colors[0] if colors else None)
    if not base:
        return []
    images = sorted((base.get("images") or []), key=lambda im: _SLOT_ORDER.get(im.get("slot") or "", 99))
    return [(im.get("slot") or "Front", im["id"]) for im in images if im.get("id")]


def base_color_image_ids(product: dict) -> list[str]:
    """기준 색상 이미지 asset id들 (slot 순서). ImageAsset.id == asset row id (업로드 계약)."""
    return [aid for _slot, aid in base_color_images(product)]


def has_base_front(product: dict) -> bool:
    """기준 색상에 정면(Front) 이미지가 있는가 (PRD: 기준 색상 앞면 필수 · TODO A-6 게이트)."""
    colors = product.get("colors") or []
    base = next((c for c in colors if c.get("isBase")), colors[0] if colors else None)
    if not base:
        return False
    return any((im.get("slot") == "Front") and im.get("id") for im in (base.get("images") or []))


def main_match_item_id(analysis: dict) -> str | None:
    """메인 매칭의류(하의) id — 있으면 마네킹컷에 함께 착장(상의+하의). 사용자 결정.
    계약형 matchSelections = [{clothingId, role}] (role='main'). {main} / [id] 폴백도 처리.
    실 프론트(httpAdapter·계약 §6)는 레거시 matchClothing(후보 전체 + selected/selOrder)으로
    analysis 에 저장하므로, matchSelections 가 비어 있으면 그쪽에서 읽는다 — 아니면 UI 가
    받은 매칭 핏 조정(matchCut)이 생성에서 조용히 무시된다."""
    sel = analysis.get("matchSelections")
    if isinstance(sel, list):
        for e in sel:  # 계약형: role=='main'
            if isinstance(e, dict) and e.get("role") == "main" and (e.get("clothingId") or e.get("id")):
                return e.get("clothingId") or e.get("id")
        first = sel[0] if sel else None  # 폴백: 첫 항목
        if isinstance(first, dict):
            first = first.get("clothingId") or first.get("id")
        if first:
            return first
    elif isinstance(sel, dict) and sel.get("main"):
        return sel["main"]
    # 레거시 폴백: selected 항목 중 selOrder 최솟값 = 메인 (UI 선택 순서 1번).
    mc = analysis.get("matchClothing")
    if isinstance(mc, list):
        chosen = sorted(
            (e for e in mc if isinstance(e, dict) and e.get("selected") and e.get("id")),
            key=lambda e: e.get("selOrder") or 99,
        )
        if chosen:
            return chosen[0]["id"]
    return None


def prompt_context(
    *, clothing_type: str, product_count: int, base_gender: str,
    image_manifest: str = "", fit_profile: dict | None = None,
) -> MannequinPromptContext:
    return MannequinPromptContext(
        clothing_type=clothing_type or "상의",
        product_count=product_count,
        base_gender=base_gender,
        image_manifest=image_manifest,
        fit_profile=fit_profile,
    )
