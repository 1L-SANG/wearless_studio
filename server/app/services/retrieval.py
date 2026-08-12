"""검색 증강(RAG) 코어 — retrieval_upgrade_prd Phase 1a/2a DETERMINISTIC v1.

순수 함수만 담는다: DB·IO·임베딩 호출 없음(NFR-5, FR-A5 — 요청 중 임베딩 금지).
설계 원칙(결정성 우선 §1.3, 매칭=보완성이지 유사성 아님 §2.4)을 그대로 따른다.

- rank_by_style_affinity / recommend_v1: FR-A1/FR-A2 (매칭 의류 스타일 태그 친화도 랭킹).
  프리필터(보완타입·isActive·성별)는 `matching.py`의 로직을 그대로 재사용한다 — 절대
  이 모듈에서 다시 구현하지 않는다(드리프트 방지, FR-A2 "불변").
- select_kb_static: FR-B1 (지식 블록 카테고리/styleTags 키 기반 정적 선택).
- build_retrieval_log: NFR-4 관측 로깅 — 호출부가 {kind, corpus_version, k, latency_ms,
  flag_state}를 만들 때 쓰는 공통 헬퍼.

tie-break은 전부 `id` 오름차순으로 고정한다(NFR-1 결정성).
"""

import logging

from .matching import diversify_top_two, prefilter

logger = logging.getLogger(__name__)


def rank_by_style_affinity(
    items: list[dict],
    product_tags: list[str],
    affinity_map: dict,
    *,
    tie_break: str = "id",
) -> list[dict]:
    """styleTags 친화도로 items를 랭킹한다 (FR-A1 — 결정적 v1).

    각 item의 score = product_tags × item["style_tags"] 모든 쌍에 대해
    affinity_map에서 찾은 점수의 합.

    affinity_map 키 규약: `(tag_a, tag_b)` 튜플 키이며 **비대칭 저장을 허용**한다 —
    조회 시 `(pt, it)`를 먼저 찾고 없으면 `(it, pt)`를 찾아 대칭으로 취급한다
    (한쪽만 채운 맵도 정상 동작). 두 방향 모두 없으면 0점.

    정렬: score 내림차순, 동점이면 `item[tie_break]`(기본 "id") 오름차순 —
    이 두 키 외의 다른 기준으로는 절대 재정렬하지 않는다(NFR-1 결정성).
    """
    scored = [
        (item, _style_affinity_score(item, product_tags, affinity_map))
        for item in items
    ]
    scored.sort(key=lambda pair: (-pair[1], pair[0][tie_break]))
    return [item for item, _ in scored]


def _style_affinity_score(item: dict, product_tags: list[str], affinity_map: dict) -> float:
    """한 후보의 스타일 친화도 합. 단독·결합 랭킹이 같은 계산을 공유한다."""
    total = 0.0
    for product_tag in product_tags:
        for item_tag in item.get("style_tags") or []:
            total += affinity_map.get(
                (product_tag, item_tag),
                affinity_map.get((item_tag, product_tag), 0),
            )
    return total


def _color_harmony_score(product_color: str, item_color: str | None, harmony: dict) -> float:
    """색 조화 대칭 조회. 미등재·미상 색은 중립 0.5로 안전하게 폴백한다."""
    if not item_color:
        return 0.5
    return harmony.get(
        (product_color, item_color),
        harmony.get((item_color, product_color), 0.5),
    )


def recommend_v1(
    items: list[dict],
    clothing_type: str,
    genders: list[str],
    product_tags: list[str],
    affinity_map: dict,
    limit: int | None = None,
    product_color: str | None = None,
    harmony: dict | None = None,
    color_weight: float = 0.3,
) -> list[dict]:
    """매칭 후보 추천 v1 — 프리필터 + 스타일·색 조화 결합 랭킹.

    프리필터는 `matching.recommend()`와 완전히 동일한 조건이다(같은 소스에서
    `complementary_type`을 import해 재사용) — 보완타입(top/outer/dress→bottom,
    그 외→top) + is_active + 성별(unisex는 항상 포함). 이 단계에서 절대
    보완타입이 아닌 항목(예: clothing_type="top" 입력에 top 항목)이 살아남지 않는다.

    상품색이 있으면 스타일 합을 풀의 최댓값으로 나눈 뒤 색 조화와 결합한다:
    ``(1-w) * style_norm + w * color_score``. 상품색이 없거나 ``w=0``이면
    기존 `rank_by_style_affinity` 경로를 그대로 사용해 순서 회귀를 막는다.

    랭킹은 프리필터 통과 풀 내부에서만 일어나며, 결합 뒤에도 동점은 id 오름차순이다.
    `diversify_top_two`는 최종 결합 정렬 뒤에 기존대로 적용한다.
    """
    pool = prefilter(items, clothing_type, genders)
    # 색 정보 없음과 운영 롤백 스위치(0)는 계산식의 우연에 기대지 않고 기존 경로를
    # 그대로 탄다. 음수 설정도 안전하게 0으로 취급한다.
    if product_color is None or color_weight <= 0:
        ranked = rank_by_style_affinity(pool, product_tags, affinity_map)
    else:
        style_scored = [
            (item, _style_affinity_score(item, product_tags, affinity_map))
            for item in pool
        ]
        max_style = max((score for _, score in style_scored), default=0.0)
        weight = min(color_weight, 1.0)
        harmony_scores = harmony or {}
        combined = [
            (
                item,
                (1 - weight) * (style_score / max_style if max_style > 0 else 0.0)
                + weight * _color_harmony_score(
                    product_color, item.get("color_group"), harmony_scores
                ),
            )
            for item, style_score in style_scored
        ]
        combined.sort(key=lambda pair: (-pair[1], pair[0]["id"]))
        ranked = [item for item, _ in combined]
    ranked = diversify_top_two(ranked)
    return ranked[:limit] if limit is not None else ranked  # limit=0 → 빈 결과(0은 falsy 방지)


def select_kb_static(chunks: list[dict], keys: dict) -> list[dict]:
    """지식 청크 정적 선택 — 카테고리/styleTags 키 매칭 (FR-B1 — 결정적 v1).

    chunk는 `keys` jsonb 필드(dict)를 가진다: {"category": str|None,
    "styleTags": list[str]|None} 형태. query `keys`도 동일 shape.

    선택 규칙(OR): chunk가 선택되는 것은 다음 중 하나라도 참일 때다.
      1. chunk["keys"]["category"]가 query["category"]와 같다(둘 다 존재·비어있지 않을 때).
      2. chunk["keys"]["styleTags"]와 query["styleTags"]가 교집합을 가진다(하나라도 겹치면 선택).
    두 매처 키 모두 없거나 매칭에 실패하면 선택되지 않는다.

    정렬: `id` 오름차순 고정(NFR-1 결정성) — version/priority로 재정렬하지 않는다.
    """
    q_category = keys.get("category")
    q_style_tags = set(keys.get("styleTags") or [])

    def _matches(chunk: dict) -> bool:
        c_keys = chunk.get("keys") or {}
        c_category = c_keys.get("category")
        if q_category and c_category and q_category == c_category:
            return True
        c_style_tags = set(c_keys.get("styleTags") or [])
        if q_style_tags and c_style_tags and (q_style_tags & c_style_tags):
            return True
        return False

    selected = [chunk for chunk in chunks if _matches(chunk)]
    selected.sort(key=lambda chunk: chunk["id"])
    return selected


def build_retrieval_log(
    kind: str,
    corpus_version: str | int,
    k: int,
    latency_ms: int,
    flag_state: str,
) -> dict:
    """검색 호출 관측 로그 dict를 만들고 로깅한다 (NFR-4).

    shape: {"kind", "corpus_version", "k", "latency_ms", "flag_state"}.
    flag_state는 호출부(라우트/서비스)가 현재 활성 flag 값(예: "off"|"tags"|"vector")을
    넘긴다 — 이 헬퍼는 flag를 스스로 읽지 않는다(config 의존 없음 = 순수 함수 유지).
    """
    log = {
        "kind": kind,
        "corpus_version": corpus_version,
        "k": k,
        "latency_ms": latency_ms,
        "flag_state": flag_state,
    }
    logger.info("retrieval_call", extra=log)
    return log


def cosine(a: list[float], b: list[float]) -> float:
    """코사인 유사도. 벡터가 L2 정규화돼 있으면 내적과 동일하지만, 미정규화 입력도
    안전하게 처리한다(0 벡터는 0.0). 순수 함수 — 테스트·폴백 랭킹용."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def rank_ref_images_by_vector(
    rows: list[dict],
    query_vec: list[float],
    *,
    k: int | None = None,
    tie_break: str = "id",
) -> list[dict]:
    """레퍼런스 컷 벡터 랭킹 — Phase 3 (FR-C, 결정적 정렬).

    프리필터(cut_type·clothing_type·gender·is_active)는 **호출부(DB 쿼리)**가 이미 적용한
    풀만 넘어온다는 전제다(FR-A2 원칙: 프리필터 불변, 랭킹은 통과 풀 내부에서만). 이 함수는
    순수 정렬만 담당하며 DB·임베딩 호출이 없다(실 검색은 repo.search_ref_images 의 pgvector).

    각 row 는 `embedding`(list[float]) 을 가진다. 정렬: 코사인 내림차순, 동점이면
    `row[tie_break]`(기본 'id') 오름차순 — 이 두 키 외로는 재정렬하지 않는다(NFR-1 결정성).
    embedding 이 없거나 차원 불일치면 유사도 0.0 으로 최하위."""
    scored = [(r, cosine(query_vec, r.get("embedding") or [])) for r in rows]
    scored.sort(key=lambda pair: (-pair[1], pair[0].get(tie_break, "")))
    ranked = [r for r, _ in scored]
    return ranked[:k] if k is not None else ranked
