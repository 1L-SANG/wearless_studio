import asyncio
from contextlib import asynccontextmanager

import pytest

from app import repo, routes
from app.workers import editor_image_job as eij
from conftest import auth_headers, fake_worker_app, make_settings, patch_route_db, worker_job


class Cursor:
    def __init__(self, conn):
        self.conn = conn
        self.sql = ""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def execute(self, sql, params=None):
        self.sql = " ".join(sql.split()).lower()
        self.conn.statements.append((self.sql, params))

    async def fetchone(self):
        if self.sql.startswith("select id from jobs"):
            return {"id": "j1"}
        if self.sql.startswith("select coalesce(max(sort_order)"):
            return {"v": 0}
        if self.sql.startswith("insert into wardrobe_images"):
            return {"id": "w1"}
        return None

    async def fetchall(self):
        return self.conn.rows


class Conn:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.statements = []

    def cursor(self):
        return Cursor(self)

    async def commit(self):
        pass


@pytest.mark.parametrize("metadata, reshoot", [
    ({"detail_recipe": {"version": 1}}, True), ({}, False),
    ({"detail_recipe": []}, False), ({"detail_recipe": {"version": 2}}, False),
])
def test_wardrobe_response_marks_only_version_one_recipes(client, make_token, monkeypatch, metadata, reshoot):
    row = {"id": "w1", "asset_id": "a1", "ai": True, "cut_type": "product",
           "color_id": "base", "asset_metadata": metadata}

    async def project(*args):
        return {"id": "p1"}

    async def images(*args):
        return [row]

    monkeypatch.setattr(routes.repo, "get_project", project)
    monkeypatch.setattr(routes.repo, "list_wardrobe_images", images)
    patch_route_db(monkeypatch, routes)
    response = client.get("/v1/projects/p1/wardrobe", headers=auth_headers(make_token))
    assert response.status_code == 200
    image = response.json()["base"][0]
    if reshoot:
        assert image["detailReshoot"] is True
    else:
        assert "detailReshoot" not in image


def test_wardrobe_list_reads_asset_metadata_with_existing_owner_and_deletion_filters():
    rows = [{"id": "w1", "asset_metadata": {"detail_recipe": {"version": 1}}}]
    conn = Conn(rows)
    assert asyncio.run(repo.list_wardrobe_images(conn, "u1", "p1")) == rows
    sql, params = conn.statements[0]
    assert "join assets" in sql and "metadata as asset_metadata" in sql
    assert "pr.user_id = %s" in sql and "pr.deleted_at is null" in sql and "wi.deleted_at is null" in sql
    assert params == ("p1", "u1")


@pytest.mark.parametrize("recipe, reshoot", [({"version": 1}, True), (None, False), ({"version": 2}, False)])
def test_editor_finalize_returns_and_persists_the_same_reshoot_marker(monkeypatch, recipe, reshoot):
    async def consume(*args, **kwargs):
        return 99

    monkeypatch.setattr(repo, "_consume_buckets", consume)
    conn = Conn()
    image = asyncio.run(repo.finalize_editor_image_success(
        conn, job_id="j1", lease_token="lease", user_id="u1", project_id="p1",
        image={"asset_id": "a1", "bucket": "b", "key": "k", "mime": "image/png",
               "metadata": {"detail_recipe": recipe}},
        group="base", cut_type="product", reserved=1, charge=1, metadata={},
    ))
    if reshoot:
        assert image["detailReshoot"] is True
    else:
        assert "detailReshoot" not in image
    result = next(params[0].obj for sql, params in conn.statements if sql.startswith("update jobs"))
    event = next(params[1].obj for sql, params in conn.statements if sql.startswith("insert into job_events"))
    assert result == event and result["data"] == image


@pytest.mark.parametrize("mode", ["new", "vary"])
def test_detail_worker_success_includes_reshoot_marker_and_original_recipe(monkeypatch, mode):
    recipe = {"version": 1, "colorId": "base", "direction": "back", "shot": "detail",
              "detailTargetId": "back-target", "exampleId": "front-example"}
    conn, generated = Conn(), []

    class Pool:
        @asynccontextmanager
        async def connection(self):
            yield conn

    async def empty(*args, **kwargs):
        return {}

    async def asset(*args):
        return {"id": "source", "r2_key": "source", "mime_type": "image/png",
                "metadata": {"cut_type": "product", "detail_recipe": recipe}}

    async def detail(app, job, spec, product, analysis, **kwargs):
        generated.append(spec)
        return eij.InlineImage("image/png", b"DETAIL"), {"passed": True}

    async def consume(*args, **kwargs):
        return 99

    async def forbidden(*args, **kwargs):
        pytest.fail(f"Detail must complete with its recipe: {kwargs}")

    monkeypatch.setattr(repo, "get_project_asset_for_user", asset)
    monkeypatch.setattr(repo, "get_product", empty)
    monkeypatch.setattr(repo, "get_analysis", empty)
    monkeypatch.setattr(repo, "create_ai_output_cleanup_intent", empty)
    monkeypatch.setattr(repo, "_consume_buckets", consume)
    monkeypatch.setattr(repo, "finalize_editor_image_failure", forbidden)
    monkeypatch.setattr(eij.detail_shot_runtime, "generate", detail)
    monkeypatch.setattr(eij, "_emit", empty)
    app = fake_worker_app(make_settings())
    app.state.pool = Pool()
    payload = ({"mode": "new", "cutType": "product", **recipe} if mode == "new" else {
        "mode": "vary", "source": {"src": "/v1/assets/source/file", "cutType": "product"}, "changes": [],
    })
    asyncio.run(eij.run_editor_image_job(app, worker_job(payload)))
    result = next(params[0].obj for sql, params in conn.statements if sql.startswith("update jobs"))
    assert result["data"]["detailReshoot"] is True
    metadata = next(params[-1].obj for sql, params in conn.statements if sql.startswith("insert into assets"))
    assert metadata["detail_recipe"] == recipe
    assert generated[0]["direction"] == "back" and generated[0]["detailTargetId"] == "back-target"
