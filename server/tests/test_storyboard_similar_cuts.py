"""Storyboard follow-ups execute from generated originals without provider calls in tests."""

import asyncio
import contextlib
import copy

import pytest

from app import repo, routes
from app.agents import content_roles, cut_variator, similar_cut
from app.agents.gemini_image import GeminiError, InlineImage
from app.workers import cut_checkpoints, detail_page_job as dpj
from conftest import FakeConn, auth_headers, fake_worker_app, make_settings, patch_route_db, worker_job
from test_detail_cut_checkpoints import _IntentDB, _R2
from test_similar_cut_repo import Connection


def source(**overrides):
    return {"id": "original", "source": "ai", "sectionRole": "styling", "sectionId": "s1",
            "contentRole": "coordination", "cutType": "styling", "direction": "front",
            "shot": "full", "colorId": "base", "faceExposure": "same", **overrides}


def child(block_id="next", **overrides):
    return {**source(), "id": block_id, "similarSourceBlockId": "original", **overrides}


def plan(key):
    return {"actionKey": key, "actionSummary": key, "instructions": "Make " + key,
            "preserve": ["person and garment"], "avoid": ["new place"]}


def item(block):
    image = InlineImage("image/png", b"EXAMPLE-AND-GARMENT")
    return (block, [image], "manifest", False, [image])


def test_relationship_inherits_canonical_source_and_preserves_child_layout(monkeypatch):
    monkeypatch.setattr(content_roles, "_example_direction", lambda b, c: {"direction": "side", "sideStyle": "threeQuarter"})
    original = source(exampleId="ex", colorId="black", shot="medium", matchIds=["m1"],
                      spaceGroupId="a-set", spaceVariation="subtle")
    followup = child(sectionId="s2", layoutRowId="row2", sectionLayout="pair", colorId="wrong",
                     shot="full", exampleId="stale", matchIds=["wrong"], spaceSetId="old-set",
                     hookFramePosition="middle", colorwayMemberRole="old")
    blocks = [original, followup]
    untouched = copy.deepcopy(blocks)
    out = content_roles.canonicalize_storyboard(blocks, for_storage=True)
    result = next(b for b in out if b["id"] == "next")
    assert (result["cutType"], result["direction"], result["sideStyle"], result["shot"]) == (
        "styling", "side", "threeQuarter", "medium")
    assert (result["colorId"], result["exampleId"], result["matchIds"]) == ("black", "ex", ["m1"])
    assert (result["sectionId"], result["layoutRowId"], result["sectionLayout"]) == ("s2", "row2", "pair")
    assert result.get("spaceGroupId") == original["spaceGroupId"]
    assert result.get("spaceVariation") == original["spaceVariation"]
    assert not any(key in result for key in ("spaceSetId", "hookFramePosition", "colorwayMemberRole"))
    assert blocks == untouched


@pytest.mark.parametrize("blocks", [
    [child(similarSourceBlockId="missing")],
    [child(similarSourceBlockId="next")],
    [child("original", similarSourceBlockId="next"), child()],
    [source(), child(), child("third", similarSourceBlockId="next")],
    [source(source="mine"), child()],
    [source(contentRole="custom"), child()],
    [source(cutType="product", contentRole="productOverview"), child()],
    [source(cutType="product", contentRole="detail", shot="detail"), child()],
    [source(sectionRole="product"), child()],
    [source(), child(source="mine")],
    [source(), child(similarSourceBlockId=3)],
])
def test_invalid_relationship_is_rejected_before_canonicalization(blocks):
    with pytest.raises(ValueError, match="invalid_similar_cut_relationship"):
        content_roles.canonicalize_storyboard(blocks, for_storage=True)


def test_each_followup_is_billable_while_exact_duplicate_remains_reused():
    blocks = [source(), {**source(), "id": "duplicate"}, child(), child("second")]
    assert dpj._duplicate_source_indexes(blocks, "top") == [None, 0, None, None]


def test_published_storyboard_actions_are_in_later_editor_history():
    saved = {"version": 1, "rootAssetId": "root-asset", "sourceAssetId": "root-asset",
             "sourceBlockId": "original", "plan": plan("moment_1")}
    conn = Connection([{"id": "asset:child-asset", "similar_cut": saved}])
    history = asyncio.run(repo.list_similar_cut_plans(conn, "u1", "p1", "root-asset"))
    assert history[0]["plan"]["actionKey"] == "moment_1"
    sql, params = conn.calls[0]
    assert "from assets" in sql and "deleted_at is null" in sql and "source = 'ai'" in sql
    assert "metadata->'similar_cut'->>'sourceBlockId'" in sql
    assert params.count("u1") == params.count("p1") == params.count("root-asset") == 2


def test_save_relationship_is_persisted_with_latest_source_recipe(client, make_token, monkeypatch):
    seen = {}

    async def project(*args):
        return {"id": "p1"}

    async def save(conn, uid, pid, blocks, base_revision=None):
        seen["blocks"] = blocks
        return 1

    monkeypatch.setattr(routes.repo, "get_project", project)
    monkeypatch.setattr(routes.repo, "save_storyboard", save)
    patch_route_db(monkeypatch, routes)
    response = client.put("/v1/projects/p1/storyboard", headers=auth_headers(make_token),
                          json=[source(colorId="black"), child(colorId="wrong")])
    assert response.status_code == 200
    assert seen["blocks"][1]["similarSourceBlockId"] == "original"
    assert seen["blocks"][1]["colorId"] == "black"


def test_save_missing_source_is_400_without_persisting(client, make_token, monkeypatch):
    async def project(*args):
        return {"id": "p1"}

    async def forbidden(*args, **kwargs):
        pytest.fail("A broken relationship must not be persisted")

    monkeypatch.setattr(routes.repo, "save_storyboard", forbidden)
    monkeypatch.setattr(routes.repo, "get_project", project)
    patch_route_db(monkeypatch, routes)
    response = client.put("/v1/projects/p1/storyboard", headers=auth_headers(make_token), json=[child()])
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_similar_cut_relationship"


def setup_generation(monkeypatch, *, source_failure=False, child_failure=False):
    seen = {"generate": [], "plans": [], "vary": [], "events": [], "connections": 0, "saved_plans": {}}

    class Pool:
        @contextlib.asynccontextmanager
        async def connection(self):
            seen["connections"] += 1
            try:
                yield FakeConn()
            finally:
                seen["connections"] -= 1

    async def generate(settings, gemini, block, product, images, **kwargs):
        assert seen["connections"] == 0
        seen["generate"].append(block["id"])
        if source_failure:
            raise GeminiError("source unavailable")
        return b"GENERATED-ORIGINAL", "image/png"

    async def next_plan(settings, image, **kwargs):
        assert seen["connections"] == 0
        seen["plans"].append((image.data, copy.deepcopy(kwargs["history"])))
        return plan("moment_" + str(len(seen["plans"])))

    async def vary(settings, gemini, image, changes, cut_type, **kwargs):
        assert seen["connections"] == 0
        seen["vary"].append((image.data, kwargs["similar_plan"]))
        if child_failure:
            raise GeminiError("child unavailable", billable=True)
        return ("CHILD-" + kwargs["similar_plan"]["actionKey"]).encode(), "image/png"

    async def emit(pool, jid, event, payload):
        seen["events"].append((event, payload))

    async def intent(*args, **kwargs):
        return "intent"

    async def saved(conn, uid, pid, jid, block_id, fingerprint):
        return seen["saved_plans"].get((block_id, fingerprint))

    async def reserve(conn, *, block_id, metadata, **kwargs):
        seen["saved_plans"][(block_id, metadata["fingerprint"])] = metadata
        return metadata

    monkeypatch.setattr(dpj.cut_generator, "generate", generate)
    monkeypatch.setattr(similar_cut, "plan_next", next_plan)
    monkeypatch.setattr(cut_variator, "generate", vary)
    monkeypatch.setattr(dpj, "_emit", emit)
    monkeypatch.setattr(repo, "create_ai_output_cleanup_intent", intent)
    monkeypatch.setattr(repo, "get_storyboard_similar_cut_plan", saved)
    monkeypatch.setattr(repo, "reserve_storyboard_similar_cut_plan", reserve)
    app = fake_worker_app(make_settings(gemini_api_key="x", r2_bucket="b", detail_cut_concurrency=1,
                                        detail_cut_max_attempts=1, detail_cut_checkpoint_enabled=True), r2=_R2())
    app.state.pool = Pool()
    return app, seen


def test_followups_wait_for_source_and_use_original_bytes_with_distinct_history(monkeypatch):
    app, seen = setup_generation(monkeypatch)
    # Dependency order must work even if section ordering places a child before its source.
    results, assets, *_ = asyncio.run(dpj._gen_cuts(
        app, worker_job(), [item(child()), item(source()), item(child("second"))],
        {"clothingType": "top"}, {}))
    assert seen["generate"] == ["original"]
    assert [p[0] for p in seen["plans"]] == [b"GENERATED-ORIGINAL", b"GENERATED-ORIGINAL"]
    assert seen["plans"][0][1] == []
    assert seen["plans"][1][1][0]["plan"]["actionKey"] == "moment_1"
    assert [v[0] for v in seen["vary"]] == [b"GENERATED-ORIGINAL", b"GENERATED-ORIGINAL"]
    assert [r["blockId"] for r in results] == ["next", "original", "second"]
    root_asset = assets[1]
    assert app.state.r2.get_bytes(root_asset["key"]) == b"GENERATED-ORIGINAL"
    for asset in (assets[0], assets[2]):
        lineage = asset["metadata"]["similar_cut"]
        assert lineage["version"] == 1
        assert lineage["rootAssetId"] == lineage["sourceAssetId"] == root_asset["asset_id"]
        assert lineage["sourceBlockId"] == "original"


def test_source_failure_never_generates_a_child_or_an_ordinary_fallback(monkeypatch):
    app, seen = setup_generation(monkeypatch, source_failure=True)
    results, assets, *_ = asyncio.run(dpj._gen_cuts(
        app, worker_job(), [item(child()), item(source())], {"clothingType": "top"}, {}))
    assert results == assets == []
    assert seen["generate"] == ["original"]
    assert seen["plans"] == seen["vary"] == []
    assert any(p.get("blockId") == "next" and p.get("status") == "cut_failed"
               for kind, p in seen["events"])


def test_retry_reuses_original_and_plan_after_child_failure_then_reuses_child_output(monkeypatch):
    app, seen = setup_generation(monkeypatch, child_failure=True)
    db = _IntentDB()
    db.install(monkeypatch)
    prepared = [item(source()), item(child())]

    async def run(jid):
        job = {**worker_job(), "id": jid}
        store = await cut_checkpoints.CutCheckpointStore.open(app, job, identity="NONE", auto_named=True)
        output = await dpj._gen_cuts(app, job, prepared, {"clothingType": "top", "name": jid}, {}, cut_checkpoints=store)
        db.error_jobs.add(jid)
        return output, store

    (first, first_store) = asyncio.run(run("j1"))
    assert [r["blockId"] for r in first[0]] == ["original"]
    assert first_store.retained_final_keys(["original"])
    assert len(seen["plans"]) == len(seen["vary"]) == 1

    async def successful(settings, gemini, image, changes, cut_type, **kwargs):
        seen["vary"].append((image.data, kwargs["similar_plan"]))
        return b"FINISHED-CHILD", "image/png"

    monkeypatch.setattr(cut_variator, "generate", successful)
    second, _ = asyncio.run(run("j2"))
    assert [r["blockId"] for r in second[0]] == ["original", "next"]
    assert seen["generate"] == ["original"]
    assert len(seen["plans"]) == 1
    assert seen["vary"][1][1] == seen["vary"][0][1]
    third, _ = asyncio.run(run("j3"))
    assert len(third[1]) == 2
    assert len(seen["plans"]) == 1 and len(seen["vary"]) == 2
    assert third[1][1]["metadata"]["similar_cut"]["rootAssetId"] == third[1][0]["asset_id"]
    assert app.state.r2.get_bytes(third[1][1]["key"]) == b"FINISHED-CHILD"


@pytest.mark.parametrize("later_source", ["original", "alias"])
@pytest.mark.parametrize("changed_context", [False, True])
def test_retry_missing_earlier_plan_uses_only_source_bound_saved_sibling_actions(
        monkeypatch, later_source, changed_context):
    app, seen = setup_generation(monkeypatch)
    db = _IntentDB()
    db.install(monkeypatch)
    planned = []

    async def next_plan(settings, image, **kwargs):
        planned.append(copy.deepcopy(kwargs["history"]))
        if len(planned) == 1:
            raise similar_cut.VisionError("temporary planning failure")
        taken = {entry["plan"]["actionKey"] for entry in kwargs["history"]}
        return plan("shoulder_relaxed" if "hand_near_chin" in taken else "hand_near_chin")

    monkeypatch.setattr(similar_cut, "plan_next", next_plan)
    prepared = [item(source())]
    if later_source == "alias":
        prepared.append(item(source(id="alias")))
    prepared.extend([item(child("first")), item(child("second", similarSourceBlockId=later_source))])

    async def run(jid):
        job = {**worker_job(), "id": jid}
        store = await cut_checkpoints.CutCheckpointStore.open(app, job, identity="NONE")
        context = {"name": "changed garment context"} if changed_context and jid == "j2" else {}
        return await dpj._gen_cuts(
            app, job, prepared, {"clothingType": "top"}, {}, cut_checkpoints=store, similar_context=context)

    first = asyncio.run(run("j1"))
    assert [r["blockId"] for r in first[0]] == (["original", "alias", "second"]
                                              if later_source == "alias" else ["original", "second"])
    db.error_jobs.add("j1")
    retry = asyncio.run(run("j2"))
    assert [r["blockId"] for r in retry[0]] == (["original", "alias", "first", "second"]
                                              if later_source == "alias" else ["original", "first", "second"])
    _original_asset, first_asset, second_asset = retry[1]
    assert first_asset["metadata"]["similar_cut"]["plan"]["actionKey"] == (
        "hand_near_chin" if changed_context else "shoulder_relaxed")
    assert second_asset["metadata"]["similar_cut"]["plan"]["actionKey"] == (
        "shoulder_relaxed" if changed_context else "hand_near_chin")
    assert app.state.r2.get_bytes(second_asset["key"]) == (
        b"CHILD-shoulder_relaxed" if changed_context else b"CHILD-hand_near_chin")
    assert seen["generate"] == ["original"]
    assert len(seen["vary"]) == (3 if changed_context else 2)
    reused_second = any(p.get("blockId") == "second" and p.get("status") == "cut_checkpoint_reused"
                        for kind, p in seen["events"])
    assert reused_second is (not changed_context)


@pytest.mark.parametrize("failure", [None, "source", "child"])
def test_worker_dependency_finalization_and_credits_use_existing_policy(monkeypatch, failure):
    app, seen = setup_generation(monkeypatch, source_failure=failure == "source", child_failure=failure == "child")
    db = _IntentDB()
    db.install(monkeypatch)

    async def project(*args):
        return {"copywriting": False}

    async def storyboard(*args):
        return [source(), child()]

    async def product(*args):
        return {"clothingType": "top", "name": "known shirt", "colors": [{
            "id": "base", "isBase": True, "images": [{"slot": "Front", "id": "garment"}]}]}

    async def analysis(*args):
        return {}

    async def asset(*args):
        return {"mime_type": "image/png", "r2_key": "garment-input"}

    async def success(conn, **kwargs):
        seen["success"] = kwargs
        return {"editor_blocks": kwargs["editor_blocks"], "available": 0}

    async def failed(conn, **kwargs):
        seen["failure"] = kwargs
        return True

    for name, fn in (("get_project", project), ("get_storyboard", storyboard), ("get_product", product),
                     ("get_analysis", analysis), ("get_asset_for_user", asset),
                     ("finalize_detail_page_success", success), ("finalize_detail_page_failure", failed)):
        monkeypatch.setattr(repo, name, fn)
    app.state.r2.objects["garment-input"] = (b"GARMENT-INPUT", "image/png", None)
    job = {**worker_job(credits_reserved=95), "metadata": {"aiCount": 2, "minCuts": 5, "perCutCost": 19}}
    asyncio.run(dpj.run_detail_page_job(app, job))
    assert seen["generate"] == ["original"]
    if failure is None:
        assert "failure" not in seen
        assert seen["success"]["charge"] == 95
        assert len(seen["success"]["cut_assets"]) == 2
    elif failure == "source":
        assert "success" not in seen
        assert seen["failure"]["reserved"] == 95
        assert seen["failure"]["metadata"]["error"] == "all_cuts_failed"
        assert not seen["plans"] and not seen["vary"]
        child_event = next(p for et, p in seen["events"] if p.get("blockId") == "next" and p.get("status") == "cut_failed")
        assert child_event["reason"] == "similar_cut_source_failed"
        assert child_event["retryable"] is False
    else:
        assert "failure" not in seen
        assert seen["success"]["charge"] == 76
        partial = seen["success"]["metadata"]["partial"]
        assert partial["completedCuts"] == 1 and partial["creditsNotCharged"] == 19
        assert partial["failedCuts"] == [{"blockId": "next", "reason": "temporary_error", "retryable": True}]
        child_slot = next(el for eb in seen["success"]["editor_blocks"] for el in eb["elements"] if el.get("sourceBlockId") == "next" and el["type"] == "image")
        assert child_slot["src"] is None and child_slot["genFailed"] is True


def test_reservation_counts_seven_followups_without_duplicate_discount(client, make_token, monkeypatch):
    captured = {}

    async def project(*args):
        return {"id": "p1"}

    async def storyboard(*args):
        return [source(), {**source(), "id": "duplicate"}, *[child(f"child-{i}") for i in range(7)]]

    async def empty(*args):
        return {}

    async def create(conn, **kwargs):
        captured.update(kwargs)
        return {"id": "job", "status": "pending", "credits_reserved": kwargs["credits_reserved"]}, True

    async def reserve(conn, uid, amount):
        captured["reserved"] = amount
        return 0

    monkeypatch.setattr(repo, "get_project", project)
    monkeypatch.setattr(repo, "get_storyboard", storyboard)
    monkeypatch.setattr(repo, "get_analysis", empty)
    monkeypatch.setattr(repo, "get_product", empty)
    monkeypatch.setattr(repo, "get_editor_blocks", empty)
    monkeypatch.setattr(repo, "create_job", create)
    monkeypatch.setattr(repo, "reserve_credits", reserve)
    patch_route_db(monkeypatch, routes)
    response = client.post("/v1/projects/p1/detail-page:generate", headers=auth_headers(make_token))
    assert response.status_code == 202, response.text
    assert captured["metadata"]["aiCount"] == 8
    assert captured["reserved"] == captured["credits_reserved"] == 152


@pytest.mark.parametrize("checkpoint_enabled", [False, True])
def test_mandatory_plan_write_failure_stops_only_the_child_before_billing(monkeypatch, checkpoint_enabled):
    app, seen = setup_generation(monkeypatch)
    app.state.settings = make_settings(detail_cut_checkpoint_enabled=checkpoint_enabled)

    async def failed_write(*args, **kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(repo, "reserve_storyboard_similar_cut_plan", failed_write)
    results, assets, *_ = asyncio.run(dpj._gen_cuts(
        app, worker_job(), [item(source()), item(child())], {"clothingType": "top"}, {}))
    assert [r["blockId"] for r in results] == ["original"]
    assert len(assets) == 1 and not seen["vary"]


def test_saved_plan_is_lease_guarded_and_merges_one_child_key():
    saved = {"fingerprint": "f" * 64, "sourceBlockId": "original", "plan": plan("moment")}
    conn = Connection({"saved": None}, {"saved": saved})
    result = asyncio.run(repo.reserve_storyboard_similar_cut_plan(
        conn, user_id="u1", project_id="p1", job_id="j1", lease_token="lease",
        block_id="next", metadata=saved))
    assert result == saved
    select_sql, select_params = conn.calls[0]
    assert "for update" in select_sql and "status = 'running'" in select_sql and "locked_by = %s" in select_sql
    assert select_params[-4:] == ("j1", "u1", "p1", "lease")
    update_sql, update_params = conn.calls[1]
    assert "jsonb_set" in update_sql and "metadata->'storyboardSimilarPlans'" in update_sql
    assert update_params[0].obj == {"next": saved}


def test_lost_lease_never_saves_a_storyboard_plan():
    conn = Connection(None)
    result = asyncio.run(repo.reserve_storyboard_similar_cut_plan(
        conn, user_id="u1", project_id="p1", job_id="j1", lease_token="old",
        block_id="next", metadata={"fingerprint": "f" * 64, "sourceBlockId": "original", "plan": plan("moment")}))
    assert result is None
    assert len(conn.calls) == 1


def test_saved_storyboard_plan_lookup_scopes_retry_and_source_bytes():
    saved = {"fingerprint": "f" * 64, "sourceBlockId": "original", "plan": plan("moment")}
    conn = Connection({"saved": saved})
    result = asyncio.run(repo.get_storyboard_similar_cut_plan(conn, "u1", "p1", "j1", "next", "f" * 64))
    assert result == saved
    sql, params = conn.calls[0]
    assert "user_id = %s" in sql and "project_id = %s" in sql and "kind = 'detail_page'" in sql
    assert "status = 'error'" in sql and "fingerprint" in sql
    assert "f" * 64 in params and "next" in params


def test_optional_checkpoints_off_still_persists_plan_before_variation(monkeypatch):
    app, seen = setup_generation(monkeypatch)
    app.state.settings = make_settings(detail_cut_checkpoint_enabled=False)
    committed = []

    class Conn(FakeConn):
        async def commit(self):
            committed.append(copy.deepcopy(seen["saved_plans"]))

    class Pool:
        @contextlib.asynccontextmanager
        async def connection(self):
            seen["connections"] += 1
            try:
                yield Conn()
            finally:
                seen["connections"] -= 1

    async def vary(settings, gemini, image, changes, cut_type, **kwargs):
        assert seen["connections"] == 0
        assert any(records for records in committed)
        assert list(seen["saved_plans"].values())[0]["plan"] == kwargs["similar_plan"]
        return b"CHILD", "image/png"

    app.state.pool = Pool()
    monkeypatch.setattr(cut_variator, "generate", vary)
    result = asyncio.run(dpj._gen_cuts(app, worker_job(), [item(source()), item(child())], {"clothingType": "top"}, {}))
    assert len(result[1]) == 2


def test_independent_roots_keep_separate_plans_and_actions(monkeypatch):
    app, seen = setup_generation(monkeypatch)
    app.state.settings = make_settings(detail_cut_concurrency=2)
    prepared = [item(source()), item(child()), item(source(id="other", colorId="black")),
                item(child("other-child", similarSourceBlockId="other"))]
    result = asyncio.run(dpj._gen_cuts(app, worker_job(), prepared, {"clothingType": "top"}, {}))
    assert len(result[0]) == 4 and len(result[1]) == 4
    assert len(seen["saved_plans"]) == 2
    assert [entry[1] for entry in seen["plans"]] == [[], []]


def test_followups_of_reused_ordinary_duplicates_share_original_action_history(monkeypatch):
    app, seen = setup_generation(monkeypatch)
    prepared = [item(source()), item(source(id="alias")), item(child()),
                item(child("alias-child", similarSourceBlockId="alias"))]
    results, assets, *_ = asyncio.run(dpj._gen_cuts(app, worker_job(), prepared, {"clothingType": "top"}, {}))
    assert len(results) == 4 and len(assets) == 3
    assert seen["plans"][1][1][0]["plan"]["actionKey"] == "moment_1"
    assert assets[1]["metadata"]["similar_cut"]["rootAssetId"] == assets[2]["metadata"]["similar_cut"]["rootAssetId"]


def test_followup_page_qc_uses_original_product_truth_and_runtime_model(monkeypatch):
    app, seen = setup_generation(monkeypatch)
    app.state.settings = make_settings(page_output_qc_mode="shadow")
    captured = {}

    async def judge(settings, page_plan, images, *, product_truth_refs):
        captured["plan"] = page_plan
        captured["refs"] = product_truth_refs
        return {"passed": True}

    monkeypatch.setattr(dpj.page_output_qc, "judge", judge)
    prepared = [item(source(modelId="resolved-model")), (child(modelId="stale"), [], "", False, [])]
    result = asyncio.run(dpj._gen_cuts(app, worker_job(), prepared, {"clothingType": "top"}, {}))
    assert len(result[1]) == 2
    assert captured["plan"][1]["productTruthIndexes"] == [0]
    assert captured["plan"][1]["modelId"] == "resolved-model"
    assert captured["refs"][0].data == b"EXAMPLE-AND-GARMENT"


def test_reclaimed_job_keeps_saved_action_without_planning_again():
    saved = {"fingerprint": "f" * 64, "sourceBlockId": "original", "plan": plan("saved")}
    conn = Connection({"saved": saved})
    result = asyncio.run(repo.reserve_storyboard_similar_cut_plan(
        conn, user_id="u1", project_id="p1", job_id="j1", lease_token="lease",
        block_id="next", metadata={**saved, "plan": plan("proposed")}))
    assert result["plan"] == plan("saved")
    assert len(conn.calls) == 1


def test_condition_plans_keep_fields_through_reservation_assets_and_checkpoint_retry(monkeypatch):
    from test_similar_cut_conditions import new_plan, choice
    app, seen = setup_generation(monkeypatch)
    db = _IntentDB()
    db.install(monkeypatch)
    actions = []

    async def next_plan(settings, image, **kwargs):
        history = copy.deepcopy(kwargs["history"])
        actions.append(history)
        return new_plan(choice=choice(selected="imageRight" if history else "imageLeft"))

    monkeypatch.setattr(similar_cut, "plan_next", next_plan)
    prepared = [item(source()), item(child("first")), item(child("second"))]

    async def run(jid):
        job = {**worker_job(), "id": jid}
        store = await cut_checkpoints.CutCheckpointStore.open(app, job, identity="NONE")
        return await dpj._gen_cuts(app, job, prepared, {"clothingType": "top"}, {}, cut_checkpoints=store)

    first = asyncio.run(run("j1"))
    assert [r["blockId"] for r in first[0]] == ["original", "first", "second"]
    assert actions[1][0]["plan"] == new_plan()
    for generated, selected in zip(first[1][1:], ("imageLeft", "imageRight")):
        expected = new_plan(choice=choice(selected=selected))
        assert generated["metadata"]["similar_cut"]["plan"] == expected
    assert len(seen["saved_plans"]) == 2
    assert all("fabricResponse" in record["plan"] for record in seen["saved_plans"].values())
    db.error_jobs.add("j1")
    resumed = asyncio.run(run("j2"))
    assert len(actions) == len(seen["vary"]) == 2
    assert [a["metadata"]["similar_cut"]["plan"] for a in resumed[1][1:]] == [
        a["metadata"]["similar_cut"]["plan"] for a in first[1][1:]]


@pytest.mark.parametrize("prompt_name", ["similar_cut_plan_v2.txt", "similar_cut_generate_v2.txt"])
def test_storyboard_similar_fingerprint_tracks_v2_prompt_files(monkeypatch, tmp_path, prompt_name):
    from test_similar_cut_conditions import new_plan
    app, seen = setup_generation(monkeypatch)
    for path in similar_cut._PROMPTS.glob("similar_cut_*.txt"):
        (tmp_path / path.name).write_bytes(path.read_bytes())
    monkeypatch.setattr(similar_cut, "_PROMPTS", tmp_path)
    calls = []
    async def next_plan(*args, **kwargs):
        calls.append(1)
        return new_plan()
    monkeypatch.setattr(similar_cut, "plan_next", next_plan)
    prepared = [item(source()), item(child())]
    for iteration in range(2):
        results, *_ = asyncio.run(dpj._gen_cuts(app, worker_job(), prepared, {"clothingType": "top"}, {}))
        assert len(results) == 2
        (tmp_path / prompt_name).write_text("changed v2 prompt")
    assert len(calls) == 2
    assert len(seen["saved_plans"]) == 2


@pytest.mark.parametrize("retry", ["same_job", "error_job", "current_before_other_v2", "changed_context"])
def test_v1_saved_storyboard_plan_and_checkpoint_survive_v2_prompt_upgrade(monkeypatch, retry):
    app, seen = setup_generation(monkeypatch)
    db = _IntentDB()
    db.install(monkeypatch)
    digest = dpj._cut_checkpoints._digest
    legacy_hashes = {
        "similar_cut_plan_v1.txt": "451d642034771085774fcc77a5852a4d73a11e7611af4e5a0470118307783cac",
        "similar_cut_generate_v1.txt": "53436a14e2c54e74da1ef3b1ca4d5f9ec54d761f923d0464b860f7fbf9e749e7",
    }
    def pre_upgrade_digest(value):
        if isinstance(value, dict) and value.get("family") == "storyboard-similar-v1":
            value = {**value, "prompts": legacy_hashes}
        return digest(value)
    monkeypatch.setattr(dpj._cut_checkpoints, "_digest", pre_upgrade_digest)
    prepared = [item(source()), item(child())]

    async def run(jid, metadata=None, context=None):
        job = {**worker_job(), "id": jid, "metadata": metadata or {}}
        store = await cut_checkpoints.CutCheckpointStore.open(app, job, identity="NONE")
        return await dpj._gen_cuts(app, job, prepared, {"clothingType": "top"}, {},
                                   cut_checkpoints=store, similar_context=context)

    first = asyncio.run(run("j1"))
    assert len(first[0]) == 2
    legacy_record = next(iter(seen["saved_plans"].values()))
    monkeypatch.setattr(dpj._cut_checkpoints, "_digest", digest)
    metadata = {"storyboardSimilarPlans": {"next": legacy_record}} if retry in {"same_job", "current_before_other_v2"} else {}
    if retry == "current_before_other_v2":
        lookup = repo.get_storyboard_similar_cut_plan
        reserve = repo.reserve_storyboard_similar_cut_plan
        async def competing_lookup(conn, uid, pid, jid, block_id, fingerprint):
            if fingerprint != legacy_record["fingerprint"]:
                return {"fingerprint": fingerprint, "sourceBlockId": "original", "plan": plan("other_job")}
            return await lookup(conn, uid, pid, jid, block_id, fingerprint)
        async def fenced_reserve(conn, *, metadata, **kwargs):
            if metadata["fingerprint"] != legacy_record["fingerprint"]:
                raise ValueError("similar_cut_lineage_changed")
            return await reserve(conn, metadata=metadata, **kwargs)
        monkeypatch.setattr(repo, "get_storyboard_similar_cut_plan", competing_lookup)
        monkeypatch.setattr(repo, "reserve_storyboard_similar_cut_plan", fenced_reserve)
    db.error_jobs.add("j1")
    resumed = asyncio.run(run("j2" if retry in {"error_job", "changed_context"} else "j1", metadata,
                             {"name": "changed garment context"} if retry == "changed_context" else None))
    assert [r["blockId"] for r in resumed[0]] == ["original", "next"]
    if retry == "changed_context":
        assert len(seen["plans"]) == len(seen["vary"]) == 2
        assert len(seen["saved_plans"]) == 2
        return
    assert len(seen["plans"]) == len(seen["vary"]) == 1
    assert resumed[1][1]["metadata"]["similar_cut"]["plan"] == first[1][1]["metadata"]["similar_cut"]["plan"]
    assert len(seen["saved_plans"]) == 1
