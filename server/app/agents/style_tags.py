"""StyleTag 닫힌 enum — 단일 정본 (common_data_contract §4 · retrieval_upgrade_prd).

AG-01(product-analyst) 출력의 `styleTags`, 매칭 랭킹(`style_affinity`)의 키, 그리고
그 둘을 검증하는 곳이 **모두 이 튜플 하나만** 참조한다. 새 태그는 여기에 추가하고
계약 §4 StyleTag 행을 함께 갱신한다(운영자 확장).

부트스트랩 정본 = `style_affinity.AFFINITY` 의 (깨끗한) 키 집합. 오염 시드
(seedMatchingItems.js 의 한글 '스트라이프'·'깅엄 체크' 등)는 쓰지 않는다.

순수 상수만 — IO·DB·모델 호출 없음.
"""

# 닫힌 enum (계약 §4). 앞 8개 = style_affinity 부트스트랩 정본, 뒤 16개 = 운영자 확장
# (한국 이커머스 흔한 스타일 방향). 모두 소문자 영문 토큰. 새 태그는 여기 + 계약 §4에 함께 추가.
STYLE_TAGS: tuple[str, ...] = (
    # 부트스트랩 8 (style_affinity 키 정본)
    "basic",
    "daily",
    "minimal",
    "casual",
    "formal",
    "classic",
    "sporty",
    "trendy",
    # 운영자 확장 16
    "street",
    "chic",
    "feminine",
    "lovely",
    "romantic",
    "vintage",
    "retro",
    "modern",
    "luxury",
    "preppy",
    "workwear",
    "athleisure",
    "cozy",
    "unique",
    "sophisticated",
    "y2k",
)

STYLE_TAG_SET: frozenset[str] = frozenset(STYLE_TAGS)


def is_style_tag(value: str) -> bool:
    """value 가 계약 enum 토큰이면 True (검증·필터용)."""
    return value in STYLE_TAG_SET
