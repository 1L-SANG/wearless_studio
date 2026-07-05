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

from .matching import prefilter

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
    def _score(item: dict) -> float:
        tags = item.get("style_tags") or []
        total = 0.0
        for pt in product_tags:
            for it in tags:
                total += affinity_map.get((pt, it), affinity_map.get((it, pt), 0))
        return total

    scored = [(item, _score(item)) for item in items]
    scored.sort(key=lambda pair: (-pair[1], pair[0][tie_break]))
    return [item for item, _ in scored]


def recommend_v1(
    items: list[dict],
    clothing_type: str,
    genders: list[str],
    product_tags: list[str],
    affinity_map: dict,
    limit: int | None = None,
) -> list[dict]:
    """매칭 후보 추천 v1 — 프리필터(matching.py 승계) + 태그 친화도 랭킹 (FR-A1/FR-A2).

    프리필터는 `matching.recommend()`와 완전히 동일한 조건이다(같은 소스에서
    `complementary_type`을 import해 재사용) — 보완타입(top/outer/dress→bottom,
    그 외→top) + is_active + 성별(unisex는 항상 포함). 이 단계에서 절대
    보완타입이 아닌 항목(예: clothing_type="top" 입력에 top 항목)이 살아남지 않는다.

    그 다음 살아남은 풀만 `rank_by_style_affinity`로 재정렬한다 — 랭킹은
    프리필터 통과 풀 내부에서만 일어난다(FR-A2 "벡터든 태그든 랭킹은 프리필터
    통과 풀 내부에서만").
    """
    pool = prefilter(items, clothing_type, genders)
    ranked = rank_by_style_affinity(pool, product_tags, affinity_map)
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
