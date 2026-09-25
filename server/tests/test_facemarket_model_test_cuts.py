"""FaceMarket 모델 테스트컷 관리자 전송·모델 확인 API."""

import asyncio
import contextlib
import copy
from datetime import date, datetime, timezone
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import facemarket_admin_models, facemarket_notify
from app.main import create_app
from conftest import assert_query_binds, make_settings


MODEL_ID = "11111111-1111-1111-1111-111111111111"
ENROLLMENT_ID = "22222222-2222-2222-2222-222222222222"
APPLICATION_ID = "33333333-3333-3333-3333-333333333333"
CUT_ID = "44444444-4444-4444-4444-444444444444"
CLOSEUP_THIRD_ID = "88888888-8888-8888-8888-888888888888"
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
        "skin_finish_code": cut.get("skin_finish_code"),
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
        assert_query_binds(sql, params)
        query = " ".join(sql.split()).lower()
        params = params or ()
        self.store.setdefault("sql", []).append(query)
        self.store.setdefault("sql_params", []).append((query, tuple(params)))
        self.one = None
        self.many = []
        model = self.store["model"]
        cuts = self.store["cuts"]
        loras = self.store.setdefault("loras", [])

        if query.startswith("select a.contact_email, m.display_name"):
            self.one = {"contact_email": self.store["application"].get("contact_email"),
                        "display_name": model["display_name"]}
        elif "catalog_is_admin" in query:
            # 모델 리스트 열람 판정(facemarket_catalog_access). 이 대역에서는 셀러 계정 하나만 자격이 있다.
            self.one = {"catalog_is_admin": False, "catalog_is_seller": params[0] == "seller-1",
                        "catalog_is_model": False}
        elif "as license_valid_days" in query and "from fm_models m" in query:
            if "where m.id = %s" in query:
                if model["id"] == str(params[0]):
                    self.one = {
                        "id": model["id"],
                        "display_name": model["display_name"],
                        "gender": model["gender"],
                        "height_cm": self.store["application"]["height_cm"],
                        "birthdate": self.store["application"].get("birthdate"),
                        "height_bucket": model["height_bucket"],
                        "body_type": model["body_type"],
                        "allowed_use": self.store["license"]["allowed_use"],
                        "forbidden_use": self.store["license"].get("forbidden_use", []),
                        "unit_price": self.store["license"]["unit_price"],
                        "license_valid_until": self.store["license"]["license_valid_until"],
                        "license_valid_days": self.store["license"]["valid_days"],
                        "cover_image_url": model.get("cover_image_url"),
                        "fullbody_image_url": model.get("fullbody_image_url"),
                        "confirmed_at": model.get("confirmed_at"),
                        "sponsorship_enabled": model.get("sponsorship_enabled", False),
                        "instagram_handle": model.get("instagram_handle"),
                        "instagram_followers": model.get("instagram_followers"),
                        "instagram_followers_reported_at": model.get("instagram_followers_reported_at"),
                        "size_top": model.get("size_top"),
                        "size_bottom_waist": model.get("size_bottom_waist"),
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
                    and (candidate.get("license_valid_until") is None or candidate["license_valid_until"] > NOW)
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
                # 전량은 보정 3종 × (확대 2 + 전신 2) = 12장이다.
                "cuts_complete": closeup_count == 6 and fullbody_count == 6,
                "skin_finish": model.get("skin_finish_code", "prod"),
                "redo_requested": model["status"] == "pending" and model["redo_count"] > 0,
                "confirm_requested_at": model.get("confirm_requested_at"),
                "confirmed_at": model.get("confirmed_at"),
                "redo_count": model["redo_count"],
                "ready_to_send": (
                    self.store["enrollment"]["status"] == "passed"
                    and self.store["license_active"]
                    and (self.store["license"]["license_valid_until"] is None or self.store["license"]["license_valid_until"] > NOW)
                ),
                "sendable": (
                    model["status"] in {"pending", "awaiting_confirm", "reverification_required"}
                    or (model["status"] == "verified" and not model.get("fullbody_image_url"))
                ),
                "test_cuts": [
                    _cut_view(c) for c in sorted(cuts, key=lambda item: item["sort"])
                ],
            }
            wanted = str(params[-1]) if params else None
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
                        and (self.store["license"]["license_valid_until"] is None or self.store["license"]["license_valid_until"] > NOW)
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
                    "skin_finish_code": model.get("skin_finish_code", "prod"),
                }
        elif (
            query.startswith("select id::text as id")
            and "from fm_model_test_cuts" in query
            and "order by sort" in query
        ):
            model_id = params[0]
            # 등록자 목록은 **보낸 보정 묶음만** 본다(coalesce 로 옛 컷도 잡는다).
            finish = params[1] if len(params) > 1 else None
            self.many = [
                _cut_view(c) for c in cuts
                if c["model_id"] == model_id
                and (finish is None or (c.get("skin_finish_code") or finish) == finish)
            ]
            self.many.sort(key=lambda item: item["sort"])
        elif query.startswith("select sort") and "from fm_model_test_cuts" in query:
            model_id = params[0]
            self.many = [
                {"sort": c["sort"], "kind": c.get("kind"),
                 "skin_finish_code": c.get("skin_finish_code")}
                for c in cuts
                if c["model_id"] == model_id
            ]
        elif query.startswith("insert into fm_model_test_cuts"):
            finish = None
            if len(params) == 7:
                cut_id, model_id, key, mime, kind, sort, finish = params
            elif len(params) == 6:
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
                "skin_finish_code": finish,
                "sort": sort,
                "approved": None,
                "created_at": NOW,
                # 확대샷은 어느 보정 단계로 나온 장인지 행에 남는다. 옛 컷은 None.
                "skin_finish": finish,
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
            finish = params[1] if len(params) > 1 else None
            selected = [
                c for c in cuts
                if c["model_id"] == model_id
                and (finish is None or (c.get("skin_finish_code") or finish) == finish)
            ]
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
                    and (self.store["license"]["license_valid_until"] is None or self.store["license"]["license_valid_until"] > NOW)
                )
            }
        elif query.startswith("update fm_models set status = 'awaiting_confirm'"):
            model["status"] = "awaiting_confirm"
            model["confirm_requested_at"] = NOW
            # 보낸 보정이 곧 그 사람의 설정이다 — 같은 UPDATE 에서 적힌다.
            model["skin_finish_code"] = params[0]
            self.one = {"confirm_requested_at": NOW,
                        "skin_finish_code": model["skin_finish_code"]}
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
                    "confirmed_at": model.get("confirmed_at"),
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
                # 보정은 **전송 때** 이미 정해졌다 — 확정은 건드리지 않는다.
                self.one = {"confirmed_at": NOW,
                            "skin_finish_code": model.get("skin_finish_code", "prod")}
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
        elif query.startswith("select id::text as id from fm_model_loras"):
            ready = sorted(
                (row for row in loras
                 if row["model_id"] == params[0] and row["status"] == "ready"),
                key=lambda row: row["version"], reverse=True,
            )
            self.one = {"id": ready[0]["id"]} if ready else None
        elif query.startswith("update fm_model_loras set enabled = false"):
            model_id, keep_id = params
            for row in loras:
                if row["model_id"] == model_id and row["id"] != keep_id:
                    row["enabled"] = False
        elif query.startswith("update fm_model_loras set enabled = true"):
            for row in loras:
                if row["id"] == params[0]:
                    row["enabled"] = True
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
            "birthdate": date(2004, 3, 15),
        },
        "license": {
            "allowed_use": ["상의", "아우터"],
            "forbidden_use": ["속옷", "수영복"],
            "unit_price": 14900,
            "license_valid_until": LICENSE_VALID_UNTIL,
            "valid_days": 365,
        },
        "license_active": True,
        "cuts": [],
        # 승인 전에 붙여 둔 얼굴 LoRA(seed_model_lora --no-enable 의 결과). 확정하는 그
        # 트랜잭션에서 켜져야 한다 — 안 켜지면 "승인은 했는데 얼굴이 안 바뀐다" 가 된다.
        "loras": [],
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
    skin_finish="prod",
):
    data = data or _png()
    key = f"private/facemarket/models/{MODEL_ID}/test-cuts/{cut_id}.png"
    cut = {
        "id": cut_id,
        "model_id": MODEL_ID,
        "r2_key": key,
        "mime": "image/png",
        "kind": kind,
        "skin_finish_code": skin_finish,
        "sort": len(store["cuts"]),
        "approved": approved,
        "created_at": NOW,
    }
    store["cuts"].append(cut)
    r2.objects[key] = (data, "image/png", None)
    return cut


def _seed_complete_cuts(store, r2, *, data=None, skin_finish="prod"):
    """**한 보정 묶음**이 보낼 수 있는 구성 — 확대샷 2장 + 전신샷 2장.

    12장 전량이 아니라 한 묶음만 심는다. 전송은 묶음 단위라 이 넷이면 보낼 수 있고,
    "다른 보정 묶음이 비었을 때 그 보정으로는 못 보낸다" 는 따로 검사한다.
    """
    return [
        _seed_cut(store, r2, cut_id=CUT_ID, kind="closeup", data=data, skin_finish=skin_finish),
        _seed_cut(store, r2, cut_id=CLOSEUP_ALT_ID, kind="closeup", data=data,
                  skin_finish=skin_finish),
        _seed_cut(store, r2, cut_id=FULLBODY_CUT_ID, kind="fullbody", data=data,
                  skin_finish=skin_finish),
        _seed_cut(store, r2, cut_id=FULLBODY_ALT_ID, kind="fullbody", data=data,
                  skin_finish=skin_finish),
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
            skin_finish="prod",
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
        data={"kind": "closeup", "skin_finish": "prod"},
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
    # 보정별 묶음 구성도 같이 온다 — 화면이 묶음마다 보낼 수 있는지 이걸로 판단한다.
    assert listing.json()["finishCounts"]["prod"] == {"closeup": 2, "fullbody": 0}
    assert listing.json()["finishCounts"]["texture"] == {"closeup": 0, "fullbody": 0}
    assert {cut["skinFinish"] for cut in listing.json()["testCuts"]} == {"prod"}
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
        data={"kind": "closeup", "skin_finish": "prod"},
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


def test_the_cap_is_two_per_finish_and_kind(test_cut_api):
    """★ 상한은 **(보정, 종류) 조합마다** 2장이다.

    종류 총합만 보면 한 보정에 6장이 몰려도 통과하는데, 그러면 그 묶음만 보낼 수 있고 나머지
    둘은 영영 못 보낸다 — 관리자가 셋을 비교할 수 없게 된다.
    """
    client, store, face_r2, _public, make_token = test_cut_api
    first = client.post(
        f"/v1/facemarket/admin/models/{MODEL_ID}/test-cuts",
        files=[
            ("images", ("one.png", _png(), "image/png")),
            ("images", ("two.png", _png(color=(80, 140, 220)), "image/png")),
        ],
        data={"kind": "closeup", "skin_finish": "prod"},
        headers=_auth(make_token, "admin-1"),
    )
    assert first.status_code == 201, first.text

    third = client.post(
        f"/v1/facemarket/admin/models/{MODEL_ID}/test-cuts",
        files=[("images", ("three.png", _png(color=(1, 2, 3)), "image/png"))],
        data={"kind": "closeup", "skin_finish": "prod"},
        headers=_auth(make_token, "admin-1"),
    )
    assert third.status_code == 409
    assert third.json()["error"]["code"] == "cut_limit"

    # 다른 보정 묶음에는 그대로 들어간다 — 상한은 조합마다지 종류마다가 아니다.
    other = client.post(
        f"/v1/facemarket/admin/models/{MODEL_ID}/test-cuts",
        files=[("images", ("three.png", _png(color=(1, 2, 3)), "image/png"))],
        data={"kind": "closeup", "skin_finish": "texture"},
        headers=_auth(make_token, "admin-1"),
    )
    assert other.status_code == 201, other.text
    assert len(store["cuts"]) == len(face_r2.objects) == 3


def test_upload_without_a_finish_is_refused(test_cut_api):
    """보정 없이 올리면 어느 묶음인지 알 수 없어 전송에서 영원히 빠진다."""
    client, store, face_r2, _public, make_token = test_cut_api
    response = client.post(
        f"/v1/facemarket/admin/models/{MODEL_ID}/test-cuts",
        files=[("images", ("one.png", _png(), "image/png"))],
        data={"kind": "closeup"},
        headers=_auth(make_token, "admin-1"),
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "skin_finish_required"
    assert store["cuts"] == [] and face_r2.objects == {}


def test_send_commits_awaiting_confirm_when_resend_is_not_configured(test_cut_api):
    client, store, face_r2, _public, make_token = test_cut_api
    _seed_complete_cuts(store, face_r2)
    response = client.post(
        f"/v1/facemarket/admin/models/{MODEL_ID}/send-test-cuts",
        json={"skinFinish": "prod"},
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
        json={"skinFinish": "prod"},
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
        json={"skinFinish": "prod"},
        headers=_auth(make_token, "admin-1"),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "model_registration_incomplete"
    assert store["model"]["status"] == "pending"


@pytest.mark.parametrize(
    "birthdate,expected",
    [
        (date(2010, 1, 1), "10대"),
        (date(2004, 3, 15), "20대 초반"),
        (date(2001, 9, 8), "20대 중반"),
        (date(1998, 5, 1), "20대 후반"),
        (date(1994, 12, 31), "30대 초반"),
        (None, None),
    ],
)
def test_age_band_reports_only_a_decade_and_thirds(monkeypatch, birthdate, expected):
    monkeypatch.setattr(facemarket_admin_models, "_today", lambda: date(2026, 9, 7))
    assert facemarket_admin_models._age_band(birthdate) == expected


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
        json={"skinFinish": "prod"},
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
        json={"skinFinish": "prod"},
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
        json={"skinFinish": "prod"},
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


def _lora(row_id, version, *, enabled=False, status="ready"):
    return {"id": row_id, "model_id": MODEL_ID, "version": version,
            "enabled": enabled, "status": status}


def test_model_confirm_turns_on_the_face_asset(test_cut_api, monkeypatch):
    """본인이 테스트컷을 확인해야 얼굴 참조 자산이 쓰이기 시작한다.

    학습은 승인 전에 돌 수 있어서 seed_model_lora 가 행만 꺼진 채로 붙여 둔다
    (scripts/seed_model_lora.resolve_enable). 켜는 자리는 여기 하나다 — verified 로
    바꾸는 **같은 트랜잭션**이라, 승인은 됐는데 얼굴은 안 바뀌는 중간 상태가 없다.
    """
    client, store, face_r2, _public_r2, make_token = test_cut_api
    _seed_complete_cuts(store, face_r2, data=_png(1600, 1200))
    store["model"]["status"] = "awaiting_confirm"
    store["loras"] = [_lora("old", 1, enabled=True), _lora("new", 2)]

    monkeypatch.setattr(
        facemarket_notify, "notify_slack_model_confirmed",
        _noop_notify, raising=False,
    )
    confirmed = client.post(
        "/v1/facemarket/model/test-cuts/confirm",
        json={"closeupCutId": CUT_ID, "fullbodyCutId": FULLBODY_CUT_ID},
        headers=_auth(make_token),
    )

    assert confirmed.status_code == 200, confirmed.text
    assert store["model"]["status"] == "verified"
    # 최신 버전만 켜져 있다 — partial unique index(model_id) where enabled 를 지킨다.
    assert {row["id"]: row["enabled"] for row in store["loras"]} == {"old": False, "new": True}


def test_model_confirm_without_a_lora_row_still_verifies(test_cut_api, monkeypatch):
    """학습 전에 확정하는 경우 — verified 만 되고 얼굴 패스는 안 걸린다(500 이 아니다)."""
    client, store, face_r2, _public_r2, make_token = test_cut_api
    _seed_complete_cuts(store, face_r2, data=_png(1600, 1200))
    store["model"]["status"] = "awaiting_confirm"
    store["loras"] = []

    monkeypatch.setattr(
        facemarket_notify, "notify_slack_model_confirmed",
        _noop_notify, raising=False,
    )
    confirmed = client.post(
        "/v1/facemarket/model/test-cuts/confirm",
        json={"closeupCutId": CUT_ID, "fullbodyCutId": FULLBODY_CUT_ID},
        headers=_auth(make_token),
    )

    assert confirmed.status_code == 200, confirmed.text
    assert store["model"]["status"] == "verified"


async def _noop_notify(_settings, *, display_name, admin_link):
    return None


def test_owner_test_cut_profile_retains_disabled_sponsorship(test_cut_api):
    client, store, _face_r2, _public_r2, make_token = test_cut_api
    store["model"].update(
        sponsorship_enabled=False,
        instagram_handle="saved.model",
        instagram_followers=1200,
        instagram_followers_reported_at=NOW,
        size_top="M",
        size_bottom_waist=28,
    )
    response = client.get("/v1/facemarket/model/test-cuts", headers=_auth(make_token))
    assert response.status_code == 200, response.text
    profile = response.json()["profile"]
    assert profile["sponsorshipEnabled"] is False
    assert profile["instagramHandle"] == "saved.model"
    assert profile["instagramFollowers"] == 1200
    assert profile["instagramFollowersReportedAt"] == "2026-09-07T03:00:00Z"
    assert profile["sizeTop"] == "M"
    assert profile["sizeBottomWaist"] == 28


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
    monkeypatch.setattr(facemarket_admin_models, "_today", lambda: date(2026, 9, 7))
    assert mine.json()["profile"] == {
        "sponsorshipEnabled": False,
        "instagramHandle": None,
        "instagramFollowers": None,
        "instagramFollowersReportedAt": None,
        "sizeTop": None,
        "sizeBottomWaist": None,
        "sponsorshipProfileConsentAt": None,
        "displayName": "정일상",
        "gender": "male",
        "ageBand": "20대 초반",
        "heightCm": 178,
        "heightBucket": "m_175_180",
        "bodyType": "toned",
        "license": {
            "allowedUse": ["상의", "아우터"],
            "forbiddenUse": [],
            "unitPrice": 14900,
            "validUntil": LICENSE_VALID_UNTIL.isoformat().replace("+00:00", "Z"),
            "validDays": 365,
        },
    }
    assert "r2Key" not in mine.text and "private/facemarket" not in mine.text

    confirmed = client.post(
        "/v1/facemarket/model/test-cuts/confirm",
        json={"closeupCutId": CUT_ID, "fullbodyCutId": FULLBODY_CUT_ID},
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
        # 고른 확대샷 1 + 전신샷 1 만 approved. 같은 묶음의 나머지 두 장은 내려간다.
        CUT_ID: True,
        CLOSEUP_ALT_ID: False,
        FULLBODY_CUT_ID: True,
        FULLBODY_ALT_ID: False,
    }
    # 보정은 **전송 때** 정해졌다 — 확정이 다시 쓰지 않는다.
    assert store["model"].get("skin_finish_code", "prod") == "prod"
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
    assert admin_view.json()["closeupCount"] == 2     # 한 보정 묶음의 확대샷 2장
    assert admin_view.json()["fullbodyCount"] == 2     # 한 보정 묶음의 전신샷 2장
    # cutsComplete 는 **12장 전량**을 뜻한다 — 한 묶음만 심은 이 시나리오는 아직 아니다.
    assert admin_view.json()["cutsComplete"] is False
    assert admin_view.json()["finishCounts"]["prod"] == {"closeup": 2, "fullbody": 2}
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


@pytest.mark.parametrize("valid_until, valid_days", [(None, None), (LICENSE_VALID_UNTIL, 365)])
def test_public_models_returns_only_eligible_profiles_without_pii(test_cut_api, valid_until, valid_days):
    client, store, _face_r2, _public_r2, _make_token = test_cut_api
    eligible = {
        "id": MODEL_ID,
        "display_name": "정일상",
        "gender": "male",
        "height_cm": 178,
        "height_bucket": "m_175_180",
        "body_type": "toned",
        "allowed_use": ["상의", "아우터"],
        "forbidden_use": ["속옷", "수영복"],
        "unit_price": 14900,
        "license_valid_until": valid_until,
        "license_valid_days": valid_days,
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

    # 모델 리스트는 등록된 셀러와 모델만 본다(2026-09-23). 셀러 계정으로 부른다.
    response = client.get("/v1/facemarket/public/models", headers=_auth(_make_token, "seller-1"))

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert len(response.json()["items"]) == 1
    item = response.json()["items"][0]
    # 공개 후보 대역에는 생년월일이 없다 → 나이대 None. 구간 계산은 test_age_band_* 가 따로 본다.
    assert "birthdate" not in response.text
    assert item == {
        "sponsorshipEnabled": False,
        "instagramHandle": None,
        "instagramFollowers": None,
        "instagramFollowersReportedAt": None,
        "sizeTop": None,
        "sizeBottomWaist": None,
        "sponsorshipProfileConsentAt": None,
        "id": MODEL_ID,
        "displayName": "정일상",
        "gender": "male",
        "ageBand": None,
        "heightCm": 178,
        "heightBucket": "m_175_180",
        "bodyType": "toned",
        "license": {
            "allowedUse": ["상의", "아우터"],
            "forbiddenUse": [],
            "unitPrice": 14900,
            "validUntil": valid_until.isoformat().replace("+00:00", "Z") if valid_until else None,
            "validDays": valid_days,
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
    # LIKE 패턴은 파라미터로 간다 — SQL 에 % 를 박으면 psycopg 가 execute 에서 죽는다.
    public_params = next(
        params
        for query, params in store["sql_params"]
        if "m.confirmed_at is not null" in query
    )
    assert public_params == ("facemarket/catalog/models/%",) * 2
    for clause in (
        "m.status = 'verified'",
        "m.cover_image_url like %s",
        "m.fullbody_image_url like %s",
        "l.enrollment_id = m.current_enrollment_id",
        "l.forbidden_use",
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


# ── 보정 묶음 전송 (2026-09-16) ─────────────────────────────────────────────
def test_sending_a_finish_that_is_not_complete_is_blocked(test_cut_api):
    """★ 반쪽 묶음을 보내면 등록자가 확대샷이나 전신샷 중 하나를 못 골라 확정이 막힌다."""
    client, store, face_r2, _public, make_token = test_cut_api
    _seed_complete_cuts(store, face_r2, skin_finish="prod")

    response = client.post(
        f"/v1/facemarket/admin/models/{MODEL_ID}/send-test-cuts",
        json={"skinFinish": "texture"},
        headers=_auth(make_token, "admin-1"),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "test_cuts_incomplete"
    assert store["model"]["status"] == "pending"
    assert "skin_finish_code" not in store["model"], "막힌 전송이 보정을 적으면 안 된다"


def test_sending_records_the_finish_and_shows_the_model_only_those_four(test_cut_api):
    """보낸 보정이 그 사람의 설정이 되고, 등록자는 그 4장만 본다."""
    client, store, face_r2, _public, make_token = test_cut_api
    _seed_complete_cuts(store, face_r2, skin_finish="texture")
    # 다른 보정 묶음도 같이 있다 — 이게 등록자에게 새어 나가면 안 된다.
    _seed_cut(store, face_r2, cut_id="99999999-9999-9999-9999-999999999999",
              kind="closeup", skin_finish="prod")

    sent = client.post(
        f"/v1/facemarket/admin/models/{MODEL_ID}/send-test-cuts",
        json={"skinFinish": "texture"},
        headers=_auth(make_token, "admin-1"),
    )
    assert sent.status_code == 200, sent.text
    assert sent.json()["skinFinish"] == "texture"
    assert store["model"]["skin_finish_code"] == "texture"

    mine = client.get("/v1/facemarket/model/test-cuts", headers=_auth(make_token))
    assert mine.status_code == 200, mine.text
    cuts = mine.json()["cuts"]
    assert len(cuts) == 4, "보낸 묶음 4장만 보여야 한다"
    assert {cut["skinFinish"] for cut in cuts} == {"texture"}
    assert {cut["kind"] for cut in cuts} == {"closeup", "fullbody"}


def test_an_unknown_finish_is_refused_at_send(test_cut_api):
    client, store, face_r2, _public, make_token = test_cut_api
    _seed_complete_cuts(store, face_r2)

    response = client.post(
        f"/v1/facemarket/admin/models/{MODEL_ID}/send-test-cuts",
        json={"skinFinish": "100"},
        headers=_auth(make_token, "admin-1"),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_skin_finish"
    assert store["model"]["status"] == "pending"


def test_building_needs_a_trained_lora(test_cut_api):
    """학습이 안 끝났으면 만들 얼굴이 없다 — 조용히 큐에 넣으면 20분 뒤 실패로만 보인다."""
    client, store, _face, _public, make_token = test_cut_api
    store.setdefault("loras", [])

    response = client.post(
        f"/v1/facemarket/admin/models/{MODEL_ID}/test-cuts/build",
        headers=_auth(make_token, "admin-1"),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "lora_not_ready"


@pytest.mark.parametrize("previously_confirmed", [False, True])
def test_completion_email_only_on_first_confirmation(test_cut_api, monkeypatch, previously_confirmed):
    client, store, face_r2, _public_r2, make_token = test_cut_api
    _seed_complete_cuts(store, face_r2, data=_png(40, 40))
    store["model"].update(status="awaiting_confirm", confirmed_at=NOW if previously_confirmed else None)
    sent = []
    async def send(settings, **kwargs):
        assert store["model"]["status"] == "verified"
        sent.append(kwargs)
        return True, "msg-1", None
    monkeypatch.setattr(facemarket_notify, "send_registration_completed_email", send, raising=False)
    response = client.post("/v1/facemarket/model/test-cuts/confirm",
                           json={"closeupCutId": CUT_ID, "fullbodyCutId": FULLBODY_CUT_ID}, headers=_auth(make_token))
    assert response.status_code == 200, response.text
    assert len(sent) == (0 if previously_confirmed else 1)
    if sent:
        assert sent[0] == {"to": "model@example.com", "display_name": store["model"]["display_name"]}
    retry = client.post("/v1/facemarket/model/test-cuts/confirm",
                        json={"closeupCutId": CUT_ID, "fullbodyCutId": FULLBODY_CUT_ID}, headers=_auth(make_token))
    assert retry.status_code == 409
    assert len(sent) == (0 if previously_confirmed else 1)


@pytest.mark.parametrize("email_error", [True, False])
def test_completion_email_failure_never_rolls_back_confirmation(test_cut_api, monkeypatch, email_error):
    client, store, face_r2, _public_r2, make_token = test_cut_api
    _seed_complete_cuts(store, face_r2, data=_png(40, 40))
    store["model"]["status"] = "awaiting_confirm"
    async def send(settings, **kwargs):
        if email_error:
            raise RuntimeError("mail unavailable")
        return False, None, "send_error"
    monkeypatch.setattr(facemarket_notify, "send_registration_completed_email", send, raising=False)
    response = client.post("/v1/facemarket/model/test-cuts/confirm",
                           json={"closeupCutId": CUT_ID, "fullbodyCutId": FULLBODY_CUT_ID}, headers=_auth(make_token))
    assert response.status_code == 200, response.text
    assert store["model"]["status"] == "verified"


def test_failed_confirmation_sends_no_completion_email(test_cut_api, monkeypatch):
    client, store, face_r2, _public_r2, make_token = test_cut_api
    _seed_complete_cuts(store, face_r2, data=_png(40, 40))
    store["model"]["status"] = "awaiting_confirm"
    store["license_active"] = False
    sent = []
    async def send(*args, **kwargs):
        sent.append(kwargs)
    monkeypatch.setattr(facemarket_notify, "send_registration_completed_email", send, raising=False)
    response = client.post("/v1/facemarket/model/test-cuts/confirm",
                           json={"closeupCutId": CUT_ID, "fullbodyCutId": FULLBODY_CUT_ID}, headers=_auth(make_token))
    assert response.status_code == 409
    assert sent == []
