"""API 프로세스 전용 오류 알림과 30일 보관 정리."""

import asyncio
import contextlib
import logging
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

from ..request_id import valid_request_id
from .subscription_biller import _PeriodicWorker

log = logging.getLogger("wearless.client_alerts")
KST = ZoneInfo("Asia/Seoul")
_LOCK_KEY = (0x4345, 1)
COOLDOWN = timedelta(hours=1)


def digest_window(day):
    start = datetime.combine(day, time(), KST)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def due_digest_days(now, first_received):
    if first_received is None:
        return []
    local = now.astimezone(KST)
    last = local.date() - timedelta(days=1 if local.time() >= time(9) else 2)
    first = max(first_received.astimezone(KST).date(), (now - timedelta(days=30)).astimezone(KST).date())
    return [first + timedelta(days=i) for i in range(max(0, (last - first).days + 1))]


def summaries(events):
    groups = defaultdict(list)
    for event in events:
        groups[event["signature"]].append(event)
    return [{"signature": signature, "count": len(cases),
             "users": len({case['user_id'] for case in cases if case.get('user_id')}),
             "cases": sorted(cases, key=lambda e: e['received_at'], reverse=True)[:3]}
            for signature, cases in sorted(groups.items())]


def slack_payload(title, groups):
    # 표시값은 집계 수치와 UUID만 쓴다. 화면, 동작, 문구, 사용자 ID는 DB에서 조사한다.
    lines = [title]
    for index, group in enumerate(groups, 1):
        lines.append(f"유형 {index}: 건수={group['count']} 영향 사용자={group['users']}")
        for case in group['cases']:
            event_id = valid_request_id(str(case.get('event_id', '')))
            request_id = valid_request_id(str(case.get('request_id', '')))
            if event_id:
                lines.append(f"event_id={event_id} request_id={request_id or '-'}")
    return {"text": "\n".join(lines)}


class ClientAlerts(_PeriodicWorker):
    name = "client-alerts"

    def __init__(self, app):
        super().__init__(app)
        self._retained_on = None

    async def _run(self):
        while not self._stop.is_set():
            try:
                async with self.app.state.pool.connection() as conn:
                    await self.tick(conn)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("client alerts tick failed")
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=10)

    async def _lock(self, conn):
        async with conn.cursor() as cur:
            await cur.execute("select pg_try_advisory_lock(%s, %s) as locked", _LOCK_KEY)
            return (await cur.fetchone())["locked"]

    async def _unlock(self, conn):
        async with conn.cursor() as cur:
            await cur.execute("select pg_advisory_unlock(%s, %s)", _LOCK_KEY)
        await conn.commit()

    async def _retention(self, conn, now):
        day = now.astimezone(KST).date()
        if self._retained_on == day:
            return
        async with conn.cursor() as cur:
            await cur.execute("delete from client_error_events where received_at < %s", (now - timedelta(days=30),))
            await cur.execute("delete from client_event_rate_limits where window_start < %s", (now - timedelta(days=1),))
            await cur.execute("delete from client_event_salts where day < %s", (day - timedelta(days=2),))
            await cur.execute("delete from client_alert_ledger where attempted_at < %s", (now - timedelta(days=30),))
        await conn.commit()
        self._retained_on = day

    async def _events(self, conn, start, end):
        async with conn.cursor() as cur:
            await cur.execute(
                "select event_id::text, request_id::text, signature, user_id::text, blocking, received_at "
                "from client_error_events where received_at >= %s and received_at < %s "
                "order by received_at, event_id", (start, end))
            return await cur.fetchall()

    async def _digest_summary(self, conn, start, end):
        async with conn.cursor() as cur:
            await cur.execute(
                "select signature, count(*) as count, count(distinct user_id) as users "
                "from client_error_events where received_at >= %s and received_at < %s "
                "group by signature order by signature", (start, end))
            groups = await cur.fetchall()
            for group in groups:
                await cur.execute(
                    "select event_id::text, request_id::text from client_error_events "
                    "where signature = %s and received_at >= %s and received_at < %s "
                    "order by received_at desc, event_id limit 3", (group['signature'], start, end))
                group['cases'] = await cur.fetchall()
        return groups

    async def _ledger(self, conn, kind, key):
        async with conn.cursor() as cur:
            await cur.execute("select sent_at, attempted_at, status from client_alert_ledger where kind = %s and key = %s",
                              (kind, key))
            return await cur.fetchone()

    async def _reserve(self, conn, kind, key, now):
        async with conn.cursor() as cur:
            await cur.execute(
                "insert into client_alert_ledger (kind, key, attempted_at, status) values (%s, %s, %s, 'sending') "
                "on conflict (kind, key) do update set attempted_at = excluded.attempted_at, status = 'sending', sent_at = null",
                (kind, key, now))
        await conn.commit()

    async def _finish(self, conn, kind, key, now, status):
        async with conn.cursor() as cur:
            await cur.execute(
                "update client_alert_ledger set status = %s, sent_at = %s where kind = %s and key = %s",
                (status, now if status == 'sent' else None, kind, key))
        await conn.commit()

    async def _digest_days(self, conn, now):
        async with conn.cursor() as cur:
            await cur.execute("select min(received_at) as first_received from client_error_events")
            first = (await cur.fetchone())["first_received"]
        return due_digest_days(now, first)

    async def _send(self, payload):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(self.app.state.settings.slack_alert_webhook_url, json=payload)
            if 200 <= response.status_code < 300:
                return "sent"
            log.warning("client alert rejected status=%s", response.status_code)
            return "failed"
        except httpx.HTTPError:
            # 전송 결과가 불명확하다. 중복을 막기 위해 sending을 유지하고 운영자가 확인한다.
            log.warning("client alert delivery uncertain")
            return "sending"

    async def _deliver(self, conn, kind, key, now, payload):
        await self._reserve(conn, kind, key, now)
        status = await self._send(payload)
        await self._finish(conn, kind, key, now, status)

    async def tick(self, conn, *, now=None):
        now = now or datetime.now(timezone.utc)
        if not await self._lock(conn):
            return {"skipped": "locked"}
        try:
            await self._retention(conn, now)
            webhook = self.app.state.settings.slack_alert_webhook_url
            if not webhook or not webhook.strip():
                return {"skipped": "unconfigured"}
            events = await self._events(conn, now - timedelta(minutes=10), now)
            groups = defaultdict(list)
            for event in events:
                groups[event['signature']].append(event)
            for signature, cases in groups.items():
                ledger = await self._ledger(conn, 'burst', signature)
                if ledger and ledger['status'] != 'failed':
                    watermark = ledger['sent_at'] or ledger['attempted_at']
                    if now - watermark < COOLDOWN:
                        continue
                    if not any(case['received_at'] > watermark for case in cases):
                        continue
                if not any(case['blocking'] for case in cases) and len(cases) < 5:
                    continue
                await self._deliver(conn, 'burst', signature, now,
                                    slack_payload('화면 오류 알림', summaries(cases)))
            for day in await self._digest_days(conn, now):
                key = day.isoformat()
                ledger = await self._ledger(conn, 'digest', key)
                if ledger and ledger['status'] in ('sending', 'sent', 'empty'):
                    continue
                groups = await self._digest_summary(conn, *digest_window(day))
                if groups:
                    await self._deliver(conn, 'digest', key, now,
                                        slack_payload(f'화면 오류 하루 요약 {key} KST', groups))
                else:
                    await self._reserve(conn, 'digest', key, now)
                    await self._finish(conn, 'digest', key, now, 'empty')
            return {"processed": True}
        except BaseException:
            if conn is not None:
                await conn.rollback()
            raise
        finally:
            await self._unlock(conn)
