"""Verify the registration ledger through real repository SQL and a fake Cafe24."""

import copy
import uuid
from datetime import datetime, timezone

import httpx
import pytest

from app import mall_connect
from app.services.cafe24_client import Cafe24Client, ConnectionChanged, ReconnectRequired
from conftest import assert_query_binds
import test_mall_cafe24_register_routes as registration_tests
from test_mall_cafe24_register_routes import (
    BASE, RegisterConn, RegisterCursor, assert_error, env as registration_env, submit, token_env,
)
from test_mall_cafe24_tokens import ACCESS
from test_mall_connect import OTHER, USER


THUMBNAIL_WARNING = "대표 이미지는 카페24 관리자에서 넣어 주세요"
CONNECTION_MESSAGE = "연결이 바뀌어 확인할 수 없어요. 카페24 관리자에서 상품 목록을 확인해 주세요"


class VerifyCursor(RegisterCursor):
    async def execute(self, sql, args=()):
        assert_query_binds(sql, args)
        q = " ".join(sql.lower().split())
        if q.startswith(("update jobs set metadata", "update jobs set result")) and args[-1] == ["done", "error"]:
            assert "user_id = %s" in q and "status = any(%s)" in q
            assert "locked_by" not in q
            patch, jid, user, statuses = args
            column = "metadata" if q.startswith("update jobs set metadata") else "result"
            row = next((r for r in self.store["jobs"] if r["id"] == jid
                        and r["user_id"] == user and r["status"] in statuses), None)
            self.rowcount = int(row is not None)
            if row:
                row[column] = {**(row[column] or {}), **copy.deepcopy(patch.obj)}
            self.store["patches"].append((column, copy.deepcopy(patch.obj), user, statuses))
            return
        await super().execute(sql, args)


class VerifyConn(RegisterConn):
    def cursor(self):
        return VerifyCursor(self.store)


@pytest.fixture
def env(registration_env, monkeypatch):
    client, store = registration_env
    monkeypatch.setattr(registration_tests, "RegisterConn", VerifyConn)
    first = submit(client, thumbnailAssetId=registration_tests.ASSET)
    assert first.status_code == 202, first.text
    job = store["jobs"][0]
    job.update(status="done", progress=100, finished_at=datetime.now(timezone.utc))
    job["metadata"].update(create={"sentAt": "2026-10-26T00:00:00+00:00", "attempt": 1},
                           images={"urls": ["https://fake.invalid/1.jpg", "https://fake.invalid/2.jpg"]})
    job["result"] = dict(outcome="unknown", imageCount=2, externalCode=job["metadata"]["externalCode"],
                         warnings=["기존 안내"], code="unreachable")
    store.update(matches=[], patches=[], remote=[])

    def respond(request):
        store["remote"].append((request.method, request.url.path))
        assert request.method == "GET", "verify must never create a product or upload images"
        assert request.url.host == "myshop.cafe24api.com"
        assert request.headers["Authorization"] == f"Bearer {ACCESS}"
        if request.url.path.endswith("/admin/store"):
            return httpx.Response(200, json={"store": {"mall_url": "https://myshop.cafe24.com"}})
        assert request.url.path.endswith("/admin/products")
        assert request.url.params["custom_product_code"] == job["metadata"]["externalCode"]
        if hook := store.pop("during_lookup", None):
            hook()
        # Cafe24 searches substrings. Only exact matches count as recovered products.
        products = [dict(product_no=number, custom_product_code=job["metadata"]["externalCode"])
                    for number in store["matches"]]
        products.append(dict(product_no=999, custom_product_code=job["metadata"]["externalCode"] + "extra"))
        return httpx.Response(200, json={"products": products})

    monkeypatch.setattr(mall_connect, "Cafe24Client", lambda *a, **kw:
        Cafe24Client(*a, **{**kw, "transport": httpx.MockTransport(respond)}))
    return client, store, job


def verify(client, job_id):
    return client.post(f"{BASE}/{job_id}/verify")


@pytest.mark.parametrize("case,status,code", [
    ("foreign", 404, "registration_not_found"), ("missing", 404, "registration_not_found"),
    ("invalid_id", 404, "registration_not_found"), ("kind", 404, "registration_not_found"),
    ("pending", 409, "not_verifiable"), ("running", 409, "not_verifiable"),
    ("created", 409, "not_verifiable"), ("failed", 409, "not_verifiable"),
    ("rejected", 409, "not_verifiable"), ("closed_account", 404, "account_closed"),
    ("unauthenticated", 401, None),
])
def test_verify_requires_owner_and_verifiable_outcome(env, case, status, code):
    client, store, job = env
    jid = job["id"]
    if case == "foreign": job["user_id"] = OTHER
    if case == "missing": jid = str(uuid.uuid4())
    if case == "invalid_id": jid = "invalid"
    if case == "kind": job["kind"] = "export"
    if case in ("pending", "running"): job["status"] = case
    if case == "created": job["metadata"]["productNo"] = 42
    if case == "failed": job["metadata"].pop("create")
    if case == "rejected": job["metadata"]["rejectedAt"] = "2026-10-26T00:01:00+00:00"
    if case == "closed_account": store["closed_accounts"].add(USER)
    if case == "unauthenticated": client.app.dependency_overrides.clear()
    before = copy.deepcopy(job)
    response = verify(client, jid)
    if code:
        assert_error(response, status, code)
    else:
        assert response.status_code == status
    assert job == before and not store["remote"] and not store["patches"]
    assert store["attempts"] == {f"mall-register:{USER}": 1}


def test_verify_accepts_error_status_job_with_sent_at(env):
    client, store, job = env
    job.update(status="error", result=None, error_message="작업 서버가 응답하지 않아 종료됐어요")
    store["matches"] = [42]
    response = verify(client, job["id"])
    assert response.status_code == 200, response.text
    view = response.json()
    assert (view["status"], view["outcome"], view["productNo"]) == ("error", "created", 42)
    assert view["errorMessage"] is None and view["verifiable"] is False
    assert THUMBNAIL_WARNING in view["warnings"]
    assert client.get(f'{BASE}/{job["id"]}').json()["productNo"] == 42


@pytest.mark.parametrize("status", ["done", "error"])
@pytest.mark.parametrize("outcome", ["unknown", "duplicate_suspected"])
def test_verify_found_one_sets_created_with_skipped_thumbnail_and_keeps_status(env, status, outcome):
    client, store, job = env
    job["status"], job["result"]["outcome"] = status, outcome
    store["matches"] = [42]
    before = copy.deepcopy(job)
    started = datetime.now(timezone.utc)
    response = verify(client, job["id"])
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    view = response.json()
    assert (view["status"], view["outcome"], view["productNo"], view["thumbnail"]) == (status, "created", 42, "skipped")
    assert view["productUrl"] == "https://myshop.cafe24.com/product/detail.html?product_no=42"
    assert view["imageCount"] == 2 and view["verifiable"] is False and view["errorMessage"] is None
    assert view["code"] is None
    assert view["warnings"][:2] == ["기존 안내", THUMBNAIL_WARNING]
    assert started <= datetime.fromisoformat(job["result"]["verifiedAt"]) <= datetime.now(timezone.utc)
    assert job["metadata"] == {**before["metadata"], "productNo": 42}
    assert job["result"]["productNo"] == 42
    assert {k: v for k, v in job.items() if k not in ("metadata", "result")} == {
        k: v for k, v in before.items() if k not in ("metadata", "result")}
    assert [p[0] for p in store["patches"]] == ["metadata", "result"]
    assert all(p[2:] == (USER, ["done", "error"]) for p in store["patches"])
    assert all(method == "GET" for method, _ in store["remote"])
    assert ACCESS not in response.text
    assert_error(verify(client, job["id"]), 409, "not_verifiable")


@pytest.mark.parametrize("already_created", [False, True])
def test_verify_found_two_sets_duplicate_suspected(env, already_created):
    client, store, job = env
    store["matches"] = [42, 43]
    if already_created:
        def confirmed_elsewhere():
            job["metadata"]["productNo"] = 41
            job["result"].update(outcome="created", productNo=41, thumbnail="skipped")
        store["during_lookup"] = confirmed_elsewhere
    before = copy.deepcopy(job)
    response = verify(client, job["id"])
    assert response.status_code == 200, response.text
    view = response.json()
    if already_created:
        assert view["outcome"] == "created" and view["productNo"] == 41
        assert job["result"]["productNo"] == 41 and not store["patches"]
        return
    assert view["outcome"] == "duplicate_suspected" and view["verifiable"] is True
    assert view["productNo"] is None and job["metadata"] == before["metadata"]
    assert job["status"] == "done"
    assert "같은 코드의 상품이 여러 개예요. 카페24 관리자에서 확인해 주세요" in view["warnings"]
    assert [p[0] for p in store["patches"]] == ["result"]


@pytest.mark.parametrize("outcome", ["unknown", "duplicate_suspected"])
def test_verify_found_none_keeps_unknown_and_never_creates(env, outcome):
    client, store, job = env
    job["result"]["outcome"] = outcome
    before = copy.deepcopy(job)
    started = datetime.now(timezone.utc)
    response = verify(client, job["id"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["found"] is False
    assert started <= datetime.fromisoformat(body["checkedAt"]) <= datetime.now(timezone.utc)
    assert body["message"] == "아직 확인되지 않았어요. 잠시 뒤 다시 확인하거나 카페24 관리자 상품 목록을 봐 주세요"
    assert job == before and not store["patches"]
    assert store["remote"] == [("GET", "/api/v2/admin/products")]


@pytest.mark.parametrize("change", ["disconnected", "replaced", "expired", "during_lookup", "wrong_mall",
                                     "token_changed", "token_reconnect"])
def test_verify_connection_changed_409(env, monkeypatch, change):
    client, store, job = env
    store["matches"] = [42]
    connection = store["connections"][0]
    if change == "disconnected": connection["status"] = "revoked"
    if change == "replaced": connection["id"] = str(uuid.uuid4())
    if change == "expired": connection["refresh_expires_at"] = datetime.now(timezone.utc)
    if change == "wrong_mall": job["payload"]["mallId"] = "othermall"
    if change == "during_lookup": store["during_lookup"] = lambda: connection.update(status="revoked")
    if change in ("token_changed", "token_reconnect"):
        async def token_error(*args, **kwargs):
            raise ConnectionChanged() if change == "token_changed" else ReconnectRequired()
        monkeypatch.setattr(mall_connect, "call_with_token", token_error)
    before = copy.deepcopy(job)
    response = verify(client, job["id"])
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] in ("connection_changed", "reconnect_required")
    assert response.json()["error"]["message"] == CONNECTION_MESSAGE
    assert job == before and not store["patches"]
    assert ACCESS not in response.text


def test_verify_rate_limited(env):
    client, store, job = env
    for _ in range(6):
        response = verify(client, job["id"])
        assert response.status_code == 200, response.text
    assert_error(verify(client, job["id"]), 429, "rate_limited")
    assert len(store["remote"]) == 6
    assert store["attempts"] == {f"mall-register:{USER}": 1, f"mall-verify:{USER}": 6}
    # Limits are shared by the user's jobs but independent from install and registration.
    other_job = {**copy.deepcopy(job), "id": str(uuid.uuid4())}
    store["jobs"].append(other_job)
    assert_error(verify(client, other_job["id"]), 429, "rate_limited")
    assert any("interval '1 minute'" in sql for sql in store["sql"] if "client_event_rate_limits" in sql)
