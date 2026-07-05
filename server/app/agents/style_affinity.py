"""스타일 태그 친화도 시드맵 — retrieval_upgrade_prd FR-A1 결정적 v1(임베딩 아님).

목적: 매칭의류 랭킹(`retrieval.rank_by_style_affinity`)이 참조하는 "어떤 스타일
태그 쌍이 잘 어울리는가"를 순수 정적 lookup으로 제공한다. 셀러 자유텍스트 →
canonical 큐로 매핑하는 `selling_points.py`와 같은 결정론적 사전 패턴을 따른다:
호출 시점 계산·모델 추론 없이 시작값 사전을 하드코딩하고, 운영자가 필요에 따라
늘려간다(§1.3 결정성 우선).

시드는 `server/seed/matching_items.json`에 실제로 등장하는 태그(basic, daily,
formal, sporty, minimal, casual 등)를 우선으로 채운다. 값은 [0, 1] 구간의 조화
점수이며, 이 모듈은 한쪽 방향만 채워도 된다 — 대칭 조회는
`retrieval.rank_by_style_affinity`가 `(pt, it)` 실패 시 `(it, pt)`를 재조회하는
방식으로 처리한다(이 맵에서 양방향을 중복 저장하지 않는다).

순수 함수/상수만 담는다. IO·DB·임베딩 호출 없음.
"""

AFFINITY: dict[tuple[str, str], float] = {
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
}


def affinity_map() -> dict:
    """현재 활성 친화도 맵을 반환한다 (교체 가능한 accessor — FR-A1).

    호출부(routes 등)는 이 함수를 통해서만 맵을 얻는다 — `AFFINITY`를 직접
    import하지 않음으로써, 추후 시드를 DB/파일 기반으로 교체해도 호출부 변경이
    없다.
    """
    return AFFINITY
