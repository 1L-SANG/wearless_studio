#!/usr/bin/env python3
"""Offline-first page-copy experiments, independent of the detail-page worker.

Examples (run from server, or use this file's absolute path)::

    python -B scripts/eval_page_copy.py --cases tests/fixtures/copy_eval_cases.json \
      --arms '[{"id":"single","provider":"gpt","model":"MODEL"}]' --output /tmp/copy-dry
    python -B scripts/eval_page_copy.py run ... --send --max-calls 190 --max-usd 6
    python -B scripts/eval_page_copy.py run --resume /tmp/copy-dry
    python -B scripts/eval_page_copy.py blind-export --input /tmp/run/slots.jsonl \
      --output /tmp/blind --one-arm-per-sheet
    python -B scripts/eval_page_copy.py blind-score --key /tmp/blind/blind.key.json \
      --judge-a claude.json --judge-b codex.json --output scores.json
    python -B scripts/eval_page_copy.py detect-export --input /tmp/run/slots.jsonl \
      --real-shop real.json --output /tmp/detect

Arms use one single generation route and optional rejected/required-slot rewrite.
Provider and model can be selected explicitly, with per-model settings. Output
caps are enforced before budgeted dispatch. Prices are owner supplied; no price
is guessed. Concurrency defaults to four pages and every physical attempt has a
durable reservation. Resume skips completed pages and retains prior spending.

With --send, --env-file may supply GEMINI_API_KEY_NEXT. A billing or exhausted
quota failure switches to that reserve once, retries the same request within its
original timeout and budget, and keeps the reserve for later calls and resumes.
Transient per-minute/per-second limits do not switch credentials. Audit records
contain only the credential source labels primary/next, never credential values.

Judge files are JSON arrays or JSONL: {id, grade: A|B|C}, {id, score: number},
or {id, scores: {rubric_item: number|boolean}, reason: string}. Detection rows
use {id, ai: boolean, confidence: 0..1, reason: string, reason_tag?: string}.
Real-shop rows use {kind, text, shopName?, productName?}; supplied names are
removed from both origins. Public sheets have only id, kind and text. Keep
*.key.json files away from judges. Model judging and detection's "ours" pool
include accepted AI text only; rejected text, unscored dry runs, seller originals,
seller cleaned text and dictionary fallbacks stay in the audit logs. Legacy rows without validation or
source metadata are treated as accepted AI text. No command invokes a judge model.
Slot summaries and public judging kinds distinguish title/body and question/answer;
raw audit rows retain kind and add slot_kind. Unknown amounts stay null, while
known_usd is only the measured subtotal, never an estimate for unpriced calls.
Each arm/page includes single and rewrite cost/time/token metrics,
distinct providers and source counts. --one-arm-per-sheet
gives each arm opaque bundle filenames; only private key files identify arms.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from types import SimpleNamespace

import httpx

SERVER = Path(__file__).resolve().parents[1]
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

DEFAULT_PRICES = Path(__file__).with_name("eval_page_copy_prices.json")
DETECT_KINDS = ("intro.body", "fabric.body", "review.text", "part.desc")
PROVIDERS = {"gpt", "gemini", "claude"}
SECRET_KEY = re.compile(r"api[_-]?key|secret|password|authorization|access[_-]?token", re.I)


class BudgetExceeded(RuntimeError):
    """The next provider call would exceed the experiment's pre-call ceiling."""


class Redactor:
    def __init__(self, settings=None, *, secrets=()):
        values = list(os.environ.items())
        if settings is not None:
            values += list(vars(settings).items())
        self.secrets = sorted({v for k, v in values if SECRET_KEY.search(k)
                               and isinstance(v, str) and v} | {v for v in secrets if v}, key=len, reverse=True)

    def __call__(self, value):
        if isinstance(value, dict):
            return {str(k): "[REDACTED]" if SECRET_KEY.search(str(k)) else self(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self(v) for v in value]
        if isinstance(value, str):
            for secret in self.secrets:
                value = value.replace(secret, "[REDACTED]")
            return value
        return value


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_rows(path):
    text = Path(path).read_text(encoding="utf-8").strip()
    if not text:
        return []
    if text.startswith("["):
        rows = json.loads(text)
    elif Path(path).suffix == ".json":
        data = json.loads(text)
        rows = data.get("rows", data.get("cases", data.get("decisions", data.get("scores")))) if isinstance(data, dict) else data
    else:
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("Expected a JSON array or JSONL of objects")
    return rows


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def append_row(path, value, *, durable=False):
    with Path(path).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")
        if durable:
            stream.flush()
            os.fsync(stream.fileno())


def new_output(directory, filenames):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if any((directory / name).exists() for name in filenames):
        raise ValueError("Output already exists; choose a new output directory")
    return directory


def _number(value, name, *, minimum=0):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < minimum:
        raise ValueError(f"{name} must be a finite number >= {minimum}")
    return value


def _provider(value):
    value = {"openai": "gpt", "google": "gemini", "anthropic": "claude"}.get(value, value)
    if value not in PROVIDERS:
        raise ValueError("Provider must be gpt, gemini, or claude")
    return value


def _route(value):
    if not isinstance(value, dict):
        raise ValueError("Each model route must be an object")
    order = value.get("order") or [value.get("provider")]
    if isinstance(order, str):
        order = [part.strip() for part in order.split(",") if part.strip()]
    order = [_provider(provider) for provider in order]
    models = {_provider(provider): model for provider, model in (value.get("models") or {}).items()}
    if value.get("model"):
        models[order[0]] = value["model"]
    if any(not isinstance(models.get(provider), str) or not models[provider].strip() for provider in order):
        raise ValueError("Every provider in order needs an explicit model")
    configured = value.get("settings") or {}
    if not isinstance(configured, dict):
        raise ValueError("settings must be an object")
    nested = bool(configured) and all(key in PROVIDERS | {"openai", "google", "anthropic"} for key in configured)
    if nested:
        configured = {_provider(key): settings for key, settings in configured.items()}
    elif len(order) > 1 and configured:
        raise ValueError("Fallback routes require provider-keyed settings")
    else:
        configured = {order[0]: configured}
    settings = {}
    for provider in order:
        if not isinstance(configured.get(provider, {}), dict):
            raise ValueError("Each provider's settings must be an object")
        settings[provider] = dict(configured.get(provider, {}))
        cap = settings[provider].get("max_output_tokens")
        if "max_output_tokens" in settings[provider] and (isinstance(cap, bool) or not isinstance(cap, int) or cap <= 0):
            raise ValueError("max_output_tokens must be a positive integer")
    return {"order": order, "models": models, "settings": settings}


def parse_arms(value):
    data = json.loads(value) if value.lstrip().startswith(("[", "{")) else read_json(value)
    data = data.get("arms") if isinstance(data, dict) else data
    if not isinstance(data, list) or not data:
        raise ValueError("--arms needs a nonempty JSON list or a file containing one")
    result, names = [], set()
    for index, arm in enumerate(data):
        if not isinstance(arm, dict):
            raise ValueError("Each arm must be an object")
        name = str(arm.get("id") or arm.get("name") or f"arm-{index + 1}")
        if name in names:
            raise ValueError("Arm IDs must be unique")
        names.add(name)
        mode = arm.get("mode", "single")
        if mode != "single":
            raise ValueError("Arm mode must be single")
        if any(key in arm for key in ("draft", "polish", "share_draft")):
            raise ValueError("Draft, polish and shared generation options were removed; use single")
        if arm.get("groups"):
            raise ValueError("A single arm uses one whole-page route; group routes were removed")
        version = arm.get("prompt_version", "v2")
        if version not in {"v1", "v2"}:
            raise ValueError("prompt_version must be v1 or v2")
        overrides = _route(arm.get("all") or arm)
        overrides.update(mode=mode, prompt_version=version)
        for option in ("rewrite_slots", "required_slots"):
            if option in arm:
                slots = arm[option]
                if not isinstance(slots, list) or any(not isinstance(slot, str) or not slot.strip() for slot in slots):
                    raise ValueError(f"{option} must be a list of nonempty slot kinds")
                overrides[option] = slots
        item = {"id": name, "overrides": overrides}
        result.append(item)
    return result


def _price(model, prices):
    item = prices.get("models", {}).get(model)
    if not isinstance(item, dict) or "input" not in item or "output" not in item:
        raise BudgetExceeded(f"Verified price missing for model {model}")
    try:
        return {key: _number(item.get(key, item["input"]), f"price {key}")
                for key in ("input", "cached_input", "output")}
    except ValueError as error:
        raise BudgetExceeded(str(error)) from None


def usage_cost(provider, model, usage, prices):
    """GPT/Claude output includes reasoning; Gemini candidates and thoughts are separate."""
    if not isinstance(usage, dict) or not {"input_tokens", "output_tokens"} <= usage.keys():
        return None
    try:
        rates = _price(model, prices)
        prompt = _number(usage["input_tokens"], "input_tokens")
        cached = min(prompt, _number(usage.get("cached_input_tokens", 0), "cached_input_tokens"))
        output = _number(usage["output_tokens"], "output_tokens")
        if provider == "gemini" and not usage.get("output_includes_reasoning", False):
            output += _number(usage.get("reasoning_tokens", 0), "reasoning_tokens")
        return ((prompt - cached) * rates["input"] + cached * rates["cached_input"] + output * rates["output"]) / 1_000_000
    except (ValueError, BudgetExceeded):
        return None


def call_ceiling(request, prices):
    rates = _price(request["model"], prices)
    settings = request.get("settings") or {}
    cap = settings.get("max_output_tokens")
    if isinstance(cap, bool) or not isinstance(cap, int) or cap <= 0:
        raise BudgetExceeded("Call lacks an enforceable max_output_tokens ceiling")
    if request["provider"] == "gemini" and "thinking_budget" in settings:
        thinking = settings["thinking_budget"]
        if isinstance(thinking, bool) or not isinstance(thinking, int) or thinking < 0:
            raise BudgetExceeded("Explicit thinking_budget must be a finite nonnegative integer")
    if request["provider"] == "gemini" and "thinking_config" in settings:
        thinking = settings["thinking_config"]
        if not isinstance(thinking, dict):
            raise BudgetExceeded("thinking_config must be an object")
        for key, value in thinking.items():
            if re.sub(r"[_-]", "", key).casefold() == "thinkingbudget":
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise BudgetExceeded("Explicit thinking_config budget must be a finite nonnegative integer")
    payload = {key: request.get(key) for key in ("system_prompt", "prompt", "schema", "settings")}
    # One UTF-8 byte per token is conservative for text; allow framing/schema overhead.
    input_ceiling = len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) + 1024
    # Gemini thinking consumes the total output limit; Gemini 3.7/3.8 cannot
    # disable thinking. Preserve level settings and optional owner budgets:
    # https://firebase.google.com/docs/ai-logic/thinking
    return (input_ceiling * max(rates["input"], rates["cached_input"]) + cap * rates["output"]) / 1_000_000


class Budget:
    """Synchronous hooks reserve before await; concurrent completions settle by ID.

    Persisted reservations survive process interruption. Unknown usage never
    releases a reservation, including calls interrupted before their audit row.
    """
    def __init__(self, *, max_calls, max_usd, prices, journal=None):
        if isinstance(max_calls, bool) or not isinstance(max_calls, int) or max_calls < 0:
            raise ValueError("max-calls must be a nonnegative integer")
        self.max_calls, self.max_usd, self.prices = max_calls, _number(max_usd, "max-usd"), prices
        self.calls, self.committed_usd, self.known_usd = 0, 0.0, 0.0
        self.pending, self.journal = {}, journal

    def restore(self, events):
        settled = set()
        for event in events:
            identifier = event["reservation_id"]
            if event["event"] == "reserve":
                if identifier in self.pending or identifier in settled:
                    raise ValueError("Duplicate budget reservation in resume journal")
                self.pending[identifier] = event
                self.calls += 1
                self.committed_usd += _number(event["reserved_usd"], "reserved_usd")
            elif event["event"] == "settle":
                reservation = self.pending.pop(identifier, None)
                if reservation is None:
                    raise ValueError("Unmatched settlement in resume journal")
                settled.add(identifier)
                if event["usd"] is not None:
                    cost = _number(event["usd"], "usd")
                    self.known_usd += cost
                    self.committed_usd += cost - reservation["reserved_usd"]
            else:
                raise ValueError("Unknown event in resume budget journal")
        if self.calls > self.max_calls or self.committed_usd > self.max_usd + 1e-12:
            raise BudgetExceeded("Resume budget is below amounts already committed")

    def before_call(self, request, *, context=None):
        if self.calls >= self.max_calls:
            raise BudgetExceeded("max-calls would be exceeded")
        reserve = call_ceiling(request, self.prices)
        if self.committed_usd + reserve > self.max_usd:
            raise BudgetExceeded("max-usd would be exceeded by the next call reservation")
        identifier = f"call-{self.calls + 1}"
        event = {"event": "reserve", "reservation_id": identifier, "provider": request["provider"],
                 "model": request["model"], "reserved_usd": reserve, "context": context or {},
                 "stage": call_stage(request),
                 **({"key_source": request["key_source"]} if "key_source" in request else {})}
        if self.journal:
            self.journal(event)
        request["reservation_id"] = identifier
        self.calls += 1
        self.committed_usd += reserve
        self.pending[identifier] = event

    def after_call(self, log):
        if not self.pending:
            raise BudgetExceeded("Call completed without a pre-call budget reservation")
        identifier = log.get("reservation_id") or (log.get("request") or {}).get("reservation_id")
        if identifier is None:
            matches = [key for key, event in self.pending.items()
                       if (event["provider"], event["model"]) == (log.get("provider"), log.get("model"))]
            if len(matches) != 1:
                raise BudgetExceeded("Ambiguous concurrent call without reservation ID")
            identifier = matches[0]
        event = self.pending.get(identifier)
        if event is None:
            raise BudgetExceeded("Call completed without a matching budget reservation")
        provider, model, reserve = event["provider"], event["model"], event["reserved_usd"]
        if log.get("provider") != provider or log.get("model") != model:
            raise BudgetExceeded("Call routing changed after budget reservation")
        cost = usage_cost(provider, model, log.get("usage"), self.prices)
        # A logged cancellation still has unknown provider completion/usage.
        # Retain its durable reservation so a resume cannot reclaim that budget.
        if log.get("status") != "cancelled" or cost is not None:
            if self.journal:
                self.journal({"event": "settle", "reservation_id": identifier, "usd": cost})
            self.pending.pop(identifier)
        log["reservation_id"] = identifier
        log["usd"], log["reserved_usd"] = cost, reserve
        log["cost_status"] = "measured" if cost is not None else "unknown_reserved"
        if cost is not None:
            self.committed_usd += cost - reserve
            self.known_usd += cost
            if cost > reserve + 1e-12:
                raise BudgetExceeded("Provider usage exceeded the enforced reservation; stopping")


def seed_storyboard(case, template):
    """Build the catalog's default v4 card layout without photos, DB, or generation.

    Existing v4 boards are reused only for their matching template. Legacy block
    fixtures are input facts, not page layouts, so use the catalog seed for those.
    """
    from app.agents.page_templates import CATALOG, get_template

    board = case.get("storyboard")
    if isinstance(board, dict):
        board = board.get(template)
    if board is None:
        board = case.get("blocks")
    if board and all(row.get("taxonomyVersion") == 4 for row in board):
        if all(str(row.get("pageSectionKey", "")).startswith(template + ".") for row in board):
            return copy.deepcopy(board)
    definition = get_template(template)
    if definition is None:
        raise ValueError("Unknown template")
    product, analysis = case.get("product") or {}, case.get("analysis") or {}
    colors = product.get("colors") or [{"id": "base"}]
    base = next((color for color in colors if color.get("isBase")), colors[0])
    candidates = (analysis.get("detailRecommendations") or {}).get("candidates") or []
    cards = []
    for section in definition["sections"]:
        if section.get("presence") != "default":
            continue
        instance, kind = section["instanceKey"], section["kind"]
        key = f"{template}.{instance}"
        common = {"taxonomyVersion": 4, "pageSectionKey": key, "pageSectionKind": kind,
                  "pageDesignId": section["designId"], "sectionId": f"section-{key}", "sectionLayout": "stack"}
        if CATALOG["kinds"][kind].get("storyboardTextCard") or not section["photoSlots"]:
            cards.append({**common, "id": f"{key}.text", "source": "text", "slotKey": None})
        for slot in section["photoSlots"]:
            count = slot["count"]
            detail = (slot.get("recipe") or {}).get("detailPool") == "points"
            if isinstance(count, dict):
                if detail:
                    count = min(len(candidates), count.get("max", len(candidates)))
                else:
                    count = min(count.get("max", 100), count.get("colors1", 1) if len(colors) == 1 else
                                count.get("colors2plus", 1) + max(0, len(colors) - 2) * count.get("perExtraColor", 0))
            for color in colors if slot.get("color") == "each" else [base]:
                for index in range(count):
                    slot_key = f"{instance}.{slot['slotKey']}" + (f".{index}" if count > 1 else "")
                    card = {**common, "id": f"{key}.{slot['slotKey']}.{color.get('id', 'base')}.{index}",
                            "source": "reuse" if slot["fill"] == "reuse" else "ai", "slotKey": slot_key,
                            "contentRole": slot.get("contentRole"), "colorId": color.get("id", "base")}
                    recipe = slot.get("recipe") or {}
                    card.update({field: recipe[field] for field in ("cutType", "shot", "direction") if field in recipe})
                    if detail:
                        card["detailTargetId"] = candidates[index]["id"]
                    cards.append(card)
        if not any(card["pageSectionKey"] == key for card in cards):
            cards.append({**common, "id": f"{key}.text", "source": "text", "slotKey": None})
    return cards


def case_inputs(case, template):
    """Accept saved analysis contracts and the richer hand-curated evaluation cases."""
    analysis = copy.deepcopy(case.get("analysis") or {})
    for key in ("mdComment", "materials", "sellingPoints", "detailRecommendations", "detailCandidates"):
        if key in case:
            analysis[key] = copy.deepcopy(case[key])
    contract = copy.deepcopy(analysis.get("detailRecommendations") or {})
    candidates = contract.get("candidates", analysis.get("detailCandidates", []))
    if not isinstance(candidates, list) or any(not isinstance(candidate, dict) for candidate in candidates):
        raise ValueError("Detail candidates must be a list of objects")
    if candidates:
        contract["candidates"] = [{**candidate, "id": candidate.get("id") or f"eval-detail-{index + 1}"}
                                  for index, candidate in enumerate(candidates)]
        analysis["detailRecommendations"] = contract
    product = copy.deepcopy(case.get("product") or {})
    if "colors" in case:
        product["colors"] = copy.deepcopy(case["colors"])
    analysis["pageStyle"] = {**analysis.get("pageStyle", {}), "templateId": template}
    return product, analysis


def _provider_keys(env_file=None):
    wanted = {"OPENAI_API_KEY", "GEMINI_API_KEY", "GEMINI_API_KEY_NEXT", "ANTHROPIC_API_KEY"}
    keys = {}
    path = Path(env_file) if env_file else SERVER / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.strip().removeprefix("export ").partition("=")
            if sep and key.strip() in wanted:
                keys[key.strip()] = value.strip().strip('"').strip("'")
    return {key: os.environ.get(key, keys.get(key, "")) for key in wanted}


def _settings(env_file=None):
    # The reserve is local to the evaluator, never added to production settings.
    for key, value in _provider_keys(env_file).items():
        if key != "GEMINI_API_KEY_NEXT" and value:
            os.environ.setdefault(key, value)
    from app.config import load_settings
    return load_settings()


def gemini_exhaustion_reason(error):
    """Distinguish lasting quota/billing failures from ordinary burst throttling.

    A bare 429/RESOURCE_EXHAUSTED or a quota-documentation link is insufficient.
    Per-second/minute quota failures are transient unless a separate explicit
    daily quota or billing-account failure is present.
    """
    response = getattr(error, "response", None)
    status = getattr(error, "status_code", None) or getattr(response, "status_code", None)
    text = (str(error) + " " + str(getattr(response, "text", ""))).casefold()
    text = re.sub(r"https?://[^\s\"'<>]+", "", text)
    if status == 402:
        return "http_402"
    billing = re.search(
        r"(?:billing|payment|credit[s]?|balance).{0,45}(?:exhausted|disabled|inactive|insufficient|depleted|unpaid|not enabled|required)"
        r"|(?:insufficient|exhausted|depleted).{0,35}(?:credit[s]?|balance)"
        r"|(?:billing|payment)[_ -](?:not[_ -]active|disabled|required)", text)
    if billing and re.search(r"billing|payment|credit[s]?|balance", text):
        return "billing"
    daily = re.search(r"per[_ /-]?day|daily|per[_ /-]?month|monthly", text)
    exhausted = re.search(r"quota.{0,35}(?:exhausted|exceeded|depleted|reached)"
                          r"|(?:exhausted|insufficient|depleted).{0,35}quota", text)
    # Gemini's canonical message says "exceeded your current quota" while
    # QuotaFailure supplies the actual daily/monthly scope in quotaId. Without
    # that scope this generic message can also describe a transient rate limit.
    if daily and re.search(r"exceeded.{0,35}quota", text):
        exhausted = True
    if not exhausted:
        return None
    transient = re.search(r"per[_ /-]?(?:minute|second)|rate[_ -]?limit|\brpm\b|\btpm\b", text)
    if transient and not daily:
        return None
    return "quota_exhausted"


async def _provider_call(settings, request, timeout, metadata, *, gemini_key=None):
    from app.agents import page_copy, vision_llm
    if request["provider"] != "gemini":
        return await page_copy._call(settings, request, timeout, metadata)
    # Preserve the complete HTTP error for quota metrics that may follow long
    # provider boilerplate. The general vision helpers truncate or replace it.
    if not gemini_key:
        raise vision_llm.VisionError("GEMINI_API_KEY 미설정")
    config = request["settings"]
    body = await vision_llm.run_cpu_bound(vision_llm._gemini_body,
        request["prompt"], [], request["schema"],
        config.get("thinking_level") or getattr(settings, "analysis_thinking_level", "low"),
        config["max_output_tokens"], request["system_prompt"],
        config.get("thinking_budget"), config.get("thinking_config"))
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(vision_llm._GEMINI_URL.format(model=request["model"]),
                                     json=body, headers={"x-goog-api-key": gemini_key})
    response.raise_for_status()
    if response.status_code != 200:
        raise vision_llm.VisionError("Unexpected Gemini response", status_code=response.status_code)
    raw = await vision_llm.run_cpu_bound(vision_llm._parse_gemini_response,
                                       response, metadata, request["model"])

    return {"response": raw, "usage": page_copy._usage("gemini", metadata.get("usage"))}


def slot_kind(row):
    """Stable copy-field identity, independent of section or repeated-item index."""
    known = row.get("slot_kind")
    if isinstance(known, str) and known:
        return known
    kind = str(row.get("kind") or "")
    role = re.sub(r"(^|\.)\d+(?=\.|$)", r"\1n", str(row.get("role") or ""))
    if row.get("target") == "part" or kind in {"part", "part_description", "part_desc"} or kind == "points" and role == "items.n.desc":
        return "part.desc"
    if row.get("target") == "feature" or kind == "feature":
        return "feature.desc"
    # Canonical kinds also occur in hand-curated and older exported sheets.
    if "." in kind:
        return re.sub(r"(^|\.)\d+(?=\.|$)", r"\1n", kind)
    if kind == "long_body" and row.get("group") == "intro":
        kind = "intro"
    if kind == "review_text" or kind == "review" and not role:
        return "review.text"
    if kind in {"review", "faq"}:
        role = re.sub(r"^items\.n\.", "", role)
    if role.startswith(kind + "."):
        return role
    return f"{kind}.{role}" if role else kind


def call_stage(record):
    return "rewrite" if record.get("stage") == "rewrite" or record.get("rewrite") else "single"


def _call_metrics(calls):
    actual = [call for call in calls if call.get("status") != "dry_run"]
    known = sum(call.get("usd") or 0 for call in actual)
    unknown = sum(call.get("usd") is None for call in actual)
    unpriced = sum(call.get("estimated_upper_usd") is None for call in calls if call.get("status") == "dry_run")
    providers = sorted({call["provider"] for call in actual})
    return {"calls": len(actual), "planned_calls": len(calls), "known_usd": known,
            "usd": None if unknown or unpriced else known, "unknown_cost_calls": unknown,
            "unpriced_planned_calls": unpriced, "seconds": sum(call.get("seconds", 0) for call in actual),
            "provider_count": len(providers), "providers": providers,
            "planned_provider_count": len({call["provider"] for call in calls}),
            "estimated_upper_usd": sum(call["estimated_upper_usd"] for call in calls)
                if calls and not actual and not unpriced else None,
            **{key: sum((call.get("usage") or {}).get(key, 0) or 0 for call in actual)
               for key in ("input_tokens", "output_tokens", "cached_input_tokens", "reasoning_tokens")}}


def _slot_metrics(rows):
    sources = Counter(row.get("source", "ai") for row in rows)
    return {"source_counts": dict(sources), "non_ai_slots": sum(count for source, count in sources.items() if source != "ai"),
            "seller_original": sources["seller_original"], "seller_cleaned": sources["seller_cleaned"]}


def _summary(slots, pages):
    grouped = defaultdict(list)
    for row in slots:
        if row.get("passed") is not None and row.get("source", "ai") == "ai":
            grouped[(slot_kind(row), row.get("model"), row.get("provider"))].append(row)
    kinds = []
    for (kind, model, provider), rows in sorted(grouped.items(), key=lambda item: str(item[0])):
        reasons = Counter(reason for row in rows for reason in row.get("reasons", []))
        kinds.append({"kind": kind, "model": model, "provider": provider, "slots": len(rows),
                      "acceptance_rate": sum(bool(row["passed"]) for row in rows) / len(rows),
                      "rewrite_rate": sum(bool(row.get("rewrite")) for row in rows) / len(rows), "reasons": dict(reasons)})
    arms = []
    for arm in sorted({page["arm"] for page in pages}):
        selected = [page for page in pages if page["arm"] == arm]
        known_usd = sum(page["known_usd"] for page in selected)
        unknown = sum(page["unknown_cost_calls"] for page in selected)
        unpriced = sum(page.get("unpriced_planned_calls", 0) for page in selected)
        warnings = (["unknown_call_cost"] if unknown else []) + (["unpriced_dry_run"] if unpriced else [])
        stages = {}
        for stage in ("single", "rewrite"):
            metrics = [page.get("stages", {}).get(stage, {}) for page in selected]
            cost = sum(item.get("known_usd", 0) for item in metrics)
            uncertain = any(item.get("unknown_cost_calls") or item.get("unpriced_planned_calls") for item in metrics)
            seconds = sum(item.get("seconds", 0) for item in metrics)
            stages[stage] = {"calls": sum(item.get("calls", 0) for item in metrics),
                "logical_calls": sum(item.get("calls", 0) for item in metrics),
                "planned_calls": sum(item.get("planned_calls", 0) for item in metrics),
                "known_usd": cost, "usd": None if uncertain else cost,
                "mean_page_usd": None if uncertain else cost / len(selected),
                "seconds": seconds, "mean_page_seconds": seconds / len(selected),
                "providers": sorted({provider for item in metrics for provider in item.get("providers", [])}),
                **{key: sum(item.get(key, 0) for item in metrics)
                   for key in ("input_tokens", "output_tokens", "cached_input_tokens", "reasoning_tokens")}}
            stages[stage]["provider_count"] = len(stages[stage]["providers"])
            stages[stage]["label"] = {"single": "생성 값", "rewrite": "고쳐 쓰기 값"}[stage]
        providers = sorted({provider for page in selected for provider in page.get("providers", [])})
        item = {"arm": arm, "pages": len(selected), "calls": sum(page["calls"] for page in selected),
                     "logical_calls": sum(page["calls"] for page in selected),
                     "planned_calls": sum(page.get("planned_calls", 0) for page in selected),
                     "usd": None if warnings else known_usd,
                     "mean_page_usd": None if warnings else known_usd / len(selected),
                     "known_usd": known_usd, "cost_warnings": warnings,
                     "unknown_cost_calls": unknown, "unpriced_planned_calls": unpriced,
                     "seconds": sum(page["seconds"] for page in selected),
                     "mean_page_seconds": statistics.mean(page["seconds"] for page in selected),
                     "mean_page_calls": statistics.mean(page["calls"] for page in selected),
                     "mean_page_provider_count": statistics.mean(page["provider_count"] for page in selected),
                     "mean_page_known_usd": statistics.mean(page["known_usd"] for page in selected),
                     "providers": providers, "provider_count": len(providers), "stages": stages,
                     **_slot_metrics([row for row in slots if row.get("arm") == arm])}
        arms.append(item)
    return {"kind_model": kinds, "arms": arms, "pages": pages, **_slot_metrics(slots)}


def _page_key(row):
    return tuple(row.get(key) for key in ("case", "template", "arm", "run"))


async def run_evaluation(args, *, caller=None, settings=None):
    directory = getattr(args, "resume", None) or args.output
    if not directory:
        raise ValueError("--output or --resume is required")
    output = Path(directory)
    output.mkdir(parents=True, exist_ok=True)
    # A second process must not share a budget or append the same page twice.
    with (output / ".run.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Evaluation directory is already in use") from None
        try:
            return await _run_evaluation(args, output, caller=caller, settings=settings)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


async def _run_evaluation(args, output, *, caller=None, settings=None):
    resume = bool(getattr(args, "resume", None))
    previous = read_json(output / "run.json") if resume else {}
    if resume and previous.get("format_version") != 3:
        raise ValueError("Resume requires an A3 run with a durable budget journal")
    send = bool(args.send)
    if resume and previous["mode"] != ("send" if send else "dry_run"):
        raise ValueError("Resume mode differs; a paid resume requires explicit --send")
    args.runs = args.runs if args.runs is not None else previous.get("runs", 1)
    args.seed = args.seed if args.seed is not None else previous.get("seed", 0)
    args.max_calls = args.max_calls if args.max_calls is not None else previous.get("max_calls")
    args.max_usd = args.max_usd if args.max_usd is not None else previous.get("max_usd")
    if send and (args.max_calls is None or args.max_usd is None):
        raise ValueError("--send requires both --max-calls and --max-usd")
    if args.runs < 1 or not math.isfinite(args.timeout) or args.timeout <= 0 or args.concurrency < 1:
        raise ValueError("runs, timeout, and concurrency must be positive")
    if not resume and (not args.arms or not args.cases):
        raise ValueError("--cases and --arms are required for a new evaluation")
    arms = parse_arms(args.arms) if args.arms else previous["arms"]
    for arm in arms:
        overrides = arm.get("overrides") or {}
        if (overrides.get("mode", "single") != "single" or overrides.get("groups")
                or any(key in overrides or key in arm for key in ("draft", "polish", "share_draft"))):
            raise ValueError("Only single arms can run or resume; staged options were removed")
    cases = read_rows(args.cases) if args.cases else previous["cases"]
    if not cases:
        raise ValueError("Cases must not be empty")
    templates = list(dict.fromkeys(part for item in (args.templates or previous.get("templates") or ["emo", "show"])
                                   for part in item.split(",") if part))
    if not templates or any(template not in {"emo", "show"} for template in templates):
        raise ValueError("Templates must be emo and/or show")
    feature_route = bool(args.feature_route or previous.get("feature_route"))
    identifiers = [str(case.get("caseId") or case.get("id") or f"case-{i+1}") for i, case in enumerate(cases)]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("Case IDs must be unique")
    args.prices = args.prices or previous.get("prices_path") or str(DEFAULT_PRICES)
    prices = read_json(args.prices) if not resume or args.prices != previous.get("prices_path") else previous["prices"]
    manifest = {"format_version": 3, "mode": "send" if send else "dry_run", "seed": args.seed,
        "runs": args.runs, "templates": templates, "arms": arms, "case_ids": identifiers, "cases": cases,
        "feature_route": feature_route, "max_calls": args.max_calls, "max_usd": args.max_usd,
        "prices": prices, "prices_path": str(Path(args.prices).resolve()), "concurrency": args.concurrency,
        "key_sources": {"gpt": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY", "claude": "ANTHROPIC_API_KEY"}}
    if settings is None:
        settings = _settings(args.env_file) if send else SimpleNamespace(model_text="", model_text_gemini="")
    next_key = (getattr(settings, "gemini_api_key_next", "") or _provider_keys(args.env_file)["GEMINI_API_KEY_NEXT"]) if send else ""
    credentials = copy.deepcopy(previous.get("gemini_credentials") or {"source": "primary", "switches": []})
    if credentials["source"] not in {"primary", "next"} or len(credentials["switches"]) > 1:
        raise ValueError("Invalid Gemini credential audit state")
    if send and credentials["source"] == "next" and not next_key:
        raise ValueError("Resume requires the Gemini reserve credential already selected by this run")
    manifest["gemini_credentials"] = credentials
    scrub = Redactor(settings, secrets=[next_key])
    manifest = scrub(manifest)
    if resume:
        for key in ("mode", "seed", "runs", "templates", "arms", "cases", "feature_route", "prices"):
            if previous.get(key) != manifest[key]:
                raise ValueError(f"Resume configuration changed: {key}")
    else:
        new_output(output, ["run.json", "calls.jsonl", "attempts.jsonl", "slots.jsonl", "summary.json", "pages.jsonl", "budget.jsonl"])
        for filename in ("calls.jsonl", "attempts.jsonl", "slots.jsonl", "pages.jsonl", "budget.jsonl"):
            (output / filename).touch()
    budget = Budget(max_calls=args.max_calls, max_usd=args.max_usd, prices=prices,
                    journal=lambda event: append_row(output / "budget.jsonl", scrub(event), durable=True)) if send else None
    if budget and resume:
        budget.restore(read_rows(output / "budget.jsonl"))
    all_calls = read_rows(output / "calls.jsonl")
    all_attempts = read_rows(output / "attempts.jsonl")
    all_slots = read_rows(output / "slots.jsonl")
    history = read_rows(output / "pages.jsonl")
    latest_pages = {_page_key(page): page for page in history}
    completed = {key for key, page in latest_pages.items() if page["status"] in {"ok", "dry_run", "not_called"}}
    write_json(output / "run.json", manifest)
    stop = None
    from app.agents import page_copy, feature_copy

    jobs = []
    for case_id, case in zip(identifiers, cases):
        for template in (["feature"] if feature_route else templates):
            for run_index in range(args.runs):
                page_seed = int.from_bytes(hashlib.sha256(f"{args.seed}:{case_id}:{template}:{run_index}".encode()).digest()[:8], "big")
                for arm in arms:
                    context = {"case": case_id, "template": template, "arm": arm["id"],
                               "run": run_index + 1, "seed": page_seed}
                    jobs.append((case, context, arm))

    for index, record in enumerate(all_calls):
        record.setdefault("call_id", record.get("reservation_id") or f"planned-{index + 1}")

    def audit_hooks(context, calls):
        retry_seconds = {}
        def before_call(request):
            nonlocal stop
            if stop:
                raise page_copy.CallLimitError(stop)
            if request["provider"] == "gemini":
                request["key_source"] = credentials["source"]
            try:
                budget.before_call(request, context=context)
            except BudgetExceeded as error:
                stop = scrub(str(error))
                raise page_copy.CallLimitError(stop) from None

        def after_call(log):
            nonlocal stop
            record = copy.deepcopy(log)
            request = log.get("request") or {}
            record["seconds"] = max(0, record.get("seconds", 0) - retry_seconds.pop(id(request), 0))
            if record["provider"] == "gemini":
                record["key_source"] = request.get("key_source", credentials["source"])
            record["stage"] = call_stage(record)
            budget_error = None
            try:
                if send:
                    budget.after_call(record)
                else:
                    record["usd"], record["cost_status"] = 0, "dry_run"
                    try:
                        record["estimated_upper_usd"] = call_ceiling(record.get("request", record), prices)
                    except (BudgetExceeded, KeyError):
                        record["estimated_upper_usd"] = None
            except BudgetExceeded as error:
                budget_error, stop = error, scrub(str(error))
            finally:
                record = scrub({**context, "call": len(calls) + 1, **record})
                record["call_id"] = record.get("reservation_id") or f"planned-{len(all_calls) + 1}"
                for field, default in (("request", {}), ("response", None), ("validation", {}), ("usage", {}), ("seconds", 0)):
                    record.setdefault(field, default)
                calls.append(record)
                all_calls.append(record)
                append_row(output / "calls.jsonl", record, durable=send)
                if send:
                    attempts = record["validation"].get("rows", [])
                    if record.get("status") == "error" and not attempts:
                        attempts = [{"slotId": slot["id"], "group": slot["group"], "kind": slot["kind"],
                            "role": slot["role"], "target": slot["target"], "text": "", "passed": False,
                            "reasons": ["provider_error"], "rewrite": record.get("rewrite", False),
                            "provider": record["provider"], "model": record["model"]}
                            for slot in record["request"].get("slots", [])]
                    for attempt in attempts:
                        row = scrub({**context, "call": record["call"], "call_id": record["call_id"],
                                     "stage": record["stage"], **attempt, "slot_kind": slot_kind(attempt)})
                        all_attempts.append(row)
                        append_row(output / "attempts.jsonl", row)
            if budget_error is not None:
                raise page_copy.CallLimitError(str(budget_error)) from None

        async def dispatch(request, timeout):
            started = time.monotonic()
            source = request.get("key_source", "primary")
            metadata = {}
            async def invoke(remaining):
                if caller is not None:
                    return await caller(request, remaining)
                key = next_key if request.get("key_source") == "next" else getattr(settings, "gemini_api_key", "")
                return await _provider_call(settings, request, remaining, metadata, gemini_key=key)
            try:
                return await invoke(timeout)
            except Exception as error:
                reason = gemini_exhaustion_reason(error) if request["provider"] == "gemini" else None
                if source != "primary" or not next_key or not reason:
                    raise
                elapsed = time.monotonic() - started
                switch = None
                if credentials["source"] == "primary":
                    switch = {"from": "primary", "to": "next", "reason": reason,
                              "call_id": request.get("reservation_id"), "context": context,
                              "at": time.time()}
                    credentials["source"] = "next"
                    credentials["switches"].append(switch)
                    manifest["gemini_credentials"] = credentials
                    write_json(output / "run.json", scrub(manifest))
                failed = {"provider": "gemini", "model": request["model"],
                    "groups": request["groups"], "stage": call_stage(request),
                    "rewrite": request.get("rewrite", False), "request": copy.deepcopy(request),
                    "response": None, "validation": {}, "usage": page_copy._usage("gemini", metadata.get("usage")),
                    "seconds": elapsed, "status": "error", "error": type(error).__name__,
                    "retry_reason": reason}
                if switch:
                    failed["credential_switch"] = switch
                after_call(failed)
                # The core still owns the terminal success/error audit. A fresh
                # reservation accounts for this second physical request.
                before_call(request)
                retry_seconds[id(request)] = elapsed
                metadata.clear()
                return await invoke(max(0, timeout - elapsed))

        return before_call, after_call, dispatch

    async def generate(case, context, overrides, timeout):
        product, analysis = case_inputs(case, templates[0] if feature_route else context["template"])
        if feature_route:
            audit = {}
            features = await feature_copy.generate(settings, analysis.get("sellingPoints") or [], product, analysis,
                seed=context["seed"], timeout_seconds=timeout, overrides=overrides, audit=audit)
            return {**audit, "featureCopy": features}
        board = seed_storyboard({**case, "product": product, "analysis": analysis}, context["template"])
        return await page_copy.generate(settings, board, product, analysis, seed=context["seed"],
            timeout_seconds=timeout, overrides=overrides)

    async def evaluate_page(case, context, arm):
        nonlocal stop
        key = _page_key(context)
        if key in completed or stop:
            return
        prior_attempts = [row.get("page_attempt", 1) for rows in (all_calls, history, all_slots)
                          for row in rows if _page_key(row) == key]
        context = {**context, "page_attempt": max(prior_attempts, default=0) + 1}
        calls, started = [], time.monotonic()
        before_call, after_call, dispatch = audit_hooks(context, calls)
        overrides = copy.deepcopy(arm["overrides"])
        overrides.update(dry_run=not send, before_call=before_call, after_call=after_call)
        if send:
            overrides["caller"] = dispatch
        result = None
        status, error_type, error_message = "dry_run" if not send else "ok", None, None
        try:
            result = await generate(case, context, overrides, args.timeout)
            if not send and result and not calls:
                for record in result.get("log", {}).get("calls", []):
                    after_call(record)
        except (page_copy.CallLimitError, BudgetExceeded) as error:
            status, stop = "budget_stopped", scrub(str(error))
            result = getattr(error, "partial_result", None)
        except Exception as error:
            status, error_type, error_message = "error", type(error).__name__, scrub(str(error))
        if result and not (status == "budget_stopped" and not calls):
            product, _ = case_inputs(case, templates[0] if feature_route else context["template"])
            for slot in result.get("log", {}).get("slots", []):
                row = {**context, **slot, "slot_kind": slot_kind(slot), "productName": product.get("name", "")}
                row.setdefault("passed", row.pop("pass", None))
                row.setdefault("reasons", [])
                row.setdefault("rewrite", False)
                if not send:
                    row["passed"] = None
                row = scrub(row)
                all_slots.append(row)
                append_row(output / "slots.jsonl", row, durable=send)
        warnings = scrub(result.get("log", {}).get("warnings", [])) if result else []
        stopped_phase = (result.get("log", {}).get("stopped") or {}).get("phase") if result else None
        if send and status == "ok" and not any(call.get("status") != "dry_run" for call in calls):
            status = "not_called"
        page = {**context, "status": status, "error_type": error_type, "error": error_message,
                "warnings": warnings, "rewrite_status": "not_run" if stopped_phase == "rewrite" else None,
                "note": "고쳐 쓰기 못 함" if stopped_phase == "rewrite" else None,
                "seconds": time.monotonic() - started,
                "call_refs": [call["call_id"] for call in calls]}
        history.append(page)
        latest_pages[key] = page
        append_row(output / "pages.jsonl", page, durable=True)

    queue = iter(jobs)

    async def worker():
        for job in queue:
            await evaluate_page(*job)

    await asyncio.gather(*(worker() for _ in range(min(args.concurrency, len(jobs)))))
    selected_slots = [row for row in all_slots if _page_key(row) in latest_pages
                      and row.get("page_attempt", 1) == latest_pages[_page_key(row)].get("page_attempt", 1)]
    unresolved = list(budget.pending.values()) if budget else []
    physical_calls = list(all_calls)
    logged_ids = {call.get("reservation_id") for call in all_calls}
    physical_calls += [{**event["context"], **event, "call_id": event["reservation_id"],
                       "status": "interrupted", "usd": None, "seconds": 0, "usage": {}}
                      for event in unresolved if event["reservation_id"] not in logged_ids]
    pages = []
    for key, latest in latest_pages.items():
        calls = [row for row in physical_calls if _page_key(row) == key]
        page_slots = [row for row in selected_slots if _page_key(row) == key]
        pages.append({**latest, **_call_metrics(calls), "call_refs": [call["call_id"] for call in calls],
            "seconds": sum(page["seconds"] for page in history if _page_key(page) == key),
            "stages": {stage: _call_metrics([call for call in calls if call_stage(call) == stage])
                       for stage in ("single", "rewrite")}, **_slot_metrics(page_slots)})
    pages.sort(key=lambda page: _page_key(page))
    physical = _call_metrics(physical_calls)
    summary = {**_summary(selected_slots, pages), "attempt_kind_model": _summary(all_attempts, [])["kind_model"],
               "mode": "send" if send else "dry_run", "stopped": stop,
               "calls": budget.calls if budget else 0, "known_usd": budget.known_usd if budget else 0,
               "committed_usd": budget.committed_usd if budget else 0,
               "unresolved_reservations": len(unresolved), "resumed": resume,
               "skipped_completed_pages": len(completed),
               "planned_calls": physical["planned_calls"], "estimated_upper_usd": physical["estimated_upper_usd"],
               "unpriced_planned_calls": physical["unpriced_planned_calls"],
               "accounting": {"total_calls": "physical requests counted once", "arm_calls": "physical requests for each arm",
                              "arm_costs": "physical request costs for each arm"}}
    summary["cost_warnings"] = sorted({warning for arm in summary["arms"] for warning in arm["cost_warnings"]})
    write_json(output / "summary.json", scrub(summary))
    return summary


def _opaque_id(rng, used):
    while True:
        value = f"sample-{rng.getrandbits(64):016x}"
        if value not in used:
            used.add(value)
            return value


def _accepted_ai_text(row):
    """Do not attribute a preserved seller original to the failed drafting model.

    Old hand-curated export fixtures predate passed/source; absent fields remain
    compatible, but explicit rejection or unscored status is never accepted.
    """
    return (row.get("passed", row.get("pass", True)) is True
            and row.get("source", "ai") == "ai"
            and isinstance(row.get("text"), str) and bool(row["text"].strip()))


def _export_rows(source):
    rows = read_rows(source)
    if not any("page_attempt" in row for row in rows):
        return rows
    latest = {}
    for row in rows:
        latest[_page_key(row)] = max(latest.get(_page_key(row), 0), row.get("page_attempt", 1))
    checkpoint = Path(source).with_name("pages.jsonl")
    if Path(source).name == "slots.jsonl" and checkpoint.exists():
        latest.update({_page_key(page): page.get("page_attempt", 1) for page in read_rows(checkpoint)})
    return [row for row in rows if row.get("page_attempt", 1) == latest[_page_key(row)]]


def _export_bundles(rows, rng, one_arm_per_sheet):
    if not one_arm_per_sheet:
        return [(None, rows)]
    arms = sorted({row.get("arm") for row in rows}, key=str)
    rng.shuffle(arms)
    used = set()
    return [("bundle-" + _opaque_id(rng, used).removeprefix("sample-"),
             [row for row in rows if row.get("arm") == arm]) for arm in arms]


def blind_export(source, output, *, seed=0, one_arm_per_sheet=False):
    rows = [row for row in _export_rows(source) if _accepted_ai_text(row)]
    if not rows:
        raise ValueError("No generated text to export")
    output = new_output(output, ["blind.jsonl", "blind.key.json"])
    rng, used, key = random.Random(seed), set(), {}
    sheets = []
    for bundle, selected in _export_bundles(rows, rng, one_arm_per_sheet):
        filename = f"blind.{bundle}.jsonl" if bundle else "blind.jsonl"
        sheets.append(filename)
        rng.shuffle(selected)
        for row in selected:
            identifier = _opaque_id(rng, used)
            canonical = slot_kind(row)
            key[identifier] = {**row, "slot_kind": canonical, "sheet": filename}
            append_row(output / filename, {"id": identifier, "kind": canonical, "text": row["text"]})
    write_json(output / "blind.key.json", {"seed": seed, "items": key})
    return {"samples": len(rows), "sheets": sheets}


def _judgments(path, key):
    rows, result = read_rows(path), {}
    for row in rows:
        identifier = row.get("id")
        if identifier not in key or identifier in result:
            raise ValueError("Unknown or duplicate judgment ID")
        result[identifier] = row
    return result


def _scores(row):
    if "grade" in row:
        if row["grade"] not in {"A", "B", "C"}:
            raise ValueError("Grade must be A, B, or C")
        return {"grade": {"A": 2, "B": 1, "C": 0}[row["grade"]]}
    scores = row.get("scores", {"score": row.get("score")})
    if not isinstance(scores, dict) or not scores:
        raise ValueError("Each judgment needs grade, score, or scores")
    return {name: float(value) if isinstance(value, bool) else _number(value, "score") for name, value in scores.items()}


def blind_score(key_path, judge_a, judge_b):
    key = read_json(key_path)["items"]
    a, b = _judgments(judge_a, key), _judgments(judge_b, key)
    if set(a) != set(key) or set(b) != set(key):
        raise ValueError("Both judges must score every exported sample")
    buckets, disagreements = defaultdict(list), []
    for identifier, row in key.items():
        scores_a, scores_b = _scores(a[identifier]), _scores(b[identifier])
        if scores_a.keys() != scores_b.keys():
            raise ValueError("Both judges must use the same rubric")
        group = (slot_kind(row), row.get("model"))
        buckets[group].append((scores_a, scores_b))
        if scores_a != scores_b or a[identifier].get("reason") != b[identifier].get("reason"):
            disagreements.append({"id": identifier, "kind": group[0], "model": group[1],
                                  "judge_a": a[identifier], "judge_b": b[identifier]})
    scores = []
    for (kind, model), pairs in sorted(buckets.items(), key=lambda item: str(item[0])):
        flat = [value for pair in pairs for rubric in pair for value in rubric.values()]
        criteria = sorted({criterion for pair in pairs for rubric in pair for criterion in rubric})
        scores.append({"kind": kind, "model": model, "samples": len(pairs), "mean_score": statistics.mean(flat),
                       "judge_a_mean": statistics.mean(v for pair in pairs for v in pair[0].values()),
                       "judge_b_mean": statistics.mean(v for pair in pairs for v in pair[1].values()),
                       "criteria": {criterion: statistics.mean(rubric[criterion] for pair in pairs for rubric in pair if criterion in rubric)
                                    for criterion in criteria}})
    return {"kind_model_scores": scores, "disagreements": disagreements, "samples": len(key)}


def detection_kind(row):
    canonical = slot_kind(row)
    if canonical in DETECT_KINDS:
        return canonical
    kind, role, group = row.get("kind", ""), row.get("role", ""), row.get("group", "")
    if kind in DETECT_KINDS:
        return kind
    section = row.get("sectionKey", row.get("section", ""))
    if row.get("target") == "part" or kind in {"part", "part_description", "part_desc"} or kind == "points" and role.endswith(".desc"):
        return "part.desc"
    if role in {"intro.body", "body"} and (group == "intro" or kind == "intro" or ".intro" in section):
        return "intro.body"
    if role in {"fabric.body", "body"} and (group == "fabric" or kind == "fabric" or ".fabric" in section):
        return "fabric.body"
    if kind in {"review", "review_text"} or (group == "voice" and role.endswith(".text")):
        return "review.text"
    return None


def _anonymous_text(row):
    text = row["text"]
    names = [row.get(field) for field in ("shopName", "shop_name", "productName", "product_name")]
    for name in sorted({name for name in names if isinstance(name, str) and name.strip()}, key=len, reverse=True):
        text = text.replace(name, "")
    return re.sub(r"[ \t]+", " ", text).strip()


def _without_symbols(text):
    return re.sub(r"[!！~～\u200d\ufe0f\u2600-\u27bf\U0001f000-\U0001faff]", "", text).strip()


def detect_export(source, real_shop, output, *, seed=0, per_kind=20, kinds=DETECT_KINDS, rounds=2,
                  one_arm_per_sheet=False):
    if per_kind < 1 or rounds < 1:
        raise ValueError("per-kind and rounds must be positive")
    if not kinds or not set(kinds) <= set(DETECT_KINDS):
        raise ValueError("Unknown detection kind")
    ours = [row for row in _export_rows(source) if _accepted_ai_text(row)]
    shop = read_rows(real_shop)
    rng = random.Random(seed)
    bundles = []
    for bundle, own_rows in _export_bundles(ours, rng, one_arm_per_sheet):
        samples = []
        for kind in kinds:
            ours_kind = [row for row in own_rows if detection_kind(row) == kind]
            shop_kind = [row for row in shop if detection_kind(row) == kind and row.get("text", "").strip()]
            if len(ours_kind) < per_kind or len(shop_kind) < per_kind:
                raise ValueError(f"Insufficient samples for {kind}: need {per_kind} per origin in every bundle")
            chosen = rng.sample(ours_kind, per_kind)
            rng.shuffle(shop_kind)
            for index, row in enumerate(chosen):
                match = min(shop_kind, key=lambda real: abs(len(_anonymous_text(real)) - len(_anonymous_text(row))))
                shop_kind.remove(match)
                for origin, item in (("ours", row), ("shop", match)):
                    samples.append({**item, "kind": kind, "origin": origin, "bundle": bundle,
                                    "bundle_arm": row.get("arm") if one_arm_per_sheet else None,
                                    "source_sample": f"{bundle or 'all'}:{kind}:{origin}:{index}",
                                    "text": _anonymous_text(item)})
        bundles.append((bundle, samples))
    if not bundles:
        raise ValueError("No generated text to export")
    output = new_output(output, ["detect.key.json"])
    key, used, filenames = {}, set(), []
    for bundle, samples in bundles:
        for variant in ("raw", "normalized"):
            for round_index in range(1, rounds + 1):
                filename = f"detect.{bundle + '.' if bundle else ''}{variant}.round-{round_index}.jsonl"
                if (output / filename).exists():
                    raise ValueError("Output already exists; choose a new output directory")
                filenames.append(filename)
                shuffled = list(samples)
                rng.shuffle(shuffled)
                for row in shuffled:
                    identifier = _opaque_id(rng, used)
                    text = _without_symbols(row["text"]) if variant == "normalized" else row["text"]
                    append_row(output / filename, {"id": identifier, "kind": row["kind"], "text": text})
                    key[identifier] = {**row, "variant": variant, "round": round_index, "sheet": filename}
    write_json(output / "detect.key.json", {"seed": seed, "per_kind": per_kind, "rounds": rounds, "items": key})
    return {"per_kind_per_origin": per_kind, "rounds": rounds,
            "samples_per_sheet": per_kind * len(kinds) * 2, "sheets": filenames}


def detect_score(key_path, decisions):
    key, judgments = read_json(key_path)["items"], {}
    for path in decisions:
        for identifier, judgment in _judgments(path, key).items():
            if identifier in judgments:
                raise ValueError("Duplicate detection decision across files")
            if not isinstance(judgment.get("ai"), bool):
                raise ValueError("Detection decision needs ai: true or false")
            if _number(judgment.get("confidence"), "confidence") > 1:
                raise ValueError("Confidence must be between zero and one")
            if not isinstance(judgment.get("reason"), str) or not judgment["reason"].strip():
                raise ValueError("Detection decision needs a reason")
            judgments[identifier] = judgment

    def aggregate(items):
        own = [(meta, judgment) for meta, judgment in items if meta["origin"] == "ours"]
        real = [(meta, judgment) for meta, judgment in items if meta["origin"] == "shop"]
        reasons = Counter(re.sub(r"\s+", " ", judgment.get("reason_tag") or judgment["reason"]).strip().casefold()
                          for _, judgment in own if judgment["ai"])
        recall = sum(judgment["ai"] for _, judgment in own) / len(own) if own else None
        recurring = [{"reason": reason, "count": count} for reason, count in reasons.most_common() if count > 3]
        return {"ours_count": len(own), "real_shop_count": len(real), "ours_ai_recall": recall,
                "real_shop_false_positive_rate": sum(j["ai"] for _, j in real) / len(real) if real else None,
                "recurring_reasons": recurring, "reason_counts": dict(reasons),
                "passed": recall <= 0.65 and not recurring if recall is not None else None}

    resolved = [(key[identifier], judgment) for identifier, judgment in judgments.items()]
    primary = aggregate([item for item in resolved if item[0]["variant"] == "normalized"])
    expected_primary = {identifier for identifier, row in key.items() if row["variant"] == "normalized"}
    primary["complete"] = expected_primary <= judgments.keys()
    if not primary["complete"]:
        primary["passed"] = None
    grouped = defaultdict(list)
    for meta, judgment in resolved:
        grouped[(meta["variant"], meta["kind"], meta.get("model") if meta["origin"] == "ours" else None)].append((meta, judgment))
    return {"primary": primary, "raw": aggregate([item for item in resolved if item[0]["variant"] == "raw"]),
            "kind_model": [{"variant": variant, "kind": kind, "model": model, **aggregate(items)}
                           for (variant, kind, model), items in sorted(grouped.items(), key=lambda item: str(item[0]))],
            "decisions": len(judgments), "unscored": len(key) - len(judgments),
            "reason_grouping": "Exact normalized reason_tag (or reason); no model judge is called."}


def parse_args(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    commands = {"run", "blind-export", "blind-score", "detect-export", "detect-score"}
    if not argv or argv[0] not in commands | {"-h", "--help"}:
        argv.insert(0, "run")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Build requests by default; --send enables budgeted calls")
    run.add_argument("--cases", help="Required for new runs; resume uses the saved case snapshot")
    run.add_argument("--templates", nargs="+")
    run.add_argument("--arms", help="JSON list or file; single mode, prompt_version and provider/model route")
    run.add_argument("--runs", type=int)
    run.add_argument("--seed", type=int)
    run.add_argument("--output")
    run.add_argument("--resume", metavar="DIR", help="Continue an A3 run; completed pages are skipped")
    run.add_argument("--concurrency", type=int, default=4)
    run.add_argument("--feature-route", action="store_true", help="Evaluate editor sellingPoints once per case/arm/run")
    gate = run.add_mutually_exclusive_group()
    gate.add_argument("--send", action="store_true")
    gate.add_argument("--dry-run", "--dry", action="store_true")
    run.add_argument("--max-calls", type=int)
    run.add_argument("--max-usd", type=float)
    run.add_argument("--prices")
    run.add_argument("--timeout", type=float, default=90)
    run.add_argument("--env-file", help="Optional file; only provider API keys are read, only with --send")
    for name in ("blind-export", "detect-export"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--input", required=True)
        cmd.add_argument("--output", required=True)
        cmd.add_argument("--seed", type=int, default=0)
        cmd.add_argument("--one-arm-per-sheet", action="store_true", help="Opaque bundles contain exactly one arm")
        if name == "detect-export":
            cmd.add_argument("--real-shop", required=True)
            cmd.add_argument("--per-kind", type=int, default=20)
            cmd.add_argument("--kinds", nargs="+", default=list(DETECT_KINDS))
            cmd.add_argument("--rounds", type=int, default=2)
    blind = sub.add_parser("blind-score")
    blind.add_argument("--key", required=True)
    blind.add_argument("--judge-a", required=True)
    blind.add_argument("--judge-b", required=True)
    blind.add_argument("--output", required=True)
    detect = sub.add_parser("detect-score")
    detect.add_argument("--key", required=True)
    detect.add_argument("--decisions", nargs="+", required=True)
    detect.add_argument("--output", required=True)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        if args.command == "run":
            result = asyncio.run(run_evaluation(args))
            print(json.dumps({"mode": result["mode"], "pages": len(result["pages"]), "calls": result["calls"],
                              "planned_calls": result["planned_calls"], "known_usd": result["known_usd"],
                              "unpriced_planned_calls": result["unpriced_planned_calls"],
                              "cost_warnings": result["cost_warnings"], "stopped": result["stopped"]}))
            return 2 if result["stopped"] or any(page["status"] not in {"ok", "dry_run"} for page in result["pages"]) else 0
        if args.command == "blind-export":
            result = blind_export(args.input, args.output, seed=args.seed, one_arm_per_sheet=args.one_arm_per_sheet)
        elif args.command == "blind-score":
            result = blind_score(args.key, args.judge_a, args.judge_b)
            write_json(args.output, result)
        elif args.command == "detect-export":
            result = detect_export(args.input, args.real_shop, args.output, seed=args.seed,
                                   per_kind=args.per_kind, kinds=args.kinds, rounds=args.rounds,
                                   one_arm_per_sheet=args.one_arm_per_sheet)
        else:
            result = detect_score(args.key, args.decisions)
            write_json(args.output, result)
        print(json.dumps({"command": args.command, "output": str(args.output)}, ensure_ascii=False))
        return 0
    except (ValueError, OSError, BudgetExceeded) as error:
        print(f"Evaluation stopped ({type(error).__name__}): {Redactor()(str(error))}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
