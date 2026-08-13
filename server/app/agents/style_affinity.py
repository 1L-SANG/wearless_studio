"""스타일 태그 친화도 시드맵 — retrieval_upgrade_prd FR-A1 결정적 v1(임베딩 아님).

목적: 매칭의류 랭킹(`retrieval.rank_by_style_affinity`)이 참조하는 "어떤 스타일
태그 쌍이 잘 어울리는가"를 순수 정적 lookup으로 제공한다. 셀러 자유텍스트 →
canonical 큐로 매핑하는 `selling_points.py`와 같은 결정론적 사전 패턴을 따른다:
호출 시점 계산·모델 추론 없이 시작값 사전을 하드코딩하고, 운영자가 필요에 따라
늘려간다(§1.3 결정성 우선).

기존 부트스트랩 8개 태그의 15쌍을 유지하면서 `style_tags.STYLE_TAGS` 24개 전체가
적어도 한 쌍에 등장하도록 의미군별로 확장한다. 값은 [0, 1] 구간의 조화 점수이며,
이 모듈은 한쪽 방향만 채워도 된다 — 대칭 조회는
`retrieval.rank_by_style_affinity`가 `(pt, it)` 실패 시 `(it, pt)`를 재조회하는
방식으로 처리한다(이 맵에서 양방향을 중복 저장하지 않는다).

순수 함수/상수만 담는다. IO·DB·임베딩 호출 없음.
"""

AFFINITY: dict[tuple[str, str], float] = {
    # 기존 부트스트랩 15쌍 — 값 유지
    ("basic", "daily"): 0.9,
    ("basic", "minimal"): 0.85,
    ("basic", "casual"): 0.75,
    ("formal", "minimal"): 0.8,
    ("formal", "classic"): 0.85,
    ("sporty", "casual"): 0.8,
    ("sporty", "daily"): 0.6,
    ("minimal", "casual"): 0.7,
    ("daily", "casual"): 0.85,
    ("trendy", "casual"): 0.65,
    ("trendy", "daily"): 0.55,
    ("formal", "daily"): 0.3,
    ("sporty", "formal"): 0.15,
    ("minimal", "trendy"): 0.5,
    ("basic", "trendy"): 0.45,

    # 일상·미니멀·모던 계열
    ("minimal", "modern"): 0.92,
    ("minimal", "sophisticated"): 0.82,
    ("minimal", "chic"): 0.78,
    ("modern", "sophisticated"): 0.9,
    ("modern", "chic"): 0.85,
    ("modern", "luxury"): 0.72,
    ("basic", "modern"): 0.72,
    ("daily", "cozy"): 0.88,
    ("casual", "cozy"): 0.9,
    ("minimal", "cozy"): 0.65,

    # 스트리트·트렌드·복고 계열
    ("street", "trendy"): 0.9,
    ("street", "y2k"): 0.88,
    ("street", "unique"): 0.82,
    ("street", "casual"): 0.82,
    ("y2k", "trendy"): 0.92,
    ("y2k", "unique"): 0.85,
    ("y2k", "retro"): 0.8,
    ("unique", "trendy"): 0.83,
    ("vintage", "retro"): 0.9,
    ("retro", "trendy"): 0.72,

    # 스포티·애슬레저 계열
    ("sporty", "athleisure"): 0.95,
    ("athleisure", "casual"): 0.88,
    ("athleisure", "daily"): 0.78,
    ("athleisure", "street"): 0.75,
    ("sporty", "street"): 0.7,

    # 페미닌·로맨틱·시크 계열
    ("feminine", "lovely"): 0.9,
    ("feminine", "romantic"): 0.92,
    ("feminine", "chic"): 0.78,
    ("feminine", "sophisticated"): 0.82,
    ("feminine", "luxury"): 0.72,
    ("lovely", "romantic"): 0.88,
    ("romantic", "vintage"): 0.78,
    ("chic", "luxury"): 0.86,
    ("chic", "sophisticated"): 0.9,

    # 클래식·포멀·프레피·워크웨어 계열
    ("classic", "sophisticated"): 0.92,
    ("classic", "luxury"): 0.86,
    ("formal", "sophisticated"): 0.92,
    ("formal", "luxury"): 0.84,
    ("formal", "chic"): 0.8,
    ("formal", "workwear"): 0.86,
    ("workwear", "classic"): 0.82,
    ("workwear", "modern"): 0.8,
    ("preppy", "classic"): 0.88,
    ("preppy", "casual"): 0.74,
    ("preppy", "vintage"): 0.72,
}


def affinity_map() -> dict:
    """현재 활성 친화도 맵을 반환한다 (교체 가능한 accessor — FR-A1).

    호출부(routes 등)는 이 함수를 통해서만 맵을 얻는다 — `AFFINITY`를 직접
    import하지 않음으로써, 추후 시드를 DB/파일 기반으로 교체해도 호출부 변경이
    없다.
    """
    return AFFINITY
