"""Required-slot policy uses isolated A5 prompts in every single arm."""
import asyncio
import json

import pytest

from page_copy_test_support import page_copy_prompt_dir
from scripts import eval_page_copy as evaluate
from test_eval_page_copy import fake_settings, read_rows
from test_eval_page_copy_a4 import arms, args_for, envelope

pytestmark = pytest.mark.usefixtures("page_copy_prompt_dir")


def test_arm_passes_required_slots_without_enabling_other_optional_rewrites():
    definition = {**arms(1)[0], "required_slots": ["intro.body", "feature.desc"], "rewrite_slots": []}
    route = evaluate.parse_arms(json.dumps([definition]))[0]["overrides"]
    assert route["required_slots"] == ["intro.body", "feature.desc"]
    assert route["rewrite_slots"] == []


@pytest.mark.parametrize("value", [None, False, "intro.body", {}, [None], [""]])
def test_arm_rejects_invalid_required_slots(value):
    with pytest.raises(ValueError, match="required_slots"):
        evaluate.parse_arms(json.dumps([{**arms(1)[0], "required_slots": value}]))


def test_required_omission_is_corrected_independently_in_each_arm(tmp_path):
    definitions = [{**arm, "required_slots": ["intro.body"]} for arm in arms(2)]
    args = args_for(tmp_path, definitions=definitions, extra=["--concurrency", "1"])
    stages = []

    async def fake(req, timeout):
        stages.append(req["stage"])
        out = envelope(req)
        if req["stage"] == "single":
            for section in out["response"]["sections"]:
                section["texts"] = [row for row in section["texts"] if row["role"] != "body"]
        else:
            assert [slot["role"] for slot in req["slots"]] == ["body"]
            context = json.loads(req["prompt"].split("CORRECTION CONTEXT (data):\n")[1])["rejections"]
            assert all(row["reasons"] == ["missing_required"] for row in context.values())
        return out

    result = asyncio.run(evaluate.run_evaluation(args, caller=fake, settings=fake_settings()))
    assert stages == ["single", "rewrite", "single", "rewrite"]
    assert result["calls"] == 4 and all(page["status"] == "ok" for page in result["pages"])
    assert all(row["passed"] for row in read_rows(tmp_path / "run/slots.jsonl"))


def test_explicit_empty_required_override_disables_synthetic_config_default(tmp_path, page_copy_prompt_dir):
    path = page_copy_prompt_dir / "settings_v2.json"
    config = json.loads(path.read_text())
    config["required_slots"] = ["intro.body"]
    path.write_text(json.dumps(config))
    definitions = arms(2)
    definitions[1]["required_slots"] = []
    args = args_for(tmp_path, definitions=definitions, extra=["--concurrency", "1"])

    async def fake(req, timeout):
        out = envelope(req)
        if req["stage"] == "single":
            for section in out["response"]["sections"]:
                section["texts"] = [row for row in section["texts"] if row["role"] != "body"]
        return out

    result = asyncio.run(evaluate.run_evaluation(args, caller=fake, settings=fake_settings()))
    assert [arm["calls"] for arm in result["arms"]] == [2, 1]
