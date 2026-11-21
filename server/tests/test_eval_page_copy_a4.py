"""Independent single arms and safe resume after removal of staged generation."""
import asyncio
import json
from pathlib import Path

import pytest

from page_copy_test_support import page_copy_prompt_dir
from scripts import eval_page_copy as evaluate
from test_eval_page_copy import evaluation_args, fake_settings, good_response, read_rows, write_json

pytestmark = pytest.mark.usefixtures("page_copy_prompt_dir")


def arms(count=2):
    return [{"id": f"arm-{i}", "mode": "single", "prompt_version": "v2",
             "provider": "gpt", "model": "test-model"} for i in range(count)]


def args_for(tmp_path, *, definitions=None, send=True, extra=()):
    options = ["--send", "--max-calls", "20", "--max-usd", "10"] if send else []
    args = evaluation_args(tmp_path, extra=[*options, *extra])
    args.arms = json.dumps(definitions or arms())
    return args


def envelope(req):
    return {"response": good_response(req["slots"]), "usage": {"input_tokens": 50, "output_tokens": 30}}


def test_identical_single_arms_each_make_and_pay_for_their_own_request(tmp_path):
    args = args_for(tmp_path)
    seen = []

    async def fake(req, timeout):
        seen.append(req)
        return envelope(req)

    result = asyncio.run(evaluate.run_evaluation(args, caller=fake, settings=fake_settings()))
    assert len(seen) == result["calls"] == 2
    assert result["known_usd"] == pytest.approx(0.0004)
    assert [arm["known_usd"] for arm in result["arms"]] == pytest.approx([0.0002, 0.0002])
    assert len({page["call_refs"][0] for page in result["pages"]}) == 2
    assert not (Path(args.output) / "shared_drafts.jsonl").exists()


def test_single_resume_skips_completed_arm_and_keeps_its_cost(tmp_path):
    args = args_for(tmp_path, extra=["--concurrency", "1"])
    args.max_calls = 1
    seen = []

    async def fake(req, timeout):
        seen.append(req)
        return envelope(req)

    first = asyncio.run(evaluate.run_evaluation(args, caller=fake, settings=fake_settings()))
    assert first["calls"] == 1 and first["stopped"]
    args.resume, args.max_calls = args.output, 2
    result = asyncio.run(evaluate.run_evaluation(args, caller=fake, settings=fake_settings()))
    assert len(seen) == result["calls"] == 2 and result["skipped_completed_pages"] == 1
    assert len(read_rows(Path(args.output) / "calls.jsonl")) == 2
    assert result["known_usd"] == pytest.approx(0.0004)


@pytest.mark.parametrize("old_option", ["mode", "polish", "share_draft"])
def test_resume_refuses_staged_manifest_before_provider_dispatch(tmp_path, old_option):
    args = args_for(tmp_path, definitions=arms(1), send=False)
    asyncio.run(evaluate.run_evaluation(args, settings=fake_settings()))
    manifest_path = Path(args.output) / "run.json"
    manifest = evaluate.read_json(manifest_path)
    if old_option == "mode":
        manifest["arms"][0]["overrides"]["mode"] = "draft"
    else:
        manifest["arms"][0]["overrides"][old_option] = "removed"
    write_json(manifest_path, manifest)
    with pytest.raises(ValueError, match="single"):
        asyncio.run(evaluate.run_evaluation(evaluate.parse_args(["--resume", args.output]), settings=fake_settings()))
    assert len(read_rows(Path(args.output) / "calls.jsonl")) == 1
