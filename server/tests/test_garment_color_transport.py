"""Garment color is measured lazily after login, never inside product analysis."""
import asyncio
import json
from dataclasses import replace
from io import BytesIO

import pytest
from PIL import Image

from app import public_routes, repo, routes
from app.agents import garment_color_evidence as evidence, garment_color_observer as observer
from app.agents import horizon_background as hb
from app.services import garment_color_measure as measure
from app.workers import analyze_job
from conftest import FakePool, auth_headers, make_settings, patch_route_db

GROUP = "ssg1__horizon-sequence-05-women-top-draped__one"


def png(color):
    out = BytesIO()
    Image.new("RGB", (80, 100), color).save(out, format="PNG")
    return out.getvalue()


def tone_block(**changes):
    return {"id": "cut", "source": "ai", "cutType": "horizon", "sectionRole": "studio",
            "contentRole": "fit", "direction": "front", "shot": "full", "pose": "auto",
            "spaceGroupId": GROUP, "spaceSetMemberOrder": 1, "exampleId": "ss_example",
            "horizonBackgroundMode": "garment-tone", "colorId": "base", **changes}


def regions(sources):
    return [{"sourceIndex": s["sourceIndex"], "clothingType": "top", "certainty": "high",
             "colorStructure": "solid", "lighting": "neutral", "material": "matte",
             "polygons": [[[.1, .1], [.45, .1], [.45, .4], [.1, .4]], [[.55, .55], [.9, .55], [.9, .9], [.55, .9]]]}
            for s in sources]


def test_analysis_never_observes_or_builds_a_color_contract(monkeypatch):
    async def analyst(*args):
        return {"product": {"clothingType": "top"}, "analysis": {}, "intermediate": {"styleTags": [], "swatchSuggestions": []}}, "fake"
    async def feature(*args, **kwargs): return [], "fake"
    async def consistency(*args, **kwargs): return None
    def forbidden(*args, **kwargs): raise AssertionError("product analysis must not measure garment color")
    monkeypatch.setattr(analyze_job.product_analyst, "analyze", analyst)
    monkeypatch.setattr(analyze_job.feature_extractor, "extract", feature)
    monkeypatch.setattr(analyze_job, "_judge_input_consistency", consistency)
    monkeypatch.setattr(evidence, "build_contract", forbidden)
    monkeypatch.setattr(observer, "observe", forbidden)
    monkeypatch.setattr(observer, "analyze_with_fallback", forbidden)
    product = {"colors": [{"id": "base", "isBase": True}, {"id": "red"}]}
    result = asyncio.run(analyze_job.analyze_image_bytes(
        make_settings(), [(png("blue"), "image/png"), (png("gray"), "image/png")],
        product=product, slots=["Front", "Back"], persist_confirmed_evidence=True))
    assert result["clothing_type"] == "top"
    assert evidence.PERSISTED_KEY not in result["analysis_payload"]
    assert evidence.PERSISTED_KEY not in result["result_data"]


def test_public_analyze_response_has_no_garment_color_keys(client, monkeypatch):
    async def core(settings, sources, **kwargs):
        assert "additional_color_sources" not in kwargs
        return {"result_data": {"clothingType": "top"}, "analysis_payload": {evidence.PERSISTED_KEY: {"server": "only"}}}
    monkeypatch.setattr(public_routes, "analyze_image_bytes", core)
    response = client.post("/v1/public/analyze", files=[("images", ("base.png", png("blue"), "image/png"))],
                           data={"slots": "Front", "productContext": json.dumps({"colors": [{"id": "base", "isBase": True}, {"id": "red"}]})})
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert evidence.PERSISTED_KEY not in data and "garmentColorEvidenceHandoff" not in data


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


def test_garment_tone_color_ids_follow_generation_gate():
    product = {"colors": [{"id": "base", "isBase": True}, {"id": "red"}, {"id": "navy"}, {"id": "ivory"}, {"id": "black"}]}
    blocks = [
        tone_block(colorId=None),                                   # None means the base color
        tone_block(colorId="red", source="mine"),                   # seller photo: never measured
        tone_block(colorId="red", horizonBackgroundMode="reference"),
        tone_block(colorId="red", cutType="styling"),
        tone_block(colorId="red", spaceGroupId="ssg1__not-a-published-set__x"),
        tone_block(colorId="unknown"),
        tone_block(colorId="navy"), tone_block(colorId="navy"),    # dedupe
        tone_block(colorId="ivory"), tone_block(colorId="black"), tone_block(colorId="red"),
    ]
    assert hb.garment_tone_color_ids(blocks, product) == ["base", "navy", "ivory", "black"]
    for block in blocks:
        supported = hb.resolve_for_block(block, product)["reason"] != "set-unsupported"
        if block.get("source") != "mine" and hb.mode(block) == "garment-tone":
            assert supported is (block.get("spaceGroupId") == GROUP)
    assert hb.garment_tone_color_ids([], product) == []


def test_replace_refuses_when_product_photos_changed_since_measurement():
    class Conn:
        def __init__(self): self.sql = []
        def cursor(self): return self
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def execute(self, sql, params=None): self.sql.append((sql, params))
        async def fetchone(self):
            if "from products" in self.sql[-1][0]:
                return {"clothing_type": "top", "colors": [{"id": "c", "images": [{"slot": "Front", "id": "new"}]}]}
            return {"?column?": 1}
    measured = {"clothing_type": "top", "colors": [{"id": "c", "images": [{"slot": "Front", "id": "old"}]}]}
    conn = Conn()
    assert asyncio.run(repo.replace_garment_color_evidence(
        conn, "p", {"version": 1}, expected=None, identity=repo.garment_color_input_identity(measured))) is False
    assert "for update" in conn.sql[0][0]
    assert not any(sql.startswith("update analyses") for sql, _ in conn.sql)
    same = Conn()
    assert asyncio.run(repo.replace_garment_color_evidence(
        same, "p", {"version": 1}, expected=None,
        identity=repo.garment_color_input_identity({"clothing_type": "top", "colors": [{"id": "c", "images": [{"slot": "Front", "id": "new"}]}]}))) is True
    update_sql, params = same.sql[-1]
    assert "is not distinct from" in update_sql and params[2] is None  # absent key, not jsonb 'null'


def _measure_env(client, monkeypatch, *, blocks, analysis=None, owner=True, observe=None, cas=True, product=None):
    seen = {"observe": 0, "saves": []}
    product = product or {"clothing_type": "top", "colors": [
        {"id": "base", "isBase": True, "images": [{"id": "front", "slot": "Front"}, {"id": "detail", "slot": "Detail"}, {"id": "back", "slot": "Back"}]},
    ]}
    photos = {"seller/front": png("#92b4c7"), "seller/back": png("#92b4c7")}
    async def project(*args): return {"id": "p"} if owner else None
    async def get_product(*args): return product
    async def get_analysis(*args): return analysis or {}
    async def storyboard(*args): return blocks
    async def asset(conn, user, aid): return {"id": aid, "r2_key": "seller/" + aid, "mime_type": "image/png"}
    async def fake_observe(settings, sources, *, product=None):
        seen["observe"] += 1
        seen["sources"] = sources
        return await observe(sources) if observe else regions(sources)
    async def replace_evidence(conn, project_id, contract, *, expected, identity):
        seen["saves"].append({"contract": contract, "expected": expected, "identity": identity})
        return cas
    for name, fn in {"get_project": project, "get_product": get_product, "get_analysis": get_analysis,
                     "get_storyboard": storyboard, "get_asset_for_user": asset,
                     "replace_garment_color_evidence": replace_evidence}.items():
        monkeypatch.setattr(repo, name, fn)
    monkeypatch.setattr(observer, "observe", fake_observe)
    patch_route_db(monkeypatch, routes)
    client.app.state.pool = FakePool()
    client.app.state.r2 = type("R2", (), {"get_bytes": staticmethod(lambda key: photos[key])})()
    return seen, photos


def _post(client, make_token):
    return client.post("/v1/projects/p/analysis/garment-colors:measure", headers=auth_headers(make_token))


def test_measure_requires_owner(client, make_token, monkeypatch):
    seen, _ = _measure_env(client, monkeypatch, blocks=[tone_block()], owner=False)
    response = _post(client, make_token)
    assert response.status_code == 404
    assert seen["observe"] == 0 and not seen["saves"]


@pytest.mark.parametrize("blocks", [[], [tone_block(horizonBackgroundMode="reference")], [tone_block(source="mine")]])
def test_measure_skips_without_needed_colors(client, make_token, monkeypatch, blocks):
    seen, _ = _measure_env(client, monkeypatch, blocks=blocks)
    response = _post(client, make_token)
    assert response.status_code == 200, response.text
    assert response.json() == {"status": "skipped", "garmentColorEvidence": None}
    assert seen["observe"] == 0 and not seen["saves"]


def test_measure_reads_original_first_front_back_and_saves_once(client, make_token, monkeypatch):
    seen, photos = _measure_env(client, monkeypatch, blocks=[tone_block(colorId=None), tone_block()])
    response = _post(client, make_token)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "measured"
    assert body["garmentColorEvidence"]["colors"][0]["status"] == "ready"
    assert "sourceBindings" not in body["garmentColorEvidence"]
    assert seen["observe"] == 1 and len(seen["saves"]) == 1
    assert [(s["sourceIndex"], s["colorId"], s["slot"], s["data"]) for s in seen["sources"]] == [
        (0, "base", "Front", photos["seller/front"]), (1, "base", "Back", photos["seller/back"])]
    saved = seen["saves"][0]
    assert saved["expected"] is None
    # Generation verifies the stored bindings against the same raw bytes.
    runtime = hb.loaded_sources({"colors": [{"id": "base", "isBase": True}]}, "base",
                                [{"slot": "Front"}, {"slot": "Back"}],
                                [type("I", (), {"data": photos["seller/front"], "mime": "image/png"}),
                                 type("I", (), {"data": photos["seller/back"], "mime": "image/png"})])
    assert evidence.source_binding_matches(saved["contract"], runtime, clothing_type="top")


def test_measure_skips_when_evidence_already_covers_needed_colors(client, make_token, monkeypatch):
    source = {"sourceIndex": 0, "colorId": "base", "slot": "Front", "data": png("red"), "mime": "image/png"}
    stored = evidence.build_contract(None, [source], clothing_type="top")  # a final unavailable row
    seen, _ = _measure_env(client, monkeypatch, blocks=[tone_block()], analysis={evidence.PERSISTED_KEY: stored})
    response = _post(client, make_token)
    assert response.json()["status"] == "skipped"
    assert response.json()["garmentColorEvidence"]["colors"][0]["status"] == "unavailable"
    assert seen["observe"] == 0 and not seen["saves"]


def test_measure_remeasures_when_category_changed(client, make_token, monkeypatch):
    source = {"sourceIndex": 0, "colorId": "base", "slot": "Front", "data": png("red"), "mime": "image/png"}
    stored = evidence.build_contract(None, [source], clothing_type="bottom")
    seen, _ = _measure_env(client, monkeypatch, blocks=[tone_block()], analysis={evidence.PERSISTED_KEY: stored})
    assert _post(client, make_token).json()["status"] == "measured"
    assert seen["observe"] == 1 and seen["saves"][0]["expected"] == stored


def test_measure_timeout_saves_nothing(client, make_token, monkeypatch):
    client.app.state.settings = replace(client.app.state.settings, analysis_aux_timeout_seconds=0.01)
    async def slow(sources):
        await asyncio.sleep(1)
    seen, _ = _measure_env(client, monkeypatch, blocks=[tone_block()], observe=slow)
    response = _post(client, make_token)
    assert response.status_code == 200
    assert response.json() == {"status": "unavailable", "garmentColorEvidence": None}
    assert seen["observe"] == 1 and not seen["saves"]


def test_measure_observer_failure_is_not_a_server_error(client, make_token, monkeypatch):
    async def broken(sources):
        raise RuntimeError("provider down")
    seen, _ = _measure_env(client, monkeypatch, blocks=[tone_block()], observe=broken)
    response = _post(client, make_token)
    assert response.status_code == 200
    assert response.json()["status"] == "unavailable" and not seen["saves"]


def test_measure_lost_compare_and_set_is_unavailable(client, make_token, monkeypatch):
    seen, _ = _measure_env(client, monkeypatch, blocks=[tone_block()], cas=False)
    response = _post(client, make_token)
    assert response.json() == {"status": "unavailable", "garmentColorEvidence": None}
    assert len(seen["saves"]) == 1


def test_measure_lost_compare_and_set_returns_winner_when_it_covers(client, make_token, monkeypatch):
    # 다른 프로세스(API 이탈 측정과 워커 백스톱)가 먼저 쓴 계약이 필요한 색을 덮으면 그걸 쓴다.
    source = {"sourceIndex": 0, "colorId": "base", "slot": "Front", "data": png("red"), "mime": "image/png"}
    winner = evidence.build_contract(None, [source], clothing_type="top")
    seen, _ = _measure_env(client, monkeypatch, blocks=[tone_block()], cas=False)
    reads = []
    async def get_analysis(*args):
        reads.append(1)
        return {evidence.PERSISTED_KEY: winner} if len(reads) > 1 else {}
    monkeypatch.setattr(repo, "get_analysis", get_analysis)
    body = _post(client, make_token).json()
    assert body["status"] == "skipped"
    assert body["garmentColorEvidence"] == evidence.public_summary(winner)
    assert len(seen["saves"]) == 1 and len(reads) == 2


def test_measure_ignores_color_whose_photos_are_gone_when_others_are_covered(client, make_token, monkeypatch):
    # 정면 자산이 지워진 색은 재도 행이 안 생기니, 나머지 색이 덮였으면 관찰기를 부르지 않는다.
    source = {"sourceIndex": 0, "colorId": "base", "slot": "Front", "data": png("red"), "mime": "image/png"}
    stored = evidence.build_contract(None, [source], clothing_type="top")
    product = {"clothing_type": "top", "colors": [
        {"id": "base", "isBase": True, "images": [{"id": "front", "slot": "Front"}]},
        {"id": "red", "images": [{"id": "gone", "slot": "Front"}]},
    ]}
    seen, _ = _measure_env(client, monkeypatch, blocks=[tone_block(), tone_block(id="red-cut", colorId="red")],
                           analysis={evidence.PERSISTED_KEY: stored}, product=product)
    async def asset(conn, user, aid):
        return None if aid == "gone" else {"id": aid, "r2_key": "seller/" + aid, "mime_type": "image/png"}
    monkeypatch.setattr(repo, "get_asset_for_user", asset)
    assert hb.garment_tone_color_ids([tone_block(), tone_block(id="red-cut", colorId="red")], product) == ["base", "red"]
    response = _post(client, make_token)
    assert response.json()["status"] == "skipped"
    assert seen["observe"] == 0 and not seen["saves"]


def test_measure_coalesces_double_calls_in_one_process(monkeypatch):
    calls = []
    async def fake_ensure(*args, **kwargs):
        calls.append("start")
        await asyncio.sleep(0)
        calls.append("end")
        return "skipped", None
    monkeypatch.setattr(measure, "_ensure", fake_ensure)
    async def run():
        await asyncio.gather(*(measure.ensure_garment_color_evidence(
            make_settings(), None, None, user_id="u", project_id="coalesce") for _ in range(2)))
    asyncio.run(run())
    assert calls == ["start", "end", "start", "end"]
    assert measure._locks == {}  # 끝난 프로젝트의 잠금은 남기지 않는다


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
