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
CLOSEUP_ALT_ID = "55555555-5555-5555-5555-555555555555"
FULLBODY_CUT_ID = "66666666-6666-6666-6666-666666666666"
FULLBODY_ALT_ID = "77777777-7777-7777-7777-777777777777"
NOW = datetime(2026, 9, 7, 3, 0, tzinfo=timezone.utc)
LICENSE_VALID_UNTIL = datetime(2027, 9, 7, 3, 0, tzinfo=timezone.utc)


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
        "kind": cut.get("kind"),
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

        if "as license_valid_days" in query and "from fm_models m" in query:
            if "where m.id = %s" in query:
                if model["id"] == str(params[0]):
                    self.one = {
                        "id": model["id"],
                        "display_name": model["display_name"],
                        "gender": model["gender"],
                        "height_cm": self.store["application"]["height_cm"],
                        "height_bucket": model["height_bucket"],
                        "body_type": model["body_type"],
                        "allowed_use": self.store["license"]["allowed_use"],
                        "unit_price": self.store["license"]["unit_price"],
                        "license_valid_until": self.store["license"]["license_valid_until"],
                        "license_valid_days": self.store["license"]["valid_days"],
                        "cover_image_url": model.get("cover_image_url"),
                        "fullbody_image_url": model.get("fullbody_image_url"),
                        "confirmed_at": model.get("confirmed_at"),
                    }
                    self.many = [self.one]
            else:
                eligible = [
                    candidate
                    for candidate in self.store.get("public_candidates", [])
                    if candidate["status"] == "verified"
                    and candidate.get("confirmed_at") is not None
                    and (candidate.get("cover_image_url") or "").startswith(
                        "facemarket/catalog/models/"
                    )
                    and (candidate.get("fullbody_image_url") or "").startswith(
                        "facemarket/catalog/models/"
                    )
                    and candidate.get("license_status") == "active"
                    and candidate.get("vc_id")
                    and candidate.get("license_valid_until") > NOW
                ]
                self.many = sorted(
                    eligible, key=lambda item: item["confirmed_at"], reverse=True
                )[:200]
                self.one = self.many[0] if self.many else None
        elif (
            query.startswith("select m.id::text as id, m.display_name")
            and "left join fm_model_test_cuts" in query
        ):
            # 콘솔 목록과 경로가 겹치지 않게 모델 1건 하위 리소스로 바뀌었다 → fetchone 도 채운다.
            closeup_count = sum(c.get("kind") == "closeup" for c in cuts)
            fullbody_count = sum(c.get("kind") == "fullbody" for c in cuts)
            row = {
                "id": model["id"],
                "display_name": model["display_name"],
                "status": model["status"],
                "enrollment_status": self.store["enrollment"]["status"],
                "test_cut_count": len(cuts),
                "closeup_count": closeup_count,
                "fullbody_count": fullbody_count,
                "cuts_complete": closeup_count == 2 and fullbody_count == 2,
                "redo_requested": model["status"] == "pending" and model["redo_count"] > 0,
                "confirm_requested_at": model.get("confirm_requested_at"),
                "confirmed_at": model.get("confirmed_at"),
                "redo_count": model["redo_count"],
                "ready_to_send": (
                    self.store["enrollment"]["status"] == "passed"
                    and self.store["license_active"]
                    and self.store["license"]["license_valid_until"] > NOW
                ),
                "sendable": (
                    model["status"] in {"pending", "awaiting_confirm", "reverification_required"}
                    or (model["status"] == "verified" and not model.get("fullbody_image_url"))
                ),
                "test_cuts": [
                    _cut_view(c) for c in sorted(cuts, key=lambda item: item["sort"])
                ],
            }
            wanted = str(params[0]) if params else None
            if wanted is not None and wanted != row["id"]:
                self.one, self.many = None, []
            else:
                self.one, self.many = row, [row]
        elif query.startswith("select m.id::text as id, m.status") and "from fm_models m" in query:
            model_id = params[-1]
            if model["id"] == model_id:
                self.one = {
                    "id": model["id"],
                    "status": model["status"],
                    "redo_count": model["redo_count"],
                    "enrollment_status": self.store["enrollment"]["status"],
                    "contact_email": self.store["application"]["contact_email"],
                    "fullbody_image_url": model.get("fullbody_image_url"),
                    "has_active_license": (
                        self.store["license_active"]
                        and self.store["license"]["license_valid_until"] > NOW
                    ),
                }
        elif (
            query.startswith("select id::text as id, status, redo_count")
            and "from fm_models" in query
        ):
            user_id = params[0]
            if model["user_id"] == user_id:
                self.one = {
                    "id": model["id"],
                    "status": model["status"],
                    "redo_count": model["redo_count"],
                    "confirm_requested_at": model.get("confirm_requested_at"),
                    "confirmed_at": model.get("confirmed_at"),
                }
        elif (
            query.startswith("select id::text as id")
            and "from fm_model_test_cuts" in query
            and "order by sort" in query
        ):
            model_id = params[0]
            self.many = [_cut_view(c) for c in cuts if c["model_id"] == model_id]
            self.many.sort(key=lambda item: item["sort"])
        elif query.startswith("select sort") and "from fm_model_test_cuts" in query:
            model_id = params[0]
            self.many = [
                {"sort": c["sort"], "kind": c.get("kind")}
                for c in cuts
                if c["model_id"] == model_id
            ]
        elif query.startswith("insert into fm_model_test_cuts"):
            if len(params) == 6:
                cut_id, model_id, key, mime, kind, sort = params
            else:
                cut_id, model_id, key, mime, sort = params
                kind = None
            cut = {
                "id": cut_id,
                "model_id": model_id,
                "r2_key": key,
                "mime": mime,
                "kind": kind,
                "sort": sort,
                "approved": None,
                "created_at": NOW,
            }
            cuts.append(cut)
            self.one = _cut_view(cut)
        elif (
            query.startswith("select id::text as id, r2_key, mime")
            and "from fm_model_test_cuts" in query
        ):
            cut_id, model_id = params[:2]
            cut = next((c for c in cuts if c["id"] == cut_id and c["model_id"] == model_id), None)
            self.one = copy.deepcopy(cut)
        elif query.startswith("delete from fm_model_test_cuts"):
            cut_id, model_id = params
            cuts[:] = [c for c in cuts if not (c["id"] == cut_id and c["model_id"] == model_id)]
        elif query.startswith("select count") and "fm_model_test_cuts" in query:
            model_id = params[0]
            selected = [c for c in cuts if c["model_id"] == model_id]
            self.one = {
                "closeup_count": sum(c.get("kind") == "closeup" for c in selected),
                "fullbody_count": sum(c.get("kind") == "fullbody" for c in selected),
            }
        elif query.startswith("select exists") and "fm_model_test_cuts" in query:
            model_id = params[0]
            self.one = {"has_cuts": any(c["model_id"] == model_id for c in cuts)}
        elif query.startswith("select exists") and "fm_licenses" in query:
            self.one = {
                "license_ok": (
                    model["id"] == params[0]
                    and self.store["license_active"]
                    and self.store["license"]["license_valid_until"] > NOW
                )
            }
        elif query.startswith("update fm_models set status = 'awaiting_confirm'"):
            model["status"] = "awaiting_confirm"
            model["confirm_requested_at"] = NOW
            self.one = {"confirm_requested_at": NOW}
        elif query.startswith("select c.id::text as id, c.r2_key"):
            cut_id, user_id = params[:2]
            cut = next(
                (
                    c
                    for c in cuts
                    if c["id"] == cut_id and model["user_id"] == user_id
                ),
                None,
            )
            if cut:
                self.one = {
                    **copy.deepcopy(cut),
                    "model_id": model["id"],
                    "model_status": model["status"],
                    "redo_count": model["redo_count"],
                    "display_name": model["display_name"],
                }
        elif query.startswith("update fm_model_test_cuts set approved"):
            if len(params) == 3:
                closeup_id, fullbody_id, model_id = params
                approved_ids = {closeup_id, fullbody_id}
            else:
                approved_id, model_id = params
                approved_ids = {approved_id}
            for cut in cuts:
                if cut["model_id"] == model_id:
                    cut["approved"] = cut["id"] in approved_ids
        elif query.startswith("update fm_models set status = 'verified'"):
            if len(params) == 5:
                consent_version, cover_key, fullbody_key, model_id, user_id = params
            else:
                consent_version, cover_key, model_id, user_id = params
                fullbody_key = None
            if (
                model["id"] == model_id
                and model["user_id"] == user_id
                and model["status"] == "awaiting_confirm"
            ):
                model.update(
                    status="verified",
                    confirmed_at=NOW,
                    confirm_consent_version=consent_version,
                    cover_image_url=cover_key,
                    fullbody_image_url=fullbody_key,
                )
                self.one = {"confirmed_at": NOW}
        elif query.startswith("update fm_models set status = 'pending'"):
            reason, model_id, user_id = params
            if (
                model["id"] == model_id
                and model["user_id"] == user_id
                and model["status"] == "awaiting_confirm"
                and model["redo_count"] < 1
            ):
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
            "display_name": "정일상",
            "gender": "male",
            "height_bucket": "m_175_180",
            "body_type": "toned",
            "status": "pending",
            "redo_count": 0,
            "current_enrollment_id": ENROLLMENT_ID,
            "confirm_requested_at": None,
            "confirmed_at": None,
            "confirm_consent_version": None,
            "cover_image_url": None,
            "fullbody_image_url": None,
        },
        "enrollment": {
            "id": ENROLLMENT_ID,
            "status": "passed",
            "application_id": APPLICATION_ID,
        },
        "application": {
            "id": APPLICATION_ID,
            "applicant_name": "민감한 실명",
            "contact_email": "model@example.com",
            "height_cm": 178,
        },
        "license": {
            "allowed_use": ["상의", "아우터"],
            "unit_price": 10000,
            "license_valid_until": LICENSE_VALID_UNTIL,
            "valid_days": 365,
        },
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


def _seed_cut(
    store,
    r2,
    *,
    cut_id=CUT_ID,
    kind="closeup",
    approved=None,
    data=None,
):
    data = data or _png()
    key = f"private/facemarket/models/{MODEL_ID}/test-cuts/{cut_id}.png"
    cut = {
        "id": cut_id,
        "model_id": MODEL_ID,
        "r2_key": key,
        "mime": "image/png",
        "kind": kind,
        "sort": len(store["cuts"]),
        "approved": approved,
        "created_at": NOW,
    }
    store["cuts"].append(cut)
    r2.objects[key] = (data, "image/png", None)
    return cut


def _seed_complete_cuts(store, r2, *, data=None):
    return [
        _seed_cut(store, r2, cut_id=CUT_ID, kind="closeup", data=data),
        _seed_cut(store, r2, cut_id=CLOSEUP_ALT_ID, kind="closeup", data=data),
        _seed_cut(store, r2, cut_id=FULLBODY_CUT_ID, kind="fullbody", data=data),
        _seed_cut(store, r2, cut_id=FULLBODY_ALT_ID, kind="fullbody", data=data),
    ]


def test_admin_test_cuts_requires_admin_role(test_cut_api):
    client, _store, _face, _public, make_token = test_cut_api
    response = client.get(
        f"/v1/facemarket/admin/models/{MODEL_ID}/test-cuts", headers=_auth(make_token)
    )
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
            kind="closeup",
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
        data={"kind": "closeup"},
        headers=_auth(make_token, "admin-1"),
    )
    assert uploaded.status_code == 201, uploaded.text
    assert len(uploaded.json()) == 2
    assert "r2Key" not in uploaded.text and "private/facemarket" not in uploaded.text

    listing = client.get(
        f"/v1/facemarket/admin/models/{MODEL_ID}/test-cuts",
        headers=_auth(make_token, "admin-1"),
    )
    assert listing.status_code == 200
    assert listing.json()["testCutCount"] == 2
    assert listing.json()["closeupCount"] == 2
    assert listing.json()["fullbodyCount"] == 0
    assert listing.json()["cutsComplete"] is False
    assert listing.json()["confirmedAt"] is None
    assert len(listing.json()["testCuts"]) == 2
    assert {cut["kind"] for cut in listing.json()["testCuts"]} == {"closeup"}
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
        data={"kind": "closeup"},
        headers=_auth(make_token, "admin-1"),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_image"
    assert store["cuts"] == []
    assert face_r2.objects == {}


@pytest.mark.parametrize("data", [{}, {"kind": "portrait"}, {"kind": "CLOSEUP"}])
def test_admin_upload_requires_a_supported_kind(test_cut_api, data):
    client, store, face_r2, _public, make_token = test_cut_api

    response = client.post(
        f"/v1/facemarket/admin/models/{MODEL_ID}/test-cuts",
        files=[("images", ("one.png", _png(), "image/png"))],
        data=data,
        headers=_auth(make_token, "admin-1"),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_kind"
    assert store["cuts"] == []
    assert face_r2.objects == {}


def test_admin_upload_rejects_the_third_cut_of_one_kind(test_cut_api):
    client, store, face_r2, _public, make_token = test_cut_api
    first = client.post(
        f"/v1/facemarket/admin/models/{MODEL_ID}/test-cuts",
        files=[
            ("images", ("one.png", _png(), "image/png")),
            ("images", ("two.png", _png(color=(80, 140, 220)), "image/png")),
        ],
        data={"kind": "closeup"},
        headers=_auth(make_token, "admin-1"),
    )
    assert first.status_code == 201, first.text

    third = client.post(
        f"/v1/facemarket/admin/models/{MODEL_ID}/test-cuts",
        files=[("images", ("three.png", _png(color=(10, 20, 30)), "image/png"))],
        data={"kind": "closeup"},
        headers=_auth(make_token, "admin-1"),
    )

    assert third.status_code == 409
    assert third.json()["error"]["code"] == "cut_limit"
    assert len(store["cuts"]) == len(face_r2.objects) == 2


def test_send_commits_awaiting_confirm_when_resend_is_not_configured(test_cut_api):
    client, store, face_r2, _public, make_token = test_cut_api
    _seed_complete_cuts(store, face_r2)
    response = client.post(
        f"/v1/facemarket/admin/models/{MODEL_ID}/send-test-cuts",
        headers=_auth(make_token, "admin-1"),
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "awaiting_confirm"
    assert response.json()["emailSent"] is False
    assert store["model"]["status"] == "awaiting_confirm"
    assert store["model"]["confirm_requested_at"] == NOW


def test_admin_send_requires_all_four_kinds(test_cut_api):
    client, store, face_r2, _public, make_token = test_cut_api
    _seed_cut(store, face_r2, cut_id=CUT_ID, kind="closeup")
    _seed_cut(store, face_r2, cut_id=CLOSEUP_ALT_ID, kind="closeup")
    _seed_cut(store, face_r2, cut_id=FULLBODY_CUT_ID, kind="fullbody")
    response = client.post(
        f"/v1/facemarket/admin/models/{MODEL_ID}/send-test-cuts",
        headers=_auth(make_token, "admin-1"),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "test_cuts_incomplete"
    assert store["model"]["status"] == "pending"


@pytest.mark.parametrize(
    "enrollment_status,license_active",
    [("license_pending", False), ("passed", False)],
)
def test_admin_cannot_send_before_enrollment_and_vc_are_complete(
    test_cut_api, enrollment_status, license_active
):
    client, store, face_r2, _public, make_token = test_cut_api
    _seed_complete_cuts(store, face_r2)
    store["enrollment"]["status"] = enrollment_status
    store["license_active"] = license_active

    response = client.post(
        f"/v1/facemarket/admin/models/{MODEL_ID}/send-test-cuts",
        headers=_auth(make_token, "admin-1"),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "model_registration_incomplete"
    assert store["model"]["status"] == "pending"


def test_admin_can_resend_to_legacy_verified_model_without_fullbody(test_cut_api):
    """옛 1장 확정 모델(verified, 전신샷 없음)은 공개 목록에 없으므로 2+2 로 다시 보낼 수 있다."""
    client, store, face_r2, _public, make_token = test_cut_api
    _seed_complete_cuts(store, face_r2)
    store["model"].update(
        status="verified",
        confirmed_at=NOW,
        cover_image_url=f"facemarket/catalog/models/{MODEL_ID}/covers/old.webp",
        fullbody_image_url=None,
    )
    card = client.get(
        f"/v1/facemarket/admin/models/{MODEL_ID}/test-cuts",
        headers=_auth(make_token, "admin-1"),
    )
    assert card.json()["sendable"] is True

    response = client.post(
        f"/v1/facemarket/admin/models/{MODEL_ID}/send-test-cuts",
        headers=_auth(make_token, "admin-1"),
    )
    assert response.status_code == 200, response.text
    assert store["model"]["status"] == "awaiting_confirm"


def test_admin_cannot_resend_to_a_model_already_published_with_two_cuts(test_cut_api):
    client, store, face_r2, _public, make_token = test_cut_api
    _seed_complete_cuts(store, face_r2)
    store["model"].update(
        status="verified",
        confirmed_at=NOW,
        cover_image_url=f"facemarket/catalog/models/{MODEL_ID}/covers/a.webp",
        fullbody_image_url=f"facemarket/catalog/models/{MODEL_ID}/covers/b.webp",
    )
    card = client.get(
        f"/v1/facemarket/admin/models/{MODEL_ID}/test-cuts",
        headers=_auth(make_token, "admin-1"),
    )
    assert card.json()["sendable"] is False

    response = client.post(
        f"/v1/facemarket/admin/models/{MODEL_ID}/send-test-cuts",
        headers=_auth(make_token, "admin-1"),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "model_not_sendable"
    assert store["model"]["status"] == "verified"


def test_admin_cannot_send_when_license_is_expired_even_if_status_is_active(test_cut_api):
    client, store, face_r2, _public, make_token = test_cut_api
    _seed_complete_cuts(store, face_r2)
    store["license"]["license_valid_until"] = datetime(2026, 9, 6, tzinfo=timezone.utc)
    card = client.get(
        f"/v1/facemarket/admin/models/{MODEL_ID}/test-cuts",
        headers=_auth(make_token, "admin-1"),
    )
    assert card.json()["readyToSend"] is False

    response = client.post(
        f"/v1/facemarket/admin/models/{MODEL_ID}/send-test-cuts",
        headers=_auth(make_token, "admin-1"),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "model_registration_incomplete"


def test_model_confirm_rejects_when_license_is_no_longer_active(test_cut_api):
    """전송 뒤 모델이 라이선스를 해지하면(ModelLicense 화면) 확정은 409 로 멈추고 공개 파일도 남기지 않는다."""
    client, store, face_r2, public_r2, make_token = test_cut_api
    _seed_complete_cuts(store, face_r2)
    store["model"]["status"] = "awaiting_confirm"
    store["license_active"] = False

    response = client.post(
        "/v1/facemarket/model/test-cuts/confirm",
        json={"closeupCutId": CUT_ID, "fullbodyCutId": FULLBODY_CUT_ID},
        headers=_auth(make_token),
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "license_inactive"
    assert public_r2.objects == {}
    assert store["model"]["status"] == "awaiting_confirm"
    assert all(cut["approved"] is None for cut in store["cuts"])


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
    # send_application_email 은 (subject, html, text) 세 값을 풀어 쓴다 — 두 값만 돌려주면
    # TypeError 가 나고 보내기 핸들러가 그 예외를 삼켜 메일이 조용히 안 나간다.
    subject, html, text = facemarket_notify._email_content(
        "test_cuts_ready",
        public_base="https://facemarket.example",
        reject_reason=None,
    )
    assert "테스트컷" in subject
    assert 'href="https://facemarket.example/model/confirm"' in html
    assert "https://facemarket.example/model/confirm" in text


def test_test_cut_ready_email_is_actually_sent_when_resend_is_configured(monkeypatch):
    posted = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"id": "msg-1"}

    class FakeAsyncClient:
        def __init__(self, *, timeout):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, url, *, headers, json):
            posted.update(url=url, payload=json)
            return FakeResponse()

    monkeypatch.setattr(facemarket_notify.httpx, "AsyncClient", FakeAsyncClient)
    settings = make_settings(facemarket_enabled=True, fm_ci_pepper="pep", resend_api_key="re_test")
    ok, message_id, error = asyncio.run(
        facemarket_notify.send_application_email(
            settings, to="model@example.com", email_type="test_cuts_ready"
        )
    )
    assert (ok, message_id, error) == (True, "msg-1", None)
    assert posted["payload"]["to"] == ["model@example.com"]
    assert "/model/confirm" in posted["payload"]["text"]


def test_model_confirmation_slack_uses_existing_webhook_pattern(monkeypatch):
    sent = {}

    class FakeResponse:
        status_code = 200

    class FakeAsyncClient:
        def __init__(self, *, timeout):
            sent["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, url, *, json):
            sent.update(url=url, payload=json)
            return FakeResponse()

    monkeypatch.setattr(facemarket_notify.httpx, "AsyncClient", FakeAsyncClient)
    settings = make_settings(
        facemarket_enabled=True,
        fm_ci_pepper="pep",
        fm_slack_webhook_url="https://hooks.example/facemarket",
    )

    asyncio.run(
        facemarket_notify.notify_slack_model_confirmed(
            settings,
            display_name="<정일상>",
            admin_link="https://admin.wearless.kr/models",
        )
    )

    assert sent["url"] == "https://hooks.example/facemarket"
    assert sent["payload"] == {
        "text": (
            ":white_check_mark: 모델 공개 확정 · 활동명: &lt;정일상&gt;\n"
            "<https://admin.wearless.kr/models|관리자 모델 콘솔 열기>"
        )
    }


def test_model_confirm_sets_two_selected_cuts_and_public_1024_images(
    test_cut_api, monkeypatch
):
    client, store, face_r2, public_r2, make_token = test_cut_api
    original = _png(1600, 1200)
    _seed_complete_cuts(store, face_r2, data=original)
    store["model"]["status"] = "awaiting_confirm"
    notified = {}

    async def fake_notify(_settings, *, display_name, admin_link):
        notified.update(display_name=display_name, admin_link=admin_link)
        raise RuntimeError("slack unavailable")

    monkeypatch.setattr(
        facemarket_notify, "notify_slack_model_confirmed", fake_notify, raising=False
    )

    mine = client.get("/v1/facemarket/model/test-cuts", headers=_auth(make_token))
    assert mine.status_code == 200
    assert mine.json()["status"] == "awaiting_confirm"
    assert mine.json()["cuts"][0]["imageUri"].endswith(f"/{CUT_ID}/image")
    assert {cut["kind"] for cut in mine.json()["cuts"]} == {"closeup", "fullbody"}
    assert mine.json()["profile"] == {
        "displayName": "정일상",
        "gender": "male",
        "heightCm": 178,
        "heightBucket": "m_175_180",
        "bodyType": "toned",
        "license": {
            "allowedUse": ["상의", "아우터"],
            "unitPrice": 10000,
            "validUntil": LICENSE_VALID_UNTIL.isoformat().replace("+00:00", "Z"),
            "validDays": 365,
        },
    }
    assert "r2Key" not in mine.text and "private/facemarket" not in mine.text

    confirmed = client.post(
        "/v1/facemarket/model/test-cuts/confirm",
        json={"closeupCutId": CUT_ID, "fullbodyCutId": FULLBODY_ALT_ID},
        headers=_auth(make_token),
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "verified"
    assert confirmed.json()["closeupImageUrl"].startswith("https://assets.example/")
    assert confirmed.json()["fullbodyImageUrl"].startswith("https://assets.example/")
    assert store["model"]["status"] == "verified"
    assert store["model"]["confirmed_at"] == NOW
    assert store["model"]["confirm_consent_version"] == "2026-10-v1"
    assert store["model"]["cover_image_url"]
    assert store["model"]["fullbody_image_url"]
    assert {
        cut["id"]: cut["approved"] for cut in store["cuts"]
    } == {
        CUT_ID: True,
        CLOSEUP_ALT_ID: False,
        FULLBODY_CUT_ID: False,
        FULLBODY_ALT_ID: True,
    }
    assert len(face_r2.objects) == 4, "비공개 원본은 남아야 한다"
    assert len(public_r2.objects) == 2
    for image_bytes, image_mime, _cache in public_r2.objects.values():
        with Image.open(BytesIO(image_bytes)) as image:
            assert max(image.size) == 1024
            assert image.format == "WEBP"
        assert image_mime == "image/webp"
    assert notified == {
        "display_name": "정일상",
        "admin_link": "https://admin.wearless.kr/models",
    }
    admin_view = client.get(
        f"/v1/facemarket/admin/models/{MODEL_ID}/test-cuts",
        headers=_auth(make_token, "admin-1"),
    )
    assert admin_view.status_code == 200
    assert admin_view.json()["closeupCount"] == 2
    assert admin_view.json()["fullbodyCount"] == 2
    assert admin_view.json()["cutsComplete"] is True
    assert admin_view.json()["confirmedAt"] == NOW.isoformat().replace("+00:00", "Z")


def test_model_confirm_rejects_cut_ids_with_swapped_kinds(test_cut_api):
    client, store, face_r2, public_r2, make_token = test_cut_api
    _seed_complete_cuts(store, face_r2)
    store["model"]["status"] = "awaiting_confirm"

    response = client.post(
        "/v1/facemarket/model/test-cuts/confirm",
        json={"closeupCutId": FULLBODY_CUT_ID, "fullbodyCutId": CUT_ID},
        headers=_auth(make_token),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "kind_mismatch"
    assert public_r2.objects == {}
    assert store["model"]["status"] == "awaiting_confirm"


def test_model_confirm_deletes_both_public_images_when_commit_fails(test_cut_api):
    client, store, face_r2, public_r2, make_token = test_cut_api
    _seed_complete_cuts(store, face_r2)
    store["model"]["status"] = "awaiting_confirm"
    store["fail_commit_once"] = True

    response = client.post(
        "/v1/facemarket/model/test-cuts/confirm",
        json={"closeupCutId": CUT_ID, "fullbodyCutId": FULLBODY_CUT_ID},
        headers=_auth(make_token),
    )

    assert response.status_code == 500
    assert public_r2.objects == {}
    assert store["model"]["status"] == "awaiting_confirm"
    assert all(cut["approved"] is None for cut in store["cuts"])


def test_public_models_returns_only_eligible_profiles_without_pii(test_cut_api):
    client, store, _face_r2, _public_r2, _make_token = test_cut_api
    eligible = {
        "id": MODEL_ID,
        "display_name": "정일상",
        "gender": "male",
        "height_cm": 178,
        "height_bucket": "m_175_180",
        "body_type": "toned",
        "allowed_use": ["상의", "아우터"],
        "unit_price": 10000,
        "license_valid_until": LICENSE_VALID_UNTIL,
        "license_valid_days": 365,
        "status": "verified",
        "confirmed_at": NOW,
        "cover_image_url": f"facemarket/catalog/models/{MODEL_ID}/covers/closeup.webp",
        "fullbody_image_url": f"facemarket/catalog/models/{MODEL_ID}/covers/fullbody.webp",
        "license_status": "active",
        "vc_id": "vc:eligible",
        "email": "secret@example.com",
        "applicant_name": "민감한 실명",
        "user_id": "user-1",
    }
    no_fullbody = {
        **eligible,
        "id": "88888888-8888-8888-8888-888888888888",
        "fullbody_image_url": None,
    }
    expired = {
        **eligible,
        "id": "99999999-9999-9999-9999-999999999999",
        "license_valid_until": datetime(2026, 9, 6, tzinfo=timezone.utc),
    }
    store["public_candidates"] = [no_fullbody, expired, eligible]

    response = client.get("/v1/facemarket/public/models")

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "public, max-age=60"
    assert len(response.json()["items"]) == 1
    item = response.json()["items"][0]
    assert item == {
        "id": MODEL_ID,
        "displayName": "정일상",
        "gender": "male",
        "heightCm": 178,
        "heightBucket": "m_175_180",
        "bodyType": "toned",
        "license": {
            "allowedUse": ["상의", "아우터"],
            "unitPrice": 10000,
            "validUntil": LICENSE_VALID_UNTIL.isoformat().replace("+00:00", "Z"),
            "validDays": 365,
        },
        "closeupImageUrl": f"https://assets.example/{eligible['cover_image_url']}",
        "fullbodyImageUrl": f"https://assets.example/{eligible['fullbody_image_url']}",
        "confirmedAt": NOW.isoformat().replace("+00:00", "Z"),
    }
    assert item["closeupImageUrl"] != eligible["cover_image_url"]
    assert item["fullbodyImageUrl"] != eligible["fullbody_image_url"]
    for secret in ("secret@example.com", "민감한 실명", "user-1"):
        assert secret not in response.text
    public_query = next(
        query
        for query in store["sql"]
        if "m.confirmed_at is not null" in query
    )
    for clause in (
        "m.status = 'verified'",
        "m.cover_image_url like 'facemarket/catalog/models/%'",
        "m.fullbody_image_url like 'facemarket/catalog/models/%'",
        "l.enrollment_id = m.current_enrollment_id",
        "l.status = 'active'",
        "nullif(btrim(l.vc_id), '') is not null",
        "l.license_valid_until > now()",
        "order by m.confirmed_at desc limit 200",
    ):
        assert clause in public_query


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
