"""발견 원장(fm_trace_findings) 헬퍼를 **실제 Postgres** 에서 검사한다(2026-09-27).

부분 유니크 인덱스 on conflict 추론·xmax 로 새 행 판별·21일 삭제는 흉내 DB 로는 못 잡는다.
로컬 Supabase DB 에서 한 트랜잭션 안에서 끝내고 되돌린다. 마이그레이션이 아직 안 붙은 로컬 DB 면
같은 트랜잭션에서 먼저 적용한다(롤백되므로 흔적이 없다). CI 는 test-db 잡이 돌린다.
"""

import asyncio
import os
import uuid
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse

import psycopg
import pytest
from psycopg.rows import dict_row

from app import fm_trace_findings as F

DB_URL = os.environ.get("FM_TRACE_TEST_DATABASE_URL",
                        "postgresql://postgres:postgres@127.0.0.1:54322/postgres")
MIGRATION = (Path(__file__).resolve().parents[2] / "supabase" / "migrations"
             / "20260927090000_fm_trace_findings.sql")

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


def _run(body):
    async def main():
        conn = await psycopg.AsyncConnection.connect(DB_URL, row_factory=dict_row)
        try:
            async with conn.cursor() as cur:
                await cur.execute("select to_regclass('public.fm_trace_findings') as t")
                if (await cur.fetchone())["t"] is None:
                    await cur.execute(MIGRATION.read_text(encoding="utf-8"))
            await body(_NoCommit(conn))
        finally:
            await conn.rollback()
            await conn.close()

    asyncio.run(main())


class _NoCommit:
    """헬퍼가 commit 을 불러도 테스트 트랜잭션이 끝나지 않게."""

    def __init__(self, conn):
        self._conn = conn

    def cursor(self, *a, **k):
        return self._conn.cursor(*a, **k)

    async def commit(self):
        return None

    async def rollback(self):
        return None


SELLER = str(uuid.uuid4())
MODEL = str(uuid.uuid4())


def _cand(target="cut", confidence="medium", watermark=False, seller=SELLER, model=MODEL,
          phash=3, tid=None):
    tid = tid or str(uuid.uuid4())
    return {
        "target": target, "confidence": confidence,
        "evidence": {"watermark": watermark, "phashDistance": phash, "dhashDistance": 5},
        "publicationId": tid if target == "publication" else None,
        "outputRecordId": tid if target == "cut" else None,
        "seller": {"id": seller}, "model": {"id": model},
    }


async def _ledger(conn, target="cut", seller=SELLER, model=MODEL, **kw):
    """원장 행을 실제로 심고 그 행을 가리키는 후보를 돌려준다(FK 가 진짜로 걸리게)."""
    async with conn.cursor() as cur:
        if target == "cut":
            await cur.execute(
                "insert into fm_output_records (license_ref, model_id, seller_id, image_sha256) "
                "values (%s, %s, %s, %s) returning id::text as id",
                (str(uuid.uuid4()), model, seller, uuid.uuid4().hex))
        else:
            await cur.execute(
                "insert into fm_publication_records (seller_id, license_ref, model_id, kind, "
                "image_sha256) values (%s, %s, %s, 'long_png', %s) returning id::text as id",
                (seller, str(uuid.uuid4()), model, uuid.uuid4().hex))
        tid = (await cur.fetchone())["id"]
    return _cand(target=target, seller=seller, model=model, tid=tid, **kw)


def _patrol(conn, cand, *, platform="zigzag", product_id="p1", store_id="s1", **kw):
    args = dict(platform=platform, product_id=product_id,
                product_url=f"https://example/{product_id}", image_url="https://img/1.jpg",
                title="데님 셔츠", store_name="가게", store_id=store_id,
                candidate=cand, candidates=[cand], image_sha256="ab" * 32)
    args.update(kw)
    return F.record_patrol_finding(conn, **args)


def test_keys_are_stable_hashes_without_raw_ids():
    k1 = F.dedupe_key("naver", "12345", "cut", "t-1")
    assert k1 == F.dedupe_key("naver", "12345", "cut", "t-1")
    assert k1 != F.dedupe_key("zigzag", "12345", "cut", "t-1")
    assert len(k1) == 64 and "12345" not in k1
    assert F.store_key("naver", "가게") == F.store_key("naver", "가게")
    assert F.store_key("naver", None) is None and F.store_key("naver", "  ") is None


def test_best_candidate_prefers_reporters_model_then_rank():
    other = _cand(model="m-other", confidence="high", watermark=True)
    mine = _cand(model="m-me", confidence="low")
    assert F.best_candidate([other, mine], prefer_model_id="m-me") is mine
    assert F.best_candidate([other, mine]) is other
    assert F.best_candidate([]) is None


def test_patrol_finding_inserts_once_then_updates_last_seen():
    async def body(conn):
        cand = await _ledger(conn)
        first = await _patrol(conn, cand)
        assert first["inserted"] is True and first["status"] == "new"
        second = await _patrol(conn, cand, product_url="https://example/p1?v=2")
        assert second["inserted"] is False and second["id"] == first["id"]
        async with conn.cursor() as cur:
            await cur.execute("select seen_count, product_url, alert_status, method, "
                              "external_purge_at from fm_trace_findings where id = %s",
                              (first["id"],))
            row = await cur.fetchone()
        assert row["seen_count"] == 2 and row["product_url"].endswith("v=2")
        assert row["alert_status"] == "pending" and row["method"] == "phash"
        assert row["external_purge_at"] is None          # 지그재그는 삭제 시한이 없다

    _run(body)


def test_naver_finding_gets_21_day_purge_and_purge_clears_raw_fields():
    async def body(conn):
        rec = await _patrol(conn, await _ledger(conn, target="publication", watermark=True,
                                                 confidence="high"), platform="naver")
        async with conn.cursor() as cur:
            await cur.execute("select external_purge_at - last_seen_at as ttl, method "
                              "from fm_trace_findings where id = %s", (rec["id"],))
            row = await cur.fetchone()
            assert row["ttl"] == timedelta(days=21) and row["method"] == "watermark"
            await cur.execute("update fm_trace_findings set external_purge_at = now() - "
                              "interval '1 second' where id = %s", (rec["id"],))
        assert await F.purge_expired_external(conn) >= 1
        async with conn.cursor() as cur:
            await cur.execute("select product_url, image_url, product_title, store_name, "
                              "external_purge_at, store_key, dedupe_key "
                              "from fm_trace_findings where id = %s", (rec["id"],))
            row = await cur.fetchone()
        assert row["product_url"] is None and row["image_url"] is None
        assert row["product_title"] is None and row["store_name"] is None
        assert row["external_purge_at"] is None
        assert row["store_key"] and row["dedupe_key"]     # 해시 키는 남는다

    _run(body)


def test_known_store_is_classified_seller_own_without_alert():
    async def body(conn):
        seller = str(uuid.uuid4())
        async with conn.cursor() as cur:
            await cur.execute("insert into fm_trace_known_stores (seller_id, platform, store_key) "
                              "values (%s, 'zigzag', %s)", (seller, F.store_key("zigzag", "s9")))
        rec = await _patrol(conn, await _ledger(conn, seller=seller), store_id="s9",
                             product_id="p9")
        assert rec["status"] == "seller_own"
        async with conn.cursor() as cur:
            await cur.execute("select alert_status from fm_trace_findings where id = %s",
                              (rec["id"],))
            assert (await cur.fetchone())["alert_status"] == "skipped"

    _run(body)


def test_model_report_is_kept_even_without_match():
    async def body(conn):
        rec = await F.record_model_report(
            conn, reporter_model_id=MODEL, reporter_user_id=str(uuid.uuid4()),
            page_url="https://shop.example/item/1", note="제 얼굴이에요",
            image_sha256="cd" * 32, candidates=[])
        async with conn.cursor() as cur:
            await cur.execute("select source, platform, target, method, status, alert_status, "
                              "report_page_url, dedupe_key from fm_trace_findings where id = %s",
                              (rec["id"],))
            row = await cur.fetchone()
        assert row["source"] == "model_report" and row["platform"] == "report"
        assert row["target"] is None and row["method"] is None and row["dedupe_key"] is None
        assert row["status"] == "new" and row["alert_status"] == "pending"
        assert row["report_page_url"] == "https://shop.example/item/1"

    _run(body)


def test_marking_seller_own_can_remember_the_store():
    async def body(conn):
        seller = str(uuid.uuid4())
        rec = await _patrol(conn, await _ledger(conn, seller=seller), product_id="p7",
                             store_id="s7")
        audits = []

        async def audit(_conn, **kw):
            audits.append(kw)

        out = await F.update_finding_status(conn, finding_id=rec["id"], actor=str(uuid.uuid4()),
                                            status="seller_own", remember_store=True,
                                            write_audit=audit)
        assert out == {"id": rec["id"], "status": "seller_own", "storeRemembered": True}
        async with conn.cursor() as cur:
            await cur.execute("select count(*) as n from fm_trace_known_stores "
                              "where seller_id = %s and store_key = %s",
                              (seller, F.store_key("zigzag", "s7")))
            assert (await cur.fetchone())["n"] == 1
        assert audits[0]["action"] == "facemarket.trace_finding.status"
        # 같은 판매처의 다음 발견은 자동 분류된다
        again = await _patrol(conn, await _ledger(conn, seller=seller), product_id="p8",
                               store_id="s7")
        assert again["status"] == "seller_own"

    _run(body)


def test_invalid_status_is_rejected():
    async def body(conn):
        rec = await _patrol(conn, await _ledger(conn), product_id="p5")
        with pytest.raises(F.FindingError):
            await F.update_finding_status(conn, finding_id=rec["id"], actor=str(uuid.uuid4()),
                                          status="deleted", remember_store=False,
                                          write_audit=None)

    _run(body)


def test_list_findings_pages_newest_first():
    async def body(conn):
        ids = []
        for i in range(3):
            ids.append((await _patrol(conn, await _ledger(conn), product_id=f"list-{i}"))["id"])
        page1 = await F.list_findings(conn, status="new", source=None, limit=2, cursor=None)
        assert len(page1["items"]) == 2 and page1["nextCursor"]
        page2 = await F.list_findings(conn, status="new", source=None, limit=2,
                                      cursor=page1["nextCursor"])
        seen = [it["id"] for it in page1["items"] + page2["items"]]
        assert set(ids) <= set(seen) and len(seen) == len(set(seen))
        item = page1["items"][0]
        assert item["source"] == "patrol" and item["platform"] == "zigzag"
        assert "sellerEmail" not in item            # 마스킹된 값만

    _run(body)


def test_alert_outbox_claims_marks_sent_and_backs_off():
    import contextlib
    from types import SimpleNamespace

    from app.workers.fm_trace_finding_alert_reconciler import TraceFindingAlertReconciler

    async def body(conn):
        class Pool:
            @contextlib.asynccontextmanager
            async def connection(self):
                yield conn

        async with conn.cursor() as cur:
            # 이 테스트 밖에서 쌓인 대기 알림이 claim 을 가로채지 않게 잠시 미룬다(롤백된다).
            await cur.execute("update fm_trace_findings set alert_next_at = now() + "
                              "interval '1 day' where alert_status = 'pending'")
            await cur.execute("insert into fm_models (display_name) values ('김*연') "
                              "returning id::text as id")
            model = (await cur.fetchone())["id"]
        rec = await _patrol(conn, await _ledger(conn, model=model), product_id="alert-1")
        worker = TraceFindingAlertReconciler(SimpleNamespace(state=SimpleNamespace(
            pool=Pool(), settings=SimpleNamespace(
                fm_application_public_base="https://facemarket.wearless.kr"))))
        job = await worker._claim_one()
        assert job["id"] == rec["id"] and job["model_name"] == "김*연"
        assert job["source"] == "patrol" and job["target"] == "cut"
        assert await worker._claim_one() is None            # 리스 중엔 다시 안 집힌다
        await worker._mark_retry(job)
        async with conn.cursor() as cur:
            await cur.execute("select alert_status, alert_attempts, alert_next_at > now() as later "
                              "from fm_trace_findings where id = %s", (rec["id"],))
            row = await cur.fetchone()
        assert row["alert_status"] == "pending" and row["alert_attempts"] == 1 and row["later"]
        async with conn.cursor() as cur:
            await cur.execute("update fm_trace_findings set alert_next_at = now() where id = %s",
                              (rec["id"],))
        job = await worker._claim_one()
        await worker._mark_sent(job)
        async with conn.cursor() as cur:
            await cur.execute("select alert_status, alert_sent_at is not null as sent "
                              "from fm_trace_findings where id = %s", (rec["id"],))
            row = await cur.fetchone()
        assert row["alert_status"] == "sent" and row["sent"]

    _run(body)


def test_low_confidence_patrol_finding_is_listed_but_not_alerted():
    async def body(conn):
        rec = await _patrol(conn, await _ledger(conn, confidence="low"), product_id="low-1")
        async with conn.cursor() as cur:
            await cur.execute("select status, alert_status from fm_trace_findings where id = %s",
                              (rec["id"],))
            row = await cur.fetchone()
        assert row["status"] == "new" and row["alert_status"] == "skipped"

    _run(body)


def test_patrol_run_claim_once_per_kst_day():
    from datetime import date

    from app.workers import fm_trace_patrol as P

    async def body(conn):
        day = date(2031, 1, 2)
        first = await P.claim_run(conn, day)
        assert first
        assert await P.claim_run(conn, day) is None           # 리스 중 — 다른 태스크가 못 집는다
        async with conn.cursor() as cur:                       # 배포로 끊겨 리스가 만료됐다
            await cur.execute("update fm_trace_patrol_runs set lease_until = now() - "
                              "interval '1 second' where id = %s", (first,))
        again = await P.claim_run(conn, day)
        assert again == first
        await P.finish_run(conn, again, "done", {"zigzag": {"new": 1}})
        assert await P.claim_run(conn, day) is None           # 끝난 날은 다시 안 돈다
        async with conn.cursor() as cur:
            await cur.execute("select status, attempts, stats, finished_at is not null as fin "
                              "from fm_trace_patrol_runs where id = %s", (first,))
            row = await cur.fetchone()
        assert row["status"] == "done" and row["attempts"] == 2 and row["fin"]
        assert row["stats"] == {"zigzag": {"new": 1}}

    _run(body)


def test_patrol_targets_are_real_cut_and_publication_projects():
    from app.workers import fm_trace_patrol as P

    async def body(conn):
        async with conn.cursor() as cur:
            await cur.execute("select id::text as id from auth.users limit 1")
            user = await cur.fetchone()
            if user is None:
                pytest.skip("auth.users 가 비어 있어 프로젝트를 만들 수 없어요")
            await cur.execute("insert into projects (user_id, title) values (%s, '순찰_테스트_셔츠') "
                              "returning id::text as id", (user["id"],))
            proj = (await cur.fetchone())["id"]
            await cur.execute("insert into jobs (project_id, user_id, kind, status) "
                              "values (%s, %s, 'detail_page', 'done') returning id::text as id",
                              (proj, user["id"]))
            job = (await cur.fetchone())["id"]
            await cur.execute("insert into fm_output_records (job_id, license_ref, model_id, "
                              "seller_id, image_sha256) values (%s, %s, %s, %s, 'x')",
                              (job, str(uuid.uuid4()), MODEL, user["id"]))
        names = await P.load_target_names(conn)
        assert "순찰_테스트_셔츠" in names

    _run(body)
