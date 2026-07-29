"""마네킹 베이스 체형(bust/hip 볼륨) 정규화 — 순수 함수 회귀."""

from app.agents.mannequin_body import DEFAULT, LEVELS, matrix_key, normalize
from app.agents import mannequin as m
from app.workers import mannequin_job
from tests.conftest import make_settings


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


MATRIX = {"slim_volume": "asset-slim-volume", "volume_volume": "asset-volume-volume"}


def test_select_base_asset_id_hits_the_matrix():
    s = make_settings(base_mannequin_women_asset_id="asset-women-default",
                      base_mannequin_men_asset_id="asset-men",
                      base_mannequin_women_matrix=MATRIX)
    assert m.select_base_asset_id(s, "women", {"bust": "slim", "hip": "volume"}) \
        == "asset-slim-volume"


def test_select_base_asset_id_falls_back_when_combination_missing():
    # 매트릭스에 없는 조합(에셋 미제작) → 현행 단일 에셋. 조용히 동작하되 결과가 바뀌지 않는다.
    s = make_settings(base_mannequin_women_asset_id="asset-women-default",
                      base_mannequin_men_asset_id="asset-men",
                      base_mannequin_women_matrix=MATRIX)
    assert m.select_base_asset_id(s, "women", {"bust": "volume", "hip": "slim"}) \
        == "asset-women-default"


def test_select_base_asset_id_falls_back_when_matrix_unset():
    # 코드를 매트릭스 env 보다 먼저 배포해도 안전해야 한다(배포 순서 무관).
    s = make_settings(base_mannequin_women_asset_id="asset-women-default",
                      base_mannequin_men_asset_id="asset-men")
    assert m.select_base_asset_id(s, "women", {"bust": "slim", "hip": "volume"}) \
        == "asset-women-default"


def test_select_base_asset_id_default_body_matches_current_behavior():
    s = make_settings(base_mannequin_women_asset_id="asset-women-default",
                      base_mannequin_men_asset_id="asset-men",
                      base_mannequin_women_matrix=MATRIX)
    assert m.select_base_asset_id(s, "women", None) == "asset-women-default"
    assert m.select_base_asset_id(
        s, "women", {"bust": "regular", "hip": "regular"}) == "asset-women-default"


def test_select_base_asset_id_men_ignores_body():
    s = make_settings(base_mannequin_women_asset_id="asset-women-default",
                      base_mannequin_men_asset_id="asset-men",
                      base_mannequin_women_matrix=MATRIX)
    assert m.select_base_asset_id(s, "men", {"bust": "slim", "hip": "volume"}) == "asset-men"


def test_body_from_job_prefers_the_payload_snapshot():
    # 스냅샷이 정본 — 잡 생성 후 셀러가 analysis 를 바꿔도 이번 잡은 잡힌 값으로 돈다.
    job = {"payload": {"mannequinBodySnapshot": {
        "version": 1, "gender": "women", "body": {"bust": "volume", "hip": "slim"}}}}
    analysis = {"mannequinBody": {"bust": "slim", "hip": "slim"}}
    assert mannequin_job._mannequin_body_from_job(job, analysis, "women") == {
        "bust": "volume", "hip": "slim"}


def test_body_from_job_falls_back_to_analysis_for_legacy_jobs():
    # 키가 없는 legacy 잡만 analysis 폴백(fitProfileSnapshot 과 동일 규율).
    job = {"payload": {"mode": "generate"}}
    analysis = {"mannequinBody": {"bust": "slim", "hip": "volume"}}
    assert mannequin_job._mannequin_body_from_job(job, analysis, "women") == {
        "bust": "slim", "hip": "volume"}


def test_body_from_job_returns_none_for_men():
    job = {"payload": {"mannequinBodySnapshot": {
        "version": 1, "gender": "women", "body": {"bust": "volume", "hip": "volume"}}}}
    assert mannequin_job._mannequin_body_from_job(job, {}, "men") is None


def test_body_from_job_ignores_unknown_snapshot_version():
    # 미래 버전 스냅샷은 신뢰하지 않고 analysis 폴백 — 조용한 오해석보다 낫다.
    job = {"payload": {"mannequinBodySnapshot": {"version": 99, "body": {"bust": "volume"}}}}
    analysis = {"mannequinBody": {"bust": "slim", "hip": "slim"}}
    assert mannequin_job._mannequin_body_from_job(job, analysis, "women") == {
        "bust": "slim", "hip": "slim"}
