import asyncio
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import logging

import pytest

from app import public_routes
from app.agents import detail_recommendations as dr, feature_extractor, vision_llm
from app.workers import analyze_job
from conftest import make_settings
from test_detail_recommendations import candidate
from test_points_merge import resign

PNG = b"\x89PNG\r\n\x1a\nfront-bytes"
RUN_ID = "run_test-123"
SECRET = "points-test-secret-at-least-32-bytes"
FILES = [("images", ("front.png", PNG, "image/png"))]
DISTRIBUTED = {
    "product": {"clothingType": "top"},
    "analysis": {"aiSuggestedPoints": ["골지 짜임"], "fit": "regular"},
    "intermediate": {"styleTags": ["daily"], "swatchSuggestions": []},
}


@pytest.fixture()
def agents(monkeypatch, client):
    client.app.state.settings = replace(client.app.state.settings, r2_secret_access_key=SECRET)
    state = {"points": ["세로 골지 짜임"], "details": [candidate()], "feature_calls": 0}

    async def analyst(settings, product, images, *, slots=None):
        state["analysis_product"] = product
        return deepcopy(DISTRIBUTED), "gemini"

    async def extract(settings, product, images, *, slots=None):
        state["feature_calls"] += 1
        state["feature_product"] = product
        state["feature_slots"] = slots
        if state.get("error"):
            raise state["error"]
        if state.get("delay"):
            await asyncio.sleep(state["delay"])
        return feature_extractor.FeaturePoints(state["points"], state["details"]), "gemini"

    async def consistency(*args, **kwargs):
        return None

    monkeypatch.setattr(analyze_job.product_analyst, "analyze", analyst)
    monkeypatch.setattr(feature_extractor, "extract", extract)
    monkeypatch.setattr(analyze_job, "_judge_input_consistency", consistency)
    return state


def post_features(client, **kwargs):
    return client.post("/v1/public/analyze/features", files=kwargs.pop("files", FILES),
                       data=kwargs.pop("data", {"runId": RUN_ID, "slots": "Front"}), **kwargs)


def tickets(early=None, late=None, *, other_run_id=RUN_ID):
    from app.agents import points_merge as pm
    return {"runId": RUN_ID,
            "pointsTicket": pm.issue_ticket(pm.POINTS_PURPOSE, RUN_ID, ["골지 짜임"] if early is None else early, SECRET),
            "featureTicket": pm.issue_ticket(pm.FEATURE_PURPOSE, other_run_id, ["세로 골지 짜임"] if late is None else late, SECRET)}


def test_core_returns_ag01_points_ticket_without_ag08_or_details(client, agents):
    from app.agents import points_merge as pm
    response = client.post("/v1/public/analyze", files=FILES, data={"parts": "core", "runId": RUN_ID})
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert agents["feature_calls"] == 0
    assert data["aiSuggestedPoints"] == ["골지 짜임"]
    assert "detailRecommendations" not in data and "detailRecommendationsHandoff" not in data
    assert pm.verify_ticket(data["pointsTicket"], SECRET, pm.POINTS_PURPOSE, RUN_ID) == ["골지 짜임"]
    assert "__confirmedGptProductEvidenceInputBinding" not in data
    assert analyze_job.product_evidence_contract.INTERNAL_BINDING_KEY in agents["analysis_product"]


@pytest.mark.parametrize("data,code", [
    ({"parts": "features", "runId": RUN_ID}, "invalid_parts"),
    ({"parts": "core"}, "invalid_run_id"),
    ({"parts": "core", "runId": "short"}, "invalid_run_id"),
    ({"parts": "core", "runId": "x" * 65}, "invalid_run_id"),
    ({"parts": "core", "runId": "bad space"}, "invalid_run_id"),
    ({"parts": "core", "runId": "abcdefgh\n"}, "invalid_run_id"),
])
def test_core_rejects_invalid_parts_or_run_id(client, agents, data, code):
    response = client.post("/v1/public/analyze", files=FILES, data=data)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == code
    assert agents["feature_calls"] == 0


def test_legacy_analyze_still_uses_features_and_signed_details(client, agents):
    response = client.post("/v1/public/analyze", files=FILES)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert agents["feature_calls"] == 1
    assert data["aiSuggestedPoints"] == ["세로 골지 짜임"]
    assert "pointsTicket" not in data
    assert data["detailRecommendations"]["status"] == "ready"
    assert dr.verify_handoff(data["detailRecommendationsHandoff"], SECRET)["status"] == "ready"


@pytest.mark.parametrize("points,details,point_status,detail_status", [
    (["세로 골지 짜임"], [candidate()], "ready", "ready"),
    ([], [candidate()], "empty", "ready"),
    (["세로 골지 짜임"], None, "ready", "unavailable"),
    (["세로 골지 짜임"], [{"broken": True}], "ready", "unavailable"),
    ([], [], "empty", "ready"),
])
def test_features_states_are_independent_and_tickets_are_valid(client, agents, points, details, point_status, detail_status):
    from app.agents import points_merge as pm
    agents.update(points=points, details=details)
    response = post_features(client, data={"runId": RUN_ID, "slots": "Front", "attempt": "2", "productContext": '{"name":"니트"}'})
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert set(data) == {"points", "detailRecommendations", "detailRecommendationsHandoff", "featureTicket"}
    assert data["points"] == {"status": point_status, "items": points}
    assert data["detailRecommendations"]["status"] == detail_status
    contract = dr.verify_handoff(data["detailRecommendationsHandoff"], SECRET)
    assert contract["status"] == detail_status
    assert contract["binding"][0]["sourceSha256"] == sha256(PNG).hexdigest()
    assert pm.verify_ticket(data["featureTicket"], SECRET, pm.FEATURE_PURPOSE, RUN_ID) == points
    assert agents["feature_product"] == {"name": "니트"}
    assert agents["feature_slots"] == ["Front"]


@pytest.mark.parametrize("data,code", [
    ({}, "invalid_run_id"), ({"runId": "short"}, "invalid_run_id"),
    ({"runId": RUN_ID, "slots": "Side"}, "invalid_slots"),
    ({"runId": RUN_ID, "slots": ["Front", "Back"]}, "invalid_slots"),
    ({"runId": RUN_ID, "attempt": "0"}, "invalid_attempt"),
    ({"runId": RUN_ID, "attempt": "4"}, "invalid_attempt"),
    ({"runId": RUN_ID, "attempt": "1.5"}, "invalid_attempt"),
])
def test_features_rejects_invalid_fields(client, agents, data, code):
    response = post_features(client, data=data)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == code
    assert agents["feature_calls"] == 0


@pytest.mark.parametrize("files,code", [
    (FILES * 5, "invalid_image_count"),
    ([("images", ("fake.txt", b"text", "text/plain"))], "unsupported_type"),
    ([("images", ("fake.jpg", PNG, "image/jpeg"))], "invalid_image_content"),
    ([("images", ("empty.png", b"", "image/png"))], "empty_file"),
])
def test_features_reuses_photo_validation(client, agents, files, code):
    response = post_features(client, files=files, data={"runId": RUN_ID})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == code
    assert agents["feature_calls"] == 0


def test_features_missing_photos_uses_existing_validation_response(client, agents):
    response = client.post("/v1/public/analyze/features", data={"runId": RUN_ID})
    assert response.status_code == 422
    assert agents["feature_calls"] == 0


def test_features_per_photo_and_total_size_limits(client, agents, monkeypatch):
    monkeypatch.setattr(public_routes, "MAX_UPLOAD_BYTES", 32)
    monkeypatch.setattr(public_routes, "MAX_PUBLIC_REQUEST_BYTES", 10000)
    over = post_features(client, files=[("images", ("large.png", PNG + b"x" * 32, "image/png"))])
    assert over.status_code == 400 and over.json()["error"]["code"] == "file_too_large"
    monkeypatch.setattr(public_routes, "MAX_PUBLIC_REQUEST_BYTES", len(PNG) * 2 - 1)
    total = post_features(client, files=FILES * 2, data={"runId": RUN_ID})
    assert total.status_code == 413 and total.json()["error"]["code"] == "request_too_large"


def test_features_total_bytes_without_content_length(client, agents, monkeypatch):
    import io
    from fastapi import HTTPException, UploadFile
    from starlette.datastructures import Headers
    monkeypatch.setattr(public_routes, "MAX_PUBLIC_REQUEST_BYTES", len(PNG) * 2 - 1)
    uploads = [UploadFile(io.BytesIO(PNG), headers=Headers({"content-type": "image/png"})) for _ in range(2)]
    with pytest.raises(HTTPException) as error:
        asyncio.run(public_routes._read_analysis_inputs(uploads, None, None))
    assert error.value.status_code == 413
    assert error.value.detail["code"] == "request_too_large"


@pytest.mark.parametrize("context", ["not-json", "[]", "x" * 4097])
def test_features_bad_context_keeps_existing_empty_product_fallback(client, agents, context):
    response = post_features(client, data={"runId": RUN_ID, "productContext": context})
    assert response.status_code == 200, response.text
    assert agents["feature_product"] == {}


def test_features_has_no_aux_timeout(client, agents):
    client.app.state.settings = replace(client.app.state.settings, analysis_aux_timeout_seconds=.001)
    agents["delay"] = .02
    response = post_features(client)
    assert response.status_code == 200
    assert response.json()["data"]["points"]["items"] == ["세로 골지 짜임"]


def test_features_vision_failure_is_retryable_502(client, agents):
    agents["error"] = vision_llm.VisionError("private provider detail")
    response = post_features(client)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "feature_extraction_failed"
    assert "private provider detail" not in response.text


def test_features_gemini_failure_does_not_fall_back_to_gpt(client, monkeypatch):
    settings = replace(client.app.state.settings, r2_secret_access_key=SECRET,
                       analysis_model_order="gemini,gpt", analysis_timeout_seconds=17)
    client.app.state.settings = settings
    calls = []

    async def gemini(settings, model, prompt, images, schema, timeout, **kwargs):
        calls.append("gemini")
        assert timeout == 17
        raise vision_llm.VisionError("Gemini failed")

    async def gpt(*args, **kwargs):
        calls.append("gpt")
        return {"selected": ["단추 여밈"], "detailCandidates": []}

    monkeypatch.setitem(vision_llm._PROVIDERS, "gemini", (gemini, lambda s: s.model_text_gemini, lambda s: True))
    monkeypatch.setitem(vision_llm._PROVIDERS, "gpt", (gpt, lambda s: s.model_text, lambda s: True))
    response = post_features(client)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "feature_extraction_failed"
    assert calls == ["gemini"]
    assert settings.analysis_model_order == "gemini,gpt"


@pytest.mark.parametrize("order,want_calls", [("gemini,gpt", ["gemini", "gpt"]), ("gpt,gemini", ["gpt"])])
@pytest.mark.parametrize("path", ["public", "job"])
def test_legacy_features_keep_configured_provider_order(client, monkeypatch, order, want_calls, path):
    settings = replace(client.app.state.settings, r2_secret_access_key=SECRET,
                       analysis_model_order=order, input_consistency="off")
    client.app.state.settings = settings
    calls = []

    async def analyst(*args, **kwargs):
        return deepcopy(DISTRIBUTED), "gemini"

    async def gemini(*args, **kwargs):
        calls.append("gemini")
        raise vision_llm.VisionError("Gemini failed")

    async def gpt(*args, **kwargs):
        calls.append("gpt")
        return {"selected": ["단추 여밈"], "detailCandidates": []}

    monkeypatch.setattr(analyze_job.product_analyst, "analyze", analyst)
    monkeypatch.setitem(vision_llm._PROVIDERS, "gemini", (gemini, lambda s: s.model_text_gemini, lambda s: True))
    monkeypatch.setitem(vision_llm._PROVIDERS, "gpt", (gpt, lambda s: s.model_text, lambda s: True))
    if path == "public":
        response = client.post("/v1/public/analyze", files=FILES)
        assert response.status_code == 200, response.text
        data = response.json()["data"]
    else:
        result = asyncio.run(analyze_job.analyze_image_bytes(
            settings, [(PNG, "image/png")], slots=["Front"], persist_confirmed_evidence=True))
        assert result["feature_provider"] == "gpt"
        data = result["result_data"]
    assert data["aiSuggestedPoints"] == ["단추 여밈"]
    assert calls == want_calls
    assert settings.analysis_model_order == order


@pytest.mark.parametrize("path,data", [("/v1/public/analyze", {"parts": "core", "runId": RUN_ID}),
                                        ("/v1/public/analyze/features", {"runId": RUN_ID})])
def test_ticket_sealing_failure_is_retryable_503(client, agents, path, data):
    client.app.state.settings = replace(client.app.state.settings, r2_secret_access_key=None)
    response = client.post(path, files=FILES, data=data)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "analysis_handoff_unavailable"


def test_detail_sealing_failure_is_retryable_503(client, agents, monkeypatch):
    def fail(*args, **kwargs):
        raise dr.DetailRecommendationError("cannot seal")
    monkeypatch.setattr(dr, "issue_handoff", fail)
    response = post_features(client)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "analysis_handoff_unavailable"


@pytest.mark.parametrize("path,semaphore_name,limiter_name,data", [
    ("/v1/public/analyze", "_analysis_semaphore", "public_analysis_limiter", {"parts": "core", "runId": RUN_ID}),
    ("/v1/public/analyze/features", "_feature_semaphore", "public_feature_limiter", {"runId": RUN_ID}),
])
def test_busy_requests_do_not_spend_rate_limit(client, agents, monkeypatch, path, semaphore_name, limiter_name, data):
    semaphore = asyncio.Semaphore(0)
    monkeypatch.setattr(public_routes, semaphore_name, semaphore, raising=False)
    limiter = public_routes.PublicAnalysisRateLimiter(hourly_limit=1, daily_limit=1)
    setattr(client.app.state, limiter_name, limiter)
    response = client.post(path, files=FILES, data=data)
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "analysis_busy"
    assert limiter._hits == {}
    semaphore.release()
    assert client.post(path, files=FILES, data=data).status_code == 200
    exhausted = client.post(path, files=FILES, data=data)
    assert exhausted.status_code == 429
    assert exhausted.json()["error"]["code"] == "rate_limited"
    assert not semaphore.locked()


@pytest.mark.parametrize("kind", ["feature", "merge"])
def test_anonymous_and_authenticated_limits_are_separate(client, agents, make_token, kind):
    setattr(client.app.state, f"public_{kind}_limiter", public_routes.PublicAnalysisRateLimiter(1, 1))
    setattr(client.app.state, f"user_{kind}_limiter", public_routes.PublicAnalysisRateLimiter(1, 1))
    if kind == "feature":
        send = lambda headers: post_features(client, headers=headers)
    else:
        send = lambda headers: client.post("/v1/public/analyze/points-merge", json=tickets(late=[]), headers=headers)
    assert send({}).status_code == 200
    assert send({}).json()["error"]["code"] == "rate_limited"
    headers = {"Authorization": f"Bearer {make_token()}"}
    assert send(headers).status_code == 200
    assert send(headers).json()["error"]["code"] == "rate_limited"
    assert send({"Authorization": f"Bearer {make_token(sub='user-2')}"}).status_code == 200
    assert list(getattr(client.app.state, f"user_{kind}_limiter")._hits) == ["user:user-1", "user:user-2"]


@pytest.mark.parametrize("early,late,want", [([], ["단추 여밈"], [{"late": 0, "action": "new", "early": None}]),
                                             (["골지 짜임"], [], [])])
def test_merge_empty_lists_return_without_model(client, agents, monkeypatch, early, late, want):
    async def forbidden(*args, **kwargs):
        pytest.fail("empty points reached model")
    monkeypatch.setattr(vision_llm, "analyze_with_fallback", forbidden)
    response = client.post("/v1/public/analyze/points-merge", json=tickets(early, late))
    assert response.status_code == 200
    assert response.json() == {"data": {"decisions": want}}


@pytest.mark.parametrize("change", ["run_mismatch", "purpose", "tamper", "missing", "bad_body"])
def test_merge_invalid_tickets_are_400(client, agents, change):
    body = tickets(other_run_id="another-run" if change == "run_mismatch" else RUN_ID)
    if change == "purpose":
        body["featureTicket"] = body["pointsTicket"]
    elif change == "tamper":
        body["pointsTicket"]["points"] = ["위조 문구"]
    elif change == "missing":
        del body["pointsTicket"]
    elif change == "bad_body":
        body = []
    response = client.post("/v1/public/analyze/points-merge", json=body)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_points_ticket"


@pytest.mark.parametrize("ticket_key", ["pointsTicket", "featureTicket"])
@pytest.mark.parametrize("points", [["문구"] * 9, ["가" * 81]])
def test_merge_rejects_correctly_signed_oversized_tickets(client, agents, monkeypatch, ticket_key, points):
    async def model(*args, **kwargs):
        late_count = len(points) if ticket_key == "featureTicket" else 1
        return {"decisions": [{"late": i, "action": "new", "early": -1} for i in range(late_count)]}, "gemini"

    monkeypatch.setattr(vision_llm, "analyze_with_fallback", model)
    body = tickets()
    body[ticket_key]["points"] = points
    resign(body[ticket_key])
    response = client.post("/v1/public/analyze/points-merge", json=body)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_points_ticket"


@pytest.mark.parametrize("raw", [None, {}, {"decisions": []}, {"decisions": [{"late": 2, "action": "refine", "early": 0}]},
                                        {"decisions": [{"late": 0, "action": "refine", "early": 2}]},
                                        {"decisions": [{"late": 0, "action": "new", "early": -1}] * 2}])
def test_merge_model_failures_and_invalid_numbers_are_retryable_503(client, agents, monkeypatch, raw):
    async def fake(*args, **kwargs):
        if raw is None:
            raise vision_llm.VisionError("provider private text")
        return raw, "gemini"
    monkeypatch.setattr(vision_llm, "analyze_with_fallback", fake)
    response = client.post("/v1/public/analyze/points-merge", json=tickets())
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "points_merge_unavailable"


def test_merge_success_and_logs_hide_phrase_and_ticket_content(client, agents, monkeypatch, caplog):
    async def fake(*args, **kwargs):
        return {"decisions": [{"late": 0, "action": "refine", "early": 0}]}, "gemini"
    monkeypatch.setattr(vision_llm, "analyze_with_fallback", fake)
    with caplog.at_level(logging.INFO, logger="wearless.public_analysis"):
        features = post_features(client)
        merged = client.post("/v1/public/analyze/points-merge", json=tickets())
    assert features.status_code == merged.status_code == 200
    assert merged.json()["data"]["decisions"] == [{"late": 0, "action": "refine", "early": 0}]
    log = "\n".join(record.getMessage() for record in caplog.records if record.name == "wearless.public_analysis")
    assert sha256(RUN_ID.encode()).hexdigest()[:12] in log
    assert "attempt=1" in log and "provider=gemini" in log and "model=" in log
    assert "refine=1" in log and "new=0" in log
    assert RUN_ID not in log and SECRET not in log and "골지" not in log
    assert features.json()["data"]["featureTicket"]["signature"] not in log


@pytest.mark.parametrize("path", ["/v1/public/analyze/features", "/v1/public/analyze/points-merge"])
def test_new_routes_receive_existing_cors_rules(client, path):
    response = client.options(path, headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST",
                                            "Access-Control-Request-Headers": "Authorization,Content-Type"})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_core_keeps_real_product_evidence_handoff(client, agents, monkeypatch):
    from app.agents import product_evidence_contract as pec
    from test_product_evidence_contract import _legacy_contract
    contract = _legacy_contract()
    async def analyst(*args, **kwargs):
        result = deepcopy(DISTRIBUTED)
        result["analysis"][pec.PERSISTED_KEY] = contract
        return result, "gemini"
    monkeypatch.setattr(analyze_job.product_analyst, "analyze", analyst)
    response = client.post("/v1/public/analyze", files=FILES, data={"parts": "core", "runId": RUN_ID})
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert pec.PERSISTED_KEY not in data
    assert pec.verify_handoff(data[pec.HANDOFF_KEY], SECRET) == contract
    assert "detailRecommendations" not in data


@pytest.mark.parametrize("name,hourly,daily", [
    ("public_feature_limiter", 10, 30), ("user_feature_limiter", 20, 60),
    ("public_merge_limiter", 20, 60), ("user_merge_limiter", 40, 120),
])
def test_new_limiter_hourly_and_daily_budgets(client, name, hourly, daily):
    limiter = getattr(client.app.state, name)
    for batch in range(daily // hourly):
        now = batch * 3601
        assert all(limiter.allow("test-key", now=now) for _ in range(hourly))
        assert limiter.allow("test-key", now=now) is False
    assert limiter.allow("test-key", now=daily // hourly * 3601) is False
    assert limiter.allow("test-key", now=86401) is True


def test_core_vision_failure_releases_slot(client, agents, monkeypatch):
    semaphore = asyncio.Semaphore(1)
    monkeypatch.setattr(public_routes, "_analysis_semaphore", semaphore)
    async def fail(*args, **kwargs):
        raise vision_llm.VisionError("private provider detail")
    monkeypatch.setattr(analyze_job.product_analyst, "analyze", fail)
    response = client.post("/v1/public/analyze", files=FILES, data={"parts": "core", "runId": RUN_ID})
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "analysis_failed"
    assert not semaphore.locked()


def test_feature_failure_releases_slot(client, agents, monkeypatch):
    semaphore = asyncio.Semaphore(1)
    monkeypatch.setattr(public_routes, "_feature_semaphore", semaphore)
    agents["error"] = vision_llm.VisionError("provider failed")
    assert post_features(client).status_code == 502
    assert not semaphore.locked()


def test_feature_concurrency_is_independent_of_core(client, agents, monkeypatch):
    monkeypatch.setattr(public_routes, "_analysis_semaphore", asyncio.Semaphore(0))
    assert post_features(client).status_code == 200


def test_feature_and_merge_invalid_bearer_use_ip_limiter(client, agents):
    client.app.state.public_feature_limiter = public_routes.PublicAnalysisRateLimiter(0, 0)
    client.app.state.public_merge_limiter = public_routes.PublicAnalysisRateLimiter(0, 0)
    headers = {"Authorization": "Bearer invalid"}
    assert post_features(client, headers=headers).json()["error"]["code"] == "rate_limited"
    assert client.post("/v1/public/analyze/points-merge", headers=headers, json=tickets(late=[])).json()["error"]["code"] == "rate_limited"


def test_explicit_empty_parts_is_invalid_instead_of_legacy_analysis(client, agents):
    response = client.post("/v1/public/analyze", files=FILES, data={"parts": "", "runId": RUN_ID})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_parts"
    assert agents["feature_calls"] == 0
