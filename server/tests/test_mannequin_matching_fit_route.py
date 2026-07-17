"""Regenerate matchingFit authorization against the persisted main selection."""

import asyncio
import contextlib

import app.routes as routes
import pytest


class _Conn:
    async def commit(self):
        return None


def _auth(make_token):
    return {"Authorization": f"Bearer {make_token()}"}


def test_main_match_resolution_falls_through_unroled_selections_to_lowest_sel_order():
    analysis = {
        "matchSelections": [{"clothingId": "not-authoritative"}],
        "matchClothing": [
            {"id": "later", "selected": True, "selOrder": 2},
            {"id": "main", "selected": True, "selOrder": 1},
        ],
    }

    assert routes.mannequin.main_match_item_id(analysis) == "main"


def test_main_match_resolution_keeps_historical_main_object_compatibility():
    assert routes.mannequin.main_match_item_id({
        "matchSelections": {"main": {"clothingId": "main"}},
    }) == "main"


def test_matching_fit_snapshot_keeps_v1_envelope_and_v2_inner_profile(monkeypatch):
    async def fake_get_analysis(conn, project_id):
        return {"matchSelections": [{"clothingId": "match-pants", "role": "main"}]}

    async def fake_get_metadata(conn, clothing_id):
        return {"clothing_type": "bottom", "category": "팬츠", "length": "full"}

    async def fake_get_asset(conn, clothing_id):
        return "asset-1"

    monkeypatch.setattr(routes.repo, "get_analysis", fake_get_analysis)
    monkeypatch.setattr(routes.repo, "get_matching_item_metadata", fake_get_metadata)
    monkeypatch.setattr(routes.repo, "get_matching_item_asset", fake_get_asset)

    profile = {
        "category": "top",
        "gender": "women",
        "axes": {"fit": "regular"},
        "source": "seller",
        "version": 2,
        "matchingFit": {
            "clothingId": "match-pants",
            "fitCategory": "pants",
            "axes": {"cut": "wide"},
        },
    }
    snapshot = asyncio.run(routes._fit_profile_snapshot(
        None, "p1", profile, validate_matching_fit=True,
    ))

    assert snapshot["version"] == 1
    assert snapshot["profile"]["version"] == 2
    assert snapshot["profile"]["matchingFit"] == profile["matchingFit"]


@pytest.mark.parametrize(
    ("metadata", "kept"),
    [
        ({"clothing_type": "bottom", "category": "팬츠", "length": "full"}, True),
        ({"clothing_type": "bottom", "category": "스커트", "length": "midi"}, False),
        ({"clothing_type": "bottom", "category": "버뮤다쇼츠", "length": "short"}, False),
        ({"clothing_type": "bottom", "category": "미등록", "length": "full"}, False),
    ],
)
def test_new_snapshot_keeps_legacy_match_cut_only_for_full_pants(monkeypatch, metadata, kept):
    async def fake_get_analysis(conn, project_id):
        return {"matchSelections": [{"clothingId": "match-1", "role": "main"}]}

    async def fake_get_metadata(conn, clothing_id):
        return metadata

    async def fake_get_asset(conn, clothing_id):
        return "asset-1"

    monkeypatch.setattr(routes.repo, "get_analysis", fake_get_analysis)
    monkeypatch.setattr(routes.repo, "get_matching_item_metadata", fake_get_metadata)
    monkeypatch.setattr(routes.repo, "get_matching_item_asset", fake_get_asset)

    snapshot = asyncio.run(routes._fit_profile_snapshot(None, "p1", {
        "category": "top", "gender": "women", "version": 1,
        "axes": {"fit": "regular"}, "matchCut": "wide",
    }))

    assert ("matchCut" in snapshot["profile"]) is kept


def test_regenerate_rejects_non_main_matching_fit_before_credit_reserve(
    client, make_token, monkeypatch,
):
    calls = {"create_job": 0, "reserve": 0}

    @contextlib.asynccontextmanager
    async def fake_conn(_request):
        yield _Conn()

    async def fake_get_project(conn, user_id, project_id):
        return {"id": project_id}

    async def fake_get_analysis(conn, project_id):
        return {
            "matchSelections": [
                {"clothingId": "match-main", "role": "main"},
                {"clothingId": "match-secondary", "role": "secondary"},
            ],
        }

    async def fake_create_job(conn, **kwargs):
        calls["create_job"] += 1
        return {"id": "job-should-not-exist"}, True

    async def fake_reserve(conn, user_id, cost):
        calls["reserve"] += 1
        return {"balance": 10, "reserved": cost}

    monkeypatch.setattr(routes, "get_conn", fake_conn)
    monkeypatch.setattr(routes.repo, "get_project", fake_get_project)
    monkeypatch.setattr(routes.repo, "get_analysis", fake_get_analysis)
    monkeypatch.setattr(routes.repo, "create_job", fake_create_job)
    monkeypatch.setattr(routes.repo, "reserve_credits", fake_reserve)

    response = client.post(
        "/v1/projects/p1/mannequins:regenerate",
        headers=_auth(make_token),
        json={
            "fitProfile": {
                "category": "top",
                "gender": "women",
                "axes": {"fit": "regular"},
                "source": "seller",
                "version": 2,
                "matchingFit": {
                    "clothingId": "match-secondary",
                    "fitCategory": "pants",
                    "axes": {"cut": "wide"},
                },
            },
        },
    )

    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "invalid_matching_fit"
    assert calls == {"create_job": 0, "reserve": 0}


def test_regenerate_rejects_spoofed_server_fit_category_before_credit_reserve(
    client, make_token, monkeypatch,
):
    calls = {"create_job": 0, "reserve": 0}

    @contextlib.asynccontextmanager
    async def fake_conn(_request):
        yield _Conn()

    async def fake_get_project(conn, user_id, project_id):
        return {"id": project_id}

    async def fake_get_analysis(conn, project_id):
        return {
            "matchSelections": [{"clothingId": "match-shorts", "role": "main"}],
        }

    async def fake_get_metadata(conn, clothing_id):
        assert clothing_id == "match-shorts"
        return {"clothing_type": "bottom", "category": "쇼츠", "length": "short"}

    async def fake_create_job(conn, **kwargs):
        calls["create_job"] += 1
        return {"id": "job-should-not-exist"}, True

    async def fake_reserve(conn, user_id, cost):
        calls["reserve"] += 1
        return {"balance": 10, "reserved": cost}

    monkeypatch.setattr(routes, "get_conn", fake_conn)
    monkeypatch.setattr(routes.repo, "get_project", fake_get_project)
    monkeypatch.setattr(routes.repo, "get_analysis", fake_get_analysis)
    monkeypatch.setattr(routes.repo, "get_matching_item_metadata", fake_get_metadata)
    monkeypatch.setattr(routes.repo, "create_job", fake_create_job)
    monkeypatch.setattr(routes.repo, "reserve_credits", fake_reserve)

    response = client.post(
        "/v1/projects/p1/mannequins:regenerate",
        headers=_auth(make_token),
        json={
            "fitProfile": {
                "category": "top",
                "gender": "women",
                "axes": {"fit": "regular"},
                "source": "seller",
                "version": 2,
                "matchingFit": {
                    "clothingId": "match-shorts",
                    "fitCategory": "pants",
                    "axes": {"cut": "wide"},
                },
            },
        },
    )

    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "invalid_matching_fit"
    assert calls == {"create_job": 0, "reserve": 0}
