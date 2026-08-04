"""AG-04 마네킹 생성 — 입력 조립 헬퍼 (순수 함수, DB/IO 없음).

워커(workers/mannequin_job.py)가 이 헬퍼로 "무엇을 로드/생성할지"를 정한다:
- 성별 베이스 선택 · 핏 프로필 spec · 기준 색상 이미지 asset id · 메인 매칭의류(하의) id.
실제 바이트 로드·R2 저장·DB 쓰기는 워커/repo가 한다 (ai_agent_modules §3 AG-04).
"""

from .prompts import MannequinPromptContext

# 기준 색상 이미지 정렬 순서 (common_data_contract §4 AngleSlot)
_SLOT_ORDER = {"Front": 0, "Back": 1, "Detail": 2, "Fit": 3}


def select_base_gender(
    analysis: dict, clothing_type: str | None = None
) -> str:
    """분석의 targetGenders로 남/여 베이스 결정 — **첫 번째 값**을 따른다. 원피스는 항상 여성.

    셀러 화면의 '대상 성별'은 단일 선택 칩이고 `targetGenders[0]` 만 표시한다
    (AnalysisForm.jsx: `value={a.targetGenders?.[0]}`). 그런데 예전 규칙은 "전부 남성 토큰일
    때만 men"이라, AI 분석이 `["men","women"]` 을 넣어두면 **화면에는 '남성'이 선택돼 보이는데
    여성 베이스가 나갔다**(2026-08-01 실측: 회색 후드·회색 니트 등 혼합 프로젝트에서 여성
    마네킹 + 가슴 2패스까지 적용). 셀러가 고른 것과 결과가 다르면 그건 배선이 끊긴 것이다.
    화면이 보여주는 값을 정본으로 삼아 UI 와 서버를 한 곳에 모은다.
    """
    if str(clothing_type or "").lower() == "dress":
        return "women"
    genders = [str(g).lower() for g in (analysis.get("targetGenders") or [])]
    men_tokens = {"men", "male", "남성", "남"}
    return "men" if genders and genders[0] in men_tokens else "women"


# 미세 반복 패턴 어휘. 이런 원단은 한 주기 안에 얇은 선 여러 개가 들어가므로 출력 해상도가
# 곧 재현 한계가 된다 — 2K 실측(2026-08-01)에서 줄 주기가 8.9px 이라 한 주기를 구성하는
# 요소(하늘색 선·흰 간격·베이지 선)당 2px 남짓뿐이어서, 두 색 줄이 한 색으로 뭉개졌다.
# 무지·단색은 재현할 고주파가 없어 해당 없음(그래서 잘 나온다).
_FINE_PATTERN_TOKENS = (
    "스트라이프", "줄무늬", "핀스트라이프", "체크", "깅엄", "타탄", "글렌체크", "하운드투스",
    "헤링본", "도트", "물방울", "잔무늬", "패턴", "격자",
    "stripe", "pinstripe", "check", "gingham", "tartan", "plaid", "houndstooth",
    "herringbone", "polka", "dot", "windowpane",
)


_SOLID_PATTERN_TYPES = {"solid", "plain", "none", "no_pattern", "무지", "단색"}
_REPEATING_PATTERN_TYPES = {
    "stripe", "pinstripe", "check", "gingham", "tartan", "plaid", "houndstooth",
    "herringbone", "dot", "polka", "windowpane", "pattern", "스트라이프", "줄무늬",
    "체크", "깅엄", "타탄", "격자", "도트", "물방울", "잔무늬", "패턴",
}
_STRIPE_PATTERN_TYPES = {"stripe", "pinstripe", "스트라이프", "줄무늬", "핀스트라이프"}
_STRIPE_PATTERN_TOKENS = tuple(_STRIPE_PATTERN_TYPES)


def _truthy_bool(v) -> bool | None:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)) and v in (0, 1):
        return bool(v)
    if isinstance(v, str):
        norm = v.strip().lower()
        if norm in {"true", "yes", "y", "1"}:
            return True
        if norm in {"false", "no", "n", "0"}:
            return False
    return None


def _pattern_spec(src: dict | None) -> dict | None:
    if not isinstance(src, dict):
        return None
    spec = src.get("patternSpec") or src.get("pattern_spec")
    return spec if isinstance(spec, dict) else None


def _get_any(src: dict, *keys: str):
    for key in keys:
        if key in src:
            return src[key]
    return None


def _structured_fine_pattern_risk(src: dict | None) -> bool | None:
    """구조화 pattern spec이 있으면 미세/반복 패턴 리스크를 결정한다.

    SOLID/PLAIN/NONE 은 명시적 no-risk 정본이다. 그 외 반복 패턴 type은 finePattern 값이
    빠진 옛 payload에서도 보수적으로 risk로 본다. finePattern이 명시돼 있으면 그 값을 따른다.
    """
    spec = _pattern_spec(src)
    if not spec:
        return None

    pattern_type = str(spec.get("type") or spec.get("patternType") or "").strip().lower()
    if pattern_type in _SOLID_PATTERN_TYPES:
        return False

    # Runtime contract: every declared repeating pattern is high-resolution risk.
    # `finePattern` is an analysis observation, not permission to downgrade a STRIPE
    # product to the ordinary 1K path.  The 2026-08-04 goldenset shirt was approved
    # as STRIPE + finePattern=false and consequently rendered at 1K; deterministic
    # projection then failed because the target period contained too few pixels.
    if pattern_type in _REPEATING_PATTERN_TYPES:
        return True
    if pattern_type and any(tok in pattern_type for tok in _REPEATING_PATTERN_TYPES):
        return True

    fine = _truthy_bool(_get_any(spec, "finePattern", "fine_pattern"))
    if fine is not None:
        return fine
    return None


def resolve_fine_pattern_risk(
    product: dict | None,
    analysis: dict | None,
    product_truth: dict | None = None,
) -> bool:
    """미세/반복 패턴 리스크 정본 해석.

    우선순위:
    1. 승인 Product Truth patternSpec / pattern_spec
    2. analysis 의 구조화 patternSpec / pattern_spec
    3. 레거시 seller/AI 텍스트 토큰

    승인 Product Truth가 SOLID 라고 말하면 stale 상품명·selling point 텍스트가 패턴을 강제할
    수 없다. Product Truth가 없을 때의 기존 텍스트 기반 동작은 유지한다.
    """
    if isinstance(product_truth, dict) and product_truth.get("status") == "approved":
        truth_risk = _structured_fine_pattern_risk(product_truth)
        if truth_risk is not None:
            return truth_risk

    analysis_risk = _structured_fine_pattern_risk(analysis)
    if analysis_risk is not None:
        return analysis_risk

    return _legacy_text_fine_pattern_risk(product, analysis)


def _legacy_text_fine_pattern_risk(product: dict | None, analysis: dict | None) -> bool:
    parts = []
    for src in (product or {}), (analysis or {}):
        for key in ("name", "suggestedName", "customCategory", "subCategory"):
            v = src.get(key)
            if isinstance(v, str):
                parts.append(v)
        for key in ("sellingPoints", "aiSuggestedPoints", "styleTags"):
            v = src.get(key)
            if isinstance(v, list):
                parts.extend(x for x in v if isinstance(x, str))
    blob = " ".join(parts).lower()
    return any(tok.lower() in blob for tok in _FINE_PATTERN_TOKENS)


def has_fine_pattern(
    product: dict | None,
    analysis: dict | None,
    product_truth: dict | None = None,
) -> bool:
    """미세 반복 패턴 상품인가 — 구조화 정본을 우선하고 레거시 텍스트로 폴백한다(순수).

    분석 payload 에 패턴 전용 필드가 없어서 이름·특징(sellingPoints)·카테고리를 훑는다.
    실측 예: 스트라이프 셔츠의 sellingPoints = ["멀티 스트라이프", "세미 크롭 기장"].
    과탐(무지인데 4K)은 비용만 더 쓰고 결과는 같지만, 미탐(패턴인데 2K)은 셀러가 원단이
    다르다고 느끼는 컷이 나가므로 **넓게 잡는 쪽**이 맞다.
    """
    return resolve_fine_pattern_risk(product, analysis, product_truth)


def has_stripe_pattern(
    product: dict | None,
    analysis: dict | None,
    product_truth: dict | None = None,
) -> bool:
    """4K 비용 승격 대상인 스트라이프인가.

    체크 등 다른 반복 패턴도 보호/QC 대상으로는 남지만, 4K 생성은 승인된 STRIPE 계열에만
    허용한다. 승인 Product Truth가 있으면 stale 상품명보다 우선한다.
    """
    for src, authoritative in (
        (product_truth, isinstance(product_truth, dict)
         and product_truth.get("status") == "approved"),
        (analysis, False),
    ):
        if src is product_truth and not authoritative:
            continue
        spec = _pattern_spec(src)
        if not spec:
            continue
        pattern_type = str(
            spec.get("type") or spec.get("patternType") or ""
        ).strip().lower()
        if pattern_type:
            is_stripe = (
                pattern_type in _STRIPE_PATTERN_TYPES
                or any(token in pattern_type for token in _STRIPE_PATTERN_TYPES)
            )
            return is_stripe

    parts = []
    for src in (product or {}), (analysis or {}):
        for key in ("name", "suggestedName", "customCategory", "subCategory"):
            value = src.get(key)
            if isinstance(value, str):
                parts.append(value)
        for key in ("sellingPoints", "aiSuggestedPoints", "styleTags"):
            value = src.get(key)
            if isinstance(value, list):
                parts.extend(item for item in value if isinstance(item, str))
    blob = " ".join(parts).lower()
    return any(token in blob for token in _STRIPE_PATTERN_TOKENS)


def generation_spec(analysis: dict) -> dict | None:
    """단일 마네킹 생성 spec. fitProfile이 없거나 형태가 아니면 프로필 블록 없이 생성한다."""
    profile = (analysis or {}).get("fitProfile")
    return profile if isinstance(profile, dict) else None


def effective_fit_profile(analysis: dict, has_match_image: bool) -> dict | None:
    """워커가 프롬프트에 쓸 최종 fit profile. 매칭 의류 이미지가 없는 잡에선 v1/v2
    매칭 축을 제거한다 — 화면에 없는 옷의 핏을 지시하면 모델이 하의를 지어내는 원인이 된다."""
    profile = generation_spec(analysis)
    if profile and not has_match_image:
        profile = {k: v for k, v in profile.items() if k not in ("matchCut", "matchingFit")}
    return profile


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
    계약형 matchSelections = [{clothingId, role}] (role='main'). {main} 폴백도 처리.
    실 프론트(httpAdapter·계약 §6)는 레거시 matchClothing(후보 전체 + selected/selOrder)으로
    analysis 에 저장하므로, matchSelections 가 비어 있으면 그쪽에서 읽는다 — 아니면 UI 가
    받은 매칭 핏 조정(matchCut/matchingFit)이 생성에서 조용히 무시된다."""
    sel = analysis.get("matchSelections")
    if isinstance(sel, list):
        for e in sel:  # 계약형: role=='main'
            if isinstance(e, dict) and e.get("role") == "main" and (e.get("clothingId") or e.get("id")):
                return e.get("clothingId") or e.get("id")
    elif isinstance(sel, dict) and sel.get("main"):
        main = sel["main"]
        if isinstance(main, dict):
            return main.get("clothingId") or main.get("id")
        return main
    # 레거시 폴백: selected 항목 중 selOrder 최솟값 = 메인 (UI 선택 순서 1번).
    mc = analysis.get("matchClothing")
    if isinstance(mc, list):
        chosen = sorted(
            (e for e in mc if isinstance(e, dict) and e.get("selected") and e.get("id")),
            key=lambda e: (
                e.get("selOrder") if isinstance(e.get("selOrder"), (int, float)) else float("inf")
            ),
        )
        if chosen:
            return chosen[0]["id"]
    return None


def prompt_context(
    *, clothing_type: str, product_count: int, base_gender: str,
    image_manifest: str = "", fit_profile: dict | None = None, adjusted_axes: tuple = (),
) -> MannequinPromptContext:
    return MannequinPromptContext(
        clothing_type=clothing_type or "상의",
        product_count=product_count,
        base_gender=base_gender,
        image_manifest=image_manifest,
        fit_profile=fit_profile,
        adjusted_axes=tuple(adjusted_axes or ()),
    )
