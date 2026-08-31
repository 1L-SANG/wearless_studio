"""FaceMarket 모델 physique(체형·키) 단일 소스 — enum·라벨·프롬프트 문구·검증·블록 빌더.
값→라벨(ko, UI)·문구(en, 프롬프트) 매핑을 여기서만 소유한다. 프롬프트엔 자유문자열을 절대
방출하지 않는다(검증된 enum→고정 문구만; fit_axes.build_fit_profile_block 관례)."""
from __future__ import annotations
from collections.abc import Mapping

GENDERS: tuple[str, ...] = ("male", "female")

HEIGHT_BUCKETS: dict[str, tuple[str, ...]] = {
    "male": ("m_lt170", "m_170_175", "m_175_180", "m_180_185", "m_185_190", "m_gte190"),
    "female": ("f_lt155", "f_155_160", "f_160_165", "f_165_170", "f_170_175", "f_gte175"),
}

#: 단일 축(살집)만 표현하던 원래 7종. 남성 목록과 기존 저장값이 계속 이 값을 쓴다.
#: `glamorous` 는 사실 살집이 아니라 분포라 축이 섞여 있었다 — 아래 매트릭스 값이 그 자리를
#: `regular_both` 로 정확히 대체한다(기존 행 이전은 하지 않는다. 값은 계속 유효하다).
_BODY_TYPES_FLAT: tuple[str, ...] = (
    "delicate", "slim", "regular", "plump", "toned", "bulk", "glamorous",
)

#: 볼륨(살집) × 실루엣(볼륨이 어디에 몰렸나) 매트릭스. 여성 목록이 쓴다.
#: "마른데 상체가 있는" 같은 조합은 단일 축으로는 표현이 안 된다 — 마름을 고르면 상체 정보가
#: 사라지고 glamorous 를 고르면 살집이 과장됐다. 값 하나에 두 축을 담아 컬럼 추가를 피한다.
#: plump_both 는 없다 — 통통은 이미 전체 볼륨이라 그 칸이 옆 칸과 시각적으로 갈리지 않는다.
_BODY_VOLUMES: tuple[str, ...] = ("delicate", "slim", "regular", "plump")
_BODY_SHAPES: tuple[str, ...] = ("basic", "upper", "hip", "both")
_BODY_TYPES_MATRIX: tuple[str, ...] = tuple(
    f"{volume}_{shape}"
    for volume in _BODY_VOLUMES
    for shape in _BODY_SHAPES
    if not (volume == "plump" and shape == "both")
)

BODY_TYPES: tuple[str, ...] = _BODY_TYPES_FLAT + _BODY_TYPES_MATRIX

# 값 → (한국어 UI 라벨, 영문 프롬프트 문구)
_HEIGHT_LABELS: dict[str, tuple[str, str]] = {
    "m_lt170": ("170cm 미만", "under 170 cm tall"),
    "m_170_175": ("170–175cm", "approximately 170–175 cm tall"),
    "m_175_180": ("175–180cm", "approximately 175–180 cm tall"),
    "m_180_185": ("180–185cm", "approximately 180–185 cm tall"),
    "m_185_190": ("185–190cm", "approximately 185–190 cm tall"),
    "m_gte190": ("190cm 이상", "190 cm or taller"),
    "f_lt155": ("155cm 미만", "under 155 cm tall"),
    "f_155_160": ("155–160cm", "approximately 155–160 cm tall"),
    "f_160_165": ("160–165cm", "approximately 160–165 cm tall"),
    "f_165_170": ("165–170cm", "approximately 165–170 cm tall"),
    "f_170_175": ("170–175cm", "approximately 170–175 cm tall"),
    "f_gte175": ("175cm 이상", "175 cm or taller"),
}
_BODY_LABELS: dict[str, tuple[str, str]] = {
    "delicate": ("여리여리", "a delicate, slender build"),
    "slim": ("마름", "a slim build"),
    "regular": ("보통", "an average build"),
    "plump": ("통통", "a fuller, soft build"),
    "toned": ("잔잔한 근육", "a lean, lightly toned build"),
    "bulk": ("벌크업", "a muscular, bulked-up build"),
    "glamorous": ("글래머러스", "a curvy, glamorous build"),
}

# 매트릭스 값의 라벨·문구는 두 축을 조합해 만든다(수작업 30줄을 두 표로 줄인다).
_VOLUME_PARTS: dict[str, tuple[str, str]] = {
    "delicate": ("여리여리", "a delicate, slender build"),
    "slim": ("마름", "a slim build"),
    "regular": ("보통", "an average build"),
    "plump": ("통통", "a fuller, soft build"),
}
_SHAPE_PARTS: dict[str, tuple[str, str]] = {
    "basic": ("기본", ""),
    "upper": ("상체 볼륨", " with a fuller bust"),
    "hip": ("골반 볼륨", " with fuller hips"),
    "both": ("상하 볼륨", " with a fuller bust and hips, clearly defined waist"),
}
for _value in _BODY_TYPES_MATRIX:
    _volume, _shape = _value.split("_", 1)
    _vol_ko, _vol_en = _VOLUME_PARTS[_volume]
    _shape_ko, _shape_en = _SHAPE_PARTS[_shape]
    _BODY_LABELS[_value] = (
        _vol_ko if _shape == "basic" else f"{_vol_ko} · {_shape_ko}",
        f"{_vol_en}{_shape_en}",
    )
del _value, _volume, _shape, _vol_ko, _vol_en, _shape_ko, _shape_en
_GENDER_PHRASE = {"male": "male presentation", "female": "female presentation"}

# Drift-prevention: HEIGHT_BUCKETS and _HEIGHT_LABELS must stay in sync
assert set(_BODY_LABELS) == set(BODY_TYPES), \
    "_BODY_LABELS keys must match BODY_TYPES"
assert set(_HEIGHT_LABELS) == {b for buckets in HEIGHT_BUCKETS.values() for b in buckets}, \
    "_HEIGHT_LABELS keys must match HEIGHT_BUCKETS"


class PhysiqueError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def bucket_gender(height_bucket: str | None) -> str | None:
    """키 구간 접두사(m_/f_)에서 성별을 유도한다. 유효한 버킷이 아니면 None.
    OACX 가 성별을 안 줄 때, 모델이 고른 키 구간에서 성별을 채우는 데 쓴다."""
    if not isinstance(height_bucket, str) or height_bucket not in _HEIGHT_LABELS:
        return None
    return _bucket_gender(height_bucket)


def _bucket_gender(bucket: str) -> str | None:
    if bucket.startswith("m_"):
        return "male"
    if bucket.startswith("f_"):
        return "female"
    return None


def validate_physique(*, height_bucket: str | None, body_type: str | None, gender: str | None) -> None:
    """부분 입력 허용(각 필드 독립). 위반 시 PhysiqueError('invalid_physique')."""
    if gender is not None:
        if not isinstance(gender, str) or gender not in GENDERS:
            raise PhysiqueError("invalid_physique", "성별 값이 올바르지 않습니다.")
    if body_type is not None:
        if not isinstance(body_type, str) or body_type not in BODY_TYPES:
            raise PhysiqueError("invalid_physique", "체형 값이 올바르지 않습니다.")
    if height_bucket is not None:
        if not isinstance(height_bucket, str) or height_bucket not in _HEIGHT_LABELS:
            raise PhysiqueError("invalid_physique", "키 구간 값이 올바르지 않습니다.")
        # 키 구간은 접두사(m_/f_)로 성별을 스스로 인코딩한다 — 별도 gender 없이도 저장 가능.
        # 모델 gender 가 이미 있을 때만 접두사와 일치하는지 교차검증한다(OACX 가 성별을 안 주는
        # 경우가 있어, gender 를 필수로 요구하면 키 구간을 아예 못 고른다).
        bg = _bucket_gender(height_bucket)
        if gender is not None and bg != gender:
            raise PhysiqueError("invalid_physique", "키 구간이 성별과 일치하지 않습니다.")


def build_body_profile_block(profile: Mapping | None) -> str:
    """profile={"gender","heightBucket","bodyType"} → 영문 프롬프트 블록.
    gender 는 트리거가 아니라 수식어 — height/body 중 하나라도 있어야 블록을 낸다
    (§6.3/§7: 미입력 → 절 생략; OACX 자동 gender만 있는 모델까지 블록이 붙는 것을 방지).
    자유문자열 미방출 — enum→고정 문구만."""
    if not isinstance(profile, Mapping):
        return ""
    parts: list[str] = []
    height = profile.get("heightBucket")
    if isinstance(height, str) and height in _HEIGHT_LABELS:
        parts.append(_HEIGHT_LABELS[height][1])
    body = profile.get("bodyType")
    if isinstance(body, str) and body in _BODY_LABELS:
        parts.append(_BODY_LABELS[body][1])
    if not parts:
        # height/body 둘 다 없음 — gender 혼자서는 블록을 못 낸다(트리거 아님, 수식어일 뿐).
        return ""
    gender = profile.get("gender")
    if isinstance(gender, str) and gender in _GENDER_PHRASE:
        parts.append(_GENDER_PHRASE[gender])
    desc = ", ".join(parts)
    return (
        "SUBJECT BUILD (generated body identity; the face is owned separately and "
        "left unchanged): the model has " + desc + ". Keep this build consistent across "
        "cuts; it has no authority over the face."
    )
