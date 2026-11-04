"""Prepare a sealed product-photo comparison; execution needs separate chat approval.

From server/: .venv/bin/python -m scripts.benchmark_product_photography
    --source ORIGINAL --output-dir NEW_FOLDER --max-calls 12 --max-usd 3

Default preparation performs local reads/writes only. After the owner approves that
specific plan, --execute --plan PLAN --approved-plan-sha256 HASH executes it once.
--sources CATEGORY_MANIFEST accepts one to four unique clothing categories.
Each category has 6 Gemini and 12 GPT calls, with a $2.19 reserve. Four independent
category plans capped at $2.75 total $11; previous spending must fit the overall approval.
No browser, DB, storage upload, retry, fallback, judge, ranking or automatic rerun.
The dollar reserve is an estimate, not an API-enforced maximum for an in-flight call.
"""

import argparse
import asyncio
import hashlib
import io
import json
import math
from pathlib import Path
import time
from unittest.mock import patch

from PIL import Image, ImageOps


MODELS = (
    "gemini-nano-banana-2.1",
    "gemini-3-pro-image",
    "gpt-image-2.5-flare",
    "gpt-image-2.5-sunburst",
)
PRESENTATIONS = ("ghost", "flat", "natural_light")
CALL_RESERVE_USD = .25
CATEGORY_RESERVES_USD = dict(zip(MODELS, (.08, .23, .06, .15)))
EXPECTED_PER_CALL_USD = dict(zip(MODELS, (.05, .15, .04, .04)))
MIME = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp", "MPO": "image/jpeg"}
EXTENSION = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def normalized_source(original):
    """Same non-generative first-frame/EXIF normalization for every provider."""
    with Image.open(io.BytesIO(original)) as image:
        if image.format not in MIME:
            raise ValueError("Use PNG, JPEG, iPhone MPO or WebP originals")
        image.seek(0)
        normalized = ImageOps.exif_transpose(image)
        normalized = normalized.convert("RGBA" if "A" in normalized.getbands() else "RGB")
        buffer = io.BytesIO()
        normalized.save(buffer, format="PNG")
        return buffer.getvalue(), list(normalized.size)


def build_plan(source=None, *, sources=None, max_calls=None, max_usd=None, profile="common"):
    """No provider/settings imports or constructors are reachable from preparation."""
    from app.agents.product_photography import render_prompt

    category_batch = sources is not None
    if category_batch and (not isinstance(sources, list) or not 1 <= len(sources) <= 4):
        raise ValueError("Supply one to four category sources")
    planned_calls = 18 * len(sources) if category_batch else 12
    required_reserve = (round(len(sources) * 3 * sum(CATEGORY_RESERVES_USD[model]
        * (2 if model.startswith("gpt-image") else 1) for model in MODELS), 6)
        if category_batch else planned_calls * CALL_RESERVE_USD)
    if (not isinstance(max_calls, int) or isinstance(max_calls, bool) or max_calls < planned_calls
            or not isinstance(max_usd, (int, float)) or isinstance(max_usd, bool)
            or not math.isfinite(max_usd) or max_usd < required_reserve):
        raise ValueError(f"Explicit max_calls >= {planned_calls} and max_usd >= {required_reserve:g} are required")
    if profile not in ("common", "model"):
        raise ValueError("Unknown prompt profile")
    if category_batch:
        categories = [item.get("clothing_type") for item in sources]
        if (source is not None or len(set(categories)) != len(categories)
                or not set(categories) <= {"top", "bottom", "outer", "dress"}):
            raise ValueError("Category sources require unique top, bottom, outer or dress types")
    else:
        sources = [{"id": "source", "clothing_type": None,
            "images": [{"role": "Front", "path": str(source)}]}]
    if len({item["id"] for item in sources}) != len(sources):
        raise ValueError("Duplicate source IDs")
    manifest = []
    for item in sources:
        if (not isinstance(item["id"], str) or not item["id"]
                or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for char in item["id"])
                or not 1 <= len(item["images"]) <= 4 or item["images"][0]["role"] != "Front"):
            raise ValueError("Each source requires a safe ID and first Front original, up to four images")
        for entry in item["images"]:
            if entry["role"] not in ("Front", "Front detail", "Back", "Back detail"):
                raise ValueError("Unknown original evidence role")
            path = Path(entry["path"]).resolve()
            original = path.read_bytes()
            with Image.open(io.BytesIO(original)) as image:
                if image.format not in MIME:
                    raise ValueError("Use PNG, JPEG or WebP originals")
                dimensions, mime = list(image.size), MIME[image.format]
                image.verify()
            provider_bytes, provider_dimensions = normalized_source(original)
            manifest.append({"source_id": item["id"], "clothing_type": item["clothing_type"],
                "role": entry["role"], "path": str(path), "sha256": sha256(original),
                "bytes": len(original), "mime": mime, "dimensions": dimensions,
                "provider_mime": "image/png", "provider_sha256": sha256(provider_bytes),
                "provider_dimensions": provider_dimensions,
                "normalization": "first frame, EXIF orientation, RGB/RGBA PNG; no resizing or generative change"})
    requests = []
    for repeat in range(1, 3 if category_batch else 2):
        for item in sources:
            evidence = [entry for entry in manifest if entry["source_id"] == item["id"]]
            for presentation in PRESENTATIONS:
                for model in MODELS:
                    if repeat == 2 and not model.startswith("gpt-image"):
                        continue
                    condition = "model" if category_batch and repeat == 2 else profile
                    prompt = render_prompt(presentation,
                        model=model if condition == "model" else None,
                        clothing_type=item["clothing_type"])
                    roles = "\n".join(f"IMAGE {index}: {entry['role']}" for index, entry in enumerate(evidence, 1))
                    prompt += ("\n\nSOURCE EVIDENCE:\n" + roles
                        + "\nIMAGE 1 Front defines the requested visible product side. "
                        "Front details clarify that side. Back evidence may clarify hidden construction "
                        "but must never copy rear pockets, seams or graphics onto the front. "
                        "Return one front product photograph, preserving the original product.")
                    requests.append({
                        "id": f"{item['id']}__{presentation}__{model}__r{repeat}",
                        "source_id": item["id"], "model": model, "repeat": repeat,
                        "prompt_profile": condition, "clothing_type": item["clothing_type"],
                        "presentation": presentation, "prompt": prompt,
                        "prompt_sha256": sha256(prompt.encode()),
                        "image_size": "1K", "aspect_ratio": "2:3",
                        "openai_quality": "medium", "openai_size": "1024x1536",
                        "per_call_reserve_usd": CATEGORY_RESERVES_USD[model] if category_batch else CALL_RESERVE_USD,
                        "expected_usd": EXPECTED_PER_CALL_USD[model],
                    })
    return {
        "schema_version": 1, "profile": profile, "source_scope": "category" if category_batch else "single",
        "input_manifest": manifest,
        "requests": requests, "planned_provider_calls": len(requests),
        "max_calls": max_calls, "max_usd": max_usd,
        "estimated_expected_usd": round(sum(row["expected_usd"] for row in requests), 6),
        "estimated_reserve_usd": round(sum(row["per_call_reserve_usd"] for row in requests), 6),
        "provider_call_counts": {"gemini": sum(not row["model"].startswith("gpt-image") for row in requests),
            "gpt": sum(row["model"].startswith("gpt-image") for row in requests)},
        "spend_guard": "estimated reserve; provider cannot guarantee an in-flight dollar ceiling",
        "availability": {model: "historical successful requests; current account access unverified"
            for model in MODELS},
        "cost_basis": {
            "nb21": "input $1.5, text+thinking $7.5, image output $30 per million tokens",
            "pro": "input $2, text+thinking $12, image output $120 per million tokens",
            "openai": "text input $5, image input $8, image output $30 per million tokens",
            "source": "Google/OpenAI official pricing and historical local usage; estimate, not invoice",
        },
        "comparison_limits": [
            "Baseline has one independent sample per model, category and presentation; no causal quality ranking",
            "Provider-native 1K outputs may differ in exact pixels and internal computation",
            "Model profile is exploratory and changes instructions; common profile isolates model selection",
            "Second GPT samples use model adapters, so they are a separate tuning condition, not identical repeats",
            "Flat is frontal planar clothing with invisible support; no forced overhead view",
        ],
        "script_sha256": sha256(Path(__file__).read_bytes()),
        "http_calls": 0, "automatic_retries": 0, "judge_calls": 0,
        "execution_authorized": False,
    }


def save_plan(plan, folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=False)
    (folder / "prompts").mkdir()
    for row in plan["requests"]:
        (folder / "prompts" / f"{row['id']}.txt").write_bytes(row["prompt"].encode())
    path = folder / "plan.json"
    path.write_bytes(json_bytes(plan))
    return path


def validate_plan(plan):
    manifest = plan["input_manifest"]
    for source in manifest:
        if sha256(Path(source["path"]).read_bytes()) != source["sha256"]:
            raise ValueError("Original source changed after preparation")
    inputs = {}
    for source in manifest:
        item = inputs.setdefault(source["source_id"], {"id": source["source_id"],
            "clothing_type": source["clothing_type"], "images": []})
        item["images"].append({"role": source["role"], "path": source["path"]})
    scope = {"sources": list(inputs.values())} if plan["source_scope"] == "category" else {"source": manifest[0]["path"]}
    expected = build_plan(**scope, max_calls=plan.get("max_calls"),
        max_usd=plan.get("max_usd"), profile=plan.get("profile"))
    if plan != expected:
        raise ValueError("Sealed request, prompt, budget or script changed; prepare a fresh plan")


def cost_usd(model, image_size, usage):
    if model not in ("gemini-nano-banana-2.1", "gemini-3-pro-image"):
        from app.agents.image_cost import estimate_cost
        cost = estimate_cost(model, image_size, usage)
        return cost.usd if cost.source == "usage" else None
    if not isinstance(usage, dict):
        return None
    keys = ("promptTokenCount", "candidatesTokenCount")
    if any(not isinstance(usage.get(key), int) or isinstance(usage[key], bool)
            or usage[key] < 0 for key in keys):
        return None
    details = usage.get("candidatesTokensDetails")
    if not isinstance(details, list) or not details:
        return None
    totals = {"IMAGE": 0, "TEXT": 0}
    for item in details:
        if (not isinstance(item, dict) or item.get("modality") not in totals
                or not isinstance(item.get("tokenCount"), int)
                or isinstance(item["tokenCount"], bool) or item["tokenCount"] < 0):
            return None
        totals[item["modality"]] += item["tokenCount"]
    thoughts = usage.get("thoughtsTokenCount", 0)
    if (not isinstance(thoughts, int) or isinstance(thoughts, bool) or thoughts < 0
            or not totals["IMAGE"] or sum(totals.values()) > usage["candidatesTokenCount"]):
        return None
    if "totalTokenCount" in usage:
        total = usage["totalTokenCount"]
        if (not isinstance(total, int) or isinstance(total, bool)
                or total != usage["promptTokenCount"] + usage["candidatesTokenCount"] + thoughts):
            return None
    text = usage["candidatesTokenCount"] - totals["IMAGE"] + thoughts
    input_rate, text_rate, image_rate = (1.5, 7.5, 30) if model == "gemini-nano-banana-2.1" else (2, 12, 120)
    return round((usage["promptTokenCount"] * input_rate + text * text_rate
        + totals["IMAGE"] * image_rate) / 1_000_000, 8)


class OneSend:
    """Stops shared-client automatic retries and key fallback before transmission."""
    def __init__(self):
        self.sent = False

    async def before_request(self, request):
        if self.sent:
            raise RuntimeError("A second HTTP send is forbidden for this case")
        self.sent = True


def make_client():
    """Called only after the sealed plan and one-run marker have been checked."""
    from scripts._env import load_env
    from app.config import load_settings
    from app.agents.gemini_image import GeminiImageClient

    load_env()
    settings = load_settings()
    if not settings.gemini_api_key or not settings.openai_api_key:
        raise ValueError("Both GEMINI_API_KEY and OPENAI_API_KEY must be configured")
    return GeminiImageClient(settings)


async def provider_generate(client, row, sources):
    from app.agents import gemini_image

    original_client = gemini_image.httpx.AsyncClient
    guard = OneSend()

    def one_request_client(*args, **kwargs):
        hooks = kwargs.pop("event_hooks", {})
        hooks["request"] = [guard.before_request, *hooks.get("request", [])]
        return original_client(*args, event_hooks=hooks, **kwargs)

    with patch.object(gemini_image.httpx, "AsyncClient", one_request_client):
        images = []
        for source in sources:
            original = Path(source["path"]).read_bytes()
            if sha256(original) != source["sha256"]:
                raise ValueError("Original source changed before transmission")
            provider_bytes, _ = normalized_source(original)
            if sha256(provider_bytes) != source["provider_sha256"]:
                raise ValueError("Normalized source changed before transmission")
            images.append(gemini_image.InlineImage(source["provider_mime"], provider_bytes))
        return await client.generate_content_image(
            row["model"], row["prompt"], images,
            row["image_size"], aspect_ratio=row["aspect_ratio"], timeout=180,
            openai_preserve_input_bytes=True, openai_quality=row["openai_quality"],
            openai_size_override=row["openai_size"],
        )


async def execute_plan(plan, folder, *, generate=None):
    validate_plan(plan)
    folder = Path(folder)
    for row in plan["requests"]:
        if (folder / "prompts" / f"{row['id']}.txt").read_bytes() != row["prompt"].encode():
            raise ValueError("Sealed prompt file changed")
    # Exclusive creation also prevents accidental repetition after an unknown timeout.
    with (folder / "execution_started.json").open("x") as file:
        json.dump({"plan_sha256": sha256(json_bytes(plan)), "started_unix": time.time()}, file)
    if generate is None:
        client = make_client()

        async def generate(row, sources):
            return await provider_generate(client, row, sources)

    (folder / "outputs").mkdir()
    (folder / "calls").mkdir()
    summary = {"status": "running", "provider_calls": 0,
        "known_usage_usd": 0.0, "unknown_billable_calls": 0,
        "results": [], "invoice_verified": False, "visual_scoring_performed": False}
    for index, row in enumerate(plan["requests"]):
        remaining_reserve = sum(item["per_call_reserve_usd"] for item in plan["requests"][index:])
        if (summary["provider_calls"] >= plan["max_calls"]
                or summary["known_usage_usd"] + remaining_reserve > plan["max_usd"] + 1e-8):
            summary["status"] = "stopped_budget_exceeded"
            break
        validate_plan(plan)
        record = {"id": row["id"], "model": row["model"],
            "prompt_sha256": row["prompt_sha256"], "started_unix": time.time()}
        (folder / "calls" / f"{row['id']}.started.json").write_bytes(json_bytes(record))
        started = time.perf_counter()
        summary["provider_calls"] += 1
        try:
            sources = [source for source in plan["input_manifest"] if source["source_id"] == row["source_id"]]
            result = await generate(row, sources)
            record["usage"] = result.usage
            cost = cost_usd(row["model"], row["image_size"], result.usage)
            record["estimated_usage_usd"] = cost
            with Image.open(io.BytesIO(result.image)) as image:
                record["dimensions"] = list(image.size)
                mime = MIME[image.format]
                image.verify()
            output = folder / "outputs" / f"{row['id']}.{EXTENSION[mime]}"
            output.write_bytes(result.image)
            record.update(output_path=str(output.resolve()), output_sha256=sha256(result.image))
            if cost is None:
                summary["unknown_billable_calls"] += 1
                summary["status"] = "stopped_unknown_cost"
            else:
                summary["known_usage_usd"] = round(summary["known_usage_usd"] + cost, 8)
                if cost > row["per_call_reserve_usd"] or summary["known_usage_usd"] > plan["max_usd"]:
                    summary["status"] = "stopped_budget_exceeded"
        except Exception as error:
            # Error messages may include provider bodies; retain only the type here.
            record["error_type"] = type(error).__name__
            summary["unknown_billable_calls"] += 1
            summary["status"] = "stopped_provider_error"
        record["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        summary["results"].append(record)
        (folder / "calls" / f"{row['id']}.result.json").write_bytes(json_bytes(record))
        (folder / "results.json").write_bytes(json_bytes(summary))
        if summary["status"] != "running":
            break
    if summary["status"] == "running" and len(summary["results"]) == len(plan["requests"]):
        summary["status"] = "completed"
    (folder / "results.json").write_bytes(json_bytes(summary))
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--sources", type=Path, help="JSON list of four category sources with ordered image roles")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--max-calls", type=int)
    parser.add_argument("--max-usd", type=float)
    parser.add_argument("--profile", choices=("common", "model"), default="common")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--approved-plan-sha256")
    args = parser.parse_args(argv)
    if args.execute:
        if not args.plan or not args.approved_plan_sha256:
            parser.error("Execution needs the separately chat-approved plan and its SHA-256")
        raw = args.plan.read_bytes()
        if sha256(raw) != args.approved_plan_sha256:
            parser.error("Approved plan SHA-256 does not match")
        result = asyncio.run(execute_plan(json.loads(raw), args.plan.parent))
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "completed" else 1
    if bool(args.source) == bool(args.sources) or not args.output_dir:
        parser.error("Preparation needs exactly one of --source/--sources and a new --output-dir")
    try:
        sources = json.loads(args.sources.read_text()) if args.sources else None
        plan = build_plan(args.source, sources=sources, max_calls=args.max_calls, max_usd=args.max_usd,
            profile=args.profile)
        path = save_plan(plan, args.output_dir)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps({"plan": str(path.resolve()), "plan_sha256": sha256(path.read_bytes()),
        "models": MODELS, "planned_provider_calls": plan["planned_provider_calls"],
        "expected_usd": plan["estimated_expected_usd"],
        "reserve_usd": plan["estimated_reserve_usd"], "http_calls": 0,
        "execution_authorized": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
