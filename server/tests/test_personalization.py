"""개인화(사용자 얼굴·신체) 엔드포인트 테스트 (docs/personalization/api-spec.md §1~3·§5).

`personalization.py`의 SQL은 savepoint(`_ensure_profile`)·ON CONFLICT upsert·FOR UPDATE 락·
다중 `get_conn` 블록을 쓴다 — FakeCursor로 SQL 문자열을 패턴매칭해 흉내내면 구현을 그대로
베끼는 꼴이라 회귀를 못 잡는다. 그래서 여기서는 로컬 Supabase Postgres(DATABASE_URL)를
**실제로** 쓴다: `personalization.get_conn`만 몽키패치해 요청마다 새 psycopg
`AsyncConnection`을 여는 얇은 어댑터로 바꾸고(연결을 컨텍스트매니저로 쓰면 정상 종료 시
commit·예외 시 rollback — `db.get_conn`이 쓰는 `pool.connection()`과 동일 시맨틱,
psycopg_pool 소스로 확인함), 외부(R2·비전 QC)만 목으로 대체한다.

테스트 유저는 매 테스트 `auth.users`에 신규 uuid 행을 심고 종료 시 삭제한다 —
personalization_profiles/consents/audit_log/generations·jobs·assets 전부 auth.users FK
`on delete cascade`라 유저 삭제 한 번으로 관련 행이 전부 정리된다(테스트 간 격리 보장,
전역 TRUNCATE 불필요).
"""

import asyncio
import contextlib
import hashlib
import json
import os
import types
import uuid
from datetime import date

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg.types.json import Json

from app import cx_identity, facemarket, personalization
from app.main import create_app
from app.personalization_qc import FaceQcResult, FaceQcUnavailable
from app.workers.personalization_purge_job import run_personalization_purge_job
from conftest import make_settings

DB_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
)

ANGLES = personalization.ANGLES
CONSENT_DOC_VERSION = personalization.CONSENT_DOC_VERSION
PNG_BYTES = b"\x89PNG\r\n\x1a\nFAKE-PNG-BYTES"


# ── DB 어댑터 ────────────────────────────────────────────────
def _sync_conn():
    return psycopg.connect(DB_URL, autocommit=True, row_factory=dict_row)


@contextlib.asynccontextmanager
async def _real_conn(_request):
    """personalization.get_conn 대역 — 요청마다 새 실연결(db.get_conn 커밋/롤백 시맨틱과 동일)."""
    async with await psycopg.AsyncConnection.connect(DB_URL, row_factory=dict_row) as conn:
        yield conn


class FakeR2Face:
    """app.state.r2_face 대역 — 바이트를 dict에 보관(FaceMarket 테스트 선례 미러)."""

    def __init__(self):
        self.objects: dict[str, tuple[bytes, str]] = {}

    def put_bytes(self, key, data, mime, cache=None):
        self.objects[key] = (data, mime)

    def get_bytes(self, key):
        return self.objects[key][0]

    def delete(self, key):
        self.objects.pop(key, None)

    def list_prefix(self, prefix):
        return [k for k in self.objects if k.startswith(prefix)]


def _qc_stub(verdict: str, reasons: list[str] | None = None):
    async def _inner(settings, *, image_bytes, mime, angle):
        return FaceQcResult(verdict=verdict, reasons=list(reasons or []))

    return _inner


async def _qc_unavailable(settings, *, image_bytes, mime, angle):
    raise FaceQcUnavailable("qc_provider_unconfigured")


# ── 유저 픽스처(FK 충족 + cascade 정리) ─────────────────────
def _new_user() -> str:
    user_id = str(uuid.uuid4())
    conn = _sync_conn()
    conn.execute("insert into auth.users (id) values (%s)", (user_id,))
    conn.close()
    return user_id


def _drop_user(user_id: str) -> None:
    conn = _sync_conn()
    conn.execute("delete from auth.users where id = %s", (user_id,))
    conn.close()


def _seed_adult_verification(user_id: str) -> None:
    """personalization_identity_verifications 에 성인 인증 행 직접 insert.

    연령 게이트(T2-1)가 동의·업로드·생성 3지점에 추가되며, 그 이전부터 있던 43개 테스트는
    전부 '이미 성인 본인확인을 마친 사용자'를 전제로 한다(각 테스트의 관심사는 동의·업로드·
    파기 등이지 연령 게이트 자체가 아니다). uid/other_uid 픽스처가 기본으로 심어 그 43개를
    수정 없이 그대로 통과시키고, 연령 게이트 자체를 검증하는 테스트는 아래 raw_uid(미인증
    원시 유저)를 따로 써서 상태를 직접 통제한다."""
    conn = _sync_conn()
    conn.execute(
        "insert into personalization_identity_verifications (user_id, cx_tx_hash, is_adult) "
        "values (%s, %s, true)",
        (user_id, f"test-seed-{uuid.uuid4()}"),
    )
    conn.close()


@pytest.fixture()
def uid():
    user_id = _new_user()
    _seed_adult_verification(user_id)
    yield user_id
    _drop_user(user_id)


@pytest.fixture()
def other_uid():
    user_id = _new_user()
    _seed_adult_verification(user_id)
    yield user_id
    _drop_user(user_id)


@pytest.fixture()
def raw_uid():
    """미인증 원시 유저 — 연령 게이트(identity:verify) 자체를 검증하는 테스트 전용.

    uid/other_uid 는 위에서 기본 성인 인증을 심으므로 미인증·미성년 상태를 재현할 수 없다."""
    user_id = _new_user()
    yield user_id
    _drop_user(user_id)


@pytest.fixture()
def raw_headers(make_token, raw_uid):
    return {"Authorization": f"Bearer {make_token(sub=raw_uid)}"}


@pytest.fixture()
def client(keypair, monkeypatch):
    _priv, public_key = keypair
    app = create_app(make_settings(personalization_enabled=True, database_url=None))
    app.state.jwt_key_resolver = lambda token: public_key
    r2 = FakeR2Face()
    app.state.r2_face = r2
    monkeypatch.setattr(personalization, "get_conn", _real_conn)
    return TestClient(app), r2


@pytest.fixture()
def headers(make_token, uid):
    return {"Authorization": f"Bearer {make_token(sub=uid)}"}


@pytest.fixture()
def other_headers(make_token, other_uid):
    return {"Authorization": f"Bearer {make_token(sub=other_uid)}"}


# ── 시드 헬퍼 ────────────────────────────────────────────────
def _seed_asset(user_id: str) -> str:
    asset_id = str(uuid.uuid4())
    conn = _sync_conn()
    conn.execute(
        "insert into assets (id, user_id, source, visibility, r2_bucket, r2_key, mime_type) "
        "values (%s, %s, 'upload', 'private', 'test-bucket', %s, 'image/png')",
        (asset_id, user_id, f"products/{asset_id}.png"),
    )
    conn.close()
    return asset_id


def _seed_credits(user_id: str, amount: int = 100) -> None:
    """credit_sources 시드 + credit_accounts.balance 동기화(repo.grant_credits류 선례 미러).

    credit_accounts_check(reserved <= balance)는 balance가 credit_sources 합과 별도로
    관리되는 컬럼이라 credit_sources만 심으면 reserve_credits의 UPDATE가 CheckViolation.
    """
    conn = _sync_conn()
    conn.execute(
        "insert into credit_sources (user_id, source_type, initial_credits, remaining_credits) "
        "values (%s, 'topup', %s, %s)",
        (user_id, amount, amount),
    )
    conn.execute(
        "update credit_accounts set balance = balance + %s where user_id = %s",
        (amount, user_id),
    )
    conn.close()


def _fetch_job(user_id: str, kind: str) -> dict | None:
    conn = _sync_conn()
    row = conn.execute(
        "select id::text as id, status, kind from jobs where user_id = %s and kind = %s "
        "order by created_at desc limit 1",
        (user_id, kind),
    ).fetchone()
    conn.close()
    return row


def _mark_purged(user_id: str) -> None:
    conn = _sync_conn()
    conn.execute(
        "update personalization_profiles set status = 'purged', purged_at = now() "
        "where user_id = %s",
        (user_id,),
    )
    conn.close()


def _fetch_profile_id(user_id: str) -> str | None:
    conn = _sync_conn()
    row = conn.execute(
        "select id::text as id from personalization_profiles "
        "where user_id = %s and status <> 'purged'",
        (user_id,),
    ).fetchone()
    conn.close()
    return row["id"] if row else None


def _seed_generation(profile_id: str, result_keys: list[str]) -> str:
    """personalization_generations 행 직접 insert(엔진 워커 없이 결과 게이트 테스트용)."""
    generation_id = str(uuid.uuid4())
    conn = _sync_conn()
    conn.execute(
        "insert into personalization_generations (id, profile_id, result_keys) values (%s, %s, %s)",
        (generation_id, profile_id, result_keys),
    )
    conn.close()
    return generation_id


def _seed_profile(user_id: str, status: str = "purging") -> str:
    """프로필 행 직접 insert. 기본 'purging' = 철회 라우트가 파기잡을 만든 직후 상태."""
    profile_id = str(uuid.uuid4())
    conn = _sync_conn()
    conn.execute(
        "insert into personalization_profiles (id, user_id, status) values (%s, %s, %s)",
        (profile_id, user_id, status),
    )
    conn.close()
    return profile_id


def _seed_purge_job(user_id: str, profile_id: str) -> dict:
    """파기 워커가 클레임을 마친 상태(running + locked_by=lease_token)의 jobs 행.

    워커는 locked_by == job['lease_token'] 로 lease 를 펜싱한다(repo.claim 이 locked_by 를
    lease_token 으로 RETURNING 하는 것과 동일 규약) — 둘을 같은 값으로 심어야 부수효과가 산다.
    """
    job_id = str(uuid.uuid4())
    lease_token = f"test-worker:{uuid.uuid4()}"
    conn = _sync_conn()
    conn.execute(
        "insert into jobs (id, user_id, project_id, kind, status, locked_by, locked_at, payload) "
        "values (%s, %s, null, 'personalization_purge', 'running', %s, now(), %s)",
        (job_id, user_id, lease_token, Json({"profileId": profile_id})),
    )
    conn.close()
    return {"id": job_id, "user_id": user_id, "lease_token": lease_token,
            "payload": {"profileId": profile_id}}


def _fetch_audit_detail(profile_id: str, event_type: str) -> dict | None:
    conn = _sync_conn()
    row = conn.execute(
        "select detail from personalization_audit_log "
        "where profile_id = %s and event_type = %s order by created_at desc limit 1",
        (profile_id, event_type),
    ).fetchone()
    conn.close()
    return row["detail"] if row else None


class _RealPool:
    """워커의 app.state.pool 대역 — 요청마다 실연결(`_real_conn` 과 동일 근거·시맨틱).

    검증 대상이 워커 SQL(lease 펜스·캐스케이드 delete·감사 insert) 자체라 커서를 목으로
    바꾸면 구현을 베끼는 꼴이 된다(모듈 docstring 방침). 실 Postgres 를 그대로 쓴다.
    """

    @contextlib.asynccontextmanager
    async def connection(self):
        async with await psycopg.AsyncConnection.connect(DB_URL, row_factory=dict_row) as conn:
            yield conn


def _purge_app(r2_face):
    return types.SimpleNamespace(state=types.SimpleNamespace(pool=_RealPool(), r2_face=r2_face))


def _count_consent_actions(user_id: str, consent_type: str, action: str) -> int:
    conn = _sync_conn()
    row = conn.execute(
        "select count(*) as c from personalization_consents "
        "where user_id = %s and consent_type = %s and action = %s",
        (user_id, consent_type, action),
    ).fetchone()
    conn.close()
    return row["c"]


# ── 플로우 헬퍼 ──────────────────────────────────────────────
def _grant_required_consents(c, headers):
    r = c.post(
        "/v1/personalization/consents",
        json={
            "items": [
                {"type": "service_use", "docVersion": CONSENT_DOC_VERSION},
                {"type": "cross_border_transfer", "docVersion": CONSENT_DOC_VERSION},
            ]
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return r


def _upload_all_angles(c, headers, monkeypatch):
    monkeypatch.setattr(personalization, "evaluate_face_qc", _qc_stub("pass"))
    for angle in ANGLES:
        r = c.post(
            "/v1/personalization/face-photos",
            files={"photo": (f"{angle}.png", PNG_BYTES, "image/png")},
            data={"angle": angle},
            headers=headers,
        )
        assert r.status_code == 201, r.text


def _put_valid_body(c, headers):
    r = c.put(
        "/v1/personalization/profile/body",
        json={"heightCm": 170.0, "weightKg": 65.0, "bodyType": "slim"},
        headers=headers,
    )
    assert r.status_code == 200, r.text


def _make_ready_profile(c, headers, monkeypatch):
    _grant_required_consents(c, headers)
    _upload_all_angles(c, headers, monkeypatch)
    _put_valid_body(c, headers)


# ── 연령 게이트(T2-1) 전용 헬퍼 ─────────────────────────────
# 성인 판별은 만 나이 계산(생일 반영)이라 고정 연도만으로는 안전하지 않다 — 1990년생은
# 어느 시점에 테스트를 돌려도 명백히 성인이라 상수로 고정. 미성년은 반대로 '오늘' 기준
# 상대 계산이어야 시간이 지나도 계속 미성년으로 남는다(하드코딩 날짜 금지 원칙).
_ADULT_BIRTH = "19900101"
_MINOR_BIRTH = f"{date.today().year - 5:04d}0101"  # 오늘 기준 5세 — 항상 미성년


def _verify_identity(c, headers, monkeypatch, *, birth: str, token: str | None = None):
    """cx_identity.fetch_trans 를 monkeypatch 해 실제 /identity:verify 라우트를 태운다(외부 CX 미호출)."""
    token = token or f"tok-{uuid.uuid4()}"

    async def _fake_fetch_trans(base_url, tok):
        return {"birth": birth}

    monkeypatch.setattr(cx_identity, "fetch_trans", _fake_fetch_trans)
    return c.post(
        "/v1/personalization/identity:verify",
        json={"token": token},
        headers=headers,
    )


def _fetch_identity_verifications(user_id: str) -> list[dict]:
    conn = _sync_conn()
    rows = conn.execute(
        "select cx_tx_hash, is_adult from personalization_identity_verifications "
        "where user_id = %s order by verified_at",
        (user_id,),
    ).fetchall()
    conn.close()
    return rows


def _delete_identity_verifications(user_id: str) -> None:
    """인증 소실 시뮬레이션 — 동의 이후 인증이 사라져도 업로드/생성이 독립적으로 재차단하는지 검증용."""
    conn = _sync_conn()
    conn.execute(
        "delete from personalization_identity_verifications where user_id = %s", (user_id,)
    )
    conn.close()


# ============================================================================
# 1) 동의(Consent) — §3.1
# ============================================================================
def test_get_consents_initial_all_none(client, headers):
    c, _r2 = client
    r = c.get("/v1/personalization/consents", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["retentionDays"] == 365
    assert {item["type"] for item in body["consents"]} == set(personalization.ALL_CONSENT_TYPES)
    for item in body["consents"]:
        assert item["status"] == "none"
        assert item["grantedAt"] is None and item["withdrawnAt"] is None
        assert item["required"] == (item["type"] in personalization.REQUIRED_CONSENTS)


def test_submit_required_consents_grants_and_creates_draft_profile(client, headers):
    c, _r2 = client
    r = _grant_required_consents(c, headers)
    by_type = {item["type"]: item for item in r.json()["consents"]}
    assert by_type["service_use"]["status"] == "granted"
    assert by_type["service_use"]["docVersion"] == CONSENT_DOC_VERSION
    assert by_type["service_use"]["grantedAt"] is not None
    assert by_type["cross_border_transfer"]["status"] == "granted"
    assert by_type["training_use"]["status"] == "none"  # 미제출 항목은 그대로
    # 프로필 draft 가 생성됐는지 GET /profile 로 확인(없으면 404였을 것)
    prof = c.get("/v1/personalization/profile", headers=headers)
    assert prof.status_code == 200, prof.text
    assert prof.json()["status"] == "draft"


def test_submit_consents_idempotent_when_already_granted(client, headers):
    c, _r2 = client
    first = _grant_required_consents(c, headers)
    granted_at = next(
        i["grantedAt"] for i in first.json()["consents"] if i["type"] == "service_use"
    )
    second = _grant_required_consents(c, headers)  # 재제출 — no-op
    assert second.status_code == 200
    granted_at_2 = next(
        i["grantedAt"] for i in second.json()["consents"] if i["type"] == "service_use"
    )
    assert granted_at_2 == granted_at  # 새 이력 행이 안 생겨 타임스탬프 불변


def test_submit_training_use_consent_is_separate_from_service_use(client, headers):
    c, _r2 = client
    r = c.post(
        "/v1/personalization/consents",
        json={"items": [{"type": "training_use", "docVersion": CONSENT_DOC_VERSION}]},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    by_type = {item["type"]: item for item in r.json()["consents"]}
    assert by_type["training_use"]["status"] == "granted"
    assert by_type["service_use"]["status"] == "none"  # 포괄 동의 아님 — 분리
    assert by_type["cross_border_transfer"]["status"] == "none"


def test_submit_consents_invalid_type_returns_400(client, headers):
    c, _r2 = client
    r = c.post(
        "/v1/personalization/consents",
        json={"items": [{"type": "bogus_type", "docVersion": CONSENT_DOC_VERSION}]},
        headers=headers,
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_consent_type"


def test_submit_consents_stale_doc_version_returns_400(client, headers):
    c, _r2 = client
    r = c.post(
        "/v1/personalization/consents",
        json={"items": [{"type": "service_use", "docVersion": "2020-01-v0"}]},
        headers=headers,
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "stale_consent_doc"


def test_submit_consents_empty_items_returns_400(client, headers):
    c, _r2 = client
    r = c.post("/v1/personalization/consents", json={"items": []}, headers=headers)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_consent_type"


def test_withdraw_service_use_returns_202_purging_with_purge_job(client, headers, uid):
    c, _r2 = client
    _grant_required_consents(c, headers)
    r = c.post("/v1/personalization/consents/service_use:withdraw", headers=headers)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["type"] == "service_use"
    assert body["status"] == "withdrawn"
    assert body["purgeJobId"] is not None
    job = _fetch_job(uid, "personalization_purge")
    assert job is not None and job["status"] == "pending"


def test_withdraw_training_use_returns_200_without_purge_job(client, headers, uid):
    c, _r2 = client
    c.post(
        "/v1/personalization/consents",
        json={"items": [{"type": "training_use", "docVersion": CONSENT_DOC_VERSION}]},
        headers=headers,
    )
    r = c.post("/v1/personalization/consents/training_use:withdraw", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "withdrawn"
    assert body["purgeJobId"] is None
    assert _fetch_job(uid, "personalization_purge") is None  # 파기 잡 없음(서비스 유지)


def test_withdraw_unknown_consent_type_returns_400(client, headers):
    c, _r2 = client
    r = c.post("/v1/personalization/consents/bogus:withdraw", headers=headers)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_consent_type"


def test_withdraw_consent_without_profile_returns_404(client, headers):
    c, _r2 = client
    r = c.post("/v1/personalization/consents/service_use:withdraw", headers=headers)
    assert r.status_code == 404


# ============================================================================
# 2) 얼굴 업로드 게이트 — §3.2
# ============================================================================
def test_upload_face_photo_without_consent_returns_403(client, headers, monkeypatch):
    c, r2 = client
    called = {"n": 0}

    async def _qc_should_not_be_called(*a, **k):  # 동의 전에는 QC(외부 비전 API) 호출 금지(§1.4)
        called["n"] += 1
        return FaceQcResult(verdict="pass", reasons=[])

    monkeypatch.setattr(personalization, "evaluate_face_qc", _qc_should_not_be_called)
    r = c.post(
        "/v1/personalization/face-photos",
        files={"photo": ("front.png", PNG_BYTES, "image/png")},
        data={"angle": "front"},
        headers=headers,
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "consent_required"
    assert called["n"] == 0
    assert len(r2.objects) == 0


def test_upload_face_photo_qc_reject_returns_400_with_reasons(client, headers, monkeypatch):
    c, r2 = client
    _grant_required_consents(c, headers)
    monkeypatch.setattr(
        personalization, "evaluate_face_qc", _qc_stub("reject", ["occlusion", "low_resolution"])
    )
    r = c.post(
        "/v1/personalization/face-photos",
        files={"photo": ("front.png", PNG_BYTES, "image/png")},
        data={"angle": "front"},
        headers=headers,
    )
    assert r.status_code == 400, r.text
    err = r.json()["error"]
    assert err["code"] == "face_quality"
    assert err["reasons"] == ["occlusion", "low_resolution"]
    assert len(r2.objects) == 0  # 불합격 원본은 저장되지 않음


def test_upload_face_photo_qc_pass_returns_201_and_stores_slot(client, headers, monkeypatch):
    c, r2 = client
    _grant_required_consents(c, headers)
    monkeypatch.setattr(personalization, "evaluate_face_qc", _qc_stub("pass"))
    r = c.post(
        "/v1/personalization/face-photos",
        files={"photo": ("front.png", PNG_BYTES, "image/png")},
        data={"angle": "front"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["angle"] == "front"
    assert body["qcStatus"] == "passed"
    assert body["qcReasons"] == []
    assert body["imageUri"] == "/v1/personalization/face-photos/front/file"
    assert body["byteSize"] == len(PNG_BYTES)
    assert len(r2.objects) == 1  # 비공개 R2에 정확히 1개 저장
    key = next(iter(r2.objects))
    assert "/faces/front." in key

    listing = c.get("/v1/personalization/face-photos", headers=headers)
    assert listing.status_code == 200
    photos = {p["angle"]: p for p in listing.json()["photos"]}
    assert photos["front"]["qcStatus"] == "passed"
    assert photos["side"]["qcStatus"] == "none"
    assert listing.json()["complete"] is False


def test_upload_face_photo_qc_unavailable_returns_503(client, headers, monkeypatch):
    c, r2 = client
    _grant_required_consents(c, headers)
    monkeypatch.setattr(personalization, "evaluate_face_qc", _qc_unavailable)
    r = c.post(
        "/v1/personalization/face-photos",
        files={"photo": ("front.png", PNG_BYTES, "image/png")},
        data={"angle": "front"},
        headers=headers,
    )
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "qc_unavailable"
    assert len(r2.objects) == 0


def test_upload_face_photo_invalid_angle_returns_400(client, headers):
    c, _r2 = client
    r = c.post(
        "/v1/personalization/face-photos",
        files={"photo": ("x.png", PNG_BYTES, "image/png")},
        data={"angle": "back"},
        headers=headers,
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_angle"


def test_upload_face_photo_unsupported_mime_returns_400(client, headers):
    c, _r2 = client
    r = c.post(
        "/v1/personalization/face-photos",
        files={"photo": ("x.pdf", b"%PDF-1.4", "application/pdf")},
        data={"angle": "front"},
        headers=headers,
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "unsupported_type"


# ============================================================================
# 3) 신체 프로필 — §3.3
# ============================================================================
def test_put_body_profile_valid_returns_200(client, headers):
    c, _r2 = client
    r = c.put(
        "/v1/personalization/profile/body",
        json={
            "heightCm": 172.5,
            "weightKg": 64.0,
            "bodyType": "slim",
            "gender": "female",
            "ageRange": "20s",
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()["body"]
    assert body["heightCm"] == 172.5
    assert body["weightKg"] == 64.0
    assert body["bodyType"] == "slim"
    assert r.json()["profileStatus"] == "draft"  # 아직 동의·사진 없음 → ready 아님


def test_put_body_profile_height_out_of_range_returns_400(client, headers):
    c, _r2 = client
    r = c.put(
        "/v1/personalization/profile/body",
        json={"heightCm": 300.0, "weightKg": 60.0, "bodyType": "slim"},
        headers=headers,
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_body_profile"


def test_put_body_profile_custom_missing_description_returns_400(client, headers):
    c, _r2 = client
    r = c.put(
        "/v1/personalization/profile/body",
        json={"heightCm": 170.0, "weightKg": 60.0, "bodyType": "custom"},
        headers=headers,
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_body_profile"


# ============================================================================
# 4) 상태 조회(생성 가능 여부) — §3.4
# ============================================================================
def test_status_no_profile_returns_none_with_all_blockers(client, headers):
    c, _r2 = client
    r = c.get("/v1/personalization/status", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "none"
    assert body["canGenerate"] is False
    codes = {b["code"] for b in body["blockers"]}
    assert codes == {"photos_incomplete", "consent_missing", "body_profile_missing"}


def test_status_blockers_narrow_to_ready_as_conditions_met(client, headers, monkeypatch):
    c, _r2 = client
    # 1) 필수 동의만 충족 — photos_incomplete·body_profile_missing 만 남아야 함
    _grant_required_consents(c, headers)
    r = c.get("/v1/personalization/status", headers=headers)
    codes = {b["code"] for b in r.json()["blockers"]}
    assert codes == {"photos_incomplete", "body_profile_missing"}
    assert r.json()["canGenerate"] is False

    # 2) 얼굴 3장 QC 통과 추가 — body_profile_missing 만 남아야 함
    _upload_all_angles(c, headers, monkeypatch)
    r = c.get("/v1/personalization/status", headers=headers)
    codes = {b["code"] for b in r.json()["blockers"]}
    assert codes == {"body_profile_missing"}

    # 3) 신체 프로필 저장 — 3조건 전부 충족 → canGenerate true, status ready
    _put_valid_body(c, headers)
    r = c.get("/v1/personalization/status", headers=headers)
    assert r.json()["blockers"] == []
    assert r.json()["canGenerate"] is True
    assert r.json()["status"] == "ready"


# ============================================================================
# 5) 생성 게이트 — §4 (워커는 실행하지 않음 — 잡 큐잉만 확인)
# ============================================================================
def test_start_generation_without_profile_returns_409(client, headers):
    c, _r2 = client
    r = c.post(
        "/v1/personalization/generations",
        json={"productImageAssetIds": [str(uuid.uuid4())]},
        headers=headers,
    )
    assert r.status_code == 409, r.text
    body = r.json()["error"]
    assert body["code"] == "profile_not_ready"
    assert {b["code"] for b in body["blockers"]} == {
        "photos_incomplete",
        "consent_missing",
        "body_profile_missing",
    }


def test_start_generation_draft_profile_not_ready_returns_409(client, headers):
    c, _r2 = client
    _grant_required_consents(c, headers)  # 프로필은 생성되지만 사진·신체 미완 → draft
    r = c.post(
        "/v1/personalization/generations",
        json={"productImageAssetIds": [str(uuid.uuid4())]},
        headers=headers,
    )
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "profile_not_ready"


def test_start_generation_ready_profile_queues_job(client, headers, monkeypatch, uid):
    c, _r2 = client
    _make_ready_profile(c, headers, monkeypatch)
    asset_id = _seed_asset(uid)
    _seed_credits(uid)
    r = c.post(
        "/v1/personalization/generations",
        json={"productImageAssetIds": [asset_id]},
        headers=headers,
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["jobId"]
    assert body["generationId"]
    job = _fetch_job(uid, "personalization_generation")
    assert job is not None
    assert job["status"] == "pending"


# ============================================================================
# 6) 파기(캐스케이드) — §3.5
# ============================================================================
def test_withdraw_all_returns_202_purging_with_job(client, headers, uid):
    c, _r2 = client
    _grant_required_consents(c, headers)
    r = c.post("/v1/personalization:withdraw", headers=headers)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "purging"
    assert body["purgeJobId"]
    job = _fetch_job(uid, "personalization_purge")
    assert job is not None and job["status"] == "pending"

    status = c.get("/v1/personalization/status", headers=headers)
    assert status.json()["status"] == "purging"
    assert status.json()["purgeJobId"] == body["purgeJobId"]


def test_write_during_purge_returns_409_purge_in_progress(client, headers):
    c, _r2 = client
    _grant_required_consents(c, headers)
    c.post("/v1/personalization:withdraw", headers=headers)

    body_write = c.put(
        "/v1/personalization/profile/body",
        json={"heightCm": 170.0, "weightKg": 60.0, "bodyType": "slim"},
        headers=headers,
    )
    assert body_write.status_code == 409
    assert body_write.json()["error"]["code"] == "purge_in_progress"


def test_upload_during_purge_returns_409_purge_in_progress(client, headers, monkeypatch):
    c, r2 = client
    _grant_required_consents(c, headers)
    c.post("/v1/personalization:withdraw", headers=headers)

    r = c.post(
        "/v1/personalization/face-photos",
        files={"photo": ("front.png", PNG_BYTES, "image/png")},
        data={"angle": "front"},
        headers=headers,
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "purge_in_progress"
    assert len(r2.objects) == 0


# ============================================================================
# 7) PII/404 — 타인·purged 리소스 은닉, 얼굴 게이트 Bearer 필수
# ============================================================================
def test_face_photo_file_requires_bearer(client):
    c, _r2 = client
    r = c.get("/v1/personalization/face-photos/front/file")
    assert r.status_code == 401


def test_face_photo_file_other_user_returns_404(client, headers, other_headers, monkeypatch):
    c, _r2 = client
    _grant_required_consents(c, headers)
    monkeypatch.setattr(personalization, "evaluate_face_qc", _qc_stub("pass"))
    c.post(
        "/v1/personalization/face-photos",
        files={"photo": ("front.png", PNG_BYTES, "image/png")},
        data={"angle": "front"},
        headers=headers,
    )
    mine = c.get("/v1/personalization/face-photos/front/file", headers=headers)
    assert mine.status_code == 200
    others = c.get("/v1/personalization/face-photos/front/file", headers=other_headers)
    assert others.status_code == 404  # 타인 소유 리소스 존재 은닉


def test_purged_profile_hides_resources_404(client, headers, uid):
    c, _r2 = client
    _grant_required_consents(c, headers)
    c.post("/v1/personalization:withdraw", headers=headers)  # purging
    _mark_purged(uid)  # 워커 없이 파기 완료를 시뮬레이션

    assert c.get("/v1/personalization/profile", headers=headers).status_code == 404
    assert c.get("/v1/personalization/face-photos", headers=headers).status_code == 404
    assert (
        c.get("/v1/personalization/face-photos/front/file", headers=headers).status_code == 404
    )
    # /status 만 예외 — purged 도 200 {"status": "none"}(재시작 가능, §3.4)
    status = c.get("/v1/personalization/status", headers=headers)
    assert status.status_code == 200
    assert status.json()["status"] == "none"
    assert status.json()["canGenerate"] is False


# ============================================================================
# 8) 생성 결과 파일 게이트 — CRITICAL-B 회귀(무인증 공유 assets 경로 미사용·존재 은닉)
# ============================================================================
def test_generation_result_file_requires_bearer(client):
    c, _r2 = client
    r = c.get(f"/v1/personalization/generations/{uuid.uuid4()}/results/0/file")
    assert r.status_code == 401


def test_generation_result_file_own_generation_returns_bytes_with_no_store_header(
    client, headers, uid
):
    c, r2 = client
    _grant_required_consents(c, headers)  # draft 프로필 생성
    profile_id = _fetch_profile_id(uid)
    key = f"personalization/profiles/{profile_id}/results/0.png"
    r2.put_bytes(key, PNG_BYTES, "image/png")
    generation_id = _seed_generation(profile_id, [key])

    r = c.get(
        f"/v1/personalization/generations/{generation_id}/results/0/file", headers=headers
    )
    assert r.status_code == 200, r.text
    assert r.content == PNG_BYTES
    assert r.headers["cache-control"] == "no-store, private"


def test_generation_result_file_other_user_returns_404(client, headers, other_headers, uid):
    c, r2 = client
    _grant_required_consents(c, headers)
    profile_id = _fetch_profile_id(uid)
    key = f"personalization/profiles/{profile_id}/results/0.png"
    r2.put_bytes(key, PNG_BYTES, "image/png")
    generation_id = _seed_generation(profile_id, [key])

    mine = c.get(
        f"/v1/personalization/generations/{generation_id}/results/0/file", headers=headers
    )
    assert mine.status_code == 200
    others = c.get(
        f"/v1/personalization/generations/{generation_id}/results/0/file",
        headers=other_headers,
    )
    assert others.status_code == 404  # 타인 소유 리소스 존재 은닉


def test_generation_result_file_profile_purging_returns_404(client, headers, uid):
    c, r2 = client
    _grant_required_consents(c, headers)
    profile_id = _fetch_profile_id(uid)
    key = f"personalization/profiles/{profile_id}/results/0.png"
    r2.put_bytes(key, PNG_BYTES, "image/png")
    generation_id = _seed_generation(profile_id, [key])

    c.post("/v1/personalization:withdraw", headers=headers)  # status -> purging

    r = c.get(
        f"/v1/personalization/generations/{generation_id}/results/0/file", headers=headers
    )
    assert r.status_code == 404


def test_generation_result_file_profile_purged_returns_404(client, headers, uid):
    c, r2 = client
    _grant_required_consents(c, headers)
    profile_id = _fetch_profile_id(uid)
    key = f"personalization/profiles/{profile_id}/results/0.png"
    r2.put_bytes(key, PNG_BYTES, "image/png")
    generation_id = _seed_generation(profile_id, [key])

    c.post("/v1/personalization:withdraw", headers=headers)
    _mark_purged(uid)  # 워커 없이 파기 완료를 시뮬레이션

    r = c.get(
        f"/v1/personalization/generations/{generation_id}/results/0/file", headers=headers
    )
    assert r.status_code == 404


def test_generation_result_file_index_out_of_range_returns_404(client, headers, uid):
    c, r2 = client
    _grant_required_consents(c, headers)
    profile_id = _fetch_profile_id(uid)
    key = f"personalization/profiles/{profile_id}/results/0.png"
    r2.put_bytes(key, PNG_BYTES, "image/png")
    generation_id = _seed_generation(profile_id, [key])  # result_keys 길이 1 → 유효 index는 0뿐

    r = c.get(
        f"/v1/personalization/generations/{generation_id}/results/1/file", headers=headers
    )
    assert r.status_code == 404


def test_generation_result_file_negative_index_returns_404(client, headers, uid):
    c, _r2 = client
    _grant_required_consents(c, headers)
    profile_id = _fetch_profile_id(uid)
    generation_id = _seed_generation(profile_id, [])

    r = c.get(
        f"/v1/personalization/generations/{generation_id}/results/-1/file", headers=headers
    )
    assert r.status_code == 404


def test_generation_result_file_invalid_uuid_returns_404_not_500(client, headers):
    c, _r2 = client
    # uuid 형식 가드 회귀 — 쓰레기 generation_id 는 500(uuid 캐스팅 에러)이 아니라 404여야 함.
    r = c.get(
        "/v1/personalization/generations/not-a-uuid/results/0/file", headers=headers
    )
    assert r.status_code == 404


# ============================================================================
# 9) MAJOR-D — 전체 파기 시 동의 캐스케이드(재온보딩 시 과거 동의 재사용 차단)
# ============================================================================
def test_withdraw_all_marks_required_consents_withdrawn_not_granted(client, headers, uid):
    c, _r2 = client
    _grant_required_consents(c, headers)
    r = c.post("/v1/personalization:withdraw", headers=headers)
    assert r.status_code == 202, r.text

    consents = c.get("/v1/personalization/consents", headers=headers)
    assert consents.status_code == 200
    by_type = {i["type"]: i for i in consents.json()["consents"]}
    assert by_type["service_use"]["status"] == "withdrawn"
    assert by_type["cross_border_transfer"]["status"] == "withdrawn"


def test_reonboarding_after_purge_blocks_face_upload_without_new_consent(
    client, headers, uid, monkeypatch
):
    """핵심 회귀: MAJOR-D 미적용 시 파기 후에도 동의가 user_id 스코프로 granted 잔존해,
    재온보딩(신규 draft 프로필)에서 재동의 없이 얼굴 업로드가 통과해버린다."""
    c, r2 = client
    _grant_required_consents(c, headers)
    c.post("/v1/personalization:withdraw", headers=headers)
    _mark_purged(uid)  # 워커 없이 파기 완료를 시뮬레이션

    # 재온보딩: 동의는 다시 제출하지 않고 신체 프로필만 입력 → _ensure_profile 이 새 draft 생성.
    _put_valid_body(c, headers)

    monkeypatch.setattr(personalization, "evaluate_face_qc", _qc_stub("pass"))
    r = c.post(
        "/v1/personalization/face-photos",
        files={"photo": ("front.png", PNG_BYTES, "image/png")},
        data={"angle": "front"},
        headers=headers,
    )
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "consent_required"
    assert len(r2.objects) == 0  # 업로드가 실제로 저장되지 않았는지도 확인


def test_withdraw_all_idempotent_does_not_duplicate_withdrawn_consent_rows(
    client, headers, uid
):
    c, _r2 = client
    _grant_required_consents(c, headers)
    first = c.post("/v1/personalization:withdraw", headers=headers)
    assert first.status_code == 202, first.text
    assert _count_consent_actions(uid, "service_use", "withdrawn") == 1
    assert _count_consent_actions(uid, "cross_border_transfer", "withdrawn") == 1

    second = c.post("/v1/personalization:withdraw", headers=headers)  # 이미 purging → 멱등
    assert second.status_code == 202, second.text
    assert second.json()["purgeJobId"] == first.json()["purgeJobId"]  # 새 잡 생성 안 됨
    assert _count_consent_actions(uid, "service_use", "withdrawn") == 1  # 중복 행 없음
    assert _count_consent_actions(uid, "cross_border_transfer", "withdrawn") == 1


def test_withdraw_consent_service_use_idempotent_no_duplicate_withdrawn_rows(
    client, headers, uid
):
    c, _r2 = client
    _grant_required_consents(c, headers)
    first = c.post("/v1/personalization/consents/service_use:withdraw", headers=headers)
    assert first.status_code == 202, first.text
    assert _count_consent_actions(uid, "service_use", "withdrawn") == 1
    assert _count_consent_actions(uid, "cross_border_transfer", "withdrawn") == 1  # 캐스케이드

    second = c.post("/v1/personalization/consents/service_use:withdraw", headers=headers)
    assert second.status_code == 202, second.text
    assert _count_consent_actions(uid, "service_use", "withdrawn") == 1  # 중복 행 없음
    assert _count_consent_actions(uid, "cross_border_transfer", "withdrawn") == 1


# ── 파기 캐스케이드 워커: 고아 산출물 회수(api-spec §3.5 · MINOR-6) ──────────
def test_purge_deletes_orphan_generation_result_unreferenced_by_db(uid):
    """생성 워커가 put_bytes 후 finalize 전에 죽어 남은 고아 객체를 파기가 회수한다.

    회귀 대상: 파기는 result_keys 로만 삭제 대상을 찾았다 → DB 참조가 없는 객체(=고아)는
    영영 못 찾아 얼굴 담긴 산출물이 파기 후에도 R2 에 잔존했다(§3.5 파기 완전성 위반).

    크래시 상태 재현: generation 행은 라우트가 요청 시점에 이미 만든다(result_keys 기본
    '{}') → 워커가 put_bytes 후 finalize 전에 죽으면 "행은 있는데 result_keys 는 빈 배열,
    객체는 R2 에 존재"가 된다. 행 자체를 빼면 이 경로를 안 밟으므로 빈 행을 그대로 심는다.
    """
    profile_id = _seed_profile(uid)
    generation_id = _seed_generation(profile_id, [])  # finalize 전 = result_keys 빈 배열
    r2 = FakeR2Face()
    orphan_key = f"personalization/{uid}/generations/{generation_id}/0.png"
    r2.put_bytes(orphan_key, b"face-bytes", "image/png")

    asyncio.run(run_personalization_purge_job(_purge_app(r2), _seed_purge_job(uid, profile_id)))

    assert orphan_key not in r2.objects
    detail = _fetch_audit_detail(profile_id, "purge_completed")
    assert detail["generationOrphansDeleted"] == 1
    assert detail["generationOrphanScan"] == "ok"


def test_purge_deletes_referenced_and_orphan_results_with_separate_counts(uid):
    """참조된 산출물(4a result_keys)과 고아(4c 스캔)를 모두 지우고 카운트는 분리 집계한다."""
    profile_id = _seed_profile(uid)
    r2 = FakeR2Face()
    ref_key = f"personalization/{uid}/generations/{uuid.uuid4()}/0.png"
    orphan_key = f"personalization/{uid}/generations/{uuid.uuid4()}/0.png"
    r2.put_bytes(ref_key, b"referenced", "image/png")
    r2.put_bytes(orphan_key, b"orphan", "image/png")
    _seed_generation(profile_id, [ref_key])

    asyncio.run(run_personalization_purge_job(_purge_app(r2), _seed_purge_job(uid, profile_id)))

    assert r2.objects == {}
    detail = _fetch_audit_detail(profile_id, "purge_completed")
    assert detail["generationResultsR2Deleted"] == 1  # 4a 가 처리
    assert detail["generationOrphansDeleted"] == 1  # 4a 이후 남은 것만 고아로 계수


def test_purge_skips_unfinalized_generation_rows_when_collecting_result_keys(uid):
    """미종결 generation(result_keys 빈 배열)이 섞여도 4a 키 수집·카운트가 흔들리지 않는다.

    이 테스트는 `where result_keys <> '{}'` 필터를 *증명하지 않는다* — 빈 배열은 flatten 하면
    사라져서 `is not null`(항상 참) 이든 `<> '{}'` 이든 결과가 같기 때문이다. 그 교체는 순수
    명료성 수정이었다. 여기서 고정하는 건 그 등가성 자체다: 필터를 또 만질 때 미종결 행이
    카운트에 새는 회귀를 잡는다.
    """
    profile_id = _seed_profile(uid)
    r2 = FakeR2Face()
    done_key = f"personalization/{uid}/generations/{uuid.uuid4()}/0.png"
    r2.put_bytes(done_key, b"done", "image/png")
    _seed_generation(profile_id, [done_key])  # 종결
    _seed_generation(profile_id, [])  # 미종결 — 키 없음

    asyncio.run(run_personalization_purge_job(_purge_app(r2), _seed_purge_job(uid, profile_id)))

    detail = _fetch_audit_detail(profile_id, "purge_completed")
    assert detail["generationResults"] == 1  # 빈 행이 섞이지 않음
    assert detail["generationResultsR2Deleted"] == 1
    assert detail["generationOrphansDeleted"] == 0  # 4a 가 이미 지움 → 고아 없음
    assert r2.objects == {}


def test_purge_orphan_sweep_scoped_to_owner_generations_prefix(uid, other_uid):
    """스캔 삭제 범위는 파기 대상 사용자의 generations prefix 뿐.

    prefix 를 넓히면(예: personalization/) 남의 산출물·본인 얼굴 원본까지 지우는 데이터
    파괴가 된다. 얼굴 원본은 personalization/profiles/{profile_id}/faces/ 로 prefix 가 다르며
    파기 1단계(face_photos 행 기반)가 따로 책임진다 — 스캔이 건드리면 안 된다.
    """
    profile_id = _seed_profile(uid)
    r2 = FakeR2Face()
    mine = f"personalization/{uid}/generations/{uuid.uuid4()}/0.png"
    theirs = f"personalization/{other_uid}/generations/{uuid.uuid4()}/0.png"
    my_face = f"personalization/profiles/{profile_id}/faces/front.png"
    for k in (mine, theirs, my_face):
        r2.put_bytes(k, b"x", "image/png")

    asyncio.run(run_personalization_purge_job(_purge_app(r2), _seed_purge_job(uid, profile_id)))

    assert mine not in r2.objects
    assert theirs in r2.objects  # 남의 얼굴 산출물
    assert my_face in r2.objects  # generations prefix 밖 — 1단계 소관


def test_purge_orphan_scan_failure_is_recorded_not_silently_complete(uid):
    """스캔 실패는 '고아 0건'과 구분돼 감사로그에 남는다.

    둘 다 generationOrphansDeleted == 0 이지만, 실패는 파기 완전성을 보장하지 못한 상태다.
    구분이 없으면 감사 증적만 보고 완전 파기됐다고 오판하게 된다(§3.5).
    """
    profile_id = _seed_profile(uid)

    class _ListFails(FakeR2Face):
        def list_prefix(self, prefix):
            raise RuntimeError("r2 list unavailable")

    asyncio.run(
        run_personalization_purge_job(_purge_app(_ListFails()), _seed_purge_job(uid, profile_id)))

    detail = _fetch_audit_detail(profile_id, "purge_completed")
    assert detail["generationOrphanScan"] == "failed"
    assert detail["generationOrphansDeleted"] == 0


def test_purge_audit_detail_carries_counts_without_r2_keys(uid):
    """§1.4 하드룰: 고아 스캔이 키를 다루더라도 감사로그엔 카운트만 — 키·경로 미기록."""
    profile_id = _seed_profile(uid)
    r2 = FakeR2Face()
    gid = str(uuid.uuid4())
    r2.put_bytes(f"personalization/{uid}/generations/{gid}/0.png", b"x", "image/png")

    asyncio.run(run_personalization_purge_job(_purge_app(r2), _seed_purge_job(uid, profile_id)))

    blob = json.dumps(_fetch_audit_detail(profile_id, "purge_completed"))
    assert "personalization/" not in blob
    assert gid not in blob
    assert uid not in blob


# ============================================================================
# 8) 연령 게이트(T2-1) — 본인확인(CX 표준인증창) 기반 성인 인증
#    미성년 생체정보 수집 차단이 목적이라 4지점(동의·업로드·생성·상태) 전부 검증한다.
# ============================================================================

# ── A) is_adult_from_birth 유닛 — 외부 호출 없음, today= 로 결정적 ──
def test_is_adult_from_birth_yyyymmdd_true_after_19th_birthday():
    today = date(2026, 7, 15)
    assert cx_identity.is_adult_from_birth("20070714", today=today) is True


def test_is_adult_from_birth_yyyymmdd_false_before_19th_birthday():
    today = date(2026, 7, 15)
    assert cx_identity.is_adult_from_birth("20070716", today=today) is False


def test_is_adult_from_birth_yyyymmdd_true_on_exact_19th_birthday():
    today = date(2026, 7, 15)
    assert cx_identity.is_adult_from_birth("20070715", today=today) is True


def test_is_adult_from_birth_18_years_old_is_false():
    today = date(2026, 7, 15)
    assert cx_identity.is_adult_from_birth("20080715", today=today) is False


def test_is_adult_from_birth_parses_dash_separated_format():
    today = date(2026, 7, 15)
    assert cx_identity.is_adult_from_birth("2000-01-01", today=today) is True


def test_is_adult_from_birth_year_only_20_year_gap_is_true():
    today = date(2026, 7, 15)
    assert cx_identity.is_adult_from_birth("2006", today=today) is True


def test_is_adult_from_birth_year_only_19_year_gap_is_conservatively_false():
    """생일 미상이라 연도차 19(경계)는 아직 만 19세 미확정 — 보수적으로 미성년 취급."""
    today = date(2026, 7, 15)
    assert cx_identity.is_adult_from_birth("2007", today=today) is False


def test_is_adult_from_birth_unparsable_raises_cx_identity_error():
    with pytest.raises(cx_identity.CxIdentityError):
        cx_identity.is_adult_from_birth("abc", today=date(2026, 7, 15))


def test_is_adult_from_birth_nonexistent_date_raises_cx_identity_error():
    with pytest.raises(cx_identity.CxIdentityError):
        cx_identity.is_adult_from_birth("20070230", today=date(2026, 7, 15))  # 2월 30일은 존재하지 않음


# ── B) POST /identity:verify — cx_identity.fetch_trans monkeypatch, 외부 CX 미호출 ──
def test_identity_verify_adult_returns_200_and_records_single_adult_row(client, raw_headers, raw_uid, monkeypatch):
    c, _r2 = client
    r = _verify_identity(c, raw_headers, monkeypatch, birth=_ADULT_BIRTH)
    assert r.status_code == 200, r.text
    assert r.json() == {"verified": True, "isAdult": True}
    rows = _fetch_identity_verifications(raw_uid)
    assert len(rows) == 1
    assert rows[0]["is_adult"] is True


def test_identity_verify_minor_returns_403_minor_blocked_but_records_row(client, raw_headers, raw_uid, monkeypatch):
    r = _verify_identity(client[0], raw_headers, monkeypatch, birth=_MINOR_BIRTH)
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "minor_blocked"
    rows = _fetch_identity_verifications(raw_uid)
    assert len(rows) == 1
    assert rows[0]["is_adult"] is False


def test_identity_verify_replay_same_token_returns_409(client, raw_headers, monkeypatch):
    c, _r2 = client
    token = f"tok-replay-{uuid.uuid4()}"
    first = _verify_identity(c, raw_headers, monkeypatch, birth=_ADULT_BIRTH, token=token)
    assert first.status_code == 200, first.text
    second = _verify_identity(c, raw_headers, monkeypatch, birth=_ADULT_BIRTH, token=token)
    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] == "identity_replay"


def test_identity_verify_missing_birth_returns_400_cx_verify_failed(client, raw_headers, raw_uid, monkeypatch):
    async def _no_birth(base_url, token):
        return {}  # trans 응답에 birth 없음

    monkeypatch.setattr(cx_identity, "fetch_trans", _no_birth)
    r = client[0].post(
        "/v1/personalization/identity:verify",
        json={"token": f"tok-{uuid.uuid4()}"},
        headers=raw_headers,
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "cx_verify_failed"
    assert _fetch_identity_verifications(raw_uid) == []


def test_identity_verify_cx_lookup_failure_returns_400_cx_verify_failed(client, raw_headers, raw_uid, monkeypatch):
    async def _boom(base_url, token):
        raise cx_identity.CxIdentityError("cx_verify_failed")

    monkeypatch.setattr(cx_identity, "fetch_trans", _boom)
    r = client[0].post(
        "/v1/personalization/identity:verify",
        json={"token": f"tok-{uuid.uuid4()}"},
        headers=raw_headers,
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "cx_verify_failed"
    assert _fetch_identity_verifications(raw_uid) == []


def test_identity_verify_without_bearer_returns_401(client):
    r = client[0].post("/v1/personalization/identity:verify", json={"token": "tok-x"})
    assert r.status_code == 401


def test_identity_verify_adult_response_excludes_birth_ci_name(client, raw_headers, monkeypatch):
    """최소수집 회귀 방지 — 성공 응답에 생년월일 원문·CI·이름이 없어야 한다(verified/isAdult 뿐)."""
    c, _r2 = client
    ok = _verify_identity(c, raw_headers, monkeypatch, birth=_ADULT_BIRTH)
    assert ok.status_code == 200, ok.text
    assert set(ok.json().keys()) == {"verified", "isAdult"}
    assert _ADULT_BIRTH not in ok.text


def test_identity_verify_minor_response_excludes_birth_ci_name(client, raw_headers, monkeypatch):
    """최소수집 회귀 방지 — 미성년 차단(403) 응답에도 생년월일 원문·CI·이름이 없어야 한다."""
    c, _r2 = client
    blocked = _verify_identity(c, raw_headers, monkeypatch, birth=_MINOR_BIRTH)
    assert blocked.status_code == 403, blocked.text
    assert _MINOR_BIRTH not in blocked.text
    assert set(blocked.json()["error"].keys()) == {"code", "message"}


# ── C) 게이트 4지점 ──────────────────────────────────────────
def test_submit_consents_without_identity_verification_returns_403_and_records_nothing(
    client, raw_headers, raw_uid
):
    c, _r2 = client
    r = c.post(
        "/v1/personalization/consents",
        json={
            "items": [
                {"type": "service_use", "docVersion": CONSENT_DOC_VERSION},
                {"type": "cross_border_transfer", "docVersion": CONSENT_DOC_VERSION},
            ]
        },
        headers=raw_headers,
    )
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "identity_verification_required"
    assert _count_consent_actions(raw_uid, "service_use", "granted") == 0
    assert _count_consent_actions(raw_uid, "cross_border_transfer", "granted") == 0
    assert _fetch_profile_id(raw_uid) is None  # 프로필도 생성되지 않아야 함(연령 게이트가 선행)


def test_submit_consents_after_minor_verification_returns_403_minor_blocked(client, raw_headers, monkeypatch):
    c, _r2 = client
    _verify_identity(c, raw_headers, monkeypatch, birth=_MINOR_BIRTH)  # 403이지만 인증행은 기록됨
    r = c.post(
        "/v1/personalization/consents",
        json={"items": [{"type": "service_use", "docVersion": CONSENT_DOC_VERSION}]},
        headers=raw_headers,
    )
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "minor_blocked"


def test_submit_consents_after_adult_verification_succeeds(client, raw_headers, monkeypatch):
    c, _r2 = client
    v = _verify_identity(c, raw_headers, monkeypatch, birth=_ADULT_BIRTH)
    assert v.status_code == 200, v.text
    r = _grant_required_consents(c, raw_headers)  # 200 아니면 헬퍼가 내부에서 assert
    by_type = {item["type"]: item for item in r.json()["consents"]}
    assert by_type["service_use"]["status"] == "granted"
    assert by_type["cross_border_transfer"]["status"] == "granted"


def test_upload_face_photo_without_identity_verification_returns_403_even_with_consent(
    client, raw_headers, raw_uid, monkeypatch
):
    """동의는 있는 상태로 세팅해 연령 게이트가 consent_required 와 독립적으로 작동하는지 확인.

    §3.2 전제조건 ②: 동의 이후 인증이 소실돼도 업로드에서 재차단해야 한다."""
    c, _r2 = client
    _verify_identity(c, raw_headers, monkeypatch, birth=_ADULT_BIRTH)
    _grant_required_consents(c, raw_headers)
    _delete_identity_verifications(raw_uid)  # 인증 소실 시뮬레이션
    monkeypatch.setattr(personalization, "evaluate_face_qc", _qc_stub("pass"))

    r = c.post(
        "/v1/personalization/face-photos",
        files={"photo": ("front.png", PNG_BYTES, "image/png")},
        data={"angle": "front"},
        headers=raw_headers,
    )
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "identity_verification_required"


def test_upload_face_photo_unverified_never_calls_qc(client, raw_headers, raw_uid, monkeypatch):
    """미인증 얼굴 바이트가 **QC(=Gemini/GPT 비전, 미국)로 전송되지 않는지** 단언.

    연령 게이트의 법적 존재 이유가 '미성년 생체정보의 수집·국외이전 차단'이라, 게이트가 QC 호출
    **이전에** 도는 순서가 load-bearing 하다. 순서가 뒤집혀도 403 은 그대로라 상태코드만으로는
    회귀를 못 잡는다 → QC 미호출을 명시 단언한다(api-spec §3.2 전제조건 ②·§1.4 국외이전 게이트).
    """
    c, _r2 = client
    called: list[int] = []

    def _qc_spy(*_a, **_k):
        called.append(1)
        raise AssertionError("연령 게이트 통과 전에 QC 가 호출됨 — 미성년 얼굴이 외부로 나갈 수 있다")

    _verify_identity(c, raw_headers, monkeypatch, birth=_ADULT_BIRTH)
    _grant_required_consents(c, raw_headers)
    _delete_identity_verifications(raw_uid)  # 인증 소실 → 업로드는 차단돼야 함
    monkeypatch.setattr(personalization, "evaluate_face_qc", _qc_spy)

    r = c.post(
        "/v1/personalization/face-photos",
        files={"photo": ("front.png", PNG_BYTES, "image/png")},
        data={"angle": "front"},
        headers=raw_headers,
    )
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "identity_verification_required"
    assert called == []  # 얼굴 바이트가 외부 비전 API 로 나가지 않았음


def test_upload_face_photo_minor_never_calls_qc(client, raw_headers, monkeypatch):
    """미성년 확정 상태에서도 얼굴 바이트가 QC(외부 전송)에 도달하지 않는지 단언(위와 동일 취지)."""
    c, _r2 = client
    called: list[int] = []

    def _qc_spy(*_a, **_k):
        called.append(1)
        raise AssertionError("미성년 얼굴이 QC 로 전달됨")

    # 성인으로 동의까지 받아둔 뒤 미성년 인증을 덧씌워, 연령 게이트만 단독으로 검증한다.
    _verify_identity(c, raw_headers, monkeypatch, birth=_ADULT_BIRTH)
    _grant_required_consents(c, raw_headers)
    minor = _verify_identity(c, raw_headers, monkeypatch, birth=_MINOR_BIRTH)
    assert minor.status_code == 403  # 미성년 인증 자체는 기록됨(최신 행 = is_adult false)
    monkeypatch.setattr(personalization, "evaluate_face_qc", _qc_spy)

    r = c.post(
        "/v1/personalization/face-photos",
        files={"photo": ("front.png", PNG_BYTES, "image/png")},
        data={"angle": "front"},
        headers=raw_headers,
    )
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "minor_blocked"
    assert called == []


def test_start_generation_blocked_when_identity_unverified_even_if_otherwise_ready(
    client, raw_headers, raw_uid, monkeypatch
):
    """미인증이면 생성이 절대 큐잉되지 않는지 확인(안전 프로퍼티).

    실측: 이 경로는 403 이 아니라 **409 profile_not_ready**(blockers 에 identity_verification_
    required 포함)로 응답한다 — start_generation 이 `if created:` 블록에서 `_readiness()` 를
    `_assert_age_eligible()`보다 먼저 호출하고, `_readiness()` 도 동일한 age_row 조건으로
    blocker 를 채우기 때문에 그 뒤의 명시적 `_assert_age_eligible()`(연령 게이트 ③, personalization.py
    :1074)은 이 경로로는 도달 불가능하다(consent 제출·업로드는 반대로 age 게이트가 먼저라 순수
    403 이 나온다). 생성이 막힌다는 안전 프로퍼티 자체는 유지되므로 여기서는 그 사실만 단언하고,
    상태코드 계약 불일치는 버그로 별도 보고한다(프로덕션 코드 수정은 범위 밖)."""
    c, _r2 = client
    profile_id = _seed_profile(raw_uid, status="draft")
    conn = _sync_conn()
    conn.execute(
        "update personalization_profiles set height_cm=170.0, weight_kg=65.0, body_type='slim' "
        "where id = %s",
        (profile_id,),
    )
    for angle in ANGLES:
        conn.execute(
            "insert into personalization_face_photos "
            "(profile_id, angle, r2_key, image_digest, mime_type, byte_size) "
            "values (%s, %s, %s, %s, %s, %s)",
            (
                profile_id,
                angle,
                f"personalization/profiles/{profile_id}/faces/{angle}.png",
                "sha256-" + "x" * 44,
                "image/png",
                len(PNG_BYTES),
            ),
        )
    for consent_type in ("service_use", "cross_border_transfer"):
        conn.execute(
            "insert into personalization_consents (user_id, profile_id, consent_type, action, doc_version) "
            "values (%s, %s, %s, 'granted', %s)",
            (raw_uid, profile_id, consent_type, CONSENT_DOC_VERSION),
        )
    conn.close()
    asset_id = _seed_asset(raw_uid)
    _seed_credits(raw_uid)

    r = c.post(
        "/v1/personalization/generations",
        json={"productImageAssetIds": [asset_id]},
        headers=raw_headers,
    )
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "profile_not_ready"
    codes = {b["code"] for b in r.json()["error"]["blockers"]}
    assert "identity_verification_required" in codes
    assert _fetch_job(raw_uid, "personalization_generation") is None  # 핵심 안전 프로퍼티: 잡 큐잉 금지


# ── D) GET /status blockers ──────────────────────────────────
def test_status_no_profile_unverified_lists_identity_blocker_first(client, raw_headers):
    r = client[0].get("/v1/personalization/status", headers=raw_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    codes = [b["code"] for b in body["blockers"]]
    assert codes == [
        "identity_verification_required",
        "photos_incomplete",
        "consent_missing",
        "body_profile_missing",
    ]
    assert body["canGenerate"] is False


def test_status_after_adult_verification_drops_identity_blocker(client, raw_headers, monkeypatch):
    c, _r2 = client
    _verify_identity(c, raw_headers, monkeypatch, birth=_ADULT_BIRTH)
    r = c.get("/v1/personalization/status", headers=raw_headers)
    codes = {b["code"] for b in r.json()["blockers"]}
    assert "identity_verification_required" not in codes
    assert codes == {"photos_incomplete", "consent_missing", "body_profile_missing"}


def test_status_after_minor_verification_lists_minor_blocked_first(client, raw_headers, monkeypatch):
    c, _r2 = client
    _verify_identity(c, raw_headers, monkeypatch, birth=_MINOR_BIRTH)
    r = c.get("/v1/personalization/status", headers=raw_headers)
    codes = [b["code"] for b in r.json()["blockers"]]
    assert codes[0] == "minor_blocked"
    assert "identity_verification_required" not in codes
    assert r.json()["canGenerate"] is False


# ============================================================================
# US-L1 — FaceMarket ↔ 개인화 본인확인 통합(Level 1) 회귀
#   personalization.py: _load_fm_age_verification / _load_age_verification (연령 폴백)
#   facemarket.py:       _record_personalization_adult (CX 1회 → 개인화 성인 인증 동시 성립)
#   personalization_purge_job.py:219-224 — 파기는 personalization_identity_verifications 만
#   지운다. fm_models/fm_identity_verifications 는 별개 테이블이라 워커 SQL이 참조조차 안 하지만,
#   CRITICAL이라 그 격리를 명시적 회귀로 고정한다(개인화 철회가 FaceMarket 데이터를 날리면
#   해커톤 필수 기능이 죽는다).
# ============================================================================

# ── 공용 픽스처 ──────────────────────────────────────────────
@pytest.fixture()
def fm_client(keypair, monkeypatch):
    """FaceMarket+개인화 둘 다 활성 + 둘 다 실 DB(personalization.get_conn·facemarket.get_conn
    모두 `_real_conn` 어댑터로 교체). US-L1 통합 회귀 전용 — 기존 `client` 는 개인화만 활성화한다."""
    _priv, public_key = keypair
    app = create_app(make_settings(
        personalization_enabled=True,
        facemarket_enabled=True,
        fm_ci_pepper="test-fm-pepper",
        database_url=None,
    ))
    app.state.jwt_key_resolver = lambda token: public_key
    r2 = FakeR2Face()
    app.state.r2_face = r2
    monkeypatch.setattr(personalization, "get_conn", _real_conn)
    monkeypatch.setattr(facemarket, "get_conn", _real_conn)
    return TestClient(app), r2


@pytest.fixture()
def fm_only_client(keypair, monkeypatch):
    """FaceMarket 만 활성(개인화 off) — Level 1 통합 훅이 `personalization_enabled` 게이트를
    실제로 존중하는지(개인화 미배포 시 no-op) 검증하는 전용 클라이언트."""
    _priv, public_key = keypair
    app = create_app(make_settings(
        personalization_enabled=False,
        facemarket_enabled=True,
        fm_ci_pepper="test-fm-pepper",
        database_url=None,
    ))
    app.state.jwt_key_resolver = lambda token: public_key
    monkeypatch.setattr(facemarket, "get_conn", _real_conn)
    return TestClient(app)


@pytest.fixture()
def fm_cleanup():
    """테스트가 직접 만든 fm_models/fm_identity_verifications 행 id 를 등록해두면 종료 시 정리.

    fm_models.user_id 는 auth.users FK 가 on delete **set null**(cascade 아님)이라 uid/raw_uid
    픽스처의 유저 삭제만으로는 fm_models 행 자체가 안 지워져 테스트 실행 간 누적된다."""
    model_ids: list[str] = []
    yield model_ids
    for mid in model_ids:
        _drop_fm_model(mid)


# ── FaceMarket 시드/조회 헬퍼 ────────────────────────────────
def _seed_fm_model(user_id: str, *, status: str = "verified") -> str:
    """fm_models 행 직접 insert(본인확인 라우트를 거치지 않는 연령 폴백 테스트용)."""
    model_id = str(uuid.uuid4())
    conn = _sync_conn()
    conn.execute(
        "insert into fm_models (id, user_id, display_name, status, ci_hash) "
        "values (%s, %s, %s, %s, %s)",
        (model_id, user_id, "Test Model", status, f"test-ci-hash-{uuid.uuid4()}"),
    )
    conn.close()
    return model_id


def _seed_fm_identity_verification(
    model_id: str, *, birth_year: str | None, cx_tx_id: str | None = None
) -> None:
    """fm_identity_verifications 행 직접 insert. birth_year=None 이면 fields 에 키 자체가 없다
    ('연령 소스 자체가 없음' 케이스 — birthYear 파싱 불가와는 다른 케이스)."""
    fields = {"birthYear": birth_year} if birth_year is not None else {}
    conn = _sync_conn()
    conn.execute(
        "insert into fm_identity_verifications (model_id, cx_tx_id, fields) values (%s, %s, %s)",
        (model_id, cx_tx_id or f"test-fm-cx-tok-{uuid.uuid4()}", Json(fields)),
    )
    conn.close()


def _fetch_fm_model(user_id: str) -> dict | None:
    conn = _sync_conn()
    row = conn.execute(
        "select id::text as id, status from fm_models where user_id = %s "
        "order by created_at desc limit 1",
        (user_id,),
    ).fetchone()
    conn.close()
    return row


def _fetch_fm_identity_verifications(model_id: str) -> list[dict]:
    conn = _sync_conn()
    rows = conn.execute(
        "select cx_tx_id, fields from fm_identity_verifications where model_id = %s "
        "order by verified_at",
        (model_id,),
    ).fetchall()
    conn.close()
    return rows


def _drop_fm_model(model_id: str) -> None:
    conn = _sync_conn()
    conn.execute("delete from fm_identity_verifications where model_id = %s", (model_id,))
    conn.execute("delete from fm_models where id = %s", (model_id,))
    conn.close()


def _claim_job_for_worker(job_id: str) -> str:
    """pending 잡을 워커 클레임 상태(running+locked_by)로 전이 — 테스트엔 디스패처가 없으므로
    실 라우트가 만든 잡을 파기 워커 함수에 직접 넘기기 위한 접착 헬퍼.

    `_seed_purge_job` 과 달리 새 잡을 만들지 않는다 — 이미 라우트가 만든 잡을 재사용해야
    `jobs_personalization_purge_singleton_idx`(사용자당 활성 파기잡 1개) 유니크 인덱스와
    충돌하지 않는다."""
    lease_token = f"test-worker:{uuid.uuid4()}"
    conn = _sync_conn()
    conn.execute(
        "update jobs set status = 'running', locked_by = %s, locked_at = now() where id = %s",
        (lease_token, job_id),
    )
    conn.close()
    return lease_token


def _verify_facemarket_identity(
    c, headers, monkeypatch, *, birth: str, ci: str | None = None, token: str | None = None
):
    """facemarket._fetch_trans 를 monkeypatch 해 실제 /v1/facemarket/identity/verify 를 태운다.

    개인화의 `_verify_identity` 헬퍼와 이름이 비슷하지만 **다른 함수**(`facemarket._fetch_trans`)를
    패치한다는 점이 핵심 — facemarket.py:14 주석대로 의도적 중복(해커톤 필수 경로 보호)."""
    token = token or f"fm-tok-{uuid.uuid4()}"
    ci = ci or f"ci-{uuid.uuid4()}"

    async def _fake_fetch_trans(base_url, tok):
        return {"birth": birth, "ci": ci, "utf8Nm": "홍길동"}

    monkeypatch.setattr(facemarket, "_fetch_trans", _fake_fetch_trans)
    return c.post(
        "/v1/facemarket/identity/verify",
        json={"token": token},
        headers=headers,
    )


def _run_facemarket_verify_consent_withdraw_and_purge(
    c, r2, headers, user_id, monkeypatch, *, birth: str = _ADULT_BIRTH
) -> dict:
    """US-L1-3 공용 셋업 — FaceMarket 인증 → 개인화 동의 → 전체 철회 → 파기 워커 실행까지.

    withdraw 라우트가 만든 실 잡을 그대로 클레임해 워커에 넘긴다(새 잡을 또 만들면
    `jobs_personalization_purge_singleton_idx` 유니크 인덱스와 충돌한다 — 기존 test_purge_* 는
    라우트를 거치지 않고 `_seed_purge_job` 으로 잡을 새로 만들지만, 여기서는 실 라우트 흐름을
    끝까지 태우는 게 목적이라 그 잡을 재사용한다)."""
    fm = _verify_facemarket_identity(c, headers, monkeypatch, birth=birth)
    assert fm.status_code == 200, fm.text
    model = _fetch_fm_model(user_id)
    assert model is not None
    consent = _grant_required_consents(c, headers)
    assert consent.status_code == 200, consent.text
    withdraw = c.post("/v1/personalization:withdraw", headers=headers)
    assert withdraw.status_code == 202, withdraw.text
    profile_id = _fetch_profile_id(user_id)
    job = _fetch_job(user_id, "personalization_purge")
    assert job is not None and job["status"] == "pending"
    lease_token = _claim_job_for_worker(job["id"])
    asyncio.run(run_personalization_purge_job(
        _purge_app(r2),
        {"id": job["id"], "user_id": user_id, "lease_token": lease_token,
         "payload": {"profileId": profile_id}},
    ))
    return {"model_id": model["id"], "profile_id": profile_id}


# ============================================================================
# A) US-L1-1 — FaceMarket 인증이 개인화 연령 소스로 인정(개인화 자체 인증 없이도 폴백)
# ============================================================================
def test_fm_verified_adult_permits_personalization_consent_without_own_verification(
    client, raw_headers, raw_uid, fm_cleanup
):
    c, _r2 = client
    model_id = _seed_fm_model(raw_uid)
    fm_cleanup.append(model_id)
    _seed_fm_identity_verification(model_id, birth_year=_ADULT_BIRTH[:4])

    r = _grant_required_consents(c, raw_headers)  # 200 아니면 헬퍼가 내부에서 assert
    assert r.status_code == 200, r.text


def test_fm_verified_adult_removes_identity_blocker_from_status(
    client, raw_headers, raw_uid, fm_cleanup
):
    c, _r2 = client
    model_id = _seed_fm_model(raw_uid)
    fm_cleanup.append(model_id)
    _seed_fm_identity_verification(model_id, birth_year=_ADULT_BIRTH[:4])

    r = c.get("/v1/personalization/status", headers=raw_headers)
    assert r.status_code == 200, r.text
    codes = {b["code"] for b in r.json()["blockers"]}
    assert "identity_verification_required" not in codes


def test_fm_identity_without_birth_year_yields_age_unavailable_not_verification_required(
    client, raw_headers, raw_uid, fm_cleanup
):
    """본인확인은 했는데 연령 파생 불가 → identity_age_unavailable(종결), 재인증 유도 금지.

    identity_verification_required 를 내면 프론트가 /model/register 로 되돌리고, 재인증해도
    birthYear 는 여전히 없어 무한 왕복이 된다. blocker 를 분리해 루프를 끊는다.
    """
    c, _r2 = client
    model_id = _seed_fm_model(raw_uid)
    fm_cleanup.append(model_id)
    _seed_fm_identity_verification(model_id, birth_year=None)

    r = c.get("/v1/personalization/status", headers=raw_headers)
    assert r.status_code == 200, r.text
    codes = {b["code"] for b in r.json()["blockers"]}
    assert "identity_age_unavailable" in codes
    assert "identity_verification_required" not in codes  # 재인증 유도 금지(루프 방지)


def test_no_identity_record_at_all_yields_verification_required_not_age_unavailable(
    client, raw_headers, raw_uid
):
    """인증 자체가 없으면 조치 가능한 identity_verification_required(= /model/register 로 유도)."""
    c, _r2 = client
    r = c.get("/v1/personalization/status", headers=raw_headers)
    assert r.status_code == 200, r.text
    codes = {b["code"] for b in r.json()["blockers"]}
    assert "identity_verification_required" in codes
    assert "identity_age_unavailable" not in codes


def test_fm_identity_verification_without_birth_year_field_not_recognized_as_age_source(
    client, raw_headers, raw_uid, fm_cleanup
):
    """birthYear 부재 → 연령 소스 미인정. 단 코드는 age_unavailable(종결)이지 재인증 유도가 아니다."""
    c, _r2 = client
    model_id = _seed_fm_model(raw_uid)
    fm_cleanup.append(model_id)
    _seed_fm_identity_verification(model_id, birth_year=None)  # fields = {} (키 자체가 없음)

    r = c.post(
        "/v1/personalization/consents",
        json={"items": [{"type": "service_use", "docVersion": CONSENT_DOC_VERSION}]},
        headers=raw_headers,
    )
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "identity_age_unavailable"


def test_fm_identity_verification_unparsable_birth_year_not_recognized_as_age_source(
    client, raw_headers, raw_uid, fm_cleanup
):
    c, _r2 = client
    model_id = _seed_fm_model(raw_uid)
    fm_cleanup.append(model_id)
    _seed_fm_identity_verification(model_id, birth_year="abc")

    r = c.post(
        "/v1/personalization/consents",
        json={"items": [{"type": "service_use", "docVersion": CONSENT_DOC_VERSION}]},
        headers=raw_headers,
    )
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "identity_age_unavailable"


def test_facemarket_verify_survives_db_exception_in_personalization_record(
    fm_client, raw_headers, raw_uid, monkeypatch, fm_cleanup
):
    """개인화 기록에서 **실제 DB 예외**가 나도 FaceMarket 등록은 확정된 채 유지된다.

    무회귀를 지키는 건 `facemarket._record_personalization_adult` 의 except→rollback 분기다.
    그 함수를 통째로 monkeypatch 로 대체하면 정작 검증하려던 그 분기가 안 돌아가므로(가짜 통과),
    **실제 함수는 그대로 두고 그 안의 insert 가 raise 하도록** 만든다 — is_adult 자리에 psycopg 가
    어댑트할 수 없는 값을 넣으면 `cur.execute` 가 실패해 진짜 except/rollback 경로를 탄다.
    """
    c, _r2 = fm_client
    from app import cx_identity as cx_mod
    from app import facemarket as fm_mod

    class _Unadaptable:  # psycopg 가 파라미터로 어댑트 못 함 → 실제 insert 가 raise
        pass

    monkeypatch.setattr(cx_mod, "is_adult_from_birth", lambda *_a, **_k: _Unadaptable())

    async def _fake_trans(base_url, tok):
        return {"ci": f"ci-{uuid.uuid4()}", "utf8Nm": "홍길동", "birth": _ADULT_BIRTH}

    monkeypatch.setattr(fm_mod, "_fetch_trans", _fake_trans)

    r = c.post(
        "/v1/facemarket/identity/verify",
        json={"token": f"tok-{uuid.uuid4()}"},
        headers=raw_headers,
    )
    assert r.status_code == 200, r.text  # FaceMarket 은 개인화 기록 실패와 무관하게 성공
    assert r.json()["verified"] is True
    model_id = r.json()["modelId"]
    fm_cleanup.append(model_id)

    conn = _sync_conn()
    row = conn.execute(
        "select status from fm_models where id = %s", (model_id,)
    ).fetchone()
    conn.close()
    assert row is not None and row["status"] == "verified"  # 등록이 롤백되지 않았음
    assert _fetch_identity_verifications(raw_uid) == []  # 개인화 기록은 rollback 됨(실패 경로 확인)


def test_gate_and_status_agree_on_age_code_for_same_user_state(
    client, raw_headers, raw_uid, fm_cleanup
):
    """게이트(403)와 /status(blocker)가 **같은 코드**를 낸다 — 갈리면 무한 왕복이 재현된다.

    종결 상태(age_unavailable)에 게이트만 '재인증하세요'(identity_verification_required)를 내면,
    프론트가 그 코드를 보고 /model/register 로 되돌려 _age_blocker 가 막으려던 루프가 살아난다.
    """
    c, _r2 = client
    model_id = _seed_fm_model(raw_uid)
    fm_cleanup.append(model_id)
    _seed_fm_identity_verification(model_id, birth_year=None)

    gate = c.post(
        "/v1/personalization/consents",
        json={"items": [{"type": "service_use", "docVersion": CONSENT_DOC_VERSION}]},
        headers=raw_headers,
    )
    status = c.get("/v1/personalization/status", headers=raw_headers)
    assert gate.status_code == 403, gate.text
    status_codes = {b["code"] for b in status.json()["blockers"]}
    assert gate.json()["error"]["code"] in status_codes  # 게이트 코드가 status blocker 에 존재


def test_birth_year_out_of_range_is_not_adult(client):
    """'0101'(MMDD 절단) 같은 값이 year=101 로 읽혀 성인 통과하지 않는다 — CX 스키마 드리프트 방어."""
    from datetime import date

    from app.cx_identity import CxIdentityError, is_adult_from_birth

    # 4자리(연도만) 경로 + 8자리 경로 **양쪽** — 8자리도 막지 않으면 'MMDDHHMM'(01011225)이
    # year=101 로 읽혀 성인 오통과한다.
    for bad in ("0101", "0", "9999", "01011225", "00010101"):
        with pytest.raises(CxIdentityError):
            is_adult_from_birth(bad, today=date(2026, 7, 15))
    # 정상 값은 오차단되지 않는다(가드가 과잉이 아님을 확인)
    assert is_adult_from_birth("19900101", today=date(2026, 7, 15)) is True
    assert is_adult_from_birth("2006", today=date(2026, 7, 15)) is True


def test_fm_birth_year_boundary_19_year_gap_is_conservatively_minor_blocked(
    client, raw_headers, raw_uid, fm_cleanup
):
    """생일 미상(연도만) 폴백도 개인화 자체 인증과 동일한 보수 판정을 따른다 — 연도차 19(경계)는
    미성년 취급(cx_identity.is_adult_from_birth 의 min_age+1 규칙, personalization.py:159)."""
    c, _r2 = client
    model_id = _seed_fm_model(raw_uid)
    fm_cleanup.append(model_id)
    boundary_year = f"{date.today().year - 19:04d}"
    _seed_fm_identity_verification(model_id, birth_year=boundary_year)

    r = c.post(
        "/v1/personalization/consents",
        json={"items": [{"type": "service_use", "docVersion": CONSENT_DOC_VERSION}]},
        headers=raw_headers,
    )
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "minor_blocked"


def test_fm_birth_year_20_year_gap_passes_conservative_adult_check(
    client, raw_headers, raw_uid, fm_cleanup
):
    c, _r2 = client
    model_id = _seed_fm_model(raw_uid)
    fm_cleanup.append(model_id)
    adult_year = f"{date.today().year - 20:04d}"
    _seed_fm_identity_verification(model_id, birth_year=adult_year)

    r = _grant_required_consents(c, raw_headers)
    assert r.status_code == 200, r.text


def test_own_personalization_identity_verification_takes_priority_over_fm_fallback(
    client, raw_headers, raw_uid, fm_cleanup
):
    """개인화 자체 인증이 있으면(설령 미성년이라도) FaceMarket 폴백보다 우선한다
    (personalization.py:194-212 `_load_age_verification` — own 이 있으면 fm 폴백을 조회조차
    안 함)."""
    c, _r2 = client
    conn = _sync_conn()
    conn.execute(
        "insert into personalization_identity_verifications (user_id, cx_tx_hash, is_adult) "
        "values (%s, %s, false)",
        (raw_uid, f"own-minor-{uuid.uuid4()}"),
    )
    conn.close()
    model_id = _seed_fm_model(raw_uid)
    fm_cleanup.append(model_id)
    _seed_fm_identity_verification(model_id, birth_year=_ADULT_BIRTH[:4])  # FM 은 성인

    r = c.post(
        "/v1/personalization/consents",
        json={"items": [{"type": "service_use", "docVersion": CONSENT_DOC_VERSION}]},
        headers=raw_headers,
    )
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "minor_blocked"  # 개인화 자체(미성년)가 이김


# ============================================================================
# B) US-L1-2 — CX 1회로 개인화 성인 인증 동시 성립 (facemarket.identity_verify Level 1)
# ============================================================================
def test_facemarket_identity_verify_adult_creates_personalization_adult_row(
    fm_client, raw_headers, raw_uid, monkeypatch, fm_cleanup
):
    c, _r2 = fm_client
    r = _verify_facemarket_identity(c, raw_headers, monkeypatch, birth=_ADULT_BIRTH)
    assert r.status_code == 200, r.text
    model = _fetch_fm_model(raw_uid)
    fm_cleanup.append(model["id"])

    rows = _fetch_identity_verifications(raw_uid)
    assert len(rows) == 1
    assert rows[0]["is_adult"] is True


def test_after_facemarket_verify_personalization_consent_succeeds_without_reverification(
    fm_client, raw_headers, raw_uid, monkeypatch, fm_cleanup
):
    c, _r2 = fm_client
    ok = _verify_facemarket_identity(c, raw_headers, monkeypatch, birth=_ADULT_BIRTH)
    assert ok.status_code == 200, ok.text
    model = _fetch_fm_model(raw_uid)
    fm_cleanup.append(model["id"])

    r = _grant_required_consents(c, raw_headers)  # 재인증 없이 바로 통과해야 함
    assert r.status_code == 200, r.text


def test_facemarket_verify_personalization_row_excludes_pii_and_hashes_token(
    fm_client, raw_headers, raw_uid, monkeypatch, fm_cleanup
):
    """PII 최소수집 — 생성된 개인화 행에 생년월일·CI·이름 없음(is_adult 불리언뿐) +
    cx_tx_hash 는 원본 토큰의 sha256(원문 재취득 불가)."""
    c, _r2 = fm_client
    token = f"fm-tok-{uuid.uuid4()}"
    r = _verify_facemarket_identity(c, raw_headers, monkeypatch, birth=_ADULT_BIRTH, token=token)
    assert r.status_code == 200, r.text
    model = _fetch_fm_model(raw_uid)
    fm_cleanup.append(model["id"])

    rows = _fetch_identity_verifications(raw_uid)
    assert len(rows) == 1
    row = rows[0]
    assert set(row.keys()) == {"cx_tx_hash", "is_adult"}
    assert row["cx_tx_hash"] != token
    assert row["cx_tx_hash"] == hashlib.sha256(token.encode()).hexdigest()


def test_facemarket_verify_succeeds_and_skips_personalization_record_when_birth_unparsable(
    fm_client, raw_headers, raw_uid, monkeypatch, fm_cleanup
):
    """무회귀 핵심: birth 가 파싱 불가(cx_identity.is_adult_from_birth 가 CxIdentityError)라
    개인화 기록이 스킵돼도 FaceMarket 모델 등록·응답은 그대로 200 이어야 한다."""
    c, _r2 = fm_client
    r = _verify_facemarket_identity(c, raw_headers, monkeypatch, birth="not-a-birth-value")
    assert r.status_code == 200, r.text
    assert r.json()["verified"] is True
    model = _fetch_fm_model(raw_uid)
    fm_cleanup.append(model["id"])
    assert model["status"] == "verified"
    assert _fetch_identity_verifications(raw_uid) == []  # 개인화 행은 생성되지 않음


def test_facemarket_verify_succeeds_when_personalization_cx_tx_hash_already_taken(
    fm_client, raw_headers, raw_uid, monkeypatch, fm_cleanup
):
    """무회귀: 개인화 쪽 cx_tx_hash 가 이미 선점돼 있어도(on conflict do nothing) FaceMarket
    등록은 정상 200 이고, 개인화 쪽은 기존 행을 덮어쓰지 않는다(중복 삽입 없음)."""
    c, _r2 = fm_client
    token = f"fm-tok-{uuid.uuid4()}"
    cx_tx_hash = hashlib.sha256(token.encode()).hexdigest()
    conn = _sync_conn()
    conn.execute(
        "insert into personalization_identity_verifications (user_id, cx_tx_hash, is_adult) "
        "values (%s, %s, %s)",
        (raw_uid, cx_tx_hash, False),
    )
    conn.close()

    r = _verify_facemarket_identity(c, raw_headers, monkeypatch, birth=_ADULT_BIRTH, token=token)
    assert r.status_code == 200, r.text
    model = _fetch_fm_model(raw_uid)
    fm_cleanup.append(model["id"])
    assert model["status"] == "verified"

    rows = _fetch_identity_verifications(raw_uid)
    assert len(rows) == 1  # 중복 삽입 안 됨
    assert rows[0]["is_adult"] is False  # 기존 행 그대로(덮어쓰지 않음)


def test_facemarket_verify_with_personalization_disabled_skips_record_but_succeeds(
    fm_only_client, raw_headers, raw_uid, monkeypatch, fm_cleanup
):
    """settings.personalization_enabled=False 면 Level 1 훅 자체가 안 돈다(facemarket.py:233
    `if settings.personalization_enabled:`) — FaceMarket 등록은 정상, 개인화 행은 미생성."""
    c = fm_only_client
    r = _verify_facemarket_identity(c, raw_headers, monkeypatch, birth=_ADULT_BIRTH)
    assert r.status_code == 200, r.text
    model = _fetch_fm_model(raw_uid)
    fm_cleanup.append(model["id"])
    assert model["status"] == "verified"
    assert _fetch_identity_verifications(raw_uid) == []


# ============================================================================
# C) US-L1-3 — 파기 격리 (CRITICAL: 개인화 철회가 FaceMarket 데이터를 날리면 해커톤 기능이 죽는다)
# ============================================================================
def test_withdraw_purge_preserves_facemarket_records_but_deletes_personalization_identity(
    fm_client, raw_headers, raw_uid, monkeypatch, fm_cleanup
):
    """CRITICAL: 파기 캐스케이드(personalization_purge_job.py:219-224)는
    personalization_identity_verifications 만 지워야 하고 fm_models·fm_identity_verifications
    는 절대 건드리면 안 된다 — 이 테스트가 그 격리를 회귀로 고정한다."""
    c, r2 = fm_client
    ctx = _run_facemarket_verify_consent_withdraw_and_purge(
        c, r2, raw_headers, raw_uid, monkeypatch
    )
    fm_cleanup.append(ctx["model_id"])

    model_after = _fetch_fm_model(raw_uid)
    assert model_after is not None and model_after["id"] == ctx["model_id"]
    assert len(_fetch_fm_identity_verifications(ctx["model_id"])) == 1  # FaceMarket 생존

    assert _fetch_identity_verifications(raw_uid) == []  # 개인화 자체 인증행은 삭제(4d)


def test_withdraw_purge_age_gate_passes_via_facemarket_fallback_but_upload_blocked_by_consent(
    fm_client, raw_headers, raw_uid, monkeypatch, fm_cleanup
):
    """설계 결정(연령=사실, 동의≠연령): 파기 후에도 FaceMarket 인증이 살아있어 연령 게이트는
    폴백으로 통과하지만, 필수 동의는 파기 시 withdrawn 으로 기록돼(MAJOR-D) 얼굴 업로드는
    여전히 403 consent_required 로 막힌다."""
    c, r2 = fm_client
    ctx = _run_facemarket_verify_consent_withdraw_and_purge(
        c, r2, raw_headers, raw_uid, monkeypatch
    )
    fm_cleanup.append(ctx["model_id"])

    status = c.get("/v1/personalization/status", headers=raw_headers)
    assert status.status_code == 200, status.text
    codes = {b["code"] for b in status.json()["blockers"]}
    assert "identity_verification_required" not in codes
    assert "minor_blocked" not in codes

    monkeypatch.setattr(personalization, "evaluate_face_qc", _qc_stub("pass"))
    upload = c.post(
        "/v1/personalization/face-photos",
        files={"photo": ("front.png", PNG_BYTES, "image/png")},
        data={"angle": "front"},
        headers=raw_headers,
    )
    assert upload.status_code == 403, upload.text
    assert upload.json()["error"]["code"] == "consent_required"
