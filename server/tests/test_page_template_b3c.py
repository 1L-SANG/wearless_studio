import json,re
from pathlib import Path
from page_template_cases import assemble_input,case_input
from app.agents.page_template_assembler import wrap_text

SOURCE=json.loads((Path(__file__).parents[2]/'tests/fixtures/page_copy_b3c.json').read_text())


def test_preview3_brown_uses_actual_font_width_and_keeps_word():
    elements=[e for b in assemble_input(SOURCE['colorInput']) for e in b['elements']]
    brown=next(e for e in elements if e.get('text')=='BROWN')
    assert brown.get('keepWords')
    required=SOURCE['brownAdvanceUnits']*brown['style']['size']/1000+5*brown['style']['tracking']
    assert brown['w']>=required
    assert wrap_text('BROWN DARK BROWN',100,28)==['BROWN','DARK','BROWN']


def test_scrim_three_stops_keep_first_copy_on_readable_band():
    for template in ['emo','show']:
        block=assemble_input(case_input(f'copy-b-{template}-I09-SCRIM-01-static-stars'))[0]
        scrim=next(e for e in block['elements'] if e.get('fill','').startswith('linear-gradient'))
        stops=[(float(a),float(p)/100) for a,p in re.findall(r'rgba\(0, 0, 0, ([\d.]+)\) ([\d.]+)%',scrim['fill'])]
        assert len(stops)==3 and stops[0]==(0,0) and stops[1][0]==.56
        assert 0<stops[1][1]<=.3
        first=min(e['y'] for e in block['elements'] if e['type']=='text' and not e.get('hidden') and (e.get('copyRole')=='lead' or e.get('copyRole','').startswith('items.')))
        assert first>=scrim['y']+scrim['h']*stops[1][1]


def test_latin_width_table_is_tied_to_the_bundled_font_files():
    import hashlib
    root=Path(__file__).parents[2]
    data=json.loads((root/'server/app/data/page_template_latin_metrics.json').read_text())
    for font in data['fonts'].values():
        assert hashlib.sha256((root/font['source']).read_bytes()).hexdigest()==font['sha256']
