"""매칭의류 추천 — mock matchingRecommendation.js 규칙의 서버 포팅.

순수 함수(DB·IO 없음). 라우트가 list_active_matching_items 결과를 넘긴다.
규칙: 보색 타입 → isActive·타입·성별 필터 → colorBrightness 내림차순, 동률 sort_order.
"""

_TOP_SIDE = {"top", "outer", "dress"}

# matching_items.category is curated seed metadata (not seller text).  Keep this
# mapping explicit so a new/unknown category cannot accidentally acquire a fit
# vocabulary just because its display name happens to contain "pants"/"skirt".
_PANTS_CATEGORIES = frozenset({"팬츠", "데님팬츠", "트라우저", "스웨트팬츠", "치노팬츠"})
_SKIRT_CATEGORIES = frozenset({"스커트"})
_SHORT_CATEGORIES = frozenset({"쇼츠", "버뮤다쇼츠"})


def complementary_type(clothing_type: str) -> str:
    return "bottom" if clothing_type in _TOP_SIDE else "top"


def fit_category(item: dict) -> str | None:
    """Return the matching-fit catalog category from curated item metadata.

    Only known full-length pants and skirts expose an adjustable vocabulary.
    Shorts/Bermudas, tops, and unknown metadata deliberately return ``None``.
    """
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
