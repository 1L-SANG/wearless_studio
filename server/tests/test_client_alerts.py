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
        progress['cases'] = (progress['cases'] + [{'event_id': event['event_id'], 'request_id': event.get('request_id')}])[:3]

    async def _overflow_cases(self, conn, cases):
        ids = {case['event_id'] for case in cases}
        return [e for e in self.store['events'] if e['event_id'] in ids]

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
    assert '1명 1건' in digests[0]['text']
    assert '0명 ' in digests[1]['text']


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
    assert case['event_id'][:8] in payload and case['request_id'][:8] in payload
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
    assert arrived['event_id'][:8] in str(store['sent'])


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
    assert text.count('기타에서 기타 실패') == 30
    assert '0명 2건' in text
    assert '유형 ' not in text
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


def readable_event(**extra):
    return event(kind='api_error', route='/v1/projects/:id/editor', action='save', status=500,
                 error_code='save_failed', device='desktop', build_sha='abcdef1234567890',
                 event_id='12345678-1234-4123-8123-123456789012',
                 request_id='87654321-1234-4123-8123-123456789012',
                 payload={'step': 'editor', 'browser': {'family': 'Chrome', 'major': '132'},
                          'os': {'family': 'Windows'}, 'network': {'effective_type': '4g', 'online': True},
                          'breadcrumbs': []}, **extra)


@pytest.mark.parametrize('blocking,label,count', [(True, '🔴 진행 막힘', 1), (False, '🟡 오류 몰림', 5)])
def test_immediate_message_explains_screen_action_cause_environment_and_lookup(store, blocking, label, count):
    case = readable_event(blocking=blocking, at=NOW - timedelta(seconds=1))
    store['events'] = [case] + [dict(case, event_id=str(uuid4()), received_at=NOW - timedelta(minutes=1))
                               for _ in range(count - 1)]
    tick(MemoryWorker(store))
    assert len(store['sent']) == 1
    assert store['sent'][0]['text'].splitlines() == [
        f'{label} · 에디터에서 저장 실패',
        f'서버 오류(500, save_failed) · 1명 {count}건 · 10/08 09:59',
        'PC · Chrome 132 · Windows · 네트워크 4g(온라인)',
        '직전 행동: 기록 없음',
        'event_id 12345678 · request_id 87654321 · 빌드 abcdef1',
    ]


@pytest.mark.parametrize('kind,status,code,cause', [
    ('network_error', 0, 'other', '연결 끊김 또는 시간 초과'),
    ('api_error', 0, 'other', '연결 끊김 또는 시간 초과'),
    ('api_error', 408, 'other', '연결 끊김 또는 시간 초과'),
    ('api_error', None, 'network_unavailable', '연결 끊김 또는 시간 초과'),
    ('api_error', None, 'connection_lost', '연결 끊김 또는 시간 초과'),
    ('api_error', None, 'timeout', '연결 끊김 또는 시간 초과'),
    ('api_error', 504, 'request_timeout', '연결 끊김 또는 시간 초과'),
    ('api_error', 503, 'unavailable', '서버 오류(503, unavailable)'),
    ('api_error', 409, 'conflict', '요청 거절(409, conflict)'),
    ('error_boundary', None, 'other', '화면 코드 오류'),
    ('runtime_error', None, 'other', '화면 코드 오류'),
    ('unhandled_rejection', None, 'other', '화면 코드 오류'),
    ('ui_error', None, 'other', '화면 코드 오류'),
    ('api_error', None, 'other', '원인 미확인'),
])
def test_shared_description_has_safe_causes(kind, status, code, cause):
    case = readable_event()
    case.update(kind=kind, status=status, error_code=code)
    text = alerts.slack_payload('하루 요약', alerts.summaries([case]))['text']
    assert f'에디터에서 저장 실패 · {cause}' in text


@pytest.mark.parametrize('step,route,screen', [
    ('input', '/unknown', '입력 화면'), ('storyboard', '/unknown', '콘티'),
    ('mannequin', '/unknown', '마네킹'), ('generating', '/:segment/:segment', '생성 중'),
    ('editor', '/unknown', '에디터'), ('library', '/unknown', '보관함'),
    ('other', '/pricing', '요금제'), ('other', '/:segment/input', '입력 화면'),
    ('other', '/v1/projects/:id/storyboard', '콘티'),
    ('other', '/editor/:id', '에디터'), ('other', '/private-person', '기타'),
])
def test_screen_maps_stored_steps_and_route_templates(step, route, screen):
    case = readable_event()
    case['payload']['step'] = step
    case['route'] = route
    text = alerts.slack_payload('하루 요약', alerts.summaries([case]))['text']
    assert f'{screen}에서 저장 실패' in text
    assert route not in text


@pytest.mark.parametrize('action,device,label', [
    ('upload', 'desktop', '사진 올리기'), ('analyze', 'desktop', '분석'),
    ('save', 'desktop', '저장'), ('save', 'mobile', '폰 저장'),
    ('load', 'desktop', '불러오기'), ('poll', 'desktop', '불러오기'),
    ('mannequinGenerate', 'desktop', '마네킹 생성'), ('mannequinAdjust', 'desktop', '마네킹 생성'),
    ('generate', 'desktop', '이미지 생성'), ('detailPageGenerate', 'desktop', '이미지 생성'),
    ('editorImage', 'desktop', '이미지 생성'), ('download', 'desktop', '다운로드'),
    ('export', 'desktop', '다운로드'), ('restore', 'desktop', '다시 열기'),
    ('navigate', 'desktop', '화면 표시'), ('unknown', 'desktop', '기타'),
])
def test_action_labels(action, device, label):
    case = readable_event()
    case.update(action=action, device=device)
    assert f'에서 {label} 실패' in alerts.slack_payload('하루 요약', alerts.summaries([case]))['text']


@pytest.mark.parametrize('device,label', [('desktop', 'PC'), ('mobile', '폰'), ('tablet', '태블릿')])
def test_immediate_environment_uses_latest_case_and_omits_missing_parts(store, device, label):
    old = readable_event(at=NOW - timedelta(minutes=2))
    old['event_id'] = str(uuid4())
    latest = readable_event(blocking=True, at=NOW - timedelta(seconds=1))
    latest['device'] = device
    latest['payload'] = {'step': 'editor', 'browser': {'family': 'Safari'},
                         'network': {'online': False}}
    store['events'] = [old, latest]
    tick(MemoryWorker(store))
    assert store['sent'][0]['text'].splitlines()[2] == f'{label} · Safari · 네트워크 오프라인'
    assert 'Chrome' not in store['sent'][0]['text'] and 'Windows' not in store['sent'][0]['text']


def test_breadcrumbs_show_only_last_five_korean_steps(store):
    case = readable_event(blocking=True, at=NOW - timedelta(seconds=1))
    case['payload']['breadcrumbs'] = [
        {'type': 'action', 'action': 'upload', 'message': 'private-old'},
        {'type': 'navigation', 'route': '/:segment/storyboard'},
        {'type': 'action', 'action': 'mannequinGenerate'},
        {'type': 'navigation', 'route': '/editor/:id'},
        {'type': 'action', 'action': 'save'},
        {'type': 'api', 'status': 500, 'route': '/private', 'error_code': 'private_code'},
    ]
    store['events'] = [case]
    tick(MemoryWorker(store))
    assert store['sent'][0]['text'].splitlines()[3] == (
        '직전 행동: 콘티 → 마네킹 생성 → 에디터 → 저장 → 요청 실패(500)')
    assert '사진 올리기' not in store['sent'][0]['text']


def test_legacy_unknown_data_is_safe_and_readable(store):
    case = event(blocking=True, at=NOW - timedelta(seconds=1),
                 kind='private-kind', status='private-status', error_code='user@example.com',
                 device='private-device', route='/private', action='private-action', build_sha='private-build')
    case['payload'] = {'step': ['private'], 'browser': {'family': 'Private Person', 'major': 'private'},
                       'os': {'family': 'user@example.com'}, 'network': {'effective_type': 'private'},
                       'breadcrumbs': [{'type': 'action', 'action': 'private'}]}
    store['events'] = [case]
    tick(MemoryWorker(store))
    text = store['sent'][0]['text']
    assert '기타에서 기타 실패' in text and '원인 미확인' in text
    assert '환경 정보 없음' in text and '직전 행동: 기타' in text
    assert 'private' not in text and 'user@example.com' not in text


@pytest.mark.parametrize('delivery', ['immediate', 'overflow', 'digest'])
def test_all_slack_payloads_exclude_private_text(delivery, store):
    case = readable_event(blocking=delivery != 'digest', at=NOW - timedelta(
        days=1 if delivery == 'digest' else 0, seconds=1))
    case.update(user_id='private-user-id', email='private@email.test', name='Private Name',
                message='private-message', tech_message='private-tech', stack='private-stack')
    case['payload'].update(message='private-payload', tech_message='private-tech', stack='private-stack',
                           name='Private Name', browser={'family': 'Chrome', 'major': '132', 'name': 'Private Name'})
    count = 12 if delivery == 'overflow' else 1
    store['events'] = [dict(case, signature=str(i), event_id=str(uuid4())) for i in range(count)]
    tick(MemoryWorker(store))
    assert store['sent']
    if delivery == 'overflow':
        overflow = next(p['text'] for p in store['sent'] if '알림이 많아 일부를 묶었어요' in p['text'])
        assert '에디터에서 저장 실패 · 서버 오류(500, save_failed)' in overflow
        assert '묶은 알림=3건' in overflow
        assert len(store['progress']['cases']) == 0
    for payload in store['sent']:
        text = str(payload)
        for secret in ('private-user-id', 'private@email.test', 'Private Name', 'private-message',
                       'private-payload', 'private-tech', 'private-stack'):
            assert secret not in text
        assert len(payload['text']) < 30000


class QueryConnection:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.queries = []

    def cursor(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def execute(self, sql, params=None):
        from conftest import assert_query_binds
        assert_query_binds(sql, params)
        self.queries.append((sql, params))

    async def fetchone(self):
        return next(self.responses)

    async def fetchall(self):
        return next(self.responses)


@pytest.mark.parametrize('query', ['immediate', 'digest', 'overflow'])
def test_real_persistence_queries_hydrate_safe_fields_without_free_text(store, query):
    case = readable_event()
    group = {'signature': 'sig', 'count': 1, 'users': 1, 'signature_total': 1, 'event_total': 1}
    responses = {'immediate': [dict(group), [case]], 'digest': [[dict(group)], [case]], 'overflow': [[case]]}
    conn = QueryConnection(responses[query])
    worker = MemoryWorker(store)
    if query == 'immediate':
        result = asyncio.run(alerts.ClientAlerts._cases(worker, conn, case))['cases']
    elif query == 'digest':
        result = asyncio.run(alerts.ClientAlerts._digest_summary(worker, conn, NOW - timedelta(days=1), NOW))['groups'][0]['cases']
    else:
        result = asyncio.run(alerts.ClientAlerts._overflow_cases(worker, conn, [case]))
    assert result == [case]
    sql = conn.queries[-1][0]
    for column in ('kind', 'route', 'action', 'status', 'error_code', 'device', 'build_sha', 'received_at', 'occurred_at'):
        assert column in sql
    for key in ('step', 'browser', 'os', 'network', 'breadcrumbs'):
        assert f"'{key}'" in sql
    for forbidden in ('tech_message', 'stack', 'email', 'name', 'message', 'select *'):
        assert forbidden not in sql
    assert "payload," not in sql and 'user_id::text' not in sql


def test_latest_case_tie_breaker_matches_database_order():
    from uuid import UUID

    first = readable_event()
    second = readable_event()
    first.update(event_id=str(UUID(int=1)), device='mobile')
    second.update(event_id=str(UUID(int=2)), device='tablet')
    group = alerts.summaries([first, second])[0]
    assert group['cases'][0] == second


def test_immediate_time_uses_occurred_at_and_lookup_is_small():
    case = readable_event(occurred_at=NOW - timedelta(minutes=5))
    payload = alerts.immediate_payload(alerts.summaries([case])[0], blocking=True)
    assert '10/08 09:55' in payload['text']
    assert payload['blocks'][1]['type'] == 'context'
    assert payload['blocks'][1]['elements'][0]['text'] == payload['text'].splitlines()[4]


def test_overflow_storage_keeps_only_three_ids(store):
    worker = MemoryWorker(store)
    for i in range(5):
        asyncio.run(worker._add_overflow(None, readable_event(signature=str(i))))
    assert store['progress']['suppressed_count'] == 5
    assert len(store['progress']['cases']) == 3
    assert all(set(case) == {'event_id', 'request_id'} for case in store['progress']['cases'])


@pytest.mark.parametrize('kind', ['ui_error', 'error_boundary', 'runtime_error', 'unhandled_rejection'])
@pytest.mark.parametrize('status', [403, 500])
def test_whole_screen_cause_takes_priority_over_http_status(kind, status):
    case = readable_event()
    case.update(kind=kind, status=status)
    assert ' · 화면 코드 오류 · ' in alerts.slack_payload('하루 요약', alerts.summaries([case]))['text']


def test_environment_omits_missing_device_and_orphan_browser_major():
    case = readable_event()
    case.pop('device')
    case['payload'] = {'browser': {'major': '132'}, 'os': {'family': 'Android'},
                       'network': {'effective_type': '3g'}}
    payload = alerts.immediate_payload(alerts.summaries([case])[0], blocking=True)
    assert payload['text'].splitlines()[2] == 'Android · 네트워크 3g'
