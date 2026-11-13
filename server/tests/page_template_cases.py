"""The same checked-in fixture is consumed by Node and Python (no model calls)."""
import json
from copy import deepcopy
from pathlib import Path

from app.agents.page_assembler import assemble
from app.agents.page_template_assembler import collect_overlay_regions

FIXTURE = json.loads((Path(__file__).parents[2] / "tests" / "fixtures" / "page_templates_cases.json").read_text(encoding="utf-8"))
CASES = FIXTURE["cases"]
MATRIX = [case for case in CASES if len(case["name"].split("-")) == 3 and case["name"].split("-")[0] in ("emo", "show") and case["name"].split("-")[1] in ("1", "2", "3")]


def case_input(name):
    return deepcopy(next(case["input"] for case in CASES if case["name"] == name))


def assemble_input(value):
    return assemble(value["storyboard"], value.get("cutResults", []), value.get("copyResults", []), value.get("product", {}), value.get("copywriting", True),
                    analysis=value.get("analysis"), page_style=value.get("pageStyle"), base_swatch_id=value.get("baseSwatchId"),
                    failed_cuts=value.get("failedCuts"), license_notice=value.get("licenseNotice"), profile=value.get("profile"), shop_name=value.get("shopName"))


def collect_input_regions(value):
    return collect_overlay_regions(value["storyboard"], value.get("cutResults", []), value.get("copyResults", []), value.get("product", {}), value.get("copywriting", True),
                                   analysis=value.get("analysis"), page_style=value.get("pageStyle"), profile=value.get("profile"), shop_name=value.get("shopName"))


def section(blocks, key):
    return [block for block in blocks if block.get("pageSectionKey") == key]


def elements(blocks):
    return [element for block in blocks for element in block["elements"]]


def photos(blocks):
    return [element for element in elements(blocks) if element["type"] == "image"]


def text(blocks, role):
    return [element for element in elements(blocks) if element.get("copyRole") == role]
