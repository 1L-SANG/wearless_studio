"""API 프로세스 전용 오류 알림과 30일 보관 정리."""

import asyncio
import contextlib
import logging
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

from psycopg.types.json import Json

from ..request_id import valid_request_id
from .subscription_biller import _PeriodicWorker

log = logging.getLogger("wearless.client_alerts")
KST = ZoneInfo("Asia/Seoul")
_LOCK_KEY = (0x4345, 1)
COOLDOWN = timedelta(hours=1)
BATCH_LIMIT = 100
IMMEDIATE_BUDGET = 10
DIGEST_LIMIT = 30
_INGEST_LOCK_KEY = (0x4345, 2)


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


def _case_lines(cases):
    lines = []
    for case in cases[:3]:
        event_id = valid_request_id(str(case.get('event_id', '')))
        request_id = valid_request_id(str(case.get('request_id', '')))
        if event_id:
            lines.append(f"event_id={event_id} request_id={request_id or '-'}")
    return lines


def slack_payload(title, groups, *, other_signatures=0, other_events=0):
    # 표시값은 집계 수치와 UUID만 쓴다. 화면, 동작, 문구, 사용자 ID는 DB에서 조사한다.
    ordered = sorted(groups, key=lambda group: (-group['count'], group['signature']))
    tail = ordered[DIGEST_LIMIT:]
    other_signatures += len(tail)
    other_events += sum(group['count'] for group in tail)
    lines = [title]
    for index, group in enumerate(ordered[:DIGEST_LIMIT], 1):
        lines.append(f"유형 {index}: 건수={group['count']} 영향 사용자={group['users']}")
        lines.extend(_case_lines(group['cases']))
    if other_signatures:
        lines.append(f"그 외 {other_signatures}종 {other_events}건")
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
            await cur.execute("delete from client_alert_attempts where attempted_at < %s", (now - timedelta(days=1),))
            await cur.execute("delete from client_alert_ledger where attempted_at < %s", (now - timedelta(days=30),))
        await conn.commit()
        self._retained_on = day

    async def _progress(self, conn):
        async with conn.cursor() as cur:
            await cur.execute("select received_at, event_id::text, suppressed_count, cases from client_alert_progress where singleton")
            return await cur.fetchone()

    async def _batch(self, conn, now):
        progress = await self._progress(conn)
        async with conn.cursor() as cur:
            # 진행 중인 수신 커밋을 기다리고 조회 직후 해제한다. Slack 전송은 수신을 막지 않는다.
            await cur.execute("select pg_advisory_xact_lock(%s, %s)", _INGEST_LOCK_KEY)
            await cur.execute(
                "select event_id::text, request_id::text, signature, user_id::text, blocking, received_at "
                "from client_error_events where user_id is not null "
                "and (received_at, event_id) > (%s, %s::uuid) and received_at < %s "
                "order by received_at, event_id limit %s",
                (progress['received_at'], progress['event_id'], now, BATCH_LIMIT))
            events = await cur.fetchall()
        await conn.commit()
        return events

    async def _advance_cursor(self, conn, event):
        async with conn.cursor() as cur:
            await cur.execute("update client_alert_progress set received_at = %s, event_id = %s where singleton",
                              (event['received_at'], event['event_id']))
        await conn.commit()

    async def _cases(self, conn, event):
        params = (event['signature'], event['received_at'] - timedelta(minutes=10),
                  event['received_at'], event['event_id'])
        predicate = "signature = %s and user_id is not null and received_at >= %s and (received_at, event_id) <= (%s, %s::uuid)"
        async with conn.cursor() as cur:
            await cur.execute("select count(*) as count, count(distinct user_id) as users from client_error_events where " + predicate, params)
            group = await cur.fetchone()
            await cur.execute("select event_id::text, request_id::text from client_error_events where " + predicate +
                              " order by received_at desc, event_id desc limit 3", params)
            group['cases'] = await cur.fetchall()
        group['signature'] = event['signature']
        return group

    async def _budget(self, conn, now):
        async with conn.cursor() as cur:
            await cur.execute(
                "select count(*) as total, count(*) filter (where kind = 'immediate') as immediate, "
                "count(*) filter (where kind = 'overflow') as overflow from client_alert_attempts where attempted_at > %s",
                (now - COOLDOWN,))
            return await cur.fetchone()

    async def _record_attempt(self, conn, kind, now):
        async with conn.cursor() as cur:
            await cur.execute("insert into client_alert_attempts (kind, attempted_at) values (%s, %s)", (kind, now))

    async def _add_overflow(self, conn, event):
        progress = await self._progress(conn)
        cases = (progress['cases'] + [{'event_id': event['event_id'], 'request_id': event.get('request_id')}])[:3]
        async with conn.cursor() as cur:
            await cur.execute("update client_alert_progress set suppressed_count = suppressed_count + 1, cases = %s where singleton", (Json(cases),))

    async def _clear_overflow(self, conn):
        async with conn.cursor() as cur:
            await cur.execute("update client_alert_progress set suppressed_count = 0, cases = '[]'::jsonb where singleton")

    async def _restore_overflow(self, conn, progress):
        async with conn.cursor() as cur:
            await cur.execute("update client_alert_progress set suppressed_count = suppressed_count + %s, cases = %s where singleton",
                              (progress['suppressed_count'], Json(progress['cases'])))

    async def _flush_overflow(self, conn, now):
        progress = await self._progress(conn)
        if not progress['suppressed_count']:
            return
        budget = await self._budget(conn, now)
        if budget['overflow'] or budget['total'] >= IMMEDIATE_BUDGET:
            return
        payload = {'text': "\n".join(['알림이 많아 일부를 묶었어요', f"묶은 알림={progress['suppressed_count']}건", *_case_lines(progress['cases'])])}
        # 예약과 함께 대기분을 비워 재시작 시 불명확한 전송을 중복하지 않는다.
        await self._clear_overflow(conn)
        await self._deliver(conn, 'overflow', 'global', now, payload, restore_on_failure=progress)

    async def _digest_summary(self, conn, start, end):
        async with conn.cursor() as cur:
            await cur.execute(
                "select signature, count(*) as count, count(distinct user_id) as users, "
                "count(*) over () as signature_total, sum(count(*)) over () as event_total "
                "from client_error_events where received_at >= %s and received_at < %s "
                "group by signature order by count desc, signature limit %s", (start, end, DIGEST_LIMIT))
            groups = await cur.fetchall()
            other_signatures = int(groups[0]['signature_total']) - len(groups) if groups else 0
            other_events = int(groups[0]['event_total']) - sum(g['count'] for g in groups) if groups else 0
            for group in groups:
                await cur.execute(
                    "select event_id::text, request_id::text from client_error_events "
                    "where signature = %s and received_at >= %s and received_at < %s "
                    "order by received_at desc, event_id limit 3", (group['signature'], start, end))
                group['cases'] = await cur.fetchall()
        return {'groups': groups, 'other_signatures': other_signatures, 'other_events': other_events}

    async def _ledger(self, conn, kind, key):
        async with conn.cursor() as cur:
            await cur.execute("select sent_at, attempted_at, status from client_alert_ledger where kind = %s and key = %s",
                              (kind, key))
            return await cur.fetchone()

    async def _reserve(self, conn, kind, key, now, status='sending'):
        async with conn.cursor() as cur:
            await cur.execute(
                "insert into client_alert_ledger (kind, key, attempted_at, status) values (%s, %s, %s, %s) "
                "on conflict (kind, key) do update set attempted_at = excluded.attempted_at, status = excluded.status, sent_at = null",
                (kind, key, now, status))
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

    async def _deliver(self, conn, kind, key, now, payload, *, restore_on_failure=None):
        if kind in ('burst', 'overflow'):
            await self._record_attempt(conn, 'immediate' if kind == 'burst' else 'overflow', now)
        await self._reserve(conn, kind, key, now)
        status = await self._send(payload)
        if status == 'failed' and restore_on_failure is not None:
            # 실패 원장과 대기분 복원을 같은 커밋으로 확정한다.
            await self._restore_overflow(conn, restore_on_failure)
        await self._finish(conn, kind, key, now, status)
        return status

    async def tick(self, conn, *, now=None):
        clock = (lambda: now) if now is not None else lambda: datetime.now(timezone.utc)
        started = clock()
        if not await self._lock(conn):
            return {"skipped": "locked"}
        try:
            await self._retention(conn, started)
            webhook = self.app.state.settings.slack_alert_webhook_url
            if not webhook or not webhook.strip():
                return {"skipped": "unconfigured"}
            processed = 0
            for event in await self._batch(conn, started):
                sent_at = clock()
                ledger = await self._ledger(conn, 'burst', event['signature'])
                eligible = True
                if ledger and ledger['status'] != 'failed':
                    watermark = ledger['sent_at'] or ledger['attempted_at']
                    eligible = sent_at - watermark >= COOLDOWN and event['received_at'] > watermark
                if eligible:
                    group = await self._cases(conn, event)
                    if event['blocking'] or group['count'] >= 5:
                        budget = await self._budget(conn, sent_at)
                        # 묶음 요약 한 자리를 남겨 전체 발송도 최근 1시간 10회를 넘지 않는다.
                        if budget['total'] >= IMMEDIATE_BUDGET or budget['immediate'] >= IMMEDIATE_BUDGET - 1:
                            await self._add_overflow(conn, event)
                            await self._reserve(conn, 'burst', event['signature'], sent_at, status='suppressed')
                        else:
                            result = await self._deliver(conn, 'burst', event['signature'], sent_at,
                                                         slack_payload('화면 오류 알림', [group]))
                            if result == 'failed':
                                break
                await self._advance_cursor(conn, event)
                processed += 1
            await self._flush_overflow(conn, clock())
            for day in await self._digest_days(conn, started):
                key = day.isoformat()
                ledger = await self._ledger(conn, 'digest', key)
                if ledger and ledger['status'] in ('sending', 'sent', 'empty'):
                    continue
                summary = await self._digest_summary(conn, *digest_window(day))
                if summary['groups']:
                    await self._deliver(conn, 'digest', key, clock(), slack_payload(
                        f'화면 오류 하루 요약 {key} KST', summary['groups'],
                        other_signatures=summary['other_signatures'], other_events=summary['other_events']))
                else:
                    await self._reserve(conn, 'digest', key, clock())
                    await self._finish(conn, 'digest', key, clock(), 'empty')
                break
            return {"processed": processed}
        except BaseException:
            if conn is not None:
                await conn.rollback()
            raise
        finally:
            await self._unlock(conn)
