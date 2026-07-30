from app.agents import mannequin


def test_dress_always_uses_women_base_gender():
    assert mannequin.select_base_gender(
        {"targetGenders": ["men"]},
        "dress",
    ) == "women"
    assert mannequin.select_base_gender(
        {"targetGenders": ["men"]},
        "outer",
    ) == "men"
