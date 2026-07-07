"""매칭의류 추천 — mock matchingRecommendation.js 규칙의 서버 포팅.

순수 함수(DB·IO 없음). 라우트가 list_active_matching_items 결과를 넘긴다.
규칙: 보색 타입 → isActive·타입·성별 필터 → colorBrightness 내림차순, 동률 sort_order.
"""

_TOP_SIDE = {"top", "outer", "dress"}


def complementary_type(clothing_type: str) -> str:
    return "bottom" if clothing_type in _TOP_SIDE else "top"


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
