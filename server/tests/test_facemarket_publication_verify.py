"""무인증 공개 검증 — 여기 실리는 값은 회수 불가다.

facemarket.py:1249 의 하드룰을 그대로 계승한다. 응답 모델에 선언된 필드가 전부이고,
SELECT 자체가 화이트리스트다. 이 테스트는 그 계약을 못박는다.

리뷰(fix round 1)가 지적한 대로, 위 두 테스트는 `PublicationVerifyResult.model_fields`만
본다 — Pydantic 스키마 선언은 검증하지만 라우트 자체는 한 번도 호출하지 않는다. 아래부터는
`test_facemarket_licenses.py`(형제 라우트 `verify_license_public`)와 같은 모양의 페이크
DB + TestClient 로 실제 핸들러를 호출해, "삭제된 컬럼은 응답에 없다"가 아니라 "가짜 행에
금지값을 채워 넣어도 직렬화된 본문에 안 나온다"까지 확인한다.
"""
import contextlib
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import facemarket_provenance
from app.facemarket_provenance import PublicationVerifyResult
from app.main import create_app
from conftest import make_settings

FORBIDDEN = {
    "faceImageKey", "faceImageUri", "faceImageDigest", "ciHash", "ci",
    "birthDate", "birthYear", "displayName", "realName", "userId",
    "r2Key", "signedSha256", "sourceAssetIds", "modelId", "sellerId",
    "imageSha256",   # 전체 해시는 안 싣는다 — 앞 12자만
}


def test_response_model_has_no_forbidden_fields():
    declared = set(PublicationVerifyResult.model_fields)
    camel = {
        "".join(w if i == 0 else w.capitalize() for i, w in enumerate(n.split("_")))
        for n in declared
    }
    leaked = camel & FORBIDDEN
    assert not leaked, f"공개 검증 응답에 금지 필드가 있다: {leaked}"


def test_response_model_fields_are_exactly_the_whitelist():
    assert set(PublicationVerifyResult.model_fields) == {
        "valid", "status", "published_at", "image_hash_prefix", "kind",
        "allowed_use", "forbidden_use", "license_valid_until", "chain", "model",
    }


# ── 여기부터: 라우트를 실제로 호출하는 통합 테스트 ─────────────────────

NOW = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
PUB_ID = "11111111-1111-4111-8111-111111111111"
_WHITELIST_KEYS = {
    "valid", "status", "publishedAt", "imageHashPrefix", "kind",
    "allowedUse", "forbiddenUse", "licenseValidUntil", "chain", "model",
}


def _row(**overrides):
    """`verify_publication`의 SELECT 가 돌려주는 행 모양 그대로. 기본값=활성·미만료."""
    base = {
        "kind": "long_png",
        "image_sha256": "ab" * 32,           # 64자 전체 해시(원본) — 응답엔 앞 12자만
        "created_at": NOW,
        "revoked_at": None,
        "chain_status": "confirmed",
        "tx_hash": "0x" + "cd" * 20,
        "chain_id": "201210",
        "recorded_block": 4242,
        "license_status": "active",
        "allowed_use": ["상의"],
        "forbidden_use": ["속옷·란제리"],
        "license_valid_until": NOW + timedelta(days=30),
        "display_name": "홍길동",
        "birth_year": "1996",
    }
    base.update(overrides)
    return base


class FakePubCursor:
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
        self.store.setdefault("queries", []).append(s)
        if s.startswith("select p.kind, p.image_sha256"):
            pub_id = params[0]
            self._result = self.store["publications"].get(pub_id)
        else:  # pragma: no cover
            raise AssertionError(f"unexpected SQL: {s}")

    async def fetchone(self):
        return self._result


class FakePubConn:
    def __init__(self, store):
        self.store = store

    def cursor(self):
        return FakePubCursor(self.store)

    async def commit(self):
        return None

    async def rollback(self):
        return None


@pytest.fixture()
def pub_client(keypair, monkeypatch):
    _priv, public_key = keypair
    app = create_app(make_settings(facemarket_enabled=True, fm_provenance_enabled=True))
    app.state.jwt_key_resolver = lambda token: public_key
    store = {"publications": {}}

    @contextlib.asynccontextmanager
    async def fake_get_conn(_request):
        yield FakePubConn(store)

    monkeypatch.setattr(facemarket_provenance, "get_conn", fake_get_conn)
    return TestClient(app), store


def test_verify_publication_returns_exactly_the_whitelist_body_keys(pub_client):
    """model_fields 가 아니라 **직렬화된 응답 본문**의 키 집합을 잰다 — 스키마 선언과
    실제로 나가는 JSON 이 어긋나는 회귀(예: response_model 오적용)는 이래야 잡힌다."""
    client, store = pub_client
    store["publications"][PUB_ID] = _row()

    r = client.get(f"/v1/facemarket/publications/verify/{PUB_ID}")

    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == _WHITELIST_KEYS
    assert set(body["model"]) == {"nameMasked", "age"}
    assert set(body["chain"]) == {"status", "txHash", "chainId", "block"}


def test_verify_publication_never_leaks_banned_values_present_in_the_row(pub_client):
    """행 자체에 금지값을 채워 넣어도(미래에 SELECT 가 넓어지는 회귀를 흉내) 직렬화된
    본문 어디에도 등장하지 않는지 확인한다 — 이게 실제로 미래의 유출을 잡는 테스트다."""
    client, store = pub_client
    full_hash = "0123456789abcdef" * 4  # 64 hex
    leaked_r2_key = "publications/user-9/leak/signed.png"
    leaked_seller_id = "99999999-9999-4999-8999-999999999999"
    leaked_model_id = "88888888-8888-4888-8888-888888888888"
    leaked_source_asset_id = "77777777-7777-4777-8777-777777777777"
    leaked_signed_sha256 = "fedcba9876543210" * 4
    leaked_raw_birth_year = "1990"
    leaked_real_name = "김철수"

    row = _row(
        image_sha256=full_hash,
        display_name=leaked_real_name,
        birth_year=leaked_raw_birth_year,
    )
    # 실제 SELECT 는 이 컬럼들을 조회조차 안 하지만(방어①), 페이크 행에 억지로 채워서
    # "설령 여기 있었어도 응답 조립 코드가 안 옮긴다"는 것까지 시험한다.
    row.update({
        "r2_key": leaked_r2_key,
        "seller_id": leaked_seller_id,
        "model_id": leaked_model_id,
        "source_asset_ids": [leaked_source_asset_id],
        "signed_sha256": leaked_signed_sha256,
    })
    store["publications"][PUB_ID] = row

    r = client.get(f"/v1/facemarket/publications/verify/{PUB_ID}")

    assert r.status_code == 200, r.text
    raw = r.text
    for leaked in (
        leaked_r2_key, leaked_seller_id, leaked_model_id, leaked_source_asset_id,
        leaked_signed_sha256, full_hash,   # 전체 해시(앞 12자 아님)
        leaked_raw_birth_year, leaked_real_name,
    ):
        assert leaked not in raw, f"공개 검증 응답에 {leaked!r} 유출"


def test_image_hash_prefix_is_first_12_chars_of_the_full_hash(pub_client):
    client, store = pub_client
    full_hash = "0123456789abcdef" * 4
    store["publications"][PUB_ID] = _row(image_sha256=full_hash)

    body = client.get(f"/v1/facemarket/publications/verify/{PUB_ID}").json()

    assert body["imageHashPrefix"] == full_hash[:12]
    assert len(body["imageHashPrefix"]) == 12
    assert body["imageHashPrefix"] != full_hash


def test_revoked_at_set_is_revoked_and_invalid(pub_client):
    client, store = pub_client
    store["publications"][PUB_ID] = _row(revoked_at=NOW)

    body = client.get(f"/v1/facemarket/publications/verify/{PUB_ID}").json()

    assert body["status"] == "revoked"
    assert body["valid"] is False


def test_license_status_revoked_is_revoked_and_invalid(pub_client):
    client, store = pub_client
    store["publications"][PUB_ID] = _row(license_status="revoked")

    body = client.get(f"/v1/facemarket/publications/verify/{PUB_ID}").json()

    assert body["status"] == "revoked"
    assert body["valid"] is False


def test_expired_license_is_expired_and_invalid(pub_client):
    """DB status='active' 라도 기간이 지났으면 status='expired'+valid=false(실시간 판정,
    verify_license_public 과 같은 _is_expired 단일 소스)."""
    client, store = pub_client
    store["publications"][PUB_ID] = _row(
        license_status="active",
        license_valid_until=datetime(2020, 1, 1, tzinfo=timezone.utc),
    )

    body = client.get(f"/v1/facemarket/publications/verify/{PUB_ID}").json()

    assert body["status"] == "expired"
    assert body["valid"] is False


def test_deleted_license_row_reads_as_revoked_not_a_crash(pub_client):
    """라이선스 행이 지워져도(on delete set null) 원장 행은 남는다 — l.status 는 NULL 로
    조인된다. 이 경우 크래시도, valid=True 도 안 되고 revoked 로 읽혀야 한다(brief 명시)."""
    client, store = pub_client
    store["publications"][PUB_ID] = _row(
        license_status=None, allowed_use=None, forbidden_use=None,
        license_valid_until=None,
    )

    r = client.get(f"/v1/facemarket/publications/verify/{PUB_ID}")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "revoked"
    assert body["valid"] is False
    assert body["allowedUse"] == []
    assert body["forbiddenUse"] == []
    assert body["licenseValidUntil"] is None


def test_malformed_id_returns_404_without_touching_db(pub_client):
    client, store = pub_client

    r = client.get("/v1/facemarket/publications/verify/not-a-uuid")

    assert r.status_code == 404
    assert store.get("queries", []) == []


def test_unknown_id_returns_404(pub_client):
    client, _ = pub_client

    r = client.get(f"/v1/facemarket/publications/verify/{PUB_ID}")

    assert r.status_code == 404


def test_alias_uuid_form_resolves_via_the_parsed_value(pub_client):
    """`uuid.UUID()`는 `urn:uuid:` 별칭도 받아준다. 원문을 그대로 쿼리에 쓰면(파싱값이
    아니라) 이 조회가 미스나거나(운영 PG 에서는 캐스팅이 터져 500) — 파싱된 canonical
    문자열을 썼는지는 별칭으로 넣었을 때도 정상 조회되는지로 증명한다."""
    client, store = pub_client
    store["publications"][PUB_ID] = _row()

    r = client.get(f"/v1/facemarket/publications/verify/urn:uuid:{PUB_ID}")

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "active"


def test_masked_name_and_derived_age_raw_birth_year_never_in_body(pub_client):
    client, store = pub_client
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo

    birth_year = "1990"
    store["publications"][PUB_ID] = _row(display_name="박영희", birth_year=birth_year)

    body = client.get(f"/v1/facemarket/publications/verify/{PUB_ID}").json()

    assert body["model"]["nameMasked"] == "박*희"
    kst_today = _dt.now(ZoneInfo("Asia/Seoul")).date()
    assert body["model"]["age"] == kst_today.year - int(birth_year) - 1
    assert birth_year not in client.get(
        f"/v1/facemarket/publications/verify/{PUB_ID}"
    ).text
    assert "박영희" not in client.get(f"/v1/facemarket/publications/verify/{PUB_ID}").text


def test_cache_control_no_store_on_success(pub_client):
    client, store = pub_client
    store["publications"][PUB_ID] = _row()

    r = client.get(f"/v1/facemarket/publications/verify/{PUB_ID}")

    assert r.headers["cache-control"] == "no-store"


def test_cache_control_no_store_on_404_malformed(pub_client):
    """리뷰 Important 2 — 404 는 valid 필드가 없어 캐시된 valid:true 사고는 안 나지만,
    캐시된 404 가 뒤이은 진짜 200 을 가리는 사고는 여전히 real이다. 헤더는 두 404 경로
    모두 무조건 붙어야 한다."""
    client, _ = pub_client

    r = client.get("/v1/facemarket/publications/verify/not-a-uuid")

    assert r.status_code == 404
    assert r.headers["cache-control"] == "no-store"


def test_cache_control_no_store_on_404_unknown(pub_client):
    client, _ = pub_client

    r = client.get(f"/v1/facemarket/publications/verify/{PUB_ID}")

    assert r.status_code == 404
    assert r.headers["cache-control"] == "no-store"
