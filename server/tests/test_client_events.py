import asyncio
import contextlib
import hashlib
import json
import uuid

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app import client_events as events
from conftest import FakeConn


USER = str(uuid.uuid4())
RID = str(uuid.uuid4())


def sample(**overrides):
    return {'event_id': str(uuid.uuid4()), 'schema_version': 1,
            'occurred_at': '2026-10-08T08:00:00Z', 'kind': 'api_error',
            'action': 'save', 'route': '/projects/' + str(uuid.uuid4()) + '/editor?token=secret',
            'status': 500, 'error_code': 'save_failed', 'request_id': RID,
            'blocking': True, **overrides}


@pytest.fixture
def ingestion(client, monkeypatch):
    store = {}
    seen = []
    limit_keys = {}
    client.app.state.test_client_event_limit_keys = limit_keys

    @contextlib.asynccontextmanager
    async def connection(request):
        yield FakeConn()

    async def limit(conn, scope, key):
        seen.append(scope)
        limit_keys[scope] = key
        return True

    async def insert(conn, event, user_id, ip_hmac):
        store.setdefault(event['event_id'], {**event, 'user_id': user_id, 'ip_hmac': ip_hmac})

    async def daily_salt(conn, cache):
        return b's' * 32

    monkeypatch.setattr(events, 'get_daily_salt', daily_salt)
    monkeypatch.setattr(events, 'get_conn', connection)
    monkeypatch.setattr(events, 'consume_limit', limit)
    monkeypatch.setattr(events, 'store_event', insert)
    async def lock(conn, user_id):
        pass
    monkeypatch.setattr(events, 'lock_user_events', lock)
    return client, store, seen


@pytest.mark.parametrize('auth', ['valid', 'invalid', 'none'])
def test_optional_authentication(ingestion, make_token, auth):
    client, store, seen = ingestion
    headers = {} if auth == 'none' else {'Authorization': 'Bearer ' + (
        make_token(sub=USER) if auth == 'valid' else 'invalid')}
    event = sample(user_id='attacker')
    response = client.post('/v1/client-events', json=event, headers=headers)
    assert response.status_code == 202
    assert response.json() == {'accepted': True}
    assert store[event['event_id']]['user_id'] == (USER if auth == 'valid' else None)
    assert seen == (['ip', 'global', 'user'] if auth == 'valid' else ['ip', 'global'])
    assert store[event['event_id']]['ip_hmac'] != 'testclient'


def test_ip_limit_precedes_jwt_and_body(ingestion, monkeypatch):
    client, store, _ = ingestion

    async def deny(conn, scope, key):
        assert scope == 'ip'
        return False

    def jwt_must_not_run(token):
        pytest.fail('JWT before IP limit')

    monkeypatch.setattr(events, 'consume_limit', deny)
    client.app.state.jwt_key_resolver = jwt_must_not_run
    assert client.post('/v1/client-events', content='invalid JSON', headers={
        'Authorization': 'Bearer invalid'}).status_code == 202
    assert not store


@pytest.mark.parametrize('scope', ['global', 'user'])
def test_shared_limits_drop_quietly(ingestion, monkeypatch, make_token, scope):
    client, store, _ = ingestion

    async def limit(conn, current, key):
        return current != scope

    monkeypatch.setattr(events, 'consume_limit', limit)
    assert client.post('/v1/client-events', json=sample(), headers={
        'Authorization': 'Bearer ' + make_token(sub=USER)}).status_code == 202
    assert not store


def test_duplicate_event_id(ingestion):
    client, store, _ = ingestion
    event = sample()
    for _ in range(2):
        assert client.post('/v1/client-events', json=event).status_code == 202
    assert len(store) == 1


def test_chunked_body_stops_before_full_read():
    reads = []
    chunks = [b'x' * 16384, b'y' * 16385, b'never-read']

    async def receive():
        reads.append(1)
        chunk = chunks.pop(0)
        return {'type': 'http.request', 'body': chunk, 'more_body': bool(chunks)}

    request = Request({'type': 'http', 'headers': []}, receive)
    with pytest.raises(HTTPException) as error:
        asyncio.run(events.read_body(request))
    assert error.value.status_code == 413
    assert len(reads) == 2
    assert chunks == [b'never-read']


@pytest.mark.parametrize('body,code', [('x' * 32769, 413), ('{invalid', 422), ('[]', 422), ('{}', 422)], ids=['large', 'malformed', 'array', 'missing'])
def test_invalid_body(ingestion, body, code):
    client, _, _ = ingestion
    assert client.post('/v1/client-events', content=body).status_code == code


def test_allowlist_sanitization_and_signature():
    event = events.sanitize_event(sample(
        user_id=USER, signature='forged', message='private input', stack='private file',
        build_sha='a' * 100, device='desktop', unknown='private',
        context={'online': True, 'visibility': 'visible', 'photo_pending_count': 4,
                 'local_backup': True, 'draft_slot_status': 'owned', 'input': 'private'},
        breadcrumbs=[{'at': '2026-10-08T08:00:00Z', 'type': 'api', 'method': 'POST',
                      'route': '/v1/projects/' + USER + '/editor?secret=yes#hash',
                      'action': 'save', 'request_id': RID, 'status': 500,
                      'error_code': 'x' * 200, 'message': 'private', 'count': 2}] * 40))
    assert event['route'] == '/projects/:id/editor'
    assert len(event['build_sha']) <= 64
    assert 'message' not in event and 'stack' not in event and 'user_id' not in event
    assert 'unknown' not in event and 'signature' not in event
    assert len(event['breadcrumbs']) == 30
    assert 'input' not in event['context']
    crumb = event['breadcrumbs'][0]
    assert crumb['route'] == '/v1/projects/:id/editor'
    assert 'message' not in crumb and len(crumb['error_code']) <= 64
    expected = hashlib.sha256(json.dumps([
        'api_error', 'save', '/projects/:id/editor', 500, 'save_failed'
    ], ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()
    assert events.event_signature(event) == expected


def test_unknown_action_and_route_never_store_free_text():
    event = events.sanitize_event(sample(action='email@example.com', route='/Alice/file.png?q=secret',
                                         error_code='a@example.com', device='secret input'))
    assert event['action'] == 'unknown'
    assert event['route'] == '/:segment/:segment'
    assert event['error_code'] is None and event['device'] == 'unknown'


def test_ip_hmac_uses_secret():
    assert events.hash_ip('1.2.3.4', b'a' * 32) != events.hash_ip('1.2.3.4', b'b' * 32)
    assert len(events.hash_ip('1.2.3.4', b'a' * 32)) == 64


def test_nested_unknown_types_are_sanitized():
    event = events.sanitize_event(sample(action=[], device={}, breadcrumbs=[{
        'type': [], 'action': {}, 'method': [], 'request_id': [], 'error_code': {}}]))
    assert event['action'] == 'unknown'
    assert event['breadcrumbs'][0]['action'] == 'unknown'


def test_user_lock_precedes_counter_for_withdrawal_safety(ingestion, monkeypatch, make_token):
    client, _, _ = ingestion
    order = []

    async def lock(conn, user_id):
        order.append('lock')

    async def limit(conn, scope, key):
        if scope == 'user':
            order.append('counter')
        return True

    monkeypatch.setattr(events, 'lock_user_events', lock)
    monkeypatch.setattr(events, 'consume_limit', limit)
    assert client.post('/v1/client-events', json=sample(), headers={
        'Authorization': 'Bearer ' + make_token(sub=USER)}).status_code == 202
    assert order == ['lock', 'counter']


def test_daily_ip_hash_is_also_the_rate_limit_key(ingestion):
    client, store, _ = ingestion
    event = sample()
    response = client.post('/v1/client-events', json=event)
    assert response.status_code == 202
    assert store[event['event_id']]['ip_hmac'] == events.hash_ip('testclient', b's' * 32)
    assert client.app.state.test_client_event_limit_keys['ip'] == store[event['event_id']]['ip_hmac']
