"""R2: display derivatives must not alter paid calls or generation input bytes."""
import asyncio
import contextlib
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np
import pytest
from PIL import Image, ImageDraw

from app import repo, routes
from app.services import fal_api, fal_cutout, garment_grid, matching_cutout as mc
from app.services import matching_flatlay as mf, outfit, qwen_flatlay
from conftest import assert_query_binds, auth_headers
from test_matching_board_url import Cursor
from test_matching_cutout_job import _job_dict, _run, _wire_worker
from test_editor_image_fallback import runtime as editor_runtime


def png(image):
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def cutout(size=(180, 240)):
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    w, h = size
    ImageDraw.Draw(image).ellipse((w // 6, h // 12, w * 5 // 6 - 1, h * 11 // 12 - 1),
                                  fill=(10, 120, 200, 255))
    return png(image)


def wire_qwen(monkeypatch):
    rgba = Image.open(io.BytesIO(cutout((128, 192))))
    alpha = np.array(rgba.getchannel("A"))
    # Partial alpha exercises the legacy paste operation, not just solid masks.
    alpha[alpha > 0] = 210
    rgba.putalpha(Image.fromarray(alpha))
    generated = Image.new("RGB", rgba.size, "white")
    generated.paste(rgba, (0, 0), rgba)
    generated_bytes = png(generated)
    calls = []

    async def run(settings, model, payload):
        calls.append((model, payload))
        return {"images": [{"url": "mock:qwen"}]}

    async def fetch(url):
        assert url == "mock:qwen"
        return generated_bytes

    segment = AsyncMock(return_value=png(rgba))
    monkeypatch.setattr(fal_api, "run_model", run)
    monkeypatch.setattr(fal_api, "fetch_image", fetch)
    monkeypatch.setattr(fal_cutout, "cutout_png", segment)

    # Pre-R2 _composite algorithm: color correction, alpha, PNG, grey paste.
    rgb = qwen_flatlay.soft_color_correct(cutout(), np.array(generated), alpha > 127)
    corrected = Image.fromarray(rgb).convert("RGBA")
    corrected.putalpha(Image.fromarray(alpha))
    legacy_raw = mc.flatten_on_bg(png(corrected))
    return calls, segment, legacy_raw, png(corrected)


def test_one_qwen_call_returns_legacy_jpeg_raw_and_transparent_board(monkeypatch):
    calls, segment, legacy_raw, transparent = wire_qwen(monkeypatch)
    render = AsyncMock(wraps=qwen_flatlay.render)
    monkeypatch.setattr(qwen_flatlay, "render", render)
    flat = asyncio.run(mf.render_thumbnail(None,
        settings=SimpleNamespace(matching_flatlay_provider="qwen"),
        cutout_png=cutout(), clothing_type=None))

    assert flat is not None
    assert flat.raw == legacy_raw
    assert flat.image == mc.encode_thumbnail(legacy_raw)
    assert Image.open(io.BytesIO(flat.image)).format == "JPEG"
    assert flat.board == mc.encode_board_cutout(transparent)
    board = Image.open(io.BytesIO(flat.board))
    assert board.format == "PNG" and board.mode == "RGBA"
    assert board.getchannel("A").getbbox() == (0, 0, *board.size)
    assert board.getchannel("A").getextrema() == (0, 210)
    assert board.size == (85, 160)
    render.assert_awaited_once()
    assert render.await_args.kwargs == {"transparent": True}
    segment.assert_awaited_once()
    assert [model for model, _ in calls] == [fal_api.QWEN_MODEL]
    canvas, _ = qwen_flatlay.prepare_canvas(cutout())
    assert calls[0][1]["image_urls"] == [fal_api.prepare_image(canvas).url]


@pytest.mark.parametrize("size,expected", [((900, 1200), (307, 512)), ((180, 240), (120, 200))])
def test_board_encoder_crops_alpha_and_caps_long_edge_without_upscaling(size, expected):
    result = mc.encode_board_cutout(cutout(size))
    image = Image.open(io.BytesIO(result))
    assert image.mode == "RGBA" and image.format == "PNG"
    assert image.size == expected
    assert image.getchannel("A").getextrema() == (0, 255)
    assert image.getchannel("A").getbbox() == (0, 0, *image.size)


def test_board_encoder_rejects_empty_alpha():
    with pytest.raises(ValueError, match="empty"):
        mc.encode_board_cutout(png(Image.new("RGBA", (20, 20))))


@pytest.mark.parametrize("mode,stage", [("full", "encode"), ("off", "encode"),
                                       ("full", "upload"), ("full", "asset")])
def test_optional_board_failure_keeps_legacy_thumbnail_and_generation_grid(monkeypatch, mode, stage):
    app, r2, calls = _wire_worker(monkeypatch, cut_png=cutout())
    app.state.settings.matching_flatlay_provider = "qwen"
    app.state.settings.matching_flatlay = mode
    qwen_calls, _, legacy_raw, _ = wire_qwen(monkeypatch)
    if stage == "encode":
        def fail(*args):
            raise ValueError("board encode failed")
        monkeypatch.setattr(mc, "encode_board_cutout", fail)
    elif stage == "upload":
        original_put = r2.put_bytes
        def fail(key, data, mime, *args):
            if mime == "image/png":
                raise OSError("board upload failed")
            return original_put(key, data, mime, *args)
        monkeypatch.setattr(r2, "put_bytes", fail)
    else:
        original_create = repo.create_asset
        async def fail(conn, **kwargs):
            if kwargs["mime"] == "image/png":
                raise RuntimeError("board insert failed")
            return await original_create(conn, **kwargs)
        monkeypatch.setattr(repo, "create_asset", fail)
    _run(app, _job_dict())
    assert calls["finalize"][1]["state"] == "ready"
    assert calls["swap"] is not None
    assert calls["commits"] == 2
    front = legacy_raw if mode == "full" else mc.flatten_on_bg(cutout())
    assert r2.puts[0][1] == mc.encode_thumbnail(front)
    assert r2.puts[1][1] == garment_grid.compose_garment_grid([front, mc.flatten_on_bg(cutout())])
    assert len(qwen_calls) == (1 if mode == "full" else 0)
    assert not any(a["metadata"].get("canonicalType") == "custom_board_cutout" for a in calls["assets"])
    assert calls["savepoints"] == (["begin", "rollback"] if stage == "asset" else [])


@pytest.mark.parametrize("mode", ["off", "on", "full", "failure"])
def test_worker_preserves_grid_bytes_and_ids_while_storing_board(monkeypatch, mode):
    app, r2, calls = _wire_worker(monkeypatch, cut_png=cutout())
    app.state.settings.matching_flatlay_provider = "qwen"
    app.state.settings.matching_flatlay = "full" if mode == "failure" else mode
    qwen_calls, segment, legacy_raw, transparent = wire_qwen(monkeypatch)
    if mode == "failure":
        async def fail(*args):
            qwen_calls.append((fal_api.QWEN_MODEL, {}))
            raise RuntimeError("mock generation failed")
        monkeypatch.setattr(fal_api, "run_model", fail)
    compose = garment_grid.compose_garment_grid
    grid_inputs = []

    def spy(images):
        grid_inputs.append(list(images))
        return compose(images)

    monkeypatch.setattr(garment_grid, "compose_garment_grid", spy)
    _run(app, _job_dict())
    assert calls["finalize"][1]["state"] == "ready"
    flat_ok = mode in ("on", "full")
    expected_inputs = [mc.flatten_on_bg(cutout())] * 2
    if mode == "full":
        expected_inputs[0] = legacy_raw
    assert grid_inputs == [expected_inputs]
    assert r2.puts[1][1] == compose(expected_inputs)
    assert r2.puts[0][1] == mc.encode_thumbnail(legacy_raw if flat_ok else expected_inputs[0])
    assert len(qwen_calls) == (0 if mode == "off" else 1)
    assert segment.await_count == (1 if flat_ok else 0)
    assert len(calls["segment"]) == 2

    fingerprint = mc.source_fingerprint(["h" + key for key in _job_dict()["payload"]["sourceKeys"]], provider="fal")
    grid_fp = mf.flatlay_fingerprint(fingerprint, provider="qwen") if mode in ("full", "failure") else fingerprint
    grid_id = mc.derived_asset_id(role="grid", matching_item_id="custom_x", source_hash=grid_fp, provider="fal")
    thumb_id = (mf.derived_asset_id(matching_item_id="custom_x", source_hash=fingerprint, provider="qwen")
                if flat_ok else mc.derived_asset_id(role="thumb", matching_item_id="custom_x", source_hash=fingerprint, provider="fal"))
    assert calls["swap"] == ("custom_x", thumb_id, grid_id)
    board, = [a for a in calls["assets"] if a["metadata"].get("canonicalType") == "custom_board_cutout"]
    assert board["asset_id"] not in (thumb_id, grid_id)
    assert board["mime"] == "image/png" and board["key"].endswith(".png")
    assert (board["source"], board["user_id"], board["project_id"]) == ("derived", "u1", "p1")
    assert board["metadata"]["sourceAssetId"] == grid_id
    assert board["metadata"]["thumbnailAssetId"] == thumb_id
    assert board["metadata"]["matchingItemId"] == "custom_x"
    assert board["metadata"]["sourceHash"] == (mf.flatlay_fingerprint(fingerprint, provider="qwen") if flat_ok else fingerprint)
    assert board["metadata"]["purpose"] == mc.CUTOUT_PURPOSE
    stored = next(data for key, data, mime in r2.puts if key == board["key"])
    assert stored == mc.encode_board_cutout(transparent if flat_ok else cutout())


@pytest.mark.parametrize("public", [False, True])
def test_catalog_and_ready_custom_board_assets_share_one_bounded_lookup(public):
    items = [{"id": "catalog", "is_custom": False, "image_asset_id": "catalog-source"}]
    if not public:
        items += [{"id": f"custom-{i}", "is_custom": True, "image_asset_id": f"grid-{i}",
                   "image_meta": {"type": mc.CUTOUT_KIND}} for i in range(60)]
    rows = [{"source_asset_id": item["image_asset_id"], "r2_key": f"board/{item['id']}.png"} for item in items]
    cursor = Cursor([items, rows])
    conn = SimpleNamespace(cursor=lambda: cursor)
    result = asyncio.run(repo.list_curated_matching_items(conn) if public
                         else repo.list_active_matching_items(conn, "user", "project"))
    assert len(cursor.calls) == 2
    assert [row["board_key"] for row in result] == [row["r2_key"] for row in rows]
    sql, params = cursor.calls[-1]
    assert_query_binds(sql, params)
    if not public:
        for gate in ("canonicalType' = 'custom_board_cutout'", "visibility = 'private'",
                     "mime_type = 'image/png'", "user_id = %s", "project_id = %s",
                     "mi.owner_user_id = assets.user_id", "mi.project_id = assets.project_id",
                     "mi.id = assets.metadata->>'matchingItemId'",
                     "mi.image_asset_id::text = assets.metadata->>'sourceAssetId'",
                     "mi.thumbnail_asset_id::text = assets.metadata->>'thumbnailAssetId'"):
            assert gate in sql
        assert [f"grid-{i}" for i in range(60)] in params
    else:
        assert "custom_board_cutout" not in sql


@pytest.mark.parametrize("has_board", [False, True])
def test_ready_custom_only_list_queries_once_and_legacy_upload_falls_back(has_board):
    item = {"id": "custom", "is_custom": True, "image_asset_id": "grid", "thumb_key": "thumb.jpg",
            "image_meta": {"type": mc.CUTOUT_KIND}}
    cursor = Cursor([[item], [{"source_asset_id": "grid", "r2_key": "board.png"}] if has_board else []])
    result, = asyncio.run(repo.list_active_matching_items(SimpleNamespace(cursor=lambda: cursor), "u", "p"))
    assert len(cursor.calls) == 2
    from app.services.matching import board_url_for
    assert board_url_for(SimpleNamespace(public_url=lambda key: key), result) == (
        "board.png" if has_board else "thumb.jpg")


@pytest.mark.parametrize("ready,board,expected", [
    (True, "board.png", "board.png"), (True, None, "thumb.jpg"),
    (False, "stale-board.png", None), (False, None, None),
])
def test_custom_board_url_prefers_png_only_after_ready(ready, board, expected):
    item = {"id": "custom", "name": "내 옷", "gender": "unisex", "is_custom": True,
            "image_asset_id": "grid", "image_key": "grid.jpg",
            "thumb_key": "thumb.jpg", "board_key": board,
            "image_meta": {"type": mc.CUTOUT_KIND} if ready else {}}
    r2 = SimpleNamespace(public_url=lambda key: f"https://assets.test/{key}")
    for row in (outfit.item_to_api(r2, item), routes._matching_item_to_api(r2, item)):
        assert row["boardUrl"] == (r2.public_url(expected) if expected else None)
        assert row["thumbnailUrl"] == r2.public_url("thumb.jpg")
        assert row["imageUrl"] == r2.public_url("grid.jpg")


def test_delete_collects_all_board_revisions_with_existing_asset_guards():
    cursor = Cursor([[{"id": "board", "r2_key": "board.png"}]])
    result = asyncio.run(repo.soft_delete_unreferenced_custom_assets(
        SimpleNamespace(cursor=lambda: cursor), "user", "project", ["grid"], matching_item_id="custom"))
    assert result == [{"id": "board", "r2_key": "board.png"}]
    sql, params = cursor.calls[0]
    assert_query_binds(sql, params)
    for gate in ("a.id = any(%s::uuid[])", "a.metadata->>'canonicalType' = 'custom_board_cutout'",
                 "a.metadata->>'matchingItemId' = %s", "a.source = 'derived'",
                 "a.user_id = %s", "a.project_id = %s", "custom_match_cutout",
                 "not exists (select 1 from mannequin_cuts", "not exists (select 1 from matching_items"):
        assert gate in sql
    assert params == (["grid"], "custom", "user", "project")


def test_delete_route_removes_board_objects_even_with_legacy_grid_metadata(client, make_token, monkeypatch):
    deleted = []
    cursor = Cursor([[{"id": "board", "r2_key": "board.png"},
                      {"id": "previous-board", "r2_key": "previous-board.png"}]])
    conn = SimpleNamespace(cursor=lambda: cursor, commit=AsyncMock())

    @contextlib.asynccontextmanager
    async def connection(_request):
        yield conn

    monkeypatch.setattr(routes, "get_conn", connection)
    monkeypatch.setattr(routes, "_r2", lambda _: SimpleNamespace(delete=deleted.append))
    monkeypatch.setattr(repo, "lock_custom_match_project", AsyncMock(return_value=True))
    monkeypatch.setattr(repo, "get_custom_matching_item", AsyncMock(return_value={
        "id": "custom", "image_asset_id": "grid", "thumbnail_asset_id": "thumb",
        "image_meta": {"sourceAssetIds": ["upload"]}}))
    monkeypatch.setattr(repo, "get_analysis", AsyncMock(return_value={}))
    monkeypatch.setattr(repo, "delete_custom_matching_item", AsyncMock())
    monkeypatch.setattr(repo, "save_analysis", AsyncMock())
    response = client.delete("/v1/projects/p1/analysis/custom-match-item", headers=auth_headers(make_token))
    assert response.status_code == 204
    assert cursor.calls[0][1][1] == "custom"
    assert deleted == ["board.png", "previous-board.png"]


@pytest.fixture
def generated_grid(monkeypatch):
    # Produce the actual worker bytes, then restore IO mocks before downstream workers run.
    with monkeypatch.context() as patch:
        app, r2, calls = _wire_worker(patch, cut_png=cutout())
        app.state.settings.matching_flatlay_provider = "qwen"
        app.state.settings.matching_flatlay = "full"
        _, _, legacy_raw, _ = wire_qwen(patch)
        expected = garment_grid.compose_garment_grid([legacy_raw, mc.flatten_on_bg(cutout())])
        _run(app, _job_dict())
        assert calls["finalize"][1]["state"] == "ready"
        assert r2.puts[1][1] == expected
        return r2.puts[1][1]


def test_mannequin_receives_unchanged_generated_grid_not_board(monkeypatch, generated_grid):
    import test_mannequin_adjust_edit_path as harness

    original = harness._R2.get_bytes

    def read(self, key):
        if key == "match.png":
            self.reads.append(key)
            return generated_grid
        return original(self, key)

    monkeypatch.setattr(harness._R2, "get_bytes", read)
    calls, r2 = harness._run_worker(monkeypatch, mode="generate", current_match_id="custom_x",
        analysis_overrides={"matchClothing": [{"id": "custom_x", "isCustom": True,
                                               "boardUrl": "DO_NOT_GENERATE_WITH_BOARD.png"}]})
    assert calls["run"][0]["match_img"].data == generated_grid
    assert r2.reads == ["bw.png", "prod.png", "match.png"]


def test_worn_detail_receives_unchanged_generated_grid_not_board(monkeypatch, generated_grid):
    from test_detail_page_fallback import _block, _item, _run as run_detail
    from test_detail_incomplete_cuts_partial import _R2

    r2 = _R2()
    r2.objects["k/match:custom_x"] = (generated_grid, "image/jpeg", None)
    result = run_detail(monkeypatch, [_block(matchIds=["custom_x"])],
        [_item("custom_x", isCustom=True, boardUrl="DO_NOT_GENERATE_WITH_BOARD.png")], r2=r2)
    assert result["generated"]["b1"]["images"] == [b"PRODUCT", generated_grid]
    assert "DO_NOT_GENERATE_WITH_BOARD.png" not in r2.gets


def test_worn_editor_receives_unchanged_generated_grid_not_board(monkeypatch, generated_grid, editor_runtime):
    from test_editor_image_fallback import run

    state = editor_runtime
    original = state["app"].state.r2.get_bytes

    def read(key):
        if key == "asset-custom_x":
            state["reads"].append(key)
            return generated_grid
        return original(key)

    monkeypatch.setattr(state["app"].state.r2, "get_bytes", read)
    state["analysis"]["matchClothing"] = [{"id": "custom_x", "isCustom": True,
                                           "boardUrl": "DO_NOT_GENERATE_WITH_BOARD.png"}]
    run(state, {"matchIds": ["custom_x"]})
    assert state["generations"][0]["images"] == [b"product", generated_grid]
    assert "DO_NOT_GENERATE_WITH_BOARD.png" not in state["reads"]
