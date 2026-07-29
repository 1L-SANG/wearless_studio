"""마네킹 베이스 체형(bust/hip 볼륨) 정규화 — 순수 함수 회귀."""

from app.agents.mannequin_body import DEFAULT, LEVELS, matrix_key, normalize


def test_levels_are_exactly_three():
    assert LEVELS == ("slim", "regular", "volume")
    assert DEFAULT == "regular"


def test_normalize_keeps_valid_levels():
    assert normalize({"bust": "volume", "hip": "slim"}, "women") == {
        "bust": "volume", "hip": "slim"}


def test_normalize_returns_none_for_men():
    # 남성은 체형 매트릭스가 없다 — 현행 단일 베이스만 쓴다.
    assert normalize({"bust": "volume", "hip": "volume"}, "men") is None


def test_normalize_falls_back_to_regular_for_unknown_values():
    assert normalize({"bust": "huge", "hip": None}, "women") == {
        "bust": "regular", "hip": "regular"}


def test_normalize_handles_missing_and_non_dict_input():
    assert normalize(None, "women") == {"bust": "regular", "hip": "regular"}
    assert normalize("volume", "women") == {"bust": "regular", "hip": "regular"}
    assert normalize({}, "women") == {"bust": "regular", "hip": "regular"}


def test_normalize_is_idempotent():
    once = normalize({"bust": "slim", "hip": "volume"}, "women")
    assert normalize(once, "women") == once


def test_matrix_key_skips_the_default_combination():
    # regular/regular 은 매트릭스에 없다 — 현행 MANNEQUIN_BASE_WOMEN_ASSET_ID 가 담당한다.
    assert matrix_key({"bust": "regular", "hip": "regular"}) is None


def test_matrix_key_builds_bust_hip_key():
    assert matrix_key({"bust": "slim", "hip": "volume"}) == "slim_volume"
    assert matrix_key({"bust": "regular", "hip": "slim"}) == "regular_slim"


def test_matrix_key_rejects_invalid_input():
    assert matrix_key(None) is None
    assert matrix_key({"bust": "huge", "hip": "slim"}) is None
    assert matrix_key({"bust": "slim"}) is None
