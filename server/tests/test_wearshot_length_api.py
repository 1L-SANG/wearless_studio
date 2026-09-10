"""Length inputs must be owned, public, scoped and frozen before reservation."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app import routes, models
from app.agents import wearshot_runtime as rt, wearshot_qc as qc, cut_generator, vision_llm
from app.agents.wearshot_contract import image_sha256
from conftest import auth_headers
from test_wearshot_api_v2 import arrange, body, TONE, TARGET, MATCH
from test_wearshot_contract_v2 import png
from test_wearshot_directed_contract import length_observed, directed
from test_wearshot_runtime_v2 import canvas, settings

LENGTH = '44444444-4444-4444-8444-444444444444'


def arrange_length(monkeypatch, client):
    state = arrange(monkeypatch)
    monkeypatch.setattr(client.app.state, 'settings', replace(client.app.state.settings, r2_bucket='public'))
    state['length'] = dict(id=LENGTH, project_id='p1', r2_key=LENGTH, r2_bucket='public',
        mime_type='image/png', source='ai', visibility='private',
        metadata={'facemarket_real_derived': False})
    state['provenance'] = {'real_derived': False, 'facemarket': None, 'cut_type': 'styling'}
    state['bytes'] = png('purple')
    async def owned(*args): return deepcopy(state['length'])
    async def provenance(*args): return deepcopy(state['provenance'])
    class Storage:
        def get_bytes(self, key):
            assert key == LENGTH
            return state['bytes'].data
    monkeypatch.setattr(routes.repo, 'get_owned_public_project_image', owned, raising=False)
    monkeypatch.setattr(routes.repo, 'get_asset_facemarket_provenance', provenance)
    monkeypatch.setattr(routes, '_r2', lambda request: Storage())
    return state


def request_length(selector='target'):
    return body() | {'lengthReferenceAssets': {selector: LENGTH}, 'directingMode': 'source_locked_v1'}


def test_optional_fields_preserve_old_model_and_snapshot_shape(client, make_token, monkeypatch):
    state = arrange(monkeypatch)
    assert models.WearshotGenerateRequest.model_validate(body()).model_dump(mode='json') == body()
    data = body() | {'lengthReferenceAssets': {}, 'directingMode': None}
    response = client.post('/v1/projects/p1/detail-page:generate', json=data, headers=auth_headers(make_token))
    assert response.status_code == 202
    snapshot = state['create'][0]['payload']['wearshotV2']
    assert snapshot['request'] == body()
    assert set(snapshot) == {'request', 'target', 'matching', 'catalog', 'matchingCatalog', 'sellerCatalog', 'selectionFingerprint'}


@pytest.mark.parametrize('selector', ['target', 'm1'])
def test_route_snapshots_exact_length_bytes_without_changing_other_anchors(client, make_token, monkeypatch, selector):
    state = arrange_length(monkeypatch, client)
    response = client.post('/v1/projects/p1/detail-page:generate', json=request_length(selector), headers=auth_headers(make_token))
    assert response.status_code == 202, response.text
    snapshot = state['create'][0]['payload']['wearshotV2']
    assert snapshot['request'] == request_length(selector)
    assert snapshot['lengthReferences'][selector]['sha256'] == image_sha256(png('purple'))
    assert snapshot['lengthReferences'][selector]['byteLength'] == len(png('purple').data)
    assert snapshot['target']['id'] == TONE and snapshot['matching']['m1']['id'] == MATCH
    assert state['reserves'] == [1]


@pytest.mark.parametrize('fault', ['unused', 'missing', 'seed', 'project', 'bucket', 'mime', 'real_marker', 'unknown_ai', 'unknown_derived', 'real_provenance', 'snapshot_provenance', 'missing_provenance', 'bad_bytes'])
def test_invalid_length_is_held_before_reservation(client, make_token, monkeypatch, fault):
    state = arrange_length(monkeypatch, client)
    data = request_length('unused' if fault == 'unused' else 'target')
    if fault == 'missing': state['length'] = None
    if fault == 'seed': state['length']['source'] = 'seed'
    if fault == 'project': state['length']['project_id'] = 'p2'
    if fault == 'bucket': state['length']['r2_bucket'] = 'face'
    if fault == 'mime': state['length']['mime_type'] = 'text/plain'
    if fault == 'real_marker': state['length']['metadata']['facemarket_real_derived'] = True
    if fault == 'unknown_ai': state['length']['metadata'] = {}
    if fault == 'unknown_derived': state['length'].update(source='derived', metadata={})
    if fault == 'real_provenance': state['provenance']['real_derived'] = True
    if fault == 'snapshot_provenance': state['provenance']['facemarket'] = {'modelId': 'real-id'}
    if fault == 'missing_provenance': state['provenance'] = None
    if fault == 'bad_bytes': state['bytes'] = SimpleNamespace(data=b'bad')
    response = client.post('/v1/projects/p1/detail-page:generate', json=data, headers=auth_headers(make_token))
    assert response.status_code == 409, response.text
    assert state['reserves'] == [] and state['create'] == []


@pytest.mark.parametrize('fault', ['uuid', 'mode'])
def test_invalid_typed_extension_rejected(client, make_token, fault):
    data = request_length()
    if fault == 'uuid': data['lengthReferenceAssets']['target'] = 'bad'
    else: data['directingMode'] = 'invented'
    response = client.post('/v1/projects/p1/detail-page:generate', json=data, headers=auth_headers(make_token))
    assert response.status_code == 422


@pytest.mark.parametrize('source', ['upload', 'ai', 'derived'])
def test_normal_owner_acl_images_on_main_storage_are_usable(client, make_token, monkeypatch, source):
    state = arrange_length(monkeypatch, client)
    state['length']['source'] = source
    if source == 'upload': state['length']['metadata'] = {}
    response = client.post('/v1/projects/p1/detail-page:generate', json=request_length(), headers=auth_headers(make_token))
    assert response.status_code == 202, response.text
    assert state['create'][0]['payload']['wearshotV2']['lengthReferences']['target']['asset']['visibility'] == 'private'


@pytest.mark.parametrize('fault', ['none', 'asset_owner', 'project_owner', 'project', 'deleted_asset', 'deleted_project', 'seed', 'mime'])
def test_length_repository_query_enforces_owner_project_and_deletion(fault):
    # Execute the real emitted query against local relational rows. SQLite needs
    # only the PostgreSQL ::text cast removed; no policy predicates are replaced.
    import sqlite3
    from app import repo
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE projects (id TEXT, user_id TEXT, deleted_at TEXT)')
    db.execute('CREATE TABLE assets (id TEXT, project_id TEXT, user_id TEXT, r2_bucket TEXT, r2_key TEXT, mime_type TEXT, source TEXT, visibility TEXT, metadata TEXT, deleted_at TEXT)')
    db.execute('INSERT INTO projects VALUES (?, ?, ?)', ('p1', 'other' if fault == 'project_owner' else 'u1', 'deleted' if fault == 'deleted_project' else None))
    db.execute('INSERT INTO assets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (LENGTH, 'p2' if fault == 'project' else 'p1', 'other' if fault == 'asset_owner' else 'u1',
         'public', 'length-image', 'text/plain' if fault == 'mime' else 'image/png',
         'seed' if fault == 'seed' else 'ai', 'private', '{}', 'deleted' if fault == 'deleted_asset' else None))
    class Cursor:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def execute(self, sql, params):
            self.rows = db.execute(sql.replace('::text', '').replace('%s', '?'), params)
        async def fetchone(self):
            row = self.rows.fetchone()
            return dict(row) if row else None
    class Connection:
        def cursor(self): return Cursor()
    try:
        result = asyncio.run(repo.get_owned_public_project_image(Connection(), 'u1', 'p1', LENGTH))
        if fault == 'none':
            assert result['id'] == LENGTH and result['visibility'] == 'private'
        else:
            assert result is None
    finally:
        db.close()


@pytest.mark.parametrize('fault', ['metadata', 'selection', 'privacy'])
def test_queued_length_metadata_or_selection_drift_is_rejected(client, make_token, monkeypatch, fault):
    state = arrange_length(monkeypatch, client)
    response = client.post('/v1/projects/p1/detail-page:generate', json=request_length(), headers=auth_headers(make_token))
    assert response.status_code == 202, response.text
    snapshot = state['create'][0]['payload']['wearshotV2']
    if fault == 'metadata': state['length']['r2_key'] = 'changed'
    if fault == 'selection': snapshot['request']['lengthReferenceAssets'] = {'m1': LENGTH}
    if fault == 'privacy': state['provenance']['real_derived'] = True
    with pytest.raises(ValueError):
        asyncio.run(rt.verify_snapshot(None, 'user', 'p1', state['project'], state['product'], state['analysis'], state['storyboard'], snapshot))


def test_replay_decoder_roundtrips_old_and_extended_metadata(tmp_path):
    from test_wearshot_contract_v2 import packet
    from app.experiments.wearshot_repair_ab import _contract
    for index, contract in enumerate((packet(), directed())):
        paths = {}
        for ref in contract.references:
            path = tmp_path / f'{index}-{ref.key}.png'
            path.write_bytes(ref.image.data)
            paths[ref.key] = path.name
        restored = _contract(contract.to_dict(), paths, tmp_path)
        assert restored.to_dict() == contract.to_dict()


@pytest.mark.parametrize('selector,drift', [('target', None), ('m1', None), ('target', 'bytes'), ('target', 'unused')])
def test_route_snapshot_prepare_generation_and_qc_share_length_packet(client, make_token, monkeypatch, selector, drift):
    state = arrange_length(monkeypatch, client)
    images = {TONE: png('blue'), TARGET: png('red'), MATCH: png('black'), 'seller-target': png('white'),
              'seller-match': png('gray'), 'model-face': png('yellow'), 'model-body': png('green'), LENGTH: png('purple')}
    old_owned = routes.repo.get_owned_mannequin_asset
    async def owned(*args):
        row = await old_owned(*args)
        if row['id'] == TONE: row['metadata']['sourceHash'] = image_sha256(images[TARGET])
        return row
    monkeypatch.setattr(routes.repo, 'get_owned_mannequin_asset', owned)
    def sheets(*args):
        return tuple(dict(key=k, mime=images[k].mime, bucket='public', byteLength=len(images[k].data), sha256=image_sha256(images[k]))
                     for k in ('model-face', 'model-body'))
    monkeypatch.setattr(cut_generator, 'resolve_confirmed_gpt_direction_sheets', sheets)
    example = png('pink')
    old_catalog = rt.catalog_projection
    def catalog(*args):
        value = old_catalog(*args)
        for row in value.values():
            row['scope']['allSha256'] = image_sha256(example)
            row['directing']['all_sha256'] = image_sha256(example)
        return value
    monkeypatch.setattr(rt, 'catalog_projection', catalog)
    async def load_example(*args, **kwargs): return example
    monkeypatch.setattr(cut_generator, 'load_example_image', load_example)
    response = client.post('/v1/projects/p1/detail-page:generate', json=request_length(selector), headers=auth_headers(make_token))
    assert response.status_code == 202, response.text
    snapshot = state['create'][0]['payload']['wearshotV2']
    asyncio.run(rt.verify_snapshot(None, 'user', 'p1', state['project'], state['product'], state['analysis'], state['storyboard'], snapshot))
    if drift == 'bytes': images[LENGTH] = png('orange')
    if drift == 'unused':
        snapshot['request']['lengthReferenceAssets'] = {'unused': LENGTH}
        snapshot['lengthReferences'] = {'unused': snapshot['lengthReferences']['target']}
    async def load_asset(row): return images[row['id']]
    async def model_image(key, *args): return images[key]
    async def prepare():
        configured = settings()
        configured.r2_bucket = 'public'
        return await rt.prepare_block(configured, None, 'user', 'p1', state['storyboard'][0], state['product'], state['analysis'],
            snapshot, load_asset=load_asset, load_model_image=model_image)
    if drift:
        with pytest.raises(ValueError, match='length'): asyncio.run(prepare())
        return
    item = asyncio.run(prepare())
    contract = item[-1]
    owner = contract.target if selector == 'target' else contract.matching[0]
    other = contract.matching[0] if selector == 'target' else contract.target
    assert owner.approved_length_key is not None and other.approved_length_key is None
    length = next(ref for ref in contract.references if ref.key == owner.approved_length_key)
    assert length.image == png('purple') and length.garment_id == owner.garment_id
    assert contract.references[0].image == example
    output = canvas()
    class Provider:
        async def generate_content_image(self, model, prompt, refs, size, **kwargs):
            assert refs == [r.image for r in contract.references]
            assert 'width/ease' in prompt
            return SimpleNamespace(image=output.data, mime=output.mime)
    async def judge(settings, model, prompt, refs, schema, timeout):
        assert refs == [*[r.image for r in contract.references], output]
        return length_observed(contract)
    monkeypatch.setattr(vision_llm, '_call_gpt', judge)
    monkeypatch.setattr(cut_generator, '_resolve_generation_model', lambda *args: 'gpt-image-2')
    generated = asyncio.run(cut_generator.generate(settings(), Provider(), item[0], state['product'], item[1], wearshot_contract=contract))
    assert generated == (output.data, output.mime)
    verdict = asyncio.run(qc.verdict(settings(), contract, output))
    assert qc.release_allowed(verdict, contract, output)
