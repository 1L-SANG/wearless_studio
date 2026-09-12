"""지원서에서 사진 선택을 지웠을 때 임시 서버 사진도 제출 불가 상태가 되는지 검증한다."""

import asyncio
import contextlib
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import facemarket_applications as applications


class _R2:
    def __init__(self, events):
        self.events = events

    def delete(self, key):
        self.events.append(("r2-delete", key))


class _Cursor:
    def __init__(self, events, row):
        self.events = events
        self.row = row

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, sql, params):
        assert "delete from fm_model_application_photo_staging" in sql.lower()
        self.events.append(("db-delete", params))

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
    conn = _Conn(events, {"r2_key": "private/staging/profile.jpg"})

    @contextlib.asynccontextmanager
    async def fake_get_conn(_request):
        yield conn

    monkeypatch.setattr(applications, "get_conn", fake_get_conn)
    response = asyncio.run(applications.delete_application_photo_staging(
        _request(_R2(events)), kind="profile", user_id="user-1"
    ))

    assert response.status_code == 204
    assert events == [
        ("db-delete", ("user-1", "profile")),
        ("commit", None),
        ("r2-delete", "private/staging/profile.jpg"),
    ]


def test_delete_staging_photo_rejects_an_unknown_kind():
    with pytest.raises(HTTPException) as caught:
        asyncio.run(applications.delete_application_photo_staging(
            _request(_R2([])), kind="unknown", user_id="user-1"
        ))
    assert caught.value.detail["code"] == "invalid_photo_kind"
