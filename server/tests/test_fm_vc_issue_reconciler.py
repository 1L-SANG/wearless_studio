import asyncio
from types import SimpleNamespace

from app import facemarket, facemarket_notify
from app.facemarket import FaceVcIssueResult
from app.workers.fm_vc_issue_reconciler import FaceVcIssueReconciler


class _Cursor:
    def __init__(self, row):
        self.row = row
        self.sql = ""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, sql, _params=None):
        self.sql = " ".join(sql.split()).lower()

    async def fetchone(self):
        return self.row

    async def fetchall(self):
        if self.row is None:
            return []
        return self.row if isinstance(self.row, list) else [self.row]


class _Connection:
    def __init__(self, row):
        self.cursor_value = _Cursor(row)
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def cursor(self):
        return self.cursor_value

    async def commit(self):
        self.committed = True


class _Pool:
    def __init__(self, row):
        self.conn = _Connection(row)

    def connection(self):
        return self.conn


def _app(row):
    return SimpleNamespace(state=SimpleNamespace(pool=_Pool(row)))


def test_reconciler_claims_old_pending_license_and_issues(monkeypatch):
    row = {"license_id": "lic-1", "model_id": "model-1", "user_id": "user-1"}
    reconciler = FaceVcIssueReconciler(_app(row))
    calls = []

    async def fake_issue(app, **kwargs):
        calls.append((app, kwargs))

    monkeypatch.setattr(
        "app.workers.fm_vc_issue_reconciler.issue_and_activate_pending_face_vc",
        fake_issue,
    )

    assert asyncio.run(reconciler._sweep_once()) is True
    assert calls[0][1] == {
        "license_id": "lic-1", "model_id": "model-1", "user_id": "user-1"
    }
    sql = reconciler.app.state.pool.conn.cursor_value.sql
    assert "e.status = 'vc_pending'" in sql
    assert "l.status = 'pending'" in sql and "l.vc_id is null" in sql
    assert "l.updated_at < now() - interval '15 seconds'" in sql
    assert "for update of l skip locked" in sql


def test_reconciler_skips_locked_pending_license(monkeypatch):
    reconciler = FaceVcIssueReconciler(_app(None))
    called = False

    async def fake_issue(*_args, **_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(
        "app.workers.fm_vc_issue_reconciler.issue_and_activate_pending_face_vc",
        fake_issue,
    )

    assert asyncio.run(reconciler._sweep_once()) is False
    assert called is False


def test_reconciler_continues_to_next_candidate_after_one_failure(monkeypatch):
    rows = [
        {"license_id": "lic-a", "model_id": "model-a", "user_id": "user-a"},
        {"license_id": "lic-b", "model_id": "model-b", "user_id": "user-b"},
    ]
    reconciler = FaceVcIssueReconciler(_app(rows))
    attempted = []

    async def fake_issue(_app, **kwargs):
        attempted.append(kwargs["license_id"])
        if kwargs["license_id"] == "lic-a":
            raise RuntimeError("holder failure")

    monkeypatch.setattr(
        "app.workers.fm_vc_issue_reconciler.issue_and_activate_pending_face_vc",
        fake_issue,
    )

    assert asyncio.run(reconciler._sweep_once()) is True
    assert attempted == ["lic-a", "lic-b"]
    assert "limit 20" in reconciler.app.state.pool.conn.cursor_value.sql


def test_license_issued_email_without_resend_configuration_is_quiet():
    settings = SimpleNamespace(
        resend_api_key=None,
        fm_application_public_base="https://facemarket.example",
    )

    result = asyncio.run(
        facemarket_notify.send_license_issued_email(
            settings, to="model@example.com", display_name="모델"
        )
    )

    assert result == (False, None, "not_configured")


def test_shared_issue_function_activates_and_emails_on_same_locked_connection(monkeypatch):
    locked = {
        "status": "pending", "vc_id": None, "enrollment_id": "enroll-1",
        "allowed_use": ["일반 의류"], "unit_price": 14900,
        "license_valid_until": None, "face_image_digest": "sha256-face",
    }
    conn = _Connection(None)
    app = SimpleNamespace(state=SimpleNamespace(
        pool=SimpleNamespace(connection=lambda: conn), settings=SimpleNamespace(),
    ))
    calls = []

    async def fake_find(actual_conn, user_id, license_id, *, skip_locked=False):
        assert actual_conn is conn
        assert skip_locked is True
        calls.append(("lock", user_id, license_id))
        return locked

    async def fake_issue(_app, **kwargs):
        calls.append(("issue", kwargs["license_id"]))
        return FaceVcIssueResult("vc-1", "did:user-1")

    async def fake_finalize(connect, **kwargs):
        async with connect() as actual_conn:
            assert actual_conn is conn
        calls.append(("activate", kwargs["issued"].vc_id))
        return {"id": "lic-1", "status": "active", "vc_id": "vc-1"}

    async def fake_email(_settings, **kwargs):
        calls.append(("email", kwargs["to"], kwargs["display_name"]))
        return True, "message-1", None

    original_cursor = conn.cursor
    def recipient_cursor():
        cursor = original_cursor()
        cursor.row = {"contact_email": "model@example.com", "display_name": "모델A"}
        return cursor
    conn.cursor = recipient_cursor
    monkeypatch.setattr(facemarket, "_find_license_for_update", fake_find)
    monkeypatch.setattr(facemarket, "issue_face_vc", fake_issue)
    monkeypatch.setattr(facemarket, "finalize_issued_face_vc", fake_finalize)
    monkeypatch.setattr(facemarket, "send_license_issued_email", fake_email)

    result = asyncio.run(facemarket.issue_and_activate_pending_face_vc(
        app, user_id="user-1", license_id="lic-1", model_id="model-1"
    ))

    assert result["status"] == "active"
    assert calls == [
        ("lock", "user-1", "lic-1"), ("issue", "lic-1"),
        ("activate", "vc-1"), ("email", "model@example.com", "모델A"),
    ]
