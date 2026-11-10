"""Approved signature pools keep the existing generation contract and local assets."""
import json
from pathlib import Path
import subprocess

from PIL import Image
import pytest

from app.agents import confirmed_gpt_runtime, content_roles, cut_generator
from app.agents.cut_plan import compile_cut_plan

ROOT = Path(__file__).parents[2]
ASSETS = json.loads((ROOT / "server/app/data/example_assets.json").read_text())["assets"]
NEW_IDS = ([f"sig_women_bottom_pants_{i:02}" for i in range(3, 10)]
           + [f"sig_women_bottom_skirt_{i:02}" for i in range(3, 10)]
           + [f"sig_women_dress_{i:02}" for i in range(3, 7)]
           + [f"sig_men_bottom_pants_{i:02}" for i in range(3, 9)])


@pytest.mark.parametrize("example_id", NEW_IDS)
def test_new_signature_registry_local_assets_and_generation_plan(example_id):
    asset = ASSETS[example_id]
    clothing = "dress" if "_dress_" in example_id else "bottom"
    assert asset["cutType"] == "signature"
    assert asset["all"] == f"https://images.wearless.kr/seed/genexamples/signature/v1/all/{example_id}.png"
    assert asset["thumb"] == f"https://images.wearless.kr/seed/genexamples/signature/v1/thumb/{example_id}.webp"
    assert asset["confirmedGptEligibleV1"] is False
    assert asset["applicableClothingTypes"] == [clothing]
    for folder, size in [("signature", (1024, 1536)), ("signature/thumb", (512, 768))]:
        with Image.open(ROOT / f"public/assets/{folder}/{example_id}.webp") as image:
            assert image.format == "WEBP" and image.size == size
    spec = {"source": "ai", "cutType": "horizon", "shot": "medium", "direction": "front", "refScope": "all", "exampleId": example_id,
            "_referenceDirectionCompatible": asset["direction"] == "front"}
    normalized = cut_generator.normalize_spec(spec, clothing_type=clothing)
    assert confirmed_gpt_runtime.profile_requested({**spec, "cutType": "styling"}) is False
    assert compile_cut_plan(normalized, clothing).signature_framing == clothing
    assert cut_generator.is_signature_cut(normalized)
    if clothing == "dress":
        assert cut_generator.is_dress_signature_cut(normalized)


@pytest.mark.parametrize("template", ["emo", "show"])
def test_both_templates_canonicalize_signature_and_lifestyle_openings(template):
    for cut_type, example_id in [("horizon", "sig_women_dress_06"), ("styling", None)]:
        block = {"id": "first", "sectionId": "first", "taxonomyVersion": 4, "source": "ai", "pageSectionKey": f"{template}.first",
                 "pageSectionKind": "first", "pageDesignId": "P01-D09" if template == "emo" else "P01-SHOW-01",
                 "slotKey": "first.hero", "cutType": cut_type, "shot": "medium", "direction": "front",
                 "exampleId": example_id, "refScope": "all", "colorId": "base"}
        normalized = content_roles.canonicalize_storyboard([block], for_storage=True)[0]
        assert normalized["cutType"] == cut_type
        assert normalized["exampleId"] == example_id
        assert normalized["contentRole"] == "hero"


def test_frontend_backend_active_pool_and_seed_parity():
    script = """
import {signatureCutsFor,pickSignatureCut} from './src/lib/signatureCutPool.js';
const contexts=[{gender:'women',clothingType:'top'},{gender:'men',clothingType:'top'},
{gender:'women',clothingType:'bottom'},{gender:'women',clothingType:'bottom',subCategory:'skirt'},
{gender:'women',clothingType:'dress'},{gender:'men',clothingType:'bottom'}];
console.log(JSON.stringify(contexts.map(c=>({c,ids:signatureCutsFor(c).map(x=>x.id).sort(),picks:['one','two','치마'].map(projectId=>pickSignatureCut({...c,projectId,slotKey:'first.hero'}).id)}))));
"""
    rows = json.loads(subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT, capture_output=True, text=True, check=True).stdout)
    for row in rows:
        context = row["c"]
        kwargs = {"gender": context["gender"], "clothing_type": context["clothingType"], "sub_category": context.get("subCategory")}
        assert sorted(cut_generator.signature_cut_ids_for(**kwargs)) == row["ids"]
        assert [cut_generator.pick_signature_cut(**kwargs, project_id=pid, slot_key="first.hero") for pid in ["one", "two", "치마"]] == row["picks"]
    for retired in ["sig_women_bottom_skirt_01", "sig_men_bottom_pants_02"]:
        assert retired in ASSETS
        assert all(retired not in row["ids"] for row in rows)
