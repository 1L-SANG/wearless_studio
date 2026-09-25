"""협찬 동의 VC 증서(fm_sponsorship_credentials)를 **실제 Postgres** 에서 검사한다.

부분 유니크 인덱스·on conflict 추론·폐기 큐 kind 제약·상태 전이는 흉내 DB 로는 못 잡는다.
마이그레이션이 적용된 로컬 Supabase DB 에 행을 심고, 한 트랜잭션 안에서 끝낸 뒤 되돌린다.
CI 는 test-db 잡이 돌린다(.github/workflows/deploy-server.yml). 로컬에 DB 가 없으면 건너뛴다.
"""

import asyncio
import contextlib
import hashlib
import json
import os
import uuid

import psycopg
import pytest
from psycopg.rows import dict_row

from app import facemarket_sponsorship_vc as svc

DB_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
)


def _db_reachable() -> bool:
    try:
        with psycopg.connect(DB_URL, connect_timeout=3):
            return True
    except psycopg.OperationalError:
        return False


if not _db_reachable():
    if os.environ.get("CI"):
        pytest.fail(f"테스트 DB({DB_URL})에 붙지 못했어요.", pytrace=False)
    pytest.skip("로컬 Supabase DB 가 없어 실제 DB 검사를 건너뛰어요.", allow_module_level=True)


class _OneConnPool:
    """pool.connection() 이 테스트 트랜잭션 연결을 그대로 돌려주게 — commit 은 no-op."""

    def __init__(self, conn):
        self._conn = _NoCommit(conn)

    @contextlib.asynccontextmanager
    async def connection(self):
        yield self._conn


class _NoCommit:
    def __init__(self, conn):
        self._conn = conn

    def cursor(self, *a, **k):
        return self._conn.cursor(*a, **k)

    async def commit(self):
        return None


async def _seed_model(conn, *, did: str | None = "did:omn:test-model"):
    user_id, model_id = str(uuid.uuid4()), str(uuid.uuid4())
    await conn.execute("insert into auth.users (id) values (%s)", (user_id,))
    await conn.execute(
        """insert into fm_models (id, user_id, display_name, status, did)
           values (%s, %s, '검*모', 'verified', %s)""",
        (model_id, user_id, did),
    )
    return user_id, model_id


async def _grant(conn, user_id, model_id):
    key = str(uuid.uuid4())
    for kind in ("sponsorship_participation", "sponsorship_profile_collection"):
        snapshot = json.dumps({"kind": kind})
        await conn.execute(
            """insert into fm_sponsorship_consent_events
                   (user_id, model_id, actor_user_id, consent_type, action, doc_version,
                    document_sha256, screen_id, notice_snapshot, idempotency_key, reason)
               values (%s, %s, %s, %s, 'granted', '2026-09-sponsorship-v1', %s,
                       'sponsorship_settings', %s::jsonb, %s, 'model_toggle')""",
            (user_id, model_id, user_id, kind, hashlib.sha256(kind.encode()).hexdigest(),
             snapshot, key),
        )
    return key


async def _gate(conn, model_id) -> bool:
    async with conn.cursor() as cur:
        await cur.execute(
            f"select {svc.ACTIVE_CREDENTIAL_SQL} as active from fm_models m where m.id = %s",
            (model_id,))
        return (await cur.fetchone())["active"]


async def _public(conn, model_id):
    """운영 공개 검증 SQL(PUBLIC_VERIFY_SQL)을 그대로 돌린다 — 라이선스 행을 하나 심는다."""
    from app.facemarket import PUBLIC_VERIFY_SQL
    license_id = str(uuid.uuid4())
    await conn.execute(
        "insert into fm_licenses (id, model_id, face_image_uri, status) values (%s, %s, %s, 'active')",
        (license_id, model_id, "https://example.invalid/face"),
    )
    async with conn.cursor() as cur:
        await cur.execute(PUBLIC_VERIFY_SQL, (license_id,))
        row = await cur.fetchone()
    return svc.public_sponsorship(row, license_valid=True)


def _run(scenario):
    async def main():
        async with await psycopg.AsyncConnection.connect(DB_URL, row_factory=dict_row) as conn:
            try:
                return await scenario(conn)
            finally:
                await conn.rollback()
    return asyncio.run(main())


def test_open_copies_consent_evidence_and_only_one_open_credential():
    async def scenario(conn):
        user_id, model_id = await _seed_model(conn)
        key = await _grant(conn, user_id, model_id)
        async with conn.cursor() as cur:
            first = await svc.open_credential(cur, model_id=model_id, user_id=user_id)
            second = await svc.open_credential(cur, model_id=model_id, user_id=user_id)
            await cur.execute("select * from fm_sponsorship_credentials where model_id = %s", (model_id,))
            rows = await cur.fetchall()
        return first, second, rows, key

    first, second, rows, key = _run(scenario)
    assert first is not None and second is None
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "pending"
    assert str(row["consent_event_key"]) == key
    assert row["consent_doc_version"] == "2026-09-sponsorship-v1"
    assert row["participation_doc_sha256"] == hashlib.sha256(b"sponsorship_participation").hexdigest()
    assert row["profile_doc_sha256"] == hashlib.sha256(b"sponsorship_profile_collection").hexdigest()


def test_status_waits_for_license_did_then_issue_activates_and_gates_open():
    async def scenario(conn):
        user_id, model_id = await _seed_model(conn, did=None)
        await _grant(conn, user_id, model_id)
        pool = _OneConnPool(conn)
        async with conn.cursor() as cur:
            await svc.open_credential(cur, model_id=model_id, user_id=user_id)
        waiting = await svc.owner_status(conn, model_id)
        not_claimed = [r for r in await svc.claim_pending(pool) if r["model_id"] == model_id]
        await conn.execute("update fm_models set did = 'did:omn:x' where id = %s", (model_id,))
        await conn.execute(
            "update fm_sponsorship_credentials set next_attempt_at = now() where model_id = %s",
            (model_id,),
        )
        pending = await svc.owner_status(conn, model_id)
        claimed = [r for r in await svc.claim_pending(pool) if r["model_id"] == model_id]
        activated = await svc.record_issued(pool, claimed[0], "vc-sponsor-1")
        return (waiting, not_claimed, pending, claimed, activated,
                await svc.owner_status(conn, model_id),
                await _gate(conn, model_id),
                await _public(conn, model_id))

    waiting, not_claimed, pending, claimed, activated, active, gate, public = _run(scenario)
    assert waiting["status"] == "waiting_license" and not_claimed == []
    assert pending["status"] == "pending" and len(claimed) == 1
    assert activated is True
    assert active["status"] == "active" and active["vc_id"] == "vc-sponsor-1"
    assert gate is True
    assert public["active"] is True and public["vc_id"] == "vc-sponsor-1"
    assert set(public) == {"active", "vc_id", "consented_on", "consent_doc_version"}
    assert public["consent_doc_version"] == "2026-09-sponsorship-v1"


def test_close_revokes_and_enqueues_sponsorship_revocation():
    async def scenario(conn):
        user_id, model_id = await _seed_model(conn)
        await _grant(conn, user_id, model_id)
        pool = _OneConnPool(conn)
        async with conn.cursor() as cur:
            await svc.open_credential(cur, model_id=model_id, user_id=user_id)
        [row] = [r for r in await svc.claim_pending(pool) if r["model_id"] == model_id]
        await svc.record_issued(pool, row, f"vc-{model_id}")
        async with conn.cursor() as cur:
            closed = await svc.close_open_credentials(cur, [model_id])
            await cur.execute(
                "select kind, license_id, vc_id, status from fm_vc_revocation_jobs where model_id = %s",
                (model_id,),
            )
            jobs = await cur.fetchall()
        return (closed, jobs, await _gate(conn, model_id),
                await _public(conn, model_id), await svc.owner_status(conn, model_id))

    closed, jobs, gate, public, status = _run(scenario)
    assert closed == 1
    assert len(jobs) == 1 and jobs[0]["kind"] == "sponsorship" and jobs[0]["license_id"] is None
    assert jobs[0]["status"] == "pending"
    assert gate is False and public is None and status["status"] == "none"


def test_revoked_while_issuing_does_not_activate_and_queues_the_new_vc():
    async def scenario(conn):
        user_id, model_id = await _seed_model(conn)
        await _grant(conn, user_id, model_id)
        pool = _OneConnPool(conn)
        async with conn.cursor() as cur:
            await svc.open_credential(cur, model_id=model_id, user_id=user_id)
        [row] = [r for r in await svc.claim_pending(pool) if r["model_id"] == model_id]
        async with conn.cursor() as cur:
            closed = await svc.close_open_credentials(cur, [model_id])  # pending → 폐기할 VC 없음
        activated = await svc.record_issued(pool, row, f"late-{model_id}")
        async with conn.cursor() as cur:
            await cur.execute(
                "select vc_id from fm_vc_revocation_jobs where model_id = %s", (model_id,))
            jobs = await cur.fetchall()
            await cur.execute(
                "select status from fm_sponsorship_credentials where model_id = %s", (model_id,))
            states = [r["status"] for r in await cur.fetchall()]
        return closed, activated, jobs, states

    closed, activated, jobs, states = _run(scenario)
    assert closed == 1 and activated is False
    assert [j["vc_id"] for j in jobs] == [jobs[0]["vc_id"]] and jobs[0]["vc_id"].startswith("late-")
    assert states == ["revoked"]


def test_failure_backs_off_and_retoggle_opens_a_new_credential():
    async def scenario(conn):
        user_id, model_id = await _seed_model(conn)
        await _grant(conn, user_id, model_id)
        pool = _OneConnPool(conn)
        async with conn.cursor() as cur:
            await svc.open_credential(cur, model_id=model_id, user_id=user_id)
        [row] = [r for r in await svc.claim_pending(pool) if r["model_id"] == model_id]
        await svc.record_failure(pool, row, "transport")
        async with conn.cursor() as cur:
            await cur.execute(
                """select attempts, last_error_code, next_attempt_at > now() as later
                     from fm_sponsorship_credentials where model_id = %s""", (model_id,))
            failed = await cur.fetchone()
            await svc.close_open_credentials(cur, [model_id])
            await _grant(conn, user_id, model_id)
            reopened = await svc.open_credential(cur, model_id=model_id, user_id=user_id)
            await cur.execute(
                "select status from fm_sponsorship_credentials where model_id = %s order by created_at",
                (model_id,))
            states = [r["status"] for r in await cur.fetchall()]
        return failed, reopened, states

    failed, reopened, states = _run(scenario)
    assert failed["attempts"] == 1 and failed["last_error_code"] == "transport" and failed["later"]
    assert reopened is not None
    assert sorted(states) == ["pending", "revoked"]


def test_license_revocation_jobs_still_require_license_id():
    async def scenario(conn):
        _user_id, model_id = await _seed_model(conn)
        with pytest.raises(psycopg.errors.CheckViolation):
            async with conn.transaction():
                await conn.execute(
                    """insert into fm_vc_revocation_jobs (kind, license_id, model_id, vc_id)
                       values ('license', null, %s, 'vc-no-license')""",
                    (model_id,),
                )
        return True

    assert _run(scenario)


def test_duplicate_issue_by_another_instance_does_not_revoke_the_active_vc():
    """두 인스턴스가 같은 행을 발급(같은 멱등키 → 같은 vc_id): 늦게 온 쪽이 폐기 큐에 넣으면 안 된다."""
    async def scenario(conn):
        user_id, model_id = await _seed_model(conn)
        await _grant(conn, user_id, model_id)
        pool = _OneConnPool(conn)
        async with conn.cursor() as cur:
            await svc.open_credential(cur, model_id=model_id, user_id=user_id)
        [row] = [r for r in await svc.claim_pending(pool) if r["model_id"] == model_id]
        first = await svc.record_issued(pool, row, f"same-{model_id}")
        second = await svc.record_issued(pool, row, f"same-{model_id}")
        async with conn.cursor() as cur:
            await cur.execute("select count(*)::int as n from fm_vc_revocation_jobs where model_id = %s",
                              (model_id,))
            jobs = (await cur.fetchone())["n"]
        return first, second, jobs, await _gate(conn, model_id)

    first, second, jobs, gate = _run(scenario)
    assert first is True and second is False
    assert jobs == 0 and gate is True


def test_claim_stops_after_max_attempts():
    async def scenario(conn):
        user_id, model_id = await _seed_model(conn)
        await _grant(conn, user_id, model_id)
        async with conn.cursor() as cur:
            await svc.open_credential(cur, model_id=model_id, user_id=user_id)
        await conn.execute(
            "update fm_sponsorship_credentials set attempts = %s where model_id = %s",
            (svc.MAX_ATTEMPTS, model_id))
        return [r for r in await svc.claim_pending(_OneConnPool(conn)) if r["model_id"] == model_id]

    assert _run(scenario) == []


def test_backfill_opens_credential_for_models_enabled_before_the_switch():
    async def scenario(conn):
        user_id, model_id = await _seed_model(conn)
        await _grant(conn, user_id, model_id)
        await conn.execute(
            """update fm_models set sponsorship_enabled = true, instagram_handle = 'a',
                      instagram_followers = 1, instagram_followers_reported_at = now(), size_top = 'M', size_bottom_waist = 28,
                      sponsorship_profile_consent_at = now()
                where id = %s""", (model_id,))
        pool = _OneConnPool(conn)
        await svc.backfill_missing(pool, limit=100000)
        await svc.backfill_missing(pool, limit=100000)  # 두 번 돌아도 하나
        async with conn.cursor() as cur:
            await cur.execute(
                "select status from fm_sponsorship_credentials where model_id = %s", (model_id,))
            return [r["status"] for r in await cur.fetchall()]

    assert _run(scenario) == ["pending"]


def test_public_sponsorship_hidden_when_license_invalid_and_date_is_kst():
    from datetime import date, datetime, timezone
    row = {"sponsorship_vc_id": "vc-1", "sponsorship_consent_doc_version": "v1",
           "sponsorship_consented_at": datetime(2026, 9, 25, 16, 30, tzinfo=timezone.utc)}
    assert svc.public_sponsorship(row, license_valid=False) is None
    assert svc.public_sponsorship(row, license_valid=True)["consented_on"] == date(2026, 9, 26)
