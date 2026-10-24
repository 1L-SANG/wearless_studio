"""Immutable detail publication upload and deletion contracts, using local storage fakes."""

import importlib
import importlib.util
import contextlib
import copy
import asyncio
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.auth import require_user
from app.main import create_app
from app import repo
from conftest import make_settings


def publication_module():
    assert importlib.util.find_spec("app.detail_publications") is not None
    return importlib.import_module("app.detail_publications")


def test_publication_key_pads_index():
    assert publication_module().publication_key("u1", "p1", 1) == "pub/u1/p1/01.jpg"


def file_token(**overrides):
    values = dict(user_id="u1", publication_id="p1", index=1,
                  key="pub/u1/p1/01.jpg", byte_size=123, expires_at=time.time() + 300)
    values.update(overrides)
    return publication_module().make_file_token("s", **values)


def test_file_token_roundtrip():
    data = publication_module().parse_file_token("s", file_token())
    assert {k: data[k] for k in ("user_id", "publication_id", "index", "key", "byte_size")} == {
        "user_id": "u1", "publication_id": "p1", "index": 1,
        "key": "pub/u1/p1/01.jpg", "byte_size": 123,
    }


def test_file_token_rejects_tamper():
    dp = publication_module()
    body, mac = file_token().split(".")
    with pytest.raises(dp.TokenInvalid):
        dp.parse_file_token("s", body + "." + ("0" if mac[0] != "0" else "1") + mac[1:])


def test_file_token_rejects_expired():
    dp = publication_module()
    with pytest.raises(dp.TokenInvalid):
        dp.parse_file_token("s", file_token(expires_at=time.time() - 1))


NOW = datetime(2026, 8, 31, 12, tzinfo=timezone.utc)
CREATED = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_compute_expires_at_subscriber():
    assert publication_module().compute_expires_at("pro", created_at=CREATED, now=NOW, current=NOW) is None


def test_compute_expires_at_free_new():
    assert publication_module().compute_expires_at("free", created_at=CREATED, now=NOW, current=None) == datetime(2027, 2, 28, 12, tzinfo=timezone.utc)


def test_compute_expires_at_free_existing():
    assert publication_module().compute_expires_at("free", created_at=CREATED, now=NOW, current=CREATED) == CREATED


USER = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
PROJECT = "33333333-3333-4333-8333-333333333333"


class FakeCursor:
    def __init__(self, store):
        self.store = store
        self.result = []
        self.rowcount = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def execute(self, sql, args=()):
        q = " ".join(sql.lower().split())
        self.store["sql"].append((q, args))
        rows = self.store["publications"]
        self.result = []
        if "pg_advisory_xact_lock" in q:
            return
        if q.startswith("select editor_revision"):
            self.result = [{"editor_revision": 7}]
        elif "from projects" in q and q.startswith("select"):
            self.result = [{"id": PROJECT, "editor_revision": 7}] if args[0] == PROJECT and args[1] == USER else []
        elif "from fm_output_records" in q:
            self.result = [{"present": True}] if self.store["real"] else []
        elif "count(*)" in q and "detail_publications" in q:
            self.result = [{"count": self.store["rate"]}]
        elif "from jobs" in q and "as closed" in q:
            self.result = [{"closed": self.store.get("account_closure") in ("pending", "running", "done")}]
        elif q.startswith("insert into detail_publications"):
            user_id, project_id, _version_project_id, preset, width, product_name, revision, files = args
            files = files.obj
            versions = [r["version"] for r in rows.values() if r["project_id"] == project_id]
            pub_id = str(uuid.uuid4())
            row = dict(id=pub_id, user_id=user_id, project_id=project_id, version=max(versions, default=0) + 1,
                       status="pending", preset=preset, width=width, product_name=product_name,
                       editor_revision=revision if revision is not None else 7, files=copy.deepcopy(files),
                       slice_count=0, total_bytes=0, expires_at=None,
                       created_at=datetime(2026, 1, 31, 12, tzinfo=timezone.utc), ready_at=None, deleted_at=None,
                       cleanup_after=datetime.now(timezone.utc) + timedelta(minutes=65), cleanup_generation=0)
            rows[pub_id] = row
            self.result = [copy.deepcopy(row)]
        elif "from detail_publications" in q and q.startswith("select"):
            if "project_id = %s" in q:
                user_id, project_id = args
                self.result = [copy.deepcopy(r) for r in rows.values() if r["user_id"] == user_id and r["project_id"] == project_id and r["status"] == "ready"]
                self.result.sort(key=lambda r: r["version"], reverse=True)
            elif "where user_id = %s" in q:
                self.result = [copy.deepcopy(r) for r in rows.values() if r["user_id"] == args[0]]
            elif "where id = %s" in q:
                pub_id, user_id = args
                row = rows.get(pub_id)
                self.result = [copy.deepcopy(row)] if row and row["user_id"] == user_id else []
            else:
                raise AssertionError(q)
        elif "from profiles" in q:
            self.result = [{"plan": self.store["plan"]}]
        elif q.startswith("insert into assets"):
            asset_id, user_id, project_id, source, bucket, key, mime, size, filename, metadata = args
            visibility = "public" if "'public'" in q else "private"
            if self.store.get("fail_asset") and self.store["assets"]:
                raise RuntimeError("asset insert failed")
            row = dict(id=asset_id, user_id=user_id, project_id=project_id, source=source,
                       visibility=visibility, r2_key=key, mime_type=mime, byte_size=size,
                       metadata=metadata.obj)
            self.store["assets"].setdefault(asset_id, row)
            self.result = [self.store["assets"][asset_id]]
        elif q.startswith("update detail_publications") and "status = 'ready'" in q:
            files, _slice_count, total_bytes, expires_at, pub_id = args
            row = rows[pub_id]
            row.update(files=copy.deepcopy(files.obj), total_bytes=total_bytes, expires_at=expires_at,
                       status="ready", slice_count=len(files.obj), ready_at=NOW)
            self.result = [copy.deepcopy(row)]
        elif q.startswith("update detail_publications") and "status = 'deleted'" in q:
            if "deleted_at = coalesce" in q:
                row = rows[args[0]]
                now = datetime.now(timezone.utc)
                if (row["status"] == "deleted" and row["cleanup_after"] <= now
                        and row["cleanup_generation"] == args[1]):
                    row.update(deleted_at=row["deleted_at"] or now, cleanup_after=now + timedelta(days=1))
                return
            pub_id, user_id = args
            row = rows.get(pub_id)
            if row and row["user_id"] == user_id:
                row.update(status="deleted", deleted_at=None,
                           cleanup_generation=row["cleanup_generation"] + 1)
                self.result = [copy.deepcopy(row)]
        elif q.startswith("update detail_publications set cleanup_after"):
            cleanup_after, pub_id = args
            rows[pub_id]["cleanup_after"] = cleanup_after
        elif q.startswith("update assets"):
            for row in self.store["assets"].values():
                if row["metadata"]["publication_id"] == args[0]:
                    row["deleted_at"] = NOW
        else:
            raise AssertionError((q, args))
        self.rowcount = len(self.result)

    async def fetchone(self):
        return self.result[0] if self.result else None

    async def fetchall(self):
        return self.result


class FakeConn:
    def __init__(self, store):
        self.store = store
        self.snapshot = copy.deepcopy(store)

    def cursor(self):
        return FakeCursor(self.store)

    async def commit(self):
        self.snapshot = copy.deepcopy(self.store)

    async def rollback(self):
        self.store.clear()
        self.store.update(copy.deepcopy(self.snapshot))


class FakeR2:
    def __init__(self):
        self.objects = {}
        self.deleted = []
        self.purged = []
        self.copied = []
        self.fail_delete = False
        self.put_sizes = []

    def presigned_put(self, key, mime, expires=300, *, byte_size=None):
        assert mime == "image/jpeg" and expires == 300
        self.put_sizes.append(byte_size)
        return "https://upload.test/" + key

    def head(self, key):
        return self.objects.get(key)

    def copy_with_cache(self, key, mime, cache):
        self.copied.append((key, mime, cache))

    def delete(self, key):
        if self.fail_delete:
            raise RuntimeError("storage unavailable")
        self.deleted.append(key)
        self.objects.pop(key, None)

    def purge_public_cache(self, keys):
        self.purged.extend(keys)

    def public_url(self, key):
        return "https://images.test/" + key


@pytest.fixture
def publication_client(monkeypatch):
    dp = publication_module()
    store = {"publications": {}, "assets": {}, "real": False, "rate": 0, "plan": "free", "sql": []}
    r2 = FakeR2()
    settings = make_settings(detail_publish_enabled=True, detail_publish_token_secret="s", r2_public_base="https://images.test")
    app = create_app(settings)
    app.dependency_overrides[require_user] = lambda: USER

    @contextlib.asynccontextmanager
    async def connection(_request):
        conn = FakeConn(store)
        try:
            yield conn
        except Exception:
            await conn.rollback()
            raise

    monkeypatch.setattr(dp, "get_conn", connection)
    with TestClient(app) as client:
        app.state.r2 = r2
        yield client, store, r2, app


def create_publication(client, count=2, **overrides):
    body = dict(preset="smartstore", width=860, productName="옷",
                files=[dict(index=i, byteSize=100, width=860, height=1000) for i in range(1, count + 1)])
    body.update(overrides)
    return client.post(f"/v1/projects/{PROJECT}/detail-publications", json=body)


def complete_publication(client, created):
    return client.post(f"/v1/detail-publications/{created['publicationId']}/complete",
                       json={"uploads": [{"index": f["index"], "uploadToken": f["uploadToken"]} for f in created["uploads"]]})


def uploaded(publication_client):
    client, store, r2, _app = publication_client
    response = create_publication(client)
    assert response.status_code == 200, response.text
    created = response.json()
    for upload in created["uploads"]:
        key = f"pub/{USER}/{created['publicationId']}/{upload['index']:02d}.jpg"
        r2.objects[key] = {"size": 100, "mime": "image/jpeg"}
    return created


def test_create_rejects_real_model_project(publication_client):
    client, store, *_ = publication_client
    store["real"] = True
    response = create_publication(client)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "real_model_not_allowed"
    assert not store["publications"]


def test_create_rejects_too_many_files(publication_client):
    response = create_publication(publication_client[0], count=41)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "too_many_files"


@pytest.mark.parametrize(("files", "code"), [
    ([dict(index=1, byteSize=3 * 1024 * 1024 + 1, width=860, height=1000)], "file_too_large"),
    ([dict(index=i, byteSize=3 * 1024 * 1024, width=860, height=1000) for i in range(1, 22)], "total_too_large"),
])
def test_create_rejects_size_limits(publication_client, files, code):
    response = create_publication(publication_client[0], files=files)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == code


def test_create_rate_limited(publication_client):
    client, store, *_ = publication_client
    store["rate"] = 20
    response = create_publication(client)
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limited"


def test_create_returns_presigned_uploads(publication_client):
    client, store, *_ = publication_client
    created = create_publication(client).json()
    assert created["version"] == 1
    assert len(created["uploads"]) == 2
    first = created["uploads"][0]
    assert first["uploadUrl"] == f"https://upload.test/pub/{USER}/{created['publicationId']}/01.jpg"
    assert publication_module().parse_file_token("s", first["uploadToken"])["byte_size"] == 100
    second = create_publication(client).json()
    assert second["version"] == 2
    assert store["publications"][created["publicationId"]]["editor_revision"] == 7
    locks = [args[0] for sql, args in store["sql"] if "pg_advisory_xact_lock" in sql]
    assert locks == [f"detail-publication-user:{USER}", f"detail-publication-project:{PROJECT}"] * 2


def test_create_signs_each_declared_upload_size(publication_client):
    client, _store, r2, _app = publication_client
    response = create_publication(client, files=[
        dict(index=1, byteSize=123, width=860, height=1000),
        dict(index=2, byteSize=456, width=860, height=1000),
    ])
    assert response.status_code == 200
    assert r2.put_sizes == [123, 456]


def test_complete_reports_missing_files(publication_client):
    client, store, r2, _ = publication_client
    created = uploaded(publication_client)
    r2.objects.pop(f"pub/{USER}/{created['publicationId']}/02.jpg")
    response = complete_publication(client, created)
    assert response.status_code == 409
    assert response.json()["error"]["missing"] == [2]
    assert store["publications"][created["publicationId"]]["status"] == "pending"
    assert not store["assets"]


@pytest.mark.parametrize("head", [{"size": 101, "mime": "image/jpeg"}, {"size": 100, "mime": "image/png"}])
def test_complete_reports_invalid_uploaded_file(publication_client, head):
    client, store, r2, _ = publication_client
    created = uploaded(publication_client)
    r2.objects[f"pub/{USER}/{created['publicationId']}/01.jpg"] = head
    response = complete_publication(client, created)
    assert response.status_code == 409
    assert response.json()["error"]["missing"] == [1]
    assert not store["assets"]


def test_complete_marks_ready_and_registers_assets(publication_client):
    client, store, r2, _ = publication_client
    created = uploaded(publication_client)
    response = complete_publication(client, created)
    assert response.status_code == 200, response.text
    result = response.json()
    assert len(result["files"]) == len(store["assets"]) == 2
    assert result["expiresAt"] == "2026-07-31T12:00:00+00:00"
    assert store["publications"][created["publicationId"]]["status"] == "ready"
    for asset in store["assets"].values():
        assert asset["visibility"] == "public" and asset["source"] == "export"
        assert asset["metadata"]["publication_id"] == created["publicationId"]
    assert all(cache == "public, max-age=31536000, immutable" for _, _, cache in r2.copied)
    assert any("for update" in sql and "detail_publications" in sql for sql, _ in store["sql"])


def test_complete_checks_files_concurrently(publication_client, monkeypatch):
    client, _store, r2, _app = publication_client
    created = create_publication(client, count=8).json()
    for upload in created["uploads"]:
        r2.objects[f"pub/{USER}/{created['publicationId']}/{upload['index']:02d}.jpg"] = {
            "size": 100, "mime": "image/jpeg"}
    peaks = {}
    for name in ("head", "copy_with_cache"):
        original = getattr(r2, name)
        lock, release = threading.Lock(), threading.Event()
        state = {"active": 0, "peak": 0}
        peaks[name] = state

        def concurrent(*args, original=original, lock=lock, release=release, state=state):
            with lock:
                state["active"] += 1
                state["peak"] = max(state["peak"], state["active"])
                if state["active"] == 6:
                    release.set()
            try:
                assert release.wait(2), "six storage calls must start together"
                return original(*args)
            finally:
                with lock:
                    state["active"] -= 1

        monkeypatch.setattr(r2, name, concurrent)
    response = complete_publication(client, created)
    assert response.status_code == 200, response.text
    assert {name: state["peak"] for name, state in peaks.items()} == {"head": 6, "copy_with_cache": 6}
    assert len(r2.copied) == len(response.json()["files"]) == 8


def test_complete_is_idempotent(publication_client):
    client, store, r2, _ = publication_client
    created = uploaded(publication_client)
    first = complete_publication(client, created)
    copied = len(r2.copied)
    second = complete_publication(client, created)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(store["assets"]) == 2
    assert len(r2.copied) == copied


def test_complete_rolls_back_all_assets_when_registration_fails(publication_client):
    client, store, _r2, _app = publication_client
    created = uploaded(publication_client)
    store["fail_asset"] = True
    response = complete_publication(client, created)
    assert response.status_code == 500
    assert not store["assets"]
    assert store["publications"][created["publicationId"]]["status"] == "pending"


def test_r2_copy_with_cache_replaces_immutable_headers():
    from app.r2 import R2Client
    from types import SimpleNamespace
    calls = []
    r2 = object.__new__(R2Client)
    r2._bucket = "images"
    r2._s3 = SimpleNamespace(copy_object=lambda **kwargs: calls.append(kwargs))
    assert hasattr(r2, "copy_with_cache")
    r2.copy_with_cache("pub/u/p/01.jpg", "image/jpeg", "public, max-age=31536000, immutable")
    assert calls == [{"Bucket": "images", "Key": "pub/u/p/01.jpg",
                      "CopySource": {"Bucket": "images", "Key": "pub/u/p/01.jpg"},
                      "ContentType": "image/jpeg", "MetadataDirective": "REPLACE",
                      "CacheControl": "public, max-age=31536000, immutable"}]


def test_complete_rejects_foreign_token_and_preserves_pending(publication_client):
    client, store, _r2, _app = publication_client
    created = uploaded(publication_client)
    created["uploads"][0]["uploadToken"] = file_token()
    response = complete_publication(client, created)
    assert response.status_code == 403
    assert not store["assets"]


def test_complete_derives_keys_instead_of_trusting_row(publication_client):
    client, store, r2, _app = publication_client
    created = uploaded(publication_client)
    store["publications"][created["publicationId"]]["files"][0]["r2_key"] = "pub/another-user/secret/01.jpg"
    response = complete_publication(client, created)
    assert response.status_code == 200
    assert all("another-user" not in key for key, _, _ in r2.copied)


def test_list_only_ready_and_owner(publication_client):
    client, store, _r2, app = publication_client
    created = uploaded(publication_client)
    complete_publication(client, created)
    create_publication(client)
    response = client.get(f"/v1/projects/{PROJECT}/detail-publications")
    assert [r["publicationId"] for r in response.json()["items"]] == [created["publicationId"]]
    app.dependency_overrides[require_user] = lambda: OTHER
    assert client.get(f"/v1/projects/{PROJECT}/detail-publications").status_code == 404
    assert complete_publication(client, created).status_code == 404


def test_delete_soft_deletes_and_calls_r2_delete(publication_client):
    client, store, r2, _ = publication_client
    created = uploaded(publication_client)
    complete_publication(client, created)
    response = client.delete(f"/v1/detail-publications/{created['publicationId']}")
    assert response.status_code == 204
    assert store["publications"][created["publicationId"]]["status"] == "deleted"
    assert store["publications"][created["publicationId"]]["deleted_at"] is None
    assert all(asset.get("deleted_at") is not None for asset in store["assets"].values())
    assert r2.deleted == r2.purged == [f"pub/{USER}/{created['publicationId']}/01.jpg", f"pub/{USER}/{created['publicationId']}/02.jpg"]
    assert client.get(f"/v1/projects/{PROJECT}/detail-publications").json() == {"items": []}


def test_delete_failure_remains_retryable(publication_client):
    client, store, r2, _ = publication_client
    created = uploaded(publication_client)
    r2.fail_delete = True
    response = client.delete(f"/v1/detail-publications/{created['publicationId']}")
    assert response.status_code == 204
    row = store["publications"][created["publicationId"]]
    assert row["status"] == "deleted" and row["deleted_at"] is None


def test_repeat_delete_reclaims_recreated_publication(publication_client):
    client, store, r2, _app = publication_client
    created = uploaded(publication_client)
    url = f"/v1/detail-publications/{created['publicationId']}"
    assert client.delete(url).status_code == 204
    key = f"pub/{USER}/{created['publicationId']}/01.jpg"
    r2.objects[key] = {"size": 100, "mime": "image/jpeg"}
    assert client.delete(url).status_code == 204
    assert key not in r2.objects
    assert len(r2.deleted) == len(r2.purged) == 4
    assert store["publications"][created["publicationId"]]["deleted_at"] is None


def test_publication_files_raises_value_error():
    with pytest.raises(ValueError, match="이미지 조각"):
        publication_module().publication_files({"files": []})


@pytest.mark.parametrize("operation", ["complete", "repeat_complete", "list", "delete"])
def test_routes_reject_invalid_publication_files(publication_client, operation):
    client, store, _r2, _app = publication_client
    created = uploaded(publication_client)
    if operation in ("repeat_complete", "list"):
        assert complete_publication(client, created).status_code == 200
    store["publications"][created["publicationId"]]["files"] = []
    if operation in ("complete", "repeat_complete"):
        response = complete_publication(client, created)
    elif operation == "list":
        response = client.get(f"/v1/projects/{PROJECT}/detail-publications")
    else:
        response = client.delete(f"/v1/detail-publications/{created['publicationId']}")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_files"


def test_account_purge_skips_invalid_files_and_continues(publication_client, caplog):
    client, store, r2, app = publication_client
    bad = uploaded(publication_client)
    good = uploaded(publication_client)
    store["publications"][bad["publicationId"]]["files"] = []
    assert asyncio.run(publication_module().purge_user_detail_publications(
        app, FakeConn(store), user_id=USER)) == 1
    assert store["publications"][bad["publicationId"]]["deleted_at"] is None
    assert store["publications"][good["publicationId"]]["deleted_at"] is None
    assert len(r2.deleted) == len(r2.purged) == 2
    assert f"account publication cleanup deferred: {bad['publicationId']}" in caplog.text


def test_unconfigured_secret_returns_503():
    publication_module()
    app = create_app(make_settings(detail_publish_enabled=True, detail_publish_token_secret=None))
    app.dependency_overrides[require_user] = lambda: USER
    with TestClient(app) as client:
        response = create_publication(client)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "detail_publish_unconfigured"


def test_routes_absent_when_detail_publish_disabled():
    app = create_app(make_settings())
    with TestClient(app) as client:
        assert create_publication(client).status_code == 404


def test_purge_user_detail_publications_deletes_all(publication_client):
    dp = publication_module()
    assert hasattr(dp, "purge_user_detail_publications")
    client, store, r2, app = publication_client
    ready = uploaded(publication_client)
    complete_publication(client, ready)
    pending = uploaded(publication_client)
    foreign = copy.deepcopy(store["publications"][ready["publicationId"]])
    foreign.update(id=str(uuid.uuid4()), user_id=OTHER)
    store["publications"][foreign["id"]] = foreign
    count = asyncio.run(dp.purge_user_detail_publications(app, FakeConn(store), user_id=USER))
    assert count == 2
    assert all(store["publications"][p["publicationId"]]["status"] == "deleted" and
               store["publications"][p["publicationId"]]["deleted_at"] is None for p in (ready, pending))
    assert foreign["status"] == "ready"
    assert sorted(r2.deleted) == sorted(r2.purged) == sorted(
        f"pub/{USER}/{p['publicationId']}/{i:02d}.jpg" for p in (ready, pending) for i in (1, 2))
    assert all(a.get("deleted_at") is not None for a in store["assets"].values())


def test_failed_account_purge_survives_paid_plan_recompute():
    dp = publication_module()
    assert hasattr(dp, "purge_user_detail_publications")
    from test_detail_publication_reaper import setup_reaper, seed, RetentionConn
    worker, store, r2 = setup_reaper(plan="pro")
    ready = seed(store, status="ready")
    pending = seed(store, status="pending", created_at=datetime.now(timezone.utc))
    r2.fail_delete = True
    assert asyncio.run(dp.purge_user_detail_publications(worker.app, RetentionConn(store), user_id=USER)) == 0
    for row in (store["publications"][ready["id"]], store["publications"][pending["id"]]):
        assert row["status"] == "deleted" and row["deleted_at"] is None
        assert row["expires_at"] is None
    r2.fail_delete = False
    asyncio.run(worker._sweep_once())
    assert store["publications"][ready["id"]]["deleted_at"] is not None
    assert store["publications"][pending["id"]]["deleted_at"] is None
    store["publications"][pending["id"]]["cleanup_after"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    asyncio.run(worker._sweep_once())
    assert store["publications"][pending["id"]]["deleted_at"] is not None
    assert len(r2.deleted) == len(r2.purged) == 2


def test_account_delete_hook_purges_detail_publications_without_changing_result(monkeypatch):
    from app.services import biometric_purge as bp
    from app import detail_publications as dp
    # Keep the established biometric workflow real; stub its preexisting external boundaries.
    calls = []
    schema = {}
    scope = dict(user_id=USER, model_ids=[], profile_ids=[])

    async def result(value, *_args, **_kwargs):
        return value

    monkeypatch.setattr(bp, "_require_storage", lambda _app: {"r2": FakeR2(), "r2_face": FakeR2()})
    for name, value in {
        "_schema": schema, "_scope": scope, "_derived_jobs": [], "_ensure_frozen": None,
        "_enrollment_ids": [], "_known_targets": (set(), set(), set(), {}),
        "_generation_result_info": (set(), 0), "_list_targets": set(),
        "_scan_generation_orphans": (set(), True),
        "_persist_target_manifest": (set(), set(), set(), 1),
        "_delete_and_reconcile": None, "_assert_target_manifest_revision": None,
        "_cleanup": None, "_clear_target_manifest": None,
    }.items():
        async def stub(*args, _value=value, **kwargs):
            return await result(_value)
        monkeypatch.setattr(bp, name, stub)

    async def purge(_app, _conn, *, user_id):
        calls.append(user_id)
        raise RuntimeError("publication storage unavailable")

    monkeypatch.setattr(dp, "purge_user_detail_publications", purge, raising=False)

    @contextlib.asynccontextmanager
    async def connection():
        yield FakeConn({})

    from types import SimpleNamespace
    app = SimpleNamespace(state=SimpleNamespace(pool=SimpleNamespace(connection=connection)))
    response = asyncio.run(bp.purge_biometric_scope(app, user_id=USER, reason="account_delete"))
    assert response.complete is True
    assert calls == [USER]


@pytest.mark.parametrize("closure", ["pending", "running", "done"])
@pytest.mark.parametrize("operation", ["create", "complete"])
def test_account_closure_blocks_publication_writes(publication_client, closure, operation):
    client, store, _r2, _app = publication_client
    created = uploaded(publication_client) if operation == "complete" else None
    store["account_closure"] = closure
    response = create_publication(client) if operation == "create" else complete_publication(client, created)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "account_closed"
    assert not store["assets"]


def test_account_purge_cleans_publications_without_face_storage(publication_client):
    from app.services import biometric_purge as bp
    client, store, r2, app = publication_client
    created = uploaded(publication_client)
    app.state.r2_face = None

    @contextlib.asynccontextmanager
    async def connection():
        yield FakeConn(store)

    from types import SimpleNamespace
    app.state.pool = SimpleNamespace(connection=connection)
    with pytest.raises(bp.PurgeIncomplete, match="storage_unavailable"):
        asyncio.run(bp.purge_biometric_scope(app, user_id=USER, reason="account_delete"))
    assert store["publications"][created["publicationId"]]["status"] == "deleted"
    assert r2.deleted == r2.purged == [f"pub/{USER}/{created['publicationId']}/01.jpg", f"pub/{USER}/{created['publicationId']}/02.jpg"]


@pytest.mark.parametrize("operation", ["delete", "account_delete"])
def test_explicit_deletion_schedules_recheck_after_success(publication_client, operation):
    dp = publication_module()
    client, store, r2, app = publication_client
    created = uploaded(publication_client)
    complete_publication(client, created)
    store["publications"][created["publicationId"]]["cleanup_after"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    if operation == "delete":
        assert client.delete(f"/v1/detail-publications/{created['publicationId']}").status_code == 204
    else:
        assert asyncio.run(dp.purge_user_detail_publications(app, FakeConn(store), user_id=USER)) == 1
    row = store["publications"][created["publicationId"]]
    assert row["status"] == "deleted" and row["deleted_at"] is not None
    assert row["cleanup_after"] > datetime.now(timezone.utc)
    key = f"pub/{USER}/{created['publicationId']}/01.jpg"
    from test_detail_publication_reaper import setup_reaper
    worker, worker_store, _ = setup_reaper()
    worker_store.update(store)
    worker.app.state.r2 = r2
    asyncio.run(worker._sweep_once())
    assert key not in r2.objects
    assert row["deleted_at"] is not None
    assert r2.deleted.count(key) == r2.purged.count(key) == 1


@pytest.mark.parametrize("operation", ["delete", "account_delete"])
def test_live_upload_keeps_deletion_retryable_until_cleanup_window(publication_client, operation):
    dp = publication_module()
    client, store, r2, app = publication_client
    created = uploaded(publication_client)
    assert complete_publication(client, created).status_code == 200
    publication_id = created["publicationId"]
    if operation == "delete":
        assert client.delete(f"/v1/detail-publications/{publication_id}").status_code == 204
    else:
        assert asyncio.run(dp.purge_user_detail_publications(app, FakeConn(store), user_id=USER)) == 1
    row = store["publications"][publication_id]
    assert row["status"] == "deleted"
    assert row["deleted_at"] is None
    assert not r2.objects
    assert r2.deleted == r2.purged


def test_reaper_reclaims_upload_recreated_after_first_completed_cleanup(publication_client):
    from test_detail_publication_reaper import setup_reaper
    client, store, r2, _app = publication_client
    created = uploaded(publication_client)
    publication_id = created["publicationId"]
    assert client.delete(f"/v1/detail-publications/{publication_id}").status_code == 204
    row = store["publications"][publication_id]
    # A transfer accepted before URL expiry can finish after an earlier cleanup.
    row["deleted_at"] = datetime.now(timezone.utc) - timedelta(days=2)
    row["cleanup_after"] = datetime.now(timezone.utc) - timedelta(days=1)
    key = f"pub/{USER}/{publication_id}/01.jpg"
    r2.objects[key] = {"size": 100, "mime": "image/jpeg"}
    worker, worker_store, _ = setup_reaper()
    worker_store.update(store)
    worker.app.state.r2 = r2
    asyncio.run(worker._sweep_once())
    assert key not in r2.objects
    assert r2.deleted.count(key) == r2.purged.count(key) == 2


def test_cleanup_window_uses_latest_url_issuance_after_lock_wait(publication_client, monkeypatch):
    from types import SimpleNamespace
    dp = publication_module()
    client, store, r2, _app = publication_client
    clock = {"now": datetime(2026, 10, 25, tzinfo=timezone.utc).timestamp()}
    monkeypatch.setattr(dp, "time", SimpleNamespace(time=lambda: clock["now"]))
    original = r2.presigned_put

    def slow_sign(*args, **kwargs):
        clock["now"] += 120
        return original(*args, **kwargs)

    monkeypatch.setattr(r2, "presigned_put", slow_sign)
    created = create_publication(client).json()
    row = store["publications"][created["publicationId"]]
    assert row["cleanup_after"] == datetime(2026, 10, 25, 1, 9, tzinfo=timezone.utc)
