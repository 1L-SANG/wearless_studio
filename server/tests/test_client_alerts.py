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

    async def _lock(self, conn):
        if self.store['locked']:
            return False
        self.store['locked'] = True
        return True

    async def _unlock(self, conn):
        self.store['locked'] = False

    async def _retention(self, conn, now):
        self.store['events'][:] = [e for e in self.store['events'] if e['received_at'] >= now - timedelta(days=30)]

    async def _events(self, conn, start, end):
        return [e for e in self.store['events'] if start <= e['received_at'] < end]

    async def _ledger(self, conn, kind, key):
        return self.store['ledger'].get((kind, key))

    async def _reserve(self, conn, kind, key, now):
        self.store['ledger'][kind, key] = {'status': 'sending', 'sent_at': None, 'attempted_at': now}

    async def _finish(self, conn, kind, key, now, status):
        self.store['ledger'][kind, key].update(status=status, sent_at=now if status == 'sent' else None)

    async def _digest_summary(self, conn, start, end):
        return alerts.summaries(await self._events(conn, start, end))

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


def test_old_events_do_not_count_for_burst(store):
    store['events'] = [event(at=NOW - timedelta(minutes=11)) for _ in range(5)]
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
    assert len(digests) == 2
    tick(MemoryWorker(store), datetime(2026, 10, 9, 1, tzinfo=timezone.utc))
    assert len(store['sent']) == 2
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
