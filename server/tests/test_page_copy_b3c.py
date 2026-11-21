import pytest
from app.agents import page_copy as pc
from app.agents.page_copy_checks import warmth_evidence
from test_page_copy import PRODUCT, slot


@pytest.mark.parametrize('materials,expected', [
    ([{'name': '울', 'ratio': 100}], True),
    ([{'name': '양모'}], True),
    ([{'name': '캐시미어', 'ratio': 10}, {'name': '면', 'ratio': 90}], True),
    ([{'name': '캐시미어', 'ratio': 100}], True),
    ([{'name': '울', 'ratio': 0}, {'name': '면', 'ratio': 100}], False),
    ([{'name': '캐시미어', 'ratio': 0}], False),
    ([{'name': '면', 'ratio': 100}], False),
    ([], False),
])
def test_owner_wool_and_cashmere_support_warmth_without_requiring_blend(materials, expected):
    assert warmth_evidence(materials, '') is expected
    facts = pc.facts_bundle(PRODUCT, {'materials': materials}, [])
    assert facts['warmthEvidence'] is expected
    s = slot('fabric', 'body', target='section')
    reasons = pc._slot_reasons('따뜻하고 포근해요', s, facts, {})
    assert ('unverified_claim' not in reasons) is expected
    for absolute in ['하나도 안 춥고 따뜻해요', '완벽 보온으로 따뜻해요']:
        assert 'unverified_claim' in pc._slot_reasons(absolute, s, facts, {})
