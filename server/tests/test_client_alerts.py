import asyncio
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.workers import client_alerts as alerts

NOW = datetime(2026, 10, 8, 1, tzinfo=timezone.utc)


def event(signature='sig', blocking=False, at=NOW, user='private-user', **extra):
    return {'signature': signature, 'blocking': blocking, 'received_at': at,
            'event_id': str(uuid4()), 'request_id': str(uuid4()), 'user_id': user,
            'payload': {'message': 'private text'}, **extra}


class MemoryWorker(alerts.ClientAlerts):
    def __init__(self, store, webhook='configured'):
        super().__init__(SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace(slack_alert_webhook_url=webhook))))
        self.store = store
        store.setdefault('progress', {'received_at': datetime(1970, 1, 1, tzinfo=timezone.utc),
                                     'event_id': '00000000-0000-0000-0000-000000000000', 'suppressed_count': 0, 'cases': []})
        store.setdefault('attempts', [])

    async def _lock(self, conn):
        if self.store['locked']:
            return False
        self.store['locked'] = True
        return True

    async def _unlock(self, conn):
        self.store['locked'] = False

    async def _retention(self, conn, now):
        self.store['events'][:] = [e for e in self.store['events'] if e['received_at'] >= now - timedelta(days=30)]

    async def _progress(self, conn):
        return dict(self.store['progress'])

    async def _batch(self, conn, now):
        progress = self.store['progress']
        cursor = (progress['received_at'], progress['event_id'])
        rows = [e for e in self.store['events'] if e['user_id'] is not None and e['received_at'] < now
                and (e['received_at'], e['event_id']) > cursor]
        return sorted(rows, key=lambda e: (e['received_at'], e['event_id']))[:alerts.BATCH_LIMIT]

    async def _advance_cursor(self, conn, event):
        self.store['progress'].update(received_at=event['received_at'], event_id=event['event_id'])

    async def _cases(self, conn, event):
        rows = [e for e in self.store['events'] if e['user_id'] is not None and e['signature'] == event['signature']
                and e['received_at'] >= event['received_at'] - timedelta(minutes=10)
                and (e['received_at'], e['event_id']) <= (event['received_at'], event['event_id'])]
        return alerts.summaries(rows)[0]

    async def _budget(self, conn, now):
        rows = [kind for kind, at in self.store['attempts'] if at > now - timedelta(hours=1)]
        return {'total': len(rows), 'immediate': rows.count('immediate'), 'overflow': rows.count('overflow')}

    async def _record_attempt(self, conn, kind, now):
        self.store['attempts'].append((kind, now))

    async def _add_overflow(self, conn, event):
        progress = self.store['progress']
        progress['suppressed_count'] += 1
        progress['cases'] = (progress['cases'] + [event])[:3]

    async def _clear_overflow(self, conn):
        self.store['progress'].update(suppressed_count=0, cases=[])

    async def _restore_overflow(self, conn, progress):
        self.store['progress'].update(suppressed_count=progress['suppressed_count'], cases=progress['cases'])

    async def _ledger(self, conn, kind, key):
        return self.store['ledger'].get((kind, key))

    async def _reserve(self, conn, kind, key, now, status='sending'):
        self.store['ledger'][kind, key] = {'status': status, 'sent_at': None, 'attempted_at': now}

    async def _finish(self, conn, kind, key, now, status):
        self.store['ledger'][kind, key].update(status=status, sent_at=now if status == 'sent' else None)

    async def _digest_summary(self, conn, start, end):
        return {'groups': alerts.summaries([e for e in self.store['events'] if start <= e['received_at'] < end]),
                'other_signatures': 0, 'other_events': 0}

    async def _digest_days(self, conn, now):
        return alerts.due_digest_days(now, min((e['received_at'] for e in self.store['events']), default=None))

    async def _send(self, payload):
        self.store['sent'].append(payload)
        await asyncio.sleep(0)
        return self.store.get('result', 'sent')


@pytest.fixture
def store():
    return {'events': [], 'ledger': {}, 'sent': [], 'locked': False}


def tick(worker, now=NOW):
    asyncio.run(worker.tick(None, now=now))


def test_blocking_first_case_and_cooldown(store):
    store['events'] = [event(blocking=True)]
    worker = MemoryWorker(store)
    tick(worker, NOW + timedelta(seconds=1))
    assert len(store['sent']) == 1
    store['events'].append(event(blocking=True, at=NOW + timedelta(minutes=30)))
    tick(worker, NOW + timedelta(minutes=30, seconds=1))
    assert len(store['sent']) == 1
    store['events'].append(event(blocking=True, at=NOW + timedelta(hours=1, seconds=1)))
    tick(worker, NOW + timedelta(hours=1, seconds=2))
    assert len(store['sent']) == 2
    tick(MemoryWorker(store), NOW + timedelta(hours=1, seconds=3))
    assert len(store['sent']) == 2


def test_burst_five_events_in_ten_minutes(store):
    store['events'] = [event(at=NOW - timedelta(minutes=9)) for _ in range(4)]
    worker = MemoryWorker(store)
    tick(worker)
    assert not store['sent']
    store['events'].append(event(at=NOW - timedelta(minutes=1)))
    tick(worker)
    assert len(store['sent']) == 1
    tick(MemoryWorker(store))
    assert len(store['sent']) == 1


def test_events_outside_the_case_ten_minute_window_do_not_count_for_burst(store):
    store['events'] = [event(at=NOW - timedelta(minutes=12)) for _ in range(4)]
    store['events'].append(event(at=NOW - timedelta(seconds=1)))
    tick(MemoryWorker(store))
    assert not store['sent']


def test_kst_digest_window_and_catch_up(store):
    assert alerts.digest_window(date(2026, 10, 7)) == (
        datetime(2026, 10, 6, 15, tzinfo=timezone.utc), datetime(2026, 10, 7, 15, tzinfo=timezone.utc))
    first = datetime(2026, 10, 6, 15, tzinfo=timezone.utc)
    assert alerts.due_digest_days(datetime(2026, 10, 7, 23, 59, tzinfo=timezone.utc), first) == []
    assert alerts.due_digest_days(datetime(2026, 10, 8, 0, tzinfo=timezone.utc), first) == [date(2026, 10, 7)]
    store['events'] = [event(at=first), event(at=first + timedelta(days=1), user=None)]
    tick(MemoryWorker(store), datetime(2026, 10, 9, 0, tzinfo=timezone.utc))
    digests = [p for p in store['sent'] if '하루 요약' in p['text']]
    assert len(digests) == 1
    tick(MemoryWorker(store), datetime(2026, 10, 9, 1, tzinfo=timezone.utc))
    assert len(store['sent']) == 2
    digests = store['sent']
    assert '건수=1' in digests[0]['text'] and '영향 사용자=1' in digests[0]['text']
    assert '영향 사용자=0' in digests[1]['text']


def test_two_workers_and_ledger_prevent_sending_twice(store):
    store['events'] = [event(blocking=True, at=NOW - timedelta(seconds=1))]

    async def run():
        await asyncio.gather(MemoryWorker(store).tick(None, now=NOW), MemoryWorker(store).tick(None, now=NOW))
        await MemoryWorker(store).tick(None, now=NOW)

    asyncio.run(run())
    assert len(store['sent']) == 1


def test_retention_even_when_webhook_empty(store):
    store['events'] = [event(at=NOW - timedelta(days=30, seconds=1)), event(at=NOW - timedelta(days=30))]
    tick(MemoryWorker(store, webhook=''))
    assert len(store['events']) == 1
    assert not store['sent']


def test_payload_has_only_safe_ids_and_aggregates(store):
    case = event(blocking=True, at=NOW - timedelta(seconds=1), message='private-message', route='/private', action='private-action')
    store['events'] = [case]
    tick(MemoryWorker(store))
    payload = str(store['sent'])
    assert case['event_id'] in payload and case['request_id'] in payload
    for forbidden in ('private-user', 'private text', 'private-message', '/private', 'private-action'):
        assert forbidden not in payload


@pytest.mark.parametrize('result,retry', [('failed', True), ('sending', False)])
def test_failure_and_ambiguous_delivery(store, result, retry):
    store['events'] = [event(blocking=True, at=NOW - timedelta(seconds=1))]
    store['result'] = result
    worker = MemoryWorker(store)
    tick(worker)
    tick(MemoryWorker(store), NOW + timedelta(seconds=20))
    assert len(store['sent']) == (2 if retry else 1)


def test_slack_webhook_sends_safe_json_without_network(store, monkeypatch):
    import httpx
    import json

    cases = [event(blocking=True)]
    payload = alerts.slack_payload('화면 오류 알림', alerts.summaries(cases))
    sent = []

    def respond(request):
        sent.append(json.loads(request.content))
        assert str(request.url) == 'https://alerts.example.test/webhook'
        return httpx.Response(200)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(alerts.httpx, 'AsyncClient', lambda **kwargs:
                        real_client(transport=httpx.MockTransport(respond), **kwargs))
    worker = MemoryWorker(store, webhook='https://alerts.example.test/webhook')
    assert asyncio.run(alerts.ClientAlerts._send(worker, payload)) == 'sent'
    assert sent == [payload]
    assert 'private' not in json.dumps(sent)


def test_settings_loads_alert_webhook_without_hmac_secret(monkeypatch):
    from app.config import load_settings

    monkeypatch.setenv('SLACK_ALERT_WEBHOOK_URL', 'https://alerts.example.test/webhook')
    settings = load_settings()
    assert settings.slack_alert_webhook_url == 'https://alerts.example.test/webhook'
    assert not hasattr(settings, 'client_events_ip_hmac_key')


def test_anonymous_blocking_events_go_only_to_digest(store):
    store['events'] = [event(signature=str(i), blocking=True, user=None, at=NOW - timedelta(seconds=1)) for i in range(120)]
    tick(MemoryWorker(store))
    assert not store['sent']
    tick(MemoryWorker(store), NOW + timedelta(days=1))
    assert len(store['sent']) == 1 and '하루 요약' in store['sent'][0]['text']


def test_anonymous_events_do_not_help_reach_burst_threshold(store):
    store['events'] = [event(user=None, at=NOW - timedelta(seconds=1)) for _ in range(4)]
    store['events'].append(event(at=NOW - timedelta(seconds=1)))
    tick(MemoryWorker(store))
    assert not store['sent']


def test_global_budget_and_one_overflow_summary_across_workers(store):
    store['events'] = [event(signature=str(i), blocking=True, at=NOW - timedelta(seconds=1)) for i in range(120)]
    tick(MemoryWorker(store))
    tick(MemoryWorker(store), NOW + timedelta(seconds=10))
    assert len(store['sent']) == 10
    assert sum('알림이 많아 일부를 묶었어요' in p['text'] for p in store['sent']) == 1
    tick(MemoryWorker(store), NOW + timedelta(minutes=59))
    assert len(store['sent']) == 10
    tick(MemoryWorker(store), NOW + timedelta(hours=1, seconds=1))
    assert sum('알림이 많아 일부를 묶었어요' in p['text'] for p in store['sent']) == 2


def test_cursor_does_not_lose_events_arriving_during_slow_tick(store):
    store['events'] = [event(blocking=True, at=NOW - timedelta(seconds=1))]
    arrived = event(signature='later', blocking=True, at=NOW + timedelta(minutes=1))

    class SlowWorker(MemoryWorker):
        async def _send(self, payload):
            if not self.store['sent']:
                self.store['events'].append(arrived)
            return await super()._send(payload)

    tick(SlowWorker(store))
    tick(MemoryWorker(store), NOW + timedelta(minutes=12))
    assert arrived['event_id'] in str(store['sent'])


def test_tick_batch_limit_and_equal_timestamp_cursor(store):
    from uuid import UUID

    store['events'] = [event(signature=str(i), at=NOW - timedelta(seconds=1),
                            event_id=str(UUID(int=i + 1))) for i in range(250)]
    tick(MemoryWorker(store))
    assert store['progress']['event_id'] == str(UUID(int=100))
    tick(MemoryWorker(store))
    assert store['progress']['event_id'] == str(UUID(int=200))
    tick(MemoryWorker(store))
    assert store['progress']['event_id'] == str(UUID(int=250))


def test_digest_top_thirty_and_remainder_under_slack_limit(store):
    store['events'] = [event(signature=f'{i:04}', user=None, at=NOW - timedelta(days=1)) for i in range(500)]
    store['events'].append(event(signature='0499', user=None, at=NOW - timedelta(days=1)))
    tick(MemoryWorker(store))
    text = store['sent'][0]['text']
    assert text.count('유형 ') == 30
    assert '유형 1: 건수=2' in text
    assert '그 외 470종 470건' in text
    assert len(text) < 30000


def test_overflow_failure_restored_before_failed_state_is_committed(store):
    store['events'] = [event(signature=str(i), blocking=True, at=NOW - timedelta(seconds=1)) for i in range(12)]

    class FailedOverflow(MemoryWorker):
        async def _send(self, payload):
            await super()._send(payload)
            return 'failed' if '알림이 많아 일부를 묶었어요' in payload['text'] else 'sent'

        async def _finish(self, conn, kind, key, now, status):
            if kind == 'overflow' and status == 'failed':
                assert self.store['progress']['suppressed_count'] == 3
            await super()._finish(conn, kind, key, now, status)

    tick(FailedOverflow(store))
    assert store['ledger']['overflow', 'global']['status'] == 'failed'
    tick(MemoryWorker(store), NOW + timedelta(hours=1, seconds=1))
    assert store['progress']['suppressed_count'] == 0
    assert store['ledger']['overflow', 'global']['status'] == 'sent'
