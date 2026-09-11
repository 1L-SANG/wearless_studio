"""FaceMarket 지원서 v3 서버 계약.

폼에서 실제로 보내는 camelCase 요청과 서버 응답, INSERT 경계를 함께 검증한다.
"""

import asyncio
import contextlib
import inspect
import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app import facemarket_applications as applications


MIGRATION = Path(__file__).resolve().parents[2] / (
    "supabase/migrations/20260910180000_facemarket_application_form_v3.sql"
)


def _payload(**overrides):
    payload = {
        "contactEmail": "model@example.com",
        "applicantName": "지원자",
        "birthdate": "1995-04-12",
        "region": "서울",
        "phone": "010-1234-5678",
        "heightCm": 172,
        "weightKg": 62,
        "agencyContracted": False,
        "categories": ["fashion"],
        "attestations": {
            "adultAndTruthful": True,
            "photosAreMine": True,
            "noAgencyContract": True,
            "reviewOnlyUse": True,
            "privacyPolicy": True,
        },
        "privacyConsent": {
            "accepted": True,
            "documentVersion": "2026-09-v1",
        },
    }
    payload.update(overrides)
    return payload


def _request(*, r2_face=None, auto_approve=False):
    state = SimpleNamespace(
        settings=SimpleNamespace(fm_application_auto_approve=auto_approve),
        r2_face=r2_face,
    )
    return SimpleNamespace(app=SimpleNamespace(state=state))


def _assert_submit_error(payload, *, code):
    body = applications.ApplicationSubmitBody.model_validate(payload)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(applications.submit_application(_request(), body, user_id="user-1"))
    assert caught.value.status_code == 400
    assert caught.value.detail["code"] == code


@pytest.mark.parametrize("field", ["phone", "heightCm", "agencyContracted"])
def test_v3_submit_fields_are_required(field):
    payload = _payload()
    payload.pop(field)

    with pytest.raises(ValidationError):
        applications.ApplicationSubmitBody.model_validate(payload)


def test_region_is_optional_and_categories_default_to_empty():
    payload = _payload()
    payload.pop("region")
    payload.pop("categories")

    body = applications.ApplicationSubmitBody.model_validate(payload)

    assert body.region is None
    assert body.categories == []


@pytest.mark.parametrize(
    "phone",
    [
        "010 1234 5678",
        "+82-10-1234-5678",
        "010-1234-ABCD",
        "12345678",
        "12345678901234",
    ],
)
def test_phone_rejects_wrong_characters_or_digit_count(phone):
    _assert_submit_error(_payload(phone=phone), code="invalid_phone")


@pytest.mark.parametrize("weight_kg", [29, 201])
def test_weight_must_be_between_30_and_200(weight_kg):
    _assert_submit_error(_payload(weightKg=weight_kg), code="invalid_weight")


@pytest.mark.parametrize("missing_key", ["noAgencyContract", "reviewOnlyUse", "privacyPolicy"])
def test_all_five_attestations_are_required(missing_key):
    attestations = dict(_payload()["attestations"])
    attestations.pop(missing_key)

    _assert_submit_error(_payload(attestations=attestations), code="attestation_required")


def test_invalid_category_is_still_rejected():
    _assert_submit_error(_payload(categories=["unknown"]), code="invalid_category")


class _R2:
    def copy(self, source, destination, mime):
        return None

    def delete(self, key):
        return None


class _Cursor:
    def __init__(self):
        self.last_sql = ""
        self.insert_sql = ""
        self.insert_params = ()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, sql, params=None):
        self.last_sql = " ".join(sql.split())
        if self.last_sql.startswith("insert into fm_model_applications"):
            self.insert_sql = self.last_sql
            self.insert_params = params

    async def fetchall(self):
        if "select kind, r2_key, mime_type" in self.last_sql:
            return [{"kind": "profile", "r2_key": "staged/profile.jpg", "mime_type": "image/jpeg"}]
        return []

    async def fetchone(self):
        if "status in ('rejected', 'cancelled')" in self.last_sql:
            return None
        if "contact_email <>" in self.last_sql:
            return {
                "id": "10000000-0000-0000-0000-000000000001",
                "user_id": "user-1",
                "status": "approved",
                "contact_email": "model@example.com",
                "applicant_name": "지원자",
                "birthdate": date(1995, 4, 12),
                "region": None,
                "gender": None,
                "height_cm": 172,
                "weight_kg": 62,
                "phone": "010-1234-5678",
                "experience_level": None,
                "agency_contracted": False,
                "categories": [],
                "portfolio_url": None,
                "sns_url": None,
                "bio": None,
                "profile_image_r2_key": "private/application/profile.jpg",
                "photo_keys": {"profile": "private/application/profile.jpg"},
                "attestations": {
                    "adultAndTruthful": True,
                    "photosAreMine": True,
                    "noAgencyContract": True,
                    "reviewOnlyUse": True,
                    "privacyPolicy": True,
                },
                "identity_mismatch_count": 0,
                "reviewed_at": datetime(2026, 9, 10, tzinfo=timezone.utc),
                "reject_reason": None,
                "created_at": datetime(2026, 9, 10, tzinfo=timezone.utc),
            }
        raise AssertionError(f"unexpected fetchone query: {self.last_sql}")


class _Conn:
    def __init__(self):
        self.cursor_instance = _Cursor()

    def cursor(self):
        return self.cursor_instance

    async def commit(self):
        return None


def test_empty_categories_and_region_are_stored_with_weight(monkeypatch):
    conn = _Conn()

    @contextlib.asynccontextmanager
    async def fake_get_conn(_request):
        yield conn

    monkeypatch.setattr(applications, "get_conn", fake_get_conn)
    payload = _payload(region=None, categories=[])
    body = applications.ApplicationSubmitBody.model_validate(payload)

    result = asyncio.run(
        applications.submit_application(
            _request(r2_face=_R2(), auto_approve=True), body, user_id="user-1"
        )
    )

    assert result.region is None
    assert result.weight_kg == 62
    assert "weight_kg" in conn.cursor_instance.insert_sql
    assert 62 in conn.cursor_instance.insert_params


def test_admin_card_exposes_weight():
    card = applications._admin_card(
        {
            "id": "application-1",
            "user_id": "user-1",
            "status": "under_review",
            "contact_email": "model@example.com",
            "applicant_name": "지원자",
            "birthdate": date(1995, 4, 12),
            "region": None,
            "height_cm": 172,
            "weight_kg": 62,
            "phone": "010-1234-5678",
            "agency_contracted": True,
            "created_at": datetime(2026, 9, 10, tzinfo=timezone.utc),
        }
    )

    assert card.weight_kg == 62


def test_v3_migration_makes_region_nullable_and_adds_checked_weight():
    assert MIGRATION.exists(), "v3 전진 마이그레이션이 없다"
    sql = " ".join(MIGRATION.read_text().split()).lower()
    assert "alter column region drop not null" in sql
    assert "add column if not exists weight_kg integer" in sql
    assert "weight_kg between 30 and 200" in sql


@pytest.mark.parametrize(
    ("status", "days_ago", "should_purge"),
    [
        ("rejected", 31, True),
        ("cancelled", 31, True),
        ("approved", 31, False),
        ("rejected", 29, False),
        ("cancelled", 29, False),
    ],
)
def test_terminal_pii_sweep_clears_body_fields_only_after_retention(
    status, days_ago, should_purge,
):
    # SQL 실행 결과를 검증한다. PostgreSQL 전용 문법만 SQLite 문법으로 바꾸고,
    # 대상 선택 조건과 UPDATE 할당은 실제 스윕의 쿼리를 그대로 사용한다.
    with contextlib.closing(sqlite3.connect(":memory:")) as db:
        db.row_factory = sqlite3.Row
        db.execute("""
            create table fm_model_applications (
                id text primary key, status text, terminated_at real,
                contact_email text, applicant_name text, birthdate text, region text,
                phone text, bio text, portfolio_url text, sns_url text,
                profile_image_r2_key text, photo_keys text,
                weight_kg integer, height_cm integer, gender text
            )
        """)
        db.execute("""
            insert into fm_model_applications values (
                'application-1', ?, julianday('now') - ?,
                'model@example.com', '지원자', '1995-04-12', '서울',
                '010-1234-5678', '소개', null, null, null, '{}', 62, 172, 'female'
            )
        """, (status, days_ago))
        before = dict(db.execute("select * from fm_model_applications").fetchone())

        class SweepCursor(_Cursor):
            async def execute(self, sql, params=None):
                sql = sql.replace("id::text", "id").replace("::jsonb", "")
                sql = sql.replace(
                    "now() - make_interval(days => %s)", "julianday('now') - %s"
                ).replace("= any(%s)", "in (select value from json_each(%s))")
                params = tuple(
                    json.dumps(value) if isinstance(value, list) else
                    value.isoformat() if isinstance(value, date) else value
                    for value in params
                )
                self.result = db.execute(sql.replace("%s", "?"), params)
                self.rowcount = self.result.rowcount

            async def fetchall(self):
                return [dict(row) for row in self.result.fetchall()]

        conn = _Conn()
        conn.cursor_instance = SweepCursor()

        @contextlib.asynccontextmanager
        async def connection():
            yield conn

        app = SimpleNamespace(state=SimpleNamespace(
            pool=SimpleNamespace(connection=connection), r2_face=None,
        ))
        count = asyncio.run(applications.sweep_terminal_application_pii(app))
        after = dict(db.execute("select * from fm_model_applications").fetchone())

        assert count == (1 if should_purge else 0)
        if should_purge:
            assert after["weight_kg"] is None
            assert after["height_cm"] is None
            assert after["gender"] is None
        else:
            assert after == before


def test_terminal_pii_sweep_update_includes_all_body_fields():
    source = inspect.getsource(applications.sweep_terminal_application_pii)
    update = source.split("update fm_model_applications", 1)[1].split("where id", 1)[0]
    for column in ("weight_kg", "height_cm", "gender"):
        assert f"{column} = null" in update


def test_missing_phone_returns_http_400(client, make_token):
    client.app.include_router(applications.router)
    payload = _payload()
    payload.pop("phone")
    response = client.post(
        "/v1/facemarket/applications", json=payload,
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.parametrize("missing_key", applications.ATTESTATION_KEYS)
def test_each_missing_attestation_returns_http_400(client, make_token, missing_key):
    client.app.include_router(applications.router)
    payload = _payload()
    payload["attestations"].pop(missing_key)
    response = client.post(
        "/v1/facemarket/applications", json=payload,
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "attestation_required"


@pytest.mark.parametrize("has_weight", [False, True])
def test_account_delete_clears_application_weight_when_column_exists(has_weight):
    from app.services import biometric_purge

    class RecordingCursor(_Cursor):
        def __init__(self):
            super().__init__()
            self.queries = []

        async def execute(self, sql, params=None):
            self.queries.append((" ".join(sql.split()), params))

    conn = _Conn()
    conn.cursor_instance = RecordingCursor()
    columns = {"user_id", "contact_email", "applicant_name", "birthdate", "region", "phone", "bio", "photo_keys", "terminated_at"}
    if has_weight:
        columns.add("weight_kg")
    asyncio.run(biometric_purge._cleanup(
        conn, {"fm_model_applications": columns},
        {"user_id": "user-1", "model_ids": set(), "license_ids": set(), "profile_ids": set()},
        set(), set(), [], {}, reason="account_delete", source_job_id=None,
        target_count=0, confirmed_absent_count=0,
    ))
    queries = conn.cursor_instance.queries
    assert any("applicant_name = '삭제된 지원자'" in sql for sql, _ in queries)
    weight_updates = [(sql, params) for sql, params in queries if "weight_kg" in sql]
    assert bool(weight_updates) is has_weight
    if has_weight:
        assert "weight_kg = null" in weight_updates[0][0]
        assert weight_updates[0][1] == ("user-1",)
