import pytest
import app.routes as routes
from conftest import auth_headers, patch_route_db


def setup_save(monkeypatch):
    seen = []
    async def owned(*args):
        return {"id": "p1"}
    async def save(conn, user_id, project_id, blocks, base_revision=None):
        seen.append(blocks)
        return 1
    monkeypatch.setattr(routes.repo, "get_project", owned)
    monkeypatch.setattr(routes.repo, "save_editor_blocks", save)
    patch_route_db(monkeypatch, routes)
    return seen


@pytest.mark.parametrize("value", [0, 48, 100, 34.5])
def test_editor_preserves_valid_blur(client, make_token, monkeypatch, value):
    seen = setup_save(monkeypatch)
    blocks = [{"id": "b", "elements": [{"id": "p", "type": "image", "blur": value}]}]
    response = client.put("/v1/projects/p1/editor-blocks", json=blocks, headers=auth_headers(make_token))
    assert response.status_code == 200
    assert seen == [blocks]


@pytest.mark.parametrize("value", [-1, 101, None, True, "48", {}, [], pytest.param(10 ** 400, id="huge-number")])
def test_editor_rejects_invalid_blur_before_persisting(client, make_token, monkeypatch, value):
    seen = setup_save(monkeypatch)
    response = client.put("/v1/projects/p1/editor-blocks", json=[{"elements": [{"type": "image", "blur": value}]}], headers=auth_headers(make_token))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "editor_blur_invalid"
    assert seen == []


def test_editor_rejects_blur_on_nonimage(client, make_token, monkeypatch):
    seen = setup_save(monkeypatch)
    response = client.put("/v1/projects/p1/editor-blocks", json=[{"elements": [{"type": "shape", "blur": 48}]}], headers=auth_headers(make_token))
    assert response.status_code == 400
    assert seen == []


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_editor_blur_requires_finite_number(value):
    from app.editor_elements import validate_editor_image_blur
    with pytest.raises(ValueError):
        validate_editor_image_blur([{"elements": [{"type": "image", "blur": value}]}])


def test_legacy_elements_without_blur_stay_unchanged(client, make_token, monkeypatch):
    seen = setup_save(monkeypatch)
    blocks = [{"elements": [{"type": "image", "src": None}, {"type": "shape"}]}]
    response = client.put("/v1/projects/p1/editor-blocks", json=blocks, headers=auth_headers(make_token))
    assert response.status_code == 200
    assert seen == [blocks]
