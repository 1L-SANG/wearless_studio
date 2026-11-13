import pytest

from app.workers.product_name import validate_product_name


def test_validate_product_name_keeps_existing_fallback_contract():
    assert validate_product_name("소프트 골지 니트") == "소프트 골지 니트"
    assert validate_product_name("새 상품") is None


@pytest.mark.parametrize("value, expected", [
    (None, None), ("", None), (" \n\t ", None), ("Daily Shirt", None), (123, None),
    ("  새\n상품 ", None), ("가" * 30, "가" * 30), ("가" * 31, None),
    ("  소프트\n 골지\t니트  ", "소프트 골지 니트"), ("Cotton 코튼 셔츠", "Cotton 코튼 셔츠"),
])
def test_validate_product_name_preserves_normalization_and_limits(value, expected):
    assert validate_product_name(value) == expected
