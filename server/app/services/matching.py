"""매칭의류 추천 — mock matchingRecommendation.js 규칙의 서버 포팅.

순수 함수(DB·IO 없음). 라우트가 list_active_matching_items 결과를 넘긴다.
규칙: 보색 타입 → isActive·타입·성별 필터 → colorBrightness 내림차순, 동률 sort_order.
"""

_TOP_SIDE = {"top", "outer", "dress"}


def complementary_type(clothing_type: str) -> str:
    return "bottom" if clothing_type in _TOP_SIDE else "top"


def recommend(items, clothing_type, genders, limit=None):
    preferred = complementary_type(clothing_type)
    gset = set(genders or [])
    pool = [
        i for i in items
        if i.get("is_active")
        and i.get("clothing_type") == preferred
        and (not gset or i.get("gender") == "unisex" or i.get("gender") in gset)
    ]
    pool.sort(key=lambda i: (
        -(i["color_brightness"] if i.get("color_brightness") is not None else 50),
        i.get("sort_order", 0),
    ))
    return pool[:limit] if limit else pool


def to_candidate(item: dict, public_url) -> dict | None:
    """matching_items row → MatchClothing 후보 shape (계약 §3.2의 과도기 확장 —
    match-candidates 라우트·PL-1 워커 공용, pl1 spec §3.4). thumb_key 없으면 None."""
    if not item.get("thumb_key"):
        return None
    thumb = public_url(item["thumb_key"])
    return {
        "id": item["id"],
        "name": item["name"],
        "gender": item["gender"],
        "thumb": thumb,
        "imageUrl": public_url(item["image_key"]) if item.get("image_key") else None,
        "thumbnailUrl": thumb,
    }
