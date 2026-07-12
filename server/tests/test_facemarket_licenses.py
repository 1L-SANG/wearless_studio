"""얼굴 라이선스 라우트 테스트 (생성 멀티파트 · 목록 · 얼굴 게이트).

DB·R2를 페이크로 대체해 순수 로직만 검증:
  · 얼굴 바이트는 비공개 R2에만 저장, 응답에 face_image_key/원본 바이트 미노출
  · face_image_uri = 게이트 URL(공개 R2 URL 아님), digest = 'sha256-...'
  · 소유 스코프(다른 사용자 접근 404) · revoked/expired 접근 차단(404)
  · verified 모델 선행 필수(없으면 400)
"""

import contextlib
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import facemarket
from app.main import create_app
from conftest import make_settings

NOW = datetime(2026, 7, 9, 12, 0, 0, tzinfo=timezone.utc)
_LICENSE_KEYS = (
    "id", "model_id", "face_image_uri", "face_image_digest", "allowed_use",
    "forbidden_use", "unit_price", "license_valid_until", "status", "vc_id", "created_at",
)


class FakeR2Face:
    """app.state.r2_face 대역 — 바이트를 dict에 보관(put/get/delete)."""

    def __init__(self):
        self.objects: dict[str, tuple[bytes, str]] = {}

    def put_bytes(self, key, data, mime, cache=None):
        self.objects[key] = (data, mime)

    def get_bytes(self, key):
        if key not in self.objects:
            raise KeyError(key)
        return self.objects[key][0]

    def delete(self, key):
        self.objects.pop(key, None)


class FakeCursor:
    def __init__(self, store):
        self.store = store
        self._result = None
        self._many = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        s = " ".join(sql.split()).lower()
        params = params or ()
        models = self.store["models"]
        licenses = self.store["licenses"]

        if s.startswith("select id from fm_models where user_id"):
            # verified 모델 조회
            m = next(
                (r for r in models if r["user_id"] == params[0] and r["status"] == "verified"),
                None,
            )
            self._result = {"id": m["id"]} if m else None
        elif s.startswith("insert into fm_licenses"):
            (lid, model_id, gate_uri, key, digest,
             allowed, forbidden, unit_price, valid_until) = params
            row = {
                "id": lid, "model_id": model_id, "face_image_uri": gate_uri,
                "face_image_key": key, "face_image_digest": digest,
                "allowed_use": list(allowed), "forbidden_use": list(forbidden),
                "unit_price": unit_price, "license_valid_until": valid_until,
                "status": "active", "vc_id": None, "created_at": NOW,
            }
            licenses.append(row)
            self._result = {k: row[k] for k in _LICENSE_KEYS}
        elif s.startswith("select l.id::text as id, l.model_id::text as model_id"):
            # 목록: 소유 모델 경유
            owned = {m["id"] for m in models if m["user_id"] == params[0]}
            rows = [r for r in licenses if r["model_id"] in owned]
            self._many = [{k: r[k] for k in _LICENSE_KEYS} for r in rows]
        elif s.startswith("select l.face_image_key, l.status"):
            # 게이트: license id + 소유자 조인
            lid, uid = params
            owned = {m["id"] for m in models if m["user_id"] == uid}
            r = next((x for x in licenses if x["id"] == lid and x["model_id"] in owned), None)
            self._result = (
                {"face_image_key": r["face_image_key"], "status": r["status"],
                 "license_valid_until": r["license_valid_until"]}
                if r else None
            )
        else:  # pragma: no cover
            raise AssertionError(f"unexpected SQL: {s}")

    async def fetchone(self):
        return self._result

    async def fetchall(self):
        return self._many or []


class FakeConn:
    def __init__(self, store):
        self.store = store

    def cursor(self):
        return FakeCursor(self.store)

    async def commit(self):
        return None


@pytest.fixture()
def fm(keypair, monkeypatch):
    _priv, public_key = keypair
    app = create_app(make_settings(facemarket_enabled=True, fm_ci_pepper="pep"))
    app.state.jwt_key_resolver = lambda token: public_key
    fake_r2 = FakeR2Face()
    app.state.r2_face = fake_r2

    # user-1 소유의 verified 모델 1개 시드
    store = {
        "models": [{"id": "model-1", "user_id": "user-1", "status": "verified"}],
        "licenses": [],
    }

    @contextlib.asynccontextmanager
    async def fake_get_conn(_request):
        yield FakeConn(store)

    monkeypatch.setattr(facemarket, "get_conn", fake_get_conn)
    return TestClient(app), store, fake_r2


def _auth(make_token, sub="user-1"):
    return {"Authorization": f"Bearer {make_token(sub=sub)}"}


def _png():
    return ("face.png", b"\x89PNG\r\n\x1a\nFAKEBYTES", "image/png")


def test_create_license_stores_face_private_and_returns_gate_url(fm, make_token):
    client, store, r2 = fm
    r = client.post(
        "/v1/facemarket/licenses",
        files={"face": _png()},
        data={"allowed_use": ["광고", "상세페이지"], "forbidden_use": ["성인"],
              "unit_price": "5000", "valid_days": "30"},
        headers=_auth(make_token),
    )
    assert r.status_code == 201, r.text
    card = r.json()
    # 게이트 URL만 노출, 내부 키/원본 바이트 미노출
    assert card["faceImageUri"] == f"/v1/facemarket/licenses/{card['id']}/face"
    assert "faceImageKey" not in card and "face_image_key" not in r.text
    assert card["faceImageDigest"].startswith("sha256-")
    assert card["allowedUse"] == ["광고", "상세페이지"]
    assert card["forbiddenUse"] == ["성인"]
    assert card["unitPrice"] == 5000
    assert card["status"] == "active"
    # 얼굴 바이트는 비공개 R2에 저장됨(응답 아님)
    assert len(r2.objects) == 1
    key = next(iter(r2.objects))
    assert key.startswith("facemarket/models/model-1/licenses/") and key.endswith(".png")
    assert len(store["licenses"]) == 1


def test_create_license_requires_verified_model(fm, make_token):
    client, _, _ = fm
    # user-2 는 verified 모델 없음
    r = client.post(
        "/v1/facemarket/licenses",
        files={"face": _png()},
        data={"unit_price": "1000"},
        headers=_auth(make_token, sub="user-2"),
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "no_verified_model"


def test_create_license_rejects_non_image(fm, make_token):
    client, _, r2 = fm
    r = client.post(
        "/v1/facemarket/licenses",
        files={"face": ("x.pdf", b"%PDF-1.4", "application/pdf")},
        headers=_auth(make_token),
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_image"
    assert len(r2.objects) == 0  # 저장 안 됨


def test_create_license_requires_auth(fm):
    client, _, _ = fm
    r = client.post("/v1/facemarket/licenses", files={"face": _png()})
    assert r.status_code == 401


def test_list_licenses_scoped_to_owner(fm, make_token):
    client, _, _ = fm
    client.post("/v1/facemarket/licenses", files={"face": _png()},
                data={"unit_price": "1000"}, headers=_auth(make_token))
    mine = client.get("/v1/facemarket/licenses", headers=_auth(make_token))
    assert mine.status_code == 200
    assert len(mine.json()) == 1
    # 다른 사용자는 못 본다
    other = client.get("/v1/facemarket/licenses", headers=_auth(make_token, sub="user-2"))
    assert other.status_code == 200 and other.json() == []


def test_face_gate_owner_gets_bytes_others_404(fm, make_token):
    client, _, _ = fm
    created = client.post("/v1/facemarket/licenses", files={"face": _png()},
                          data={"unit_price": "1000"}, headers=_auth(make_token)).json()
    lid = created["id"]
    # 소유자 = 바이트 200
    ok = client.get(f"/v1/facemarket/licenses/{lid}/face", headers=_auth(make_token))
    assert ok.status_code == 200
    assert ok.headers["content-type"].startswith("image/")
    assert ok.headers["cache-control"] == "no-store, private"
    assert ok.content == b"\x89PNG\r\n\x1a\nFAKEBYTES"
    # 타인 = 404(존재 노출 방지)
    other = client.get(f"/v1/facemarket/licenses/{lid}/face", headers=_auth(make_token, sub="user-2"))
    assert other.status_code == 404


def test_face_gate_blocks_revoked_and_expired(fm, make_token):
    client, store, _ = fm
    created = client.post("/v1/facemarket/licenses", files={"face": _png()},
                          data={"unit_price": "1000"}, headers=_auth(make_token)).json()
    lid = created["id"]
    lic = store["licenses"][0]
    # revoked → 404
    lic["status"] = "revoked"
    assert client.get(f"/v1/facemarket/licenses/{lid}/face", headers=_auth(make_token)).status_code == 404
    # active 로 되돌리고 만료시키면 → 404
    lic["status"] = "active"
    lic["license_valid_until"] = datetime(2020, 1, 1, tzinfo=timezone.utc)
    assert client.get(f"/v1/facemarket/licenses/{lid}/face", headers=_auth(make_token)).status_code == 404


def test_face_gate_missing_license_404(fm, make_token):
    client, _, _ = fm
    r = client.get("/v1/facemarket/licenses/00000000-0000-0000-0000-000000000000/face",
                   headers=_auth(make_token))
    assert r.status_code == 404


def test_storage_unavailable_503(keypair, monkeypatch, make_token):
    """r2_face 미설정이면 라이선스 생성이 503(공개 버킷 폴백 금지)."""
    _priv, public_key = keypair
    app = create_app(make_settings(facemarket_enabled=True, fm_ci_pepper="pep"))
    app.state.jwt_key_resolver = lambda token: public_key
    app.state.r2_face = None  # 저장소 없음

    store = {"models": [{"id": "model-1", "user_id": "user-1", "status": "verified"}], "licenses": []}

    @contextlib.asynccontextmanager
    async def fake_get_conn(_request):
        yield FakeConn(store)

    monkeypatch.setattr(facemarket, "get_conn", fake_get_conn)
    client = TestClient(app)
    r = client.post("/v1/facemarket/licenses", files={"face": _png()},
                    data={"unit_price": "1000"}, headers={"Authorization": f"Bearer {make_token(sub='user-1')}"})
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "storage_unavailable"
