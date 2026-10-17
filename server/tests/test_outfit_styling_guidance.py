import json

import pytest

from app.agents.outfit_recommender import build_prompt


@pytest.mark.parametrize("clothing_type,subtype,expected", [
    ("outer", "coat", "coat hem"), ("outer", "padding", "bulk"),
    ("outer", "cardigan", "neckline"), ("outer", "shirt", "open overshirt"),
    ("top", "shirt", "standalone shirt"), ("top", "knit", "knit texture"),
    ("top", "tshirt", "T-shirt"), ("bottom", "jeans", "denim wash"),
    ("bottom", "slacks", "tailored trousers"), ("bottom", "skirt", "skirt hem"),
    ("bottom", "training_pants", "sporty"), ("outer", "코트", "coat hem"),
])
def test_actual_model_prompt_has_category_specific_styling_priorities(clothing_type, subtype, expected):
    prompt = build_prompt({"clothingType": clothing_type, "subCategory": subtype}, {}, {"slots": {}})
    product = json.loads(prompt.split("PRODUCT DATA JSON\n", 1)[1])["product"]
    assert expected in product["stylingGuidance"]


def test_whole_outfit_prompt_balances_color_texture_and_footwear_without_blanket_bans():
    prompt = build_prompt({"clothingType": "outer", "subCategory": "jacket"}, {}, {"slots": {}})
    for criterion in ["hue, lightness and saturation", "visible area", "texture", "footwear", "not a blanket penalty"]:
        assert criterion in prompt
    assert "origin=user" in prompt
    assert "ONLY IDs" in prompt
