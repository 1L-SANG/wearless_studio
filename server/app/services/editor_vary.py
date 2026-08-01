"""Editor vary 요청 계약 — 허용 change type과 **서버가 유도하는** 의미 범위 (순수 함수).

에디터의 `changes[]` 는 지금까지 프롬프트 조립용 자유 목록이었다(cut_variator 는 미상
type 도 관대하게 통과시킨다 — 프롬프트에는 그게 맞다). 하지만 판정 계약으로 쓰려면 그
관대함이 위험해진다: 알 수 없는 type 을 통과시키면 "무엇을 요청했는지" 자체가 불명확한데
QC 는 그것을 기준으로 위반을 세야 한다.

그래서 **판정 경로에서만** 엄격하게 검증한다. 프롬프트 조립은 기존 동작 그대로다.

label 은 표시용이라 판정에 쓰지 않는다. value 는 프롬프트에 들어갈 때 기존 sanitize 를
거치고, 여기서는 길이·타입만 본다 — 자유 텍스트의 **의미**를 서버가 해석하지 않는다.
"""

# 에디터 UI 가 실제로 보내는 type (cut_variator._TYPE_LABEL 과 같은 집합).
ALLOWED_CHANGE_TYPES = ("direction", "shot", "pose", "face", "bg")

MAX_CHANGES = 4          # UI 칩 개수 상한 — 그 이상은 요청 오류로 본다
MAX_VALUE_LEN = 200

# change type → 그 요청이 **바꿔도 되는** 의미 항목(Vision 관찰 이름).
# 요청하지 않은 의류 구조·패턴·로고는 어떤 조합에서도 여기 들어오지 않는다.
_CHANGE_SEMANTICS = {
    "direction": ("cameraChanged",),
    "shot": ("framingChanged",),
    "pose": ("poseChanged",),
    "face": ("poseChanged",),        # 표정은 별도 관찰 항목이 없다 — 얼굴/포즈로 묶인다
    "bg": ("backgroundChanged", "lightingChanged"),
}

# 어떤 vary 에서도 잠긴다. 상품 자체가 달라지는 변화는 "비슷한 컷"의 범위가 아니다.
ALWAYS_LOCKED_OBSERVATIONS = (
    "collarChanged", "sleevesChanged", "buttonsChanged", "pocketsChanged",
    "patternChanged", "logoChanged", "mannequinIdentityChanged",
)


class VaryRequestError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def validate_changes(changes) -> list[dict]:
    """판정 경로용 검증 → 정규화된 [{type, value}]. 위반은 VaryRequestError.

    `changes=[]`("비슷한 컷")는 유효하다 — 다만 무엇이 바뀌어도 되는지 서버가 모르므로
    허용 범위가 비고, 그 결과는 자동 통과 대상이 아니다(CUSTOM_REVIEW_REQUIRED).
    """
    if changes is None:
        return []
    if not isinstance(changes, list):
        raise VaryRequestError("invalid_changes", "변경 요청 형식이 올바르지 않아요.")
    if len(changes) > MAX_CHANGES:
        raise VaryRequestError("too_many_changes",
                               f"변경 항목은 최대 {MAX_CHANGES}개까지예요.")
    out: list[dict] = []
    seen: set[str] = set()
    for c in changes:
        if not isinstance(c, dict):
            raise VaryRequestError("invalid_change", "변경 항목 형식이 올바르지 않아요.")
        ctype = c.get("type")
        if ctype not in ALLOWED_CHANGE_TYPES:
            raise VaryRequestError("unknown_change_type",
                                   f"지원하지 않는 변경 항목이에요: {ctype!r}")
        if ctype in seen:
            # 같은 축을 두 번 지시하면 어느 쪽이 요청인지 서버가 정할 수 없다.
            raise VaryRequestError("duplicate_change_type",
                                   f"{ctype} 변경이 중복됐어요.")
        seen.add(ctype)
        value = c.get("value")
        if value is not None and not isinstance(value, str):
            raise VaryRequestError("invalid_change_value", "변경 값 형식이 올바르지 않아요.")
        if isinstance(value, str) and len(value) > MAX_VALUE_LEN:
            raise VaryRequestError("change_value_too_long", "변경 값이 너무 길어요.")
        out.append({"type": ctype, "value": value})
    return out


def semantic_scope(changes: list[dict]) -> dict:
    """서버가 유도하는 의미 허용/금지 범위. **클라이언트는 여기 관여하지 않는다.**

    반환 형태는 edit_intent_qc 가 아는 관찰 이름 공간이다(…Changed).
    """
    allowed: list[str] = []
    for c in changes or ():
        allowed.extend(_CHANGE_SEMANTICS.get(c.get("type"), ()))
    allowed = sorted(set(allowed))
    forbidden = sorted(set(ALWAYS_LOCKED_OBSERVATIONS) | (
        {"cameraChanged", "framingChanged", "poseChanged",
         "backgroundChanged", "lightingChanged"} - set(allowed)))
    return {"requestedTypes": sorted({c["type"] for c in changes or ()}),
            "allowedObservations": allowed,
            "forbiddenObservations": forbidden}


def edit_type_for(changes: list[dict]) -> str:
    """vary 요청 → edit type.

    기존 edit type 으로 **정확히** 표현되는 경우에만 그것을 쓴다. bg 단독은
    BACKGROUND_ONLY 와 같은 요청이다. direction·shot·pose·face 나 복합 변경은 지금
    파이프라인이 정량으로 검증할 수 없으므로 지원하는 척 축소하지 않고
    CUSTOM_REVIEW_REQUIRED 로 둔다(자동 통과 금지).
    """
    types = {c["type"] for c in changes or ()}
    if types == {"bg"}:
        return "BACKGROUND_ONLY"
    return "CUSTOM_REVIEW_REQUIRED"


def entailed_metrics(changes: list[dict]) -> tuple[str, ...]:
    """요청이 **필연적으로** 끌고 가는 정량 지표 — 드리프트로 세지 않는다.

    구도를 바꿔 달라고 했으면 bbox 도 실루엣도 달라지는 게 정상이다. 그걸 위반으로 세면
    요청대로 된 결과가 전부 reject 된다(마네킹 편집에서 실측으로 배운 것과 같은 함정).
    """
    types = {c["type"] for c in changes or ()}
    out: set[str] = set()
    if types & {"shot"}:
        out |= {"subjectHeight", "centerX", "centerY", "bodyWidth", "shoulderWidth"}
    if types & {"direction", "pose"}:
        out |= {"bodyWidth", "shoulderWidth", "centerX", "cuffY", "hemY"}
    if "bg" in types:
        out |= {"backgroundDeltaE"}
    return tuple(sorted(out))
