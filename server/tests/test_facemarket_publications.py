"""층②·① 배포본 공증 라우트.

핵심 계약 3개:
  1. uploadToken 없이는 임의 R2 키를 서명 대상으로 못 민다.
  2. 같은 (셀러, 해시) 는 몇 번 sign 해도 원장 1행이고 publicationId 가 같다.
  3. C2PA 서명 실패는 다운로드를 막지 않는다(원본 반환 + c2paStatus='failed').

Fix round 1(리뷰 I5) 부터는 라우트 자체(presign/sign)도 페이크 DB·R2 로 실제로 태운다 —
test_facemarket_licenses.py 의 FakeCursor/FakeConn 패턴(SQL 텍스트 접두어로 분기하는 인메모리
스토어)을 이 모듈의 SQL 집합에 맞춰 축소판으로 따라간다.
"""
import contextlib
import hashlib
import time
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import facemarket_provenance as fp
from app.main import create_app
from conftest import auth_headers, make_settings


SECRET = "test-secret"


def token(**over):
    kw = dict(
        seller_id="u1", key="publications/u1/abc/upload",
        project_id="p1", kind="long_png", expires_at=time.time() + 300,
    )
    kw.update(over)
    return fp.make_upload_token(SECRET, **kw)


def test_upload_token_roundtrip():
    parsed = fp.parse_upload_token(SECRET, token())
    assert parsed["seller_id"] == "u1"
    assert parsed["key"] == "publications/u1/abc/upload"
    assert parsed["project_id"] == "p1"
    assert parsed["kind"] == "long_png"


def test_upload_token_rejects_tamper():
    # 원 브리핑의 `.replace("u1", "u2", 1)` 은 base64 인코딩된 페이로드 안에서 리터럴 "u1"
    # 부분 문자열이 살아남는다고 가정했는데, 실측(2000회 샘플, 0건 일치)해 보니 이 특정
    # 페이로드 구조에서는 "u1" 이 인코딩 결과에 전혀 나타나지 않아 replace 가 항상 no-op
    # 이었다 — 변조되지 않은 유효한 토큰을 그대로 다시 검증하는 셈이라 통과 중인
    # 구현에서도 예외가 안 난다. mac 마지막 문자를 직접 뒤집어 실제로 서명을 깨뜨린다.
    body, mac = token().split(".", 1)
    flipped = "0" if mac[-1] != "0" else "1"
    tampered = f"{body}.{mac[:-1]}{flipped}"
    with pytest.raises(fp.TokenInvalid):
        fp.parse_upload_token(SECRET, tampered)


def test_upload_token_rejects_expired():
    with pytest.raises(fp.TokenInvalid):
        fp.parse_upload_token(SECRET, token(expires_at=time.time() - 1))


def test_upload_token_rejects_foreign_secret():
    with pytest.raises(fp.TokenInvalid):
        fp.parse_upload_token("other-secret", token())


class FakeSigner:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = 0

    def sign(self, data, mime, manifest):
        self.calls += 1
        if self.fail:
            raise RuntimeError("signing blew up")
        return data + b"-SIGNED"


def test_sign_bytes_returns_original_on_failure():
    """서명 실패가 셀러의 결과물을 인질로 잡지 않는다."""
    data = b"png-bytes"
    out, status = fp.sign_bytes(FakeSigner(fail=True), data, "image/png", {})
    assert out == data
    assert status == "failed"


def test_sign_bytes_returns_signed_on_success():
    data = b"png-bytes"
    out, status = fp.sign_bytes(FakeSigner(), data, "image/png", {})
    assert out == data + b"-SIGNED"
    assert status == "signed"


def test_sign_bytes_skips_when_signer_missing():
    data = b"png-bytes"
    out, status = fp.sign_bytes(None, data, "image/png", {})
    assert out == data
    assert status == "skipped"


def test_routes_absent_when_flag_off(make_token):
    app = create_app(make_settings(facemarket_enabled=True, fm_provenance_enabled=False))
    with TestClient(app) as client:
        r = client.post(
            "/v1/facemarket/publications/presign",
            json={"projectId": "p1", "kind": "long_png", "byteSize": 10},
            headers=auth_headers(make_token),
        )
    assert r.status_code == 404


# ============================================================================
# 라우트 레벨(리뷰 I5) — presign/sign 을 페이크 DB·R2 로 실제로 태운다.
#
# FakeCursor 는 이 모듈이 실제로 던지는 SQL 문 5개(usage 조회 · resolve_model_license ·
# INSERT ... ON CONFLICT · 충돌 시 fallback SELECT · UPDATE 2종 · anchor INSERT)만 알면
# 된다 — test_facemarket_licenses.py 의 범용 엔진과 달리 이 라우트 하나의 좁은 SQL 집합만
# 다루므로 인메모리 리스트 조인 대신 딕셔너리 store 로 충분하다.
# ============================================================================

SELLER = "seller-1"
OTHER_SELLER = "seller-2"
PROJECT_ID = "proj-1"
MODEL_ID = "11111111-1111-1111-1111-111111111111"
LICENSE_ID = "22222222-2222-2222-2222-222222222222"
ENROLLMENT_ID = "33333333-3333-3333-3333-333333333333"
ROUTE_SECRET = "route-test-secret"


def _evidence_row(model_id=MODEL_ID, **overrides):
    """resolve_model_license() 의 실제 SELECT 가 돌려주는 것과 같은 모양의 dict.

    verify_license_local 이 요구하는 필드를 전부 채운 '통과' 기본값이다 — 개별 테스트는
    overrides 로 하나만 깨서 특정 게이트를 노린다.
    """
    row = {
        "id": LICENSE_ID, "model_id": model_id, "model_name": "홍*동",
        "status": "active", "license_valid_until": None, "unit_price": 1000,
        "vc_id": "vc-1", "allowed_use": ["상의"], "forbidden_use": [],
        "model_status": "verified", "assets_status": "ready",
        "gender": "female", "height_bucket": "m", "body_type": "standard",
        "current_enrollment_id": ENROLLMENT_ID, "license_enrollment_id": ENROLLMENT_ID,
        "enrollment_status": "passed", "match_policy_version": "v1",
        "has_face_front": True, "has_grid_sedcard": True,
        "assets_current_evidence": True,
    }
    row.update(overrides)
    return row


def _new_store() -> dict:
    return {
        "jobs": {},               # job_id -> project_id
        "output_records": [],     # [{job_id, seller_id, model_id, license_ref, asset_id, created_at}]
        "evidence": {},           # (model_id, license_id) -> _evidence_row() 또는 None
        "publications": {},       # publication_id -> row dict
        "publications_by_hash": {},  # (seller_id, sha) -> publication_id
        "anchor_jobs": [],        # [publication_id, ...] (중복 없음 — on conflict do nothing 흉내)
    }


def _seed_output_record(
    store, *, seller_id=SELLER, project_id=PROJECT_ID, model_id=MODEL_ID,
    license_ref=LICENSE_ID, asset_id=None, job_id=None, created_at=None,
):
    job_id = job_id or str(uuid.uuid4())
    store["jobs"][job_id] = project_id
    store["output_records"].append({
        "job_id": job_id, "seller_id": seller_id, "model_id": model_id,
        "license_ref": license_ref, "asset_id": asset_id or str(uuid.uuid4()),
        "created_at": created_at or datetime.now(timezone.utc),
    })


def _matching_usage(store, project_id, seller_id):
    project_of = store["jobs"]
    candidates = [
        r for r in store["output_records"]
        if r["seller_id"] == seller_id and project_of.get(r["job_id"]) == project_id
    ]
    if not candidates:
        return None
    groups: dict = {}
    for r in candidates:
        key = (r["license_ref"], r["model_id"])
        g = groups.setdefault(key, {"max_created": r["created_at"], "asset_ids": []})
        g["max_created"] = max(g["max_created"], r["created_at"])
        if r.get("asset_id"):
            g["asset_ids"].append(r["asset_id"])
    best_key = max(groups, key=lambda k: groups[k]["max_created"])
    license_ref, model_id = best_key
    return {
        "license_ref": license_ref, "model_id": model_id,
        "asset_ids": groups[best_key]["asset_ids"] or None,
    }


_PUB_COLS = ("id", "c2pa_status", "chain_status", "r2_key", "signed_sha256")


class FakeCursor:
    def __init__(self, store):
        self.store = store
        self._result = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        s = " ".join(sql.split()).lower()
        params = params or ()
        store = self.store
        self._result = None

        if s.startswith("select r.license_ref::text as license_ref"):
            project_id, seller_id = params
            self._result = _matching_usage(store, project_id, seller_id)
        elif s.startswith("select l.id::text as id, m.id::text as model_id"):
            model_id = params[0]
            license_id = params[1] if len(params) > 1 else None
            self._result = store["evidence"].get((model_id, license_id))
        elif s.startswith("insert into fm_publication_records"):
            (project_id, seller_id, license_id, license_ref, model_id, kind,
             sha, size, asset_ids) = params
            hkey = (seller_id, sha)
            if hkey in store["publications_by_hash"]:
                self._result = None   # on conflict do nothing
            else:
                pub_id = str(uuid.uuid4())
                row = {
                    "id": pub_id, "seller_id": seller_id, "image_sha256": sha,
                    "kind": kind, "c2pa_status": "skipped", "chain_status": "pending",
                    "r2_key": None, "signed_sha256": None,
                    "source_asset_ids": list(asset_ids or []),
                }
                store["publications"][pub_id] = row
                store["publications_by_hash"][hkey] = pub_id
                self._result = {k: row[k] for k in _PUB_COLS}
        elif s.startswith("select id::text as id, c2pa_status, chain_status, r2_key, signed_sha256"):
            seller_id, sha = params
            pub_id = store["publications_by_hash"].get((seller_id, sha))
            row = store["publications"].get(pub_id) if pub_id else None
            self._result = {k: row[k] for k in _PUB_COLS} if row else None
        elif s.startswith("update fm_publication_records") and "c2pa_manifest" in s:
            r2_key, signed_sha256, c2pa_status, _manifest, pub_id = params
            store["publications"][pub_id].update(
                r2_key=r2_key, signed_sha256=signed_sha256, c2pa_status=c2pa_status,
            )
        elif s.startswith("update fm_publication_records") and "'skipped'" in s:
            r2_key, signed_sha256, pub_id = params
            store["publications"][pub_id].update(
                r2_key=r2_key, signed_sha256=signed_sha256, c2pa_status="skipped",
            )
        elif s.startswith("insert into fm_publication_anchor_jobs"):
            (pub_id,) = params
            if pub_id not in store["anchor_jobs"]:
                store["anchor_jobs"].append(pub_id)
        else:
            raise AssertionError(f"FakeCursor 가 모르는 SQL: {s[:150]!r}")
        return self

    async def fetchone(self):
        return self._result


class FakeConn:
    def __init__(self, store):
        self.store = store

    def cursor(self):
        return FakeCursor(self.store)

    async def commit(self):
        return None

    async def rollback(self):
        return None


class FakeRouteR2:
    """R2 이중 — 업로드 바이트·서명본 저장·HEAD 크기 응답을 흉내낸다."""

    def __init__(self, objects=None):
        self.objects = dict(objects or {})
        self.size_override: dict = {}
        self.put_calls: list = []
        self.deleted: list = []
        self.head_calls: list = []

    def presigned_put(self, key, mime, expires):
        return f"https://r2.test/put/{key}"

    def head(self, key):
        self.head_calls.append(key)
        if key in self.size_override:
            return {"size": self.size_override[key], "mime": "application/octet-stream"}
        data = self.objects.get(key)
        if data is None:
            return None
        return {"size": len(data), "mime": "application/octet-stream"}

    def get_bytes(self, key):
        if key not in self.objects:
            raise RuntimeError(f"missing key: {key}")
        return self.objects[key]

    def put_bytes(self, key, data, mime, cache=None):
        self.put_calls.append((key, data, mime))
        self.objects[key] = data

    def delete(self, key):
        self.deleted.append(key)
        self.objects.pop(key, None)

    def preview_url(self, key, expires=3600):
        return f"https://r2.test/get/{key}"


@pytest.fixture()
def prov(keypair, monkeypatch):
    _priv, public_key = keypair
    app = create_app(make_settings(
        facemarket_enabled=True, fm_provenance_enabled=True,
        fm_provenance_token_secret=ROUTE_SECRET,
        public_web_origin="https://wearless.kr",
    ))
    app.state.jwt_key_resolver = lambda _token: public_key
    r2 = FakeRouteR2()
    app.state.r2 = r2
    app.state.fm_c2pa_signer = None
    store = _new_store()

    @contextlib.asynccontextmanager
    async def fake_get_conn(_request):
        yield FakeConn(store)

    monkeypatch.setattr(fp, "get_conn", fake_get_conn)
    return TestClient(app), store, r2


def _auth(make_token, sub=SELLER):
    return {"Authorization": f"Bearer {make_token(sub=sub)}"}


def _presign(client, make_token, *, sub=SELLER, project_id=PROJECT_ID,
             kind="long_png", byte_size=100):
    return client.post(
        "/v1/facemarket/publications/presign",
        json={"projectId": project_id, "kind": kind, "byteSize": byte_size},
        headers=_auth(make_token, sub),
    )


def _sign(client, make_token, upload_token, *, sub=SELLER):
    return client.post(
        "/v1/facemarket/publications/sign",
        json={"uploadToken": upload_token},
        headers=_auth(make_token, sub),
    )


def test_route_presign_happy_path(prov, make_token):
    client, store, _r2 = prov
    _seed_output_record(store)
    store["evidence"][(MODEL_ID, LICENSE_ID)] = _evidence_row()

    r = _presign(client, make_token)
    assert r.status_code == 200, r.text
    body = r.json()
    parsed = fp.parse_upload_token(ROUTE_SECRET, body["uploadToken"])
    assert parsed["seller_id"] == SELLER
    assert parsed["project_id"] == PROJECT_ID
    assert parsed["kind"] == "long_png"


def test_route_presign_rejected_when_license_gate_fails(prov, make_token):
    client, store, _r2 = prov
    _seed_output_record(store)
    store["evidence"][(MODEL_ID, LICENSE_ID)] = _evidence_row(status="revoked")

    r = _presign(client, make_token)
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "license_revoked"


def test_route_sign_rejects_cross_seller_token(prov, make_token):
    """구조적으로 유효한 토큰(A 셀러 발급)을 B 셀러가 들이밀면 거부된다."""
    client, store, r2 = prov
    _seed_output_record(store, seller_id=SELLER)
    store["evidence"][(MODEL_ID, LICENSE_ID)] = _evidence_row()

    p = _presign(client, make_token, sub=SELLER)
    assert p.status_code == 200, p.text
    upload_token = p.json()["uploadToken"]
    key = fp.parse_upload_token(ROUTE_SECRET, upload_token)["key"]
    r2.objects[key] = b"\x89PNG-bytes"

    r = _sign(client, make_token, upload_token, sub=OTHER_SELLER)
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "invalid_token"
    assert store["publications"] == {}, "다른 셀러 토큰으로는 원장 행이 생기면 안 된다"


def test_route_sign_happy_path_writes_ledger_and_one_anchor_job(prov, make_token):
    client, store, r2 = prov
    _seed_output_record(store)
    store["evidence"][(MODEL_ID, LICENSE_ID)] = _evidence_row()

    p = _presign(client, make_token)
    upload_token = p.json()["uploadToken"]
    key = fp.parse_upload_token(ROUTE_SECRET, upload_token)["key"]
    raw = b"\x89PNG-original-bytes"
    r2.objects[key] = raw

    r = _sign(client, make_token, upload_token)
    assert r.status_code == 200, r.text
    body = r.json()
    pub_id = body["publicationId"]
    assert pub_id in store["publications"]
    assert body["c2paStatus"] == "skipped"   # fm_c2pa_signer is None in this fixture
    assert store["anchor_jobs"] == [pub_id]
    # I4 — source_asset_ids 가 실제로 실려야 한다.
    assert store["publications"][pub_id]["source_asset_ids"], \
        "source_asset_ids 가 원장에 안 실렸다(리뷰 I4 회귀)"
    assert r2.deleted == [key]   # 임시 업로드본 정리


def test_route_sign_idempotent_replay_returns_same_id_without_resigning(prov, make_token):
    client, store, r2 = prov
    _seed_output_record(store)
    store["evidence"][(MODEL_ID, LICENSE_ID)] = _evidence_row()
    signer = FakeSigner()
    client.app.state.fm_c2pa_signer = signer

    raw = b"\x89PNG-identical-bytes"

    p1 = _presign(client, make_token)
    token1 = p1.json()["uploadToken"]
    key1 = fp.parse_upload_token(ROUTE_SECRET, token1)["key"]
    r2.objects[key1] = raw
    r1 = _sign(client, make_token, token1)
    assert r1.status_code == 200, r1.text
    assert signer.calls == 1

    p2 = _presign(client, make_token)
    token2 = p2.json()["uploadToken"]
    key2 = fp.parse_upload_token(ROUTE_SECRET, token2)["key"]
    r2.objects[key2] = raw   # 동일 바이트 → 동일 sha256
    puts_before = len(r2.put_calls)
    r2res = _sign(client, make_token, token2)
    assert r2res.status_code == 200, r2res.text
    assert r2res.json()["publicationId"] == r1.json()["publicationId"]
    assert signer.calls == 1, "재서명이 signer 를 다시 불렀다 — 멱등이 깨졌다"
    assert len(r2.put_calls) == puts_before, "재서명이 R2 에 다시 썼다 — 멱등이 깨졌다"


def test_route_sign_zip_takes_skip_branch_with_ledger_row_written(prov, make_token):
    client, store, r2 = prov
    _seed_output_record(store)
    store["evidence"][(MODEL_ID, LICENSE_ID)] = _evidence_row()

    p = _presign(client, make_token, kind="zip")
    upload_token = p.json()["uploadToken"]
    key = fp.parse_upload_token(ROUTE_SECRET, upload_token)["key"]
    r2.objects[key] = b"PK\x03\x04-zip-bytes"

    r = _sign(client, make_token, upload_token)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["c2paStatus"] == "skipped"
    assert body["publicationId"] in store["publications"]
    assert store["anchor_jobs"] == [body["publicationId"]]


def test_route_sign_signer_failure_returns_200_with_original_bytes(prov, make_token):
    client, store, r2 = prov
    _seed_output_record(store)
    store["evidence"][(MODEL_ID, LICENSE_ID)] = _evidence_row()
    client.app.state.fm_c2pa_signer = FakeSigner(fail=True)

    p = _presign(client, make_token)
    upload_token = p.json()["uploadToken"]
    key = fp.parse_upload_token(ROUTE_SECRET, upload_token)["key"]
    raw = b"\x89PNG-original-bytes"
    r2.objects[key] = raw

    r = _sign(client, make_token, upload_token)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["c2paStatus"] == "failed"
    assert r2.put_calls, "put_bytes 가 호출돼야 한다"
    stored_key, stored_bytes, _mime = r2.put_calls[-1]
    assert stored_bytes == raw, "서명 실패는 원본 바이트를 그대로 저장해야 한다"


def test_route_sign_refuses_when_license_revoked_between_presign_and_sign(prov, make_token):
    """I2 — presign 통과 후 sign 전에 라이선스가 철회되면 sign 이 거부해야 한다.

    거부 시 원장 행·앵커 잡·서명본 어느 것도 생기면 안 된다 — 산출물은 sign 에서
    '만들어지므로' 만들지 않는 게 이 테스트의 전부다.
    """
    client, store, r2 = prov
    _seed_output_record(store)
    store["evidence"][(MODEL_ID, LICENSE_ID)] = _evidence_row()

    p = _presign(client, make_token)
    assert p.status_code == 200, p.text
    upload_token = p.json()["uploadToken"]
    key = fp.parse_upload_token(ROUTE_SECRET, upload_token)["key"]
    r2.objects[key] = b"\x89PNG-bytes"

    # presign 과 sign 사이(_UPLOAD_TTL 창)에 라이선스가 철회됐다고 가정.
    store["evidence"][(MODEL_ID, LICENSE_ID)] = _evidence_row(status="revoked")

    r = _sign(client, make_token, upload_token)
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "license_revoked"
    assert store["publications"] == {}, "철회된 라이선스로 원장 행이 생기면 안 된다"
    assert store["anchor_jobs"] == [], "철회된 라이선스로 앵커 잡이 생기면 안 된다"
    assert r2.put_calls == [], "철회된 라이선스로 서명본이 R2 에 써지면 안 된다"


def test_route_sign_rejects_when_actual_size_exceeds_cap(prov, make_token):
    """I3 — presign 의 byte_size 는 자기신고값이다. HEAD 로 본 실제 크기가 상한을 넘으면
    get_bytes(전체 바이트 로드)에 닿기 전에 거부해야 한다.

    presign 자체도 같은 라이선스 게이트를 통과해야 하므로 evidence 는 정상적으로 심어
    presign 을 통과시킨다 — 이 테스트가 노리는 건 presign 통과 여부가 아니라 sign 내부에서
    HEAD 크기 확인이 get_bytes 보다 먼저 걸리는지다.
    """
    client, store, r2 = prov
    _seed_output_record(store)
    store["evidence"][(MODEL_ID, LICENSE_ID)] = _evidence_row()

    p = _presign(client, make_token, byte_size=100)
    assert p.status_code == 200, p.text
    upload_token = p.json()["uploadToken"]
    key = fp.parse_upload_token(ROUTE_SECRET, upload_token)["key"]
    r2.objects[key] = b"tiny"                       # 실제 저장된 바이트는 작지만
    r2.size_override[key] = fp._MAX_UPLOAD_BYTES + 1  # HEAD 가 보고하는 크기는 상한 초과

    r = _sign(client, make_token, upload_token)
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "too_large"
    assert store["publications"] == {}, "초과용량 요청으로 원장 행이 생기면 안 된다"


def test_route_sign_503_when_token_secret_unconfigured(keypair, monkeypatch):
    """fm_provenance_token_secret 미설정이면 fm_ci_pepper 로 조용히 안 넘어가고 503 이다."""
    _priv, public_key = keypair
    app = create_app(make_settings(
        facemarket_enabled=True, fm_provenance_enabled=True,
        fm_provenance_token_secret=None, fm_ci_pepper="unrelated-ci-pepper",
    ))
    app.state.jwt_key_resolver = lambda _token: public_key
    app.state.r2 = FakeRouteR2()
    app.state.fm_c2pa_signer = None
    store = _new_store()

    @contextlib.asynccontextmanager
    async def fake_get_conn(_request):
        yield FakeConn(store)

    monkeypatch.setattr(fp, "get_conn", fake_get_conn)

    with TestClient(app) as client:
        import jwt as jwt_lib
        token = jwt_lib.encode(
            {"sub": SELLER, "aud": "authenticated", "exp": int(time.time()) + 3600},
            _priv, algorithm="ES256",
        )
        r = client.post(
            "/v1/facemarket/publications/presign",
            json={"projectId": PROJECT_ID, "kind": "long_png", "byteSize": 10},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 503, r.text
    assert r.json()["error"]["code"] == "provenance_unconfigured"
