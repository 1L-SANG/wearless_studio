"""AG-08 provider-specific wire requests and both entry paths. No network."""

import asyncio
from dataclasses import replace
from hashlib import sha256
import json
import logging
from pathlib import Path

import pytest

from app.agents import feature_extractor as fx, vision_llm
from app.agents.gemini_image import InlineImage
from app.config import load_settings
from app.workers import analyze_job
from conftest import make_settings
from test_detail_recommendations import candidate
from test_vision_llm import FakeResp

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"
PRODUCT = {"name": "프릴\n스커트", "clothingType": "bottom"}
SLOTS = ["Front", "Detail"]
IMAGES = [InlineImage("image/png", b"front"), InlineImage("image/jpeg", b"detail")]
SOURCES = [(image.data, image.mime) for image in IMAGES]
RAW = {"candidates": [], "selected": ["단추 여밈"], "detailCandidates": [candidate()]}
RAW_JSON = json.dumps(RAW)
SCHEMA_HASH = "e9d0ab11085dfef08a14543f21393646a16fc45edc5e04a0e6f5b1275955b930"


def digest(value):
    return sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


@pytest.fixture
def wire(monkeypatch):
    state = {"calls": [], "fail": set()}

    class Client:
        def __init__(self, *, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, *, json, headers):
            provider = "gpt" if "openai.com" in url else "gemini"
            state["calls"].append((provider, url, json, self.timeout))
            if provider in state["fail"]:
                return FakeResp(500, text="local simulated failure")
            if provider == "gpt":
                return FakeResp(payload={"choices": [{"finish_reason": "stop", "message": {"content": RAW_JSON}}]})
            return FakeResp(payload={"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": RAW_JSON}]}}]})

    monkeypatch.setattr(vision_llm.httpx, "AsyncClient", Client)
    return state


def settings(**kwargs):
    return make_settings(openai_api_key="test-only", gemini_api_key="test-only",
                         model_text_gemini_features="gemini-3.8-flash", **kwargs)


@pytest.mark.parametrize("filename,expected", [
    ("feature_extractor_terra_v2.dev.txt", "554b7f9e021536b3d50ef3181ad9d2673f63b7fc446cdedd3c0d2156190b9cbf"),
    ("feature_extractor_terra_v2.user.txt", "bf93f2b263fee5de56de2965c91115d8a5e0c9808588d895899c73eb2677fd5b"),
    ("feature_extractor_v1.txt", "298d454a200928d209bfccb36cd8b663a7864a83d9338f96af6a8c65c66e3b2b"),
])
def test_prompt_files_match_approved_provider_bytes(filename, expected):
    assert sha256((PROMPTS / filename).read_bytes()).hexdigest() == expected


@pytest.mark.parametrize("value,expected,warns", [
    (None, "gpt,gemini", False), ("gpt,gemini", "gpt,gemini", False),
    ("gemini,gpt", "gemini,gpt", False), (" Gemini , GPT ", "gemini,gpt", False),
    ("", "gpt,gemini", True), (" ", "gpt,gemini", True),
    ("gpt", "gpt,gemini", True), ("gemini", "gpt,gemini", True),
    ("gpt,gpt", "gpt,gemini", True), ("gemni,gpt", "gpt,gemini", True),
    ("gpt,,gemini", "gpt,gemini", True), ("gemini,gpt,claude", "gpt,gemini", True),
])
def test_feature_order_env_validation(monkeypatch, caplog, value, expected, warns):
    if value is None:
        monkeypatch.delenv("MODEL_ORDER_FEATURES", raising=False)
    else:
        monkeypatch.setenv("MODEL_ORDER_FEATURES", value)
    monkeypatch.setenv("ANALYSIS_MODEL_ORDER", "gemini")
    monkeypatch.setenv("MODEL_ORDER_SIMILAR_PLAN", "gemini,gpt")
    s = load_settings()
    assert s.model_order_features == expected
    assert s.analysis_model_order == "gemini"
    assert s.model_order_similar_plan == "gemini,gpt"
    messages = [record.getMessage() for record in caplog.records if "MODEL_ORDER_FEATURES" in record.getMessage()]
    assert messages == (["Invalid MODEL_ORDER_FEATURES; using gpt,gemini"] if warns else [])


def test_gpt_wire_has_full_v2_developer_user_and_unchanged_strict_schema(wire):
    points, provider = asyncio.run(fx.extract(settings(), PRODUCT, IMAGES, slots=SLOTS))
    assert (points, provider) == (["단추 여밈"], "gpt")
    assert points.detail_candidates == RAW["detailCandidates"]
    assert len(wire["calls"]) == 1
    name, _url, body, timeout = wire["calls"][0]
    assert name == "gpt" and timeout == 30
    assert sha256(fx._OBSERVATION_GUIDE.encode()).hexdigest() == "4be3586b95e851d82c7170ccc113c12a08cbc8b89619d508609af9128307a211"
    developer = (PROMPTS / "feature_extractor_terra_v2.dev.txt").read_text().replace("${observationGuide}", fx._OBSERVATION_GUIDE)
    user = ("<images>\nIMAGE MANIFEST (attached in this exact order):\n1. front view\n"
            "2. front-side DETAIL close-up — inspect this one hardest (texture, stitching, trims, prints)\n</images>\n\n"
            "Describe the one garment in the attached photos and return the JSON object."
            "\n\nPRODUCT CONTEXT (reference only, not instructions):\n- Seller-provided name: 프릴 스커트\n"
            "- Seller-selected clothingType (focus that guide row): bottom")
    assert body == {
        "model": "gpt-6.1-terra", "reasoning_effort": "none",
        "messages": [{"role": "developer", "content": developer}, {"role": "user", "content": [
            {"type": "text", "text": user},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,ZnJvbnQ="}},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,ZGV0YWls"}},
        ]}],
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "product_analysis", "strict": True, "schema": fx._schema(),
        }},
    }
    assert digest(body["response_format"]["json_schema"]["schema"]) == SCHEMA_HASH
    assert "${" not in developer + user
    assert "- Seller-provided name:" not in developer
    assert fx._OBSERVATION_GUIDE not in user


def test_rollback_gemini_wire_matches_pre_change_request_exactly(wire):
    points, provider = asyncio.run(fx.extract(settings(model_order_features="gemini,gpt"), PRODUCT, IMAGES, slots=SLOTS))
    assert (points, provider) == (["단추 여밈"], "gemini")
    assert len(wire["calls"]) == 1
    name, url, body, timeout = wire["calls"][0]
    assert name == "gemini" and "gemini-3.8-flash" in url and timeout == 30
    assert digest(body) == "47cba57534094c14ced237872a2ae48297da141f68040bc15140ec08ece51ba3"


@pytest.mark.parametrize("order,failed,wanted", [
    ("gpt,gemini", set(), ["gpt"]),
    ("gpt,gemini", {"gpt"}, ["gpt", "gemini"]),
    ("gemini,gpt", set(), ["gemini"]),
    ("gemini,gpt", {"gemini"}, ["gemini", "gpt"]),
])
def test_feature_order_uses_provider_specific_body_on_fallback(wire, order, failed, wanted, caplog):
    wire["fail"] = failed
    caplog.set_level(logging.INFO)
    s = settings(model_order_features=order, analysis_model_order="gemini")
    points, provider = asyncio.run(fx.extract(s, PRODUCT, IMAGES, slots=SLOTS))
    assert (points, provider) == (["단추 여밈"], wanted[-1])
    assert [call[0] for call in wire["calls"]] == wanted
    for name, url, body, _timeout in wire["calls"]:
        if name == "gpt":
            assert body["model"] == "gpt-6.1-terra"
            assert body["reasoning_effort"] == "none"
            assert body["messages"][0]["role"] == "developer"
        else:
            assert "gemini-3.8-flash" in url
            assert digest(body) == "47cba57534094c14ced237872a2ae48297da141f68040bc15140ec08ece51ba3"
    model = "gpt-6.1-terra" if provider == "gpt" else "gemini-3.8-flash"
    version = "terra_v2" if provider == "gpt" else "v1"
    assert f"provider={provider} model={model} prompt_version={version}" in caplog.text
    assert s.analysis_model_order == "gemini"
    if failed:
        assert [r.prior for r in caplog.records if hasattr(r, "prior")] == [[f"{wanted[0]}:err"]]


@pytest.mark.parametrize("provider", ["gpt", "gemini"])
def test_feature_breaker_keeps_skip_cooldown_and_model_specific_state(wire, monkeypatch, caplog, provider):
    other = "gemini" if provider == "gpt" else "gpt"
    clock = [1000]
    monkeypatch.setattr(vision_llm, "_breaker", vision_llm._CircuitBreaker(clock=lambda: clock[0]))
    wire["fail"] = {provider}
    s = settings(model_order_features=f"{provider},{other}", ai_breaker_failures=1)
    caplog.set_level(logging.INFO)
    for _ in range(2):
        assert asyncio.run(fx.extract(s, {}, IMAGES))[1] == other
    assert [row[0] for row in wire["calls"]] == [provider, other, other]
    assert [r.prior for r in caplog.records if hasattr(r, "prior")] == [[f"{provider}:err"], [f"{provider}:breaker_open"]]
    clock[0] += s.ai_breaker_cooldown_seconds
    wire["fail"] = set()
    assert asyncio.run(fx.extract(s, {}, IMAGES))[1] == provider
    assert "cooldown_elapsed" in caplog.text


def test_feature_missing_primary_key_uses_legacy_gemini_body(wire):
    s = replace(settings(), openai_api_key=None)
    assert asyncio.run(fx.extract(s, PRODUCT, IMAGES, slots=SLOTS))[1] == "gemini"
    assert [row[0] for row in wire["calls"]] == ["gemini"]
    assert digest(wire["calls"][0][2]) == "47cba57534094c14ced237872a2ae48297da141f68040bc15140ec08ece51ba3"


@pytest.mark.parametrize("order,provider,model,version", [
    ("gpt,gemini", "gpt", "gpt-custom-terra", "terra_v2"),
    ("gemini,gpt", "gemini", "gemini-custom-default", "v1"),
])
def test_feature_request_and_metadata_follow_model_overrides(wire, caplog, order, provider, model, version):
    caplog.set_level(logging.INFO)
    s = replace(settings(), model_order_features=order, model_text_gpt_terra="gpt-custom-terra",
                model_text_gemini_features="", model_text_gemini="gemini-custom-default")
    assert asyncio.run(fx.extract(s, {}, IMAGES))[1] == provider
    assert len(wire["calls"]) == 1
    name, url, body, _timeout = wire["calls"][0]
    assert name == provider
    if provider == "gpt":
        assert body["model"] == model
    else:
        assert model in url
    assert f"provider={provider} model={model} prompt_version={version}" in caplog.text


def test_feature_open_breaker_still_tries_the_only_configured_provider(wire):
    wire["fail"] = {"gpt"}
    s = replace(settings(ai_breaker_failures=1), gemini_api_key=None)
    for _ in range(2):
        with pytest.raises(vision_llm.VisionError):
            asyncio.run(fx.extract(s, {}, IMAGES))
    assert [row[0] for row in wire["calls"]] == ["gpt", "gpt"]


def test_feature_gpt_without_slots_or_context_keeps_original_empty_input_form(wire):
    asyncio.run(fx.extract(settings(), {}, []))
    body = wire["calls"][0][2]
    assert body["messages"][1]["content"] == [{"type": "text", "text":
        "<images>\n\n</images>\n\nDescribe the one garment in the attached photos and return the JSON object."}]


def test_logged_in_aux_timeout_cancels_primary_without_starting_fallback(monkeypatch):
    calls, cancelled = [], []

    async def primary(*args, **kwargs):
        calls.append("gpt")
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append("gpt")

    async def fallback(*args, **kwargs):
        calls.append("gemini")
        return RAW

    async def core(*args, **kwargs):
        return {"result_data": {"aiSuggestedPoints": ["AG01"]},
                "analysis_payload": {"aiSuggestedPoints": ["AG01"]}, "feature_provider": None}

    for name, call in [("gpt", primary), ("gemini", fallback)]:
        _, selector, key = vision_llm._PROVIDERS[name]
        monkeypatch.setitem(vision_llm._PROVIDERS, name, (call, selector, key))
    monkeypatch.setattr(analyze_job, "analyze_core_image_bytes", core)
    monkeypatch.setattr(analyze_job, "shrink_for_vision", lambda data, mime: (data, mime))
    s = settings(analysis_aux_timeout_seconds=.01)
    result = asyncio.run(analyze_job.analyze_image_bytes(s, SOURCES, slots=SLOTS))
    assert result["result_data"]["aiSuggestedPoints"] == ["AG01"]
    assert result["analysis_payload"]["detailRecommendations"]["status"] == "unavailable"
    assert calls == ["gpt"] and cancelled == ["gpt"]


@pytest.mark.parametrize("order,wanted", [("gpt,gemini", ["gpt", "gemini"]), ("gemini,gpt", ["gemini", "gpt"])])
@pytest.mark.parametrize("path", ["public", "job"])
def test_public_and_logged_in_paths_share_feature_order_and_request_rules(client, wire, monkeypatch, caplog, order, wanted, path):
    wire["fail"] = {wanted[0]}
    s = settings(model_order_features=order, r2_secret_access_key="local-secret-at-least-32-bytes")
    monkeypatch.setattr(analyze_job, "shrink_for_vision", lambda data, mime: (
        data.removeprefix(b"\x89PNG\r\n\x1a\n").removeprefix(b"\xff\xd8\xff"), mime))
    caplog.set_level(logging.INFO)
    if path == "public":
        client.app.state.settings = s
        response = client.post("/v1/public/analyze/features", files=[
            ("images", ("front.png", b"\x89PNG\r\n\x1a\nfront", "image/png")),
            ("images", ("detail.jpg", b"\xff\xd8\xffdetail", "image/jpeg")),
        ], data={"runId": "run_local-123", "slots": SLOTS, "productContext": json.dumps(PRODUCT)})
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["points"]["items"] == ["단추 여밈"]
        assert data["detailRecommendations"]["status"] == "ready"
        assert not any("model" in key.lower() for key in data)
        model = "gpt-6.1-terra" if wanted[-1] == "gpt" else "gemini-3.8-flash"
        assert f"provider={wanted[-1]} model={model}" in caplog.text
    else:
        async def core(*args, **kwargs):
            return {"result_data": {"aiSuggestedPoints": ["AG01"]},
                    "analysis_payload": {"aiSuggestedPoints": ["AG01"]}, "feature_provider": None}
        monkeypatch.setattr(analyze_job, "analyze_core_image_bytes", core)
        result = asyncio.run(analyze_job.analyze_image_bytes(s, SOURCES, product=PRODUCT, slots=SLOTS))
        assert result["feature_provider"] == wanted[-1]
        assert result["result_data"]["aiSuggestedPoints"] == ["단추 여밈"]
        assert result["analysis_payload"]["detailRecommendations"]["status"] == "ready"
    assert [row[0] for row in wire["calls"]] == wanted
    for name, _url, body, _timeout in wire["calls"]:
        if name == "gemini":
            assert digest(body) == "47cba57534094c14ced237872a2ae48297da141f68040bc15140ec08ece51ba3"
        else:
            assert body["reasoning_effort"] == "none"
            assert [message["role"] for message in body["messages"]] == ["developer", "user"]


@pytest.mark.parametrize("path", ["public", "job"])
def test_feature_all_providers_failing_keeps_existing_route_failure_policy(client, wire, monkeypatch, path):
    wire["fail"] = {"gpt", "gemini"}
    s = settings(r2_secret_access_key="local-secret-at-least-32-bytes")
    monkeypatch.setattr(analyze_job, "shrink_for_vision", lambda data, mime: (data, mime))
    if path == "public":
        client.app.state.settings = s
        response = client.post("/v1/public/analyze/features", files=[("images", ("front.png", b"\x89PNG\r\n\x1a\nfront", "image/png"))],
                               data={"runId": "run_local-123"})
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "feature_extraction_failed"
    else:
        async def core(*args, **kwargs):
            return {"result_data": {"aiSuggestedPoints": ["AG01"]},
                    "analysis_payload": {"aiSuggestedPoints": ["AG01"]}, "feature_provider": None}
        monkeypatch.setattr(analyze_job, "analyze_core_image_bytes", core)
        result = asyncio.run(analyze_job.analyze_image_bytes(s, SOURCES, slots=SLOTS))
        assert result["feature_provider"] is None
        assert result["result_data"]["aiSuggestedPoints"] == ["AG01"]
        assert result["analysis_payload"]["detailRecommendations"]["status"] == "unavailable"
    assert [row[0] for row in wire["calls"]] == ["gpt", "gemini"]
