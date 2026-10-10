import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from app import repo, routes
from app.agents import content_roles
from app.workers import detail_page_job as dpj
from app.workers import editor_image_job as eij
from conftest import auth_headers, fake_worker_app, make_settings, patch_route_db, worker_job
from test_detail_cut_checkpoints import _IntentDB
from test_storyboard_similar_cuts import item, setup_generation


def _analysis(kind="none", closure_range=None):
    return {"frontClosure": {"version": 1, "kind": kind, "range": closure_range,
                             "secondary": None, "evidenceSlots": ["Front"], "note": None}}


def _product():
    return {"clothingType": "outer", "name": "known coat", "colors": [{
        "id": "base", "isBase": True, "images": [{"slot": "Front", "id": "garment"}]}]}


def _block(block_id, **changes):
    return {"id": block_id, "source": "ai", "sectionRole": "studio", "sectionId": "s1",
            "contentRole": "fit", "cutType": "horizon", "direction": "front", "shot": "full",
            "colorId": "base", "faceExposure": "same", "outerClosureState": "open", **changes}


def _reserve_route(client, make_token, monkeypatch, blocks, analysis, *, balance=95):
    captured = {}
    for name, value in (("get_project", {}), ("get_analysis", analysis),
                        ("get_editor_blocks", []), ("get_storyboard", blocks), ("get_product", _product())):
        monkeypatch.setattr(repo, name, AsyncMock(return_value=value))

    async def create(conn, **kwargs):
        captured.update(kwargs)
        return {"id": "j1"}, True

    async def reserve(conn, user_id, amount):
        captured["reserved"] = amount
        return balance - amount if amount <= balance else None

    monkeypatch.setattr(repo, "create_job", create)
    monkeypatch.setattr(repo, "reserve_credits", reserve)
    monkeypatch.setattr(routes, "_wake_dispatcher", lambda request: None)
    monkeypatch.setattr(routes, "_prewarm_detail_worker", lambda request: None)
    patch_route_db(monkeypatch, routes)
    response = client.post("/v1/projects/p1/detail-page:generate", headers=auth_headers(make_token))
    return response, captured


@pytest.mark.parametrize("kind,closure_range,first,second", [
    ("none", None, "open", "closed"),
    ("button", "binary", "partial", "closed"),
])
def test_reservation_counts_effective_outer_cuts_without_rejecting_affordable_board(
    client, make_token, monkeypatch, kind, closure_range, first, second,
):
    blocks = [_block("first", outerClosureState=first), _block("copy", outerClosureState=second)]
    blocks += [_block(str(index), direction=direction, shot=shot)
               for index, (direction, shot) in enumerate(
                   [("front", "medium"), ("back", "full"), ("back", "medium"), ("side", "medium")], 2)]
    saved = deepcopy(blocks)
    analysis = _analysis(kind, closure_range)
    response, captured = _reserve_route(client, make_token, monkeypatch, blocks, analysis)
    assert response.status_code == 202, response.text
    assert captured["metadata"]["aiCount"] == 5
    assert captured["reserved"] == 95

    app, seen = setup_generation(monkeypatch)
    results = asyncio.run(dpj._gen_cuts(
        app, worker_job(), [item(b) for b in content_roles.canonicalize_storyboard(blocks)],
        _product(), analysis))
    assert len(results[0]) == 6 and len(results[1]) == 5
    assert len(seen["generate"]) == 5
    assert blocks == saved


def test_minimum_charge_survives_outer_projection_on_already_reserved_job(monkeypatch):
    app, seen = setup_generation(monkeypatch)
    _IntentDB().install(monkeypatch)
    blocks = [_block(state, outerClosureState=state) for state in ("open", "partial", "closed")]
    blocks += [_block("medium", shot="medium"), _block("back", direction="back")]
    saved = deepcopy(blocks)
    for name, value in (("get_project", {"copywriting": False}), ("get_storyboard", blocks),
                        ("get_product", _product()), ("get_analysis", _analysis()),
                        ("get_asset_for_user", {"mime_type": "image/png", "r2_key": "garment-input"})):
        monkeypatch.setattr(repo, name, AsyncMock(return_value=value))
    finalized = AsyncMock(return_value={"editor_blocks": [], "available": 0})
    monkeypatch.setattr(repo, "finalize_detail_page_success", finalized)
    monkeypatch.setattr(repo, "finalize_detail_page_failure", AsyncMock())
    app.state.r2.objects["garment-input"] = (b"GARMENT-INPUT", "image/png", None)
    job = {**worker_job(credits_reserved=95), "metadata": {"aiCount": 5, "minCuts": 5, "perCutCost": 19}}
    asyncio.run(dpj.run_detail_page_job(app, job))
    assert finalized.await_count == 1
    outcome = finalized.await_args.kwargs
    assert len(outcome["cut_assets"]) == 3
    assert len(seen["generate"]) == 3
    assert outcome["charge"] == 95
    assert blocks == saved


def test_reservation_keeps_confirmed_gpt_requested_closures_distinct(client, make_token, monkeypatch):
    blocks = [_block(state, cutType="styling", sectionRole="styling", contentRole="coordination",
                     exampleId="ex_styling_men_outer_full_01", outerClosureState=state)
              for state in ("closed", "partial", "open")]
    response, captured = _reserve_route(client, make_token, monkeypatch, blocks, _analysis())
    assert response.status_code == 202, response.text
    assert captured["metadata"]["aiCount"] == 3


def test_projection_never_discounts_similar_children(client, make_token, monkeypatch):
    blocks = [_block("original", outerClosureState="closed"), _block("alias"),
              _block("child", similarSourceBlockId="original"),
              _block("alias-child", similarSourceBlockId="alias")]
    response, captured = _reserve_route(client, make_token, monkeypatch, blocks, _analysis())
    assert response.status_code == 202, response.text
    assert captured["metadata"]["aiCount"] == 3
    app, seen = setup_generation(monkeypatch)
    result = asyncio.run(dpj._gen_cuts(
        app, worker_job(), [item(b) for b in content_roles.canonicalize_storyboard(blocks)],
        _product(), _analysis()))
    assert len(result[0]) == 4 and len(result[1]) == 3
    assert len(seen["generate"]) == 1 and len(seen["vary"]) == 2


def test_identical_editor_new_images_are_each_charged_once_without_storyboard_minimum(monkeypatch):
    for name, value in (("get_project", {}), ("get_product", _product()), ("get_analysis", _analysis()),
                        ("get_asset_for_user", {"id": "garment", "mime_type": "image/png", "r2_key": "garment"})):
        monkeypatch.setattr(repo, name, AsyncMock(return_value=value))
    monkeypatch.setattr(repo, "create_ai_output_cleanup_intent", AsyncMock(return_value="intent"))
    finalized = AsyncMock(return_value={"id": "wardrobe-output"})
    monkeypatch.setattr(repo, "finalize_editor_image_success", finalized)
    generated = AsyncMock(return_value=(b"OUTPUT", "image/png"))
    monkeypatch.setattr(eij.cut_generator, "generate", generated)
    monkeypatch.setattr(eij, "_emit", AsyncMock())
    app = fake_worker_app(make_settings(credit_cost_editor_image=17))
    request = {"mode": "new", "cutType": "horizon", "direction": "front", "shot": "full",
               "colorId": "base", "outerClosureState": "closed"}
    for index in range(2):
        asyncio.run(eij.run_editor_image_job(app, {
            **worker_job(deepcopy(request), credits_reserved=17), "id": f"editor-{index}"}))
    assert generated.await_count == finalized.await_count == 2
    outcomes = [call.kwargs for call in finalized.await_args_list]
    assert [outcome["charge"] for outcome in outcomes] == [17, 17]
    assert outcomes[0]["image"]["asset_id"] != outcomes[1]["image"]["asset_id"]
    assert request["outerClosureState"] == "closed"
