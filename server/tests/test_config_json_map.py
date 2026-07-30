"""`_json_str_map` — MANNEQUIN_BASE_WOMEN_MATRIX env 파싱 (Minor-4).

이 파서는 절대 raise 하면 안 된다: env 오타가 서버 부팅을 막아서는 안 되고, 대신 조용히
빈 맵으로 떨어져 매트릭스가 미설정된 것과 동일하게(=현행 단일 베이스 폴백) 취급돼야 한다.
`load_settings()` 를 거치는 실제 진입점(config.py:225)까지 한 번은 꼭 통과시킨다 —
`make_settings()`(conftest)는 `Settings`를 직접 생성하므로 이 경로를 절대 실행하지 않는다.
"""
import pytest

from app.config import _json_str_map, load_settings


@pytest.mark.parametrize("raw", [
    None,
    "",
    "not json",
    "[1,2]",
    "null",
    "true",
    '"a bare json string"',
])
def test_json_str_map_degrades_to_empty_on_garbage(raw):
    assert _json_str_map(raw) == {}


def test_json_str_map_empty_object():
    assert _json_str_map("{}") == {}


def test_json_str_map_filters_non_string_and_empty_values():
    raw = '{"slim_volume":"a1","bad":3,"empty":""}'
    assert _json_str_map(raw) == {"slim_volume": "a1"}


def test_load_settings_parses_matrix_env(monkeypatch):
    monkeypatch.setenv("MANNEQUIN_BASE_WOMEN_MATRIX", '{"slim_volume":"a1","bad":3,"empty":""}')
    assert load_settings().base_mannequin_women_matrix == {"slim_volume": "a1"}


def test_load_settings_matrix_env_typo_degrades_to_empty_map(monkeypatch):
    monkeypatch.setenv("MANNEQUIN_BASE_WOMEN_MATRIX", "not json")
    assert load_settings().base_mannequin_women_matrix == {}


def test_load_settings_matrix_env_unset_defaults_to_empty_map(monkeypatch):
    monkeypatch.delenv("MANNEQUIN_BASE_WOMEN_MATRIX", raising=False)
    assert load_settings().base_mannequin_women_matrix == {}
