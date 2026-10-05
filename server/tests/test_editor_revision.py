"""에디터 블록 낙관적 동시성(editor_revision) — 라우트 계약.

두 탭(또는 두 기기)이 같은 상세페이지를 열어 두면, 나중에 저장한 쪽이 앞의 편집을 통째로
덮었다(LWW, 2026-09-30 레이스 감사 High). 저장마다 리비전을 올리고, 클라이언트가 자기가 본
리비전(baseRevision)을 실어 보내 다르면 409 로 거절한다. 리비전을 안 싣는 옛 번들은 배포
도중에도 깨지지 않게 그대로 받는다.
"""

import app.routes as routes
from conftest import auth_headers, patch_route_db


async def _owned_project(conn, user_id, project_id):
    return {"id": project_id}


def _patch(monkeypatch, *, revision=3, blocks=None, save_result=None, seen=None):
    seen = seen if seen is not None else {}

    async def get_editor_state(conn, project_id):
        return (blocks if blocks is not None else [{"id": "b1"}]), revision

    async def get_editor_revision(conn, project_id):
        return revision

    async def save_editor_blocks(conn, user_id, project_id, body, base_revision=None):
        seen["base_revision"] = base_revision
        seen["blocks"] = body
        return save_result

    monkeypatch.setattr(routes.repo, "get_project", _owned_project)
    monkeypatch.setattr(routes.repo, "get_editor_state", get_editor_state)
    monkeypatch.setattr(routes.repo, "get_editor_revision", get_editor_revision)
    monkeypatch.setattr(routes.repo, "save_editor_blocks", save_editor_blocks)
    patch_route_db(monkeypatch, routes)
    return seen


def test_get_keeps_plain_list_for_old_clients(client, make_token, monkeypatch):
    async def get_editor_blocks(conn, project_id):
        return [{"id": "b1"}]

    _patch(monkeypatch)
    monkeypatch.setattr(routes.repo, "get_editor_blocks", get_editor_blocks)
    res = client.get("/v1/projects/p1/editor-blocks", headers=auth_headers(make_token))
    assert res.status_code == 200, res.text
    assert res.json() == [{"id": "b1"}]


def test_get_with_revision_returns_blocks_and_revision(client, make_token, monkeypatch):
    _patch(monkeypatch, revision=7)
    res = client.get("/v1/projects/p1/editor-blocks?withRevision=1", headers=auth_headers(make_token))
    assert res.status_code == 200, res.text
    assert res.json() == {"blocks": [{"id": "b1"}], "revision": 7}


def test_put_with_matching_base_returns_new_revision(client, make_token, monkeypatch):
    seen = _patch(monkeypatch, save_result=4)
    res = client.put("/v1/projects/p1/editor-blocks?baseRevision=3",
                     json=[{"id": "b1"}], headers=auth_headers(make_token))
    assert res.status_code == 200, res.text
    assert res.json() == {"ok": True, "revision": 4}
    assert seen["base_revision"] == 3


def test_put_with_stale_base_is_rejected_with_current_revision(client, make_token, monkeypatch):
    seen = _patch(monkeypatch, revision=5, save_result=None)
    res = client.put("/v1/projects/p1/editor-blocks?baseRevision=3",
                     json=[{"id": "stale"}], headers=auth_headers(make_token))
    assert res.status_code == 409, res.text
    err = res.json()["error"]
    assert err["code"] == "editor_conflict"
    assert err["meta"] == {"revision": 5}
    assert seen["base_revision"] == 3


def test_put_without_base_still_saves_for_old_clients(client, make_token, monkeypatch):
    seen = _patch(monkeypatch, save_result=9)
    res = client.put("/v1/projects/p1/editor-blocks", json=[{"id": "b1"}], headers=auth_headers(make_token))
    assert res.status_code == 200, res.text
    assert res.json() == {"ok": True, "revision": 9}
    assert seen["base_revision"] is None


def test_put_rejects_negative_base(client, make_token, monkeypatch):
    _patch(monkeypatch, save_result=1)
    res = client.put("/v1/projects/p1/editor-blocks?baseRevision=-1",
                     json=[], headers=auth_headers(make_token))
    assert res.status_code in (400, 422)
