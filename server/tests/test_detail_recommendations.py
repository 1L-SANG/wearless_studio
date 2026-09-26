import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from io import BytesIO

import pytest
from PIL import Image

from app.agents import detail_recommendations as dr

SOURCES = [(b"front-original", "image/png"), (b"back-original", "image/png")]
SLOTS = ["Front", "Back"]
SECRET = "detail-test-secret-at-least-32-bytes"


def candidate(**changes):
    return {
        "kind": "closure", "sourceIndex": 0,
        "region": {"x": .2, "y": .1, "w": .5, "h": .6},
        "photoUse": "standalone", "visibility": "clear", "rank": 1,
        "standaloneValue": "construction",
        "label": "단추 여밈", "reason": "단추와 짜임을 함께 확인할 수 있어요",
        "informationGroup": "front_closure", "featurePoints": ["단추 여밈"],
        **changes,
    }


def contract(rows=None):
    return dr.build_contract([candidate()] if rows is None else rows, SOURCES, SLOTS)


def test_empty_ready_differs_from_missing_or_invalid_model_evidence():
    assert contract([])["status"] == "ready"
    assert dr.build_contract(None, SOURCES, SLOTS)["status"] == "unavailable"
    assert contract([candidate(region={"x": 0, "y": 0, "w": 2, "h": .5})])["status"] == "unavailable"


def test_source_bound_stable_ids_and_back_direction():
    value = contract([candidate(sourceIndex=1)])
    target = value["candidates"][0]
    assert target["direction"] == "back" and target["sourceSlot"] == "Back"
    assert target["id"] == contract([candidate(sourceIndex=1)])["candidates"][0]["id"]
    assert dr.resolve_target({dr.PERSISTED_KEY: value}, target["id"], source_images=SOURCES, slots=SLOTS, direction="back") == target
    for images, slots, direction in [(SOURCES[::-1], SLOTS, "back"), (SOURCES, SLOTS, "front")]:
        with pytest.raises(dr.DetailRecommendationError):
            dr.resolve_target({dr.PERSISTED_KEY: value}, target["id"], source_images=images, slots=slots, direction=direction)


def test_absent_id_is_legacy_but_unknown_or_empty_id_is_never_fallback():
    assert dr.resolve_target({}, None, source_images=[], slots=[], direction="front") is None
    for target_id in ["unknown", "", 42]:
        with pytest.raises(dr.DetailRecommendationError):
            dr.resolve_target({dr.PERSISTED_KEY: contract()}, target_id, source_images=SOURCES, slots=SLOTS, direction="front")


@pytest.mark.parametrize("patch", [{"kind": "free_prompt"}, {"sourceIndex": True}, {"rank": 0}, {"photoUse": "yes"}, {"region": {"x": float('nan'), "y": 0, "w": .4, "h": .4}}])
def test_invalid_candidates_cannot_become_recommendations(patch):
    assert contract([candidate(**patch)])["candidates"] == []


def test_context_and_uncertainty_are_retained_without_promoting_them():
    value = contract([candidate(photoUse="context"), candidate(kind="fabric", visibility="uncertain")])
    assert [row["photoUse"] for row in value["candidates"]] == ["context", "standalone"]
    assert value["candidates"][1]["visibility"] == "uncertain"


def test_appearance_only_cannot_be_automatically_promoted_to_its_own_photo():
    item = contract([candidate(standaloneValue="appearance_only")])["candidates"][0]
    assert item["photoUse"] == "context"
    assert item["standaloneValue"] == "appearance_only"


def test_material_value_does_not_blacklist_lace_or_use_pixel_size():
    item = contract([candidate(kind="fabric", label="레이스 원단", standaloneValue="material", region={"x": .2, "y": .2, "w": .12, "h": .12})])["candidates"][0]
    assert item["photoUse"] == "standalone"


def test_handoff_rejects_forged_expired_or_cross_protocol_payload():
    value = contract()
    signed = dr.issue_handoff(value, SECRET, now=1000)
    assert dr.verify_handoff(signed, SECRET, now=1001) == value
    changed = deepcopy(signed)
    changed["contract"]["candidates"][0]["region"]["x"] = .1
    for handoff, now in [(changed, 1001), (signed, signed["expiresAt"] + 1), ({**signed, "purpose": "product-evidence"}, 1001)]:
        with pytest.raises(dr.DetailRecommendationError):
            dr.verify_handoff(handoff, SECRET, now=now)
    assert "binding" not in dr.public_summary(value)


def _through_browser(value):
    """서버 응답이 브라우저를 거쳐 돌아온 모양. JSON.stringify 는 0.0·1.0 을 0·1 로 쓴다 —
    파이썬은 그걸 int 로 읽는다. 다른 실수는 양쪽 다 최단 표기라 값이 그대로 돌아온다."""
    def js(v):
        if isinstance(v, float) and v.is_integer():
            return int(v)
        if isinstance(v, dict):
            return {k: js(x) for k, x in v.items()}
        if isinstance(v, list):
            return [js(x) for x in v]
        return v
    return js(json.loads(json.dumps(value)))


@pytest.mark.parametrize("region", [
    {"x": 0.0, "y": .3, "w": .4, "h": .2},    # 왼쪽 끝에 붙은 영역
    {"x": 0.0, "y": .5, "w": 1.0, "h": .5},   # 전체 폭(밑단·단)
])
def test_handoff_survives_browser_round_trip_with_whole_number_coordinates(region):
    """2026-09-26 prod: 좌표가 0.0/1.0 인 후보 하나로 서명이 어긋나 승격이 전부 400 이었다.

    서명·후보 id 는 파이썬 canonical JSON 위에서 계산되는데, 브라우저가 0.0 을 0 으로 바꿔
    보내면 같은 값인데도 글자가 달라졌다. 파이썬끼리만 왕복하던 기존 테스트는 못 잡았다.
    """
    value = contract([candidate(region=region)])
    signed = dr.issue_handoff(value, SECRET, now=1000)
    returned = dr.verify_handoff(_through_browser(signed), SECRET, now=1001)
    assert [c["id"] for c in returned["candidates"]] == [c["id"] for c in value["candidates"]]
    # 저장 뒤 다시 읽는 경로(GET analysis·resolve_target)도 같은 계약으로 통과해야 한다.
    assert dr.validate_persisted(_through_browser(returned)) == returned


def test_small_exif_rotated_source_is_normalized_before_analysis():
    from app.workers.analyze_job import shrink_for_vision
    image = Image.new("RGB", (60, 30), "red")
    exif = image.getexif()
    exif[274] = 6
    out = BytesIO()
    image.save(out, format="JPEG", exif=exif)
    data, _ = shrink_for_vision(out.getvalue(), "image/jpeg")
    with Image.open(BytesIO(data)) as analyzed:
        assert analyzed.size == (30, 60)
        assert analyzed.getexif().get(274, 1) == 1


@pytest.mark.parametrize("drift", [False, True])
def test_public_handoff_is_promoted_only_against_actual_uploaded_bytes(client, make_token, monkeypatch, drift):
    from app import public_routes, routes
    from conftest import auth_headers, patch_route_db
    patch_route_db(monkeypatch, routes)
    client.app.state.settings = replace(client.app.state.settings, r2_secret_access_key=SECRET)
    uploaded = b"\x89PNG\r\n\x1a\noriginal"
    value = dr.build_contract([candidate()], [(uploaded, "image/png")], ["Front"])

    async def fake_analyze(*args, **kwargs):
        return {"result_data": {dr.PERSISTED_KEY: dr.public_summary(value)}, "analysis_payload": {dr.PERSISTED_KEY: value}}

    monkeypatch.setattr(public_routes, "analyze_image_bytes", fake_analyze)
    response = client.post("/v1/public/analyze", files=[("images", ("front.png", uploaded, "image/png"))], data={"slots": ["Front"]})
    assert response.status_code == 200, response.text
    handoff = response.json()["data"][dr.HANDOFF_KEY]
    assert dr.verify_handoff(handoff, SECRET) == value

    async def fake_project(*args):
        return {"id": "p1"}

    async def fake_product(*args):
        return {"colors": [{"isBase": True, "images": [{"slot": "Front", "id": "asset-front"}]}]}

    async def fake_asset(*args):
        return {"r2_key": "source/front.png", "mime_type": "image/png"}

    saved = []

    async def fake_save(conn, project_id, artifact):
        saved.append(artifact)

    monkeypatch.setattr(routes.repo, "get_project", fake_project)
    monkeypatch.setattr(routes.repo, "get_product", fake_product)
    monkeypatch.setattr(routes.repo, "get_asset_for_user", fake_asset)
    monkeypatch.setattr(routes.repo, "save_detail_recommendations", fake_save, raising=False)
    client.app.state.r2 = type("R2", (), {"get_bytes": staticmethod(lambda key: b"changed" if drift else uploaded)})()
    response = client.post("/v1/projects/p1/analysis/detail-recommendations:promote", headers=auth_headers(make_token), json=handoff)
    assert response.status_code == (400 if drift else 200), response.text
    assert saved == ([] if drift else [value])


def test_forged_detail_handoff_never_reaches_database(client, make_token):
    from conftest import auth_headers
    client.app.state.settings = replace(client.app.state.settings, r2_secret_access_key=SECRET)
    handoff = dr.issue_handoff(contract(), SECRET)
    handoff["contract"]["candidates"][0]["kind"] = "pocket"
    response = client.post("/v1/projects/p1/analysis/detail-recommendations:promote", headers=auth_headers(make_token), json=handoff)
    assert response.status_code == 400


@pytest.mark.parametrize("details,status,count", [(None, "unavailable", 0), ([], "ready", 0), ([candidate()], "ready", 1)])
def test_analysis_core_persists_original_binding_and_sends_only_summary(monkeypatch, details, status, count):
    from app.agents.feature_extractor import FeaturePoints
    from app.workers import analyze_job
    from conftest import make_settings

    async def analyze(*args):
        return {"product": {"clothingType": "top"}, "analysis": {}, "intermediate": {"styleTags": [], "swatchSuggestions": []}}, "gemini"

    async def extract(*args, **kwargs):
        return (FeaturePoints(["골지 짜임"], details) if details is not None else ["골지 짜임"]), "gemini"

    monkeypatch.setattr(analyze_job.product_analyst, "analyze", analyze)
    monkeypatch.setattr(analyze_job.feature_extractor, "extract", extract)
    monkeypatch.setattr(analyze_job, "shrink_for_vision", lambda data, mime: (b"resized", mime))
    result = asyncio.run(analyze_job.analyze_image_bytes(make_settings(input_consistency="off"), SOURCES, slots=SLOTS))
    persisted = result["analysis_payload"][dr.PERSISTED_KEY]
    public = result["result_data"][dr.PERSISTED_KEY]
    assert public["status"] == status and len(public["candidates"]) == count
    assert dr.source_binding_matches(persisted, SOURCES, SLOTS)
    assert "binding" not in public


@pytest.mark.parametrize("field,value", [("sourceSlot", []), ("sourceSha256", None)])
def test_malformed_persisted_binding_fails_with_contract_error(field, value):
    altered = contract()
    altered["binding"][0][field] = value
    with pytest.raises(dr.DetailRecommendationError):
        dr.validate_persisted(altered)


def test_display_text_is_bounded_and_single_line():
    item = contract([candidate(label="단추\n<여밈>", reason="확인\x00가능" )])["candidates"][0]
    assert item["label"] == "단추 여밈"
    assert item["reason"] == "확인 가능"


def test_analysis_source_selection_matches_public_subset_and_direction_order():
    images = [{"id": str(i), "slot": slot} for i, slot in enumerate(["Detail", "BackDetail", "Front", "Back", "Detail"])]
    product = {"colors": [{"isBase": True, "images": images}]}
    assert dr.analysis_source_entries(product) == [("Front", "2"), ("Back", "3"), ("Detail", "0"), ("BackDetail", "1")]


def test_resolve_remaps_analyzed_subset_index_to_full_originals():
    value = dr.build_contract([candidate(sourceIndex=1)], [SOURCES[0], (b"detail", "image/png")], ["Front", "Detail"])
    full = [SOURCES[0], SOURCES[1], (b"other-detail", "image/png"), (b"detail", "image/png")]
    slots = ["Front", "Back", "Detail", "Detail"]
    assert dr.source_binding_matches(value, full, slots)
    item = dr.resolve_target({dr.PERSISTED_KEY: value}, value["candidates"][0]["id"], source_images=full, slots=slots, direction="front")
    assert item["sourceIndex"] == 3
    assert value["candidates"][0]["sourceIndex"] == 1


def test_analysis_get_exposes_candidates_without_private_binding(client, make_token, monkeypatch):
    from app import routes
    from conftest import auth_headers, patch_route_db
    patch_route_db(monkeypatch, routes)
    value = contract()

    async def project(*args):
        return {"id": "p1"}

    async def analysis(*args):
        return {dr.PERSISTED_KEY: value}

    monkeypatch.setattr(routes.repo, "get_project", project)
    monkeypatch.setattr(routes.repo, "get_analysis", analysis)
    response = client.get("/v1/projects/p1/analysis", headers=auth_headers(make_token))
    assert response.status_code == 200
    assert response.json()[dr.PERSISTED_KEY] == dr.public_summary(value)
