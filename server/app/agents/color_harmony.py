"""상품색 × 매칭 의류색 조화 시드맵 — 결정적 정적 lookup.

상품 분석의 12색 스와치(`product_analyst.SWATCH_IDS`)와 매칭 실데이터에만 있는
``khaki``를 어휘로 삼는다. 값은 [0, 1] 구간이며, 대칭인 조합을 양방향으로
중복 저장하지 않는다. 조회부는 정방향 실패 시 역방향을 확인한다.

동색은 톤온톤이지만 한 벌처럼 뭉개질 수 있어 0.55로 둔다. 미등재 조합이나
새로 들어온 색은 0.5(중립)로 처리해 색 어휘 확장이 전체 랭킹을 망가뜨리지 않는다.
"""

import re

HARMONY: dict[tuple[str, str], float] = {
    # 톤온톤 — 안정적이지만 대비가 약하므로 중간값
    ("white", "white"): 0.55,
    ("ivory", "ivory"): 0.55,
    ("gray", "gray"): 0.55,
    ("black", "black"): 0.55,
    ("beige", "beige"): 0.55,
    ("brown", "brown"): 0.55,
    ("red", "red"): 0.55,
    ("yellow", "yellow"): 0.55,
    ("green", "green"): 0.55,
    ("blue", "blue"): 0.55,
    ("navy", "navy"): 0.55,
    ("pink", "pink"): 0.55,
    ("khaki", "khaki"): 0.55,

    # 무채색끼리 — 명도 대비가 분명한 조합을 높게
    ("white", "ivory"): 0.75,
    ("white", "gray"): 0.82,
    ("white", "black"): 0.90,
    ("ivory", "gray"): 0.78,
    ("ivory", "black"): 0.82,
    ("gray", "black"): 0.72,

    # 화이트·아이보리 × 유채색/어스톤 — 대부분의 색을 받아 주는 밝은 베이스
    ("white", "beige"): 0.86,
    ("white", "brown"): 0.80,
    ("white", "red"): 0.88,
    ("white", "yellow"): 0.78,
    ("white", "green"): 0.82,
    ("white", "blue"): 0.92,
    ("white", "navy"): 0.90,
    ("white", "pink"): 0.86,
    ("white", "khaki"): 0.80,
    ("ivory", "beige"): 0.80,
    ("ivory", "brown"): 0.82,
    ("ivory", "red"): 0.80,
    ("ivory", "yellow"): 0.74,
    ("ivory", "green"): 0.78,
    ("ivory", "blue"): 0.84,
    ("ivory", "navy"): 0.88,
    ("ivory", "pink"): 0.84,
    ("ivory", "khaki"): 0.82,

    # 그레이 × 유채색/어스톤 — 채도를 누그러뜨리는 중립 베이스
    ("gray", "beige"): 0.78,
    ("gray", "brown"): 0.70,
    ("gray", "red"): 0.82,
    ("gray", "yellow"): 0.72,
    ("gray", "green"): 0.76,
    ("gray", "blue"): 0.82,
    ("gray", "navy"): 0.76,
    ("gray", "pink"): 0.82,
    ("gray", "khaki"): 0.72,

    # 블랙 × 유채색/어스톤 — 고대비 클래식과 탁한 저대비 조합을 분리
    ("black", "beige"): 0.90,
    ("black", "brown"): 0.35,
    ("black", "red"): 0.85,
    ("black", "yellow"): 0.78,
    ("black", "green"): 0.68,
    ("black", "blue"): 0.62,
    ("black", "navy"): 0.38,
    ("black", "pink"): 0.78,
    ("black", "khaki"): 0.58,

    # 베이지 중심 클래식/어스톤 — 네이비·카키·브라운과 안정적인 조합
    ("beige", "brown"): 0.78,
    ("beige", "red"): 0.72,
    ("beige", "yellow"): 0.70,
    ("beige", "green"): 0.75,
    ("beige", "blue"): 0.82,
    ("beige", "navy"): 0.92,
    ("beige", "pink"): 0.78,
    ("beige", "khaki"): 0.80,

    # 브라운·카키 중심 어스톤
    ("brown", "red"): 0.65,
    ("brown", "yellow"): 0.68,
    ("brown", "green"): 0.72,
    ("brown", "blue"): 0.58,
    ("brown", "navy"): 0.62,
    ("brown", "pink"): 0.70,
    ("brown", "khaki"): 0.78,
    ("red", "khaki"): 0.55,
    ("yellow", "khaki"): 0.60,
    ("green", "khaki"): 0.72,
    ("blue", "khaki"): 0.55,
    ("navy", "khaki"): 0.72,
    ("pink", "khaki"): 0.58,

    # 강한 유채색끼리 — 경쟁이 큰 조합은 의도적으로 낮게
    ("red", "yellow"): 0.30,
    ("red", "green"): 0.22,
    ("red", "blue"): 0.35,
    ("red", "navy"): 0.65,
    ("red", "pink"): 0.30,
    ("yellow", "green"): 0.30,
    ("yellow", "blue"): 0.35,
    ("yellow", "navy"): 0.58,
    ("yellow", "pink"): 0.28,
    ("green", "blue"): 0.35,
    ("green", "navy"): 0.62,
    ("green", "pink"): 0.25,
    ("blue", "navy"): 0.60,
    ("blue", "pink"): 0.35,
    ("navy", "pink"): 0.68,
    # 퍼플(2026-08-15 팔레트 추가) — 무채색·아이보리와 잘 맞고, 유채색끼리는 낮게.
    ("purple", "purple"): 0.55,
    ("white", "purple"): 0.84,
    ("ivory", "purple"): 0.8,
    ("gray", "purple"): 0.8,
    ("black", "purple"): 0.82,
    ("beige", "purple"): 0.7,
    ("brown", "purple"): 0.52,
    ("khaki", "purple"): 0.4,
    ("navy", "purple"): 0.62,
    ("blue", "purple"): 0.6,
    ("pink", "purple"): 0.58,
    ("red", "purple"): 0.3,
    ("yellow", "purple"): 0.32,
    ("green", "purple"): 0.34,
}


def harmony_map() -> dict:
    """현재 활성 색 조화 맵을 반환한다 (교체 가능한 accessor)."""
    return HARMONY


# @nsteady_kr, https://www.instagram.com/p/DZg7EuIkhXY/ (2~16장, 2026-10-13 확인).
# 각 행은 상의 색과 어울리는 하의 4색이다. 나열 순서는 우열이 아니며 역방향으로
# 복제하지 않는다. 사진/문구는 저장하지 않고 배색 사실만 사용한다.
TOP_BOTTOM_COLORS = {
    "white": ("light_denim", "mid_denim", "beige", "khaki"),
    "burgundy": ("cream", "dark_denim", "beige", "black_denim"),
    "navy": ("khaki", "white", "light_denim", "brown"),
    "gray": ("white", "dark_denim", "beige", "black"),
    "brown": ("mid_denim", "beige", "white", "light_denim"),
    "light_green": ("white", "light_denim", "cream", "black_denim"),
    "black": ("white", "dark_denim", "cream", "gray"),
    "yellow": ("brown", "white", "dark_denim", "black_denim"),
    "purple": ("white", "dark_denim", "beige", "black_denim"),
    "mid_denim": ("black_denim", "gray", "dark_denim", "khaki"),
    "ivory": ("khaki", "beige", "light_denim", "brown"),
    "pink": ("beige", "white", "light_denim", "brown"),
    "charcoal": ("white", "light_denim", "khaki", "gray"),
    "red": ("white", "mid_denim", "beige", "light_denim"),
    "khaki": ("beige", "black_denim", "cream", "dark_denim"),
}

_SHADE_GROUPS = {
    "burgundy": ("red",), "charcoal": ("gray", "black"),
    "light_green": ("green",), "cream": ("ivory", "white"),
    "light_denim": ("blue",), "mid_denim": ("blue",),
    "dark_denim": ("navy", "blue"), "black_denim": ("black", "gray"),
    "khaki": ("khaki", "green"),
}
_SHADE_NAMES = {
    "burgundy": ("버건디", "버건디레드", "와인", "와인레드", "burgundy", "wine", "winered"),
    "charcoal": ("차콜", "차콜그레이", "charcoal", "charcoalgray", "charcoalgrey"),
    "light_green": ("라이트그린", "연두", "연두색", "lightgreen"),
    "cream": ("크림", "크림색", "크림아이보리", "cream", "creamivory"),
    "light_denim": ("연청", "연청색", "아이스진", "lightdenim", "lightwashdenim"),
    "mid_denim": ("중청", "중청색", "미디엄데님", "middenim", "mediumdenim"),
    "dark_denim": ("진청", "진청색", "딥인디고", "darkdenim", "darkwashdenim"),
    "black_denim": ("흑청", "흑청색", "블랙데님", "blackdenim"),
    "khaki": ("카키", "워시드카키", "올리브", "올리브그린", "khaki", "olive", "olivegreen"),
}
_DENIM_CATEGORIES = {"데님팬츠", "데님셔츠", "데님재킷", "데님자켓", "denim_pants", "denim_shirt", "denim_jacket"}
_LIGHT_DENIM_NAMES = {"라이트블루", "아이스블루", "lightblue", "iceblue"}


def matching_color(group: str | None, name: str | None = None, category: str | None = None) -> str | None:
    """스와치와 모순되지 않는 명시적 색 이름만 세분화한다. 저장 enum은 바꾸지 않는다."""
    group = group.strip().lower() if isinstance(group, str) else None
    if not group:
        return None
    normalized = re.sub(r"[\s_-]+", "", name.lower()) if isinstance(name, str) else ""
    for shade, names in _SHADE_NAMES.items():
        if normalized in names and group in _SHADE_GROUPS[shade]:
            return shade
    # 색 이름만으로 파란 옷을 데님으로 간주하지 않는다. 큐레이션 카테고리로 확정한다.
    if category in _DENIM_CATEGORIES:
        if group == "blue":
            return "light_denim" if normalized in _LIGHT_DENIM_NAMES else "mid_denim"
        if group == "navy":
            return "dark_denim"
        if group == "black":
            return "black_denim"
    return group


def outfit_color_score(product_color: str | None, item_color: str | None,
                       clothing_type: str, harmony: dict | None = None) -> float:
    """추천 배색은 최소 0.95, 나머지는 기존 조화 점수. 0.95는 제품의 랭킹 가중값이다."""
    scores = HARMONY if harmony is None else harmony
    product_group = _SHADE_GROUPS.get(product_color, (product_color,))[0]
    item_group = _SHADE_GROUPS.get(item_color, (item_color,))[0]
    fallback = scores.get((product_group, item_group), scores.get((item_group, product_group), 0.5))
    top, bottom = ((product_color, item_color) if clothing_type in ("top", "outer")
                   else (item_color, product_color))
    if clothing_type in ("top", "outer", "bottom") and bottom in TOP_BOTTOM_COLORS.get(top, ()):
        return max(0.95, fallback)
    return fallback
