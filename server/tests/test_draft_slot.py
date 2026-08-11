import contextlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import app.routes as routes


ASSET_A = "11111111-1111-4111-8111-111111111111"
ASSET_B = "22222222-2222-4222-8222-222222222222"
MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "supabase/migrations/20260811000000_draft_slots.sql"
)


def _auth(make_token):
    return {"Authorization": f"Bearer {make_token()}"}


def _payload(*asset_ids, name="니트"):
    return {
        "product": {
            "name": name,
            "colors": [{
                "id": "base",
                "isBase": True,
                "images": [
                    {"id": asset_id, "slot": "Front"} for asset_id in asset_ids
                ],
            }],
        },
        "analysis": {"fit": "regular"},
        "composeMode": "basic",
    }


class _Conn:
    async def commit(self):
        return None


class _R2:
    def __init__(self):
        self.deletes = []
        self.heads = {}
        self.presigned = []

    def delete(self, key):
        self.deletes.append(key)

    def presigned_put(self, key, mime):
        self.presigned.append((key, mime))
        return f"https://upload.test/{key}"

    def head(self, key):
        return self.heads.get(key)

    def public_url(self, key):
        return f"https://r2.test/{key}"


def _patch_slot_repo(monkeypatch):
    state = {"slot": None, "cleaned": []}
    r2 = _R2()

    @contextlib.asynccontextmanager
    async def fake_conn(_request):
        yield _Conn()

    async def lock_slot(conn, user_id):
        return state["slot"]

    async def create_slot(
        conn, *, user_id, payload, active_token, device_label, photos_pending
    ):
        now = datetime.now(timezone.utc)
        state["slot"] = {
            "user_id": user_id,
            "payload": payload,
            "active_token": active_token,
            "device_label": device_label,
            "photos_pending": photos_pending,
            "updated_at": now,
            "expires_at": now + timedelta(days=7),
        }
        return state["slot"]

    async def update_slot(
        conn, *, user_id, payload, device_label, photos_pending
    ):
        now = datetime.now(timezone.utc)
        state["slot"].update({
            "payload": payload,
            "device_label": device_label,
            "photos_pending": photos_pending,
            "updated_at": now,
            "expires_at": now + timedelta(days=7),
        })
        return state["slot"]

    async def takeover_slot(conn, user_id, active_token):
        state["slot"]["active_token"] = active_token
        return state["slot"]

    async def delete_slot(conn, user_id):
        state["slot"] = None

    async def soft_delete(conn, user_id, asset_ids):
        state["cleaned"].append(list(asset_ids))
        return [
            {"id": asset_id, "r2_key": f"draft/{asset_id}.jpg"}
            for asset_id in asset_ids
        ]

    monkeypatch.setattr(routes, "get_conn", fake_conn)
    monkeypatch.setattr(routes, "_r2", lambda request: r2)
    monkeypatch.setattr(routes.repo, "lock_draft_slot", lock_slot)
    monkeypatch.setattr(routes.repo, "create_draft_slot", create_slot)
    monkeypatch.setattr(routes.repo, "update_draft_slot", update_slot)
    monkeypatch.setattr(routes.repo, "takeover_draft_slot", takeover_slot)
    monkeypatch.setattr(routes.repo, "delete_draft_slot", delete_slot)
    monkeypatch.setattr(routes.repo, "soft_delete_unreferenced_draft_assets", soft_delete)
    return state, r2


def _put(client, headers, payload, token=None, device="Mac Chrome", pending=False):
    return client.put(
        "/v1/draft-slot",
        headers=headers,
        json={
            "payload": payload,
            "token": token,
            "deviceLabel": device,
            "photosPending": pending,
        },
    )


def test_draft_slot_crud_and_get_token_metadata(client, make_token, monkeypatch):
    state, _r2 = _patch_slot_repo(monkeypatch)
    headers = _auth(make_token)

    assert client.get("/v1/draft-slot", headers=headers).status_code == 204

    created = _put(client, headers, _payload(ASSET_A), pending=True)
    assert created.status_code == 201, created.text
    token = created.json()["token"]
    UUID(token)

    meta_only = client.get("/v1/draft-slot", headers=headers)
    assert meta_only.status_code == 200
    assert meta_only.json() == {
        "meta": {
            "updatedAt": state["slot"]["updated_at"].isoformat(),
            "deviceLabel": "Mac Chrome",
            "photoCount": 1,
            "photosPending": True,
        },
        "holdsToken": False,
    }

    full_headers = {**headers, "X-Draft-Token": token}
    full = client.get("/v1/draft-slot?full=1", headers=full_headers)
    assert full.status_code == 200
    assert full.json()["holdsToken"] is True
    assert full.json()["payload"] == _payload(ASSET_A)

    updated = _put(
        client, headers, _payload(ASSET_A, name="가디건"), token=token,
        device="iPhone", pending=False,
    )
    assert updated.status_code == 200
    assert updated.json()["token"] == token
    assert state["slot"]["expires_at"] > datetime.now(timezone.utc) + timedelta(days=6)

    assert client.delete("/v1/draft-slot", headers=headers).status_code == 204
    assert client.get("/v1/draft-slot", headers=headers).status_code == 204


def test_draft_slot_put_token_mismatch_returns_409_meta(client, make_token, monkeypatch):
    state, _r2 = _patch_slot_repo(monkeypatch)
    headers = _auth(make_token)
    created = _put(client, headers, _payload())

    mismatch = _put(client, headers, _payload(name="덮어쓰면 안 됨"), token=str(UUID(int=7)))

    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["code"] == "token_mismatch"
    assert mismatch.json()["error"]["meta"]["deviceLabel"] == "Mac Chrome"
    assert state["slot"]["active_token"] == created.json()["token"]
    assert state["slot"]["payload"] == _payload()


def test_takeover_invalidates_old_token(client, make_token, monkeypatch):
    _state, _r2 = _patch_slot_repo(monkeypatch)
    headers = _auth(make_token)
    old_token = _put(client, headers, _payload()).json()["token"]

    takeover = client.post("/v1/draft-slot:takeover", headers=headers)
    assert takeover.status_code == 200
    new_token = takeover.json()["token"]
    assert new_token != old_token
    assert takeover.json()["payload"] == _payload()

    assert _put(client, headers, _payload(), token=old_token).status_code == 409
    assert _put(client, headers, _payload(), token=new_token).status_code == 200


def test_expired_get_is_absent_and_cleans_draft_assets(client, make_token, monkeypatch):
    state, r2 = _patch_slot_repo(monkeypatch)
    headers = _auth(make_token)
    _put(client, headers, _payload(ASSET_A))
    state["slot"]["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)

    response = client.get("/v1/draft-slot", headers=headers)

    assert response.status_code == 204
    assert state["slot"] is None
    assert state["cleaned"] == [[ASSET_A]]
    assert r2.deletes == [f"draft/{ASSET_A}.jpg"]


def test_expired_put_creates_fresh_slot_and_cleans_only_removed_assets(
    client, make_token, monkeypatch
):
    state, r2 = _patch_slot_repo(monkeypatch)
    headers = _auth(make_token)
    old_token = _put(client, headers, _payload(ASSET_A, ASSET_B)).json()["token"]
    state["slot"]["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)

    recreated = _put(client, headers, _payload(ASSET_B), token=old_token)

    assert recreated.status_code == 201
    assert recreated.json()["token"] != old_token
    assert state["cleaned"] == [[ASSET_A]]
    assert r2.deletes == [f"draft/{ASSET_A}.jpg"]


def test_expired_takeover_is_absent_and_cleans_assets(client, make_token, monkeypatch):
    state, r2 = _patch_slot_repo(monkeypatch)
    headers = _auth(make_token)
    _put(client, headers, _payload(ASSET_A))
    state["slot"]["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)

    response = client.post("/v1/draft-slot:takeover", headers=headers)

    assert response.status_code == 204
    assert state["cleaned"] == [[ASSET_A]]
    assert r2.deletes == [f"draft/{ASSET_A}.jpg"]


def test_replace_and_delete_cleanup_removed_draft_assets(client, make_token, monkeypatch):
    state, r2 = _patch_slot_repo(monkeypatch)
    headers = _auth(make_token)
    token = _put(client, headers, _payload(ASSET_A, ASSET_B)).json()["token"]

    replaced = _put(client, headers, _payload(ASSET_B), token=token)
    deleted = client.delete("/v1/draft-slot", headers=headers)

    assert replaced.status_code == 200
    assert deleted.status_code == 204
    assert state["cleaned"] == [[ASSET_A], [ASSET_B]]
    assert r2.deletes == [f"draft/{ASSET_A}.jpg", f"draft/{ASSET_B}.jpg"]


def test_draft_slot_get_requires_bearer(client):
    response = client.get("/v1/draft-slot")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_draft_slot_upload_reuses_asset_flow_without_project(
    client, make_token, monkeypatch
):
    r2 = _R2()
    client.app.state.r2 = r2
    captured = {}

    @contextlib.asynccontextmanager
    async def fake_conn(_request):
        yield _Conn()

    async def create_asset(conn, **kwargs):
        captured.update(kwargs)
        return {
            "id": kwargs["asset_id"],
            "r2_key": kwargs["key"],
            "mime_type": kwargs["mime"],
            "byte_size": kwargs["size"],
        }

    monkeypatch.setattr(routes, "get_conn", fake_conn)
    monkeypatch.setattr(routes.repo, "create_asset", create_asset)
    headers = _auth(make_token)
    issued = client.post(
        "/v1/assets/upload-url",
        headers=headers,
        json={
            "filename": "front.jpg",
            "mime": "image/jpeg",
            "size": 123,
            "projectId": None,
            "purpose": "draft_slot",
        },
    )
    assert issued.status_code == 200, issued.text
    asset_id = issued.json()["assetId"]
    key = f"users/user-1/projects/draft-slot/uploads/{asset_id}.jpg"
    assert r2.presigned == [(key, "image/jpeg")]
    r2.heads[key] = {"mime": "image/jpeg", "size": 123}

    completed = client.post(
        f"/v1/assets/{asset_id}/complete",
        headers=headers,
        json={
            "mime": "image/jpeg",
            "filename": "front.jpg",
            "projectId": None,
            "purpose": "draft_slot",
        },
    )

    assert completed.status_code == 200, completed.text
    assert captured["project_id"] is None
    assert captured["metadata"] == {"purpose": "draft_slot"}
    assert captured["key"] == key


def test_draft_slot_migration_declares_exact_table_and_owner_rls():
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    assert "create table if not exists public.draft_slots" in sql
    assert "user_id uuid primary key references auth.users (id) on delete cascade" in sql
    assert "payload jsonb not null" in sql
    assert "active_token uuid not null" in sql
    assert "device_label text" in sql
    assert "photos_pending boolean not null default false" in sql
    assert "updated_at timestamptz not null default now()" in sql
    assert "expires_at timestamptz not null" in sql
    assert "alter table public.draft_slots enable row level security" in sql
    assert "user_id = auth.uid()" in sql
