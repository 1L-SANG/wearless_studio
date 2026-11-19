"""A3 experiment isolation, accounting and resumability, with fake provider traffic only."""
import asyncio
import json
from pathlib import Path

import pytest

from page_copy_test_support import page_copy_prompt_dir, FIXTURE_LUNA_CAP

pytestmark = pytest.mark.usefixtures("page_copy_prompt_dir")

from scripts import eval_page_copy as evaluate
from test_eval_page_copy import (evaluation_args, fake_settings, good_response, prices,
                                 read_rows, request, write_json, write_rows)


def test_single_arms_allow_explicit_prompt_versions_and_provider_settings():
    arms = evaluate.parse_arms(json.dumps([
        {"id": "argon", "mode": "single", "prompt_version": "v2", "provider": "gemini",
         "model": "test-model", "settings": {"thinking_level": "high"}},
        {"id": "legacy", "prompt_version": "v1", "provider": "gpt", "model": "test-model"}]))
    assert arms[0]["overrides"]["settings"]["gemini"]["thinking_level"] == "high"
    assert arms[1]["overrides"]["prompt_version"] == "v1"
    for invalid in [{"mode": "unknown"}, {"prompt_version": "v3"}, {"groups": {"fabric": {}}}]:
        with pytest.raises(ValueError):
            evaluate.parse_arms(json.dumps([{"provider": "gpt", "model": "test-model", **invalid}]))


def test_concurrent_budget_matches_same_model_calls_finishing_in_reverse_order():
    budget = evaluate.Budget(max_calls=3, max_usd=1, prices=prices())
    first, second = request(max_output_tokens=100), request(max_output_tokens=900)
    budget.before_call(first)
    first_reserve = budget.committed_usd
    budget.before_call(second)
    second_reserve = budget.committed_usd - first_reserve
    second_log = {"request": second, "provider": "gpt", "model": "test-model", "usage": {}}
    budget.after_call(second_log)
    first_log = {"request": first, "provider": "gpt", "model": "test-model",
                 "usage": {"input_tokens": 20, "output_tokens": 5}}
    budget.after_call(first_log)
    assert second_log["reserved_usd"] == pytest.approx(second_reserve)
    assert first_log["reserved_usd"] == pytest.approx(first_reserve)
    assert budget.committed_usd == pytest.approx(second_reserve + 0.000045)


def test_non_ai_is_separately_counted_and_not_in_model_acceptance():
    slots = [{"arm": "a", "kind": "intro.body", "source": source, "model": "m", "passed": passed,
              "text": "본문", "reasons": []} for source, passed in [
        ("ai", True), ("ai", False),
        ("seller_original", False), ("seller_cleaned", True),
        ("dictionary", True)]]
    summary = evaluate._summary(slots, [])
    assert summary["kind_model"][0]["slots"] == 2
    assert summary["kind_model"][0]["acceptance_rate"] == 0.5
    assert summary["source_counts"] == {"ai": 2, "seller_original": 1, "seller_cleaned": 1, "dictionary": 1}
    assert summary["non_ai_slots"] == 3


def test_blind_one_arm_per_sheet_uses_opaque_bundle_names_and_private_map(tmp_path):
    source = write_rows(tmp_path / "slots.jsonl", [
        {"arm": arm, "kind": "intro.body", "text": f"본문 {index}", "source": source, "passed": True}
        for arm in ["expensive-opus", "cheap-gemini"] for index, source in enumerate(["ai", "seller_cleaned"])])
    result = evaluate.blind_export(source, tmp_path / "blind", seed=7, one_arm_per_sheet=True)
    key = evaluate.read_json(tmp_path / "blind" / "blind.key.json")
    assert len(result["sheets"]) == 2
    for sheet in result["sheets"]:
        assert not any(name in sheet for name in ["expensive", "cheap", "opus", "gemini"])
        rows = read_rows(tmp_path / "blind" / sheet)
        assert len({key["items"][row["id"]]["arm"] for row in rows}) == 1
        assert all(set(row) == {"id", "kind", "text"} for row in rows)
        assert all(key["items"][row["id"]]["source"] == "ai" for row in rows)
    assert result["samples"] == 2


def test_detection_one_arm_per_sheet_balances_each_arm_with_real_shop(tmp_path):
    ours = write_rows(tmp_path / "ours.jsonl", [{"arm": arm, "kind": "intro.body", "text": "셔츠를 걸쳐요", "source": "ai", "passed": True}
        for arm in ["expensive-opus", "cheap-gemini"]])
    shop = write_json(tmp_path / "shop.json", [{"kind": "intro.body", "text": "포켓이 달린 셔츠"}])
    result = evaluate.detect_export(ours, shop, tmp_path / "detect", per_kind=1,
        kinds=["intro.body"], rounds=2, one_arm_per_sheet=True)
    key = evaluate.read_json(tmp_path / "detect" / "detect.key.json")["items"]
    assert len(result["sheets"]) == 8
    for sheet in result["sheets"]:
        assert not any(name in sheet for name in ["expensive", "cheap", "opus", "gemini"])
        rows = read_rows(tmp_path / "detect" / sheet)
        assert len(rows) == 2
        own = [key[row["id"]] for row in rows if key[row["id"]]["origin"] == "ours"]
        assert len(own) == 1
        assert all(set(row) == {"id", "kind", "text"} for row in rows)


def test_run_concurrency_and_stage_accounting(tmp_path):
    args = evaluation_args(tmp_path, extra=["--send", "--max-calls", "10", "--max-usd", "1"])
    assert args.concurrency == 4
    cases = evaluate.read_rows(args.cases)
    write_json(Path(args.cases), [{**cases[0], "caseId": str(i)} for i in range(4)])
    active, peak = 0, 0

    async def fake(req, timeout):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return {"response": good_response(req["slots"]), "usage": {"input_tokens": 50, "output_tokens": 30}}

    result = asyncio.run(evaluate.run_evaluation(args, caller=fake, settings=fake_settings()))
    assert peak == 4 and result["calls"] == 4
    assert result["arms"][0]["stages"]["single"]["mean_page_usd"] == pytest.approx(0.0002)
    for page in result["pages"]:
        assert page["stages"]["single"]["calls"] == 1
        assert page["stages"]["single"]["input_tokens"] == 50
        assert page["stages"]["single"]["output_tokens"] == 30
        assert page["usd"] == pytest.approx(0.0002)
    assert {row["stage"] for row in read_rows(tmp_path / "run" / "calls.jsonl")} == {"single"}


def test_resume_skips_completed_pages_and_preserves_total_budget(tmp_path):
    args = evaluation_args(tmp_path, extra=["--send", "--max-calls", "1", "--max-usd", "1", "--concurrency", "1"])
    cases = evaluate.read_rows(args.cases)
    write_json(Path(args.cases), [cases[0], {**cases[0], "caseId": "second"}])
    sent = []

    async def fake(req, timeout):
        sent.append(req)
        return {"response": good_response(req["slots"]), "usage": {"input_tokens": 50, "output_tokens": 30}}

    first = asyncio.run(evaluate.run_evaluation(args, caller=fake, settings=fake_settings()))
    assert first["calls"] == 1 and first["stopped"]
    before = (tmp_path / "run" / "calls.jsonl").read_text()
    args.resume, args.max_calls = args.output, 2
    resumed = asyncio.run(evaluate.run_evaluation(args, caller=fake, settings=fake_settings()))
    assert resumed["stopped"] is None and resumed["calls"] == 2
    assert len(sent) == 2 and len(resumed["pages"]) == 2
    assert resumed["known_usd"] == pytest.approx(0.0004)
    assert (tmp_path / "run" / "calls.jsonl").read_text().startswith(before)
    assert len(read_rows(tmp_path / "run" / "slots.jsonl")) == 4
    again = asyncio.run(evaluate.run_evaluation(args, caller=fake, settings=fake_settings()))
    assert again["calls"] == 2 and len(sent) == 2


def test_resume_refuses_changed_cases_before_dispatch(tmp_path):
    args = evaluation_args(tmp_path)
    asyncio.run(evaluate.run_evaluation(args, settings=fake_settings()))
    cases = evaluate.read_rows(args.cases)
    cases[0]["product"]["name"] = "다른 상품"
    write_json(Path(args.cases), cases)
    args.resume = args.output
    with pytest.raises(ValueError, match="resume|Resume"):
        asyncio.run(evaluate.run_evaluation(args, settings=fake_settings()))


def test_resume_pending_reservations_remain_charged_after_interruption(tmp_path):
    args = evaluation_args(tmp_path, extra=["--send", "--max-calls", "1", "--max-usd", "1"])

    async def interrupt(req, timeout):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(evaluate.run_evaluation(args, caller=interrupt, settings=fake_settings()))
    args.resume = args.output
    sent = []

    async def forbidden(req, timeout):
        sent.append(req)
        return {}

    result = asyncio.run(evaluate.run_evaluation(args, caller=forbidden, settings=fake_settings()))
    assert result["calls"] == 1 and sent == []
    assert result["committed_usd"] > 0 and result["known_usd"] == 0
    assert result["unresolved_reservations"] == 1
    assert "max-calls" in result["stopped"]


def test_feature_route_runs_once_per_case_not_once_per_template(tmp_path):
    args = evaluation_args(tmp_path, extra=["--feature-route", "--send", "--max-calls", "5", "--max-usd", "1"])
    args.templates = ["emo", "show"]
    cases = evaluate.read_rows(args.cases)
    cases[0]["analysis"]["sellingPoints"] = ["패치 포켓"]
    cases[0]["analysis"]["aiSuggestedPoints"] = ["금지된 추가 특징"]
    write_json(Path(args.cases), cases)
    sent = []

    async def fake(req, timeout):
        sent.append(req)
        assert [slot["point"] for slot in req["slots"]] == ["패치 포켓"]
        return {"response": {"featureCopy": [{"point": "패치 포켓", "desc": "앞쪽에 패치 포켓을 달았어요"}]},
                "usage": {"input_tokens": 50, "output_tokens": 30}}

    result = asyncio.run(evaluate.run_evaluation(args, caller=fake, settings=fake_settings()))
    assert len(result["pages"]) == 1 and len(sent) == 1
    slots = read_rows(tmp_path / "run" / "slots.jsonl")
    assert len(slots) == 1 and slots[0]["slot_kind"] == "feature.desc"
    assert slots[0]["text"] == "앞쪽에 패치 포켓을 달았어요" and slots[0]["source"] == "ai"


def test_rewrite_slot_override_can_disable_correction_for_an_arm(tmp_path):
    args = evaluation_args(tmp_path, arm={"id": "no-rewrite", "provider": "gpt", "model": "test-model",
        "rewrite_slots": []}, extra=["--send", "--max-calls", "5", "--max-usd", "1"])

    async def invalid(req, timeout):
        response = good_response(req["slots"])
        for section in response["sections"]:
            for row in section["texts"]:
                if row["role"] == "body":
                    row["text"] = "**금지 본문**"
        return {"response": response, "usage": {"input_tokens": 50, "output_tokens": 30}}

    result = asyncio.run(evaluate.run_evaluation(args, caller=invalid, settings=fake_settings()))
    assert result["calls"] == 1
    assert result["pages"][0]["stages"]["rewrite"]["calls"] == 0


def test_standalone_resume_uses_saved_cases_and_skips_finished_dry_pages(tmp_path):
    args = evaluation_args(tmp_path)
    first = asyncio.run(evaluate.run_evaluation(args, settings=fake_settings()))
    resumed = asyncio.run(evaluate.run_evaluation(evaluate.parse_args(["--resume", args.output]), settings=fake_settings()))
    assert resumed["planned_calls"] == first["planned_calls"]
    assert resumed["skipped_completed_pages"] == 1
    assert len(read_rows(tmp_path / "run" / "calls.jsonl")) == first["planned_calls"]


def test_dry_run_plans_each_single_arm_without_dispatch(tmp_path):
    args = evaluation_args(tmp_path)
    args.arms = json.dumps([{"id": model, "mode": "single", "prompt_version": "v2",
        "provider": "gemini", "model": model} for model in ["gemini-4-argon", "gemini-4-hydrogen"]])

    async def forbidden(req, timeout):
        raise AssertionError("dry-run cannot call a provider")

    result = asyncio.run(evaluate.run_evaluation(args, caller=forbidden, settings=fake_settings()))
    assert result["calls"] == 0 and len(result["pages"]) == 2
    assert all(page["planned_calls"] == 1 for page in result["pages"])
    calls = read_rows(tmp_path / "run" / "calls.jsonl")
    assert {call["stage"] for call in calls} == {"single"}
    assert all(call["request"]["prompt_version"] == "v2" for call in calls)


def test_concurrent_inflight_cost_reservations_block_extra_dispatch(tmp_path):
    args = evaluation_args(tmp_path)
    asyncio.run(evaluate.run_evaluation(args, settings=fake_settings()))
    first_request = read_rows(tmp_path / "run" / "calls.jsonl")[0]["request"]
    ceiling = evaluate.call_ceiling(first_request, evaluate.read_json(args.prices))
    args.output = str(tmp_path / "budget")
    args.send, args.max_calls, args.max_usd = True, 10, ceiling + 1e-10
    cases = evaluate.read_rows(args.cases)
    write_json(Path(args.cases), [{**cases[0], "caseId": str(i)} for i in range(4)])
    sent = []

    async def fake(req, timeout):
        sent.append(req)
        await asyncio.sleep(0.01)
        return {"response": good_response(req["slots"]), "usage": {"input_tokens": 50, "output_tokens": 30}}

    result = asyncio.run(evaluate.run_evaluation(args, caller=fake, settings=fake_settings()))
    assert len(sent) == 1 and result["calls"] == 1
    assert "max-usd" in result["stopped"]
    assert result["committed_usd"] <= args.max_usd


def test_blind_resume_export_ignores_superseded_attempts(tmp_path):
    common = {"case": "c", "arm": "a", "template": "emo", "run": 1, "kind": "intro.body", "passed": True}
    source = write_rows(tmp_path / "slots.jsonl", [
        {**common, "text": "옛 시도", "page_attempt": 1}, {**common, "text": "현재 시도", "page_attempt": 2}])
    write_rows(tmp_path / "pages.jsonl", [{**common, "page_attempt": 1}, {**common, "page_attempt": 2}])
    evaluate.blind_export(source, tmp_path / "blind")
    assert [row["text"] for row in read_rows(tmp_path / "blind" / "blind.jsonl")] == ["현재 시도"]


def test_arm_without_output_override_uses_model_cap_for_dry_run_reservation(tmp_path):
    args = evaluation_args(tmp_path, arm={"id": "luna", "provider": "gpt", "model": "gpt-6-luna", "prompt_version": "v2"})
    asyncio.run(evaluate.run_evaluation(args, settings=fake_settings()))
    call = read_rows(tmp_path / "run" / "calls.jsonl")[0]
    # The harness must use the model cap supplied by this synthetic fixture.
    assert call["request"]["settings"]["max_output_tokens"] == FIXTURE_LUNA_CAP


def test_generic_thinking_config_preserves_values_but_rejects_unbounded_budget():
    safe = request(provider="gemini", thinking_config={"thinkingLevel": "high", "includeThoughts": True})
    budget = evaluate.Budget(max_calls=1, max_usd=1, prices=prices())
    budget.before_call(safe)
    assert budget.calls == 1
    assert safe["settings"]["thinking_config"] == {"thinkingLevel": "high", "includeThoughts": True}
    for config in [{"thinkingBudget": -1}, {"thinking_budget": float("inf")}, []]:
        with pytest.raises(evaluate.BudgetExceeded, match="thinking"):
            evaluate.call_ceiling(request(provider="gemini", thinking_config=config), prices())


