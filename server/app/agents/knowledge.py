"""정적 지식블록 주입 — 카테고리/styleTags 기반 큐레이션 스타일링 가이드 (feature 2a).

목적(retrieval_upgrade_prd FR-B1/2a): 이미지 모델에게 "이 카테고리/스타일은 이렇게
구도·실루엣을 잡아라"라는 운영자 큐레이션 영문 가이드를 결정적으로 골라 주입한다.
셀러 자유텍스트는 전혀 관여하지 않는다 — 대상은 상품 category(clothing_type +
subCategory)와 analysis.styleTags뿐이며, 둘 다 이미 서버가 정규화한 구조적 필드다
(자유 텍스트 아님 → selling_points.py의 alias 룩업과 달리 canonicalize 불필요).

방식은 순수 결정적 lookup이다(임베딩 아님). 선택 로직 자체는 `retrieval.py`의
`select_kb_static`을 그대로 재사용한다(재구현 금지 — 드리프트 방지, FR-B1 "불변").
이 모듈은 (1) 큐레이션 청크 시드 데이터, (2) 카테고리/styleTags 키를 만들어
`select_kb_static`을 호출하는 얇은 래퍼(`select`)만 담당한다. 순수 함수, IO 없음.

⚠️ body_en은 "사실 단정"이 아니라 "구도/스타일링 방향"이다(소재 원산지·기능성 등
단정 금지 — 이미지 렌더 지시로만 씀). 청크는 시작값이며, 운영자가 늘린다.
"""

from ..services import retrieval

# ── 큐레이션 지식 청크 (id 오름차순 유지 — 가독성용, 정렬 자체는 select_kb_static 몫) ──
# id 네이밍: kb-<category|tag>-NN. keys.category는 clothing_type 값(top/bottom/outer/
# dress)과 매칭, keys.styleTags는 analysis.styleTags 값(예: basic/daily/formal/sporty)과
# 교집합 매칭. body_en은 전부 구도/실루엣/드레이프 프레젠테이션 지시문(영문, 인젝션 안전
# — 운영자가 직접 작성한 고정 문자열만 존재, 셀러 입력이 섞이지 않는다).
_CHUNKS: list[dict] = [
    {
        "id": "kb-top-01",
        "keys": {"category": "top", "styleTags": None},
        "body_en": "Frame the torso straight-on to keep shoulder seams and neckline symmetrical.",
    },
    {
        "id": "kb-bottom-01",
        "keys": {"category": "bottom", "styleTags": None},
        "body_en": "Show the full leg line from waistband to hem so the silhouette reads at a glance.",
    },
    {
        "id": "kb-outer-01",
        "keys": {"category": "outer", "styleTags": None},
        "body_en": "Let the outer layer hang open enough to reveal drape and layering depth over the base garment.",
    },
    {
        "id": "kb-dress-01",
        "keys": {"category": "dress", "styleTags": None},
        "body_en": "Capture the dress in one continuous vertical line from neckline to hem to emphasize overall silhouette.",
    },
    {
        "id": "kb-style-basic-01",
        "keys": {"category": None, "styleTags": ["basic", "daily"]},
        "body_en": "Keep the pose neutral and the framing clean — no dramatic angles, let the garment be the focus.",
    },
    {
        "id": "kb-style-formal-01",
        "keys": {"category": None, "styleTags": ["formal"]},
        "body_en": "Favor an upright, composed stance and crisp fabric lines to convey a polished, formal presentation.",
    },
    {
        "id": "kb-style-sporty-01",
        "keys": {"category": None, "styleTags": ["sporty"]},
        "body_en": "Use a dynamic, slightly athletic stance that highlights ease of movement and functional fit.",
    },
    {
        "id": "kb-style-romantic-01",
        "keys": {"category": None, "styleTags": ["romantic"]},
        "body_en": "Emphasize soft fabric flow and gentle curves with a relaxed, graceful pose.",
    },
]


def select(category: str, style_tags: list[str]) -> list[str]:
    """category + style_tags → 선택된 큐레이션 body_en 리스트 (결정적 순서).

    내부적으로 `retrieval.select_kb_static`을 그대로 재사용한다 — 이 함수는
    query keys dict를 만드는 얇은 어댑터일 뿐, 매칭·정렬 로직을 다시 구현하지 않는다.
    """
    keys = {"category": category or None, "styleTags": list(style_tags or [])}
    selected = retrieval.select_kb_static(_CHUNKS, keys)
    return [chunk["body_en"] for chunk in selected]
