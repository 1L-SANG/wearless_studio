"""A03 behavior through MockTransport. These synthetic envelopes are NOT live API fixtures."""
import asyncio
import base64
import copy
from dataclasses import replace
from io import BytesIO
import json

import httpx
from PIL import Image
import pytest

from app.services.cafe24_client import Cafe24ApiError, Cafe24Client
from app.services.mall_adapters.cafe24 import build_cafe24_product_request, match_cafe24_variants, _target
from app.services.mall_images import prepare_mall_jpeg
from app.services.mall_registration_contract import derive_registration_outcome, registration_view
from test_mall_cafe24_worker import WorkerEnv, status_error


@pytest.fixture
def setup_env(monkeypatch):
    env = WorkerEnv(monkeypatch)

    async def respond(request):
        token = request.headers['Authorization'].removeprefix('Bearer ')
        path, method = request.url.path.removeprefix('/api/v2/admin'), request.method
        body = json.loads(request.content) if request.content else {}
        try:
            if path == '/products/images':
                urls = await env.upload_description_images(token, [base64.b64decode(v['image']) for v in body['requests']])
                return httpx.Response(201, json={'images': [dict(path=url) for url in urls]})
            if path == '/products/setting':
                return httpx.Response(200, json={'products_setting': {'calculate_price_based_on': await env.product_setting(token)}})
            if path == '/products' and method == 'POST':
                return httpx.Response(201, json={'product': {'product_no': await env.create_product(token, body)}})
            if path == '/products':
                ids = await env.find_products_by_custom_code(token, request.url.params['custom_product_code'])
                return httpx.Response(200, json={'products': [dict(product_no=no, custom_product_code=request.url.params['custom_product_code']) for no in ids]})
            if path == '/products/123' and method == 'GET':
                state = await env.product_state(token, 123)
                return httpx.Response(200, json={'product': {'product_no': 123, **state}})
            if path == '/products/123' and method == 'PUT':
                await env.set_product_state(token, 123, **body['request'])
                return httpx.Response(200, json={'product': {'product_no': 123, **env.remote_state}})
            if path == '/products/123/variants' and method == 'GET':
                rows = await env.variants(token, 123)
                keys = dict(variantCode='variant_code', additionalAmount='additional_amount', useInventory='use_inventory',
                            displaySoldout='display_soldout', safetyInventory='safety_inventory')
                return httpx.Response(200, json={'variants': [{keys.get(k, k): v for k, v in row.items()} for row in rows]})
            if path == '/products/123/variants':
                changes = body['request']['variants']
                if getattr(env, 'partial_once', False):
                    env.partial_once = False
                    await env.remote('inventory', token, 123, changes)
                    env.remote_variants[0].update(_target(env.job['payload']['snapshot']['variants'][0]))
                    return httpx.Response(200, json={'variants': [{'variant_code': 'sku-black'}, {'error': {'code': 'synthetic-failure'}}]})
                await env.update_variants(token, 123, changes)
                return httpx.Response(200, json={'variants': changes})
            if path == '/products/123/images':
                await env.upload_product_image(token, 123, base64.b64decode(body['request']['detail_image'].split(',')[1]))
                return httpx.Response(201, json={'image': {'detail_image': 'https://myshop.cafe24.com/main.jpg'}})
            if path == '/products/123/additionalimages':
                images = [base64.b64decode(value.split(',')[1]) for value in body['request']['additional_image']]
                await env.upload_additional_images(token, 123, images)
                return httpx.Response(201, json={'additionalimages': [dict(additional_image=f'https://myshop.cafe24.com/sub{i}.jpg') for i in range(len(images))]})
        except httpx.HTTPStatusError as exc:
            return httpx.Response(exc.response.status_code, headers=exc.response.headers,
                                  json={'error': {'message': 'private-upstream-body'}})
        pytest.fail(f'unexpected mock request: {method} {path}')

    monkeypatch.setattr(env.worker, 'Cafe24Client', lambda *args, **kwargs:
        Cafe24Client(*args, **kwargs, transport=httpx.MockTransport(respond)))
    return env


def restart(env):
    env.job = copy.deepcopy(env.stored)
    env.job.update(status='running', result=None)
    env.stored.update(status='running', result=None)
    env.run()


def add_image(env):
    env.job['payload']['snapshot']['images'].append(dict(assetId='extra', r2Key='owned/extra.png', mimeType='image/png'))


def test_create_sends_options_material_origin_and_hidden_flags(setup_env):
    env = setup_env
    env.run()
    body = env.named('create')[0][2][0]
    request = body['request']
    assert request['display'] == request['selling'] == 'F'
    assert request['has_option'] == 'T' and request['option_type'] == 'S'
    assert request['options'] == [dict(name='색상', value=['검정', '흰색']), dict(name='사이즈', value=['FREE'])]
    assert request['made_in_code'] == 'KR' and request['product_material'] == '면 100%'
    assert request['shipping_fee_by_product'] == 'F' and not {'shipping_fee', 'address'} & request.keys()
    assert env.stored['result']['setupStatus'] == 'complete'


def test_price_basis_b_sends_only_confirmed_price_excluding_tax(setup_env):
    env = setup_env
    env.run()
    request = env.named('create')[0][2][0]['request']
    assert request['price_excluding_tax'] == request['supply_price'] == 39000 and 'price' not in request
    snapshot = copy.deepcopy(env.job['payload']['snapshot'])
    snapshot['price']['basis'] = 'selling_price'
    with pytest.raises(Cafe24ApiError, match='가격'):
        build_cafe24_product_request(snapshot, description='detail', price_basis='B', external_code='fixed')
    env.seed({})
    env.scripts['setting'] = ['S']
    previous = len(env.named('create'))
    env.run()
    assert len(env.named('create')) == previous and env.stored['result']['code'] == 'price_basis_changed'


def test_variant_mapping_uses_names_values_and_rejects_missing_or_duplicate_rows(setup_env):
    env = setup_env
    expected, rows = env.job['payload']['snapshot']['variants'], env.remote_variants
    mapping = match_cafe24_variants(expected, list(reversed(rows)))
    assert mapping == {expected[0]['variantKey']: 'sku-black', expected[1]['variantKey']: 'sku-white'}
    for actual in (rows[:1], [rows[0], rows[0]], [dict(rows[0], options=[]), rows[1]],
                   [rows[0], dict(rows[1], variantCode='sku-black')]):
        with pytest.raises(Cafe24ApiError):
            match_cafe24_variants(expected, actual)
    env.remote_variants = rows[:1]
    env.run()
    assert env.stored['metadata']['stage'] == 'mapping_variants'
    assert env.stored['result']['outcome'] == 'created' and not env.named('inventory')


def test_inventory_management_zero_stock_and_surcharges_are_applied_before_activation(setup_env):
    env = setup_env
    env.job['payload']['snapshot']['sales']['listingMode'] = 'ON_SALE'
    env.run()
    changes = env.named('inventory')[0][2][1]
    assert changes[0] == dict(variant_code='sku-black', additional_amount=0, quantity=0,
        display='T', selling='F', use_inventory='T', display_soldout='T', safety_inventory=0)
    assert changes[1]['quantity'] == 3 and changes[1]['additional_amount'] == 1000 and changes[1]['selling'] == 'T'
    names = [row[0] for row in env.calls]
    assert names.index('inventory') < names.index('thumbnail') < names.index('activate')
    assert env.remote_state['selling'] == 'T' and env.stored['result']['setupStatus'] == 'complete'


def test_partial_inventory_failure_keeps_created_hidden_and_resumes_same_product(setup_env):
    env = setup_env
    env.partial_once = True
    env.run()
    assert env.stored['result']['outcome'] == 'created' and env.stored['result']['setupStatus'] == 'needs_attention'
    assert env.remote_state['display'] == env.remote_state['selling'] == 'F' and not env.named('activate')
    assert env.remote_variants[0]['useInventory'] == 'T' and env.remote_variants[1]['useInventory'] == 'F'
    restart(env)
    assert len(env.named('create')) == 1 and len(env.named('inventory')) == 2
    assert env.stored['metadata']['productId'] == '123' and env.stored['result']['setupStatus'] == 'complete'


def test_image_crop_and_additional_upload_limits():
    raw = BytesIO()
    image = Image.new('RGB', (120, 80), 'red')
    image.paste('blue', (30, 0, 90, 80))
    exif = Image.Exif(); exif[274] = 6
    image.save(raw, format='JPEG', exif=exif)
    jpeg = prepare_mall_jpeg(raw.getvalue(), ratio=(1, 2), max_side=100)
    with Image.open(BytesIO(jpeg)) as result:
        assert result.size == (50, 100) and result.mode == 'RGB' and result.format == 'JPEG'
        assert result.getpixel((25, 50))[2] > 200
    assert len(jpeg) <= 5000000
    # Small byte inputs test upload boundaries without allocating huge photos.
    calls = []
    api = Cafe24Client('myshop', client_id='id', client_secret='secret', api_version='2026-09-01',
        transport=httpx.MockTransport(lambda req: calls.append(req) or httpx.Response(201, json={
            'additionalimages': [{'additional_image': 'https://myshop.cafe24.com/sub.jpg'}] * len(json.loads(req.content)['request']['additional_image'])})))
    assert asyncio.run(api.upload_additional_images('token', 123, [b'x'] * 20))['count'] == 20
    assert asyncio.run(api.upload_additional_images('token', 123, [b'x' * 5000000]))['count'] == 1
    for images in ([b'x'] * 21, [b'x' * 5000001], [b'x' * 5000000] * 5):
        with pytest.raises(ValueError):
            asyncio.run(api.upload_additional_images('token', 123, images))
    assert len(calls) == 2


def test_lost_additional_image_response_never_blindly_appends(setup_env):
    env = setup_env
    add_image(env)
    env.scripts['additional'] = [TimeoutError('private-upstream-body')]
    env.run()
    assert len(env.named('additional')) == 1 and not env.named('activate') and env.sleeps == []
    assert env.stored['metadata']['checkpoints']['uploading_additional_images']['status'] == 'unknown'
    assert env.stored['result']['retryAction'] == 'open_admin'
    restart(env)
    assert len(env.named('additional')) == 1 and len(env.named('create')) == 1
    assert env.stored['result']['setupStatus'] == 'needs_attention'


def test_completed_image_checkpoints_survive_worker_restart(setup_env):
    env = setup_env
    add_image(env)
    env.hooks['additional'] = lambda: setattr(env, 'alive', False)
    env.run()
    # Simulate a crash after the completed thumbnail, before the next stage response commits.
    assert env.stored['metadata']['checkpoints']['uploading_thumbnail']['status'] == 'complete'
    assert 'finalize' not in env.events
    env.alive = True
    env.hooks.clear()
    restart(env)
    assert len(env.named('thumbnail')) == 1 and len(env.named('upload')) == 2 and len(env.named('create')) == 1
    assert len(env.named('additional')) == 1
    assert env.stored['result']['retryAction'] == 'open_admin'


@pytest.mark.parametrize('crash', [False, True])
def test_activation_response_loss_only_reads_state_and_never_resets_stock(setup_env, crash):
    env = setup_env
    env.job['payload']['snapshot']['sales']['listingMode'] = 'ON_SALE'
    def activated():
        env.remote_state.update(display='T', selling='T')
        env.remote_variants[1]['quantity'] = 1  # An order can consume stock immediately.
        if crash:
            env.alive = False
    env.hooks['activate'] = activated
    env.scripts['activate'] = [TimeoutError('private')]
    env.run()
    if crash:
        assert env.stored['result'] is None and 'finalize' not in env.events
        env.alive = True
    else:
        assert env.stored['result']['setupStatus'] == 'complete'
    assert len(env.named('activate')) == 1
    before = len(env.calls)
    restart(env)
    assert [row[0] for row in env.calls[before:]] == ['state']
    assert len(env.named('inventory')) == 1 and env.remote_variants[1]['quantity'] == 1
    assert env.stored['result']['setupStatus'] == 'complete'


@pytest.mark.parametrize('mode,zero,flags', [('HIDDEN', False, ('F', 'F')), ('ON_SALE', False, ('T', 'T')), ('ON_SALE', True, ('T', 'F'))])
def test_hidden_on_sale_and_all_sold_out_final_states(setup_env, mode, zero, flags):
    env = setup_env
    env.job['payload']['snapshot']['sales']['listingMode'] = mode
    if zero:
        for row in env.job['payload']['snapshot']['variants']:
            row['quantity'] = 0
    env.run()
    assert (env.remote_state['display'], env.remote_state['selling']) == flags
    assert env.stored['result']['setupStatus'] == 'complete'
    assert any(w['code'] == 'all_sold_out' for w in env.stored['result']['warnings']) is zero


@pytest.mark.parametrize('changed', ['quantity', 'options', 'selling'])
def test_resume_stops_after_manual_remote_product_change(setup_env, changed):
    env = setup_env
    env.scripts['thumbnail'] = [status_error(422)]
    env.run()
    if changed == 'selling':
        env.remote_state.update(selling='T')
    elif changed == 'options':
        env.remote_variants[0]['options'][0]['value'] = 'M'
    else:
        env.remote_variants[1]['quantity'] = 99
    restart(env)
    assert len(env.named('create')) == 1 and len(env.named('inventory')) == 1
    assert not env.named('activate') and env.stored['result']['code'] == 'remote_product_changed'
    assert env.stored['result']['retryAction'] == 'open_admin'


def test_create_sent_commit_precedes_call_and_recovery_only_looks_up(setup_env):
    env = setup_env
    env.scripts['create'] = [TimeoutError('private')]
    env.run()  # WorkerEnv.create_product asserts that sentAt was committed before this call.
    assert len(env.named('create')) == len(env.named('find')) == 1
    assert env.stored['result']['outcome'] == 'unknown'
    before = len(env.calls)
    restart(env)
    assert [row[0] for row in env.calls[before:]] == ['find']
    assert len(env.named('create')) == 1 and env.stored['metadata']['create']['attempt'] == 1


def test_outcome_priority_product_id_rejection_and_unknown():
    for metadata, result, outcome in [({'productId': '123', 'rejectedAt': 'yes', 'create': {'sentAt': 'yes'}}, {}, 'created'),
            ({'rejectedAt': 'yes', 'create': {'sentAt': 'yes'}}, {}, 'failed'),
            ({'create': {'sentAt': 'yes'}}, {}, 'unknown'),
            ({'create': {'sentAt': 'yes'}}, {'outcome': 'duplicate_suspected'}, 'duplicate_suspected')]:
        job = dict(id='id', payload={'mall': 'cafe24'}, status='error', metadata=metadata, result=result)
        assert derive_registration_outcome(job) == outcome
        assert registration_view(job)['outcome'] == outcome


@pytest.mark.parametrize('gate', ['flag', 'disconnect', 'purge', 'cancel', 'lease'])
def test_recheck_after_token_wait_enforces_flag_disconnect_purge_and_cancel(setup_env, gate):
    env = setup_env
    def waited():
        if gate == 'flag':
            env.app.state.settings = replace(env.app.state.settings, multimall_register_enabled=False)
        elif gate == 'disconnect':
            env.connection_row['status'] = 'revoked'
        else:
            setattr(env, {'purge': 'closed', 'cancel': 'cancelled', 'lease': 'alive'}[gate], gate != 'lease')
    env.hooks['ensure'] = waited
    env.run()
    assert not env.calls and not env.stored['metadata'].get('create')
    if gate == 'lease':
        assert 'finalize' not in env.events
    else:
        assert env.stored['result']['code'] == {'flag': 'cafe24_disabled', 'disconnect': 'connection_changed', 'purge': 'account_closed', 'cancel': 'cancelled'}[gate]


@pytest.mark.parametrize('manual_change', [False, True])
def test_inventory_retry_checks_hidden_state_before_another_absolute_put(setup_env, manual_change):
    env = setup_env
    env.scripts['inventory'] = [status_error(503)]
    if manual_change:
        env.hooks['inventory'] = lambda: env.remote_state.update(selling='T')
    env.run()
    assert env.sleeps == [1]
    assert len(env.named('inventory')) == (1 if manual_change else 2)
    assert env.stored['result']['outcome'] == 'created'
    assert env.stored['result']['setupStatus'] == ('needs_attention' if manual_change else 'complete')
    if manual_change:
        assert not env.named('activate') and env.stored['result']['retryAction'] == 'open_admin'
