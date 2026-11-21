"""Actual feature wrapper and HTTP route must not upgrade AI suggestions."""
import asyncio
from unittest.mock import AsyncMock
import pytest
from app import repo,routes
from app.agents import page_copy as pc,feature_copy as fc
from conftest import make_settings,patch_route_db,auth_headers
from page_copy_test_support import page_copy_prompt_dir
pytestmark=pytest.mark.usefixtures('page_copy_prompt_dir')


@pytest.mark.parametrize('seller',[False,True])
def test_requested_feature_points_are_separate_from_trusted_stored_points(seller):
    analysis={'sellingPoints':['구김이 생기지 않아요'] if seller else [],'aiSuggestedPoints':['구김이 안 가요']}
    async def fake(req,timeout):return {'response':{'featureCopy':[{'point':'구김이 안 가요','desc':'구김이 잘 안 생겨요'}]}}
    output=asyncio.run(fc.generate(make_settings(),['구김이 안 가요'],{},analysis,overrides={'caller':fake}))
    assert output==([{'point':'구김이 안 가요','desc':'구김이 잘 안 생겨요'}] if seller else [])
    assert analysis['sellingPoints']==(['구김이 생기지 않아요'] if seller else [])


def test_auto_feature_route_never_saves_rejected_ai_only_claim(client,make_token,monkeypatch):
    patch_route_db(monkeypatch,routes)
    monkeypatch.setattr(repo,'get_project',AsyncMock(return_value={'id':'p1'}))
    monkeypatch.setattr(repo,'get_product',AsyncMock(return_value={'name':'셔츠','name_source':'ai'}))
    monkeypatch.setattr(repo,'get_analysis',AsyncMock(return_value={'sellingPoints':[],'aiSuggestedPoints':['구김이 안 가요']}))
    saved=AsyncMock();monkeypatch.setattr(repo,'save_analysis',saved)
    monkeypatch.setattr(client.app.state,'settings',make_settings(gemini_api_key='fixture-key'))
    calls=[]
    async def fake(settings,request,timeout,metadata):
        calls.append(request)
        return {'response':{'featureCopy':[{'point':'구김이 안 가요','desc':'구김이 잘 안 생겨요'}]}}
    monkeypatch.setattr(pc,'_call',fake)
    response=client.post('/v1/projects/p1/feature-copy:draft',headers=auth_headers(make_token))
    assert response.status_code==502
    saved.assert_not_awaited()
    assert calls and calls[0]['slots'][0]['point']=='구김이 안 가요'
