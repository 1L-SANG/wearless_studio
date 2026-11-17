"""origin/main 기준, AG-08 GPT dev 승인 변경만 갱신. 유료 호출과 실행 중 git 접근은 없다."""

import asyncio
from hashlib import sha256
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.agents import feature_extractor, input_consistency, outfit_recommender
from app.agents import product_analyst, product_evidence_contract, vision_llm
from app.agents.gemini_image import InlineImage
from test_product_evidence_contract import _binding, _raw


BASELINE_PATH = Path(__file__).parent / "fixtures/analysis_request_baseline.json"


def _digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def request_bodies():
    product = {"name": "프릴\n스커트", "clothingType": "bottom", "colors": [{"id": "base"}]}
    images = [InlineImage("image/png", b"front"), InlineImage("image/jpeg", b"back")]
    bodies = {}

    def wire(name, prompt, schema, *, developer=None, gpt_prompt=None):
        bodies[name + ":gpt"] = vision_llm._gpt_body(
            "baseline-model", gpt_prompt or prompt, images, schema,
            reasoning_effort="none", system_prompt=developer,
        )
        bodies[name + ":gemini"] = vision_llm._gemini_body(prompt, images, schema, "medium")

    for with_evidence in (False, True):
        wire("product:" + str(with_evidence), product_analyst.build_prompt(
            product, evidence_binding=_binding() if with_evidence else None,
        ), product_analyst.analysis_schema(include_confirmed_evidence=with_evidence))
    bodies["evidence"] = {
        "prompt": product_evidence_contract.render_prompt_block(_binding()),
        "schema": product_evidence_contract.evidence_schema(),
        "contract": product_evidence_contract.validate_and_bind(_raw(), _binding()),
    }
    developer, user = feature_extractor.build_terra_prompts(product, ["Front", "Back"])
    wire("features", feature_extractor.build_prompt(product, ["Front", "Back"]),
         feature_extractor._schema(), developer=developer, gpt_prompt=user)
    for slot in ("Back", "Detail", "BackDetail"):
        wire("consistency:" + slot, input_consistency.build_prompt(slot), input_consistency.schema())

    async def fake_images(*args):
        return images

    context = {"clothingType": "top", "subCategory": "shirt", "fit": "regular", "gender": "women"}
    candidate = {"id": "item-1", "name": "와이드 바지", "clothing_type": "bottom", "category": "slacks",
                 "color_name": "블랙", "color_group": "black", "style_tags": ["minimal"], "fit": "wide", "length": "long"}
    with patch.object(outfit_recommender, "_request_images", fake_images):
        for provider in ("gpt", "gemini"):
            for props in (False, True):
                if props:
                    request = asyncio.run(outfit_recommender.build_props_request(
                        None, (), {"bag": [candidate]}, context=context, provider=provider))
                else:
                    request = asyncio.run(outfit_recommender.build_request(
                        None, (), {"bottom": [candidate]}, context=context, fixed={"slots": {}}, provider=provider))
                if provider == "gpt":
                    body = vision_llm._gpt_body("baseline-model", request["prompt"], request["images"], request["schema"],
                                               reasoning_effort="none", system_prompt=request["system_prompt"])
                else:
                    body = vision_llm._gemini_body(request["prompt"], request["images"], request["schema"], "medium",
                                                  system_prompt=request["system_prompt"])
                bodies[f"outfit:{props}:{provider}"] = body
    return bodies


@pytest.fixture(scope="module")
def observed_requests():
    return request_bodies()


@pytest.mark.parametrize("name,expected", json.loads(BASELINE_PATH.read_text())["requests"].items())
def test_analysis_request_body_matches_approved_baseline(observed_requests, name, expected):
    assert _digest(observed_requests[name]) == expected


def test_evidence_contract_source_and_contract_hash_are_identical_to_main():
    baseline = json.loads(BASELINE_PATH.read_text())
    assert sha256(Path(product_evidence_contract.__file__).read_bytes()).hexdigest() == baseline["evidence_module_sha256"]
    assert product_evidence_contract.validate_and_bind(_raw(), _binding())["contractSha256"] == baseline["contractSha256"]
