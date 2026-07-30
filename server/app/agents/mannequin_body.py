"""마네킹 베이스 체형(가슴·힙 볼륨) — 순수 모듈. 프론트 src/lib/mannequinBody.js 와 수동 미러.

체형 값은 프롬프트에 절대 보간되지 않는다. 어떤 베이스 마네킹 이미지를 image 1 로 넣을지를
고르는 데만 쓴다 — 그래서 fit_axes 카탈로그(프롬프트 보간·adjusted_axes·fit QC 경로에
물려 있음)와 분리한다.
"""

LEVELS = ("slim", "regular", "volume")
DEFAULT = "regular"


def normalize(raw, gender: str) -> dict | None:
    """여성 베이스에만 적용. gender != 'women' 이면 None(남성은 매트릭스가 없다).

    카탈로그 밖 값·타입 불일치는 조용히 DEFAULT 로 떨어진다. 반환은 항상 두 축이 채워진
    dict 이므로 호출자가 키 존재를 방어할 필요가 없다. 이미 정규화된 값을 다시 넣어도 같다.
    """
    if gender != "women":
        return None
    src = raw if isinstance(raw, dict) else {}

    def _level(key: str) -> str:
        value = src.get(key)
        return value if value in LEVELS else DEFAULT

    return {"bust": _level("bust"), "hip": _level("hip")}


def matrix_key(body: dict | None) -> str | None:
    """베이스 에셋 매트릭스 조회 키 '{bust}_{hip}'.

    regular/regular 은 매트릭스에 넣지 않는다 — 현행 단일 에셋이 그대로 담당해야
    기본값 셀러의 결과가 바뀌지 않는다. 무효 입력도 None(→ 호출자가 현행 에셋 폴백).
    """
    if not isinstance(body, dict):
        return None
    bust, hip = body.get("bust"), body.get("hip")
    if bust not in LEVELS or hip not in LEVELS:
        return None
    if bust == DEFAULT and hip == DEFAULT:
        return None
    return f"{bust}_{hip}"
