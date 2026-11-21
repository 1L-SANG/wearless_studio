import json
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import repo, routes
from conftest import FakeConn, auth_headers
from test_detail_model_billing import _wire


GENERATE = "/v1/projects/p1/detail-page:generate"
NOW = datetime(2026, 11, 21, 12, tzinfo=timezone.utc)
DB_TIMEZONE = timezone(timedelta(hours=9))
RETRY_AT = NOW + timedelta(minutes=55)


class _HistoryConn(FakeConn):
    def __init__(self, db):
        self.db = db

    @asynccontextmanager
    async def cursor(self):
        yield self

    async def execute(self, query, params):
        self.rows = self.db.execute(query.replace("%s", "?"), params).fetchall()

    async def fetchall(self):
        return [{**row, "finished_at": datetime.fromisoformat(row["finished_at"])} for row in self.rows]


@pytest.fixture
def history(monkeypatch):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz)

    monkeypatch.setattr(repo, "datetime", FixedDatetime)
    with sqlite3.connect(":memory:", check_same_thread=False) as db:
        db.row_factory = sqlite3.Row
        db.execute("""create table jobs (id integer primary key, user_id text,
            project_id text, kind text, status text, created_at text, started_at text,
            finished_at text, result text)""")

        def add_job(status, minutes_ago, *, started=True, code=None, user="user-1",
                    project="p1", kind="detail_page", created_minutes_ago=None):
            finished_at = (NOW - timedelta(minutes=minutes_ago)).astimezone(DB_TIMEZONE)
            created_at = finished_at - timedelta(minutes=1)
            if created_minutes_ago is not None:
                created_at = (NOW - timedelta(minutes=created_minutes_ago)).astimezone(DB_TIMEZONE)
            db.execute("""insert into jobs (user_id, project_id, kind, status, created_at,
                started_at, finished_at, result) values (?, ?, ?, ?, ?, ?, ?, ?)""",
                (user, project, kind, status, created_at.isoformat(),
                 created_at.isoformat() if started else None, finished_at.isoformat(),
                 json.dumps({"errorCode": code}) if code else None))

        @asynccontextmanager
        async def connection(*_args):
            yield _HistoryConn(db)

        yield add_job, connection
    db.close()


def _wire_history(monkeypatch, history, **options):
    lookup = repo.get_detail_page_retry_locked_until
    _wire(monkeypatch, **options)
    monkeypatch.setattr(repo, "get_detail_page_retry_locked_until", lookup)
    monkeypatch.setattr(routes, "get_conn", history[1])
    return history[0]


@pytest.mark.parametrize("case", [
    "one_failure", "at_expiry", "after_expiry", "success_between", "cancelled", "never_started", "other_jobs",
])
def test_retry_allowed(client, make_token, monkeypatch, history, case):
    add_job = _wire_history(monkeypatch, history)
    add_job("error", {"at_expiry": 60, "after_expiry": 61}.get(case, 5))
    if case in {"at_expiry", "after_expiry"}:
        add_job("error", 65)
    elif case == "success_between":
        add_job("error", 15)
        add_job("done", 10, created_minutes_ago=20)
    elif case == "cancelled":
        add_job("cancelled", 8)
        add_job("error", 10, code="job_cancelled")
    elif case == "never_started":
        add_job("error", 8, started=False, code="insufficient_credits")
    elif case == "other_jobs":
        add_job("error", 8, kind="editor_image")
        add_job("error", 10, project="p2")
        add_job("error", 12, user="other")

    response = client.post(GENERATE, headers=auth_headers(make_token))
    assert response.status_code == 202, response.text
    assert response.json() == {"jobId": "j1"}
    repo.create_job.assert_awaited_once()
    repo.reserve_credits.assert_awaited_once()


def test_two_failures_lock_before_credit_reservation_and_creation(client, make_token, monkeypatch, history):
    add_job = _wire_history(monkeypatch, history)
    add_job("error", 90)
    add_job("error", 5, code="incomplete_cuts")
    add_job("cancelled", 1)

    response = client.post(GENERATE, headers=auth_headers(make_token))
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "generation_retry_locked"
    assert error["message"] == "지금 서버에 문제가 생겼어요. 1시간 뒤에 다시 시도해 주세요."
    retry_at = datetime.fromisoformat(error["meta"]["retryAt"])
    assert retry_at == RETRY_AT
    assert retry_at.utcoffset() == timedelta(0)
    repo.create_job.assert_not_awaited()
    repo.reserve_credits.assert_not_awaited()


@pytest.mark.parametrize("cached,existing,status", [
    (True, None, 200),
    (False, {"id": "active", "status": "running", "payload": {}}, 202),
    (False, {"id": "replayed", "status": "error", "payload": {}}, 202),
])
def test_existing_generation_bypasses_retry_lock(client, make_token, monkeypatch, history, cached, existing, status):
    add_job = _wire_history(monkeypatch, history, cached=cached, existing=existing)
    add_job("error", 10)
    add_job("error", 5)
    retry_lock = AsyncMock(side_effect=AssertionError("Existing jobs must bypass the history check"))
    monkeypatch.setattr(repo, "get_detail_page_retry_locked_until", retry_lock)
    response = client.post(GENERATE, headers={**auth_headers(make_token), "Idempotency-Key": "original"})
    assert response.status_code == status, response.text
    assert response.json() == ({"data": [{"id": "done"}], "credits": 200} if cached else {"jobId": existing["id"]})
    retry_lock.assert_not_awaited()
    repo.create_job.assert_not_awaited()
    repo.reserve_credits.assert_not_awaited()


@pytest.mark.parametrize("status,failures,minutes_ago,expected", [
    ("error", 1, 5, None), ("error", 2, 5, RETRY_AT), ("error", 2, 61, None),
    ("cancelled", 2, 5, RETRY_AT),
])
def test_retry_lock_in_job_status_and_failure_events(client, make_token, monkeypatch, history,
                                                    status, failures, minutes_ago, expected):
    add_job, connection = history
    add_job("error", minutes_ago)
    if failures == 2:
        add_job("error", 90)
    if status == "cancelled":
        add_job("cancelled", 1)
    monkeypatch.setattr(routes, "get_conn", connection)
    monkeypatch.setattr(client.app.state, "pool", SimpleNamespace(connection=connection))
    job = {"id": "j1", "project_id": "p1", "kind": "detail_page", "status": status,
           "progress": 40, "created_at": NOW, "updated_at": NOW, "error_message": "실패했어요."}
    # An event may become visible just after the polling request checks ownership.
    monkeypatch.setattr(repo, "get_job", AsyncMock(side_effect=[job, {**job, "status": "running"}, job]))
    payload = {"code": "job_cancelled" if status == "cancelled" else "generation_failed",
               "message": job["error_message"]}
    monkeypatch.setattr(repo, "list_job_events", AsyncMock(return_value=[
        {"id": 1, "event_type": "progress", "payload": {"progress": 40}},
        {"id": 2, "event_type": status, "payload": payload},
    ]))
    headers = auth_headers(make_token)
    job_response = client.get("/v1/jobs/j1", headers=headers)
    poll = client.get("/v1/jobs/j1/events?poll=1", headers=headers)
    stream = client.get("/v1/jobs/j1/events", headers=headers)
    assert job_response.status_code == poll.status_code == stream.status_code == 200
    assert poll.json()["events"][0]["payload"] == {"progress": 40}
    event_payload = poll.json()["events"][1]["payload"]
    sse_payload = json.loads(stream.text.split(f"event: {status}\ndata: ", 1)[1].splitlines()[0])
    for data in (job_response.json(), event_payload, sse_payload):
        retry_at = data["retryLockedUntil"]
        assert (datetime.fromisoformat(retry_at) if retry_at is not None else None) == expected
    for data in (event_payload, sse_payload):
        assert {key: data[key] for key in payload} == payload
    assert "retryLockedUntil" not in payload


@pytest.mark.parametrize("status", ["error", "cancelled"])
def test_retry_lock_in_project_failure_summaries(client, make_token, monkeypatch, history, status):
    add_job, connection = history
    add_job("error", 10)
    add_job("error", 5)
    if status == "cancelled":
        add_job("cancelled", 1)
    monkeypatch.setattr(routes, "get_conn", connection)
    failed_job = {"id": "j2", "status": status,
                  "errorCode": "job_cancelled" if status == "cancelled" else "incomplete_cuts"}
    common = {"id": "p1", "title": "상품", "updated_at": NOW}
    monkeypatch.setattr(repo, "get_project", AsyncMock(return_value={**common, "status": "draft",
        "copywriting": True, "selected_mannequin_id": None, "adjust_count": 0, "created_at": NOW}))
    monkeypatch.setattr(repo, "get_latest_project_jobs", AsyncMock(return_value={
        "mannequin": None, "detailPage": failed_job}))
    headers = auth_headers(make_token)
    project = client.get("/v1/projects/p1", headers=headers)
    assert project.status_code == 200
    job = project.json()["latestJobs"]["detailPage"]
    assert datetime.fromisoformat(job["retryLockedUntil"]) == RETRY_AT
    assert "retryLockedUntil" not in failed_job


def test_generate_openapi_declares_retry_at(client):
    schema = client.app.openapi()
    conflict = schema["paths"]["/v1/projects/{project_id}/detail-page:generate"]["post"]["responses"]["409"]
    models = schema["components"]["schemas"]
    response_model = conflict["content"]["application/json"]["schema"]["$ref"].split("/")[-1]
    error_model = models[response_model]["properties"]["error"]["$ref"].split("/")[-1]
    meta = models[error_model]["properties"]["meta"]
    meta_model = next(item["$ref"].split("/")[-1] for item in meta["anyOf"] if "$ref" in item)
    assert "retryAt" in models[meta_model]["properties"]
