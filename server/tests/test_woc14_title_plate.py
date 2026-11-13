from copy import deepcopy
import pytest
from PIL import Image
from app.agents.page_templates import CATALOG, validate_catalog
from app.agents.page_template_contrast import overlay_text_metadata, _plate_position
from page_template_cases import case_input, collect_input_regions, assemble_input, elements, section
KEY = 'emo.first:first.hero:center'

def plan(value):
    return next(p for p in collect_input_regions(value) if p['key'] == KEY)

def measure(value, image):
    hero = next(c for c in value['storyboard'] if c['pageSectionKind'] == 'first')
    return overlay_text_metadata(hero, image, product=value['product'], analysis=value['analysis'], storyboard=value['storyboard'], copy_results=value['copyResults'])

def paint(image, candidate, color):
    for line in candidate['lines']:
        r = line['region']; bounds = (0, int(r['top'] * 1250), 1000, int(r['bottom'] * 1250) + 1)
        image.paste(color, bounds)

def checker(image):
    # Thin stripes leave both dark and light pixels behind every visible text line.
    for x in range(1000): image.paste('white' if x % 2 else 'black', (x, 0, x + 1, 1250))

@pytest.mark.parametrize('colors,expected', [
    ({'top': 255, 'center': 128, 'bottom': 128}, 'top'),
    ({'top': 255, 'center': 245, 'bottom': 255}, 'center'),
    ({'top': 255, 'bottom': 255}, 'bottom'),
    ({'top': 255, 'bottom': 128}, 'top'),
])
def test_all_positions_are_scored_then_best_and_near_best_preference(colors, expected):
    value = case_input('emo-2-mono')
    with Image.new('RGB', (1000, 1250)) as image:
        checker(image)
        for candidate in plan(value)['positionCandidates']:
            if candidate['position'] in colors: paint(image, candidate, (colors[candidate['position']],) * 3)
        result = measure(value, image)
    assert result['overlayTextPositions'] == {KEY: expected}
    assert not result.get('overlayTextPlates')
    assert all(not line['shade'] for line in result['overlayTextLines'].values())

def test_failed_contrast_emits_plate_metadata_white_ink_no_rules_or_shades():
    value = case_input('emo-2-mono')
    with Image.new('RGB', (1000, 1250)) as image:
        checker(image); result = measure(value, image)
    assert result['overlayTextPlates'] == {KEY: True}
    assert result['overlayTextColors'] == {KEY: '#ffffff'}
    assert all(not line['shade'] for line in result['overlayTextLines'].values())
    value['cutResults'][0].update(result)
    els = elements(section(assemble_input(value), 'emo.first'))
    shapes = [e for e in els if e['type'] == 'shape']
    assert len(shapes) == 1 and shapes[0]['shape'] == 'rect' and shapes[0]['opacity'] == .6
    assert shapes[0]['fill'] == CATALOG['neutralColors']['titlePlate']
    for text in [e for e in els if e['type'] == 'text']:
        assert text['style']['color'] == '#ffffff' and text['groupId'] == shapes[0]['groupId']
        assert all(line['shade'] is False for line in text['contrastLines'])

@pytest.mark.parametrize('band,regions,expected', [
    ([.3,.5], [(0.4,.6),(.75,.95),(.05,.25)], 'bottom'),  # exactly50% is too much
    ([.18,.98],[(.4,.6),(.75,.95),(.02,.12)], 'center'),
    ([.2,.8],[(.4,.6),(.7,.9),(.1,.3)], 'bottom'), # all >=50%; tie keeps bottom before top
    ([.2,.8],[(.4,.6),(.65,.85),(.05,.25)], 'bottom'),
])
def test_plate_avoids_half_overlap_then_chooses_least(band, regions, expected):
    p = {'garmentBand':band,'plateCandidates':[{'position':position,'region':{'top':start,'bottom':end}} for position,(start,end) in zip(['center','bottom','top'],regions)]}
    assert _plate_position(p) == expected

@pytest.mark.parametrize('kind', ['top','outer','bottom','dress'])
@pytest.mark.parametrize('shot', ['full','medium'])
def test_both_engine_region_contract_reads_shared_clothing_shot_table(kind, shot):
    value=case_input('emo-2-mono');value['product']['clothingType']=kind
    next(c for c in value['storyboard'] if c['pageSectionKind']=='first')['shot']=shot
    assert plan(value)['garmentBand']==CATALOG['firstTitlePlate']['garmentBands'][kind][shot]

@pytest.mark.parametrize('position', ['top','center','bottom'])
def test_plate_box_respects_center_and_existing_edge_inset(position):
    value=case_input('emo-2-mono');value['cutResults'][0].update(overlayTextPlates={KEY:True},overlayTextPositions={KEY:position})
    plate=next(e for e in elements(section(assemble_input(value),'emo.first')) if e['type']=='shape')
    assert plate['x']+plate['w']/2==500
    if position in ('top','center'):assert plate['y']+plate['h']/2==625
    else:assert plate['y']+plate['h']==1150

def test_plate_config_validation_rejects_bad_table_and_opacity():
    for field,value in [('opacity',1.1),('garmentBands',{}),('preference',['top','center','bottom']),('platePreference',['center','bottom','top'])]:
        invalid=deepcopy(CATALOG);invalid['firstTitlePlate'][field]=value
        with pytest.raises(ValueError,match='first title plate'):validate_catalog(invalid)

@pytest.mark.parametrize('kind,expected', [('top','bottom'),('bottom','center'),('dress','bottom')])
def test_r2_full_shot_plate_never_moves_up_over_the_face(kind, expected):
    p={'garmentBand':CATALOG['firstTitlePlate']['garmentBands'][kind]['full'],
       'plateCandidates':[{'position':name,'region':{'top':start,'bottom':end}}
                          for name,start,end in [('center',.4,.6),('bottom',.72,.92),('top',.08,.28)]]}
    assert _plate_position(p)==expected

def test_r2_measurement_plan_never_includes_a_top_plate():
    p=plan(case_input('emo-2-mono'))
    assert [item['position'] for item in p['plateCandidates']]==['center','bottom']
    assert {item['position'] for item in p['positionCandidates']}=={'top','center','bottom'}
