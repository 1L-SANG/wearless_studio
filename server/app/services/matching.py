"""매칭의류 추천 — mock matchingRecommendation.js 규칙의 서버 포팅.

순수 함수(DB·IO 없음). 라우트가 list_active_matching_items 결과를 넘긴다.
규칙: 보색 타입 → isActive·타입·성별 필터 → colorBrightness 내림차순, 동률 sort_order.
"""

_TOP_SIDE = {"top", "outer"}
# 원피스는 그 자체로 상·하의가 붙은 한 벌이라 **맞춰 입힐 하의가 없다**. 예전에는 dress 를
# 상의쪽으로 묶어 하의를 추천했는데, 셀러 화면에 필요 없는 매칭 카드가 뜨고 마네킹 컷에도
# 원피스 아래 바지·치마가 함께 들어가 옷을 가렸다(2026-08-01 셀러 보고).
_NO_MATCH = {"dress"}

# matching_items.category is curated seed metadata (not seller text).  Keep this
# mapping explicit so a new/unknown category cannot accidentally acquire a fit
# vocabulary just because its display name happens to contain "pants"/"skirt".
_PANTS_CATEGORIES = frozenset({"팬츠", "데님팬츠", "트라우저", "스웨트팬츠", "치노팬츠"})
_SKIRT_CATEGORIES = frozenset({"스커트"})
_SHORT_CATEGORIES = frozenset({"쇼츠", "버뮤다쇼츠"})


def complementary_type(clothing_type: str) -> str | None:
    """맞춰 입힐 반대편 카테고리. 매칭이 성립하지 않는 종류(원피스)는 None."""
    if clothing_type in _NO_MATCH:
        return None
    return "bottom" if clothing_type in _TOP_SIDE else "top"


def fit_category(item: dict) -> str | None:
    """Return the matching-fit catalog category from curated item metadata.

    bottoms: only known full-length pants and skirts expose an adjustable vocabulary —
    shorts/Bermudas and unknown metadata deliberately return ``None``.
    tops: return ``"top"`` (2026-08-01, WS2) — 하의 상품의 매칭 상의는 length 축으로 조정된다.
    상의가 상품(바지)의 허리를 가리는 문제의 조정 수단이라, 여기서 None 을 돌려주면 프론트
    매칭 조정 스텝이 구조적으로 뜰 수 없다(matchingFit.js MATCHING_AXIS 미러).
    """
    if item.get("clothing_type") == "top":
        return "top"
    if item.get("clothing_type") != "bottom":
        return None
    category = item.get("category")
    if category in _SHORT_CATEGORIES:
        return None
    if category in _SKIRT_CATEGORIES:
        return "skirt"
    if category in _PANTS_CATEGORIES and item.get("length") == "full":
        return "pants"
    return None


def prefilter(items, clothing_type, genders):
    """보완타입·is_active·성별 프리필터 (룰베이스 recommend·v1 태그 랭킹 공통 소스).

    보완타입(top/outer/dress→bottom, 그 외→top) + is_active + 성별(unisex 항상 포함).
    랭킹 전 후보 풀을 좁히는 단계 — recommend()와 retrieval.recommend_v1()이 같은 소스를
    쓰도록 여기 한 곳에만 둔다(드리프트 방지, FR-A2 "프리필터 불변").
    """
    preferred = complementary_type(clothing_type)
    if preferred is None:      # 원피스 — 추천할 반대편이 없다
        return []
    gset = set(genders or [])
    return [
        i for i in items
        if i.get("is_active")
        and i.get("clothing_type") == preferred
        and (not gset or i.get("gender") == "unisex" or i.get("gender") in gset)
    ]


def recommend(items, clothing_type, genders, limit=None):
    pool = prefilter(items, clothing_type, genders)
    pool.sort(key=lambda i: (
        -(i["color_brightness"] if i.get("color_brightness") is not None else 50),
        i.get("sort_order", 0),
    ))
    return pool[:limit] if limit is not None else pool  # limit=0 → 빈 결과(0은 falsy 방지)
