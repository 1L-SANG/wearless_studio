import json
from pathlib import Path

from app.agents import cut_generator, space_set_assets

ROOT = Path(__file__).resolve().parents[2]
CHANGES = json.loads((ROOT / 'tests/fixtures/verified-example-tags-20261106.json').read_text())


def test_published_pocket_pose_applicability_agrees_between_client_and_server():
    frontend = {entry['id']: entry for entry in json.loads((ROOT / 'src/data/genExamples.json').read_text())}
    _, backend = cut_generator.load_example_asset_registry()
    for change in CHANGES:
        if not change.get('excludeClothingTypes'):
            continue
        example_id = change['id']
        assert frontend[example_id]['applicableClothingTypes'] == backend[example_id]['applicableClothingTypes'] == ['top', 'outer']
        for clothing in change['excludeClothingTypes']:
            assert cut_generator.example_asset_status(example_id, clothing) == 'not_applicable'
        for clothing in ('top', 'outer'):
            assert cut_generator.example_asset_status(example_id, clothing) == 'available'


def test_corrected_set_references_reach_direction_and_face_attachment_rules():
    _, registry = space_set_assets.load_space_set_registry()
    frontend = json.loads((ROOT / 'src/data/storyboardSpaceSets.json').read_text())['sets']
    frontend_members = {member['exampleId']: member for group in frontend for member in group['members']}
    for change in CHANGES:
        if change.get('excludeClothingTypes'):
            continue
        group = next(group for group in registry.values() if any(member['exampleId'] == change['id'] for member in group['members']))
        member = next(member for member in group['members'] if member['exampleId'] == change['id'])
        block = {**member, 'faceExposure': 'same'}
        clothing = group['applicableClothingTypes'][0]
        reference = space_set_assets.resolve_published_example_reference(block, clothing_type=clothing, gender=group['gender'], scope='all')
        assert reference['directionCompatible'] is True
        for field in ('shot', 'direction'):
            if field in change:
                assert member[field] == reference[field] == frontend_members[change['id']][field] == change[field]
        if member['direction'] == 'back' or (clothing == 'bottom' and member['shot'] == 'medium'):
            assert cut_generator._face_fits(block, is_bottom=clothing == 'bottom') is False


def test_s10_seated_lower_body_crop_keeps_face_out_of_frame():
    _, registry = cut_generator.load_example_asset_registry()
    entry = registry['ex_styling_women_bottom_medium_snapshot_04']
    assert entry['shot'] == 'medium'
    assert not cut_generator._face_fits({**entry, 'faceExposure': 'same'}, is_bottom=True)
