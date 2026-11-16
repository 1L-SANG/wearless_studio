"""Offline contracts for Terra outfit requests, Flash rollback and bounded fallback."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.agents import outfit_recommender as agent, vision_llm
from app.agents.gemini_image import InlineImage
from app.config import load_settings
from app.services import outfit
from conftest import FakeR2, make_settings
from test_outfit import row

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"
FIXTURES = Path(__file__).with_name("fixtures")
HASHES = {
    "outfit_recommendation_terra_v1.dev.txt": "70ac48bdf8be555fd83542954f2378b2417abd6cbdfe684d273fb90d5a62daa0",
    "outfit_recommendation_terra_v1.user.txt": "01882466e4851d6be84c8b56ac786d177aa7b703c5351c28df803db837f16838",
    "outfit_props_terra_v1.dev.txt": "48e5d4de44820ec62f028719564629753ca5e537a649dc0c48df4c96ba9560e8",
    "outfit_props_terra_v1.user.txt": "b955bcd5c2fa8f76941c31e929083705851fdb1a9fbc535c80c9ca1348d2aaac",
}


@pytest.fixture
def images(monkeypatch):
    value = [InlineImage("image/jpeg", b"product"), InlineImage("image/jpeg", b"candidate")]
    monkeypatch.setattr(agent, "_request_images", AsyncMock(return_value=value))
    return value


def request_args(kind):
    if kind == "sets":
        return ({"inner": [row("inner", "inner")], "bottom": [row("bottom", "bottom")], "footwear": []},
                {"context": {"clothingType": "outer", "subCategory": "cardigan"},
                 "fixed": {"slots": {"inner": {"itemId": None, "origin": "user"},
                                      "bottom": {"itemId": "bottom", "origin": "user"}}}})
    return {"bag": [row("bag", "bag")]}, {"context": {"clothingType": "top"}}


async def build(kind, **kwargs):
    pools, options = request_args(kind)
    builder = agent.build_request if kind == "sets" else agent.build_props_request
    return await builder(None, [], pools, **options, **kwargs)


async def recommend(kind, settings, **kwargs):
    pools, options = request_args(kind)
    call = agent.recommend if kind == "sets" else agent.recommend_props
    return await call(settings, None, [], pools, **options, **kwargs)


@pytest.mark.parametrize("name,digest", HASHES.items())
def test_experiment_prompt_files_are_copied_byte_for_byte(name, digest):
    assert sha256((PROMPTS / name).read_bytes()).hexdigest() == digest


@pytest.mark.parametrize("kind", ["sets", "props"])
def test_flash_request_body_matches_prechange_bytes(images, kind):
    request = asyncio.run(build(kind))
    body = vision_llm._gemini_body(request["prompt"], request["images"], request["schema"],
                                   "low", 4096 if kind == "sets" else 1024, request["system_prompt"])
    assert json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode() == (
        FIXTURES / f"outfit_{kind}_gemini_wire.json").read_bytes()


@pytest.mark.parametrize("kind", ["sets", "props"])
def test_terra_full_messages_schema_and_output_options_match_experiment(monkeypatch, images, kind):
    async def run():
        legacy = await build(kind)
        request = await build(kind, provider="gpt")
        _, rest = legacy["prompt"].split("\nIMAGE MANIFEST\n", 1)
        manifest, data = rest.split("\nPRODUCT DATA JSON\n", 1)
        version = "outfit_recommendation_terra_v1" if kind == "sets" else "outfit_props_terra_v1"
        developer = (PROMPTS / f"{version}.dev.txt").read_text().strip()
        user = (PROMPTS / f"{version}.user.txt").read_text().strip().replace(
            "${imageManifest}", manifest).replace("${productDataJson}", data)
        assert request["system_prompt"] == developer
        assert request["prompt"] == user
        assert request["images"] == legacy["images"]
        gpt, flash = AsyncMock(return_value={}), AsyncMock(return_value={})
        monkeypatch.setattr(agent, "_call_gpt", gpt)
        monkeypatch.setattr(agent, "_call_gemini", flash)
        settings = make_settings(openai_api_key="mock", gemini_api_key="mock")
        await recommend(kind, settings)
        args, options = gpt.await_args
        assert args[1:5] == ("gpt-6.1-terra", user, legacy["images"], request["schema"])
        assert options["reasoning_effort"] == "none"
        assert options.get("max_completion_tokens") is None  # eval.py call_once omits the cap.
        assert "thinking_level" not in options
        body = vision_llm._gpt_body(*args[1:5], reasoning_effort=options["reasoning_effort"],
                                    max_completion_tokens=options.get("max_completion_tokens"),
                                    system_prompt=options["system_prompt"])
        assert body["messages"][0] == {"role": "developer", "content": developer}
        assert body["messages"][1]["content"][0] == {"type": "text", "text": user}
        assert body["reasoning_effort"] == "none"
        assert "max_completion_tokens" not in body
        assert "maxLength" not in json.dumps(body)
        assert "x-maxLength" not in json.dumps(body)
        if kind == "sets":
            slots = body["response_format"]["json_schema"]["schema"]["properties"]["sets"]["items"]["properties"]["slots"]["properties"]
            assert slots["inner"]["enum"] == ["inner", None]
            assert slots["bottom"]["enum"] == ["bottom", None]
            assert "enum" not in slots["footwear"]
        else:
            assert body["response_format"]["json_schema"]["schema"]["properties"]["picks"]["items"]["enum"] == ["bag"]
        flash.assert_not_awaited()
        assert await build(kind) == legacy  # conversion must not mutate the Flash request.
    asyncio.run(run())


def test_schema_conversion_changes_only_nullable_enums_and_length_annotations():
    schema = {"properties": {"nullable": {"type": ["string", "null"], "enum": ["a"]},
        "already": {"type": ["string", "null"], "enum": ["a", None]},
        "required": {"type": "string", "enum": ["b"], "maxLength": 48}},
        "minItems": 1, "maxItems": 3}
    original = deepcopy(schema)
    converted = agent._gpt_schema(schema)
    assert schema == original
    assert converted == {"properties": {"nullable": {"type": ["string", "null"], "enum": ["a", None]},
        "already": {"type": ["string", "null"], "enum": ["a", None]},
        "required": {"type": "string", "enum": ["b"], "x-maxLength": 48}},
        "minItems": 1, "maxItems": 3}


@pytest.mark.parametrize("value,expected,warns", [
    (None, "gpt,gemini", False), ("gpt,gemini", "gpt,gemini", False),
    (" Gemini , GPT ", "gemini,gpt", False), ("", "gpt,gemini", True),
    ("gpt", "gpt,gemini", True), ("gemini", "gpt,gemini", True),
    ("gpt,gpt", "gpt,gemini", True), ("gemni,gpt", "gpt,gemini", True),
    ("gpt,,gemini", "gpt,gemini", True), ("gpt,gemini,claude", "gpt,gemini", True),
])
def test_outfit_order_matches_b_validation(monkeypatch, caplog, value, expected, warns):
    if value is None:
        monkeypatch.delenv("MODEL_ORDER_OUTFIT", raising=False)
    else:
        monkeypatch.setenv("MODEL_ORDER_OUTFIT", value)
    settings = load_settings()
    assert settings.model_order_outfit == expected
    warnings = [record.getMessage() for record in caplog.records if "MODEL_ORDER_OUTFIT" in record.getMessage()]
    assert warnings == (["Invalid MODEL_ORDER_OUTFIT; using gpt,gemini"] if warns else [])


@pytest.mark.parametrize("kind", ["sets", "props"])
@pytest.mark.parametrize("order,first,second", [("gpt,gemini", "gpt", "gemini"), ("gemini,gpt", "gemini", "gpt")])
def test_order_fallback_uses_separate_requests_and_records_actual_provider(monkeypatch, images, caplog, kind, order, first, second):
    async def run():
        calls = []
        async def call(provider, *args, **kwargs):
            calls.append(provider)
            if provider == first:
                raise vision_llm.VisionError("offline provider failure")
            return {"sets": [], "picks": []}
        for provider in ("gpt", "gemini"):
            async def invoke(*args, _provider=provider, **kwargs):
                return await call(_provider, *args, **kwargs)
            monkeypatch.setattr(agent, f"_call_{provider}", AsyncMock(side_effect=invoke))
        settings = replace(make_settings(openai_api_key="mock", gemini_api_key="mock"), model_order_outfit=order)
        metadata = {}
        with caplog.at_level("INFO"):
            await recommend(kind, settings, metadata=metadata)
        assert calls == [first, second]
        assert metadata["provider"] == second
        assert metadata["model"] == (settings.model_text_gpt_terra if second == "gpt" else settings.model_text_gemini_outfit)
        assert metadata["providerFallback"] is True
        assert f"provider={second} model={metadata['model']}" in caplog.text
        flash = agent._call_gemini.await_args
        legacy = await build(kind)
        assert flash.args[2:5] == (legacy["prompt"], legacy["images"], legacy["schema"])
        assert flash.kwargs["thinking_level"] == "low"
        assert flash.kwargs["max_output_tokens"] == (4096 if kind == "sets" else 1024)
        assert flash.kwargs["system_prompt"] == agent._SYSTEM
    asyncio.run(run())


@pytest.mark.parametrize("kind", ["sets", "props"])
def test_one_deadline_includes_thumbnails_and_failure_leaves_no_followup(monkeypatch, images, kind):
    async def run():
        async def slow(*args, **kwargs):
            await asyncio.sleep(1)
        gpt, flash = AsyncMock(side_effect=slow), AsyncMock(return_value={})
        monkeypatch.setattr(agent, "_call_gpt", gpt)
        monkeypatch.setattr(agent, "_call_gemini", flash)
        settings = make_settings(openai_api_key="mock", gemini_api_key="mock",
                                 outfit_timeout_seconds=.01, outfit_props_timeout_seconds=.01)
        with pytest.raises(TimeoutError):
            await recommend(kind, settings)
        gpt.assert_awaited_once()
        flash.assert_not_awaited()
        gpt.reset_mock()
        monkeypatch.setattr(agent, "_request_images", AsyncMock(side_effect=slow))
        with pytest.raises(TimeoutError):
            await recommend(kind, settings)
        gpt.assert_not_awaited()
        flash.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize("keys,provider", [
    ({"openai_api_key": "mock"}, "gpt"), ({"openai_api_key_fallback": "mock"}, "gpt"),
    ({"gemini_api_key": "mock"}, "gemini"), ({}, None),
])
def test_available_keys_control_pending_and_cache_identity(keys, provider):
    outfit.clear_cache()
    async def run():
        settings = make_settings(outfit_recommendation_enabled=True, **keys)
        items = [row("bottom", "bottom"), row("bag", "bag")]
        args = (settings, FakeR2(), {"clothingType": "top"}, {}, [(b"front", "image/png")], items)
        rec = outfit.prepare_recommendation(*args)["outfitRecommendation"]
        assert rec["status"] == ("pending" if provider else "fallback")
        if provider:
            assert rec["model"] == (settings.model_text_gpt_terra if provider == "gpt" else settings.model_text_gemini_outfit)
        fields = outfit.start_props(*args, "bag", semaphore=asyncio.Semaphore(0))
        prop = fields["outfitRecommendation"]["props"]["bag"]
        assert prop["status"] == ("pending" if provider else "fallback")
        await asyncio.sleep(0)  # Enter complete() so cancellation runs its existing finally cleanup.
        for task in list(outfit._inflight.values()):
            task.cancel()
        await asyncio.gather(*list(outfit._inflight.values()), return_exceptions=True)
        outfit.clear_cache()
    asyncio.run(run())


def test_cache_keys_follow_effective_provider_prompt_and_routing(monkeypatch):
    settings = make_settings(openai_api_key="mock", gemini_api_key="mock")
    args = ({"clothingType": "top"}, {}, [(b"front", "image/png")], ["Front"],
            [row("bottom", "bottom"), row("bag", "bag")])
    def keys(s):
        return (outfit.recommendation_key(s, *args, {"slots": {}}),
                outfit.props_key(s, *args, "bag"))
    original = keys(settings)
    rollback = keys(replace(settings, model_order_outfit="gemini,gpt"))
    assert all(a != b for a, b in zip(original, rollback))
    assert all(a != b for a, b in zip(original, keys(replace(settings, model_text_gpt_terra="gpt-other"))))
    flash_only = replace(settings, openai_api_key=None)
    assert agent.request_identity(flash_only)["model"] == "gemini-3.8-flash"
    assert agent.request_identity(flash_only)["promptVersion"] == "outfit_recommendation_v3"
    monkeypatch.setattr(agent, "PROMPT_VERSION", "terra-next")
    assert all(a != b for a, b in zip(original, keys(settings)))
    assert keys(flash_only) == keys(replace(flash_only, model_text_gpt_terra="unused"))
    props_before = keys(settings)[1]
    monkeypatch.setattr(agent, "PROPS_PROMPT_VERSION", "props-next")
    assert keys(settings)[1] != props_before


def test_null_from_terra_respects_empty_lock_and_seller_fixed_id(images):
    pools, options = request_args("sets")
    request = asyncio.run(build("sets", provider="gpt"))
    slots = {"inner": None, "bottom": "bottom", "footwear": None}
    schema = request["schema"]["properties"]["sets"]["items"]["properties"]["slots"]["properties"]
    assert slots["inner"] in schema["inner"]["enum"]
    response = [{"slots": slots, "reason": "고정한 선택을 지킨 코디예요.", "concept": "fixed-empty"}]
    assert outfit.validate_sets(response, pools, options["fixed"], require_concept=True, enforce_fixed=True) == response
    for role, wrong in (("inner", "inner"), ("bottom", None)):
        bad = deepcopy(response)
        bad[0]["slots"][role] = wrong
        with pytest.raises(outfit.OutfitSelectionError):
            outfit.validate_sets(bad, pools, options["fixed"], require_concept=True, enforce_fixed=True)


@pytest.mark.parametrize("kind", ["sets", "props"])
def test_real_service_stores_fallback_provider_and_model(monkeypatch, images, kind):
    outfit.clear_cache()
    gpt = AsyncMock(side_effect=vision_llm.VisionError("offline"))
    result = ({"sets": [{"slots": {"bottom": "bottom", "footwear": None}, "reason": "조화", "concept": "tonal"}]}
              if kind == "sets" else {"picks": ["bag"], "reason": "조화"})
    flash = AsyncMock(return_value=result)
    monkeypatch.setattr(agent, "_call_gpt", gpt)
    monkeypatch.setattr(agent, "_call_gemini", flash)
    async def run():
        settings = make_settings(openai_api_key="mock", gemini_api_key="mock", outfit_recommendation_enabled=True)
        args = (settings, FakeR2(), {"clothingType": "top"}, {}, [(b"front", "image/png")],
                [row("bottom", "bottom"), row("bag", "bag")])
        if kind == "sets":
            fields = await outfit.recommend(*args)
            rec = fields["outfitRecommendation"]
        else:
            initial = outfit.start_props(*args, "bag")
            key = initial["outfitRecommendation"]["propsContextKeys"]["bag"]
            fields = await outfit._inflight[key]
            rec = fields["outfitRecommendation"]["props"]["bag"]
        assert rec["status"] == "ready"
        assert rec["provider"] == "gemini"
        assert rec["model"] == "gemini-3.8-flash"
        assert rec["providerFallback"] is True
        assert rec["promptVersion"] == ("outfit_recommendation_v3" if kind == "sets" else "outfit_props_v1")
    asyncio.run(run())


@pytest.mark.parametrize("kind", ["sets", "props"])
def test_invalid_terra_choices_fall_back_to_flash_using_unchanged_server_validation(monkeypatch, images, kind):
    outfit.clear_cache()
    valid = ({"sets": [{"slots": {"bottom": "bottom", "footwear": None}, "reason": "조화", "concept": "tonal"}]}
             if kind == "sets" else {"picks": ["bag"], "reason": "조화"})
    invalid = deepcopy(valid)
    if kind == "sets":
        invalid["sets"][0]["slots"]["bottom"] = "fabricated"
    else:
        invalid["picks"] = ["fabricated"]
    monkeypatch.setattr(agent, "_call_gpt", AsyncMock(return_value=invalid))
    flash = AsyncMock(return_value=valid)
    monkeypatch.setattr(agent, "_call_gemini", flash)
    async def run():
        settings = make_settings(openai_api_key="mock", gemini_api_key="mock", outfit_recommendation_enabled=True)
        args = (settings, FakeR2(), {"clothingType": "top"}, {}, [(b"front", "image/png")],
                [row("bottom", "bottom"), row("bag", "bag")])
        if kind == "sets":
            result = await outfit.recommend(*args)
            rec = result["outfitRecommendation"]
        else:
            pending = outfit.start_props(*args, "bag")
            key = pending["outfitRecommendation"]["propsContextKeys"]["bag"]
            result = await outfit._inflight[key]
            rec = result["outfitRecommendation"]["props"]["bag"]
        assert rec["status"] == "ready"
        assert rec["provider"] == "gemini"
        assert rec["providerFallback"] is True
        flash.assert_awaited_once()
    asyncio.run(run())


@pytest.mark.parametrize("kind", ["sets", "props"])
def test_service_outer_timeout_keeps_actual_attempt_metadata_and_free_picks(monkeypatch, images, kind):
    outfit.clear_cache()
    async def slow(*args, **kwargs):
        await asyncio.sleep(1)
    monkeypatch.setattr(agent, "_call_gpt", AsyncMock(side_effect=slow))
    flash = AsyncMock()
    monkeypatch.setattr(agent, "_call_gemini", flash)
    async def run():
        settings = make_settings(openai_api_key="mock", gemini_api_key="mock", outfit_recommendation_enabled=True,
                                 outfit_timeout_seconds=.02, outfit_props_timeout_seconds=.02)
        args = (settings, FakeR2(), {"clothingType": "top"}, {}, [(b"front", "image/png")],
                [row("bottom", "bottom"), row("bag", "bag")])
        if kind == "sets":
            result = await outfit.recommend(*args)
            rec = result["outfitRecommendation"]
            assert rec["sets"][0]["slots"]["bottom"] == "bottom"
        else:
            pending = outfit.start_props(*args, "bag")
            key = pending["outfitRecommendation"]["propsContextKeys"]["bag"]
            result = await outfit._inflight[key]
            rec = result["outfitRecommendation"]["props"]["bag"]
            assert rec["picks"] == ["bag"]
        assert rec["status"] == "fallback"
        assert rec["provider"] == "gpt"
        assert rec["model"] == "gpt-6.1-terra"
        assert rec["providerFallback"] is False
        flash.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize("kind", ["sets", "props"])
def test_cancellation_does_not_start_flash(monkeypatch, images, kind):
    async def run():
        started = asyncio.Event()
        async def slow(*args, **kwargs):
            started.set()
            await asyncio.sleep(10)
        monkeypatch.setattr(agent, "_call_gpt", AsyncMock(side_effect=slow))
        flash = AsyncMock()
        monkeypatch.setattr(agent, "_call_gemini", flash)
        task = asyncio.create_task(recommend(kind, make_settings(openai_api_key="mock", gemini_api_key="mock")))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        flash.assert_not_awaited()
    asyncio.run(run())


def test_cache_payload_uses_actual_eligible_model_and_flash_rollback_version(monkeypatch):
    recorded = []
    canonical = outfit._canonical
    def capture(value):
        recorded.append(deepcopy(value))
        return canonical(value)
    monkeypatch.setattr(outfit, "_canonical", capture)
    args = ({"clothingType": "top"}, {}, [(b"front", "image/png")], None, [], {"slots": {}})
    settings = make_settings(openai_api_key="mock", gemini_api_key="mock")
    for order, model, prompt in (("gpt,gemini", "gpt-6.1-terra", "outfit_recommendation_terra_v1"),
                                 ("gemini,gpt", "gemini-3.8-flash", "outfit_recommendation_v3")):
        outfit.recommendation_key(replace(settings, model_order_outfit=order), *args)
        assert recorded[-1]["model"] == model
        assert recorded[-1]["promptVersion"] == prompt
    outfit.recommendation_key(replace(settings, openai_api_key=None), *args)
    assert recorded[-1]["provider"] == "gemini"
    assert recorded[-1]["providerRequests"] == [agent.request_identity(settings, provider="gemini")]


def test_old_base_cache_never_returns_props_from_another_provider_route(monkeypatch):
    outfit.clear_cache()
    async def fake_props(*args, metadata, **kwargs):
        metadata.update(provider="gemini", model="gemini-3.8-flash", promptVersion="outfit_props_v1", providerFallback=False)
        return {"picks": ["bag"], "reason": "조화"}
    monkeypatch.setattr(agent, "recommend_props", fake_props)
    async def run():
        settings_a = make_settings(openai_api_key="mock", gemini_api_key="mock", outfit_recommendation_enabled=True)
        settings_b = replace(settings_a, model_order_outfit="gemini,gpt")
        values = (FakeR2(), {"clothingType": "top"}, {}, [(b"route", "image/png")],
                  [row("bottom", "bottom"), row("bag", "bag")])
        stored_a = outfit.prepare_recommendation(settings_a, *values)
        stored_a["outfitRecommendation"]["status"] = "ready"
        base_key = stored_a["outfitRecommendation"]["contextKey"]
        # A signed/persisted base can outlive a config change; start_props retains its sets.
        pending_b = outfit.start_props(settings_b, values[0], values[1], stored_a, values[3], values[4], "bag")
        b_key = pending_b["outfitRecommendation"]["propsContextKeys"]["bag"]
        await outfit._inflight[b_key]
        assert outfit.read_recommendation(base_key)["outfitRecommendation"]["props"]["bag"]["model"] == "gemini-3.8-flash"
        current_a = outfit.start_recommendation(settings_a, *values)
        assert "bag" not in current_a["outfitRecommendation"].get("props", {})
        assert "bag" not in current_a["outfitRecommendation"].get("propsContextKeys", {})
        assert "bag" not in current_a["outfitRecommendation"].get("propsExpiresAt", {})
    asyncio.run(run())


def test_props_never_copies_another_roles_stale_route_from_stored_base(monkeypatch):
    outfit.clear_cache()
    async def fake_props(*args, metadata, **kwargs):
        metadata.update(provider="gpt", model="gpt-6.1-terra", promptVersion="outfit_props_terra_v1", providerFallback=False)
        return {"picks": ["bag"], "reason": "조화"}
    monkeypatch.setattr(agent, "recommend_props", fake_props)
    async def run():
        settings_a = make_settings(openai_api_key="mock", gemini_api_key="mock", outfit_recommendation_enabled=True)
        settings_b = replace(settings_a, model_order_outfit="gemini,gpt")
        values = (FakeR2(), {"clothingType": "top"}, {}, [(b"route", "image/png")],
                  [row("bottom", "bottom"), row("bag", "bag"), row("ring", "accessory", category="반지")])
        stored_a = outfit.prepare_recommendation(settings_a, *values)
        initial_a = outfit.start_props(settings_a, values[0], values[1], stored_a, values[3], values[4], "bag")
        done_a = await outfit._inflight[initial_a["outfitRecommendation"]["propsContextKeys"]["bag"]]
        started_b = outfit.start_props(settings_b, values[0], values[1], done_a, values[3], values[4], "accessory",
                                       semaphore=asyncio.Semaphore(0))
        rec = started_b["outfitRecommendation"]
        assert "bag" not in rec.get("props", {})
        await asyncio.sleep(0)
        task = outfit._inflight[rec["propsContextKeys"]["accessory"]]
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    asyncio.run(run())


def test_restoring_both_keys_invalidates_saved_free_fallback_and_starts_terra(monkeypatch):
    outfit.clear_cache()
    call = AsyncMock(return_value={"picks": ["bag"], "reason": "조화"})
    monkeypatch.setattr(agent, "recommend_props", call)
    async def run():
        no_keys = make_settings(outfit_recommendation_enabled=True)
        both_keys = replace(no_keys, openai_api_key="mock", gemini_api_key="mock")
        values = (FakeR2(), {"clothingType": "top"}, {}, [(b"keys", "image/png")],
                  [row("bottom", "bottom"), row("bag", "bag")])
        stored = outfit.start_props(no_keys, *values, "bag")
        before = stored["outfitRecommendation"]
        now = outfit.prepare_recommendation(both_keys, values[0], values[1], stored, values[3], values[4])
        assert now["outfitRecommendation"]["contextKey"] != before["contextKey"]
        new_key = outfit.props_key(both_keys, values[1], stored, values[3], None, values[4], "bag")
        assert new_key != before["propsContextKeys"]["bag"]
        restarted = outfit.start_props(both_keys, values[0], values[1], stored, values[3], values[4], "bag")
        assert restarted["outfitRecommendation"]["props"]["bag"]["status"] == "pending"
        await outfit._inflight[new_key]
        call.assert_awaited_once()
    asyncio.run(run())


def test_free_or_queued_recommendation_does_not_claim_a_provider_was_used(monkeypatch):
    outfit.clear_cache()
    async def run():
        values = (FakeR2(), {"clothingType": "top"}, {}, [(b"queued", "image/png")], [row("bottom", "bottom")])
        free = outfit.prepare_recommendation(make_settings(), *values)["outfitRecommendation"]
        assert free["status"] == "fallback"
        assert free["provider"] is None
        assert free["model"] is None
        settings = make_settings(openai_api_key="mock", outfit_recommendation_enabled=True, outfit_timeout_seconds=.01)
        call = AsyncMock()
        monkeypatch.setattr(agent, "recommend", call)
        pending = outfit.start_recommendation(settings, *values, semaphore=asyncio.Semaphore(0))
        finished = await outfit._inflight[pending["outfitRecommendation"]["contextKey"]]
        assert finished["outfitRecommendation"]["status"] == "fallback"
        assert finished["outfitRecommendation"]["provider"] is None
        assert finished["outfitRecommendation"]["model"] is None
        call.assert_not_awaited()
    asyncio.run(run())
