"""콘티는 에디터/생성 완료와 같은 리비전으로 저장 경합을 막는다. 실제 DB는 사용하지 않는다."""
import asyncio
import inspect
from unittest.mock import AsyncMock

import pytest

from app import repo, routes
from conftest import auth_headers, assert_query_binds, patch_route_db


def _wire(monkeypatch, result=4):
    patch_route_db(monkeypatch, routes)
    monkeypatch.setattr(repo, "get_project", AsyncMock(return_value={"id": "p1"}))
    save = AsyncMock(return_value=result)
    monkeypatch.setattr(repo, "save_storyboard", save)
    monkeypatch.setattr(repo, "get_storyboard", AsyncMock(return_value=[{"id": "latest"}]))
    monkeypatch.setattr(repo, "get_storyboard_state", AsyncMock(return_value=([{"id": "latest"}], 5)))
    return save


@pytest.mark.parametrize("query", ["", "?withRevision=0"])
def test_get_default_keeps_the_plain_array(client, make_token, monkeypatch, query):
    _wire(monkeypatch)
    response = client.get(f"/v1/projects/p1/storyboard{query}", headers=auth_headers(make_token))
    assert response.status_code == 200
    assert response.json() == [{"id": "latest"}]
    repo.get_storyboard_state.assert_not_awaited()


def test_get_opt_in_returns_storyboard_and_revision_from_one_snapshot(client, make_token, monkeypatch):
    _wire(monkeypatch)
    response = client.get("/v1/projects/p1/storyboard?withRevision=1", headers=auth_headers(make_token))
    assert response.status_code == 200
    assert response.json() == {"blocks": [{"id": "latest"}], "revision": 5}
    repo.get_storyboard.assert_not_awaited()


@pytest.mark.parametrize("base,expected", [(3, {"blocks": [], "revision": 4}), (None, [])])
def test_put_matching_or_absent_revision(client, make_token, monkeypatch, base, expected):
    save = _wire(monkeypatch)
    query = "" if base is None else f"?baseRevision={base}"
    response = client.put(f"/v1/projects/p1/storyboard{query}", json=[], headers=auth_headers(make_token))
    assert response.status_code == 200, response.text
    assert response.json() == expected
    assert save.call_args.kwargs == {"base_revision": base}


def test_put_stale_revision_returns_latest_storyboard_in_meta(client, make_token, monkeypatch):
    _wire(monkeypatch, result=None)
    response = client.put("/v1/projects/p1/storyboard?baseRevision=3", json=[], headers=auth_headers(make_token))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "revision_conflict"
    assert response.json()["error"]["meta"] == {"storyboard": [{"id": "latest"}], "revision": 5}


def test_negative_revision_is_rejected_before_save(client, make_token, monkeypatch):
    save = _wire(monkeypatch)
    response = client.put("/v1/projects/p1/storyboard?baseRevision=-1", json=[], headers=auth_headers(make_token))
    assert response.status_code == 422
    save.assert_not_awaited()


class _Conn:
    def __init__(self):
        self.blocks, self.revision, self.calls = [], 3, []

    def cursor(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def execute(self, sql, params):
        assert_query_binds(sql, params)
        self.calls.append((sql, params))
        assert "editor_revision = editor_revision + 1" in sql
        assert "and (%s::integer is null or editor_revision = %s::integer)" in sql
        assert "where id = %s and user_id = %s and deleted_at is null" in sql
        blocks, _, _, base, _ = params
        self.result = None
        if base is None or base == self.revision:
            self.blocks = blocks.obj
            self.revision += 1
            self.result = {"editor_revision": self.revision}

    async def fetchone(self):
        return self.result


def test_conditional_update_serializes_two_writers_and_legacy_save_increments():
    conn = _Conn()
    assert asyncio.run(repo.save_storyboard(conn, "u1", "p1", [{"id": "first"}], 3)) == 4
    assert asyncio.run(repo.save_storyboard(conn, "u1", "p1", [{"id": "stale"}], 3)) is None
    assert conn.blocks == [{"id": "first"}]
    assert asyncio.run(repo.save_storyboard(conn, "u1", "p1", [], None)) == 5
    assert len(conn.calls) == 3


def test_editor_save_conflicts_with_a_stale_storyboard_and_success_shares_revision():
    conn = _Conn()
    assert asyncio.run(repo.save_editor_blocks(conn, "u1", "p1", [{"id": "editor"}], 3)) == 4
    assert asyncio.run(repo.save_storyboard(conn, "u1", "p1", [{"id": "stale"}], 3)) is None
    assert "editor_revision = editor_revision + 1" in inspect.getsource(repo.finalize_detail_page_success)
