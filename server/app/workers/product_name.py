"""상세페이지 자동 상품명의 기존 검증 규칙."""

import re

from ..agents.prompts import clean_text


def validate_product_name(value) -> str | None:
    """기존 자동 상품명 폴백이 쓰는 짧은 한국어 이름 검사."""
    name = clean_text(value)
    if not name or name == "새 상품" or len(name) > 30 or not re.search(r"[가-힣]", name):
        return None
    return name

