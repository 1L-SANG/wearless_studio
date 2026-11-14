"""WOC20 local photo measurement and copy acceptance, without provider calls."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image

from app.agents import page_template_contrast as contrast, page_part_copy
from page_template_cases import assemble_input, case_input, collect_input_regions


def faq_input():
    return case_input('woc20-emo-faq-pending')


def measure(value, image, **kwargs):
    photo = next(card for card in value['storyboard'] if card['source'] != 'text')
    return contrast.overlay_text_metadata(photo, image, product=value['product'], analysis=value['analysis'],
        storyboard=value['storyboard'], copy_results=value['copyResults'], **kwargs)


@pytest.mark.parametrize('boxes,expected', [
    ([], 'overlay'), ([(420, 70, 140, 120)], 'outside'),
    ([(420, 500, 140, 120)], 'overlay'),
    ([(420, 500, 140, 120), (500, 90, 30, 40)], 'outside'),
    ([(930, 70, 50, 50)], 'overlay'),  # touching an edge has zero overlap
    ([(929, 70, 50, 50)], 'outside'),
    ([(420, 50, float('nan'), 100)], 'outside'),
])
def test_face_positions_measure_all_faces_and_persist_the_shared_layout(monkeypatch, boxes, expected):
    detector = Mock(return_value=[SimpleNamespace(box=box) for box in boxes])
    monkeypatch.setattr(contrast, '_detect_heading_faces', detector)
    value = faq_input()
    key = next(plan['key'] for plan in collect_input_regions(value) if plan.get('avoidFaces'))
    with Image.new('RGB', (1000, 1250), 'white') as image:
        metadata = measure(value, image)
    assert detector.call_count == 1
    assert metadata['overlayTextPositions'] == {key: expected}
    value['cutResults'][0].update(metadata)
    block = next(block for block in assemble_input(value) if block.get('pageSectionKind') == 'faq')
    photo = next(el for el in block['elements'] if el['type'] == 'image')
    assert photo['y'] == (246 if expected == 'outside' else 0)
    assert any(el.get('opacity') == .3 for el in block['elements']) == (expected == 'overlay')


@pytest.mark.parametrize('failure', [FileNotFoundError, RuntimeError, ImportError])
def test_missing_or_failed_local_detector_separates_the_title(monkeypatch, failure):
    from app.agents import face_identity
    monkeypatch.setattr(face_identity, 'detect_faces', Mock(side_effect=failure('offline')))
    with Image.new('RGB', (1000, 1250)) as image:
        result = measure(faq_input(), image)
    assert set(result['overlayTextPositions'].values()) == {'outside'}


def test_unreadable_photo_also_separates_instead_of_dropping_metadata():
    result = measure(faq_input(), b'not an image')
    assert set(result['overlayTextPositions'].values()) == {'outside'}


@pytest.mark.parametrize('face_y,expected', [(710, 'outside'), (90, 'overlay')])
def test_cover_crop_is_applied_before_face_intersection(monkeypatch, face_y, expected):
    monkeypatch.setattr(contrast, '_detect_heading_faces', lambda image: [SimpleNamespace(box=(420, face_y, 100, 50))])
    with Image.new('RGB', (1000, 2500)) as image:
        result = measure(faq_input(), image)
    assert set(result['overlayTextPositions'].values()) == {expected}


def test_explicit_crop_uses_the_same_pixel_mapping_as_contrast():
    plan = {'frame': {'w': 1000, 'h': 1250, 'crop': {'iw': 2000, 'ih': 2500, 'ox': 300, 'oy': 400}},
            'region': {'left': .07, 'top': 68/1250, 'right': .93, 'bottom': 174/1250}}
    with Image.new('RGB', (1000, 1250)) as image:
        assert contrast._source_region(image, plan) == (185, 234, 615, 287)
        assert contrast._face_heading_position(image, plan, [SimpleNamespace(box=(200, 240, 30, 40))]) == 'outside'
        assert contrast._face_heading_position(image, plan, [SimpleNamespace(box=(420, 70, 140, 100))]) == 'overlay'


def test_non_faq_photos_do_not_invoke_face_detection(monkeypatch):
    detector = Mock(side_effect=AssertionError('must not inspect faces'))
    monkeypatch.setattr(contrast, '_detect_heading_faces', detector)
    value = case_input('emo-2-mono')
    hero = next(card for card in value['storyboard'] if card['pageSectionKind'] == 'first')
    with Image.new('RGB', (100, 125), 'white') as image:
        result = contrast.overlay_text_metadata(hero, image, product=value['product'], analysis=value['analysis'],
            storyboard=value['storyboard'], copy_results=value['copyResults'])
    assert result['overlayTextPositions']
    detector.assert_not_called()


@pytest.mark.parametrize('desc,accepted', [
    ('단추로 앞을 여밀 수 있어요', True),
    ('단추가 더해진 여밈으로\n차분한 옷차림을 완성해요', True),
    ('단추로 앞을 여밀 수 있어요\n', False), ('', False), ('   ', False),
    ('단추로 앞을 여밀 수 있어요\n둘째 줄이에요\n셋째 줄이에요', False),
    ('신축성이 좋아 편안해요', False),
    (' 단추로 앞을 여밀 수 있어요', False), ('단추가 있어요\t', False),
])
def test_part_validation_accepts_one_line_without_relaxing_other_guards(desc, accepted):
    assert bool(page_part_copy.validate_parts([{'point': '단추', 'desc': desc}], ['단추'])) == accepted


def test_duplicate_photo_alias_measures_faq_placement_on_the_generated_source(monkeypatch):
    value = case_input('emo-2-mono')
    hero = next(card for card in value['storyboard'] if card['pageSectionKind'] == 'first')
    faq = next(card for card in value['storyboard'] if card['pageSectionKind'] == 'faq' and card['source'] != 'text')
    detector = Mock(return_value=[SimpleNamespace(box=(420, 70, 140, 120))])
    monkeypatch.setattr(contrast, '_detect_heading_faces', detector)
    with Image.new('RGB', (1000, 1250), 'white') as image:
        result = contrast.overlay_text_metadata(hero, image, product=value['product'], analysis=value['analysis'],
            storyboard=value['storyboard'], copy_results=value['copyResults'], source_aliases={faq['id']: hero['id']})
    assert result['overlayTextPositions']['emo.faq:faq.background:heading'] == 'outside'
    assert result['overlayTextPositions']['emo.first:first.hero:center'] in ('top', 'center', 'bottom')
    detector.assert_called_once()
