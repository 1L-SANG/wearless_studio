"""추천 저장은 모양·길이만 검증하고 기존 칩과 핏 결과에는 손대지 않는다."""

import contextlib
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from app import repo, routes
from conftest import auth_headers
from test_analysis_server_owned_fields import _Conn, _save


SUGGESTIONS = [{"point": "소매 리본", "evidence": "소매 끝에 리본이 달려 있어요"}]


@pytest.mark.parametrize("merge", [False, True])
def test_omitted_recommendations_survive_older_forms_and_other_field_updates(merge):
    saved = _save({"featureSuggestions": deepcopy(SUGGESTIONS)}, {"fit": "regular"}, merge=merge)
    assert saved["featureSuggestions"] == SUGGESTIONS


def test_client_storage_normalizes_only_shape_and_length_and_caps_five():
    raw = [{"point": "오버핏", "evidence": "English note", "extra": "ignore"},
           {"point": "소매 리본", "evidence": "가" * 41}, {"point": "긴 문장이고 단어가 아주 많아서 칩으로 못 써요", "evidence": "있어요"},
           {"point": 123, "evidence": "있어요"}, *[{"point": "밑단 프릴", "evidence": ""}] * 7]
    original = deepcopy(raw)
    saved = _save(None, {"featureSuggestions": raw, "sellingPoints": ["셀러 칩"], "aiSuggestedPoints": ["기존 칩"]})
    assert len(saved["featureSuggestions"]) == 5
    assert saved["featureSuggestions"][:2] == [{"point": "오버핏", "evidence": "English note"},
                                               {"point": "소매 리본", "evidence": ""}]
    assert all(set(item) == {"point", "evidence"} for item in saved["featureSuggestions"])
    assert saved["sellingPoints"] == ["셀러 칩"] and saved["aiSuggestedPoints"] == ["기존 칩"]
    assert raw == original


def test_explicit_empty_array_clears_previous_recommendations():
    assert _save({"featureSuggestions": deepcopy(SUGGESTIONS)}, {"featureSuggestions": []})["featureSuggestions"] == []


@pytest.mark.parametrize("value", [None, "bad"])
def test_invalid_array_shape_does_not_overwrite_previous_recommendations(value):
    assert _save({"featureSuggestions": deepcopy(SUGGESTIONS)}, {"featureSuggestions": value})["featureSuggestions"] == SUGGESTIONS


def test_api_save_get_keeps_validated_recommendations(client, make_token, monkeypatch):
    conn = _Conn()
    @contextlib.asynccontextmanager
    async def get_conn(request):
        yield conn
    monkeypatch.setattr(routes, "get_conn", get_conn)
    monkeypatch.setattr(repo, "get_project", AsyncMock(return_value={"id": "p1"}))
    monkeypatch.setattr(repo, "get_product", AsyncMock(return_value={}))
    response = client.patch("/v1/projects/p1/analysis", headers=auth_headers(make_token),
        json={"featureSuggestions": [{"point": "소매\n리본", "evidence": "짧은 근거"}]})
    assert response.status_code == 200, response.text
    fetched = client.get("/v1/projects/p1/analysis", headers=auth_headers(make_token))
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["featureSuggestions"] == [{"point": "소매 리본", "evidence": "짧은 근거"}]
