"""하루 1회 자동 출처 순찰(2026-09-27, 오너 결정 — 무료 경로만).

무엇: REAL 컷(fm_output_records)·배포본(fm_publication_records)이 있는 프로젝트의 상품명으로 허용된
플랫폼(fm_trace_sources — 네이버 공식 검색 API · 지그재그)을 검색하고, 검색 결과의 **대표 이미지만**
받아 관리자 추적과 같은 대조(facemarket_trace.trace_image — 워터마크 → 지문)를 돌린다. 맞으면 발견
원장(fm_trace_findings)에 남기고, 알림 워커가 슬랙으로 알린다.

운영 패턴(fm_publication_anchor 와 같은 결):
  - 전용 테이블(fm_trace_patrol_runs)의 KST 날짜 unique 행을 lease 로 집는다 → API 태스크가 여럿이어도
    하루 한 번. 배포로 끊기면 lease(30분)가 만료된 뒤 다른 태스크가 처음부터 다시 돈다(발견은 dedupe_key
    로 멱등, 최대 3회).
  - 순찰 시작 때 네이버 원문 21일 삭제(검색 API 특약 2.4)를 먼저 돌린다(순찰이 꺼져 있어도 알림
    워커가 한 시간마다 같은 삭제를 돌린다 — fm_trace_finding_alert_reconciler).
  - 플랫폼마다 이미지 상한(MAX_IMAGES)·같은 이미지 한 번만·멈추라면 바로 멈춘다.
  - 검색어는 하루 MAX_QUERIES 개씩 날마다 시작점을 밀어 전부 돌아가며 찾는다(daily_queries).

비용: 무료. 네이버 검색 API 는 하루 25,000건 한도 안에서 쿼리 수(MAX_QUERIES) 만큼만 부른다.
이미지 바이트는 저장하지 않는다(대조만 하고 버린다 — sha256 만 원장에).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
from datetime import date, datetime, timedelta, timezone

import httpx

from .. import facemarket_trace, fm_fingerprint_store, fm_trace_findings
from ..services import fm_trace_sources

log = logging.getLogger("wearless.fm_trace_patrol")

KST = timezone(timedelta(hours=9))
MAX_QUERIES = 200
MAX_IMAGES = 2000
MIN_QUERY_CHARS = 2
_CHECK_SECONDS = 600
_LEASE_MINUTES = 30
_HEARTBEAT_SECONDS = 60
_MAX_ATTEMPTS = 3
_STOP_TIMEOUT_SECONDS = 10

_TARGET_NAMES_SQL = """
with projects_with_real as (
  select j.project_id from fm_output_records r join jobs j on j.id = r.job_id
   where j.project_id is not null
  union
  select p.project_id from fm_publication_records p
   where p.project_id is not null and p.revoked_at is null
)
select coalesce(nullif(pd.name, ''), nullif(pr.title, '')) as name
  from projects_with_real t
  join projects pr on pr.id = t.project_id
  left join products pd on pd.project_id = pr.id
 order by pr.updated_at desc nulls last
"""

_SPACE = re.compile(r"\s+")


def queries_from_names(names, limit: int | None = MAX_QUERIES) -> list[str]:
    """상품명 → 검색어. 밑줄은 띄어쓰기로, 공백은 하나로, 띄어쓰기만 다른 말은 한 번만, 너무 짧은 건 버린다."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in names:
        q = _SPACE.sub(" ", str(raw or "").replace("_", " ")).strip()
        key = q.replace(" ", "").lower()          # "데님셔츠"·"데님 셔츠"는 같은 검색이다
        if len(q) < MIN_QUERY_CHARS or key in seen:
            continue
        seen.add(key)
        out.append(q)
        if limit is not None and len(out) >= limit:
            break
    return out


def daily_queries(names, run_date: date) -> list[str]:
    """오늘 검색할 몫(MAX_QUERIES 개). 날마다 시작점을 MAX_QUERIES 만큼 밀어, 상품이 많아도
    며칠에 걸쳐 전부 한 번씩 돌게 한다 — 최근 수정순 앞쪽만 매일 도는 일이 없게."""
    every = queries_from_names(names, limit=None)
    if len(every) <= MAX_QUERIES:
        return every
    start = (run_date.toordinal() * MAX_QUERIES) % len(every)
    return [every[(start + i) % len(every)] for i in range(MAX_QUERIES)]


async def load_target_names(conn) -> list[str]:
    async with conn.cursor() as cur:
        await cur.execute(_TARGET_NAMES_SQL)
        return [r["name"] for r in await cur.fetchall() or [] if r.get("name")]


def kst_due(now: datetime, *, hour: int) -> date | None:
    """KST 기준 오늘 순찰 시각이 지났으면 그 날짜, 아니면 None."""
    local = now.astimezone(KST)
    return local.date() if local.hour >= hour else None


async def claim_run(conn, run_date: date) -> str | None:
    """오늘 실행 행을 lease 로 집는다. 이미 끝났거나 다른 태스크가 돌고 있으면 None."""
    async with conn.cursor() as cur:
        await cur.execute(
            f"""insert into fm_trace_patrol_runs (run_date, status, attempts, lease_until)
                values (%s, 'running', 1, now() + interval '{_LEASE_MINUTES} minutes')
                on conflict (run_date) do update
                   set status = 'running',
                       attempts = fm_trace_patrol_runs.attempts + 1,
                       lease_until = excluded.lease_until,
                       last_error = null,
                       started_at = now()
                 where fm_trace_patrol_runs.status <> 'done'
                   and fm_trace_patrol_runs.attempts < {_MAX_ATTEMPTS}
                   and (fm_trace_patrol_runs.status = 'failed'
                        or fm_trace_patrol_runs.lease_until < now())
                returning id::text as id""",
            (run_date,),
        )
        row = await cur.fetchone()
    await conn.commit()
    return row["id"] if row else None


async def heartbeat_run(conn, run_id: str) -> None:
    async with conn.cursor() as cur:
        await cur.execute(
            f"update fm_trace_patrol_runs set lease_until = now() + interval "
            f"'{_LEASE_MINUTES} minutes' where id = %s and status = 'running'",
            (run_id,),
        )
    await conn.commit()


async def finish_run(conn, run_id: str, status: str, stats: dict, error: str | None = None):
    async with conn.cursor() as cur:
        await cur.execute(
            "update fm_trace_patrol_runs set status = %s, stats = %s::jsonb, last_error = %s, "
            "finished_at = now(), lease_until = null where id = %s",
            (status, json.dumps(stats, ensure_ascii=False), error, run_id),
        )
    await conn.commit()


def _blank() -> dict:
    return {"queries": 0, "hits": 0, "images": 0, "matched": 0, "new": 0, "errors": 0,
            "budget": False}


async def patrol_once(pool, *, adapters, fetcher, queries: list[str], rows: list[dict],
                      origin: str, trace=None, stop=lambda: False, max_images: int = MAX_IMAGES,
                      heartbeat=None) -> dict:
    """한 번 순찰. 반환 = 플랫폼별 통계 + stopped(shutdown|None).

    이미지 상한(max_images)은 **플랫폼마다** 따로다 — 앞 플랫폼이 다 써도 뒤 플랫폼은 돈다. 실제로 받은
    이미지만 센다(robots 거부·404 는 세지 않는다). 받기 시도는 상한의 3배에서 끊는다(무한 시도 방지).
    """
    trace = trace or facemarket_trace.trace_image
    stats: dict = {a.platform: _blank() for a in adapters}
    stats["stopped"] = None
    fetched: set[str] = set()
    last_beat = time.monotonic()
    for adapter in adapters:
        s = stats[adapter.platform]
        attempts = 0
        for query in queries:
            if s["budget"]:
                break
            if stop():
                stats["stopped"] = "shutdown"
                return stats
            s["queries"] += 1
            try:
                hits = await adapter.search(query)
            except Exception:
                log.warning("trace patrol search failed platform=%s", adapter.platform,
                            exc_info=True)
                s["errors"] += 1
                continue
            s["hits"] += len(hits)
            for hit in hits:
                if stop():
                    stats["stopped"] = "shutdown"
                    return stats
                if s["images"] >= max_images or attempts >= max_images * 3:
                    s["budget"] = True
                    break
                if hit.image_url in fetched:
                    continue
                fetched.add(hit.image_url)
                attempts += 1
                data = await fetcher.fetch_image(hit.image_url)
                if heartbeat is not None and time.monotonic() - last_beat > _HEARTBEAT_SECONDS:
                    last_beat = time.monotonic()
                    await heartbeat()
                if not data:
                    continue
                s["images"] += 1
                try:
                    async with pool.connection() as conn:
                        try:
                            result = await trace(conn, data, origin=origin, rows=rows)
                        except ValueError:
                            continue
                        candidates = result["candidates"]
                        if not candidates:
                            continue
                        s["matched"] += 1
                        rec = await fm_trace_findings.record_patrol_finding(
                            conn, platform=hit.platform, product_id=hit.product_id,
                            product_url=hit.product_url, image_url=hit.image_url,
                            title=hit.title, store_name=hit.store_name, store_id=hit.store_id,
                            candidate=fm_trace_findings.best_candidate(candidates),
                            candidates=candidates, image_sha256=result["image"]["sha256"],
                        )
                        await conn.commit()
                        if rec["inserted"]:
                            s["new"] += 1
                except Exception:
                    log.warning("trace patrol match failed platform=%s", adapter.platform,
                                exc_info=True)
                    s["errors"] += 1
    return stats


class TracePatrol:
    def __init__(self, app):
        self.app = app
        self._task = None
        self._stop = asyncio.Event()

    async def start(self):
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="fm-trace-patrol")

    async def stop(self):
        self._stop.set()
        task = self._task
        if task is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=_STOP_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        finally:
            self._task = None

    async def _run(self):
        while not self._stop.is_set():
            try:
                await self._tick(datetime.now(timezone.utc))
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("trace patrol tick failed")
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=_CHECK_SECONDS)

    async def _tick(self, now: datetime):
        settings = self.app.state.settings
        run_date = kst_due(now, hour=settings.fm_trace_patrol_hour_kst)
        if run_date is None:
            return
        pool = self.app.state.pool
        async with pool.connection() as conn:
            run_id = await claim_run(conn, run_date)
        if run_id is None:
            return
        log.info("trace patrol start run=%s date=%s", run_id, run_date)
        try:
            async with pool.connection() as conn:
                purged = await fm_trace_findings.purge_expired_external(conn)
                await conn.commit()
                queries = daily_queries(await load_target_names(conn), run_date)
                rows = await fm_fingerprint_store.load_fingerprints(conn)
            async with httpx.AsyncClient() as client:
                fetcher = fm_trace_sources.PoliteFetcher(client, contact=settings.fm_trace_contact)
                adapters = fm_trace_sources.build_adapters(settings, fetcher)

                async def beat():
                    async with pool.connection() as c:
                        await heartbeat_run(c, run_id)

                stats = await patrol_once(
                    pool, adapters=adapters, fetcher=fetcher, queries=queries, rows=rows,
                    origin=settings.public_web_origin, stop=self._stop.is_set, heartbeat=beat)
            stats.update({"purged": purged, "queryCount": len(queries),
                          "fingerprints": len(rows),
                          "platforms": [a.platform for a in adapters]})
            if stats.get("stopped") == "shutdown":
                # 다음 태스크가 lease 만료 뒤 이어서 돈다 — 'running' 으로 둔다.
                log.info("trace patrol interrupted run=%s", run_id)
                return
            async with pool.connection() as conn:
                await finish_run(conn, run_id, "done", stats)
            log.info("trace patrol done run=%s stats=%s", run_id, stats)
        except Exception as exc:
            async with pool.connection() as conn:
                await finish_run(conn, run_id, "failed", {}, type(exc).__name__)
            raise
