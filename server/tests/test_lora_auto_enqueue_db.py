"""인물 LoRA 자동 큐잉 — 조건 SQL 을 **실제 Postgres** 에서 검사한다(2026-09-28).

한 건이 GPU 파드 3~4시간·약 $11 이다. 그래서 여기서 지키는 건 "넣어야 할 때 넣는다" 보다
"**넣지 말아야 할 때 절대 안 넣는다**" 쪽이다.
  · 준비된 등록(증서 발급 + 사진 확인 + 라이선스 활성)만 넣는다. 한 등록에 **한 번**.
  · 정지·재인증 대기·라이선스 해지·사진 확인 전·심사 대기(정*상 같은 경우)·옛 등록은 안 넣는다.
  · ready LoRA 가 이미 있는 모델(노*운 — 손으로 seed 한 v1)은 안 넣는다.
  · 실패한 런이 있어도 다시 넣지 않는다 — 같은 이유로 $11 씩 계속 탄다.
  · 손으로 넣은 런(등록 칸이 비어도)과 겹치지 않는다.
흉내 DB 로는 술어·동시성 잠금·인덱스를 못 잡는다. 로컬 Supabase DB 에서 한 트랜잭션 안에서
끝내고 되돌린다. CI 는 test-db 잡이 돌린다.
"""

import asyncio
import os
import uuid
from urllib.parse import urlparse

import psycopg
import pytest
from psycopg.rows import dict_row

from app.workers import lora_training_reconciler as ltr

DB_URL = os.environ.get("FM_LORA_TEST_DATABASE_URL",
                        "postgresql://postgres:postgres@127.0.0.1:54322/postgres")

# server/.env 의 DATABASE_URL 은 운영 DB 를 가리킨 적이 있다 — 이 파일은 로컬 호스트에만 붙는다.
assert urlparse(DB_URL).hostname in ("127.0.0.1", "localhost"), "로컬 DB 에서만 돈다"


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


class _NoCommit:
    """헬퍼가 commit/rollback 을 불러도 테스트 트랜잭션이 끝나지 않게 — 끝에서 통째로 되돌린다."""

    def __init__(self, conn):
        self._conn = conn

    def cursor(self, *a, **k):
        return self._conn.cursor(*a, **k)

    async def commit(self):
        return None

    async def rollback(self):
        return None


def _run(body):
    async def main():
        conn = await psycopg.AsyncConnection.connect(DB_URL, row_factory=dict_row)
        try:
            await body(conn, _NoCommit(conn))
        finally:
            await conn.rollback()
            await conn.close()

    asyncio.run(main())


async def _one(conn, sql, params=()):
    async with conn.cursor() as cur:
        await cur.execute(sql, params)
        return await cur.fetchone()


async def _seed(conn, *, model_status="pending", enrollment_status="passed",
                decision="passed", review_status=None, photo_review="approved",
                license_status="active", vc_id="vc-1", license_until="now() + interval '1 year'",
                current=True, gender="female") -> dict:
    """준비가 끝난 모델 한 명(기본값). 인자로 한 곳씩 깨뜨려 본다."""
    model = await _one(conn, "insert into fm_models (display_name, status, gender) "
                             "values (%s, %s, %s) returning id::text as id",
                       (f"t-{uuid.uuid4().hex[:6]}", model_status, gender))
    enrollment = await _one(
        conn,
        """insert into fm_biometric_enrollments
             (model_id, device_digest, consent_version, status, decision, review_status,
              photo_review_status, completed_at)
           values (%s, %s, 'v3', %s, %s, %s, %s, now())
           returning id::text as id""",
        (model["id"], uuid.uuid4().hex, enrollment_status, decision, review_status, photo_review))
    await _one(
        conn,
        f"""insert into fm_licenses (model_id, enrollment_id, face_image_uri, face_image_digest,
                                     license_valid_until, status, vc_id)
            values (%s, %s, 'gate://face', 'sha256-x', {license_until}, %s, %s)
            returning id""",
        (model["id"], enrollment["id"], license_status, vc_id))
    if current:
        await _one(conn, "update fm_models set current_enrollment_id = %s where id = %s "
                         "returning id", (enrollment["id"], model["id"]))
    return {"model": model["id"], "enrollment": enrollment["id"]}


async def _sweep(nc) -> list[dict]:
    # 로컬 DB 에 다른 개발 행이 있어도 이 테스트의 행이 잘리지 않게 상한을 넉넉히.
    return await ltr.enqueue_ready(nc, limit=10_000)


def _mine(rows, seeded) -> list[dict]:
    return [row for row in rows if row["model_id"] == seeded["model"]]


async def _runs(conn, model_id) -> list[dict]:
    async with conn.cursor() as cur:
        await cur.execute("select status, enrollment_id::text as enrollment_id, attempts "
                          "from fm_lora_training_runs where model_id = %s", (model_id,))
        return await cur.fetchall()


# ── 넣는다 ─────────────────────────────────────────────────────────────────
def test_a_ready_enrollment_is_queued_once():
    """증서 발급·사진 확인·라이선스 활성 — 이 셋이 갖춰지면 다음 회차에 큐에 들어간다(정*정)."""
    async def body(conn, nc):
        seeded = await _seed(conn)
        first = _mine(await _sweep(nc), seeded)
        assert [row["enrollment_id"] for row in first] == [seeded["enrollment"]]
        # ★ 두 번째 회차는 아무것도 더 넣지 않는다 — 배포·재기동마다 $11 이 늘면 안 된다.
        assert _mine(await _sweep(nc), seeded) == []
        runs = await _runs(conn, seeded["model"])
        assert [(r["status"], r["enrollment_id"], r["attempts"]) for r in runs] == [
            ("queued", seeded["enrollment"], 0)]

    _run(body)


def test_the_identity_review_path_queues_only_after_it_passes():
    """정*상 — 신원 심사 대기(vc_pending·pending) 동안은 안 넣고, 증서까지 끝나면 넣는다."""
    async def body(conn, nc):
        seeded = await _seed(conn, enrollment_status="vc_pending", decision="passed",
                             review_status="pending", license_status="pending", vc_id=None)
        assert _mine(await _sweep(nc), seeded) == []
        # 심사 승인 → VC 발급 → 등록 passed · 라이선스 active
        await _one(conn, "update fm_biometric_enrollments set review_status = 'approved', "
                         "status = 'passed' where id = %s returning id", (seeded["enrollment"],))
        await _one(conn, "update fm_licenses set status = 'active', vc_id = 'vc-2' "
                         "where enrollment_id = %s returning id", (seeded["enrollment"],))
        assert len(_mine(await _sweep(nc), seeded)) == 1

    _run(body)


def test_the_simple_auth_path_has_no_review_status_and_still_queues():
    """표준인증(mid)은 사람 심사가 없어 review_status 가 null 이다 — 그래도 학습 대상이다."""
    async def body(conn, nc):
        seeded = await _seed(conn, review_status=None)
        assert len(_mine(await _sweep(nc), seeded)) == 1

    _run(body)


# ── 안 넣는다 ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("override", [
    {"model_status": "suspended"},
    {"model_status": "reverification_required"},
    {"enrollment_status": "vc_pending"},              # 증서 발급 전
    {"decision": "failed", "enrollment_status": "failed"},
    {"review_status": "pending"},
    {"review_status": "rejected"},
    {"photo_review": "pending"},                      # 관리자 사진 확인 전
    {"photo_review": "reshoot_requested"},
    {"photo_review": None},
    {"license_status": "revoked"},
    {"license_status": "expired"},
    {"vc_id": None},
    {"vc_id": "  "},
    {"license_until": "now() - interval '1 day'"},   # 사용권 만료
    {"current": False},                               # 모델의 현재 등록이 아니다
    {"gender": None},                                 # 성별 미상 — 트리거를 정할 수 없다
], ids=lambda o: ",".join(f"{k}={v}" for k, v in o.items()))
def test_an_enrollment_that_is_not_ready_is_never_queued(override):
    async def body(conn, nc):
        seeded = await _seed(conn, **override)
        assert _mine(await _sweep(nc), seeded) == []
        assert await _runs(conn, seeded["model"]) == []

    _run(body)


async def _lora(conn, model_id, *, enabled, source):
    await _one(
        conn,
        """insert into fm_model_loras (model_id, version, status, enabled, lora_r2_key, bucket,
                                       trigger_token, lora_sha256, source_enrollment_id)
           values (%s, 1, 'ready', %s, 'facemarket/models/x/loras/v1.safetensors', 'face',
                   'ohwx man', 'sha', %s)
           returning id""",
        (model_id, enabled, source))


@pytest.mark.parametrize("enabled,source", [
    (True, None),       # 노*운: 손으로 seed 한 v1 — 켜져 있고 출처가 비었다
    (True, "other"),    # 켜진 LoRA 가 있으면 등록이 달라도 자동으로는 다시 안 태운다
    (False, None),      # 꺼져 있어도 출처를 모르면 안 태운다
    (False, "same"),    # 이 등록에서 나온 LoRA 가 승인 대기 중(꺼짐)
])
def test_a_model_that_already_has_a_ready_lora_is_skipped(enabled, source):
    async def body(conn, nc):
        seeded = await _seed(conn)
        src = {"same": seeded["enrollment"], "other": None, None: None}[source]
        if source == "other":
            other = await _seed(conn, current=False)
            src = other["enrollment"]
        await _lora(conn, seeded["model"], enabled=enabled, source=src)
        assert _mine(await _sweep(nc), seeded) == []

    _run(body)


@pytest.mark.parametrize("status", ["queued", "preparing", "training", "scoring", "done", "failed"])
def test_any_existing_run_for_the_enrollment_blocks_a_second_one(status):
    """★ failed 도 막는다 — 스윕이 실패한 런을 다시 넣으면 같은 이유로 $11 씩 계속 탄다."""
    async def body(conn, nc):
        seeded = await _seed(conn)
        await _one(conn, "insert into fm_lora_training_runs (model_id, enrollment_id, status) "
                         "values (%s, %s, %s) returning id",
                   (seeded["model"], seeded["enrollment"], status))
        assert _mine(await _sweep(nc), seeded) == []
        assert len(await _runs(conn, seeded["model"])) == 1

    _run(body)


def test_a_hand_inserted_run_without_an_enrollment_is_respected():
    """사람이 등록 칸을 비운 채 넣은 런이 있어도 두 번째를 넣지 않는다."""
    async def body(conn, nc):
        seeded = await _seed(conn)
        await _one(conn, "insert into fm_lora_training_runs (model_id, status) "
                         "values (%s, 'queued') returning id", (seeded["model"],))
        assert _mine(await _sweep(nc), seeded) == []

    _run(body)


def test_a_run_from_an_older_enrollment_does_not_block_the_current_one():
    """재등록(새 사진)은 새 학습 대상이다 — 옛 등록의 런은 그 등록의 것이다."""
    async def body(conn, nc):
        old = await _seed(conn, current=False)
        seeded = await _seed(conn)
        # 옛 등록을 같은 모델로 옮겨 붙인다.
        await _one(conn, "update fm_biometric_enrollments set model_id = %s where id = %s "
                         "returning id", (seeded["model"], old["enrollment"]))
        await _one(conn, "insert into fm_lora_training_runs (model_id, enrollment_id, status) "
                         "values (%s, %s, 'done') returning id",
                   (seeded["model"], old["enrollment"]))
        assert [row["enrollment_id"] for row in _mine(await _sweep(nc), seeded)] == [
            seeded["enrollment"]]

    _run(body)


# ── 시작 직전 재확인 · 큐 소비 ──────────────────────────────────────────────
def test_the_training_source_is_the_current_enrollments_photos():
    async def body(conn, nc):
        seeded = await _seed(conn)
        await _one(
            conn,
            """insert into fm_biometric_enrollment_photos
                 (enrollment_id, angle, r2_key, image_digest, mime_type, byte_size, storage_state)
               values (%s, 'sh_front', 'orig/sh_front.jpg', 'd', 'image/jpeg', 10, 'approved')
               returning angle""",
            (seeded["enrollment"],))
        for enrollment in (seeded["enrollment"], None):          # 손 런은 등록 칸이 빌 수 있다
            source = await ltr.load_training_source(nc, seeded["model"], enrollment)
            assert source["enrollment_id"] == seeded["enrollment"]
            assert source["photo_review_status"] == "approved"
            assert [p["angle"] for p in source["photos"]] == ["sh_front"]
            assert source["gender"] == "female"

    _run(body)


def test_a_model_suspended_while_queued_is_not_trained():
    """큐에서 기다리는 사이 정지되면 — 만들기 직전 재확인이 막는다."""
    async def body(conn, nc):
        seeded = await _seed(conn)
        await _one(conn, "update fm_models set status = 'suspended' where id = %s returning id",
                   (seeded["model"],))
        assert await ltr.load_training_source(nc, seeded["model"], seeded["enrollment"]) is None
        other = await _seed(conn)
        # 다른 사람의 등록 id 를 들고 온 런도 안 된다(모델의 현재 등록이어야 한다).
        assert await ltr.load_training_source(nc, other["model"], seeded["enrollment"]) is None

    _run(body)


async def _clear_queue(conn):
    """로컬 DB 에 남은 런이 claim 순서를 흐리지 않게(트랜잭션 안이라 되돌려진다)."""
    async with conn.cursor() as cur:
        await cur.execute("delete from fm_lora_training_runs")


def test_claiming_while_a_run_is_active_waits_quietly():
    """★ 동시 1건 위반은 UPDATE 실행 시점에 난다 — 매분 예외가 아니라 None(대기)이어야 한다.

    (위반 뒤 트랜잭션은 깨진 상태라 이 테스트는 여기서 끝난다 — 바깥에서 통째로 되돌린다.)
    """
    async def body(conn, nc):
        await _clear_queue(conn)
        seeded = await _seed(conn)
        await _one(conn, "insert into fm_lora_training_runs (model_id, status) "
                         "values (%s, 'scoring') returning id", (seeded["model"],))
        other = await _seed(conn)
        assert len(_mine(await _sweep(nc), other)) == 1          # 큐에는 들어간다
        assert await ltr.claim_next(nc) is None                  # 자리가 없어 집지 않는다

    _run(body)


def test_a_balance_wait_does_not_spend_an_attempt():
    async def body(conn, nc):
        await _clear_queue(conn)
        seeded = await _seed(conn)
        await _sweep(nc)
        claimed = await ltr.claim_next(nc)
        assert claimed["model_id"] == seeded["model"] and claimed["attempts"] == 1
        await ltr.release_run(nc, claimed["id"], "balance_too_low 잔액 $3.00 < 임계 $20.00")
        row = await _one(conn, "select status, attempts, started_at, error "
                               "from fm_lora_training_runs where id = %s", (claimed["id"],))
        assert (row["status"], row["attempts"], row["started_at"]) == ("queued", 0, None)
        assert row["error"].startswith("balance_too_low")
        # 다시 집으면 첫 시도다.
        assert (await ltr.claim_next(nc))["attempts"] == 1

    _run(body)


def test_the_dataset_key_is_recorded_on_the_run():
    async def body(conn, nc):
        seeded = await _seed(conn)
        (run,) = _mine(await _sweep(nc), seeded)
        await ltr.set_dataset_key(nc, run["id"], "facemarket/models/m/training/r/dataset.tgz")
        row = await _one(conn, "select dataset_key from fm_lora_training_runs where id = %s",
                         (run["id"],))
        assert row["dataset_key"] == "facemarket/models/m/training/r/dataset.tgz"

    _run(body)


async def _application(conn, *, gender, user_id=None) -> dict:
    user_id = user_id or str(uuid.uuid4())
    await _one(conn, "insert into auth.users (id) values (%s) on conflict do nothing returning id",
               (user_id,))
    return await _one(
        conn,
        """insert into fm_model_applications
             (user_id, status, contact_email, applicant_name, birthdate, gender,
              privacy_consent_version)
           values (%s, 'approved', 'a@example.com', '정*정', '1999-01-01', %s, 'v1')
           returning id::text as id, user_id::text as user_id""",
        (user_id, gender))


@pytest.mark.parametrize("link", ["enrollment", "user"])
def test_gender_falls_back_to_the_application(link):
    """정*정 — fm_models.gender 는 비었고 지원서에만 female 이 있다. 그래도 성별을 안다."""
    async def body(conn, nc):
        seeded = await _seed(conn, gender=None)
        app = await _application(conn, gender="female")
        if link == "enrollment":
            await _one(conn, "update fm_biometric_enrollments set application_id = %s "
                             "where id = %s returning id", (app["id"], seeded["enrollment"]))
        else:
            await _one(conn, "update fm_models set user_id = %s where id = %s returning id",
                       (app["user_id"], seeded["model"]))
        assert len(_mine(await _sweep(nc), seeded)) == 1
        source = await ltr.load_training_source(nc, seeded["model"], seeded["enrollment"])
        assert source["gender"] == "female"

    _run(body)


def test_the_model_row_gender_wins_over_the_application():
    async def body(conn, nc):
        seeded = await _seed(conn, gender="male")
        app = await _application(conn, gender="female")
        await _one(conn, "update fm_biometric_enrollments set application_id = %s "
                         "where id = %s returning id", (app["id"], seeded["enrollment"]))
        source = await ltr.load_training_source(nc, seeded["model"], seeded["enrollment"])
        assert source["gender"] == "male"

    _run(body)
