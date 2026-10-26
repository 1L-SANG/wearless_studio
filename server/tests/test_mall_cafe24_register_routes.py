"""Registration HTTP contracts using real repository projections and no network."""

import copy
import uuid
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from app import mall_connect, repo
from app.auth import require_user
from app.services.cafe24_client import Cafe24Client
from conftest import assert_query_binds
from test_mall_cafe24_tokens import ACCESS, CONNECTION, TokenConn, TokenCursor, env as token_env
from test_mall_connect import OTHER, USER


BASE = "/v1/malls/cafe24/registrations"
PROJECT, PUBLICATION, ASSET = (str(uuid.UUID(int=i)) for i in (10, 11, 12))
PROJECT2 = str(uuid.UUID(int=13))
BODY = dict(projectId=PROJECT, publicationId=PUBLICATION, requestKey=str(uuid.UUID(int=14)),
            productName="데일리 니트", price=39000, display="T", selling="T", taxType="A")


def project_row(project_id=PROJECT, user_id=USER):
    return dict(id=project_id, user_id=user_id, deleted_at=None)


def publication_row():
    return dict(id=PUBLICATION, user_id=USER, project_id=PROJECT, status="ready", width=860,
                files=[dict(index=1, width=860, height=1000, bytes=100, r2_key="other/unsafe.jpg")])


class RegisterCursor(TokenCursor):
    async def execute(self, sql, args=()):
        assert_query_binds(sql, args)
        q = " ".join(sql.lower().split())
        self.result, self.rowcount = [], 0
        if q.startswith(("savepoint ", "release savepoint ", "rollback to savepoint ")):
            return
        rows = None
        if "from projects " in q:
            assert "user_id = %s" in q and "deleted_at is null" in q
            pid, user = args
            rows = [r for r in self.store["projects"] if r["id"] == pid and r["user_id"] == user
                    and r["deleted_at"] is None]
        elif "from fm_output_records" in q:
            assert "j.project_id = %s" in q and "r.seller_id = %s" in q
            rows = [{"present": 1}] if tuple(args) in self.store["real_models"] else []
        elif "from detail_publications" in q:
            assert "user_id = %s" in q and "for update" in q
            pid, user = args
            self.store["publication_locked"] = True
            rows = [r for r in self.store["publications"] if r["id"] == pid and r["user_id"] == user]
        elif "from assets " in q:
            assert "user_id = %s" in q and "project_id = %s" in q and "deleted_at is null" in q
            aid, user, pid = args
            rows = [r for r in self.store["assets"] if (r["id"], r["user_id"], r["project_id"]) == (aid, user, pid)
                    and r["deleted_at"] is None]
        elif q.startswith("insert into jobs"):
            assert not self.store["publication_locked"], "release the publication row lock before job creation"
            user, pid, kind, payload, key, credits, metadata = args
            assert credits == 0 and kind == "mall_register"
            jobs = self.store["jobs"]
            conflict = any(r["idempotency_key"] == key or (r["project_id"] == pid and r["kind"] == kind
                           and r["status"] in ("pending", "running")) for r in jobs)
            rows = []
            if not conflict:
                now = datetime.now(timezone.utc)
                row = dict(id=str(uuid.uuid4()), user_id=user, project_id=pid, kind=kind, status="pending",
                           progress=0, steps=[], payload=copy.deepcopy(payload.obj), result=None, error_message=None,
                           idempotency_key=key, metadata=copy.deepcopy(metadata.obj), credits_reserved=credits,
                           credits_charged=0, created_at=now, updated_at=now, finished_at=None)
                jobs.append(row)
                rows = [row]
        elif q.startswith("update jobs set metadata"):
            patch, jid, user, statuses = args
            assert "user_id = %s" in q and "status = any(%s)" in q and statuses == ["pending"]
            row = next(r for r in self.store["jobs"] if r["id"] == jid)
            if self.store.get("claim_before_patch"):
                row["status"] = "running"
            if row["user_id"] == user and row["status"] in statuses:
                row["metadata"].update(patch.obj)
                self.rowcount = 1
            return
        elif "from jobs" in q and "as closed" not in q:
            assert "user_id = %s" in q
            jobs = self.store["jobs"]
            if "where idempotency_key" in q:
                key, user, *kinds = args
                rows = [r for r in jobs if r["idempotency_key"] == key and r["user_id"] == user
                        and (not kinds or r["kind"] == kinds[0])]
            elif "payload->>'mallid'" in q:
                user, pid, mall_id = args
                rows = [r for r in jobs if (r["user_id"], r["project_id"], r["kind"]) == (user, pid, "mall_register")
                        and r["payload"].get("mall") == "cafe24" and r["payload"].get("mallId") == mall_id
                        and (r["status"] in ("pending", "running") or (r["status"] in ("done", "error", "cancelled")
                             and not r["metadata"].get("productNo") and not r["metadata"].get("rejectedAt")
                             and (r["metadata"].get("create") or {}).get("sentAt")))]
                rows = sorted(rows, key=lambda r: (r["created_at"], r["id"]), reverse=True)[:1]
            elif "where id = %s" in q:
                jid, user = args
                rows = [r for r in jobs if r["id"] == jid and r["user_id"] == user]
            elif "limit %s" in q:
                user, pid, kind, limit = args
                assert "order by created_at desc" in q
                rows = sorted([r for r in jobs if (r["user_id"], r["project_id"], r["kind"]) == (user, pid, kind)],
                              key=lambda r: (r["created_at"], r["id"]), reverse=True)[:limit]
            else:
                pid, user, kind = args
                rows = [r for r in jobs if (r["project_id"], r["user_id"], r["kind"]) == (pid, user, kind)
                        and r["status"] in ("pending", "running")]
            if q.startswith("select idempotency_key, project_id::text, payload"):
                self.result = [{k: copy.deepcopy(r[k]) for k in ("idempotency_key", "project_id", "payload")} for r in rows]
                self.store["identity_reads"] += 1
                return
        if rows is not None:
            self.store["sql"].append(q)
            # This matches _JOB_COLS: create_job/get_job never return the idempotency key.
            self.result = [{k: copy.deepcopy(v) for k, v in r.items() if k != "idempotency_key"} for r in rows]
            return
        await super().execute(sql, args)

    async def fetchall(self):
        return self.result


class RegisterConn(TokenConn):
    def cursor(self):
        return RegisterCursor(self.store)

    async def commit(self):
        self.store["publication_locked"] = False
        self.store["commits"] += 1
        await super().commit()
        self.committed_jobs_attempts = copy.deepcopy((self.store["jobs"], self.store["attempts"]))


class RegisterPool:
    def __init__(self, store):
        self.store = store

    @asynccontextmanager
    async def connection(self):
        before = copy.deepcopy((self.store["jobs"], self.store["attempts"]))
        conn = RegisterConn(self.store)
        try:
            yield conn
            await conn.commit()
        except BaseException:
            self.store["jobs"], self.store["attempts"] = getattr(conn, "committed_jobs_attempts", before)
            self.store["publication_locked"] = False
            raise


@pytest.fixture
def env(token_env):
    client, store = token_env
    store.update(projects=[project_row(), project_row(PROJECT2)], publications=[publication_row()],
                 assets=[dict(id=ASSET, user_id=USER, project_id=PROJECT, mime_type="image/jpeg", deleted_at=None)],
                 jobs=[], real_models=set(), identity_reads=0, publication_locked=False, commits=0, wakes=0)
    client.app.state.settings = replace(client.app.state.settings, detail_publish_enabled=True)
    client.app.state.pool = RegisterPool(store)
    def wake():
        assert not store["publication_locked"] and store["commits"] > 0
        store["wakes"] += 1
    client.app.state.dispatcher = SimpleNamespace(wake=wake)
    return client, store


def submit(client, **changes):
    return client.post(BASE, json={**BODY, **changes})


def assert_error(response, status, code):
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code


@pytest.mark.parametrize("case,code", [
    ("missing_project", "project_not_found"), ("foreign_project", "project_not_found"),
    ("deleted_project", "project_not_found"), ("closed_account", "account_closed"),
    ("missing_publication", "publication_not_found"), ("foreign_publication", "publication_not_found"),
    ("pending_publication", "publication_not_found"), ("other_project_publication", "publication_not_found"),
    ("invalid_files", "invalid_files"),
    *[(case, "validation_error") for case in ("price_zero", "price_overflow", "price_float", "price_boolean",
        "price_string", "supply_negative", "product_empty", "product_long", "request_key_not_uuid",
        "invalid_display", "invalid_selling", "invalid_tax", "invalid_category", "invalid_thumbnail")],
])
def test_create_registration_requires_live_owned_project_and_ready_publication_match(env, case, code):
    client, store = env
    if case == "missing_project": store["projects"] = []
    if case == "foreign_project": store["projects"][0]["user_id"] = OTHER
    if case == "deleted_project": store["projects"][0]["deleted_at"] = datetime.now(timezone.utc)
    if case == "closed_account": store["closed_accounts"].add(USER)
    if case == "missing_publication": store["publications"] = []
    if case == "foreign_publication": store["publications"][0]["user_id"] = OTHER
    if case == "pending_publication": store["publications"][0]["status"] = "pending"
    if case == "other_project_publication": store["publications"][0]["project_id"] = PROJECT2
    if case == "invalid_files": store["publications"][0]["files"][0]["index"] = 2
    invalid_input = {"price_zero": {"price": 0}, "price_overflow": {"price": 2147483648},
        "price_float": {"price": 1.5}, "price_boolean": {"price": True}, "price_string": {"price": "39000"},
        "supply_negative": {"supplyPrice": -1}, "product_empty": {"productName": ""},
        "product_long": {"productName": "가" * 101}, "request_key_not_uuid": {"requestKey": "bad"},
        "invalid_display": {"display": "yes"}, "invalid_selling": {"selling": "yes"},
        "invalid_tax": {"taxType": "C"}, "invalid_category": {"categoryNo": 0},
        "invalid_thumbnail": {"thumbnailAssetId": "bad"}}
    status = 422 if code == "validation_error" else 400 if code == "invalid_files" else 404
    assert_error(submit(client, **invalid_input.get(case, {})), status, code)
    assert not store["jobs"] and not store["attempts"] and not store["remote"]


def test_create_registration_blocks_real_model_project(env):
    client, store = env
    store["real_models"].add((PROJECT, USER))
    store["connections"].clear()  # The real-model check precedes the connection check.
    response = submit(client)
    assert_error(response, 403, "real_model_not_allowed")
    assert response.json()["error"]["message"] == "실존 모델 컷은 다운로드로 받아 몰에 직접 올려 주세요."
    assert not store["jobs"]


@pytest.mark.parametrize("case", ["foreign_project", "foreign_owner", "deleted", "not_image"])
def test_create_registration_rejects_asset_from_other_project(env, case):
    client, store = env
    asset = store["assets"][0]
    if case == "foreign_project": asset["project_id"] = PROJECT2
    if case == "foreign_owner": asset["user_id"] = OTHER
    if case == "deleted": asset["deleted_at"] = datetime.now(timezone.utc)
    if case == "not_image": asset["mime_type"] = "application/pdf"
    assert_error(submit(client, thumbnailAssetId=ASSET), 404, "asset_not_found")
    assert not store["jobs"] and not store["attempts"]


@pytest.mark.parametrize("case,code", [("expired", "reconnect_required"), ("missing_expiry", "reconnect_required"),
                                      ("not_allowed", "cafe24_not_allowed"), ("missing", "not_connected")])
def test_create_registration_409_when_reconnect_required_or_not_allowed(env, case, code):
    client, store = env
    if case == "expired": store["connections"][0]["refresh_expires_at"] = datetime.now(timezone.utc)
    if case == "missing_expiry": store["connections"][0]["refresh_expires_at"] = None
    if case == "not_allowed": client.app.state.settings = replace(client.app.state.settings, cafe24_allowed_mall_ids=frozenset({"other"}))
    if case == "missing": store["connections"].clear()
    assert_error(submit(client), 404 if case == "missing" else 409, code)
    assert not store["jobs"] and not store["remote"]


@pytest.mark.parametrize("flag,value", [("mall_connect_enabled", False), ("detail_publish_enabled", False),
    ("cafe24_enabled", False), ("cafe24_client_id", ""), ("cafe24_client_secret", ""), ("mall_keys_kek", "")])
def test_create_registration_503_when_any_required_flag_off(env, flag, value):
    client, store = env
    client.app.state.settings = replace(client.app.state.settings, **{flag: value})
    assert_error(submit(client), 503, "cafe24_unconfigured")
    assert not store["sql"] and not store["jobs"]


@pytest.mark.parametrize("status", ["pending", "running", "done", "error"])
def test_same_request_retry_returns_original_job_using_actual_repo_projection(env, status):
    client, store = env
    first = submit(client)
    assert first.status_code == 202, first.text
    job = store["jobs"][0]
    job["status"] = status
    if status == "done":
        job["metadata"]["productNo"] = 42
        job["result"] = dict(outcome="created", productNo=42)
    # Reconnecting to the same mall may change only connectionId.
    store["connections"][0]["id"] = str(uuid.uuid4())
    retry = submit(client, supplyPrice=39000)
    assert retry.status_code == 200, retry.text
    assert retry.json()["jobId"] == first.json()["jobId"]
    assert retry.json()["status"] == status
    assert len(store["jobs"]) == 1 and store["wakes"] == 1
    assert store["attempts"] == {f"mall-register:{USER}": 1}
    assert "idempotency_key" not in repo._JOB_COLS


@pytest.mark.parametrize("change", ["quota", "publication", "connection", "project", "mall", "real_model", "thumbnail"])
def test_accepted_request_replay_survives_changed_resources_without_new_quota(env, change):
    client, store = env
    first = submit(client, thumbnailAssetId=ASSET)
    assert first.status_code == 202, first.text
    job = store["jobs"][0]
    job.update(status="done", metadata={"productNo": 42}, result={"outcome": "created"})
    store["attempts"][f"mall-register:{USER}"] = 10
    if change == "publication": store["publications"].clear()
    if change == "connection": store["connections"][0]["status"] = "revoked"
    if change == "project": store["projects"][0]["deleted_at"] = datetime.now(timezone.utc)
    if change == "mall": store["connections"][0]["shop_id"] = "another-store"
    if change == "real_model": store["real_models"].add((PROJECT, USER))
    if change == "thumbnail": store["assets"][0]["deleted_at"] = datetime.now(timezone.utc)
    retry = submit(client, thumbnailAssetId=ASSET, supplyPrice=39000)
    assert retry.status_code == 200, retry.text
    assert retry.json()["jobId"] == first.json()["jobId"]
    assert retry.json()["productNo"] == 42 and retry.json()["outcome"] == "created"
    assert store["attempts"] == {f"mall-register:{USER}": 10}
    assert len(store["jobs"]) == 1 and store["wakes"] == 1


def test_accepted_request_replay_still_denies_closed_account_and_disabled_feature(env):
    client, store = env
    assert submit(client).status_code == 202
    store["closed_accounts"].add(USER)
    assert_error(submit(client), 404, "account_closed")
    store["closed_accounts"].clear()
    client.app.state.settings = replace(client.app.state.settings, cafe24_enabled=False)
    assert_error(submit(client), 503, "cafe24_unconfigured")
    assert store["attempts"] == {f"mall-register:{USER}": 1}


@pytest.mark.parametrize("field,value", [("price", 40000), ("productName", "다른 상품"),
    ("projectId", PROJECT2), ("publicationId", str(uuid.UUID(int=99))), ("supplyPrice", 0),
    ("display", "F"), ("selling", "F"), ("taxType", "B"), ("categoryNo", 27),
    ("thumbnailAssetId", ASSET)])
def test_reused_key_changed_input_is_409_even_after_resources_removed_and_quota_full(env, field, value):
    client, store = env
    assert submit(client).status_code == 202
    store["publications"].clear()
    store["connections"].clear()
    store["projects"].clear()
    store["attempts"][f"mall-register:{USER}"] = 10
    assert_error(submit(client, **{field: value}), 409, "idempotency_key_reused")
    assert len(store["jobs"]) == 1 and store["attempts"] == {f"mall-register:{USER}": 10}


@pytest.mark.parametrize("change", ["owner", "kind"])
def test_accepted_key_lookup_never_replays_other_owner_or_kind(env, change):
    client, store = env
    assert submit(client).status_code == 202
    job = store["jobs"][0]
    job["user_id" if change == "owner" else "kind"] = OTHER if change == "owner" else "export"
    store["projects"].clear()
    assert_error(submit(client), 404, "project_not_found")
    assert store["attempts"] == {f"mall-register:{USER}": 1}


@pytest.mark.parametrize("change", [dict(price=40000), dict(productName="다른 상품"), dict(projectId=PROJECT2)])
def test_same_request_key_with_different_input_or_project_is_409(env, change):
    client, store = env
    assert submit(client).status_code == 202
    store["jobs"][0]["status"] = "done"
    if "projectId" in change:
        other = {**publication_row(), "id": str(uuid.uuid4()), "project_id": PROJECT2}
        store["publications"].append(other)
        change = {**change, "publicationId": other["id"]}
    assert_error(submit(client, **change), 409, "idempotency_key_reused")
    assert len(store["jobs"]) == 1 and store["wakes"] == 1


def test_create_registration_409_when_other_registration_active_with_job_id(env):
    client, store = env
    first = submit(client)
    assert first.status_code == 202
    response = submit(client, requestKey=str(uuid.uuid4()))
    assert_error(response, 409, "registration_in_progress")
    assert response.json()["error"]["jobId"] == first.json()["jobId"]
    assert len(store["jobs"]) == 1 and store["wakes"] == 1
    assert store["attempts"] == {f"mall-register:{USER}": 2}


def test_create_registration_rate_limited(env):
    client, store = env
    for _ in range(10):
        assert submit(client, requestKey=str(uuid.uuid4())).status_code == 202
        store["jobs"][-1]["status"] = "done"
    assert_error(submit(client, requestKey=str(uuid.uuid4())), 429, "rate_limited")
    assert store["attempts"] == {f"mall-register:{USER}": 10}
    assert len(store["jobs"]) == 10 and store["wakes"] == 10


@pytest.mark.parametrize("claimed", [False, True])
def test_create_registration_sets_external_code_from_job_id_only_while_pending(env, claimed):
    client, store = env
    store["claim_before_patch"] = claimed
    response = submit(client, thumbnailAssetId=ASSET, categoryNo=27)
    assert response.status_code == 202, response.text
    job = store["jobs"][0]
    code = "wl" + uuid.UUID(job["id"]).hex[:16]
    assert response.json() == dict(jobId=job["id"], status="queued", externalCode=code)
    assert job["metadata"].get("externalCode") == (None if claimed else code)
    assert job["payload"] == dict(mall="cafe24", connectionId=CONNECTION, mallId="myshop", width=860,
        **{k: v for k, v in BODY.items() if k != "projectId"}, supplyPrice=39000, thumbnailAssetId=ASSET, categoryNo=27)
    assert job["credits_reserved"] == 0 and job["metadata"]["stage"] == "queued"
    assert not store["remote"]
    for secret in (ACCESS, "fixture-refresh-private", client.app.state.settings.cafe24_client_secret):
        assert secret not in response.text and secret not in str(job)


def test_registration_get_and_list_are_owner_scoped_and_kind_checked(env):
    client, store = env
    first = submit(client)
    assert first.status_code == 202
    original = store["jobs"][0]
    original["status"] = "done"
    for i in range(7):
        store["jobs"].append({**copy.deepcopy(original), "id": str(uuid.uuid4()),
                              "created_at": original["created_at"] + timedelta(seconds=i + 1)})
    for changes in (dict(user_id=OTHER), dict(kind="export"), dict(project_id=PROJECT2)):
        store["jobs"].append({**copy.deepcopy(original), "id": str(uuid.uuid4()), **changes})
    foreign, wrong_kind, other_project = store["jobs"][-3:]
    for jid in (foreign["id"], wrong_kind["id"], str(uuid.uuid4()), "invalid"):
        assert_error(client.get(BASE + "/" + jid), 404, "registration_not_found")
    assert client.get(BASE + "/" + original["id"]).json()["jobId"] == original["id"]
    response = client.get(f"/v1/projects/{PROJECT}/mall-registrations")
    assert response.status_code == 200, response.text
    expected = [r["id"] for r in sorted(store["jobs"][:8], key=lambda r: r["created_at"], reverse=True)[:5]]
    assert [r["jobId"] for r in response.json()["items"]] == expected
    store["projects"][0]["user_id"] = OTHER
    assert_error(client.get(f"/v1/projects/{PROJECT}/mall-registrations"), 404, "project_not_found")
    client.app.dependency_overrides.clear()
    for path in (BASE + "/" + original["id"], f"/v1/projects/{PROJECT}/mall-registrations", "/v1/malls/cafe24/categories"):
        assert client.get(path).status_code == 401
    assert submit(client).status_code == 401


@pytest.mark.parametrize("status,result", [("pending", None), ("running", None),
    ("done", {"outcome": "unknown"}), ("error", {"outcome": "duplicate_suspected"}),
    ("cancelled", {"outcome": "unknown"})])
def test_registration_list_recovers_current_store_unresolved_beyond_latest_five(env, status, result):
    client, store = env
    assert submit(client).status_code == 202
    original = store["jobs"][0]
    original.update(status=status, metadata={"create": {"sentAt": "2026-10-27T00:00:00Z"}}, result=result)
    for i in range(6):
        recent = copy.deepcopy(original)
        recent.update(id=str(uuid.uuid4()), status="done", metadata={"productNo": i + 100},
                      created_at=original["created_at"] + timedelta(seconds=i + 1))
        store["jobs"].append(recent)
    for changes in (dict(user_id=OTHER), dict(kind="export"), dict(project_id=PROJECT2),
                    dict(payload={**original["payload"], "mallId": "another-store"})):
        store["jobs"].append({**copy.deepcopy(original), "id": str(uuid.uuid4()),
                              "created_at": original["created_at"] + timedelta(minutes=1), **changes})
    response = client.get(f"/v1/projects/{PROJECT}/mall-registrations")
    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data["items"]) == 5 and original["id"] not in [row["jobId"] for row in data["items"]]
    assert data["recovery"]["jobId"] == original["id"] and data["recovery"]["status"] == status
    assert data["recovery"]["mallId"] == "myshop" and data["recovery"]["publicationId"] == PUBLICATION
    store["connections"][0]["refresh_expires_at"] = datetime.now(timezone.utc) - timedelta(days=1)
    assert client.get(f"/v1/projects/{PROJECT}/mall-registrations").json()["recovery"]["jobId"] == original["id"]
    store["connections"].clear()
    assert client.get(f"/v1/projects/{PROJECT}/mall-registrations").json()["recovery"] is None


@pytest.mark.parametrize("metadata", [{}, {"productNo": 42, "create": {"sentAt": "now"}},
    {"rejectedAt": "later", "create": {"sentAt": "now"}}])
def test_registration_list_does_not_recover_confirmed_or_failed_terminal_jobs(env, metadata):
    client, store = env
    assert submit(client).status_code == 202
    store["jobs"][0].update(status="error", metadata=metadata, result={"outcome": "unknown"})
    response = client.get(f"/v1/projects/{PROJECT}/mall-registrations")
    assert response.status_code == 200, response.text
    assert response.json()["recovery"] is None


@pytest.mark.parametrize("metadata,result,outcome,verifiable", [
    ({}, None, "failed", False),
    ({"create": {"sentAt": "now"}}, None, "unknown", True),
    ({"create": {"sentAt": "now"}}, {"outcome": "duplicate_suspected"}, "duplicate_suspected", True),
    ({"productNo": 42}, None, "created", False),
    ({"productNo": 42, "create": {"sentAt": "now"}}, {"outcome": "failed"}, "created", False),
    ({"create": {"sentAt": "now"}, "rejectedAt": "later"}, {"code": "invalid_request"}, "failed", False),
    ({"productNo": 42, "rejectedAt": "later"}, {"outcome": "failed"}, "created", False),
])
def test_registration_view_derives_outcome_from_metadata_when_result_missing(env, metadata, result, outcome, verifiable):
    client, store = env
    assert submit(client).status_code == 202
    job = store["jobs"][0]
    job.update(status="error", metadata={**metadata, "externalCode": "wl0123456789abcdef"}, result=result,
               error_message="작업 서버가 응답하지 않아 종료됐어요.")
    assert hasattr(mall_connect, "derive_outcome") and hasattr(mall_connect, "registration_view")
    assert mall_connect.derive_outcome(job) == outcome
    view = client.get(BASE + "/" + job["id"]).json()
    assert view["outcome"] == outcome and view["verifiable"] is verifiable
    assert view["productNo"] == metadata.get("productNo")
    assert view["externalCode"] == "wl0123456789abcdef"
    assert view["errorMessage"] == (job["error_message"] if outcome == "failed" else None)
    assert view["warnings"] == (["마무리 확인이 늦어졌어요. 상품은 만들어졌어요"] if outcome == "created" else [])
    assert set(view) == {"jobId", "status", "progress", "outcome", "productNo", "productUrl", "imageCount", "thumbnail",
                         "code", "errorMessage", "externalCode", "verifiable", "warnings", "createdAt", "finishedAt",
                         "mallId", "publicationId"}
    assert view["mallId"] == "myshop" and view["publicationId"] == PUBLICATION


def test_categories_route_pages_three_times_filters_hidden_and_caches(env, monkeypatch):
    client, store = env
    calls = []
    def respond(request):
        assert request.url.path == "/api/v2/admin/categories"
        assert request.headers["Authorization"] == f"Bearer {ACCESS}"
        assert request.url.params["limit"] == "100"
        offset = int(request.url.params["offset"])
        calls.append(offset)
        return httpx.Response(200, json={"categories": [dict(category_no=offset+i, category_depth=2,
            category_name=f"분류 {offset+i}", full_category_name=["의류", f"분류 {offset+i}"],
            use_display="F" if i == 0 else "T") for i in range(100)]})
    monkeypatch.setattr(mall_connect, "Cafe24Client", lambda *a, **kw:
        Cafe24Client(*a, **{**kw, "transport": httpx.MockTransport(respond)}))
    if hasattr(mall_connect, "_CATEGORY_CACHE"):
        monkeypatch.setattr(mall_connect, "_CATEGORY_CACHE", {})
    path = "/v1/malls/cafe24/categories"
    response = client.get(path)
    assert response.status_code == 200, response.text
    items = response.json()
    assert len(items) == 297 and calls == [0, 100, 200]
    assert items[0] == dict(categoryNo=1, depth=2, name="분류 1", fullName=["의류", "분류 1"])
    assert client.get(path).json() == items and len(calls) == 3
    clock = [0.0]
    monkeypatch.setattr(mall_connect, "_CATEGORY_CACHE", {})
    monkeypatch.setattr(mall_connect, "monotonic", lambda: clock[0])
    assert client.get(path).status_code == 200 and len(calls) == 6
    clock[0] = 599
    assert client.get(path).status_code == 200 and len(calls) == 6
    clock[0] = 601
    assert client.get(path).status_code == 200 and len(calls) == 9
    store["connections"][0]["id"] = str(uuid.uuid4())
    assert client.get(path).status_code == 200 and len(calls) == 12
    store["connections"].clear()
    assert_error(client.get(path), 404, "not_connected")
