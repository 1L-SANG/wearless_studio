import asyncio
import contextlib
import json
import re
import zipfile
from io import BytesIO

import app.routes as routes
import pytest
from app.services import export_render
from app.workers import export_job
from conftest import fake_worker_app, make_settings, worker_job
from PIL import Image


class _Conn:
    async def commit(self):
        return None


def _auth(make_token):
    return {"Authorization": f"Bearer {make_token()}"}


def _patch_db(monkeypatch):
    @contextlib.asynccontextmanager
    async def fake_conn(_request):
        yield _Conn()
    monkeypatch.setattr(routes, "get_conn", fake_conn)


def _hash(snapshot):
    return export_render.sha256_hex(export_render.canonical_bytes(snapshot))


def test_export_route_is_flagged_off_by_default(client, make_token):
    snapshot = {"editorBlocks": []}
    res = client.post(
        "/v1/projects/p1/export",
        json={"snapshot": snapshot, "snapshotHash": _hash(snapshot)},
        headers={**_auth(make_token), "Idempotency-Key": "k1"},
    )
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "export_disabled"


def test_export_route_rejects_stale_snapshot_before_job(client, make_token, monkeypatch):
    client.app.state.settings = make_settings(export_backend="on")
    _patch_db(monkeypatch)
    seen = {"create_job": 0}

    async def create_job(*args, **kwargs):
        seen["create_job"] += 1
        return {"id": "job-1"}, True

    async def get_editor_snapshot(conn, uid, pid):
        return {"blocks": [{"x": 0, "y": 0, "w": 120, "h": 80, "text": "A"}], "revision": 1}

    async def get_project(conn, uid, pid):
        return {"id": pid}

    async def by_key(conn, uid, key):
        return None

    monkeypatch.setattr(routes.repo, "get_project", get_project)
    monkeypatch.setattr(routes.repo, "get_editor_snapshot", get_editor_snapshot)
    monkeypatch.setattr(routes.repo, "get_job_by_idempotency_key", by_key)
    monkeypatch.setattr(routes.repo, "create_job", create_job)
    res = client.post(
        "/v1/projects/p1/export",
        json={"snapshot": {"editorBlocks": []}, "snapshotHash": "wrong"},
        headers={**_auth(make_token), "Idempotency-Key": "k1"},
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "snapshot_hash_mismatch"
    assert seen["create_job"] == 0


def test_export_route_idempotency_conflict(client, make_token, monkeypatch):
    client.app.state.settings = make_settings(export_backend="on")
    _patch_db(monkeypatch)
    snapshot = {"editorBlocks": [{"x": 0, "y": 0, "w": 120, "h": 80, "text": "A"}]}
    old_body_hash = "different"

    async def get_editor_snapshot(conn, uid, pid):
        return {"blocks": snapshot["editorBlocks"], "revision": 1}

    async def get_project(conn, uid, pid):
        return {"id": pid}

    async def by_key(conn, uid, key):
        return {"id": "job-1", "payload": {"requestBodyHash": old_body_hash}}

    monkeypatch.setattr(routes.repo, "get_project", get_project)
    monkeypatch.setattr(routes.repo, "get_editor_snapshot", get_editor_snapshot)
    monkeypatch.setattr(routes.repo, "get_job_by_idempotency_key", by_key)
    res = client.post(
        "/v1/projects/p1/export",
        json={"snapshot": snapshot, "snapshotHash": _hash(snapshot)},
        headers={**_auth(make_token), "Idempotency-Key": "k1"},
    )
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "idempotency_conflict"


def test_export_route_uses_persisted_snapshot_not_client_body(client, make_token, monkeypatch):
    client.app.state.settings = make_settings(export_backend="on")
    _patch_db(monkeypatch)
    captured = {}
    server_snapshot = {"editorBlocks": [{"text": "server truth"}]}

    async def get_editor_snapshot(conn, uid, pid):
        return {"projectId": pid, "revision": 7, "blocks": server_snapshot["editorBlocks"]}

    async def get_project(conn, uid, pid):
        return {"id": pid}

    async def by_key(*args, **kwargs):
        return None

    async def create_job(conn, **kwargs):
        captured["job"] = kwargs
        return {"id": "job-1"}, True

    async def create_export(conn, **kwargs):
        captured["export"] = kwargs
        return {"id": kwargs["export_id"]}

    monkeypatch.setattr(routes.repo, "get_project", get_project)
    monkeypatch.setattr(routes.repo, "get_editor_snapshot", get_editor_snapshot)
    monkeypatch.setattr(routes.repo, "get_job_by_idempotency_key", by_key)
    monkeypatch.setattr(routes.repo, "create_job", create_job)
    monkeypatch.setattr(routes.repo, "create_export_record", create_export)
    monkeypatch.setattr(routes, "_wake_dispatcher", lambda request: None)
    res = client.post(
        "/v1/projects/p1/export",
        json={
            "snapshot": {"editorBlocks": [{"src": "https://attacker.test/image.png"}]},
            "snapshotHash": _hash(server_snapshot),
        },
        headers={**_auth(make_token), "Idempotency-Key": "k1"},
    )
    assert res.status_code == 202
    assert captured["job"]["payload"]["snapshot"] == server_snapshot
    assert captured["export"]["snapshot_revision"] == 7


def test_export_renderer_is_deterministic_and_provider_free():
    snapshot = {"editorBlocks": [{"x": 10, "y": 20, "w": 260, "h": 120, "text": "Hello"}]}
    body = {"title": "Export"}
    options = {"format": "zip", "width": 600}
    a = export_render.render(snapshot=snapshot, body=body, options=options)
    b = export_render.render(snapshot=snapshot, body=body, options=options)
    assert [f["sha256"] for f in a.files] == [f["sha256"] for f in b.files]
    assert a.manifest["providerCalls"] == 0
    zip_file = next(f for f in a.files if f["role"] == "zip")
    with zipfile.ZipFile(BytesIO(zip_file["bytes"])) as zf:
        assert zf.namelist() == [
            "body.json",
            "provenance.json",
            "sections/001.png",
            "snapshot.json",
            "wearless-export.png",
        ]
        prov = json.loads(zf.read("provenance.json"))
    assert prov["rendererVersion"] == export_render.RENDERER_VERSION


def test_export_rejects_external_image_source_instead_of_placeholder():
    snapshot = {"editorBlocks": [{"src": "https://example.test/private.png"}]}
    with pytest.raises(export_render.ExportRenderError) as exc:
        export_render.render(snapshot=snapshot, body={}, options={})
    assert exc.value.code == "unsupported_export_image_source"


def test_export_renderer_renders_editor_elements_with_owned_asset():
    asset_id = "11111111-1111-4111-8111-111111111111"
    snapshot = {
        "editorBlocks": [{
            "x": 0, "y": 0, "w": 420, "h": 260, "bg": "#ffffff",
            "elements": [
                {"type": "image", "x": 20, "y": 20, "w": 120, "h": 90,
                 "src": f"/v1/assets/{asset_id}/file"},
                {"type": "text", "x": 160, "y": 40, "w": 180, "h": 40, "text": "Caption"},
            ],
        }],
    }
    raw = BytesIO()
    Image.new("RGB", (32, 32), (210, 20, 30)).save(raw, format="PNG")
    rendered = export_render.render(
        snapshot=snapshot,
        body={"title": "Export"},
        options={"format": "long_png", "width": 600},
        asset_bytes=lambda aid: raw.getvalue() if aid == asset_id else None,
    )
    assert rendered.files[0]["mime"] == "image/png"
    assert export_render.referenced_asset_ids(snapshot) == [asset_id]


def test_export_worker_is_provider_free_and_finalizes_uploaded_files(monkeypatch):
    captured = {}

    class TrackingR2:
        def __init__(self):
            self.puts = []
            self.deletes = []

        def put_bytes(self, key, data, mime, cache=None):
            self.puts.append((key, mime, len(data), cache))

        def delete(self, key):
            self.deletes.append(key)

    async def emit(*args, **kwargs):
        return None

    async def list_assets(*args, **kwargs):
        return []

    async def finalize_success(conn, **kwargs):
        captured.update(kwargs)
        return {"exportId": kwargs["export_id"]}

    async def finalize_failure(*args, **kwargs):
        raise AssertionError("success path must not finalize failure")

    monkeypatch.setattr(export_job, "_emit", emit)
    monkeypatch.setattr(export_job.repo, "list_project_assets_by_ids", list_assets)
    monkeypatch.setattr(export_job.repo, "finalize_export_success", finalize_success)
    monkeypatch.setattr(export_job.repo, "finalize_export_failure", finalize_failure)
    r2 = TrackingR2()
    app = fake_worker_app(make_settings(export_backend="on", r2_bucket="bucket"), r2=r2)
    snapshot = {"editorBlocks": [{"x": 0, "y": 0, "w": 120, "h": 80, "text": "A"}]}
    job = worker_job({
        "exportId": "e1", "snapshot": snapshot, "snapshotHash": _hash(snapshot),
        "body": {}, "options": {"format": "zip", "width": 600},
    })
    asyncio.run(export_job.run_export_job(app, job))

    assert [item["role"] for item in captured["files"]] == ["long_png", "zip"]
    assert captured["provenance"]["providerCalls"] == 0
    assert len(r2.puts) == 2
    assert r2.deletes == []


def test_export_migration_adds_kind_and_rows():
    sql = open(
        "/Users/nojeong-un/devs/wearless_studio/supabase/migrations/"
        "20260803020000_phase9_exports.sql",
        encoding="utf-8",
    ).read()
    assert "'export'" in sql
    assert re.search(r"create table if not exists public\.export_assets", sql)
    assert re.search(r"create table if not exists public\.export_provenance", sql)
    assert "provider_calls = 0" in sql
