"""Offline evaluation controls: no API keys, provider traffic, or database access."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from page_copy_test_support import page_copy_prompt_dir
from scripts import eval_page_copy as evaluate

pytestmark = pytest.mark.usefixtures("page_copy_prompt_dir")


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return str(path)


def write_rows(path, rows):
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    return str(path)


def read_rows(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line]


def request(provider="gpt", model="test-model", **settings):
    return {"provider": provider, "model": model, "system_prompt": "rules", "prompt": "facts",
            "schema": {}, "settings": {"max_output_tokens": 100, **settings}, "groups": ["intro"]}


def prices():
    return {"models": {"test-model": {"input": 1, "cached_input": 0.1, "output": 5}}}


def test_no_send_defaults_to_dry_run_and_send_needs_both_budgets(tmp_path):
    cases = write_json(tmp_path / "cases.json", [{"caseId": "c", "product": {}, "analysis": {}}])
    flags = ["--cases", cases, "--arms", '[{"id":"a","provider":"gpt","model":"test-model"}]',
             "--output", str(tmp_path / "out")]
    args = evaluate.parse_args(flags)
    assert not args.send
    with pytest.raises(ValueError, match="max-calls.*max-usd"):
        asyncio.run(evaluate.run_evaluation(evaluate.parse_args([*flags, "--send"])))


def test_budget_stops_before_extra_call_and_unknown_prices_fail_closed():
    budget = evaluate.Budget(max_calls=1, max_usd=1, prices=prices())
    budget.before_call(request())
    first = {"request": request(), "provider": "gpt", "model": "test-model",
             "usage": {"input_tokens": 20, "output_tokens": 5}, "status": "ok"}
    budget.after_call(first)
    assert first["usd"] == pytest.approx(0.000045)
    with pytest.raises(evaluate.BudgetExceeded, match="max-calls"):
        budget.before_call(request())
    assert budget.calls == 1
    with pytest.raises(evaluate.BudgetExceeded, match="price"):
        evaluate.Budget(max_calls=5, max_usd=5, prices={"models": {}}).before_call(request())


def test_budget_reserves_before_send_and_does_not_release_unknown_usage():
    budget = evaluate.Budget(max_calls=5, max_usd=0.001, prices=prices())
    with pytest.raises(evaluate.BudgetExceeded, match="max-usd"):
        budget.before_call(request())  # UTF-8 input upper bound + output ceiling already exceeds budget.
    assert budget.calls == 0
    budget = evaluate.Budget(max_calls=5, max_usd=1, prices=prices())
    budget.before_call(request())
    reserve = budget.committed_usd
    log = {"request": request(), "provider": "gpt", "model": "test-model", "usage": {}, "status": "error"}
    budget.after_call(log)
    assert log["usd"] is None
    assert budget.committed_usd == reserve
    assert log["reserved_usd"] == reserve


def test_cost_counts_gemini_thoughts_and_openai_included_reasoning_once():
    usage = {"input_tokens": 100, "cached_input_tokens": 20, "output_tokens": 50, "reasoning_tokens": 30}
    assert evaluate.usage_cost("gpt", "test-model", usage, prices()) == pytest.approx(0.000332)
    assert evaluate.usage_cost("gemini", "test-model", usage, prices()) == pytest.approx(0.000482)


def test_fallback_arms_preserve_per_provider_settings():
    arms = evaluate.parse_arms(json.dumps([{"id": "fallback", "order": ["gpt", "gemini"],
        "models": {"gpt": "test-model", "gemini": "fallback-model"},
        "settings": {"gpt": {"reasoning_effort": "none"}, "gemini": {"thinking_level": "low"}}}]))
    route = arms[0]["overrides"]
    assert route["models"] == {"gpt": "test-model", "gemini": "fallback-model"}
    assert route["settings"]["gpt"] == {"reasoning_effort": "none"}
    assert route["settings"]["gemini"] == {"thinking_level": "low"}


def test_gemini_38_keeps_thinking_level_without_injecting_disabled_thinking():
    arms = evaluate.parse_arms(json.dumps([{"id": "flash", "provider": "gemini", "model": "gemini-3.8-flash",
        "settings": {"thinking_level": "low", "max_output_tokens": 512}}]))
    settings = arms[0]["overrides"]["settings"]["gemini"]
    assert settings["thinking_level"] == "low"
    assert settings["max_output_tokens"] == 512
    assert "thinking_budget" not in settings


def test_gemini_thinking_level_has_finite_ceiling_without_separate_thinking_budget():
    budget = evaluate.Budget(max_calls=1, max_usd=1, prices=prices())
    budget.before_call(request(provider="gemini", thinking_level="low"))
    assert budget.calls == 1 and 0 < budget.committed_usd < 1
    with pytest.raises(evaluate.BudgetExceeded, match="thinking_budget"):
        evaluate.call_ceiling(request(provider="gemini", thinking_budget=-1), prices())


def test_seed_uses_default_catalog_sections_and_detail_evidence():
    case = {"product": {"name": "셔츠", "colors": [{"id": "white", "name": "화이트", "isBase": True}]},
            "analysis": {"mdComment": "포켓을 강조", "materials": [{"name": "면", "ratio": 100}],
                         "detailRecommendations": {"candidates": [{"id": "pocket", "label": "패치 포켓", "reason": "앞판 패치"}]}}}
    board = evaluate.seed_storyboard(case, "emo")
    assert all(row["taxonomyVersion"] == 4 for row in board)
    assert {"emo.intro", "emo.review", "emo.fabric", "emo.faq"} <= {row["pageSectionKey"] for row in board}
    assert any(row.get("detailTargetId") == "pocket" for row in board)
    assert case["analysis"]["mdComment"] == "포켓을 강조"


def test_preview_case_details_without_ids_feed_both_facts_and_storyboard():
    case = {"product": {"name": "니트"}, "analysis": {"mdComment": "카라가 포인트", "materials": [
        {"name": "면", "ratio": 60}, {"name": "폴리에스터", "ratio": 40}],
        "detailCandidates": [{"label": "오픈 카라", "reason": "단추 없이 열린 목선"}]}}
    product, analysis = evaluate.case_inputs(case, "show")
    board = evaluate.seed_storyboard({"product": product, "analysis": analysis}, "show")
    assert analysis["materials"][0]["ratio"] == 60
    assert analysis["detailRecommendations"]["candidates"][0]["reason"] == "단추 없이 열린 목선"
    assert any(card.get("detailTargetId") == "eval-detail-1" for card in board)
    assert "detailRecommendations" not in case["analysis"]


def test_blind_exports_hide_models_and_two_judges_report_disagreements(tmp_path):
    source = write_rows(tmp_path / "slots.jsonl", [
        {"kind": "long_body", "role": "body", "group": "intro", "text": "포켓 셔츠예요", "model": "model-a", "arm": "a", "passed": True},
        {"kind": "long_body", "role": "body", "group": "intro", "text": "셔츠를 걸쳐요", "model": "model-b", "arm": "b", "passed": True}])
    out = tmp_path / "blind"
    evaluate.blind_export(source, out, seed=7)
    rows = read_rows(out / "blind.jsonl")
    assert all(set(row) == {"id", "kind", "text"} for row in rows)
    a = write_json(tmp_path / "a.json", [{"id": row["id"], "grade": "A"} for row in rows])
    b = write_json(tmp_path / "b.json", [{"id": row["id"], "grade": "B"} for row in rows])
    result = evaluate.blind_score(out / "blind.key.json", a, b)
    assert len(result["disagreements"]) == 2
    assert {r["model"] for r in result["kind_model_scores"]} == {"model-a", "model-b"}
    assert all(row["mean_score"] == 1.5 for row in result["kind_model_scores"])


def test_judging_exports_exclude_seller_fallback_and_rejected_ai(tmp_path):
    source = write_rows(tmp_path / "slots.jsonl", [
        {"kind": "intro.body", "text": "포켓을 달았어요", "model": "accepted-model", "passed": True, "source": "ai"},
        {"kind": "intro.body", "text": "셀러가 남긴 원문", "model": "failed-model", "passed": False, "source": "seller_original"},
        {"kind": "intro.body", "text": "검사에서 떨어진 글", "model": "rejected-model", "passed": False, "source": "ai"},
        {"kind": "intro.body", "text": "아직 평가하지 않은 글", "model": "dry-model", "passed": None, "source": "ai"},
        {"kind": "intro.body", "text": "통과 표시가 잘못된 원문", "model": "seller-model", "passed": True, "source": "seller_original"}])
    blind = tmp_path / "blind"
    evaluate.blind_export(source, blind)
    rows = read_rows(blind / "blind.jsonl")
    assert [row["text"] for row in rows] == ["포켓을 달았어요"]
    a = write_json(tmp_path / "judge-a.json", [{"id": row["id"], "grade": "A"} for row in rows])
    b = write_json(tmp_path / "judge-b.json", [{"id": row["id"], "grade": "A"} for row in rows])
    scores = evaluate.blind_score(blind / "blind.key.json", a, b)
    assert [row["model"] for row in scores["kind_model_scores"]] == ["accepted-model"]

    real = write_json(tmp_path / "shop.json", [{"kind": "intro.body", "text": "셔츠를 걸쳐요"}] * 2)
    detect = tmp_path / "detect"
    evaluate.detect_export(source, real, detect, per_kind=1, kinds=["intro.body"], rounds=1)
    key = evaluate.read_json(detect / "detect.key.json")["items"]
    assert {row["model"] for row in key.values() if row["origin"] == "ours"} == {"accepted-model"}
    with pytest.raises(ValueError, match="Insufficient samples"):
        evaluate.detect_export(source, real, tmp_path / "too-few", per_kind=2, kinds=["intro.body"], rounds=1)


def test_blind_export_accepts_legacy_rows_without_validation_metadata(tmp_path):
    source = write_rows(tmp_path / "legacy.jsonl", [{"kind": "intro.body", "text": "셔츠를 걸쳐요", "model": "legacy"}])
    evaluate.blind_export(source, tmp_path / "blind")
    assert read_rows(tmp_path / "blind" / "blind.jsonl")[0]["text"] == "셔츠를 걸쳐요"


def test_summary_separates_titles_bodies_questions_answers_and_normalizes_indices():
    rows = [{"kind": kind, "role": role, "model": "m", "provider": "gpt", "passed": passed,
             "rewrite": False, "reasons": [] if passed else ["voice"], **extra}
            for kind, role, passed, extra in [
                ("intro", "title", False, {}), ("intro", "body", True, {}),
                ("fabric", "title", False, {}), ("fabric", "body", True, {}),
                ("faq", "items.0.q", False, {}), ("faq", "items.0.a", True, {}),
                ("review", "items.0.text", True, {}), ("review", "items.2.text", False, {}),
                ("points", "items.1.desc", True, {"target": "part"}),
                ("feature", "desc", True, {"target": "feature"})]]
    groups = {row["kind"]: row for row in evaluate._summary(rows, [])["kind_model"]}
    assert set(groups) == {"intro.title", "intro.body", "fabric.title", "fabric.body", "faq.q", "faq.a",
                           "review.text", "part.desc", "feature.desc"}
    assert groups["intro.title"]["acceptance_rate"] == 0
    assert groups["intro.body"]["acceptance_rate"] == 1
    assert groups["faq.q"]["acceptance_rate"] == 0 and groups["faq.a"]["acceptance_rate"] == 1
    assert groups["review.text"]["slots"] == 2 and groups["review.text"]["acceptance_rate"] == 0.5
    assert rows[0]["kind"] == "intro"


def test_blind_scores_keep_title_and_body_quality_separate(tmp_path):
    source = write_rows(tmp_path / "slots.jsonl", [
        {"kind": "intro", "role": "title", "text": "포켓 셔츠", "model": "m", "passed": True},
        {"kind": "intro", "role": "body", "text": "포켓을 달았어요", "model": "m", "passed": True}])
    evaluate.blind_export(source, tmp_path / "blind")
    public = read_rows(tmp_path / "blind" / "blind.jsonl")
    assert {row["kind"] for row in public} == {"intro.title", "intro.body"}
    key = evaluate.read_json(tmp_path / "blind" / "blind.key.json")["items"]
    assert {row["kind"] for row in key.values()} == {"intro"}
    assert {row["slot_kind"] for row in key.values()} == {"intro.title", "intro.body"}
    judges = [{"id": row["id"], "score": 1 if row["kind"] == "intro.title" else 5} for row in public]
    a, b = write_json(tmp_path / "a.json", judges), write_json(tmp_path / "b.json", judges)
    result = evaluate.blind_score(tmp_path / "blind" / "blind.key.json", a, b)
    assert {row["kind"]: row["mean_score"] for row in result["kind_model_scores"]} == {"intro.title": 1, "intro.body": 5}


@pytest.mark.parametrize("unknown,unpriced,amount", [(1, 0, 0.2), (0, 2, 0), (0, 0, 0.2)])
def test_arm_unknown_cost_is_null_and_known_cost_remains_explicit_subtotal(unknown, unpriced, amount):
    page = {"arm": "a", "calls": 0 if unpriced else 1, "known_usd": amount,
            "unknown_cost_calls": unknown, "unpriced_planned_calls": unpriced,
            "seconds": 1, "provider_count": 1}
    arm = evaluate._summary([], [page, {**page, "unknown_cost_calls": 0, "unpriced_planned_calls": 0}])["arms"][0]
    assert arm["known_usd"] == pytest.approx(amount * 2)
    assert arm["mean_page_known_usd"] == pytest.approx(amount)
    if unknown or unpriced:
        assert arm["usd"] is None and arm["mean_page_usd"] is None
        assert arm["cost_warnings"]
    else:
        assert arm["usd"] == pytest.approx(0.4) and arm["mean_page_usd"] == pytest.approx(0.2)


def test_detection_balances_lengths_normalizes_symbols_and_counts_reasons(tmp_path):
    ours = write_rows(tmp_path / "ours.jsonl", [
        {"kind": "intro.body", "text": f"브랜드 상품 셔츠를 입어요 {i}", "model": "m", "productName": "상품", "shopName": "브랜드"}
        for i in range(4)])
    real = write_json(tmp_path / "shop.json", [
        {"kind": "intro.body", "text": f"브랜드 상품 셔츠예요!!~😊 {i}", "productName": "상품", "shopName": "브랜드"}
        for i in range(4)])
    out = tmp_path / "detect"
    evaluate.detect_export(ours, real, out, seed=7, per_kind=4, kinds=["intro.body"], rounds=1)
    rows = read_rows(out / "detect.normalized.round-1.jsonl")
    assert len(rows) == 8
    assert all(set(row) == {"id", "kind", "text"} for row in rows)
    assert all(not any(word in row["text"] for word in ("브랜드", "상품", "!", "~", "😊")) for row in rows)
    decisions = write_json(tmp_path / "decisions.json", [
        {"id": row["id"], "ai": True, "confidence": 0.8, "reason": "같은 문장 구조"} for row in rows])
    result = evaluate.detect_score(out / "detect.key.json", [decisions])
    assert result["primary"]["ours_ai_recall"] == 1
    assert result["primary"]["ours_count"] == 4
    assert result["primary"]["passed"] is False
    assert result["primary"]["recurring_reasons"] == [{"reason": "같은 문장 구조", "count": 4}]


def test_secret_values_are_redacted_recursively():
    scrub = evaluate.Redactor(SimpleNamespace(openai_api_key="sk-test-never-print"))
    assert scrub({"api_key": "other-secret", "error": "failed sk-test-never-print", "nested": ["sk-test-never-print"]}) == {
        "api_key": "[REDACTED]", "error": "failed [REDACTED]", "nested": ["[REDACTED]"]}


def evaluation_args(tmp_path, *, arm=None, extra=()):
    case = {"caseId": "shirt", "product": {"name": "패치 포켓 셔츠", "clothingType": "top",
            "colors": [{"id": "ivory", "name": "아이보리", "isBase": True}]},
            "analysis": {"mdComment": "앞판에 포켓을 달았어요", "materials": [{"name": "면", "ratio": 100}],
                         "sellingPoints": []},
            "storyboard": [{"taxonomyVersion": 4, "pageSectionKey": "emo.intro", "pageSectionKind": "intro",
                "pageDesignId": "T01-D07", "sectionId": "intro", "id": "intro", "source": "text"}]}
    cases_path = write_json(tmp_path / "cases.json", [case])
    table = prices()
    table["models"]["fallback-model"] = {"input": 1, "output": 5}
    prices_path = write_json(tmp_path / "prices.json", table)
    return evaluate.parse_args(["run", "--cases", cases_path, "--templates", "emo", "--arms", json.dumps([
        arm or {"id": "single", "provider": "gpt", "model": "test-model"}]),
        "--prices", prices_path, "--output", str(tmp_path / "run"), *extra])


def good_response(slots):
    sections = {}
    for slot in slots:
        text = "앞쪽에 포켓을 달았어요" if slot["role"] == "body" else "포켓 셔츠"
        sections.setdefault(slot["key"], []).append({"role": slot["role"], "text": text})
    return {"sections": [{"key": key, "texts": texts} for key, texts in sections.items()],
            "partDescriptionCopy": [], "featureCopy": []}


def fake_settings(**overrides):
    return SimpleNamespace(model_text="unused", model_text_gemini="unused", **overrides)


def test_real_request_builder_dry_run_never_dispatches_and_marks_slots_unscored(tmp_path):
    args = evaluation_args(tmp_path)

    async def forbidden_call(request, timeout):
        raise AssertionError("dry-run dispatched a provider")

    result = asyncio.run(evaluate.run_evaluation(args, caller=forbidden_call, settings=fake_settings()))
    assert result["mode"] == "dry_run"
    assert result["calls"] == 0 and result["planned_calls"] >= 1
    assert not result["kind_model"]
    calls = read_rows(tmp_path / "run" / "calls.jsonl")
    assert all(call["status"] == "dry_run" and call["request"]["prompt"] for call in calls)
    assert all("앞판에 포켓을 달았어요" in call["request"]["prompt"] for call in calls)


@pytest.mark.parametrize("path", ["fallback", "rewrite"])
def test_real_pipeline_budget_blocks_fallback_and_rewrite_before_dispatch(tmp_path, path):
    arm = {"id": "single", "provider": "gpt", "model": "test-model"}
    if path == "fallback":
        arm = {"id": "fallback", "order": ["gpt", "claude"],
               "models": {"gpt": "test-model", "claude": "fallback-model"}}
    args = evaluation_args(tmp_path, arm=arm, extra=["--send", "--max-calls", "1", "--max-usd", "1"])
    if path == "rewrite":
        cases = evaluate.read_rows(args.cases)
        cases[0]["analysis"]["mdComment"] = ""
        write_json(Path(args.cases), cases)
    sent = []

    async def fake_call(request, timeout):
        sent.append(request)
        if path == "fallback":
            raise RuntimeError("fake provider outage sk-test-secret")
        response = good_response(request["slots"])
        if path == "rewrite":
            for section in response["sections"]:
                for row in section["texts"]:
                    if row["role"] == "body":
                        row["text"] = "**금지 본문**"
        return {"response": response,
                "usage": {"input_tokens": 50, "output_tokens": 30, "reasoning_tokens": 0}}

    result = asyncio.run(evaluate.run_evaluation(args, caller=fake_call,
        settings=fake_settings(openai_api_key="sk-test-secret")))
    assert len(sent) == 1
    assert "max-calls" in (result["stopped"] or "")
    calls = read_rows(tmp_path / "run" / "calls.jsonl")
    assert len(calls) == 1
    assert {"request", "response", "validation", "usage", "seconds", "usd"} <= calls[0].keys()
    assert "sk-test-secret" not in (tmp_path / "run" / "calls.jsonl").read_text()


def test_run_usd_ceiling_rejects_first_request_without_sending(tmp_path):
    args = evaluation_args(tmp_path, extra=["--send", "--max-calls", "10", "--max-usd", "0.000001"])
    sent = []

    async def fake_call(request, timeout):
        sent.append(request)
        return {"response": good_response(request["slots"]), "usage": {"input_tokens": 50, "output_tokens": 10}}

    result = asyncio.run(evaluate.run_evaluation(args, caller=fake_call, settings=fake_settings()))
    assert sent == []
    assert result["calls"] == 0 and "max-usd" in result["stopped"]


def test_successful_calls_write_slot_results_usage_and_page_cost(tmp_path):
    args = evaluation_args(tmp_path, extra=["--send", "--max-calls", "5", "--max-usd", "1"])

    async def fake_call(request, timeout):
        return {"response": good_response(request["slots"]), "usage": {"input_tokens": 50, "output_tokens": 30}}

    result = asyncio.run(evaluate.run_evaluation(args, caller=fake_call, settings=fake_settings()))
    assert result["stopped"] is None and result["calls"] == 1
    assert result["known_usd"] == pytest.approx(0.0002)
    assert result["pages"][0]["provider_count"] == 1
    assert result["kind_model"][0]["acceptance_rate"] == 1
    rows = read_rows(tmp_path / "run" / "slots.jsonl")
    assert len(rows) == 2 and all(row["passed"] for row in rows)
    assert {row["kind"] for row in rows} == {"intro"}
    assert {row["slot_kind"] for row in rows} == {"intro.title", "intro.body"}
    assert {"case", "template", "arm", "group", "kind", "text", "passed", "reasons", "rewrite", "model"} <= rows[0].keys()
    assert len(read_rows(tmp_path / "run" / "attempts.jsonl")) == 2


def test_rewrite_retains_first_attempt_reasons_in_summary(tmp_path):
    args = evaluation_args(tmp_path, extra=["--send", "--max-calls", "3", "--max-usd", "1"])
    calls = []

    async def fake_call(request, timeout):
        calls.append(request)
        response = good_response(request["slots"])
        if len(calls) == 1:
            for section in response["sections"]:
                for row in section["texts"]:
                    if row["role"] == "body":
                        row["text"] = "**금지 본문**"
        return {"response": response, "usage": {"input_tokens": 50, "output_tokens": 30}}

    result = asyncio.run(evaluate.run_evaluation(args, caller=fake_call, settings=fake_settings()))
    assert result["calls"] == 2
    assert result["kind_model"][0]["acceptance_rate"] == 1
    body_attempts = next(row for row in result["attempt_kind_model"] if row["kind"] == "intro.body")
    assert body_attempts["reasons"]["plain_text"] == 1
    attempts = read_rows(tmp_path / "run" / "attempts.jsonl")
    assert any(not row["passed"] and row["text"] == "**금지 본문**" for row in attempts)


def test_dry_single_routes_and_runs_share_seed_between_arms(tmp_path):
    args = evaluation_args(tmp_path)
    args.templates = ["emo", "show"]
    args.runs = 2
    args.arms = json.dumps([
        {"id": "one", "provider": "gpt", "model": "test-model"},
        {"id": "gemini", "provider": "gemini", "model": "unpriced-model"}])
    cases = evaluate.read_rows(args.cases)
    cases[0].pop("storyboard")
    write_json(Path(args.cases), cases)
    result = asyncio.run(evaluate.run_evaluation(args))
    assert result["calls"] == 0 and len(result["pages"]) == 8
    seeds = {}
    for page in result["pages"]:
        seeds.setdefault((page["template"], page["run"]), set()).add(page["seed"])
        assert page["status"] == "dry_run"
    assert all(len(values) == 1 for values in seeds.values())
    assert len({page["seed"] for page in result["pages"]}) == 4
    calls = read_rows(tmp_path / "run" / "calls.jsonl")
    assert {call["provider"] for call in calls} == {"gpt", "gemini"}
    assert len(calls) == 8 and {call["stage"] for call in calls} == {"single"}
    assert all(row["passed"] is None for row in read_rows(tmp_path / "run" / "slots.jsonl"))
    gemini = next(arm for arm in result["arms"] if arm["arm"] == "gemini")
    assert gemini["usd"] is None and gemini["mean_page_usd"] is None
    assert result["cost_warnings"] == ["unpriced_dry_run"]


def test_usd_stop_before_rewrite_saves_received_slot_results(tmp_path):
    args = evaluation_args(tmp_path)
    cases = evaluate.read_rows(args.cases)
    cases[0]["analysis"]["mdComment"] = ""
    write_json(Path(args.cases), cases)
    asyncio.run(evaluate.run_evaluation(args, settings=fake_settings()))
    first_request = read_rows(tmp_path/"run"/"calls.jsonl")[0]["request"]
    ceiling = evaluate.call_ceiling(first_request, evaluate.read_json(args.prices))
    args.output = str(tmp_path/"send")
    args.send, args.max_calls, args.max_usd = True, 10, ceiling + 1e-10
    sent=[]
    async def fake(request, timeout):
        sent.append(request)
        response = good_response(request["slots"])
        for section in response["sections"]:
            for row in section["texts"]:
                if row["role"] == "body":row["text"]="**금지 본문**"
        # Exhaust this fake call's reservation, leaving no room for correction.
        return {"response":response,"usage":{"input_tokens":round(ceiling*1_000_000),"output_tokens":0}}
    summary=asyncio.run(evaluate.run_evaluation(args,caller=fake,settings=fake_settings()))
    assert len(sent)==1 and "max-usd" in summary["stopped"]
    rows=read_rows(tmp_path/"send"/"slots.jsonl")
    assert len(rows)==2
    assert next(r for r in rows if r["role"]=="title")["passed"] is True
    body=next(r for r in rows if r["role"]=="body")
    assert body["text"]=="**금지 본문**" and body["passed"] is False
    assert summary["pages"][0]["rewrite_status"]=="not_run"
    assert summary["pages"][0]["note"]=="고쳐 쓰기 못 함"


def test_rewrite_error_does_not_try_another_route_and_preserves_received_text(tmp_path):
    arm={"id":"fallback","order":["gpt","claude"],"models":{"gpt":"test-model","claude":"fallback-model"}}
    args=evaluation_args(tmp_path,arm=arm,extra=["--send","--max-calls","2","--max-usd","1"])
    cases = evaluate.read_rows(args.cases)
    cases[0]["analysis"]["mdComment"] = ""
    write_json(Path(args.cases), cases)
    sent=[]
    async def fake(request,timeout):
        sent.append(request)
        if len(sent)>1:raise RuntimeError("fake rewrite outage")
        response=good_response(request["slots"])
        for section in response["sections"]:
            for row in section["texts"]:
                if row["role"]=="body":row["text"]="**금지 본문**"
        return {"response":response,"usage":{"input_tokens":50,"output_tokens":30}}
    result=asyncio.run(evaluate.run_evaluation(args,caller=fake,settings=fake_settings()))
    assert len(sent)==2 and result["stopped"] is None
    assert {request["model"] for request in sent} == {"test-model"}
    rows=read_rows(tmp_path/"run"/"slots.jsonl")
    body=next(r for r in rows if r["role"]=="body")
    assert body["text"]=="**금지 본문**" and body["model"]=="test-model"
    assert "plain_text" in body["reasons"] and body["passed"] is False
    assert next(r for r in rows if r["role"]=="title")["passed"] is True
