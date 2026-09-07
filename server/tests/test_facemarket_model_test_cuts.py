"""FaceMarket 모델 테스트컷 관리자 전송·모델 확인 API."""

import asyncio
import contextlib
import copy
from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import facemarket_admin_models, facemarket_notify
from app.main import create_app
from conftest import make_settings


MODEL_ID = "11111111-1111-1111-1111-111111111111"
ENROLLMENT_ID = "22222222-2222-2222-2222-222222222222"
APPLICATION_ID = "33333333-3333-3333-3333-333333333333"
CUT_ID = "44444444-4444-4444-4444-444444444444"
NOW = datetime(2026, 9, 7, 3, 0, tzinfo=timezone.utc)


def _png(width=40, height=60, color=(220, 120, 80)) -> bytes:
    out = BytesIO()
    Image.new("RGB", (width, height), color).save(out, format="PNG")
    return out.getvalue()


class FakeR2:
    def __init__(self, base="https://assets.example"):
        self.objects = {}
        self.base = base
        self.fail_get = False

    def put_bytes(self, key, data, mime, cache=None):
        self.objects[key] = (data, mime, cache)

    def get_bytes(self, key):
        if self.fail_get:
            raise RuntimeError("temporary read failure")
        return self.objects[key][0]

    def delete(self, key):
        self.objects.pop(key, None)

    def public_url(self, key):
        return f"{self.base}/{key}"


def _cut_view(cut):
    return {
        "id": cut["id"],
        "mime": cut["mime"],
        "sort": cut["sort"],
        "approved": cut.get("approved"),
        "created_at": cut.get("created_at", NOW),
    }


class FakeCursor:
    def __init__(self, store):
        self.store = store
        self.one = None
        self.many = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def execute(self, sql, params=None):
        query = " ".join(sql.split()).lower()
        params = params or ()
        self.store.setdefault("sql", []).append(query)
        self.one = None
        self.many = []
        model = self.store["model"]
        cuts = self.store["cuts"]

        if query.startswith("select m.id::text as id, m.display_name") and "left join fm_model_test_cuts" in query:
            self.many = [{
                "id": model["id"],
                "display_name": model["display_name"],
                "status": model["status"],
                "enrollment_status": self.store["enrollment"]["status"],
                "test_cut_count": len(cuts),
                "redo_requested": model["status"] == "pending" and model["redo_count"] > 0,
                "confirm_requested_at": model.get("confirm_requested_at"),
                "redo_count": model["redo_count"],
                "ready_to_send": self.store["enrollment"]["status"] == "passed" and self.store["license_active"],
                "test_cuts": [_cut_view(c) for c in sorted(cuts, key=lambda item: item["sort"])],
            }]
        elif query.startswith("select m.id::text as id, m.status") and "from fm_models m" in query:
            model_id = params[-1]
            if model["id"] == model_id:
                self.one = {
                    "id": model["id"],
                    "status": model["status"],
                    "redo_count": model["redo_count"],
                    "enrollment_status": self.store["enrollment"]["status"],
                    "contact_email": self.store["application"]["contact_email"],
                    "has_active_license": self.store["license_active"],
                }
        elif query.startswith("select id::text as id, status, redo_count") and "from fm_models" in query:
            user_id = params[0]
            if model["user_id"] == user_id:
                self.one = {
                    "id": model["id"],
                    "status": model["status"],
                    "redo_count": model["redo_count"],
                    "confirm_requested_at": model.get("confirm_requested_at"),
                    "confirmed_at": model.get("confirmed_at"),
                }
        elif query.startswith("select id::text as id, mime, sort, approved, created_at from fm_model_test_cuts"):
            model_id = params[0]
            self.many = [_cut_view(c) for c in cuts if c["model_id"] == model_id]
            self.many.sort(key=lambda item: item["sort"])
        elif query.startswith("select sort from fm_model_test_cuts"):
            model_id = params[0]
            self.many = [{"sort": c["sort"]} for c in cuts if c["model_id"] == model_id]
        elif query.startswith("insert into fm_model_test_cuts"):
            cut_id, model_id, key, mime, sort = params
            cut = {
                "id": cut_id,
                "model_id": model_id,
                "r2_key": key,
                "mime": mime,
                "sort": sort,
                "approved": None,
                "created_at": NOW,
            }
            cuts.append(cut)
            self.one = _cut_view(cut)
        elif query.startswith("select id::text as id, r2_key, mime, approved from fm_model_test_cuts"):
            cut_id, model_id = params[:2]
            cut = next((c for c in cuts if c["id"] == cut_id and c["model_id"] == model_id), None)
            self.one = copy.deepcopy(cut)
        elif query.startswith("delete from fm_model_test_cuts"):
            cut_id, model_id = params
            cuts[:] = [c for c in cuts if not (c["id"] == cut_id and c["model_id"] == model_id)]
        elif query.startswith("select exists") and "fm_model_test_cuts" in query:
            model_id = params[0]
            self.one = {"has_cuts": any(c["model_id"] == model_id for c in cuts)}
        elif query.startswith("update fm_models set status = 'awaiting_confirm'"):
            model["status"] = "awaiting_confirm"
            model["confirm_requested_at"] = NOW
            self.one = {"confirm_requested_at": NOW}
        elif query.startswith("select c.id::text as id, c.r2_key"):
            cut_id, user_id = params[:2]
            cut = next((c for c in cuts if c["id"] == cut_id and model["user_id"] == user_id), None)
            if cut:
                self.one = {
                    **copy.deepcopy(cut),
                    "model_id": model["id"],
                    "model_status": model["status"],
                    "redo_count": model["redo_count"],
                }
        elif query.startswith("update fm_model_test_cuts set approved"):
            approved_id, model_id = params
            for cut in cuts:
                if cut["model_id"] == model_id:
                    cut["approved"] = cut["id"] == approved_id
        elif query.startswith("update fm_models set status = 'verified'"):
            consent_version, cover_key, model_id, user_id = params
            if model["id"] == model_id and model["user_id"] == user_id and model["status"] == "awaiting_confirm":
                model.update(
                    status="verified",
                    confirmed_at=NOW,
                    confirm_consent_version=consent_version,
                    cover_image_url=cover_key,
                )
                self.one = {"confirmed_at": NOW}
        elif query.startswith("update fm_models set status = 'pending'"):
            reason, model_id, user_id = params
            if model["id"] == model_id and model["user_id"] == user_id and model["status"] == "awaiting_confirm" and model["redo_count"] < 1:
                model.update(
                    status="pending",
                    redo_count=model["redo_count"] + 1,
                    redo_reason=reason,
                    confirm_requested_at=None,
                )
                self.one = {"redo_count": model["redo_count"]}
        else:  # pragma: no cover - 새 SQL은 테스트 대역에도 명시적으로 추가한다.
            raise AssertionError(f"unexpected SQL: {query}")

    async def fetchone(self):
        return self.one

    async def fetchall(self):
        return self.many


class FakeConn:
    def __init__(self, store):
        self.store = store
        self.snapshot = copy.deepcopy(store)

    def cursor(self):
        return FakeCursor(self.store)

    async def commit(self):
        if self.store.pop("fail_commit_once", False):
            raise RuntimeError("commit failed")
        self.snapshot = copy.deepcopy(self.store)

    async def rollback(self):
        self.store.clear()
        self.store.update(copy.deepcopy(self.snapshot))


@pytest.fixture()
def test_cut_api(keypair, make_token, monkeypatch):
    _private, public_key = keypair
    app = create_app(make_settings(facemarket_enabled=True, fm_ci_pepper="pep"))
    app.state.jwt_key_resolver = lambda _token: public_key
    app.state.r2_face = FakeR2("https://private.invalid")
    app.state.r2 = FakeR2()
    store = {
        "model": {
            "id": MODEL_ID,
            "user_id": "user-1",
            "display_name": "홍*동",
            "status": "pending",
            "redo_count": 0,
            "current_enrollment_id": ENROLLMENT_ID,
            "confirm_requested_at": None,
            "confirmed_at": None,
            "confirm_consent_version": None,
            "cover_image_url": None,
        },
        "enrollment": {
            "id": ENROLLMENT_ID,
            "status": "passed",
            "application_id": APPLICATION_ID,
        },
        "application": {"id": APPLICATION_ID, "contact_email": "model@example.com"},
        "license_active": True,
        "cuts": [],
    }

    @contextlib.asynccontextmanager
    async def fake_get_conn(_request):
        conn = FakeConn(store)
        try:
            yield conn
        except Exception:
            await conn.rollback()
            raise

    async def fake_is_admin(_conn, user_id):
        return user_id == "admin-1"

    monkeypatch.setattr(facemarket_admin_models, "get_conn", fake_get_conn)
    monkeypatch.setattr(facemarket_admin_models.repo, "is_admin", fake_is_admin)
    return TestClient(app), store, app.state.r2_face, app.state.r2, make_token


def _auth(make_token, sub="user-1"):
    return {"Authorization": f"Bearer {make_token(sub=sub)}"}


def _seed_cut(store, r2, *, cut_id=CUT_ID, approved=None, data=None):
    data = data or _png()
    key = f"private/facemarket/models/{MODEL_ID}/test-cuts/{cut_id}.png"
    cut = {
        "id": cut_id,
        "model_id": MODEL_ID,
        "r2_key": key,
        "mime": "image/png",
        "sort": len(store["cuts"]),
        "approved": approved,
        "created_at": NOW,
    }
    store["cuts"].append(cut)
    r2.objects[key] = (data, "image/png", None)
    return cut


def test_admin_models_requires_admin_role(test_cut_api):
    client, _store, _face, _public, make_token = test_cut_api
    response = client.get("/v1/facemarket/admin/models", headers=_auth(make_token))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_non_admin_upload_is_rejected_before_reading_files(test_cut_api):
    _client, _store, face_r2, public_r2, _make_token = test_cut_api

    class UnreadableUpload:
        content_type = "image/png"

        async def read(self):
            raise AssertionError("non-admin upload body must not be read")

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(r2_face=face_r2, r2=public_r2))
    )
    with pytest.raises(Exception) as caught:
        asyncio.run(facemarket_admin_models.admin_upload_test_cuts(
            request=request,
            model_id=MODEL_ID,
            images=[UnreadableUpload()],
            user_id="user-1",
        ))
    assert caught.value.status_code == 403


def test_admin_upload_lists_and_streams_cuts_without_emitting_r2_keys(test_cut_api):
    client, store, face_r2, _public, make_token = test_cut_api
    uploaded = client.post(
        f"/v1/facemarket/admin/models/{MODEL_ID}/test-cuts",
        files=[
            ("images", ("one.png", _png(), "image/png")),
            ("images", ("two.png", _png(color=(80, 140, 220)), "image/png")),
        ],
        headers=_auth(make_token, "admin-1"),
    )
    assert uploaded.status_code == 201, uploaded.text
    assert len(uploaded.json()) == 2
    assert "r2Key" not in uploaded.text and "private/facemarket" not in uploaded.text

    listing = client.get("/v1/facemarket/admin/models", headers=_auth(make_token, "admin-1"))
    assert listing.status_code == 200
    assert listing.json()[0]["testCutCount"] == 2
    assert len(listing.json()[0]["testCuts"]) == 2
    assert "r2Key" not in listing.text and "private/facemarket" not in listing.text

    cut_id = uploaded.json()[0]["id"]
    image = client.get(
        f"/v1/facemarket/admin/models/{MODEL_ID}/test-cuts/{cut_id}/image",
        headers=_auth(make_token, "admin-1"),
    )
    assert image.status_code == 200
    assert image.content == _png()
    assert image.headers["cache-control"] == "private, no-store"
    assert len(face_r2.objects) == len(store["cuts"]) == 2


def test_admin_upload_rejects_corrupt_image_before_storage(test_cut_api):
    client, store, face_r2, _public, make_token = test_cut_api
    response = client.post(
        f"/v1/facemarket/admin/models/{MODEL_ID}/test-cuts",
        files=[("images", ("broken.png", b"not-an-image", "image/png"))],
        headers=_auth(make_token, "admin-1"),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_image"
    assert store["cuts"] == []
    assert face_r2.objects == {}


def test_send_commits_awaiting_confirm_when_resend_is_not_configured(test_cut_api):
    client, store, face_r2, _public, make_token = test_cut_api
    _seed_cut(store, face_r2)
    response = client.post(
        f"/v1/facemarket/admin/models/{MODEL_ID}/send-test-cuts",
        headers=_auth(make_token, "admin-1"),
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "awaiting_confirm"
    assert response.json()["emailSent"] is False
    assert store["model"]["status"] == "awaiting_confirm"
    assert store["model"]["confirm_requested_at"] == NOW


def test_admin_send_requires_at_least_one_cut(test_cut_api):
    client, store, _face_r2, _public, make_token = test_cut_api
    response = client.post(
        f"/v1/facemarket/admin/models/{MODEL_ID}/send-test-cuts",
        headers=_auth(make_token, "admin-1"),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "test_cuts_required"
    assert store["model"]["status"] == "pending"


@pytest.mark.parametrize(
    "enrollment_status,license_active",
    [("license_pending", False), ("passed", False)],
)
def test_admin_cannot_send_before_enrollment_and_vc_are_complete(
    test_cut_api, enrollment_status, license_active
):
    client, store, face_r2, _public, make_token = test_cut_api
    _seed_cut(store, face_r2)
    store["enrollment"]["status"] = enrollment_status
    store["license_active"] = license_active

    response = client.post(
        f"/v1/facemarket/admin/models/{MODEL_ID}/send-test-cuts",
        headers=_auth(make_token, "admin-1"),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "model_registration_incomplete"
    assert store["model"]["status"] == "pending"


def test_admin_can_delete_an_unapproved_cut_and_private_object(test_cut_api):
    client, store, face_r2, _public, make_token = test_cut_api
    _seed_cut(store, face_r2)
    response = client.delete(
        f"/v1/facemarket/admin/models/{MODEL_ID}/test-cuts/{CUT_ID}",
        headers=_auth(make_token, "admin-1"),
    )
    assert response.status_code == 204
    assert store["cuts"] == []
    assert face_r2.objects == {}


def test_admin_delete_restores_private_object_when_database_commit_fails(test_cut_api):
    client, store, face_r2, _public, make_token = test_cut_api
    _seed_cut(store, face_r2)
    original = dict(face_r2.objects)
    store["fail_commit_once"] = True

    response = client.delete(
        f"/v1/facemarket/admin/models/{MODEL_ID}/test-cuts/{CUT_ID}",
        headers=_auth(make_token, "admin-1"),
    )

    assert response.status_code == 500
    assert len(store["cuts"]) == 1
    assert face_r2.objects == original


def test_admin_delete_stops_before_delete_when_backup_read_fails(test_cut_api):
    client, store, face_r2, _public, make_token = test_cut_api
    _seed_cut(store, face_r2)
    original = dict(face_r2.objects)
    face_r2.fail_get = True

    response = client.delete(
        f"/v1/facemarket/admin/models/{MODEL_ID}/test-cuts/{CUT_ID}",
        headers=_auth(make_token, "admin-1"),
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "storage_unavailable"
    assert len(store["cuts"]) == 1
    assert face_r2.objects == original


def test_test_cut_ready_email_links_to_confirmation_page():
    subject, html = facemarket_notify._email_content(
        "test_cuts_ready",
        public_base="https://facemarket.example",
        reject_reason=None,
    )
    assert "테스트컷" in subject
    assert 'href="https://facemarket.example/model/confirm"' in html


def test_model_confirm_sets_verified_consent_and_public_1024_cover(test_cut_api):
    client, store, face_r2, public_r2, make_token = test_cut_api
    original = _png(1600, 1200)
    _seed_cut(store, face_r2, data=original)
    store["model"]["status"] = "awaiting_confirm"

    mine = client.get("/v1/facemarket/model/test-cuts", headers=_auth(make_token))
    assert mine.status_code == 200
    assert mine.json()["status"] == "awaiting_confirm"
    assert mine.json()["cuts"][0]["imageUri"].endswith(f"/{CUT_ID}/image")
    assert "r2Key" not in mine.text and "private/facemarket" not in mine.text

    confirmed = client.post(
        "/v1/facemarket/model/test-cuts/confirm",
        json={"approvedCutId": CUT_ID},
        headers=_auth(make_token),
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "verified"
    assert confirmed.json()["coverImageUrl"].startswith("https://assets.example/")
    assert store["model"]["status"] == "verified"
    assert store["model"]["confirmed_at"] == NOW
    assert store["model"]["confirm_consent_version"] == "2026-10-v1"
    assert store["cuts"][0]["approved"] is True
    assert len(face_r2.objects) == 1, "비공개 원본은 남아야 한다"
    assert len(public_r2.objects) == 1
    cover_bytes, cover_mime, _cache = next(iter(public_r2.objects.values()))
    with Image.open(BytesIO(cover_bytes)) as image:
        assert max(image.size) == 1024
        assert image.format == "WEBP"
    assert cover_mime == "image/webp"


def test_each_confirmation_attempt_uses_a_distinct_public_cover_key():
    first = facemarket_admin_models._new_confirmation_cover_key(MODEL_ID, CUT_ID)
    second = facemarket_admin_models._new_confirmation_cover_key(MODEL_ID, CUT_ID)
    assert first != second
    assert f"facemarket/catalog/models/{MODEL_ID}/covers/" in first
    assert CUT_ID in first


def test_confirmed_cover_has_1024px_long_edge_for_small_input():
    cover = facemarket_admin_models._resize_cover(_png(40, 60))
    with Image.open(BytesIO(cover)) as image:
        assert image.size == (683, 1024)


def test_other_user_cannot_read_model_test_cuts(test_cut_api):
    client, store, face_r2, _public, make_token = test_cut_api
    _seed_cut(store, face_r2)
    response = client.get(
        f"/v1/facemarket/model/test-cuts/{CUT_ID}/image",
        headers=_auth(make_token, "user-2"),
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_model_redo_is_allowed_once_then_returns_redo_limit(test_cut_api):
    client, store, _face, _public, make_token = test_cut_api
    store["model"]["status"] = "awaiting_confirm"
    first = client.post(
        "/v1/facemarket/model/test-cuts/redo",
        json={"reason": "얼굴 느낌이 달라요"},
        headers=_auth(make_token),
    )
    assert first.status_code == 200
    assert first.json() == {"status": "pending", "redoCount": 1}
    assert store["model"]["status"] == "pending"
    assert store["model"]["redo_count"] == 1

    store["model"]["status"] = "awaiting_confirm"
    second = client.post(
        "/v1/facemarket/model/test-cuts/redo",
        json={},
        headers=_auth(make_token),
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "redo_limit"


def test_approved_test_cut_cannot_be_deleted(test_cut_api):
    client, store, face_r2, _public, make_token = test_cut_api
    _seed_cut(store, face_r2, approved=True)
    response = client.delete(
        f"/v1/facemarket/admin/models/{MODEL_ID}/test-cuts/{CUT_ID}",
        headers=_auth(make_token, "admin-1"),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "approved_cut_locked"
    assert len(store["cuts"]) == len(face_r2.objects) == 1
