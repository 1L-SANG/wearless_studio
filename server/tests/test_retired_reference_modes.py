import pytest

from app.agents import content_roles, cut_generator, cut_plan


@pytest.mark.parametrize('scope', ['pose', 'bg'])
@pytest.mark.parametrize('field', ['refScope', 'ref_scope'])
def test_old_single_cut_uses_complete_example_without_mutating_saved_record(scope, field):
    saved = {'id': 'old', 'source': 'ai', 'cutType': 'styling', 'shot': 'full',
             'direction': 'front', 'faceExposure': 'same',
             'exampleId': 'chosen', field: scope, 'thumb': 'existing-image.png'}
    canonical = content_roles.canonicalize_storyboard_block(saved, for_storage=True)
    assert canonical['refScope'] == 'all'
    assert 'ref_scope' not in canonical
    assert canonical['exampleId'] == 'chosen'
    assert canonical['thumb'] == 'existing-image.png'
    assert saved[field] == scope
    assert cut_generator.normalize_spec(saved)['refScope'] == 'all'
    plan = cut_plan.compile_cut_plan(saved, 'top')
    assert plan.reference_mode == 'all'


@pytest.mark.parametrize('cut_type,want', [('styling', 'pose'), ('horizon', 'all')])
def test_series_owns_internal_reference_mode_not_the_old_user_option(cut_type, want):
    saved = {'cutType': cut_type, 'shot': 'full', 'direction': 'front', 'faceExposure': 'same', 'exampleId': 'chosen',
             'spaceGroupId': 'ssg1__set-one__instance-one', 'refScope': 'bg'}
    assert cut_generator.normalize_spec(saved)['refScope'] == want
    assert cut_plan.compile_cut_plan(saved, 'top').reference_mode == want


def test_retired_background_asset_cannot_be_loaded_by_old_direct_call(monkeypatch):
    monkeypatch.setattr(cut_generator, 'load_example_asset_registry', lambda: (None, {
        'chosen': {'all': 'https://example.com/photo.png', 'bg': 'https://example.com/old.png'},
    }))
    assert cut_generator.resolve_example_asset('chosen', scope='bg') is None
    assert cut_generator.resolve_example_asset('chosen') == 'https://example.com/photo.png'


def test_background_edit_manifest_is_not_an_active_generation_mode():
    with pytest.raises(ValueError, match='unsupported_reference_scope'):
        cut_generator.build_manifest([], has_mannequin=False, has_match=False, mood_count=0, example_scope='bg')


def test_complete_example_still_sets_direction_and_keeps_an_audit_record(monkeypatch):
    monkeypatch.setattr(cut_generator, 'load_example_asset_registry', lambda: (None, {
        'chosen': {'cutType': 'styling', 'direction': 'back', 'all': 'photo.png'},
    }))
    saved = {'cutType': 'styling', 'exampleId': 'chosen', 'direction': 'front', 'refScope': 'all'}
    resolved, audit = cut_generator.example_direction_from_catalog(saved)
    assert resolved['direction'] == 'back'
    assert audit['fromDirection'] == 'front'
    assert audit['toDirection'] == 'back'
    assert saved['direction'] == 'front'


@pytest.mark.parametrize('scope', ['pose', 'bg'])
def test_editor_checks_effective_reference_before_model_eligibility_or_credit(client, make_token, monkeypatch, scope):
    from unittest.mock import AsyncMock
    from app import routes
    from conftest import patch_route_db, auth_headers

    patch_route_db(monkeypatch, routes)
    monkeypatch.setattr(routes.repo, 'get_project', AsyncMock(return_value={'id': 'p1'}))
    monkeypatch.setattr(routes.repo, 'get_analysis', AsyncMock(return_value={}))
    reserve = AsyncMock(side_effect=AssertionError('ineligible request must not reserve credit'))
    monkeypatch.setattr(routes.repo, 'create_job', reserve)
    response = client.post('/v1/projects/p1/editor:generate-image', headers=auth_headers(make_token), json={
        'mode': 'new', 'cutType': 'styling', 'direction': 'front', 'shot': 'full',
        'exampleId': 'ex_styling_men_outer_full_01', 'refScope': scope,
        'modelId': '00000000-0000-4000-8000-000000000001',
    })
    assert response.status_code == 409
    assert response.json()['error']['code'] == 'identity_scope_mismatch'
    reserve.assert_not_awaited()
