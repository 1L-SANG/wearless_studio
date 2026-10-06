from unittest.mock import AsyncMock

import pytest

from app import repo, routes
from app.agents.fit_axes import confirmed_copy_fit
from conftest import auth_headers, patch_route_db
from test_analysis_server_owned_fields import _save


@pytest.mark.parametrize(('snapshot', 'expected_fit'), [
    ({'version': 1, 'profile': None}, None),
    ({'version': 1, 'profile': {'axes': {'fit': 'over'}}}, 'over'),
    (None, 'slim'),
    ({'version': 0, 'profile': None}, 'slim'),
])
def test_feature_copy_uses_selected_cut_after_merge_null_deletes_profile(
    client, make_token, monkeypatch, snapshot, expected_fit,
):
    patch_route_db(monkeypatch, routes)
    analysis = _save({'fit': 'slim', 'fitProfile': {'axes': {'fit': 'slim'}},
                      'sellingPoints': ['핏 확인']}, {'fitProfile': None}, merge=True, delete_nulls=True)
    assert 'fitProfile' not in analysis
    monkeypatch.setattr(repo, 'get_project', AsyncMock(return_value={
        'id': 'p1', 'selected_mannequin_id': 'A-1',
    }))
    monkeypatch.setattr(repo, 'get_product', AsyncMock(return_value={}))
    monkeypatch.setattr(repo, 'get_analysis', AsyncMock(return_value=analysis))
    monkeypatch.setattr(repo, 'list_mannequin_cuts', AsyncMock(return_value=[
        {'candidate': 'B', 'version': 2, 'fit_profile_snapshot': {'version': 1, 'profile': {'axes': {'fit': 'regular'}}}},
        {'candidate': 'A', 'version': 1, 'fit_profile_snapshot': snapshot},
    ]))
    save = AsyncMock(return_value={})
    monkeypatch.setattr(repo, 'save_analysis', save)
    generated = AsyncMock(return_value=[{'point': '핏 확인', 'desc': '사진으로 확인해 주세요.'}])
    monkeypatch.setattr(routes.feature_copy, 'generate', generated)
    response = client.post('/v1/projects/p1/feature-copy:draft', headers=auth_headers(make_token))
    assert response.status_code == 200
    used_analysis = generated.call_args.args[3]
    assert confirmed_copy_fit(used_analysis) == expected_fit
    assert analysis == {'fit': 'slim', 'sellingPoints': ['핏 확인']}
    assert set(save.call_args.args[2]) == {'featureCopy'}
