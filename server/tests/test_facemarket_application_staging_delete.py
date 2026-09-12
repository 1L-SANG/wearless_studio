"""지원서에서 사진 선택을 지웠을 때 임시 서버 사진도 제출 불가 상태가 되는지 검증한다."""

import asyncio
import contextlib
import hashlib
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers
from starlette.datastructures import UploadFile

from app import facemarket_applications as applications


class _R2:
    def __init__(self, events):
        self.events = events

    def delete(self, key):
        self.events.append(("r2-delete", key))

    def put_bytes(self, key, data, mime):
        self.events.append(("r2-put", key, data, mime))


class _Cursor:
    def __init__(self, events, row):
        self.events = events
        self.row = row
        self.last_sql = ""

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, sql, params):
        self.last_sql = " ".join(sql.lower().split())
        if self.last_sql.startswith("select r2_key"):
            self.events.append(("db-select", params))
        elif self.last_sql.startswith("delete from fm_model_application_photo_staging"):
            self.events.append(("db-delete", params))
        elif self.last_sql.startswith("insert into fm_model_application_photo_staging"):
            self.events.append(("db-upsert", params))
        else:
            raise AssertionError(self.last_sql)

    async def fetchone(self):
        return self.row


class _Conn:
    def __init__(self, events, row):
        self.events = events
        self.cursor_instance = _Cursor(events, row)

    def cursor(self):
        return self.cursor_instance

    async def commit(self):
        self.events.append(("commit", None))


def _request(r2):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(r2_face=r2)))


def test_delete_staging_photo_removes_the_owned_row_before_r2(monkeypatch):
    events = []
    key = "private/staging/profile.jpg"
    conn = _Conn(events, {"r2_key": key})

    @contextlib.asynccontextmanager
    async def fake_get_conn(_request):
        yield conn

    monkeypatch.setattr(applications, "get_conn", fake_get_conn)
    response = asyncio.run(applications.delete_application_photo_staging(
        _request(_R2(events)), kind="profile",
        stage_id=hashlib.sha256(key.encode()).hexdigest(), user_id="user-1"
    ))

    assert response.status_code == 204
    assert events == [
        ("db-select", ("user-1", "profile")),
        ("db-delete", ("user-1", "profile", key)),
        ("commit", None),
        ("r2-delete", key),
    ]


def test_delete_staging_photo_does_not_remove_a_newer_tab_photo(monkeypatch):
    events = []
    current_key = "private/staging/newer-profile.jpg"
    conn = _Conn(events, {"r2_key": current_key})

    @contextlib.asynccontextmanager
    async def fake_get_conn(_request):
        yield conn

    monkeypatch.setattr(applications, "get_conn", fake_get_conn)
    stale_id = hashlib.sha256(b"private/staging/older-profile.jpg").hexdigest()
    with pytest.raises(HTTPException) as caught:
        asyncio.run(applications.delete_application_photo_staging(
            _request(_R2(events)), kind="profile", stage_id=stale_id, user_id="user-1"
        ))

    assert caught.value.status_code == 409
    assert caught.value.detail["code"] == "staging_changed"
    assert events == [("db-select", ("user-1", "profile"))]


def test_delete_staging_photo_rejects_an_unknown_kind():
    with pytest.raises(HTTPException) as caught:
        asyncio.run(applications.delete_application_photo_staging(
            _request(_R2([])), kind="unknown", stage_id="0" * 64, user_id="user-1"
        ))
    assert caught.value.detail["code"] == "invalid_photo_kind"


def test_stage_photo_returns_an_opaque_id_for_the_saved_object(monkeypatch):
    events = []
    key = "private/fm-application/staging/user-1/profile-fixed.jpg"
    conn = _Conn(events, None)

    @contextlib.asynccontextmanager
    async def fake_get_conn(_request):
        yield conn

    monkeypatch.setattr(applications, "get_conn", fake_get_conn)
    monkeypatch.setattr(applications, "_staging_key", lambda *_args: key)
    image = UploadFile(
        file=BytesIO(b"photo"), filename="profile.jpg",
        headers=Headers({"content-type": "image/jpeg"}),
    )

    result = asyncio.run(applications.stage_application_photo(
        _request(_R2(events)), image=image, kind="profile", user_id="user-1"
    ))

    assert result == {
        "staged": True,
        "kind": "profile",
        "stageId": hashlib.sha256(key.encode()).hexdigest(),
    }
    assert key not in result.values()
