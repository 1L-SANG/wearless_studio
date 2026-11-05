"""Consent-first launch, encrypted quarantine and authenticated one-use binding."""

import base64
import copy
import hashlib
import hmac
import logging
import secrets
import uuid
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from app import mall_connect
from app.auth import require_user
from app.main import create_app
from app.log_privacy import protect_log_handler
from app.services.cafe24_client import Cafe24Client
from conftest import make_settings
from test_mall_cafe24_install import InstallConn, InstallCursor, BASE, CODE, ACCESS, REFRESH, SCOPES, launch_query
from test_mall_connect import FakeCursor, USER, OTHER

COOKIE = 'wl_cafe24_pending'


class PendingCursor(InstallCursor):
    async def execute(self, sql, args=()):
        q = ' '.join(sql.lower().split())
        self.result, self.rowcount = [], 0
        now = datetime.now(timezone.utc)
        if q.startswith('update mall_install_attempts set voided_at'):
            return await FakeCursor.execute(self, sql, args)
        if q.startswith('select exists') and 'from mall_connections' in q:
            user, created, revoked = args
            self.result = [{'changed': any(row['user_id'] == user and row['mall'] == 'cafe24'
                and (row['created_at'] >= created or row.get('revoked_at') is not None and row['revoked_at'] >= revoked)
                for row in self.store['connections'])}]
            return
        if q.startswith('update mall_connections set status'):
            active = [row for row in self.store['connections'] if row['user_id'] == args[0]
                      and row['mall'] == args[1] and row['status'] == 'connected']
            await super().execute(sql, args)
            for row in active: row['revoked_at'] = now
            return
        if 'from mall_connections' in q and 'as refresh_token' in q:
            kek, kek2, user, key = args
            assert kek == kek2 == 'mall-test-kek'
            row = next((row for row in self.store['connections'] if row['user_id'] == user
                        and row['id'] == key and row['mall'] == 'cafe24' and row['status'] == 'connected'), None)
            if row:
                self.result = [{**self.safe(row),
                    'access_token': row['access_key_enc'].decode().removeprefix('encrypted:'),
                    'refresh_token': row['secret_key_enc'].decode().removeprefix('encrypted:')}]
            return
        if 'cafe24_pending_installs' in q:
            self.store['sql'].append(q)
            rows = self.store['pending']
            if q.startswith('insert'):
                mall, back, cookie_hash, expires = args
                key = str(uuid.uuid4())
                rows[key] = dict(id=key, mall_id=mall, return_to=back, cookie_hash=cookie_hash,
                    expires_at=expires, callback_at=None, access_token_enc=None, refresh_token_enc=None, created_at=now)
                self.result = [{'id': key}]
            elif q.startswith('delete') and not args:
                for key, row in list(rows.items()):
                    if row['expires_at'] <= now: del rows[key]
            elif q.startswith('delete'):
                row = rows.get(args[0])
                assert 'cookie_hash = %s' in q
                claiming = 'access_token_enc is not null' in q
                if claiming: assert 'expires_at > clock_timestamp()' in q
                if row and row['cookie_hash'] == args[1] and (not claiming or
                        row['expires_at'] > now and row['access_token_enc'] and row['refresh_token_enc']):
                    self.result, self.rowcount = [rows.pop(args[0])], 1
            elif 'set callback_at' in q:
                row = rows.get(args[0])
                if row and row['cookie_hash'] == args[1] and row['callback_at'] is None and row['expires_at'] > now:
                    row['callback_at'] = now
                    self.result, self.rowcount = [copy.deepcopy(row)], 1
            else:
                assert q.count('wl_billing_encrypt') == 2 and "interval '30 minutes'" in q
                access, kek, refresh, kek2, expires, refresh_expires, scopes, shop_name, key = args
                assert kek == kek2 == 'mall-test-kek'
                row = rows.get(key)
                if row and row['callback_at'] and row['access_token_enc'] is None and row['expires_at'] > now:
                    row.update(access_token_enc=b'encrypted:' + access.encode(), refresh_token_enc=b'encrypted:' + refresh.encode(),
                        token_expires_at=expires, refresh_expires_at=refresh_expires, scopes=scopes, shop_name=shop_name, expires_at=now+timedelta(minutes=30))
                    self.rowcount = 1
            return
        if q.startswith('insert into mall_connections') and 'wl_billing_encrypt' not in q:
            user, mall, name, access, refresh, expires, refresh_expires, scopes, permissions = args
            row = dict(id=str(uuid.uuid4()), user_id=user, mall='cafe24', shop_id=mall, shop_name=name,
                access_key_enc=access, secret_key_enc=refresh, token_expires_at=expires,
                refresh_expires_at=refresh_expires, scopes=scopes, permissions=permissions,
                status='connected', created_at=now)
            self.store['connections'].append(row)
            self.result = [self.safe(row)]
            return
        if q.startswith('update mall_connections set access_key_enc'):
            access, refresh, expires, refresh_expires, scopes, permissions, shop_name, key, user = args
            row = next(r for r in self.store['connections'] if r['id'] == key and r['user_id'] == user)
            row.update(access_key_enc=access, secret_key_enc=refresh, token_expires_at=expires,
                refresh_expires_at=refresh_expires, scopes=scopes, permissions=permissions, shop_name=shop_name)
            self.result = [self.safe(row)]
            return
        if q.startswith('insert into mall_defaults') and len(args) == 2:
            args = (args[0], 'cafe24', args[1])
        await super().execute(sql, args)


class PendingConn(InstallConn):
    def cursor(self): return PendingCursor(self.store)
    async def commit(self):
        await super().commit()
        self.store['pending_committed'] = {r['id'] for r in self.store['pending'].values() if r['callback_at']}


@pytest.fixture
def env(monkeypatch):
    store = dict(connections=[], defaults={}, attempts={}, sql=[], closed_accounts=set(),
        installs={}, pending={}, committed=set(), pending_committed=set(), locks=[], remote=[], token_status=200)
    app = create_app(make_settings(mall_connect_enabled=True, cafe24_enabled=True, mall_keys_kek='mall-test-kek',
        cafe24_client_id='fixture-client', cafe24_client_secret='fixture-client-secret',
        cafe24_redirect_uri='https://api.test/v1/malls/cafe24/callback', public_web_origin='https://web.test'))
    class Pool:
        @asynccontextmanager
        async def connection(self):
            conn = PendingConn(store)
            yield conn
            await conn.commit()
    app.state.pool = Pool()
    async def respond(request):
        store['remote'].append(request.url.path)
        assert request.url.host == 'myshop.cafe24api.com'
        if request.url.path.endswith('/oauth/token'):
            assert store['pending_committed'], 'callback gate must commit before exchange'
            if hook := store.get('on_token_exchange'):
                hook()
            return httpx.Response(store['token_status'], json=store.get('token_payload', dict(
                mall_id='myshop', access_token=ACCESS, refresh_token=REFRESH, scopes=SCOPES)))
        if request.url.path.endswith('/admin/store'):
            assert request.method == 'GET' and request.headers['Authorization'] == f'Bearer {ACCESS}'
            return httpx.Response(store.get('store_status', 200), json={'store': {'shop_name': store.get('shop_name', '테스트 쇼핑몰')}})
        assert request.url.path.endswith('/oauth/revoke'), 'unexpected pending-install resource API'
        if hook := store.get('on_revoke'):
            hook()
        return httpx.Response(200)
    monkeypatch.setattr(mall_connect, 'Cafe24Client', lambda *a, **kw: Cafe24Client(*a, **kw, transport=httpx.MockTransport(respond)))
    return TestClient(app, base_url='https://api.test'), store


def launch(client, **patch):
    response = client.post(BASE+'/launch', json={'query': launch_query(**patch), 'returnTo': '/editor/project'})
    assert response.status_code == 200, response.text
    authorize = urlsplit(response.json()['startUrl'])
    assert authorize.hostname == 'myshop.cafe24api.com' and authorize.path == '/api/v2/oauth/authorize'
    return parse_qs(urlsplit(response.json()['startUrl']).query)['state'][0]


def consent(client):
    state = launch(client)
    response = client.get(BASE+'/callback', params={'state': state, 'code': CODE}, follow_redirects=False)
    assert parse_qs(urlsplit(response.headers['location']).query)['result'] == ['pending']
    return response


def login(client, user=USER):
    client.app.dependency_overrides[require_user] = lambda: user


def test_official_hmac_vector_verbatim(monkeypatch):
    # https://developers.cafe24.com/app/front/app/develop/manage/runappauth
    message = 'is_multi_shop=T&lang=ko_KR&mall_id=jhbaek02&nation=KR&shop_no=1&timestamp=1622513360&user_id=jhbaek02&user_name=jhbaek02&user_type=P'
    signature = '8%2BhYywQW5fBMpfbTlA1puAMpM91N0FYtrpzHYrdodDM%3D'
    class Then(datetime):
        @classmethod
        def now(cls, tz=None): return datetime.fromtimestamp(1622513360, timezone.utc)
    monkeypatch.setattr(mall_connect, 'datetime', Then)
    params, reason = mall_connect._verify_cafe24_launch(message+'&hmac='+signature, 'zoQxSUptApmiFLRl2ChaxB')
    assert reason == 'valid' and params['mall_id'] == 'jhbaek02'


@pytest.mark.parametrize('logged_in', [False, True])
def test_launch_immediately_authorizes_before_login_with_raw_korean_and_auth_config(env, caplog, logged_in):
    client, store = env
    if logged_in: login(client)
    caplog.set_level(logging.INFO, logger='app.mall_connect')
    state = launch(client, user_name='부운영자 + 이름', auth_config='{"Fabc1":"T","space":"a b"}')
    assert state and len(store['pending']) == 1 and not store['installs'] and not store['remote']
    assert not store['connections']
    assert store['attempts'] == {'mall-launch:myshop': 1}
    row = next(iter(store['pending'].values()))
    assert 'user_id' not in row and row['access_token_enc'] is None
    assert client.cookies[COOKIE].split('.')[0] == row['id']
    assert [r.getMessage() for r in caplog.records if r.name == 'app.mall_connect'] == ['cafe24_launch hmac_valid=true reason=valid']


def test_anonymous_launch_limit_is_shared_by_mall_even_when_query_changes(env):
    client, store = env
    for _ in range(10):
        launch(client)
    for query in (launch_query(), launch_query(user_name='다른 서명')):
        response = client.post(BASE+'/launch', json={'query': query})
        assert response.status_code == 429
        assert response.json()['error']['code'] == 'rate_limited'
        assert COOKIE not in response.headers.get('set-cookie', '')
    assert len(store['pending']) == 10 and not store['remote']
    assert client.post(BASE+'/launch', json={'query': launch_query(mall_id='other-shop')}).status_code == 200


@pytest.mark.parametrize('phase', ['before_callback', 'after_callback', 'during_exchange'])
def test_disconnect_voids_pending_consent_including_inflight_token_exchange(env, phase):
    client, store = env
    login(client)
    consent(client)
    assert client.post(BASE+'/pending/claim').status_code == 200
    state = launch(client)
    if phase == 'after_callback':
        assert parse_qs(urlsplit(client.get(BASE+'/callback', params={'state': state, 'code': CODE},
            follow_redirects=False).headers['location']).query)['result'] == ['pending']
    if phase == 'during_exchange':
        def disconnect():
            store.pop('on_token_exchange')
            assert client.delete(BASE).status_code == 204
        store['on_token_exchange'] = disconnect
    else:
        assert client.delete(BASE).status_code == 204
    if phase != 'after_callback':
        callback = client.get(BASE+'/callback', params={'state': state, 'code': CODE}, follow_redirects=False)
        result = parse_qs(urlsplit(callback.headers['location']).query)['result']
        assert result in (['state_invalid'], ['token_exchange_failed'])
    assert client.post(BASE+'/pending/claim').status_code == 409
    assert not [row for row in store['connections'] if row['status'] == 'connected']
    assert not store['pending']


def test_disconnect_in_another_browser_blocks_older_pending_claim(env):
    client, store = env
    login(client)
    consent(client)
    assert client.post(BASE+'/pending/claim').status_code == 200
    state = launch(client)
    pending_cookie = client.cookies[COOKIE]
    client.cookies.delete(COOKIE)
    assert client.delete(BASE).status_code == 204
    client.cookies.set(COOKIE, pending_cookie, domain='api.test', path=BASE)
    callback = client.get(BASE+'/callback', params={'state': state, 'code': CODE}, follow_redirects=False)
    assert parse_qs(urlsplit(callback.headers['location']).query)['result'] == ['pending']
    response = client.post(BASE+'/pending/claim')
    assert response.status_code == 409 and response.json()['error']['code'] == 'connection_changed'
    assert not [row for row in store['connections'] if row['status'] == 'connected']


def test_fresh_launch_during_remote_revoke_keeps_its_cookie_and_can_connect(env):
    client, store = env
    login(client)
    consent(client)
    assert client.post(BASE+'/pending/claim').status_code == 200
    fresh = []
    store['on_revoke'] = lambda: fresh.append(launch(client))
    assert client.delete(BASE).status_code == 204
    store.pop('on_revoke')
    assert len(fresh) == 1 and COOKIE in client.cookies
    callback = client.get(BASE+'/callback', params={'state': fresh[0], 'code': CODE}, follow_redirects=False)
    assert parse_qs(urlsplit(callback.headers['location']).query)['result'] == ['pending']
    assert client.post(BASE+'/pending/claim').status_code == 200


@pytest.mark.parametrize('age', [-7199, -3601, 3601, 7199])
def test_two_hour_window(env, age):
    launch(env[0], timestamp=str(int(datetime.now(timezone.utc).timestamp())+age))


@pytest.mark.parametrize('invalid', ['missing', 'wrong', 'tampered', 'malformed', 'bad_timestamp', 'missing_timestamp',
    'old', 'future', 'duplicate_mall', 'duplicate_hmac', 'duplicate_timestamp', 'many', 'hmac_first', 'hmac_middle'])
def test_invalid_launch_is_rejected_without_any_side_effect(env, invalid, caplog):
    client, store = env
    query = launch_query()
    if invalid == 'missing': query = 'mall_id=myshop'
    elif invalid == 'wrong': query = query.split('&hmac=')[0]+'&hmac='+base64.b64encode(b'x'*32).decode()
    elif invalid == 'tampered': query = query.replace('myshop', 'other-shop')
    elif invalid == 'malformed': query = query.split('&hmac=')[0]+'&hmac=%ZZ'
    elif invalid in {'old', 'future'}: query = launch_query(timestamp=str(int(datetime.now(timezone.utc).timestamp())+(-7201 if invalid == 'old' else 7201)))
    elif invalid in {'missing_timestamp', 'bad_timestamp'}: query = launch_query(timestamp='' if invalid == 'missing_timestamp' else 'nan')
    elif invalid == 'many': query += '&'+'&'.join(f'p{i}=x' for i in range(101))
    elif invalid == 'hmac_first': query = 'hmac=x&'+query.split('&hmac=')[0]
    elif invalid == 'hmac_middle': query += '&unsigned=yes'
    else: query += {'duplicate_mall':'&mall_id=other-shop','duplicate_hmac':'&hmac=x','duplicate_timestamp':'&timestamp=1'}[invalid]
    response = client.post(BASE+'/launch', json={'query': query})
    assert response.json() == {'hmacValid': False}
    assert not store['pending'] and not store['remote']
    assert query not in caplog.text+response.text


def test_validation_does_not_echo_raw_launch(env):
    client, _ = env
    response = client.post(BASE+'/launch', json={'query': launch_query()+'x'*16384})
    assert response.status_code == 422 and 'hmac=' not in response.text


def test_callback_quarantines_encrypted_tokens_then_login_claims_once(env, caplog):
    client, store = env
    protect_log_handler(caplog.handler)
    response = consent(client)
    row = next(iter(store['pending'].values()))
    assert row['access_token_enc'] == b'encrypted:'+ACCESS.encode()
    assert row['refresh_token_enc'] == b'encrypted:'+REFRESH.encode()
    assert 1798 < (row['expires_at']-datetime.now(timezone.utc)).total_seconds() <= 1800
    assert not store['connections'] and not store['defaults']
    assert store['remote'] == ['/api/v2/oauth/token', '/api/v2/admin/store']
    cookie = response.headers['set-cookie']
    assert all(value in cookie for value in ['HttpOnly', 'Secure', 'SameSite=lax', 'Max-Age=1800'])
    assert parse_qs(urlsplit(response.headers['location']).query)['returnTo'] == ['/editor/project']
    assert client.post(BASE+'/pending/claim').status_code == 401
    login(client)
    old_cookie = client.cookies[COOKIE]
    claimed = client.post(BASE+'/pending/claim')
    assert claimed.json() == {'connected': True} and COOKIE not in client.cookies
    assert len(store['connections']) == 1 and not store['pending']
    assert store['connections'][0]['user_id'] == USER
    assert all(value not in claimed.text+str(response.headers)+caplog.text for value in (ACCESS, REFRESH, CODE))
    client.cookies.set(COOKIE, old_cookie)
    assert client.post(BASE+'/pending/claim').status_code == 409
    assert len(store['connections']) == 1


@pytest.mark.parametrize('invalid', ['expired', 'other_browser', 'wrong_secret', 'unapproved', 'account_closed'])
def test_pending_claim_rejects_missing_expired_or_wrong_browser_and_closed_account(env, invalid):
    client, store = env
    if invalid == 'unapproved': launch(client)
    else: consent(client)
    row = next(iter(store['pending'].values()))
    if invalid == 'expired': row['expires_at'] = datetime.now(timezone.utc)-timedelta(seconds=1)
    elif invalid == 'other_browser': client.cookies.clear()
    elif invalid == 'wrong_secret':
        client.cookies.clear(); client.cookies.set(COOKIE, row['id']+'.'+secrets.token_urlsafe(32))
    elif invalid == 'account_closed': store['closed_accounts'].add(USER)
    login(client)
    response = client.post(BASE+'/pending/claim')
    assert response.status_code == (404 if invalid == 'account_closed' else 409)
    assert not store['connections'] and not store['defaults']


@pytest.mark.parametrize('stage', ['launch', 'callback', 'claim'])
def test_allowlist_applies_at_every_stage(env, stage):
    client, store = env
    if stage == 'callback': state = launch(client)
    elif stage == 'claim': consent(client); login(client)
    client.app.state.settings = replace(client.app.state.settings, cafe24_allowed_mall_ids=frozenset({'other-shop'}))
    if stage == 'launch': response = client.post(BASE+'/launch', json={'query':launch_query()})
    elif stage == 'callback':
        response = client.get(BASE+'/callback', params={'state':state,'code':CODE}, follow_redirects=False)
        assert parse_qs(urlsplit(response.headers['location']).query)['result'] == ['not_allowed']
        assert not store['remote']; return
    else: response = client.post(BASE+'/pending/claim')
    assert response.status_code == 403 and not store['connections']


@pytest.mark.parametrize('same_account', [True, False])
def test_same_mall_reuses_same_account_row_and_preserves_cross_account_policy(env, same_account):
    client, store = env
    consent(client); login(client)
    assert client.post(BASE+'/pending/claim').status_code == 200
    key = store['connections'][0]['id']
    consent(client)
    if not same_account: login(client, OTHER)
    assert client.post(BASE+'/pending/claim').status_code == 200
    assert len(store['connections']) == (1 if same_account else 2)
    assert store['connections'][0]['id'] == key


@pytest.mark.parametrize('invalid', ['missing_cookie', 'wrong_cookie', 'expired', 'reused'])
def test_pending_callback_is_browser_bound_and_one_use(env, invalid):
    client, store = env
    state = launch(client)
    if invalid == 'missing_cookie': client.cookies.clear()
    elif invalid == 'wrong_cookie':
        value = client.cookies[COOKIE].split('.')[0]+'.wrong'
        client.cookies.clear(); client.cookies.set(COOKIE, value)
    elif invalid == 'expired': next(iter(store['pending'].values()))['expires_at'] = datetime.now(timezone.utc)-timedelta(seconds=1)
    else: client.get(BASE+'/callback', params={'state':state,'code':CODE}, follow_redirects=False)
    remote = list(store['remote'])
    response = client.get(BASE+'/callback', params={'state':state,'code':CODE}, follow_redirects=False)
    assert parse_qs(urlsplit(response.headers['location']).query)['result'] == ['state_invalid']
    assert store['remote'] == remote


def test_pending_migration_is_server_only():
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / 'supabase/migrations/20261105090000_cafe24_pending_installs.sql'
    sql = ' '.join(path.read_text().lower().split())
    assert 'alter table public.cafe24_pending_installs enable row level security' in sql
    assert 'revoke all on public.cafe24_pending_installs from public, anon, authenticated' in sql
    assert 'grant all on public.cafe24_pending_installs to service_role' in sql
    assert 'create policy' not in sql


def test_hmac_keeps_original_order_percent_case_and_space_encoding():
    now = int(datetime.now(timezone.utc).timestamp())
    message = f'user_name=%EB%B6%80%20%EC%9a%B4+%EC%98%81%EC%9e%90&auth_config=%7b%22Fabc1%22%3a%22T%22%7d&timestamp={now}&mall_id=myshop'
    digest = base64.b64encode(hmac.digest(b'fixture-client-secret', message.encode(), 'sha256')).decode()
    params, reason = mall_connect._verify_cafe24_launch(message+'&'+urlencode({'hmac':digest}), 'fixture-client-secret')
    assert reason == 'valid' and params['mall_id'] == 'myshop'


@pytest.mark.parametrize('failure', ['cancelled', 'exchange', 'mall_mismatch', 'missing_permission', 'storage'])
def test_pending_callback_failures_never_connect_or_use_resource_apis(env, monkeypatch, failure):
    from app import repo
    client, store = env
    state = launch(client)
    if failure == 'exchange': store['token_status'] = 500
    if failure in {'mall_mismatch', 'missing_permission'}:
        store['token_payload'] = dict(mall_id='other-shop' if failure == 'mall_mismatch' else 'myshop',
            access_token=ACCESS, refresh_token=REFRESH, scopes=SCOPES[:-2] if failure == 'missing_permission' else SCOPES)
    if failure == 'storage':
        async def fail(*args, **kwargs): raise RuntimeError('fixture-storage-failure')
        monkeypatch.setattr(repo, 'save_cafe24_pending_tokens', fail)
    response = client.get(BASE+'/callback', params={'state':state, **({'error':'access_denied'} if failure == 'cancelled' else {'code':CODE})}, follow_redirects=False)
    expected = {'exchange':'token_exchange_failed', 'storage':'token_exchange_failed'}.get(failure, failure)
    assert parse_qs(urlsplit(response.headers['location']).query)['result'] == [expected]
    assert not store['connections'] and not store['defaults']
    assert all(row['access_token_enc'] is None for row in store['pending'].values())


def test_claim_invalidates_older_settings_attempt_and_preserves_its_cookie_until_claim(env):
    client, store = env
    login(client)
    installed = client.post(BASE+'/install', json={'mallId':'myshop'})
    started = client.get(installed.json()['startUrl'], follow_redirects=False)
    state = parse_qs(urlsplit(started.headers['location']).query)['state'][0]
    old_cookie = client.cookies['wl_cafe24_install']
    consent(client)
    assert client.cookies['wl_cafe24_install'] == old_cookie
    assert client.post(BASE+'/pending/claim').status_code == 200
    assert next(iter(store['installs'].values()))['voided_at'] is not None
    calls = list(store['remote'])
    response = client.get(BASE+'/callback', params={'state':state, 'code':CODE}, follow_redirects=False)
    assert parse_qs(urlsplit(response.headers['location']).query)['result'] == ['state_invalid']
    assert store['remote'] == calls


@pytest.mark.parametrize('store_status, expected_name', [(200, '새 쇼핑몰 이름'), (500, 'myshop')])
def test_pending_install_saves_store_name_or_mall_id_fallback(env, store_status, expected_name):
    client, store = env
    store['store_status'] = store_status
    store['shop_name'] = '새 쇼핑몰 이름'
    consent(client)
    login(client)
    assert client.post(BASE+'/pending/claim').status_code == 200
    assert store['connections'][0]['shop_name'] == expected_name


def test_pending_reconsent_refreshes_existing_shop_name(env):
    client, store = env
    consent(client); login(client)
    assert client.post(BASE+'/pending/claim').status_code == 200
    previous = store['connections'][0]['id']
    store['shop_name'] = '바뀐 쇼핑몰 이름'
    consent(client)
    assert client.post(BASE+'/pending/claim').status_code == 200
    assert len(store['connections']) == 1 and store['connections'][0]['id'] == previous
    assert store['connections'][0]['shop_name'] == '바뀐 쇼핑몰 이름'
