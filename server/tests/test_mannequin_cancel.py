"""마네킹 취소 정산과 활성 중복 합류 계약."""

import asyncio
import contextlib
import inspect

import app.routes as routes
from app import repo


class _RouteConn:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


def _auth(make_token):
    return {"Authorization": f"Bearer {make_token()}"}


def _route_db(monkeypatch):
    conn = _RouteConn()

    @contextlib.asynccontextmanager
    async def fake_conn(_request):
        yield conn

    monkeypatch.setattr(routes, "get_conn", fake_conn)
    return conn


def test_cancel_route_charges_active_job_and_returns_credits(
    client, make_token, monkeypatch,
):
    seen = {}

    async def fake_get_project(conn, user_id, project_id):
        return {"id": project_id}

    async def fake_cancel(conn, user_id, project_id):
        seen["scope"] = (user_id, project_id)
        return 6

    monkeypatch.setattr(routes.repo, "get_project", fake_get_project)
    monkeypatch.setattr(routes.repo, "cancel_active_mannequin_job", fake_cancel)
    conn = _route_db(monkeypatch)

    response = client.post(
        "/v1/projects/p1/mannequins:cancel", headers=_auth(make_token)
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"cancelled": True, "credits": 6}
    assert seen["scope"] == ("user-1", "p1")
    assert conn.commits == 1


def test_cancel_route_is_idempotent_when_no_active_job(
    client, make_token, monkeypatch,
):
    async def fake_get_project(conn, user_id, project_id):
        return {"id": project_id}

    async def fake_cancel(conn, user_id, project_id):
        return None

    async def fake_get_account(conn, user_id):
        return {"credits": 8}

    monkeypatch.setattr(routes.repo, "get_project", fake_get_project)
    monkeypatch.setattr(routes.repo, "cancel_active_mannequin_job", fake_cancel)
    monkeypatch.setattr(routes.repo, "get_account", fake_get_account)
    _route_db(monkeypatch)

    response = client.post(
        "/v1/projects/p1/mannequins:cancel", headers=_auth(make_token)
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"cancelled": False, "credits": 8}


def _wire_join_route(monkeypatch, *, job, requested_snapshot):
    async def fake_get_project(conn, user_id, project_id):
        return {"id": project_id}

    async def fake_list_cuts(conn, user_id, project_id):
        return []

    async def fake_snapshot(*args, **kwargs):
        return requested_snapshot

    async def fake_create_job(conn, **kwargs):
        return job, False

    monkeypatch.setattr(routes.repo, "get_project", fake_get_project)
    monkeypatch.setattr(routes.repo, "list_mannequin_cuts", fake_list_cuts)
    monkeypatch.setattr(routes, "_fit_profile_snapshot", fake_snapshot)
    monkeypatch.setattr(routes.repo, "create_job", fake_create_job)
    _route_db(monkeypatch)


def test_active_mannequin_with_different_payload_returns_409(
    client, make_token, monkeypatch,
):
    running_profile = {"category": "top", "axes": {"fit": "regular"}}
    requested_profile = {"category": "top", "axes": {"fit": "slim"}}
    _wire_join_route(
        monkeypatch,
        job={
            "id": "running-job",
            "status": "running",
            "payload": {
                "mode": "generate",
                "fitProfileSnapshot": {
                    "version": 1,
                    "profile": running_profile,
                    "adjustedAxes": [],
                },
            },
        },
        requested_snapshot={
            "version": 1,
            "profile": requested_profile,
            "adjustedAxes": [],
        },
    )

    response = client.post(
        "/v1/projects/p1/mannequins:generate", headers=_auth(make_token)
    )

    assert response.status_code == 409, response.text
    assert response.json() == {"error": {
        "code": "generation_in_progress",
        "message": "이미 다른 마네킹 생성이 진행 중이에요. 잠시 뒤 다시 시도해 주세요.",
    }}


def test_generate_same_payload_rejoins_active_job(client, make_token, monkeypatch):
    profile = {"category": "top", "axes": {"fit": "regular"}}
    snapshot = {"version": 1, "profile": profile, "adjustedAxes": []}
    _wire_join_route(
        monkeypatch,
        job={
            "id": "running-job",
            "status": "running",
            "payload": {"mode": "generate", "fitProfileSnapshot": snapshot},
        },
        requested_snapshot=snapshot,
    )

    response = client.post(
        "/v1/projects/p1/mannequins:generate", headers=_auth(make_token)
    )

    assert response.status_code == 202, response.text
    assert response.json() == {"jobId": "running-job"}


def test_regenerate_same_profile_rejoins_despite_adjusted_axes_recalculation(
    client, make_token, monkeypatch,
):
    profile = {"category": "top", "axes": {"fit": "slim"}}
    _wire_join_route(
        monkeypatch,
        job={
            "id": "running-job",
            "status": "pending",
            "payload": {
                "mode": "regenerate",
                "fitProfileSnapshot": {
                    "version": 1,
                    "profile": profile,
                    "adjustedAxes": ["fit"],
                },
            },
        },
        requested_snapshot={
            "version": 1,
            "profile": profile,
            "adjustedAxes": [],
        },
    )

    response = client.post(
        "/v1/projects/p1/mannequins:regenerate",
        json={"fitProfile": profile},
        headers=_auth(make_token),
    )

    assert response.status_code == 202, response.text
    assert response.json() == {"jobId": "running-job"}


def test_regenerate_different_profile_returns_409(client, make_token, monkeypatch):
    running_profile = {"category": "top", "axes": {"fit": "regular"}}
    requested_profile = {"category": "top", "axes": {"fit": "slim"}}
    _wire_join_route(
        monkeypatch,
        job={
            "id": "running-job",
            "status": "running",
            "payload": {
                "mode": "regenerate",
                "fitProfileSnapshot": {
                    "version": 1,
                    "profile": running_profile,
                    "adjustedAxes": ["fit"],
                },
            },
        },
        requested_snapshot={
            "version": 1,
            "profile": requested_profile,
            "adjustedAxes": [],
        },
    )

    response = client.post(
        "/v1/projects/p1/mannequins:regenerate",
        json={"fitProfile": requested_profile},
        headers=_auth(make_token),
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "generation_in_progress"


def test_regenerate_different_mode_returns_409(client, make_token, monkeypatch):
    profile = {"category": "top", "axes": {"fit": "regular"}}
    snapshot = {"version": 1, "profile": profile, "adjustedAxes": []}
    _wire_join_route(
        monkeypatch,
        job={
            "id": "running-job",
            "status": "pending",
            "payload": {"mode": "generate", "fitProfileSnapshot": snapshot},
        },
        requested_snapshot=snapshot,
    )

    response = client.post(
        "/v1/projects/p1/mannequins:regenerate",
        json={"fitProfile": profile},
        headers=_auth(make_token),
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "generation_in_progress"


def test_payload_match_requires_same_snapshot_version_and_snapshot_presence():
    profile = {"category": "top", "axes": {"fit": "regular"}}
    request_payload = {
        "mode": "generate",
        "fitProfileSnapshot": {"version": 1, "profile": profile, "adjustedAxes": []},
    }

    assert not routes._mannequin_payload_matches(
        {
            "status": "running",
            "payload": {
                "mode": "generate",
                "fitProfileSnapshot": {"version": 2, "profile": profile, "adjustedAxes": []},
            },
        },
        request_payload,
    )
    assert not routes._mannequin_payload_matches(
        {"status": "running", "payload": {"mode": "generate"}},
        request_payload,
    )


class _RepoState:
    def __init__(self):
        self.active = True
        self.executed = []

    def cursor(self):
        return _RepoCursor(self)


class _RepoCursor:
    def __init__(self, state):
        self.state = state
        self.row = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, query, params=None):
        self.state.executed.append((query, params))
        if "select id::text as id, credits_reserved" in query:
            self.row = {"id": "j1", "credits_reserved": 2} if self.state.active else None
        elif "update jobs set status = 'cancelled'" in query:
            self.state.active = False
            self.row = None
        else:
            self.row = None

    async def fetchone(self):
        row, self.row = self.row, None
        return row


def test_cancel_repo_settles_once_with_bucket_charge_and_base_guard(monkeypatch):
    state = _RepoState()
    calls = {"consume": [], "settle": []}

    async def fake_consume(conn, **kwargs):
        calls["consume"].append(kwargs)
        return 6

    async def fake_settle(conn, **kwargs):
        calls["settle"].append(kwargs)
        return 6

    monkeypatch.setattr(repo, "_consume_buckets", fake_consume)
    monkeypatch.setattr(repo, "_settle_credits", fake_settle)

    first = asyncio.run(repo.cancel_active_mannequin_job(state, "u1", "p1"))
    second = asyncio.run(repo.cancel_active_mannequin_job(state, "u1", "p1"))

    assert (first, second) == (6, None)
    assert len(calls["consume"]) == len(calls["settle"]) == 1
    consume = calls["consume"][0]
    assert consume["reserved"] == consume["charge"] == 2
    assert consume["metadata"] == {"reason": "user_cancelled"}
    settle = calls["settle"][0]
    assert settle["reserved"] == settle["charge"] == 0
    assert settle["settle_key"] == "credit:job:j1:settle"
    assert settle["metadata"] == {"reason": "user_cancelled"}
    select_sql = state.executed[0][0]
    assert "user_id = %s and project_id = %s and kind = 'mannequin'" in select_sql
    assert "status in ('pending', 'running')" in select_sql
    assert "for update" in select_sql
    event_sql, event_params = next(
        (sql, params) for sql, params in state.executed if "insert into job_events" in sql
    )
    assert "'cancelled'" in event_sql
    assert event_params[0] == "j1"


def test_cancel_terminal_event_avoids_progress_emit_lock_inversion():
    source = inspect.getsource(repo.cancel_active_mannequin_job)

    # progress emit은 advisory lock 뒤 jobs UPDATE 순서다. 취소는 jobs FOR UPDATE를 이미
    # 잡았으므로 advisory helper를 호출하지 않고 terminal insert를 직접 해야 교착이 없다.
    assert "append_job_event(" not in source
    assert "pg_advisory_xact_lock" not in source
    assert "insert into job_events" in source


def test_cancelled_jobs_are_terminal_and_never_recovered_or_finalized():
    recovery = inspect.getsource(repo.recover_stale_leases)
    success = inspect.getsource(repo.finalize_mannequin_success)
    failure = inspect.getsource(repo._finalize_job_failure)

    assert "where status = 'running'" in recovery
    assert "status = 'running' for update" in success
    assert "status = 'running' for update" in failure
