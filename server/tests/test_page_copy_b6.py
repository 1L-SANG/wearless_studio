"""B6 review inputs, with synthetic prompts/providers and unchanged source data."""
import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from app import repo, routes
from app.agents import page_copy as pc, page_copy_checks as checks
from conftest import make_settings, patch_route_db, auth_headers
from page_copy_test_support import page_copy_prompt_dir
from test_page_copy import PRODUCT, slot
from test_page_copy_b4 import seller_facts, CLAIM_REASONS

pytestmark = pytest.mark.usefixtures('page_copy_prompt_dir')


@pytest.mark.parametrize('previous', [None, {'aiSuggestedPoints': ['지난 분석 추천']}])
def test_normal_analysis_and_reanalysis_persist_suggestions_through_later_chip_save(previous):
    from app.agents.product_analyst import distribute
    from conftest import assert_query_binds
    from test_analysis_server_owned_fields import _save
    payload = distribute({'clothingType': 'top', 'materials': [{'name': '면', 'ratio': 100}],
                          'aiSuggestedPoints': ['구김이 안 가요']})['analysis']
    class Cursor:
        result = None
        async def __aenter__(self):return self
        async def __aexit__(self, *args):return False
        async def execute(self, sql, params):
            assert_query_binds(sql, params)
            self.result = None
            if sql.startswith('select id from jobs'):self.result = {'id': 'job'}
            if sql.startswith('insert into analyses'):
                conn.saved = deepcopy(params[1].obj)
                self.result = {'payload': conn.saved}
        async def fetchone(self):return self.result
    class Connection:
        saved = previous
        def cursor(self):return Cursor()
    conn = Connection()
    result = asyncio.run(repo.finalize_analyze_success(conn, job_id='job', lease_token='lease', user_id='seller',
        project_id='p1', clothing_type=None, analysis_payload=payload, result={'data': payload}, metadata={}))
    assert conn.saved['aiSuggestedPoints'] == ['구김이 안 가요']
    assert result['result']['data']['aiSuggestedPoints'] == ['구김이 안 가요']
    saved = _save(conn.saved, {'sellingPoints': ['구김이 안 가요']}, merge=True)
    assert saved['aiSuggestedPoints'] == ['구김이 안 가요']
    assert not pc.facts_bundle({}, saved, [])['sellerClaims']


@pytest.mark.parametrize('point', ['구김이 안 가요', '  구김이  안\t가요  ', '구김이안가요'])
def test_automatic_chip_is_not_seller_evidence_but_remains_generation_input(point):
    analysis = {'sellingPoints': [point, '구김이 생기지 않아요', '직접 추가한 특징'],
                'aiSuggestedPoints': ['구김이 안 가요']}
    before = deepcopy(analysis)
    s = slot('feature', 'desc', target='feature')
    facts = pc.facts_bundle(PRODUCT, analysis, [s])
    assert facts['sellingPoints'] == [p.strip() for p in analysis['sellingPoints']]
    claims = [c['text'] for c in facts['sellerClaims'] if c['source'].startswith('sellingPoints.')]
    assert claims == ['구김이 생기지 않아요', '직접 추가한 특징']
    assert analysis == before


@pytest.mark.parametrize('edited', [False, True])
def test_actual_auto_feature_route_distinguishes_automatic_and_edited_chips(client, make_token, monkeypatch, edited):
    patch_route_db(monkeypatch, routes)
    point = '구김이 생기지 않아요' if edited else '구김이 안 가요'
    analysis = {'sellingPoints': [point], 'aiSuggestedPoints': ['구김이 안 가요']}
    monkeypatch.setattr(repo, 'get_project', AsyncMock(return_value={'id': 'p1'}))
    monkeypatch.setattr(repo, 'get_product', AsyncMock(return_value={'name': '셔츠', 'name_source': 'ai'}))
    monkeypatch.setattr(repo, 'get_analysis', AsyncMock(return_value=analysis))
    saved = AsyncMock(); monkeypatch.setattr(repo, 'save_analysis', saved)
    monkeypatch.setattr(client.app.state, 'settings', make_settings(gemini_api_key='fixture-key'))
    calls = []
    async def fake(settings, request, timeout, metadata):
        calls.append(request)
        return {'response': {'featureCopy': [{'point': point, 'desc': '구김이 잘 안 생겨요'}]}}
    monkeypatch.setattr(pc, '_call', fake)
    response = client.post('/v1/projects/p1/feature-copy:draft', headers=auth_headers(make_token))
    assert response.status_code == (200 if edited else 502)
    assert saved.await_count == (1 if edited else 0)
    assert calls and calls[0]['slots'][0]['point'] == point


@pytest.mark.parametrize('source,expected', [
    ('핏이 편해요. 2만 9천 원짜리예요.', '핏이 편해요.'),
    ('핏이 편해요.무료 배송이에요.', '핏이 편해요.'),
    ('스티치 간격이 1.5cm예요.무료 배송이에요.', '스티치 간격이 1.5cm예요.'),
    ('핏이 편해요!무료 배송이에요.소매가 길어요.', '핏이 편해요! 소매가 길어요.'),
    ('핏이 편해요. https://shop.example.com에서 주문해요.', '핏이 편해요.'),
])
def test_md_review_sentences_keep_safe_text_and_decimals(source, expected):
    assert checks.clean_seller_text(source) == expected


@pytest.mark.parametrize('source', ['원단이 튼튼해요', '소재가 튼튼해요'])
def test_fabric_durability_cannot_move_to_a_zipper(source):
    s = slot('points', 'items.0.desc', target='part', template='show')
    s['point'] = '지퍼'
    facts = seller_facts('sellingPoints', source, [s])
    facts['parts'] = [{'name': '지퍼', 'evidence': '지퍼가 달려 있어요'}]
    assert CLAIM_REASONS.intersection(pc._slot_reasons('튼튼한 지퍼', s, facts, {}))


@pytest.mark.parametrize('text', ['튼튼한 원단이에요', '튼튼한 소재예요', '튼튼해요'])
def test_fabric_durability_stays_allowed_in_its_own_fabric_body(text):
    s = slot('fabric', 'body', target='section')
    facts = seller_facts('sellingPoints', '원단이 튼튼해요', [s])
    assert not CLAIM_REASONS.intersection(pc._slot_reasons(text, s, facts, {}))


@pytest.mark.parametrize('kind,role,target', [('feature', 'desc', 'feature'), ('fabric', 'body', 'section')])
@pytest.mark.parametrize('source,text', [
    ('구김이 잘 안 가요', '구김이 거의 생기지 않아요'),
    ('구김이 거의 생기지 않아요', '구김이 잘 안 가요'),
    ('물빠짐이 없어요', '물이 빠지지 않아요'),
    ('물이 빠지지 않아요', '물빠짐이 없어요'),
])
def test_same_strength_negation_and_color_bleeding_inflections_are_supported(kind, role, target, source, text):
    s = slot(kind, role, target=target)
    facts = seller_facts('sellingPoints', source, [s])
    assert not CLAIM_REASONS.intersection(pc._slot_reasons(text, s, facts, {}))


@pytest.mark.parametrize('kind,role,target', [('feature', 'desc', 'feature'), ('fabric', 'body', 'section')])
@pytest.mark.parametrize('source,text', [
    ('구김이 잘 안 가요', '구김이 전혀 생기지 않아요'),
    ('구김이 잘 안 가요', '구김이 절대 생기지 않아요'),
    ('물빠짐이 없어요', '물이 전혀 빠지지 않아요'),
    ('물빠짐이 없어요', '물이 절대 빠지지 않아요'),
])
def test_seller_negation_does_not_authorize_added_absolute_strength(kind, role, target, source, text):
    s = slot(kind, role, target=target)
    facts = seller_facts('sellingPoints', source, [s])
    assert CLAIM_REASONS.intersection(pc._slot_reasons(text, s, facts, {}))
