"""Cafe24 worker recovery and write fences, with no external services."""

import asyncio
import copy
import importlib
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from app import repo
from app.services import cafe24_tokens
from app.services.cafe24_client import ERROR_COPY, Cafe24ApiError
from conftest import assert_query_binds, make_settings


USER, PROJECT, PUBLICATION, CONNECTION, JOB = [f"{i:08d}-0000-4000-8000-000000000000" for i in range(1, 6)]
LEASE = "worker-lease"


def status_error(status, retry_after=None):
    request = httpx.Request("POST", "https://fake.invalid/products")
    response = httpx.Response(status, request=request,
        headers={"Retry-After": retry_after} if retry_after is not None else {},
        json={"error": {"message": "upstream-private-body", "more_info": "upstream-private-detail"}})
    return httpx.HTTPStatusError("upstream-private-exception", request=request, response=response)


class WorkerEnv:
    def __init__(self, monkeypatch):
        assert importlib.util.find_spec("app.workers.mall_register_job"), "registration worker is missing"
        self.worker = importlib.import_module("app.workers.mall_register_job")
        self.job = dict(id=JOB, user_id=USER, project_id=PROJECT, kind="mall_register", status="running",
            lease_token=LEASE, payload=dict(mall="cafe24", connectionId=CONNECTION, mallId="myshop",
                publicationId=PUBLICATION, width=860, productName='니트 <"상품">', price=39000,
                supplyPrice=30000, display="T", selling="F", taxType="A", categoryNo=27,
                thumbnailAssetId=None), metadata={}, result=None)
        self.stored = copy.deepcopy(self.job)
        self.committed = copy.deepcopy(self.stored)
        self.connection_row = dict(id=CONNECTION, user_id=USER, mall="cafe24", shop_id="myshop",
            status="connected", refresh_expires_at=datetime.now(timezone.utc) + timedelta(days=1))
        self.publication = dict(id=PUBLICATION, user_id=USER, project_id=PROJECT, status="ready",
            files=[dict(index=i, bytes=10, r2_key="unsafe/other-owner") for i in range(1, 10)])
        self.alive, self.closed, self.cancelled = True, False, False
        self.calls, self.events, self.reads, self.patches, self.sleeps, self.ensures = [], [], [], [], [], []
        self.scripts, self.hooks = {}, {}
        self.last_registered, self.fail_patch, self.fail_progress = [], None, False
        self.version = datetime.now(timezone.utc)
        self.token_generation = 0
        self.asset_bytes = BytesIO()
        Image.new("RGBA", (2500, 1000), (255, 0, 0, 128)).save(self.asset_bytes, format="PNG")
        self.app = SimpleNamespace(state=SimpleNamespace(pool=self, r2=self,
            settings=make_settings(multimall_register_enabled=True, mall_connect_enabled=True, detail_publish_enabled=True,
                cafe24_enabled=True, cafe24_client_id="fixture-id", cafe24_client_secret="fixture-private",
                mall_keys_kek="fixture-kek", cafe24_allowed_mall_ids=frozenset({"myshop"}))))
        monkeypatch.setattr(self.worker, "Cafe24Client", lambda *a, **kw: self)
        monkeypatch.setattr(repo, "renew_job_lease", self.renew)
        monkeypatch.setattr(repo, "user_account_purge_closed", self.account_closed)
        monkeypatch.setattr(repo, "is_job_cancelled", self.is_cancelled)
        monkeypatch.setattr(repo, "get_cafe24_connection_by_id", self.get_connection)
        monkeypatch.setattr(repo, "get_detail_publication", self.get_publication)
        monkeypatch.setattr(repo, "get_project_asset_for_user", self.get_asset)
        monkeypatch.setattr(repo, "set_job_metadata_patch", self.patch)
        monkeypatch.setattr(repo, "set_job_progress_fenced", self.progress)
        monkeypatch.setattr(repo, "finalize_uncharged_job", self.finalize)
        monkeypatch.setattr(cafe24_tokens, "ensure_cafe24_access_token", self.ensure)
        monkeypatch.setattr(cafe24_tokens, "_mark", self.mark)
        # Patch only the worker's sleep, keeping the event loop's sleep available.
        monkeypatch.setattr(self.worker, "_sleep", self.sleep)

    @asynccontextmanager
    async def connection(self):
        yield self

    def cursor(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def execute(self, sql, params):
        assert_query_binds(sql, params)
        q = " ".join(sql.lower().split())
        assert q.startswith("update mall_connections set last_registered_at")
        assert "status = 'connected'" in q and "locked_by = %s" in q and "status = 'running'" in q
        assert "user_id = %s" in q and "exists" in q
        cid, user, jid, lease = params
        assert (cid, user, jid, lease) == (CONNECTION, USER, JOB, LEASE)
        if self.alive and self.connection_row["status"] == "connected" and self.connection_row["id"] == cid:
            self.last_registered.append(cid)

    async def commit(self):
        self.committed = copy.deepcopy(self.stored)
        self.events.append("commit")

    async def renew(self, conn, jid, lease):
        assert (jid, lease) == (JOB, LEASE)
        self.events.append("recheck")
        return self.alive

    async def account_closed(self, conn, user):
        assert user == USER
        return self.closed

    async def is_cancelled(self, conn, jid):
        return self.cancelled

    async def get_connection(self, conn, *, user_id, connection_id):
        assert (user_id, connection_id) == (USER, CONNECTION)
        return copy.deepcopy(self.connection_row)

    async def get_publication(self, conn, *, user_id, publication_id):
        assert (user_id, publication_id) == (USER, PUBLICATION)
        return copy.deepcopy(self.publication)

    async def get_asset(self, conn, user, project, asset):
        assert (user, project, asset) == (USER, PROJECT, "thumb")
        return {"r2_key": "owned/thumb.png", "mime_type": "image/png"}

    async def patch(self, conn, *, job_id, patch, lease_token):
        assert (job_id, lease_token) == (JOB, LEASE)
        if not self.alive or self.fail_patch in patch:
            self.alive = False
            return False
        self.patches.append(copy.deepcopy(patch))
        self.stored["metadata"].update(copy.deepcopy(patch))
        return True

    async def progress(self, conn, *, job_id, progress, lease_token):
        assert (job_id, lease_token) == (JOB, LEASE)
        assert 0 <= progress <= 100
        if self.fail_progress:
            self.alive = False
        return self.alive

    async def finalize(self, conn, *, job_id, lease_token, status, result, error_message=None):
        assert (job_id, lease_token) == (JOB, LEASE)
        if not self.alive:
            return False
        self.stored.update(status=status, result=copy.deepcopy(result), error_message=error_message)
        self.events.append("finalize")
        return True

    async def ensure(self, app, *, user_id, connection_id, rejected_token_expires_at=None):
        assert (user_id, connection_id) == (USER, CONNECTION)
        self.ensures.append(rejected_token_expires_at)
        if rejected_token_expires_at is not None:
            self.version += timedelta(seconds=1)
            self.token_generation += 1
        if hook := self.hooks.get("ensure"):
            hook()
        return f"fixture-token-{self.token_generation}", self.version

    async def mark(self, *args):
        return True

    async def sleep(self, delay):
        self.sleeps.append(delay)
        if hook := self.hooks.get("sleep"):
            hook()

    def get_bytes(self, key):
        assert not key.startswith("unsafe/")
        self.reads.append(key)
        if key == "owned/thumb.png":
            return self.asset_bytes.getvalue()
        return b"x" * getattr(self, "image_size", 10)

    async def remote(self, name, token, *args):
        assert self.events[-1] == "commit", "remote calls require a committed recheck"
        self.calls.append((name, token, args))
        self.events.append(name)
        if hook := self.hooks.get(name):
            hook()
        script = self.scripts.get(name, [])
        if script:
            value = script.pop(0)
            if isinstance(value, Exception):
                raise value
            return value
        if name == "upload":
            return [f"https://myshop.cafe24.com/upload/{len(self.calls)}-{i}.jpg" for i in range(len(args[0]))]
        return {"setting": "B", "create": 123, "find": [], "thumbnail": {},
                "store": {"mall_url": "https://shop.example/"}}[name]

    async def upload_description_images(self, token, images):
        return await self.remote("upload", token, images)

    async def product_setting(self, token):
        return await self.remote("setting", token)

    async def create_product(self, token, body):
        assert self.committed["metadata"]["create"]["sentAt"]
        assert self.committed["metadata"]["create"]["leaseToken"] == LEASE
        return await self.remote("create", token, body)

    async def find_products_by_custom_code(self, token, code):
        assert code == "wl" + JOB.replace("-", "")[:16]
        return await self.remote("find", token, code)

    async def upload_product_image(self, token, product_no, image):
        assert product_no == 123
        with Image.open(BytesIO(image)) as im:
            assert im.format == "JPEG" and max(im.size) == 2000 and im.mode == "RGB"
        return await self.remote("thumbnail", token, product_no, image)

    async def store(self, token):
        return await self.remote("store", token)

    def seed(self, metadata):
        self.job["metadata"] = copy.deepcopy(metadata)
        self.stored["metadata"] = copy.deepcopy(metadata)
        self.committed = copy.deepcopy(self.stored)

    def run(self):
        asyncio.run(self.worker.run_mall_register_job(self.app, self.job))

    def named(self, name):
        return [call for call in self.calls if call[0] == name]


@pytest.fixture
def env(monkeypatch):
    return WorkerEnv(monkeypatch)


@pytest.mark.parametrize("image_size,batches", [(10, [8, 1]), (3 * 1024 * 1024, [5, 4])])
def test_worker_uploads_images_in_worker_batches_then_creates_product_with_cafe24_urls(env, image_size, batches):
    env.image_size = image_size
    env.run()
    assert [len(call[2][0]) for call in env.named("upload")] == batches
    body = env.named("create")[0][2][0]
    request = body["request"]
    assert body["shop_no"] == 1 and request["price_excluding_tax"] == 39000 and "price" not in request
    assert request["supply_price"] == 30000 and request["selling"] == "F"
    assert request["custom_product_code"] == "wl" + JOB.replace("-", "")[:16]
    assert request["add_category_no"] == [{"category_no": 27, "recommend": "F", "new": "F"}]
    assert request["description"] == request["mobile_description"]
    assert request["description"].count('<img src="https://myshop.cafe24.com/') == 9
    assert "&lt;&quot;상품&quot;&gt;" in request["description"]
    assert env.stored["result"]["outcome"] == "created" and env.stored["result"]["thumbnail"] == "none"
    assert env.stored["result"]["productUrl"] == "https://shop.example/product/detail.html?product_no=123"
    from app.workers.dispatcher import _WORKERS
    assert _WORKERS["mall_register"] is env.worker.run_mall_register_job


def test_worker_resumes_after_partial_image_upload_without_reupload(env):
    urls = [f"https://saved.example/{i}.jpg" for i in range(8)]
    env.seed({"images": {"urls": urls}})
    env.run()
    assert len(env.named("upload")) == 1 and len(env.named("upload")[0][2][0]) == 1
    assert env.reads == [f"pub/{USER}/{PUBLICATION}/09.jpg"]
    assert env.stored["metadata"]["images"]["urls"][:8] == urls


@pytest.mark.parametrize("failure,delay", [(status_error(429, "120"), 60),
    (status_error(503), 1), (TimeoutError("private"), 1), (status_error(401), None)])
def test_worker_rechecks_before_each_write_and_retry(env, failure, delay):
    env.scripts["upload"] = [failure]
    env.run()
    assert len(env.named("upload")) == 3  # first batch attempted twice, second once
    if delay is not None:
        assert env.sleeps == [delay]
    for i, event in enumerate(env.events):
        if event in {"upload", "setting", "create", "thumbnail", "find", "store"}:
            assert env.events[i - 2:i] == ["recheck", "commit"]


@pytest.mark.parametrize("change", ["mall_connect_enabled", "detail_publish_enabled", "cafe24_enabled",
    "cafe24_client_id", "cafe24_client_secret", "mall_keys_kek", "allowlist"])
def test_worker_stops_before_next_write_when_any_required_flag_or_allowlist_changes(env, change):
    def disable():
        field, value = ("cafe24_allowed_mall_ids", frozenset({"another"})) if change == "allowlist" else (
            change, False if change.endswith("enabled") else "")
        env.app.state.settings = replace(env.app.state.settings, **{field: value})
    env.hooks["upload"] = disable
    env.run()
    assert [c[0] for c in env.calls] == ["upload"]
    assert env.stored["result"]["code"] == ("cafe24_not_allowed" if change == "allowlist" else "cafe24_disabled")


@pytest.mark.parametrize("when", ["upload", "sleep", "ensure", "401"])
def test_worker_stops_after_disconnect_mid_upload_without_more_remote_calls(env, when):
    env.hooks["upload" if when == "401" else when] = lambda: env.connection_row.update(status="revoked")
    if when == "sleep":
        env.scripts["upload"] = [status_error(503)]
    if when == "401":
        env.scripts["upload"] = [status_error(401)]
    env.run()
    assert len(env.calls) == (0 if when == "ensure" else 1)
    assert env.stored["result"]["code"] == "connection_changed"
    if when == "401":
        assert env.ensures == [None], "the changed connection must stop before token refresh"


def test_worker_stops_after_account_delete_mid_upload(env):
    env.hooks["upload"] = lambda: setattr(env, "closed", True)
    env.run()
    assert len(env.calls) == 1 and env.stored["result"]["code"] == "account_closed"


@pytest.mark.parametrize("error", [status_error(503), status_error(401), TimeoutError("private"),
    Cafe24ApiError("price_setting_unavailable")])
def test_worker_aborts_before_create_when_price_setting_unavailable(env, error):
    env.scripts["setting"] = [error, error, error]
    env.run()
    assert not env.named("create") and not env.stored["metadata"].get("create")
    assert env.stored["status"] == "error" and env.stored["result"]["code"] == "price_setting_unavailable"


@pytest.mark.parametrize("token_failure", [False, True])
def test_worker_records_sent_at_before_create_post(env, token_failure):
    if token_failure:
        def fail_before_create():
            if env.named("setting"):
                raise TimeoutError("private")
        env.hooks["ensure"] = fail_before_create
    env.run()
    if token_failure:
        assert not env.stored["metadata"].get("create")
        assert not env.named("create") and not env.named("find")
        assert env.stored["status"] == "error" and env.stored["result"]["code"] == "unreachable"
    else:
        assert env.stored["metadata"]["create"]["attempt"] == 1
        assert env.stored["metadata"]["create"]["sentAt"]


def test_worker_never_resends_create_after_sent_at(env):
    env.seed({"create": {"sentAt": "2026-10-26T00:00:00+00:00", "attempt": 1, "leaseToken": "old"}})
    env.run()
    assert [c[0] for c in env.calls] == ["find"] and not env.reads
    assert env.stored["status"] == "done" and env.stored["result"]["outcome"] == "unknown"


@pytest.mark.parametrize("status", [500, 429])
def test_worker_5xx_after_create_recovers_by_lookup_without_second_post(env, status):
    env.scripts.update(create=[status_error(status)], find=[[123]])
    env.run()
    assert len(env.named("create")) == len(env.named("find")) == 1
    assert env.stored["metadata"]["productNo"] == 123 and env.stored["result"]["outcome"] == "created"


@pytest.mark.parametrize("lookup", [[], TimeoutError("private")])
def test_worker_timeout_after_create_with_no_match_ends_done_unknown(env, lookup):
    env.scripts.update(create=[TimeoutError("private")], find=[lookup])
    env.run()
    assert len(env.named("create")) == len(env.named("find")) == 1
    assert env.stored["status"] == "done" and env.stored["result"]["outcome"] == "unknown"
    assert env.stored["metadata"]["create"]["sentAt"] and not env.stored["metadata"].get("productNo")


def test_worker_lookup_two_matches_ends_duplicate_suspected(env):
    env.scripts.update(create=[TimeoutError()], find=[[123, 456]])
    env.run()
    assert env.stored["status"] == "done" and env.stored["result"]["outcome"] == "duplicate_suspected"


@pytest.mark.parametrize("failures,outcome,attempts,refresh_error", [
    (1, "created", 2, None), (2, "unknown", 2, None), (1, "unknown", 1, status_error(400))])
def test_worker_401_refreshes_once_and_resends_with_new_attempt(env, failures, outcome, attempts, refresh_error):
    env.scripts["create"] = [status_error(401) for _ in range(failures)]
    if refresh_error:
        def fail_refresh():
            if env.ensures[-1] is not None:
                raise refresh_error
        env.hooks["ensure"] = fail_refresh
    env.run()
    assert len(env.named("create")) == attempts
    assert env.stored["metadata"]["create"]["attempt"] == attempts
    if attempts > 1:
        assert env.named("create")[0][1] != env.named("create")[1][1]
    assert any(version is not None for version in env.ensures)
    assert env.stored["result"]["outcome"] == outcome
    assert not env.stored["metadata"].get("rejectedAt")


@pytest.mark.parametrize("failure", [TimeoutError("private"), None])
def test_worker_thumbnail_failure_keeps_created_with_warning(env, failure):
    env.job["payload"]["thumbnailAssetId"] = "thumb"
    if failure:
        env.scripts["thumbnail"] = [failure] * 4
    env.run()
    assert env.stored["status"] == "done" and env.stored["result"]["outcome"] == "created"
    assert env.stored["result"]["thumbnail"] == ("failed" if failure else "ok")
    assert bool(env.stored["result"]["warnings"]) is bool(failure)


def test_disconnect_after_product_created_preserves_created_outcome(env):
    env.job["payload"]["thumbnailAssetId"] = "thumb"
    env.hooks["create"] = lambda: env.connection_row.update(status="revoked")
    env.run()
    assert env.stored["metadata"]["productNo"] == 123
    assert env.stored["status"] == "done" and env.stored["result"]["outcome"] == "created"
    assert env.stored["result"]["thumbnail"] == "skipped" and env.stored["result"]["warnings"]
    assert not env.named("thumbnail") and not env.named("store") and not env.last_registered


@pytest.mark.parametrize("rejected", [False, True])
def test_resume_uses_saved_product_no_without_lookup(env, rejected):
    env.seed({"productNo": 123, "create": {"sentAt": "saved"}, **({"rejectedAt": "saved"} if rejected else {})})
    env.run()
    assert not env.named("find") and not env.named("create") and not env.named("upload")
    assert env.stored["result"]["outcome"] == "created"


@pytest.mark.parametrize("loss", ["initial", "create_patch", "product_patch", "progress", "retry", "thumbnail"])
def test_worker_lease_loss_writes_nothing_and_makes_no_remote_call(env, loss):
    if loss == "initial":
        env.alive = False
    elif loss.endswith("patch"):
        env.fail_patch = "create" if loss == "create_patch" else "productNo"
    elif loss == "progress":
        env.fail_progress = True
    elif loss == "retry":
        env.scripts["upload"] = [status_error(503)]
        env.hooks["sleep"] = lambda: setattr(env, "alive", False)
    else:
        env.job["payload"]["thumbnailAssetId"] = "thumb"
        env.hooks["thumbnail"] = lambda: setattr(env, "alive", False)
    env.run()
    assert "finalize" not in env.events and not env.last_registered
    if loss in {"initial", "create_patch"}:
        assert not env.named("create")
    if loss == "initial":
        assert not env.calls and not env.patches and not env.reads
    if loss == "product_patch":
        assert len(env.named("create")) == 1 and not env.named("find")
    if loss in {"retry", "progress"}:
        assert len(env.calls) == 1
    if loss == "thumbnail":
        assert not env.named("store")


@pytest.mark.parametrize("changed", [False, True])
def test_worker_updates_last_registered_at_only_for_same_connection(env, changed):
    if changed:
        env.hooks["create"] = lambda: env.connection_row.update(id="new-connection")
    env.run()
    assert env.last_registered == ([] if changed else [CONNECTION])
    assert env.stored["result"]["outcome"] == "created"


@pytest.mark.parametrize("code", ["image_upload_mismatch", "rate_limited", "invalid_request"])
def test_registration_failure_preserves_seller_code_and_copy(env, caplog, code):
    env.scripts["upload"] = ([[]] if code == "image_upload_mismatch" else
        [status_error(429 if code == "rate_limited" else 422)] * 4)
    env.run()
    assert env.stored["status"] == "error" and env.stored["result"]["code"] == code
    assert env.stored["error_message"] == ERROR_COPY[code]
    assert env.stored["result"]["outcome"] == "failed"
    assert "upstream-private" not in repr(env.stored) + caplog.text


@pytest.mark.parametrize("status", [400, 422])
@pytest.mark.parametrize("resume", [False, True])
def test_worker_definite_rejection_after_send_ends_failed_not_unknown(env, status, resume):
    if resume:
        env.seed({"create": {"sentAt": "saved"}, "rejectedAt": "saved", "code": "invalid_request"})
    else:
        env.scripts["create"] = [status_error(status)]
    env.run()
    assert len(env.named("create")) == (0 if resume else 1) and not env.named("find")
    assert env.stored["metadata"]["rejectedAt"] and env.stored["metadata"]["code"] == "invalid_request"
    assert env.stored["status"] == "error" and env.stored["result"]["outcome"] == "failed"
    assert env.stored["error_message"] == ERROR_COPY["invalid_request"]
    assert env.stored["metadata"].get("rejectedAt")
