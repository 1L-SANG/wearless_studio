"""Initial JS seeds retain fabric eligibility and detail coverage in both engines.

There is no Python initial seeder: the server validates and assembles the saved
browser seed. Exercise that actual boundary without regenerating shared fixtures.
"""
from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from app.agents.content_roles import canonicalize_storyboard, validate_storyboard_v4
from app.agents.page_template_assembler import assemble_v4


ROOT = Path(__file__).resolve().parents[2]


def candidate(name, kind):
    return {"id": name, "kind": kind, "label": name, "rank": 1,
            "informationGroup": name, "photoUse": "standalone",
            "visibility": "clear", "direction": "front"}


SUBJECTS = [candidate(name, kind) for name, kind in [
    ("neck", "neckline"), ("cuff", "cuff"), ("hem", "hem"),
    ("pocket", "pocket"), ("closure", "closure"),
]]
FABRIC = candidate("weave", "fabric")
CASES = [
    ("no-fabric", [], [{"slot": "Front", "src": "/front.jpg"}], 0, None),
    ("overviews-only", [], [{"slot": "Front", "src": "/front.jpg"}, {"slot": "Back", "src": "/back.jpg"}], 0, None),
    ("blank-closeups", [], [{"slot": "Detail"}, {"slot": "BackDetail", "id": "", "src": ""}], 0, None),
    ("fabric-candidate", [FABRIC], [], 1, "front"),
    ("one-structure-fabric", [*SUBJECTS[:1], FABRIC], [], 1, "front"),
    ("two-structures-fabric", [*SUBJECTS[:2], FABRIC], [], 3, "front"),
    ("three-structures-fabric", [*SUBJECTS[:3], FABRIC], [], 4, "front"),
    ("five-structures-fabric", [*SUBJECTS, FABRIC], [], 4, "front"),
    ("seller-front-detail", [], [{"id": "front-detail", "slot": "Detail"}], 0, None),
    ("seller-back-detail", [], [{"id": "back-detail", "slot": "BackDetail"}], 0, None),
    ("seller-front-three-details", SUBJECTS[:3], [{"id": "front-detail", "slot": "Detail"}], 3, None),
    ("seller-back-three-details", SUBJECTS[:3], [{"id": "back-detail", "slot": "BackDetail"}], 3, None),
]


@pytest.fixture(scope="module")
def seeded():
    inputs = []
    for template_id in ("emo", "show"):
        for name, candidates, images, _, _ in CASES:
            inputs.append({"name": f"{template_id}-{name}",
                           "product": {"name": "가을 셔츠", "clothingType": "top", "colors": [
                               {"id": "base", "isBase": True, "images": images}]},
                           "analysis": {"targetGenders": ["women"], "pageStyle": {"templateId": template_id},
                                        "detailRecommendations": {"version": 1, "status": "ready", "candidates": candidates}}})
    script = """import { readFileSync } from 'node:fs';
import { seedStoryboard } from './src/lib/pageTemplates/seed.js';
import { assemblePage } from './src/lib/pageTemplates/assemble.js';
import { defaultPageStyle } from './src/lib/pageTemplates/pageStyle.js';
const values = JSON.parse(readFileSync(0, 'utf8')).map(input => {
  const analysis = { ...input.analysis, pageStyle: { ...defaultPageStyle(), ...input.analysis.pageStyle } };
  const storyboard = seedStoryboard(input.product, analysis, { projectId: 'woc3-python-seed' });
  const cutResults = storyboard.filter(block => block.source === 'ai').map(block =>
    ({ blockId: block.id, imageUrl: 'cut:' + block.id, width: 1024, height: 1365 }));
  const value = { ...input, analysis, storyboard, cutResults };
  return { ...value, page: assemblePage(value) };
});
process.stdout.write(JSON.stringify(values));"""
    result = subprocess.run(["node", "--input-type=module", "-e", script], input=json.dumps(inputs),
                            text=True, capture_output=True, cwd=ROOT, timeout=20)
    assert result.returncode == 0, result.stderr
    return {case["name"]: case for case in json.loads(result.stdout)}


@pytest.mark.parametrize("template_id", ["emo", "show"])
@pytest.mark.parametrize("name,candidates,images,expected_count,direction", CASES, ids=[case[0] for case in CASES])
def test_initial_seed_eligibility_and_full_geometry_match_both_engines(seeded, template_id, name, candidates, images,
                                                                    expected_count, direction):
    value = seeded[f"{template_id}-{name}"]
    blocks = value["storyboard"]
    before = deepcopy(blocks)
    validate_storyboard_v4(blocks)
    detail = [block for block in blocks if block.get("source") == "ai" and block.get("shot") == "detail"]
    assert len(detail) == expected_count
    fabric = [block for block in blocks if block["pageSectionKind"] == "fabric"]
    if direction is None:
        assert fabric == []
    else:
        assert len(fabric) == 1 and fabric[0]["source"] == "ai" and fabric[0]["direction"] == direction
        if name.startswith("seller-"):
            assert "detailTargetId" not in fabric[0]
    point_ids = [block.get("detailTargetId") for block in detail if block["pageSectionKind"] == "points"]
    assert point_ids == ["neck", "cuff", "hem"][:max(0, expected_count - bool(fabric))]
    page = assemble_v4(blocks, value["cutResults"], [], value["product"], True, analysis=value["analysis"])
    assert page == value["page"]
    emitted = {element.get("sourceBlockId") for run in page for element in run["elements"] if element["type"] == "image"}
    assert all(block["id"] in emitted for block in detail)
    assert blocks == before


@pytest.mark.parametrize("template_id", ["emo", "show"])
def test_old_saved_zero_photo_fabric_still_validates_and_uses_existing_text_fallback(seeded, template_id):
    value = deepcopy(seeded[f"{template_id}-fabric-candidate"])
    index = next(i for i, block in enumerate(value["storyboard"]) if block["pageSectionKind"] == "fabric")
    fabric = value["storyboard"][index]
    metadata = ("id", "taxonomyVersion", "pageSectionKey", "pageSectionKind", "pageDesignId", "sectionId",
                "sectionTitle", "sectionLayout", "sectionCustom")
    value["storyboard"][index] = {**{key: fabric[key] for key in metadata}, "source": "text", "slotKey": None,
                                  "cutType": None, "contentRole": None, "sectionRole": None}
    canonical = canonicalize_storyboard(value["storyboard"])
    assert canonical[index]["source"] == "text"
    page = assemble_v4(canonical, value["cutResults"], [], value["product"], True, analysis=value["analysis"])
    saved_fabric = [run for run in page if run.get("pageSectionKey") == f"{template_id}.fabric"]
    assert len(saved_fabric) == 1
    assert saved_fabric[0]["pageDesignId"] == "T03-TEXT-01"
    assert not any(element["type"] == "image" for element in saved_fabric[0]["elements"])
