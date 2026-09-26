"""Observed-color transport must not change the fixed main-analysis evidence packet."""
import asyncio
import json
from dataclasses import replace
from io import BytesIO

import pytest
from PIL import Image

from app import public_routes, repo, routes
from app.agents import garment_color_evidence as evidence, garment_color_observer as observer
from app.agents import product_evidence_contract as confirmed
from app.workers import analyze_job
from conftest import auth_headers, make_settings, patch_route_db


def png(color):
    out = BytesIO()
    Image.new("RGB", (80, 100), color).save(out, format="PNG")
    return out.getvalue()


def test_source_map_keeps_base_detail_indexes_and_additional_colors_independent():
    product = {"colors": [
        {"id": "base", "isBase": True, "images": [{"id": "f", "slot": "Front"}, {"id": "d", "slot": "Detail"}, {"id": "b", "slot": "Back"}]},
        {"id": "other", "images": [{"id": "o", "slot": "Front"}, {"id": "o2", "slot": "Front"}, {"id": "ob", "slot": "Back"}]},
    ]}
    assert analyze_job.garment_color_source_entries(product) == [
        {"sourceIndex": 0, "colorId": "base", "slot": "Front", "assetId": "f"},
        {"sourceIndex": 1, "colorId": "base", "slot": "Back", "assetId": "b"},
        {"sourceIndex": 3, "colorId": "other", "slot": "Front", "assetId": "o"},
        {"sourceIndex": 4, "colorId": "other", "slot": "Back", "assetId": "ob"},
    ]


def test_multiple_front_uploads_do_not_expand_color_observer_beyond_one_front_per_color():
    product = {"colors": [{"id": "base", "isBase": True, "images": [
        {"id": "f1", "slot": "Front"}, {"id": "f2", "slot": "Front"}, {"id": "f3", "slot": "Front"},
    ]}]}
    assert analyze_job.garment_color_source_entries(product) == [
        {"sourceIndex": 0, "colorId": "base", "slot": "Front", "assetId": "f1"},
    ]


@pytest.mark.parametrize("observer_state", ["ready", "timeout", "malformed", "bad-image"])
def test_shared_analysis_isolates_optional_observer_and_preserves_original_sources(monkeypatch, observer_state):
    raw = [(png("blue"), "image/png"), (png("green"), "image/png"), (png("gray"), "image/png")]
    extra = {"colorId": "other", "slot": "Front", "data": png("red"), "mime": "image/png"}
    if observer_state == "bad-image":
        extra["data"] = b"invalid-image-bytes"
    seen = {}
    async def analyst(settings, product, images):
        seen["main"] = [i.data for i in images]
        seen["binding"] = product[confirmed.INTERNAL_BINDING_KEY]
        return {"product": {"clothingType": "top"}, "analysis": {}, "intermediate": {"styleTags": [], "swatchSuggestions": []}}, "fake"
    async def feature(settings, product, images, slots=None):
        seen["feature"] = [i.data for i in images]
        return [], "fake"
    async def consistency(settings, images, slots):
        seen["consistency"] = [i.data for i in images]
        return None
    async def observe(settings, sources, *, product):
        seen["sources"] = sources
        if observer_state == "timeout":
            raise asyncio.TimeoutError()
        if observer_state == "bad-image":
            return [{"sourceIndex": s["sourceIndex"], "clothingType": "top", "certainty": "high",
                     "colorStructure": "solid", "lighting": "neutral", "material": "matte",
                     "polygons": [[[.1, .1], [.45, .1], [.45, .4], [.1, .4]], [[.55, .55], [.9, .55], [.9, .9], [.55, .9]]]}
                    for s in sources]
        return [] if observer_state == "ready" else "invalid-provider-output"
    monkeypatch.setattr(analyze_job.product_analyst, "analyze", analyst)
    monkeypatch.setattr(analyze_job.feature_extractor, "extract", feature)
    monkeypatch.setattr(analyze_job, "_judge_input_consistency", consistency)
    monkeypatch.setattr(observer, "observe", observe)
    result = asyncio.run(analyze_job.analyze_image_bytes(
        make_settings(), raw, slots=["Front", "Back", "Detail"], product={"colors": [{"id": "base", "isBase": True}]},
        persist_confirmed_evidence=True, additional_color_sources=[extra],
    ))
    assert seen["main"] == seen["feature"] == seen["consistency"] == [r[0] for r in raw]
    assert len(seen["binding"]["images"]) == 3
    assert [(s["sourceIndex"], s["colorId"], s["data"]) for s in seen["sources"]] == [(0, "base", raw[0][0]), (1, "base", raw[1][0]), (3, "other", extra["data"])]
    assert result["clothing_type"] == "top"
    profile = result["analysis_payload"][evidence.PERSISTED_KEY]
    assert {c["colorId"] for c in profile["colors"]} == {"base", "other"}
    by_color = {color["colorId"]: color for color in profile["colors"]}
    if observer_state == "bad-image":
        # The damaged additional color must not invalidate the base garment's
        # valid Front/Back pixels; their different panels now yield a coarse tone.
        assert by_color["base"]["status"] == "ready"
        assert by_color["other"]["status"] == "unavailable"
    else:
        assert all(color["status"] == "unavailable" for color in profile["colors"])
    assert "sourceBindings" not in result["result_data"][evidence.PERSISTED_KEY]
    if observer_state == "bad-image":
        assert by_color["other"]["reason"] == "invalid_image"


def test_observer_never_calls_provider_without_sources_and_returns_only_regions(monkeypatch):
    seen = []
    async def provider(settings, prompt, images, schema, **kwargs):
        seen.append((prompt, images, schema))
        return {"regions": []}, "fake"
    monkeypatch.setattr(observer, "analyze_with_fallback", provider)
    assert asyncio.run(observer.observe(make_settings(), [])) == []
    assert not seen
    source = {"sourceIndex": 4, "colorId": "not-a-prompt", "slot": "Front", "data": png("blue"), "mime": "image/png"}
    assert asyncio.run(observer.observe(make_settings(), [source], product={"clothingType": "bottom"})) == []
    assert "sourceIndex=4" in seen[0][0] and "bottom" in seen[0][0]
    assert "not-a-prompt" not in seen[0][0]
    assert list(seen[0][2]["properties"]) == ["regions"]


def test_observer_rejects_oversize_header_before_pixel_conversion_and_keeps_source_indexes(monkeypatch):
    original_open = Image.open
    class Oversize:
        width, height, size = 10000, 6000, (10000, 6000)
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def convert(self, *args): raise AssertionError("oversize image must not allocate converted pixels")
        def thumbnail(self, *args): raise AssertionError("oversize image must not resize")
    def open_image(stream, *args, **kwargs):
        return Oversize() if stream.getvalue() == b"large-image-header" else original_open(stream, *args, **kwargs)
    seen = {}
    async def provider(settings, prompt, images, schema, **kwargs):
        seen.update(prompt=prompt, images=images)
        return {"regions": []}, "fake"
    monkeypatch.setattr(Image, "open", open_image)
    monkeypatch.setattr(observer, "analyze_with_fallback", provider)
    large = {"sourceIndex": 1, "colorId": "large", "slot": "Front", "data": b"large-image-header", "mime": "image/png"}
    small = {"sourceIndex": 4, "colorId": "small", "slot": "Front", "data": png("blue"), "mime": "image/png"}
    assert observer._observer_image(b"broken", "image/png") is None
    assert asyncio.run(observer.observe(make_settings(), [large])) == []
    assert not seen
    asyncio.run(observer.observe(make_settings(), [large, small]))
    assert len(seen["images"]) == 1
    assert "Image 1: sourceIndex=4" in seen["prompt"]
    assert "sourceIndex=1" not in seen["prompt"]


def test_analysis_without_color_sources_never_builds_a_color_contract(monkeypatch):
    async def analyst(*args):
        return {"product": {"clothingType": "top"}, "analysis": {}, "intermediate": {"styleTags": [], "swatchSuggestions": []}}, "fake"
    async def feature(*args, **kwargs): return [], "fake"
    def forbidden(*args, **kwargs): raise AssertionError("empty sources must not enter strict binding validation")
    monkeypatch.setattr(analyze_job.product_analyst, "analyze", analyst)
    monkeypatch.setattr(analyze_job.feature_extractor, "extract", feature)
    monkeypatch.setattr(evidence, "build_contract", forbidden)
    monkeypatch.setattr(observer, "analyze_with_fallback", forbidden)
    result = asyncio.run(analyze_job.analyze_image_bytes(make_settings(input_consistency="off"), [(png("blue"), "image/png")], product={}, slots=["Front"]))
    assert result["clothing_type"] == "top"
    assert evidence.PERSISTED_KEY not in result["analysis_payload"]


def test_public_multipart_keeps_base_four_and_passes_separate_color_sources(client, monkeypatch):
    seen = {}
    async def core(settings, sources, **kwargs):
        seen.update(sources=sources, **kwargs)
        return {"result_data": {"clothingType": "top"}}
    monkeypatch.setattr(public_routes, "analyze_image_bytes", core)
    a, b = png("blue"), png("red")
    response = client.post("/v1/public/analyze", files=[("images", ("base.png", a, "image/png")), ("colorImages", ("red.png", b, "image/png"))],
        data={"slots": "Front", "colorSlots": "Front", "colorIds": "red", "productContext": json.dumps({"colors": [{"id": "base", "isBase": True}, {"id": "red"}]})})
    assert response.status_code == 200, response.text
    assert seen["sources"] == [(a, "image/png")]
    assert seen["additional_color_sources"] == [{"colorId": "red", "slot": "Front", "data": b, "mime": "image/png"}]


@pytest.mark.parametrize("bad", ["unknown-color", "detail-slot", "five-photos", "duplicate-view"])
def test_public_rejects_unbound_or_excess_color_inputs_before_provider(client, monkeypatch, bad):
    async def forbidden(*args, **kwargs): raise AssertionError("invalid color transport must not call analysis")
    monkeypatch.setattr(public_routes, "analyze_image_bytes", forbidden)
    count = 5 if bad == "five-photos" else 2 if bad == "duplicate-view" else 1
    photos = [("images", ("base.png", png("blue"), "image/png"))] + [("colorImages", (f"c{i}.png", png("red"), "image/png")) for i in range(count)]
    response = client.post("/v1/public/analyze", files=photos, data={
        "colorSlots": ["Detail" if bad == "detail-slot" else "Front"] * count,
        "colorIds": ["unknown" if bad == "unknown-color" else "red"] * count,
        "productContext": json.dumps({"colors": [{"id": "base"}, {"id": "red"}]}),
    })
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_color_sources"


def test_invalidation_identity_ignores_color_labels_but_detects_photo_and_category_changes():
    original = {"clothing_type": "top", "colors": [{"id": "c", "name": "blue", "images": [{"slot": "Front", "id": "f"}]}]}
    renamed = {**original, "colors": [{**original["colors"][0], "name": "light blue", "swatchId": "blue"}]}
    assert repo.garment_color_input_identity(original) == repo.garment_color_input_identity(renamed)
    assert repo.garment_color_input_identity(original) != repo.garment_color_input_identity({**original, "clothing_type": "bottom"})
    changed = {**original, "colors": [{"id": "c", "images": [{"slot": "Front", "id": "new"}]}]}
    assert repo.garment_color_input_identity(original) != repo.garment_color_input_identity(changed)


@pytest.mark.parametrize("change,invalidates", [
    ({"colors": [{"id": "c", "name": "renamed", "images": [{"slot": "Front", "id": "f"}]}]}, False),
    ({"colors": [{"id": "c", "images": [{"slot": "Front", "id": "replacement"}]}]}, True),
    ({"clothing_type": "bottom"}, True),
])
def test_product_save_invalidates_only_measurement_when_current_input_changes(change, invalidates):
    class Conn:
        product = {"clothing_type": "top", "colors": [{"id": "c", "images": [{"slot": "Front", "id": "f"}]}]}
        analysis = {"garmentColorEvidence": {"server": "measured"}, "fit": "regular"}
        def cursor(self): return self
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def execute(self, sql, params=None):
            if sql.startswith("update products"):
                self.product = {**self.product, **change}
            if sql.startswith("update analyses"):
                self.analysis = {k: v for k, v in self.analysis.items() if k != "garmentColorEvidence"}
        async def fetchone(self): return dict(self.product)
    conn = Conn()
    asyncio.run(repo.save_product(conn, "p", "owner", change))
    assert ("garmentColorEvidence" not in conn.analysis) is invalidates
    assert conn.analysis["fit"] == "regular"


def test_public_combined_photo_budget_includes_optional_colors(client, monkeypatch):
    image = png("blue")
    monkeypatch.setattr(public_routes, "MAX_PUBLIC_REQUEST_BYTES", len(image) * 2 - 1)
    # Omitted/unreliable Content-Length is covered by the actual file-byte count too.
    response = client.post("/v1/public/analyze", headers={"content-length": "0"},
        files=[("images", ("base.png", image, "image/png")), ("colorImages", ("other.png", image, "image/png"))],
        data={"colorSlots": "Front", "colorIds": "other", "productContext": json.dumps({"colors": [{"id": "base"}, {"id": "other"}]})})
    assert response.status_code == 413


def test_color_promotion_rejects_forged_handoff_without_writes(client, make_token, monkeypatch):
    async def forbidden(*args, **kwargs): raise AssertionError("forged evidence must not be saved")
    monkeypatch.setattr(repo, "save_garment_color_evidence", forbidden)
    response = client.post("/v1/projects/p/analysis/garment-colors:promote", json={"observedHex": "#ff0000"}, headers=auth_headers(make_token))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_analysis_handoff"


@pytest.mark.parametrize("case", ["same", "unobserved-color", "white-browser", "photo-changed", "category-changed", "tampered", "wrong-owner"])
def test_signed_color_promotion_binds_current_owned_photos_and_category(client, make_token, monkeypatch, case):
    client.app.state.settings = replace(client.app.state.settings, r2_secret_access_key="test-color-secret-at-least-32-bytes")
    original = png("white" if case == "white-browser" else "#92b4c7")
    source = {"sourceIndex": 0, "colorId": "base", "slot": "Front", "data": original, "mime": "image/png"}
    regions = [{"sourceIndex": 0, "clothingType": "top", "certainty": "high", "colorStructure": "solid",
                "lighting": "neutral", "material": "matte", "polygons": [
                    [[.1, .1], [.45, .1], [.45, .4], [.1, .4]],
                    [[.55, .55], [.9, .55], [.9, .9], [.55, .9]],
                ]}] if case == "white-browser" else None
    contract = evidence.build_contract(regions, [source], clothing_type="top")
    handoff = evidence.issue_handoff(contract, client.app.state.settings.r2_secret_access_key)
    if case == "white-browser":
        assert contract["colors"][0]["status"] == "ready"
        assert contract["colors"][0]["lightnessRange"][1] == 1.0
        handoff = json.loads(json.dumps(handoff), parse_float=lambda text: int(float(text)) if float(text).is_integer() else float(text))
    if case == "tampered":
        handoff["contract"]["colors"][0]["observedHex"] = "#ff0000"
    saved = []
    async def project(*args): return None if case == "wrong-owner" else {"id": "p"}
    async def product(*args): return {"clothingType": "bottom" if case == "category-changed" else "top", "colors": [
        {"id": "base", "isBase": True, "images": [{"id": "photo", "slot": "Front"}]},
        *([{"id": "unobserved", "images": [{"id": "other", "slot": "Front"}]}] if case == "unobserved-color" else []),
    ]}
    async def asset(*args): return {"r2_key": "current", "mime_type": "image/png"}
    async def save(conn, project_id, value): saved.append(value)
    monkeypatch.setattr(repo, "get_project", project)
    monkeypatch.setattr(repo, "get_product", product)
    monkeypatch.setattr(repo, "get_asset_for_user", asset)
    monkeypatch.setattr(repo, "save_garment_color_evidence", save)
    client.app.state.r2 = type("R2", (), {"get_bytes": staticmethod(lambda key: png("red") if case == "photo-changed" else original)})()
    patch_route_db(monkeypatch, routes)
    response = client.post("/v1/projects/p/analysis/garment-colors:promote", json=handoff, headers=auth_headers(make_token))
    assert response.status_code == (200 if case in {"same", "unobserved-color", "white-browser"} else 404 if case == "wrong-owner" else 400), response.text
    assert bool(saved) is (case in {"same", "unobserved-color", "white-browser"})
    if saved:
        assert saved[0] == contract
        assert "sourceBindings" not in response.json()[evidence.PERSISTED_KEY]
