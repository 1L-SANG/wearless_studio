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
             allowed, forbidden, unit_price, valid_until, profile_id) = params
            row = {
                "id": lid, "model_id": model_id, "face_image_uri": gate_uri,
                "face_image_key": key, "face_image_digest": digest,
                "allowed_use": list(allowed), "forbidden_use": list(forbidden),
                "unit_price": unit_price, "license_valid_until": valid_until,
                "status": "active", "vc_id": None, "created_at": NOW,
                "profile_id": profile_id,  # 개인화 프로필 참조(레거시 face 경로는 None)
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
        elif s.startswith("select id::text as id, status from personalization_profiles"):
            # 개인화 프로필 — 소유자 스코프 + purged 제외를 SQL 이 하므로 페이크도 동일하게
            pid, uid = params
            p = next(
                (x for x in self.store["profiles"]
                 if x["id"] == pid and x["user_id"] == uid and x["status"] != "purged"),
                None,
            )
            self._result = {"id": p["id"], "status": p["status"]} if p else None
        elif s.startswith("select r2_key, image_digest from personalization_face_photos"):
            pid = params[0]
            ph = next(
                (x for x in self.store["face_photos"]
                 if x["profile_id"] == pid and x["angle"] == "front"),
                None,
            )
            self._result = (
                {"r2_key": ph["r2_key"], "image_digest": ph["image_digest"]} if ph else None
            )
        elif s.startswith("select l.status, l.allowed_use"):
            # 공개 검증(QR) — 라이선스 + 모델 표시명 + 최신 본인확인의 birthYear
            lid = params[0]
            lic = next((x for x in licenses if x["id"] == lid), None)
            if lic is None:
                self._result = None
            else:
                m = next((x for x in models if x["id"] == lic["model_id"]), None)
                ident = next(
                    (i for i in self.store["identities"] if i["model_id"] == lic["model_id"]), None
                )
                self._result = {
                    "status": lic["status"], "allowed_use": lic["allowed_use"],
                    "forbidden_use": lic["forbidden_use"], "unit_price": lic["unit_price"],
                    "license_valid_until": lic["license_valid_until"], "vc_id": lic["vc_id"],
                    "display_name": (m or {}).get("display_name") or "",
                    "birth_year": (ident or {}).get("birth_year"),
                }
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
        "models": [
            {"id": "model-1", "user_id": "user-1", "status": "verified", "display_name": "홍*동"}
        ],
        "licenses": [],
        "profiles": [],     # 개인화 프로필 {id, user_id, status}
        "face_photos": [],  # 개인화 얼굴 슬롯 {profile_id, angle, r2_key, image_digest}
        "identities": [],   # fm_identity_verifications {model_id, birth_year}
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


# ── step02: 개인화 프로필 기반 발급 ────────────────────────────
PROFILE_ID = "11111111-1111-1111-1111-111111111111"
FRONT_KEY = f"personalization/profiles/{PROFILE_ID}/faces/front.png"
FRONT_BYTES = b"\x89PNG\r\n\x1a\nPROFILEFRONT"
FRONT_DIGEST = "sha256-frontdigestvalue"


def _seed_profile(store, r2, *, status="ready", user_id="user-1", with_front=True):
    store["profiles"].append({"id": PROFILE_ID, "user_id": user_id, "status": status})
    if with_front:
        store["face_photos"].append(
            {"profile_id": PROFILE_ID, "angle": "front",
             "r2_key": FRONT_KEY, "image_digest": FRONT_DIGEST}
        )
        r2.objects[FRONT_KEY] = (FRONT_BYTES, "image/png")


def test_create_license_from_profile_references_front_slot(fm, make_token):
    """profile_id 발급 = 프로필 front 슬롯을 **참조**(사본 금지 — 파기 캐스케이드 보존)."""
    client, store, r2 = fm
    _seed_profile(store, r2)
    r = client.post(
        "/v1/facemarket/licenses",
        data={"profile_id": PROFILE_ID, "allowed_use": ["광고"], "unit_price": "7000"},
        headers=_auth(make_token),
    )
    assert r.status_code == 201, r.text
    card = r.json()
    assert card["faceImageUri"] == f"/v1/facemarket/licenses/{card['id']}/face"
    assert card["faceImageDigest"] == FRONT_DIGEST  # 프로필 digest 그대로
    assert card["unitPrice"] == 7000
    lic = store["licenses"][0]
    assert lic["profile_id"] == PROFILE_ID          # 프로필 참조 기록
    assert lic["face_image_key"] == FRONT_KEY       # 사본 아님 = 개인화 키 그대로
    assert len(r2.objects) == 1                     # 새 업로드 0(프로필 얼굴 재사용)


def test_profile_license_face_gate_streams_profile_bytes(fm, make_token):
    """프로필 참조 라이선스도 기존 얼굴 게이트로 소유자에게만 스트림된다."""
    client, store, r2 = fm
    _seed_profile(store, r2)
    card = client.post("/v1/facemarket/licenses", data={"profile_id": PROFILE_ID},
                       headers=_auth(make_token)).json()
    ok = client.get(f"/v1/facemarket/licenses/{card['id']}/face", headers=_auth(make_token))
    assert ok.status_code == 200
    assert ok.content == FRONT_BYTES
    assert ok.headers["cache-control"] == "no-store, private"
    # 타인은 여전히 404
    other = client.get(f"/v1/facemarket/licenses/{card['id']}/face",
                       headers=_auth(make_token, sub="user-2"))
    assert other.status_code == 404


def test_create_license_rejects_not_ready_profile(fm, make_token):
    client, store, r2 = fm
    _seed_profile(store, r2, status="draft")  # 온보딩 미완(3각도·동의·신체 중 결손)
    r = client.post("/v1/facemarket/licenses", data={"profile_id": PROFILE_ID},
                    headers=_auth(make_token))
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "profile_not_ready"
    assert store["licenses"] == []


def test_create_license_rejects_foreign_profile(fm, make_token):
    """타인 프로필은 '없는 프로필'과 같은 코드 — 존재 여부가 새지 않는다."""
    client, store, r2 = fm
    _seed_profile(store, r2, user_id="user-2")
    r = client.post("/v1/facemarket/licenses", data={"profile_id": PROFILE_ID},
                    headers=_auth(make_token))
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_profile"
    missing = client.post(
        "/v1/facemarket/licenses",
        data={"profile_id": "22222222-2222-2222-2222-222222222222"},
        headers=_auth(make_token),
    )
    assert missing.json()["error"]["code"] == "invalid_profile"  # 동일 코드
    assert store["licenses"] == []


def test_create_license_rejects_purged_profile(fm, make_token):
    client, store, r2 = fm
    _seed_profile(store, r2, status="purged")
    r = client.post("/v1/facemarket/licenses", data={"profile_id": PROFILE_ID},
                    headers=_auth(make_token))
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_profile"


def test_create_license_rejects_malformed_profile_id(fm, make_token):
    """비-uuid 는 500 아닌 400."""
    client, _, _ = fm
    r = client.post("/v1/facemarket/licenses", data={"profile_id": "not-a-uuid"},
                    headers=_auth(make_token))
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_profile"


def test_create_license_requires_face_or_profile(fm, make_token):
    client, _, r2 = fm
    r = client.post("/v1/facemarket/licenses", data={"unit_price": "1000"},
                    headers=_auth(make_token))
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "face_or_profile_required"
    assert len(r2.objects) == 0


def test_create_license_rejects_face_and_profile_together(fm, make_token):
    """둘 다 오면 명시적 거절 — 어느 얼굴을 라이선스했는지 모호해지면 안 된다."""
    client, store, r2 = fm
    _seed_profile(store, r2)
    r = client.post("/v1/facemarket/licenses", files={"face": _png()},
                    data={"profile_id": PROFILE_ID}, headers=_auth(make_token))
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "face_and_profile_conflict"
    assert store["licenses"] == []
    assert len(r2.objects) == 1  # 프로필 얼굴만 — 업로드분 저장 0


def test_legacy_face_license_records_null_profile(fm, make_token):
    """레거시 face 1장 경로는 profile_id = None 으로 그대로 동작(회귀 0)."""
    client, store, _ = fm
    r = client.post("/v1/facemarket/licenses", files={"face": _png()},
                    data={"unit_price": "1000"}, headers=_auth(make_token))
    assert r.status_code == 201
    assert store["licenses"][0]["profile_id"] is None


# ── step02: 공개 검증(QR — 무인증) ─────────────────────────────
_PUBLIC_KEYS = {
    "valid", "status", "allowedUse", "forbiddenUse", "unitPrice", "validUntil", "vcId", "model",
}


def _make_license(client, make_token, **data):
    return client.post("/v1/facemarket/licenses", files={"face": _png()},
                       data={"unit_price": "5000", **data}, headers=_auth(make_token)).json()


def test_public_verify_exposes_only_whitelist_no_pii(fm, make_token):
    """🔴 하드룰 — 무인증 라우트에 얼굴·신원·내부키가 한 톨도 실리면 안 된다(영구 유출)."""
    client, store, _ = fm
    store["identities"].append({"model_id": "model-1", "birth_year": "1996"})
    card = _make_license(client, make_token, allowed_use="광고", forbidden_use="성인")
    r = client.get(f"/v1/facemarket/verify/{card['id']}")  # Authorization 헤더 없음
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == _PUBLIC_KEYS
    assert set(body["model"]) == {"nameMasked", "age"}
    assert body["valid"] is True and body["status"] == "active"
    assert body["allowedUse"] == ["광고"] and body["forbiddenUse"] == ["성인"]
    assert body["unitPrice"] == 5000
    assert body["model"]["nameMasked"] == "홍*동"
    assert body["model"]["age"] == datetime.now(timezone.utc).year - 1996 - 1  # 보수적 하한
    # 유출 금지 값이 응답 본문 어디에도 등장하지 않는지 원문으로 확인
    raw = r.text
    for leaked in (
        card["faceImageDigest"],          # 얼굴 digest(생체 파생 고정 식별자)
        "sha256-", "face_image", "faceImage", "faceImageKey",
        "facemarket/models",              # 내부 R2 키스페이스
        "model-1", "user-1",              # model_id · user_id
        "ci_hash", "birthYear", "1996",   # CI 해시 · 생년(원문)
    ):
        assert leaked not in raw, f"공개 검증 응답에 {leaked!r} 유출"
    assert r.headers["cache-control"] == "no-store"


def test_public_verify_requires_no_auth_and_hides_nothing_else(fm, make_token):
    """무인증 도달 확인 — 인증 헤더 유무와 무관하게 같은 응답."""
    client, _, _ = fm
    card = _make_license(client, make_token)
    anon = client.get(f"/v1/facemarket/verify/{card['id']}")
    authed = client.get(f"/v1/facemarket/verify/{card['id']}", headers=_auth(make_token))
    assert anon.status_code == authed.status_code == 200
    assert anon.json() == authed.json()
    # 타인 토큰으로도 동일(공개 라우트)
    other = client.get(f"/v1/facemarket/verify/{card['id']}", headers=_auth(make_token, sub="user-2"))
    assert other.status_code == 200


def test_public_verify_revoked_is_invalid(fm, make_token):
    client, store, _ = fm
    card = _make_license(client, make_token)
    store["licenses"][0]["status"] = "revoked"
    body = client.get(f"/v1/facemarket/verify/{card['id']}").json()
    assert body["valid"] is False and body["status"] == "revoked"


def test_public_verify_expired_is_invalid(fm, make_token):
    """DB status='active' 라도 기간이 지났으면 status='expired' + valid=false(실시간 판정)."""
    client, store, _ = fm
    card = _make_license(client, make_token)
    store["licenses"][0]["license_valid_until"] = datetime(2020, 1, 1, tzinfo=timezone.utc)
    body = client.get(f"/v1/facemarket/verify/{card['id']}").json()
    assert body["valid"] is False and body["status"] == "expired"


def test_public_verify_age_null_when_birth_year_unusable(fm, make_token):
    """birthYear 없음/파싱 불가 → age null(성인 오통과 방지 — 안전측)."""
    client, store, _ = fm
    card = _make_license(client, make_token)
    body = client.get(f"/v1/facemarket/verify/{card['id']}").json()
    assert body["model"]["age"] is None  # identities 미시드
    store["identities"].append({"model_id": "model-1", "birth_year": "0101"})  # MMDD 오염
    body2 = client.get(f"/v1/facemarket/verify/{card['id']}").json()
    assert body2["model"]["age"] is None  # 연도 범위 밖 → null(1900+ 세 오표기 금지)


def test_public_verify_unknown_and_malformed_404(fm):
    client, _, _ = fm
    assert client.get(
        "/v1/facemarket/verify/00000000-0000-0000-0000-000000000000"
    ).status_code == 404
    assert client.get("/v1/facemarket/verify/not-a-uuid").status_code == 404


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
