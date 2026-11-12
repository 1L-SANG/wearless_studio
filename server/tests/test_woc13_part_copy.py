from app.agents.page_part_copy import validate_parts


def test_literal_newline_is_normalized_before_validation():
    description = r"끝선이 또렷하게 잡힌 카라라\n얼굴 쪽이 단정해 보여요"
    assert validate_parts([{"point": "카라", "desc": description}], ["카라"]) == {
        "카라": description.replace(r"\n", "\n")}


def test_normalization_keeps_line_count_and_banned_claim_checks():
    for description in [r"끝선이 잡힌 카라라\n통기성이 좋아요", r"끝선이 잡힌 카라라\n단정해 보여요\n길어요"]:
        assert validate_parts([{"point": "카라", "desc": description}], ["카라"]) == {}
