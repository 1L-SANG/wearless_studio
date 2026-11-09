"""D204/D206 saved JS choices reach the real worker input builder, without providers."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import subprocess

from PIL import Image
import pytest

from app.agents import confirmed_gpt_runtime, cut_generator, space_set_assets
from app.workers import detail_page_job as dpj
from conftest import FakeR2, fake_worker_app, make_settings, worker_job

ROOT = Path(__file__).parents[2]
JS_CHOICES = """
import { STORYBOARD_SPACE_SETS, STORYBOARD_SPACE_SET_EXAMPLES } from './src/lib/storyboardSpaceSetCatalog.js';
import { generationExampleSelectionPatch } from './src/lib/storyboardExampleSelection.js';
import { assignGenerationExamples } from './src/lib/generationExamples.js';
const byId = new Map(STORYBOARD_SPACE_SET_EXAMPLES.map(e => [e.id, e]));
const cases = STORYBOARD_SPACE_SETS.flatMap(set => set.members.map(member => {
  const block = { id: member.exampleId, source: 'ai', taxonomyVersion: 4,
    pageSectionKey: 'emo.wear_1', pageSectionKind: 'wear', sectionId: 'wear',
    pageSectionUsage: member.cutType === 'horizon' ? 'fit' : 'styling',
    pageDesignId: member.cutType === 'horizon' ? 'P04-D01' : 'P02-D01',
    slotKey: null, contentRole: member.cutType === 'horizon' ? 'fit' : 'coordination',
    cutType: member.cutType, shot: 'full', direction: 'front', colorId: 'base',
    matchIds: [], refAssetIds: ['mood'] };
  return { type: set.setType, gender: set.gender, clothing: set.applicableClothingTypes[0],
    member, setId: set.id, block: { ...block, ...generationExampleSelectionPatch(block, byId.get(member.exampleId)).patch } };
}));
const signatures = [['women','top',''],['men','top',''],['women','outer',''],['men','outer',''],
 ['women','bottom','skirt'],['women','bottom','shorts'],['men','bottom','pants'],['women','dress','']].map(([gender,clothing,subCategory]) => {
 const block = { id: `signature-${gender}-${clothing}-${subCategory}`, source: 'ai', taxonomyVersion: 4,
  pageSectionKey: 'show.first', pageSectionKind: 'first', pageDesignId: 'P01-D01',
  sectionId: 'opening', slotKey: 'first.hero', contentRole: 'hero', cutType: 'horizon',
  shot: 'full', direction: 'front', colorId: 'base', refAssetIds: ['mood'], matchIds: [] };
 return { type: 'signature', gender, clothing, subCategory,
  block: assignGenerationExamples([block], { catalog: [], product: {clothingType:clothing,subCategory},gender }).blocks[0] };
});
console.log(JSON.stringify({cases,signatures}));
"""


@pytest.fixture(scope="module")
def saved_choices():
    result = subprocess.run(["node", "--input-type=module", "-e", JS_CHOICES], cwd=ROOT,
                            capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


@pytest.fixture(autouse=True)
def released_registry_cache():
    # Earlier registry tests load temporary manifests into this process-wide
    # zero-argument cache. Read the real restored release path for these tests.
    space_set_assets.load_space_set_registry.cache_clear()
    yield
    space_set_assets.load_space_set_registry.cache_clear()


def test_every_published_js_singleton_passes_actual_server_binding_and_avoids_confirmed_packet(saved_choices):
    assert saved_choices["cases"]
    for case in saved_choices["cases"]:
        block = case["block"]
        binding = space_set_assets.bind_storyboard_space_sets(
            [block], clothing_type=case["clothing"], gender=case["gender"])[id(block)]
        assert binding["set"]["setId"] == case["setId"]
        normalized = cut_generator.normalize_spec(block, clothing_type=case["clothing"])
        assert normalized["direction"] == case["member"]["direction"]
        assert normalized["shot"] == case["member"]["shot"]
        assert confirmed_gpt_runtime.profile_requested(normalized) is False
        reference_key = "poseReference" if case["type"] == "styling" else "horizonReference"
        reference = binding[reference_key]
        assert reference["exampleId"] == block["exampleId"]
        variant = "pose" if case["type"] == "styling" else "all"
        assert reference["asset"]["key"].split("/")[-2] == variant


def test_published_styling_coverage_and_exact_absent_gender_combinations(saved_choices):
    present = {(c["gender"], c["member"]["shot"], c["member"]["direction"])
               for c in saved_choices["cases"] if c["type"] == "styling"}
    requested = {(gender, shot, direction) for gender in ("men", "women")
                 for shot in ("full", "medium") for direction in ("front", "side", "back")}
    # #608 corrected a released men's full side pose; medium back is still absent.
    # Check the real published registry instead of fabricating a pose asset.
    assert ("men", "full", "side") in present
    assert requested - present == {("men", "medium", "back")}


def test_singletons_and_signature_choices_reach_actual_worker_input_builder(monkeypatch, saved_choices):
    captured_rows = []
    cases = saved_choices["cases"] + saved_choices["signatures"]
    output = BytesIO()
    Image.new("RGB", (32, 32), "#2a5db0").save(output, "PNG")
    product_png = output.getvalue()

    class ProductR2(FakeR2):
        def get_bytes(self, key):
            return b"seller-mood" if key.endswith("mood") else product_png

    async def fake_asset(conn, uid, asset_id):
        return {"id": asset_id, "r2_key": f"k/{asset_id}", "mime_type": "image/png"}

    async def fake_set_image(settings, asset, *, role):
        return dpj.InlineImage("image/png", f"{role}:{asset['key']}".encode())

    async def fake_example(settings, example_id, scope="all", clothing_type=None):
        assert example_id.startswith("sig_")
        assert scope == "all"
        return dpj.InlineImage("image/png", f"signature:{example_id}".encode())

    async def capture_generation(app, job, prepared, product, analysis, **kwargs):
        captured_rows.extend(prepared)
        results = [{"blockId": item[0]["id"], "imageUrl": f"/{item[0]['id']}"}
                   for item in prepared if item[1]]
        return results, [{"key": f"out/{row['blockId']}"} for row in results], 0, [], None, []

    async def fake_finalize(conn, **kwargs):
        return {"editor_blocks": [], "available": 99}

    async def fail_finalize(conn, **kwargs):
        pytest.fail(f"worker failed before provider boundary: {kwargs}")

    async def fake_emit(*args, **kwargs):
        return None

    monkeypatch.setattr(dpj.repo, "get_asset_for_user", fake_asset)
    monkeypatch.setattr(dpj.space_set_assets, "load_space_set_image", fake_set_image)
    monkeypatch.setattr(dpj.cut_generator, "load_example_image", fake_example)
    monkeypatch.setattr(dpj, "_gen_cuts", capture_generation)
    monkeypatch.setattr(dpj.page_assembler, "assemble", lambda *args, **kwargs: [])
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_success", fake_finalize)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_failure", fail_finalize)
    monkeypatch.setattr(dpj, "_emit", fake_emit)

    async def run_case(case):
        async def fake_project(conn, uid, pid):
            return {"copywriting": False}

        async def fake_storyboard(conn, pid):
            return [deepcopy(case["block"])]

        async def fake_product(conn, pid):
            return {"clothingType": case["clothing"], "subCategory": case.get("subCategory", ""),
                    "colors": [{"id": "base", "isBase": True,
                                "images": [{"slot": "Front", "id": "product"},
                                           {"slot": "Back", "id": "product-back"}]}]}

        async def fake_analysis(conn, pid):
            return {"targetGenders": [case["gender"]]}

        monkeypatch.setattr(dpj.repo, "get_project", fake_project)
        monkeypatch.setattr(dpj.repo, "get_storyboard", fake_storyboard)
        monkeypatch.setattr(dpj.repo, "get_product", fake_product)
        monkeypatch.setattr(dpj.repo, "get_analysis", fake_analysis)
        app = fake_worker_app(make_settings(gemini_api_key="x", r2_bucket="b",
                                           detailpage_fallback_model_id=""), r2=ProductR2())
        await dpj.run_detail_page_job(app, worker_job(credits_reserved=1))

    for case in cases:
        captured_rows.clear()
        asyncio.run(run_case(case))
        assert len(captured_rows) == 1, case
        item = captured_rows[0]
        spec, images, manifest = item[:3]
        assert images, case
        assert product_png in [image.data for image in images], case
        assert b"seller-mood" not in [image.data for image in images], case
        assert item[8] is None, "a set singleton or signature must never request the confirmed styling packet"
        assert spec["direction"] == case["block"]["direction"], case
        assert spec["shot"] == case["block"]["shot"], case
        if case["type"] == "styling":
            _, registry = space_set_assets.load_space_set_registry()
            released = registry[case["setId"]]
            member = next(m for m in released["members"] if m["exampleId"] == spec["exampleId"])
            assert [image.data for image in images][-2:] == [
                f"대표 배경:{released['representativePlate']['key']}".encode(),
                f"포즈:{member['pose']['key']}".encode()]
            assert spec["refScope"] == "pose"
            assert "POSE CONTROL" in manifest
            assert item[5] is not None and item[6] is True
        elif case["type"].startswith("horizon-"):
            _, registry = space_set_assets.load_space_set_registry()
            member = next(m for m in registry[case["setId"]]["members"] if m["exampleId"] == spec["exampleId"])
            assert images[-1].data == f"전체 예시:{member['all']['key']}".encode()
            assert spec["refScope"] == "all"
            assert "EXAMPLE REFERENCE (scope: all)" in manifest
            assert "POSE CONTROL" not in manifest
            assert item[5] is None
        else:
            assert images[-1].data == f"signature:{spec['exampleId']}".encode()
            assert spec["shot"] == "medium" and spec["direction"] == "front"
            assert spec["pageDesignId"] == "P01-D01"
            assert "EXAMPLE REFERENCE (scope: all)" in manifest
            assert cut_generator.is_signature_cut(spec)
            assert "BACKGROUND: same hue family as the garment's dominant color" in cut_generator.build_prompt(
                spec, {"clothingType": case["clothing"]})


def test_js_singleton_credit_count_matches_server_exclusions(saved_choices):
    block = saved_choices["cases"][0]["block"]
    assert dpj._duplicate_source_indexes([block, {**block, "id": "copy"}], saved_choices["cases"][0]["clothing"]) == [None, None]
    assert dpj._example_repeat_indexes([block], saved_choices["cases"][0]["clothing"]) == [None]
