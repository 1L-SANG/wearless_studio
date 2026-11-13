from copy import deepcopy

import pytest

from app.agents.page_templates import CATALOG
from page_template_cases import CASES, FIXTURE, assemble_input, collect_input_regions


def test_shared_fixture_targets_current_catalog():
    assert FIXTURE["catalogVersion"] == CATALOG["catalogVersion"]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_python_matches_shared_editor_blocks(case):
    before = deepcopy(case["input"])
    assert assemble_input(case["input"]) == case["expected"]
    assert collect_input_regions(case["input"]) == case["expectedOverlayRegions"]
    assert case["input"] == before
