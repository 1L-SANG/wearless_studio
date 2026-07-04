"""강조특징(셀링포인트) 정규화 — 셀러 자유텍스트 → canonical 영문 스타일링 큐.

목적(retrieval_upgrade_prd FR-D1): 셀러가 강조특징 칩에 자유 입력한 한국어를 이미지
프롬프트에 **그대로 넣지 않는다**. 운영자 큐레이션 alias 사전에서 canonical 영문 큐로
매핑하고, 매핑 안 되는 표현은 버린다(enforce) — 프롬프트 인젝션 표면 제거.

방식은 순수 결정적 lookup이다(임베딩 아님). `materials.py`의 alias 패턴을 강조특징
어휘로 확장한 것 — 같은 보안 논리(canonical 영문만 프롬프트에 들어감, 셀러 원문은
지시로 실행되지 않음). 순수 함수, IO 없음.

⚠️ 대상은 sellingPoints·aiSuggestedPoints뿐. 상품명·소재명은 대상 아님(계약).
사전은 시작값 — 운영자가 늘린다. 미매칭이 잦으면 사전을 키운다(임베딩 도입 아님).
"""

# ── canonical 영문 큐 → 셀러 표현 alias (한국어/영문) ──────────────────────────
# 큐 본문은 '스타일링 방향'이지 사실 단정이 아니다(비침 없음·기능성 등 단정 회피 —
# 이미지 렌더 지시로만 씀. 카피 단정 금지는 AG-02 몫, 계약 §AG-02).
_CUES = {
    "soft, smooth hand-feel": ["부들부들", "부드러운 촉감", "부드러운", "소프트한", "촉감이 좋은"],
    "comfortable stretch that moves with the body": ["신축성", "잘 늘어나", "스판", "편한 착용감", "활동성", "스트레치"],
    "breathable, cool summer fabric": ["시원한", "쿨링", "통기성", "시원하게", "여름용", "썸머"],
    "warm, cozy insulation": ["보온", "따뜻한", "따뜻하게", "기모", "포근한"],
    "flattering, body-skimming silhouette": ["핏이 예쁜", "라인이 예쁜", "라인을 살리", "슬림한", "날씬해 보이", "핏이 좋은"],
    "relaxed oversized fit": ["오버핏", "루즈핏", "루즈한", "여유로운 핏", "박시한"],
    "clean, minimal design": ["베이직", "심플한", "미니멀", "깔끔한", "데일리"],
    "premium, elegant look": ["고급스러운", "우아한", "럭셔리", "세련된", "고급진"],
    "lightweight feel": ["가벼운", "경량", "가볍게"],
    "wrinkle-resistant, easy care": ["구김 없는", "구김이 적은", "관리가 편한", "이지케어"],
    "rendered as opaque, no see-through": ["비침 없는", "안비침", "비침없는", "커버력"],
    "rich, vivid color": ["발색", "색감이 좋은", "선명한 컬러", "컬러감"],
}

_ALIAS_TO_CUE = {a.lower(): cue for cue, aliases in _CUES.items() for a in aliases}
# 부분일치 안전: 한글 ≥2자 / 라틴 ≥3자, 긴 alias 우선(짧은 오탐 방지 — materials.py §110 교훈)
_SUBSTR_ALIASES = sorted(
    (a for a in _ALIAS_TO_CUE if (len(a) >= 3 if a.isascii() else len(a) >= 2)),
    key=len,
    reverse=True,
)


def _normalize(text: str) -> str:
    return " ".join((text or "").split()).strip().lower()


def _lookup(point: str) -> str | None:
    """강조특징 1개 → canonical 큐. 전체 exact → 안전 부분일치(긴 alias 우선). 없으면 None."""
    s = _normalize(point)
    if not s:
        return None
    if s in _ALIAS_TO_CUE:
        return _ALIAS_TO_CUE[s]
    for alias in _SUBSTR_ALIASES:
        if alias in s:
            return _ALIAS_TO_CUE[alias]
    return None


def canonicalize(points: list) -> tuple[list[str], list[str]]:
    """강조특징 리스트 → (matched canonical 큐, unmatched 원문).

    - matched: 중복 제거, 첫 등장 순서 유지(결정적).
    - unmatched: 사전에 없는 원문 그대로(호출부가 shadow 로그·enforce 폐기에 씀).
    같은 큐로 매핑되는 표현이 여러 개면 큐 1개로 합쳐진다.
    """
    matched: list[str] = []
    unmatched: list[str] = []
    seen: set[str] = set()
    for p in points or []:
        if not isinstance(p, str) or not p.strip():
            continue
        cue = _lookup(p)
        if cue is None:
            unmatched.append(p)
        elif cue not in seen:
            seen.add(cue)
            matched.append(cue)
    return matched, unmatched
