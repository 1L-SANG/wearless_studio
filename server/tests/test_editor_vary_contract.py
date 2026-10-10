import asyncio

import pytest

from app import routes
from app.agents import cut_variator
from app.workers import editor_image_job as eij
from conftest import auth_headers, fake_worker_app, make_settings, patch_route_db, worker_job


ASSET = "11111111-1111-4111-8111-111111111111"


def _route_setup(monkeypatch, metadata):
    seen = {}

    async def project(*args):
        return {"id": "p1"}

    async def lookup(conn, uid, pid, aid):
        assert pid == "p1" and aid == ASSET
        seen["lookup"] = True
        return {"id": aid, "metadata": metadata, "source": "ai"}

    async def provenance(*args):
        return None

    async def create(conn, **kwargs):
        seen["job"] = kwargs
        return {"id": "j1"}, True

    async def reserve(*args):
        seen["reserved"] = True
        return 99

    patch_route_db(monkeypatch, routes)
    for name, fake in [("get_project", project), ("get_project_asset_for_user", lookup),
                       ("get_asset_facemarket_provenance", provenance), ("create_job", create),
                       ("reserve_credits", reserve)]:
        monkeypatch.setattr(routes.repo, name, fake)
    return seen


@pytest.mark.parametrize("similar", [False, True])
@pytest.mark.parametrize("override", [
    {"changes": [{"type": "pose", "value": "standing"}]}, {"changes": None},
    {"refBg": "background"}, {"refBgAssetId": ASSET}, {"refBg": None}, {"refBgAssetId": ""},
])
def test_vary_rejects_changes_and_background_before_reservation(client, make_token, monkeypatch, similar, override):
    seen = _route_setup(monkeypatch, {"detail_recipe": {"version": 1}, "cut_type": "product"})
    response = client.post("/v1/projects/p1/editor:generate-image", headers=auth_headers(make_token), json={
        "mode": "vary", "source": {"src": f"/v1/assets/{ASSET}/file", "cutType": "product"},
        **({"variationMode": "similar"} if similar else {}), **override,
    })
    assert response.status_code == 400
    assert response.json()["error"]["code"] == ("invalid_similar_cut" if similar else "vary_unsupported")
    assert "job" not in seen and "reserved" not in seen


@pytest.mark.parametrize("recipe", [None, {}, [], {"version": 2}, {"version": "1"}])
@pytest.mark.parametrize("cut_type", ["product", "styling"])
def test_vary_without_valid_recipe_never_reserves(client, make_token, monkeypatch, recipe, cut_type):
    seen = _route_setup(monkeypatch, {"detail_recipe": recipe, "cut_type": cut_type})
    response = client.post("/v1/projects/p1/editor:generate-image", headers=auth_headers(make_token), json={
        "mode": "vary", "source": {"src": f"/v1/assets/{ASSET}/file", "cutType": cut_type}, "changes": [],
    })
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "vary_unsupported"
    assert seen["lookup"]
    assert "job" not in seen and "reserved" not in seen


@pytest.mark.parametrize("similar", [False, True])
@pytest.mark.parametrize("changes", [False, True])
def test_supported_vary_accepts_omitted_or_empty_changes(client, make_token, monkeypatch, similar, changes):
    metadata = {"cut_type": "styling"} if similar else {"cut_type": "product", "detail_recipe": {"version": 1}}
    seen = _route_setup(monkeypatch, metadata)
    response = client.post("/v1/projects/p1/editor:generate-image", headers=auth_headers(make_token), json={
        "mode": "vary", "source": {"src": f"/v1/assets/{ASSET}/file", "cutType": metadata["cut_type"]},
        **({"variationMode": "similar"} if similar else {}), **({"changes": []} if changes else {}),
    })
    assert response.status_code == 202, response.text
    assert seen["lookup"] and seen["reserved"]


@pytest.mark.parametrize("recipe", [None, {}, [], {"version": 2}, "missing"])
def test_queued_legacy_vary_fails_without_generation_or_upload(monkeypatch, recipe):
    captured = {}

    async def asset(*args):
        if recipe == "missing":
            return None
        return {"id": ASSET, "mime_type": "image/png", "r2_key": "source",
                "metadata": {"cut_type": "product", "detail_recipe": recipe}}

    async def failure(conn, **kwargs):
        captured.update(kwargs)

    async def forbidden(*args, **kwargs):
        pytest.fail("Unsupported queued vary must not generate or succeed")

    monkeypatch.setattr(eij.repo, "get_project_asset_for_user", asset)
    monkeypatch.setattr(eij.repo, "get_asset_for_user", asset)
    monkeypatch.setattr(eij.repo, "finalize_editor_image_failure", failure)
    monkeypatch.setattr(eij.repo, "finalize_editor_image_success", forbidden)
    monkeypatch.setattr(eij.cut_variator, "generate", forbidden)
    app = fake_worker_app(make_settings())
    app.state.r2.get_bytes = lambda *_args: pytest.fail("Unsupported vary must fail before reading storage")
    app.state.r2.put_bytes = lambda *_args, **_kwargs: pytest.fail("Unsupported vary must not upload")
    asyncio.run(eij.run_editor_image_job(app, worker_job({
        "mode": "vary", "source": {"src": f"/v1/assets/{ASSET}/file", "cutType": "product"}, "changes": [],
    })))
    assert captured["metadata"]["error"] == "vary_unsupported"
    assert captured["code"] == "vary_unsupported"
    assert captured["reserved"] == 1


def test_variator_requires_similar_plan_before_provider_call():
    class Gemini:
        async def generate_content_image(self, *args, **kwargs):
            pytest.fail("No plan must not invoke a paid provider")

    with pytest.raises(ValueError, match="^similar_plan_required$"):
        asyncio.run(cut_variator.generate(make_settings(), Gemini(), eij.InlineImage("image/png", b"source"), [], "styling"))
