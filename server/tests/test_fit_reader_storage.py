"""서버 핏 판독을 저장·조회·특징 갱신에서 보존한다."""

import asyncio
import contextlib
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from app import repo, routes
from app.agents import fit_axes
from conftest import auth_headers
from test_analysis_server_owned_fields import _Conn, _save
from test_fit_reader import reading


@pytest.mark.parametrize("merge", [False, True])
def test_point_refresh_or_older_form_omission_keeps_the_existing_fit_read(merge):
    original = reading()
    saved = _save({"fitRead": original, "fit": "slim"}, {"aiSuggestedPoints": ["골지 짜임"]}, merge=merge)
    assert saved["fitRead"] == original
    assert saved["aiSuggestedPoints"] == ["골지 짜임"]


def test_repo_validates_first_fit_read_and_does_not_mutate_the_request():
    raw = reading("hanger")
    raw["frontHem"] = {"value": "SELLER ATTACK", "confident": True}
    before = deepcopy(raw)
    saved = _save(None, {"fitRead": raw})["fitRead"]
    assert saved["topLength"] == {"value": "basic", "confident": False}
    assert saved["sleeve"] == {"value": "wrist", "confident": False}
    assert saved["pantsLength"]["confident"] is False
    assert saved["frontHem"] == {"value": None, "confident": False}
    assert raw == before


def test_invalid_photo_type_is_not_saved_or_exposed():
    invalid = reading("UNKNOWN")
    assert "fitRead" not in _save(None, {"fitRead": invalid})
    assert "fitRead" not in fit_axes.normalize_analysis_fit({"fitRead": invalid})


def test_analysis_normalizer_checks_the_dto_and_keeps_existing_profile_unchanged():
    p = {"category": "top", "gender": "women", "axes": {"fit": "over"}, "axisSources": {"fit": "ai"}}
    data = {"fitRead": reading("flat"), "fitProfile": p}
    before = deepcopy(data)
    actual = fit_axes.normalize_analysis_fit(data)
    assert actual["fitRead"]["sleeve"]["confident"] is False
    assert actual["fitProfile"] == p
    assert data == before


@pytest.mark.parametrize("url", ["/v1/projects/p1/analysis", "/v1/projects/p1/analysis?merge=1"])
@pytest.mark.parametrize("initial", [None, reading()])
def test_patch_preserves_existing_fit_read_and_sanitizes_axis_sources(client, make_token, monkeypatch, url, initial):
    conn = _Conn({"fitRead": initial} if initial else None)
    @contextlib.asynccontextmanager
    async def get_conn(request):
        yield conn
    monkeypatch.setattr(routes, "get_conn", get_conn)
    monkeypatch.setattr(repo, "get_project", AsyncMock(return_value={"id": "p1"}))
    monkeypatch.setattr(repo, "get_product", AsyncMock(return_value={}))
    profile = {"category": "top", "gender": "women", "axes": {"length": "crop"},
               "axisSources": {"length": "seller", "sleeve": "secret", "waist": "ai", "fit": "unsure"}}
    response = client.patch(url, headers=auth_headers(make_token), json={"fitRead": reading("hanger"), "fitProfile": profile})
    assert response.status_code == 200, response.text
    fetched = client.get("/v1/projects/p1/analysis", headers=auth_headers(make_token))
    assert fetched.status_code == 200, fetched.text
    assert conn.saved["fitRead"] == fetched.json()["fitRead"]
    if initial:
        assert conn.saved["fitRead"] == initial
    else:
        assert conn.saved["fitRead"]["photoType"] == "hanger"
        assert conn.saved["fitRead"]["topLength"]["confident"] is False
    assert conn.saved["fitProfile"]["axisSources"] == {"length": "seller", "fit": "unsure"}


def test_existing_no_mannequin_consumers_accept_the_reader_dto(monkeypatch):
    from app.agents import fit_reader, confirmed_gpt_prompt
    from test_fit_cut_prompts import generic
    from test_confirmed_gpt_no_mannequin import _packet
    p = {"category": "top", "gender": "men", "axes": {"fit": "slim"}, "axisSources": {"fit": "seller"}}
    analysis = {"fitProfile": p, "fitRead": fit_reader.normalize_fit_read(reading("hanger"))}
    precise = _packet(monkeypatch, analysis=analysis)
    for prompt in (generic(analysis), confirmed_gpt_prompt.compile_confirmed_gpt_prompt(precise.prompt_input)):
        assert "the short sleeves grip the biceps" not in prompt
        assert "FRONT HEM SHAPE (from the product photo): the front hem is curved" in prompt
