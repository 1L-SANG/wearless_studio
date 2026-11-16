import json
from copy import deepcopy
from pathlib import Path

import pytest

from app.agents import content_roles, cut_generator, context_fill, page_templates, space_set_assets
from app.agents.cut_plan import compile_cut_plan
from app.agents.studio_examples import is_minimal_example, is_minimal_example_id, slot_allows_cut_type

ROOT = Path(__file__).resolve().parents[2]
MINIMAL_ID = 'ex_styling_men_outer_full_06'
SET_MINIMAL_ID = 'ss_set_04_men_top_indoor_window_room_anotheroffice_1881_01'
SHOW_SLOTS = [(section, slot) for section in page_templates.get_template('show')['sections']
              if section['kind'] == 'wear' for slot in section['photoSlots']]


def block_for(section, slot, **patch):
    return dict(id='old-photo', taxonomyVersion=4, source='ai', sectionId=section['instanceKey'],
                pageSectionKey=f"show.{section['instanceKey']}", pageSectionKind='wear', pageSectionUsage='fit',
                pageDesignId=section['designId'], slotKey=f"{section['instanceKey']}.{slot['slotKey']}",
                cutType='styling', shot='full', direction='front', exampleId=MINIMAL_ID) | patch


def test_minimal_tags_match_exact_tier1_ids_and_frontend():
    tier = json.loads((ROOT / 'tests/fixtures/minimal-studio-tier1-20261106.json').read_text())
    expected = {photo['id'] for place in tier for photo in place['photos'] if photo['status'] == 'live'}
    _, entries = cut_generator.load_example_asset_registry()
    _, sets = space_set_assets.load_space_set_registry()
    backend = entries | {member['exampleId']: member for group in sets.values() for member in group['members']}
    front = {entry['id']: entry for entry in json.loads((ROOT / 'src/data/genExamples.json').read_text())}
    front |= {member['exampleId']: member for group in json.loads((ROOT / 'src/data/storyboardSpaceSets.json').read_text())['sets'] for member in group['members']}
    assert {key for key, entry in backend.items() if is_minimal_example(entry)} == expected
    assert {key for key, entry in front.items() if is_minimal_example(entry)} == expected
    assert len(expected) == 16
    for key, entry in front.items():
        assert entry.get('studioStyle') == backend[key].get('studioStyle')
    assert sum(group.get('studioStyle') == 'minimal' for group in sets.values()) == 3


def test_mixed_minimal_set_is_a_data_error():
    raw = json.loads((ROOT / 'server/app/data/space_set_assets.json').read_text())
    group = next(group for group in raw['sets'] if any(is_minimal_example(member) for member in group['members']))
    del group['members'][0]['studioStyle']
    with pytest.raises(ValueError, match='mixed_studio_style_set'):
        space_set_assets.validate_space_set_registry_document(raw)


@pytest.mark.parametrize('section,slot', SHOW_SLOTS, ids=[slot['slotKey'] for _, slot in SHOW_SLOTS])
@pytest.mark.parametrize('example_id', [MINIMAL_ID, SET_MINIMAL_ID])
def test_show_horizon_only_slots_preserve_minimal_and_the_slot_content_role(section, slot, example_id):
    block = block_for(section, slot, exampleId=example_id)
    assert slot['recipe']['allowedCutTypes'] == ['horizon']
    result = content_roles.canonicalize_storyboard([block])[0]
    assert result['cutType'] == 'styling'
    assert result['exampleId'] == example_id
    assert result['contentRole'] == slot['contentRole']
    assert page_templates.wear_usage_for_block(result) == 'fit'
    assert 'studioStyle' not in result
    # The generation compiler still uses the location-photo family, including fit-purpose slots.
    spec = cut_generator.normalize_spec(result, clothing_type='outer' if example_id == MINIMAL_ID else 'top')
    spec = cut_generator.apply_reference_compatibility(spec)
    plan = compile_cut_plan(spec, 'outer' if example_id == MINIMAL_ID else 'top')
    assert plan.recipe_family == 'styling'
    assert plan.capture_mode == 'lifestyle'
    assert plan.horizon_background is None
    if example_id.startswith('ss_'):
        reference = space_set_assets.resolve_published_example_reference(result,
            clothing_type='top', gender='men', scope='all')
        assert reference['exampleId'] == example_id
        _, registry = space_set_assets.load_space_set_registry()
        group = next(group for group in registry.values() if any(member['exampleId'] == example_id for member in group['members']))
        grouped = content_roles.canonicalize_storyboard([{**result,
            'spaceGroupId': f"ssg1__{group['setId']}__minimal", 'spaceVariation': group['spaceVariation'],
            'spaceSetMemberOrder': 1}])[0]
        binding = space_set_assets.bind_storyboard_space_sets([grouped], clothing_type='top', gender='men')[id(grouped)]
        assert binding['set']['representativePlate']
        assert binding['poseReference']['exampleId'] == example_id
        grouped_spec = cut_generator.apply_reference_compatibility(cut_generator.normalize_spec(grouped, clothing_type='top'))
        grouped_plan = compile_cut_plan(grouped_spec, 'top')
        assert grouped_plan.recipe_family == 'styling'
        assert grouped_plan.reference_mode == 'pose'
        assert grouped_plan.space_set_continuity is True
        assert grouped_plan.horizon_background is None
    else:
        _, assets = cut_generator.load_example_asset_registry()
        assert content_roles.validate_storyboard_example_references([result], assets=assets,
            clothing_type='outer', gender='men') is None


@pytest.mark.parametrize('section,slot', SHOW_SLOTS, ids=[slot['slotKey'] for _, slot in SHOW_SLOTS])
@pytest.mark.parametrize('example_id', [None, 'unknown', 'ex_styling_men_outer_full_07'])
def test_show_horizon_only_slots_revert_ordinary_styling_and_ignore_client_tag(section, slot, example_id):
    block = block_for(section, slot, exampleId=example_id, studioStyle='minimal', thumb='example', baseThumb='original')
    result = content_roles.canonicalize_storyboard([block])[0]
    assert result['cutType'] == 'horizon'
    assert result['exampleId'] is None
    assert result['exampleSelectionOrigin'] is None
    assert result['thumb'] == 'original'
    assert result['contentRole'] == slot['contentRole']


def test_styling_only_slot_accepts_legacy_minimal():
    assert slot_allows_cut_type({'allowedCutTypes': ['styling']}, {'cutType': 'styling', 'exampleId': MINIMAL_ID})
    for example_id in ('unknown', None, [], {}):
        assert not is_minimal_example_id(example_id)


def test_server_automatic_styling_examples_exclude_minimal_for_every_product_condition():
    _, examples = cut_generator.load_example_asset_registry()
    for gender in ('men', 'women'):
        for clothing in ('top', 'bottom', 'outer', 'dress'):
            context = {'product': {'clothingType': clothing}, 'analysis': {'targetGenders': [gender]}, 'examples': examples}
            for shot in ('full', 'medium'):
                for direction in ('front', 'side', 'back'):
                    assert all(not is_minimal_example(entry) for _, entry in context_fill._examples_for(context, 'styling', shot, direction))


@pytest.mark.parametrize('patch', [{}, {'cutType': 'horizon', 'exampleId': None}, {'cutType': 'styling', 'exampleId': None}])
def test_old_blocks_round_trip_without_migration(patch):
    section = page_templates.get_template('show')['sections'][0]
    slot = next(slot for slot in section['photoSlots'] if slot['slotKey'] == 'hero')
    old = block_for(section, slot, **patch)
    old.update(pageSectionKind='first', pageSectionUsage=None)
    saved = deepcopy(old)
    first = content_roles.canonicalize_storyboard([old])[0]
    second = content_roles.canonicalize_storyboard([first])[0]
    assert first == second
    assert first['cutType'] == old['cutType']
    assert first.get('exampleId') == old.get('exampleId')
    assert old == saved
