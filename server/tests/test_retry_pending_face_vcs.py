import asyncio
import contextlib
import types

import pytest

from app.facemarket import FaceVcIssueError, FaceVcIssueResult
from scripts import retry_pending_face_vcs as retry


class _Cursor:
    def __init__(self, store):
        self.store = store

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def execute(self, sql, _params=()):
        self.store["query"] = " ".join(sql.split()).lower()

    async def fetchall(self):
        return list(self.store["rows"])


class _Conn:
    def __init__(self, store):
        self.store = store

    def cursor(self):
        return _Cursor(self.store)


class _Pool:
    def __init__(self, rows):
        self.store = {"rows": rows, "query": None}
        self.opened = False
        self.closed = False

    async def open(self):
        self.opened = True

    async def close(self):
        self.closed = True

    def connection(self):
        @contextlib.asynccontextmanager
        async def connection():
            yield _Conn(self.store)

        return connection()


def _settings(*, base="http://holder:8100", secret="shared-secret"):
    return types.SimpleNamespace(
        database_url="postgresql://test",
        opendid_holder_url=base,
        opendid_holder_hmac_secret=secret,
    )


def _row(n):
    return {
        "id": f"license-{n}",
        "model_id": f"model-{n}",
        "user_id": f"user-{n}",
        "enrollment_id": f"enrollment-{n}",
        "allowed_use": ["일반 여성 의류"],
        "forbidden_use": [],
        "unit_price": 1000,
        "license_valid_until": "2027-01-01",
        "face_image_digest": "sha256-x",
    }


def test_retry_dry_run_selects_only_owned_pending_vc_candidates_and_prints_counts(
    monkeypatch, capsys
):
    pool = _Pool([_row(1), _row(2)])
    monkeypatch.setattr(retry, "load_settings", _settings)
    monkeypatch.setattr(retry, "create_pool", lambda _url: pool)
    monkeypatch.setattr(
        retry,
        "issue_face_vc",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("dry run issued")),
    )

    asyncio.run(retry.main([]))

    query = pool.store["query"]
    assert "from fm_licenses l" in query
    assert "join fm_models m on m.id = l.model_id" in query
    assert "join fm_biometric_enrollments e on e.id = l.enrollment_id" in query
    assert "l.status = 'pending'" in query and "l.vc_id is null" in query
    assert "m.user_id is not null" in query
    assert "e.status = 'vc_pending'" in query
    assert "e.user_id = m.user_id" in query and "e.model_id = m.id" in query
    assert capsys.readouterr().out.strip() == (
        "mode=DRY_RUN pending=2 issued=0 failed=0"
    )
    assert pool.opened and pool.closed


@pytest.mark.parametrize("missing", ["base", "secret"])
@pytest.mark.parametrize("missing_value", [None, "", " "])
def test_retry_requires_holder_config_before_opening_pool(
    monkeypatch, missing, missing_value
):
    values = {"base": "http://holder:8100", "secret": "shared-secret"}
    values[missing] = missing_value
    monkeypatch.setattr(retry, "load_settings", lambda: _settings(**values))
    monkeypatch.setattr(
        retry,
        "create_pool",
        lambda _url: (_ for _ in ()).throw(AssertionError("pool created")),
    )
    expected = "OPENDID_HOLDER_URL" if missing == "base" else "OPENDID_HOLDER_HMAC_SECRET"
    with pytest.raises(SystemExit, match=expected):
        asyncio.run(retry.main(["--apply"]))


def test_retry_apply_uses_shared_issue_and_finalizer_continues_and_prints_counts_only(
    monkeypatch, capsys
):
    rows = [_row(1), _row(2), _row(3)]
    pool = _Pool(rows)
    issued = []
    finalized = []

    async def fake_issue(_app, **kwargs):
        issued.append(kwargs["license_id"])
        if kwargs["license_id"] == "license-2":
            raise FaceVcIssueError("vc_issue_delayed", status_code=503)
        return FaceVcIssueResult(f"vc-{kwargs['license_id']}", "did:omn:user")

    async def fake_finalize(connect, **kwargs):
        assert connect == pool.connection
        finalized.append((kwargs["license_id"], kwargs["issued"].vc_id))
        return {"status": "active"}

    monkeypatch.setattr(retry, "load_settings", _settings)
    monkeypatch.setattr(retry, "create_pool", lambda _url: pool)
    monkeypatch.setattr(retry, "issue_face_vc", fake_issue)
    monkeypatch.setattr(retry, "finalize_issued_face_vc", fake_finalize)

    with pytest.raises(SystemExit, match="VC 재발급 실패: 1/3"):
        asyncio.run(retry.main(["--apply"]))

    assert issued == ["license-1", "license-2", "license-3"]
    assert finalized == [
        ("license-1", "vc-license-1"),
        ("license-3", "vc-license-3"),
    ]
    output = capsys.readouterr().out.strip()
    assert output == "mode=APPLY pending=3 issued=2 failed=1"
    assert all(row["id"] not in output for row in rows)
    assert pool.closed
