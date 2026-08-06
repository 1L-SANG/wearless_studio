import pytest

from scripts import image_cost_report


def test_unknown_amount_is_not_rendered_as_zero():
    assert image_cost_report._usd(None) == "미확인"
    assert image_cost_report._won(None) == "미확인"


def test_4k_counterfactual_uses_each_models_own_1k_price():
    pro = image_cost_report._one_k_counterfactual(
        "gemini-3-pro-image", calls=1, actual_usd=0.24, output_image_tokens=2000)
    flash = image_cost_report._one_k_counterfactual(
        "gemini-3.1-flash-image", calls=1, actual_usd=0.1512, output_image_tokens=2520)

    assert pro == pytest.approx(0.1344)
    assert flash == pytest.approx(0.0672)
    assert image_cost_report._one_k_counterfactual("new-model", 1, 1, 1) is None
