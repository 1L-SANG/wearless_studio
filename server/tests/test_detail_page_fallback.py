"""ADR-0017 runtime substitutions use local IO doubles and real worker contracts."""
import asyncio
import contextlib
from copy import deepcopy
import logging
import types

import pytest

from app.agents.detail_recommendations import DetailRecommendationError
from app.workers import cut_checkpoints, detail_page_job as dpj
from conftest import FakeConn, make_settings, worker_job
from test_detail_incomplete_cuts_partial import _IntentDB, _R2


def _block(block_id="b1", **kwargs):
    return {"id": block_id, "source": "ai", "cutType": "horizon", "shot": "full",
            "direction": "front", "colorId": "base", **kwargs}


def _item(item_id, category="pants", clothing_type="bottom", **kwargs):
    return {"id": item_id, "category": category, "clothingType": clothing_type, **kwargs}


def _run(monkeypatch, blocks, recommendations=(), *, missing_rows=(), missing_assets=(),
         missing_bytes=(), metadata=None, analysis=None, generate_errors=None, detail_errors=None,
         settings=None, checkpoint_db=None, r2=None, lease_lost=False, job_id="j1",
         model_id=None, checkpoint_base_error=None, job_metadata=None, reserved=None,
         assemble_real=False):
    captured = {"events": [], "generated": {}, "cut_plans": [], "page_plans": [],
                "metadata_lookups": [], "matching_lookups": [], "checkpoint_inputs": [],
                "qc_fit_profiles": {}, "base_reused": False, "generation_attempts": []}
    saved = deepcopy(blocks)
    analysis = deepcopy(analysis or {})
    analysis["matchClothing"] = deepcopy(list(recommendations))
    original_analysis = deepcopy(analysis)
    captured["saved"] = saved
    captured["analysis"] = analysis
    captured["original_analysis"] = original_analysis
    product = {"name": "셔츠", "clothingType": "top", "colors": [{"id": "base", "isBase": True,
               "images": [{"slot": "Front", "id": "product"}]}]}
    active_connections = {"count": 0}

    class Pool:
        @contextlib.asynccontextmanager
        async def connection(self):
            active_connections["count"] += 1
            try:
                yield FakeConn()
            finally:
                active_connections["count"] -= 1

    async def get_project(*args): return {"copywriting": False}
    async def get_storyboard(*args): return saved
    async def get_product(*args): return product
    async def get_analysis(*args): return analysis
    async def matching_asset(conn, item_id, user_id, project_id):
        assert (user_id, project_id) == ("u1", "p1")
        captured["matching_lookups"].append(item_id)
        return None if item_id in missing_rows else f"match:{item_id}"
    async def matching_metadata(conn, item_id, user_id, project_id):
        assert (user_id, project_id) == ("u1", "p1")
        captured["metadata_lookups"].append(item_id)
        return (metadata or {}).get(item_id)
    async def asset(conn, uid, asset_id):
        if asset_id in {f"match:{item_id}" for item_id in missing_assets}:
            return None
        return {"id": asset_id, "mime_type": "image/png", "r2_key": f"k/{asset_id}"}
    async def generate(config, gemini, spec, product, images, **kwargs):
        assert active_connections["count"] == 0
        captured["generation_attempts"].append(spec["id"])
        base = kwargs.get("base_checkpoint")
        if base is not None:
            captured["base_reused"] = base.image is not None
            if base.image is None:
                await base.keep(b"BASE", "image/png")
            if checkpoint_base_error is not None:
                raise checkpoint_base_error
        if spec["id"] in (generate_errors or {}):
            raise generate_errors[spec["id"]]
        captured["generated"][spec["id"]] = {
            "spec": deepcopy(spec), "images": [image.data for image in images],
            "analysis": deepcopy(kwargs.get("analysis")), "manifest": kwargs.get("manifest"),
            "prompt": dpj.cut_generator.build_prompt(spec, product,
                analysis=kwargs.get("analysis"), manifest=kwargs.get("manifest"))}
        return f"OUTPUT-{spec['id']}".encode(), "image/png"
    async def detail_generate(app, job, block, product, analysis, **kwargs):
        raise detail_errors[block["id"]]
    async def cut_qc(config, plan, references, output, **kwargs):
        captured["cut_plans"].append(plan.to_dict())
        return {"overall": "PASS"}
    async def page_qc(config, plan, outputs, **kwargs):
        captured["page_plans"] = deepcopy(plan)
        return {"overall": "PASS"}
    original_assemble = dpj.page_assembler.assemble
    def assemble(storyboard, cut_results, *args, **kwargs):
        captured["cut_results"] = cut_results
        if assemble_real:
            return original_assemble(storyboard, cut_results, *args, **kwargs)
        return [{"id": "editor", "elements": []}]
    async def finalize(conn, **kwargs):
        captured["success"] = kwargs
        return None if lease_lost else {"editor_blocks": kwargs["editor_blocks"], "available": 99}
    async def failure(conn, **kwargs):
        captured["failure"] = kwargs
        return True
    async def emit(pool, job_id, event_type, payload):
        captured["events"].append((event_type, payload))
    for name, fn in [("get_project", get_project), ("get_storyboard", get_storyboard),
                     ("get_product", get_product), ("get_analysis", get_analysis),
                     ("get_matching_item_asset", matching_asset),
                     ("get_matching_item_metadata", matching_metadata),
                     ("get_asset_for_user", asset), ("finalize_detail_page_success", finalize),
                     ("finalize_detail_page_failure", failure)]:
        monkeypatch.setattr(dpj.repo, name, fn)
    monkeypatch.setattr(dpj.cut_generator, "generate", generate)
    if detail_errors:
        monkeypatch.setattr(dpj.detail_shot_runtime, "generate", detail_generate)
    monkeypatch.setattr(dpj.cut_output_qc, "verdict", cut_qc)
    original_compile = dpj.cut_plan.compile_cut_plan
    def compile_plan(spec, clothing_type, **kwargs):
        captured["qc_fit_profiles"][tuple(spec.get("matchIds") or []) + (spec["direction"],)] = deepcopy(kwargs.get("fit_profile"))
        return original_compile(spec, clothing_type, **kwargs)
    monkeypatch.setattr(dpj.cut_plan, "compile_cut_plan", compile_plan)
    monkeypatch.setattr(dpj.page_output_qc, "judge", page_qc)
    monkeypatch.setattr(dpj.page_assembler, "assemble", assemble)
    monkeypatch.setattr(dpj, "_emit", emit)
    calculate = dpj.calculate_detail_charge
    def capture_charge(**kwargs):
        captured["charge_metadata"] = deepcopy(kwargs["metadata"])
        return calculate(**kwargs)
    monkeypatch.setattr(dpj, "calculate_detail_charge", capture_charge)
    if checkpoint_db is not None:
        checkpoint_db.install(monkeypatch)
        original_for_cut = cut_checkpoints.CutCheckpointStore.for_cut
        async def for_cut(store, block, config, product, images, kwargs, **extra):
            captured["checkpoint_inputs"].append((deepcopy(block), deepcopy(kwargs)))
            return await original_for_cut(store, block, config, product, images, kwargs, **extra)
        monkeypatch.setattr(cut_checkpoints.CutCheckpointStore, "for_cut", for_cut)
    r2 = r2 or _R2()
    ids = {row["id"] for row in recommendations} | {
        match_id for block in blocks for match_id in block.get("matchIds", [])}
    r2.objects.setdefault("k/product", (b"PRODUCT", "image/png", None))
    for item_id in ids - set(missing_bytes):
        r2.objects.setdefault(f"k/match:{item_id}", (f"MATCH-{item_id}".encode(), "image/png", None))
    config = make_settings(gemini_api_key="x", r2_bucket="b", detail_cut_max_attempts=1,
        cut_output_qc_mode="shadow", page_output_qc_mode="shadow",
        detail_cut_checkpoint_enabled=checkpoint_db is not None, **(settings or {}))
    app = types.SimpleNamespace(state=types.SimpleNamespace(settings=config, pool=Pool(), r2=r2,
                                                          gemini=types.SimpleNamespace()))
    job = worker_job(credits_reserved=19 * len(blocks) if reserved is None else reserved)
    if model_id is not None:
        job["payload"]["modelId"] = model_id
    job.update(id=job_id, metadata={"perCutCost": 19, **(job_metadata or {})})
    original_job_metadata = deepcopy(job["metadata"])
    asyncio.run(dpj.run_detail_page_job(app, job))
    assert saved == blocks
    assert analysis == original_analysis
    assert job["metadata"] == original_job_metadata
    return captured


@pytest.mark.parametrize("unavailable", ["row", "asset", "bytes"])
def test_missing_matching_is_substituted_and_charged_with_runtime_evidence(monkeypatch, unavailable, caplog):
    blocks = [_block(matchIds=["missing"])]
    kwargs = {f"missing_{'rows' if unavailable == 'row' else 'assets' if unavailable == 'asset' else 'bytes'}": ["missing"]}
    with caplog.at_level(logging.INFO, logger=dpj.log.name):
        result = _run(monkeypatch, blocks, [_item("missing"), _item("replacement")], **kwargs)
    assert result["success"]["charge"] == 19
    generated = result["generated"]["b1"]
    assert generated["spec"]["matchIds"] == ["replacement"]
    assert generated["images"] == [b"PRODUCT", b"MATCH-replacement"]
    assert generated["manifest"].count("MATCHING") == 1
    assert result["cut_plans"][0]["storyboard"]["matching"] == ["replacement"]
    assert result["page_plans"][0]["matchingIds"] == ["replacement"]
    assert result["success"]["cut_assets"][0]["metadata"]["fallback"] == [
        {"kind": "matching_substituted", "missingMatchId": "missing", "substituteMatchId": "replacement"}]
    assert result["metadata_lookups"] == []
    assert len([record for record in caplog.records if "matching substituted" in record.message]) == 1


@pytest.mark.parametrize("recommendations,missing_rows,missing_bytes,expected", [
    ([_item("missing"), _item("different", "skirt"), _item("same")], ["missing"], [], "same"),
    ([_item("missing"), _item("same"), _item("different", "skirt")], ["missing", "same"], [], "different"),
    ([_item("missing"), _item("same"), _item("different", "skirt")], ["missing"], ["same"], "different"),
    ([_item("missing"), _item("custom", isCustom=True), _item("incompatible", isCompatible=False),
      _item("top", clothing_type="top"), _item("same")], ["missing"], [], "same"),
])
def test_matching_candidates_follow_saved_category_then_type_order(monkeypatch, recommendations,
                                                                  missing_rows, missing_bytes, expected):
    result = _run(monkeypatch, [_block(matchIds=["missing"])], recommendations,
                  missing_rows=missing_rows, missing_bytes=missing_bytes)
    assert result["generated"]["b1"]["spec"]["matchIds"] == [expected]


def test_matching_metadata_uses_short_repo_lookup_when_saved_original_is_absent(monkeypatch):
    result = _run(monkeypatch, [_block(matchIds=["missing"])], [_item("replacement")],
                  missing_rows=["missing"], metadata={"missing": {"clothing_type": "bottom", "category": "pants"}})
    assert result["generated"]["b1"]["spec"]["matchIds"] == ["replacement"]
    assert result["metadata_lookups"] == ["missing"]


@pytest.mark.parametrize("recommendations,missing_rows,missing_bytes", [
    ([_item("missing"), _item("already")], ["missing"], []),
    ([_item("missing"), _item("already"), _item("unavailable")], ["missing", "unavailable"], []),
    ([_item("missing"), _item("already"), _item("unavailable")], ["missing"], ["unavailable"]),
    ([_item("already")], ["missing"], []),
])
def test_matching_exhaustion_preserves_empty_cut_with_retryable_reason(monkeypatch, recommendations,
                                                                     missing_rows, missing_bytes):
    result = _run(monkeypatch, [_block(matchIds=["missing", "already"])], recommendations,
                  missing_rows=missing_rows, missing_bytes=missing_bytes)
    assert result["generated"] == {}
    assert result["failure"]["code"] == "all_cuts_failed"
    assert [payload for _, payload in result["events"] if payload.get("status") == "cut_failed"] == [
        {"blockId": "b1", "status": "cut_failed", "reason": "matching_asset_unavailable", "retryable": True}]


def test_same_missing_matching_reuses_substitute_and_only_affected_cuts_lose_matching_fit(monkeypatch):
    fit = {"version": 2, "category": "top", "gender": "women", "axes": {"length": "cropped"},
           "matchCut": "wide", "matchingFit": {"clothingId": "missing", "fitCategory": "pants",
                                               "axes": {"cut": "wide"}}}
    blocks = [_block("b1", matchIds=["missing"]),
              _block("b2", matchIds=["missing"], direction="back"),
              _block("b3", matchIds=["replacement"], direction="side")]
    result = _run(monkeypatch, blocks, [_item("missing"), _item("replacement")],
                  missing_rows=["missing"], analysis={"fitProfile": fit})
    for block_id in ("b1", "b2"):
        generated = result["generated"][block_id]
        assert generated["spec"]["matchIds"] == ["replacement"]
        assert generated["analysis"]["fitProfile"] == {key: value for key, value in fit.items()
                                                       if key not in {"matchCut", "matchingFit"}}
        assert "matching bottom" not in generated["prompt"]
    assert result["generated"]["b3"]["analysis"]["fitProfile"] == fit
    assert result["qc_fit_profiles"][("replacement", "front")] == {key: value for key, value in fit.items()
                                                                     if key not in {"matchCut", "matchingFit"}}
    assert result["qc_fit_profiles"][("replacement", "back")] == result["qc_fit_profiles"][("replacement", "front")]
    assert result["qc_fit_profiles"][("replacement", "side")] == fit
    assert "matching bottom" in result["generated"]["b3"]["prompt"]
    assert result["success"]["charge"] == 57


def test_two_missing_ids_in_one_cut_get_distinct_substitutes(monkeypatch):
    result = _run(monkeypatch, [_block(matchIds=["one", "two"])],
        [_item("one"), _item("two"), _item("first"), _item("second")], missing_rows=["one", "two"])
    assert result["generated"]["b1"]["spec"]["matchIds"] == ["first", "second"]
    assert result["generated"]["b1"]["images"] == [b"PRODUCT", b"MATCH-first", b"MATCH-second"]


@pytest.mark.parametrize("reverse", [False, True])
def test_shared_matching_replacement_is_admissible_for_every_affected_cut(monkeypatch, reverse):
    blocks = [_block("b1", matchIds=["missing"]), _block("b2", matchIds=["missing", "replacement"])]
    if reverse:
        blocks.reverse()
    result = _run(monkeypatch, blocks,
        [_item("missing"), _item("replacement"), _item("another")], missing_rows=["missing"])
    assert result["generated"]["b1"]["spec"]["matchIds"] == ["another"]
    assert result["generated"]["b2"]["spec"]["matchIds"] == ["another", "replacement"]
    assert result["success"]["charge"] == 38
    assert "partial" not in result["success"]["metadata"]


def test_shared_matching_replacement_exhaustion_does_not_duplicate_attachments(monkeypatch):
    result = _run(monkeypatch, [_block("b1", matchIds=["missing"]),
        _block("b2", matchIds=["missing", "replacement"])],
        [_item("missing"), _item("replacement"), _item("another")], missing_rows=["missing", "another"])
    assert result["generated"] == {}
    assert result["failure"]["code"] == "all_cuts_failed"
    assert all(payload["reason"] == "matching_asset_unavailable" for _, payload in result["events"]
               if payload.get("status") == "cut_failed")


@pytest.mark.parametrize("fail_substituted", [False, True])
def test_matching_convergence_does_not_fold_cuts_with_different_effective_fit(monkeypatch, fail_substituted):
    fit = {"version": 2, "category": "top", "gender": "women", "axes": {"length": "cropped"},
           "matchingFit": {"clothingId": "replacement", "fitCategory": "pants", "axes": {"cut": "wide"}}}
    result = _run(monkeypatch, [_block("b1", matchIds=["missing"]), _block("b2", matchIds=["replacement"])],
        [_item("missing"), _item("replacement")], missing_rows=["missing"], analysis={"fitProfile": fit},
        generate_errors={"b1": ValueError("broken cut")} if fail_substituted else {})
    assert "b2" in result["generated"]
    assert "matching bottom" in result["generated"]["b2"]["prompt"]
    if fail_substituted:
        assert result["success"]["charge"] == 19
        assert result["success"]["metadata"]["partial"]["failedCuts"] == [
            {"blockId": "b1", "reason": "invalid_cut_spec", "retryable": True}]
    else:
        assert set(result["generated"]) == {"b1", "b2"}
        assert "matching bottom" not in result["generated"]["b1"]["prompt"]
        assert len(result["cut_plans"]) == 2
        assert result["success"]["charge"] == 38


@pytest.mark.parametrize("matching_fit", [False, True])
def test_identical_effective_fit_still_folds_matching_convergence(monkeypatch, matching_fit):
    fit = {"version": 2, "category": "top", "gender": "women", "axes": {"length": "cropped"}}
    if matching_fit:
        fit["matchingFit"] = {"clothingId": "missing", "fitCategory": "pants", "axes": {"cut": "wide"}}
    blocks = [_block("b1", matchIds=["missing"]),
              _block("b2", matchIds=["missing" if matching_fit else "replacement"])]
    result = _run(monkeypatch, blocks, [_item("missing"), _item("replacement")],
                  missing_rows=["missing"], analysis={"fitProfile": fit})
    assert list(result["generated"]) == ["b1"]
    assert [row["blockId"] for row in result["cut_results"]] == ["b1", "b2"]
    assert result["success"]["charge"] == 19


@pytest.mark.parametrize("reverse", [False, True])
def test_matching_convergence_keeps_minimum_charge_and_all_card_fallbacks(monkeypatch, reverse):
    blocks = [_block("lost", matchIds=["A"]), _block("intact", matchIds=["B"])]
    if reverse:
        blocks.reverse()
    result = _run(monkeypatch, blocks, [_item("A"), _item("B")], missing_rows=["A"],
                  job_metadata={"aiCount": 2, "minCuts": 5}, reserved=95)
    assert result["generation_attempts"] == [blocks[0]["id"]]
    assert len(result["cut_results"]) == 2
    assert len({cut["imageUrl"] for cut in result["cut_results"]}) == 1
    assert len(result["success"]["cut_assets"]) == 1
    assert result["success"]["charge"] == 95
    assert "partial" not in result["success"]["metadata"]
    assert result["success"]["cut_assets"][0]["metadata"]["fallback"] == [{
        "kind": "matching_substituted", "missingMatchId": "A", "substituteMatchId": "B"}]


@pytest.mark.parametrize("reverse", [False, True])
def test_failed_matching_convergence_keeps_runtime_source_through_partial_publish(monkeypatch, reverse):
    entries = {
        "front-ref": {"cutType": "horizon", "direction": "front", "shot": "full",
                      "all": "front.png", "pose": "front.png"},
        "back-ref": {"cutType": "horizon", "direction": "back", "shot": "full",
                     "all": "back.png", "pose": "back.png"},
    }
    monkeypatch.setattr(dpj.cut_generator, "load_example_asset_registry", lambda: (None, entries))
    async def example(*args, **kwargs):
        return dpj.InlineImage("image/png", b"REFERENCE")
    monkeypatch.setattr(dpj.cut_generator, "load_example_image", example)
    blocks = [_block("lost", matchIds=["A"], exampleId="front-ref", refScope="all"),
              _block("intact", matchIds=["B"], exampleId="front-ref", refScope="all")]
    if reverse:
        blocks.reverse()
    primary, duplicate = blocks[0]["id"], blocks[1]["id"]
    blocks.append(_block("ok", direction="back", exampleId="back-ref", refScope="all"))
    result = _run(monkeypatch, blocks, [_item("A"), _item("B")], missing_rows=["A"],
                  generate_errors={primary: RuntimeError("temporary provider failure")},
                  job_metadata={"aiCount": 3, "minCuts": 5}, reserved=95, assemble_real=True)
    assert result["generation_attempts"] == [primary, "ok"]
    partial = result["success"]["metadata"]["partial"]
    failure = next(item for item in partial["failedCuts"] if item["blockId"] == duplicate)
    assert failure == {"blockId": duplicate, "reason": "duplicate_cut_source_failed",
                       "retryable": False, "sourceBlockId": primary}
    event = next(payload for _, payload in result["events"]
                 if payload.get("status") == "cut_failed" and payload.get("blockId") == duplicate)
    assert event.get("sourceBlockId") == primary
    image = next(element for block in result["success"]["editor_blocks"]
                 for element in block["elements"] if element.get("sourceBlockId") == duplicate)
    assert image.get("genFailSourceBlockId") == primary
    assert image["genFailed"] is True and not image.get("src")
    assert result["success"]["charge"] == 76


@pytest.mark.parametrize("fail_other,expected_charge", [(False, 95), (True, 76)])
def test_matching_convergence_above_minimum_subtracts_only_an_actual_failure(
    monkeypatch, fail_other, expected_charge,
):
    blocks = [_block("lost", matchIds=["A"]), _block("intact", matchIds=["B"])]
    blocks += [_block(f"other-{index}", direction=direction, shot=shot)
               for index, (direction, shot) in enumerate([
                   ("back", "full"), ("side", "full"), ("front", "medium"), ("back", "medium")])]
    result = _run(monkeypatch, blocks, [_item("A"), _item("B")], missing_rows=["A"],
        job_metadata={"aiCount": 6, "minCuts": 5}, reserved=114,
        generate_errors={"other-3": ValueError("invalid independent cut")} if fail_other else {})
    assert len(result["generation_attempts"]) == 5
    assert len(result["success"]["cut_assets"]) == (4 if fail_other else 5)
    assert len(result["cut_results"]) == (5 if fail_other else 6)
    assert result["success"]["charge"] == expected_charge
    assert result["charge_metadata"]["aiCount"] == 5
    if fail_other:
        assert result["success"]["metadata"]["partial"]["failedCuts"] == [{
            "blockId": "other-3", "reason": "invalid_cut_spec", "retryable": True}]


def test_existing_duplicate_keeps_minimum_charge_without_matching_substitution(monkeypatch):
    result = _run(monkeypatch, [_block("original", matchIds=["B"]), _block("copy", matchIds=["B"])],
                  [_item("B")], job_metadata={"aiCount": 1, "minCuts": 5}, reserved=95)
    assert result["generation_attempts"] == ["original"]
    assert len(result["cut_results"]) == 2
    assert result["success"]["charge"] == 95
    assert "fallback" not in result["success"]["cut_assets"][0]["metadata"]


def test_generation_unit_count_never_increases_the_quote_snapshot(monkeypatch):
    seen = []
    calculate = dpj.calculate_detail_charge
    def capture_charge(**kwargs):
        seen.append(deepcopy(kwargs["metadata"]))
        return calculate(**kwargs)
    monkeypatch.setattr(dpj, "calculate_detail_charge", capture_charge)
    result = _run(monkeypatch, [_block("lost", matchIds=["A"]), _block("intact", matchIds=["B"]),
                              _block("back", direction="back")],
                  [_item("A"), _item("B")], missing_rows=["A"],
                  job_metadata={"aiCount": 1, "minCuts": 5}, reserved=95)
    assert len(result["generation_attempts"]) == 2
    assert seen == [{"perCutCost": 19, "aiCount": 1, "minCuts": 5}]
    assert result["success"]["charge"] == 95


@pytest.mark.parametrize("reverse", [False, True])
def test_matching_convergence_checkpoint_keeps_duplicate_card_records_on_reuse(monkeypatch, reverse):
    db, r2 = _IntentDB(), _R2()
    blocks = [_block("lost", matchIds=["A"]), _block("intact", matchIds=["B"])]
    if reverse:
        blocks.reverse()
    recommendations = [_item("A"), _item("B")]
    expected = [{"kind": "matching_substituted", "missingMatchId": "A", "substituteMatchId": "B"}]
    first = _run(monkeypatch, blocks, recommendations, missing_rows=["A"], checkpoint_db=db, r2=r2,
                 lease_lost=True, job_id="old", job_metadata={"aiCount": 2, "minCuts": 5}, reserved=95)
    assert len(first["generation_attempts"]) == 1
    finals = [cut_checkpoints.unpack(value[0])[0] for key, value in r2.objects.items() if key.endswith("/final.bin")]
    assert len(finals) == 1
    assert finals[0]["meta"]["fallback"] == expected
    db.error_jobs.add("old")
    with monkeypatch.context() as second_patch:
        second = _run(second_patch, blocks, recommendations, missing_rows=["A"], checkpoint_db=db, r2=r2,
                      job_id="new", job_metadata={"aiCount": 2, "minCuts": 5}, reserved=95)
    assert second["generation_attempts"] == []
    assert len(second["cut_results"]) == 2
    assert second["success"]["charge"] == 95
    assert second["success"]["cut_assets"][0]["metadata"]["fallback"] == expected


@pytest.mark.parametrize("stage", ["final", "base"])
def test_duplicate_card_fallback_survives_checkpoint_when_that_card_is_removed(monkeypatch, stage):
    db, r2 = _IntentDB(), _R2()
    recommendations = [_item("A"), _item("B")]
    blocks = [_block("intact", matchIds=["B"]), _block("lost", matchIds=["A"])]
    _run(monkeypatch, blocks, recommendations, missing_rows=["A"], checkpoint_db=db, r2=r2,
         lease_lost=True, job_id="old", job_metadata={"aiCount": 2, "minCuts": 5}, reserved=95,
         checkpoint_base_error=dpj.face_identity.FacePassUnavailable("pod_not_ready") if stage == "base" else None)
    db.error_jobs.add("old")
    with monkeypatch.context() as second_patch:
        second = _run(second_patch, blocks[:1], recommendations, checkpoint_db=db, r2=r2,
                      job_id="new", job_metadata={"aiCount": 1, "minCuts": 5}, reserved=95)
    assert second["success"]["charge"] == 95
    assert second["success"]["cut_assets"][0]["metadata"]["fallback"] == [{
        "kind": "matching_substituted", "missingMatchId": "A", "substituteMatchId": "B"}]
    if stage == "final":
        assert second["generation_attempts"] == []
    else:
        assert second["base_reused"] is True


@pytest.mark.parametrize("stage", ["final", "base"])
def test_reused_checkpoint_persists_new_duplicate_card_fallback_through_another_interruption(monkeypatch, stage):
    db, r2 = _IntentDB(), _R2()
    recommendations = [_item("A"), _item("B")]
    original = _block("intact", matchIds=["B"])
    interruption = dpj.face_identity.FacePassUnavailable("pod_not_ready") if stage == "base" else None
    _run(monkeypatch, [original], recommendations, checkpoint_db=db, r2=r2,
         lease_lost=True, job_id="seed", checkpoint_base_error=interruption,
         job_metadata={"aiCount": 1, "minCuts": 5}, reserved=95)
    db.error_jobs.add("seed")
    with monkeypatch.context() as second_patch:
        second = _run(second_patch, [original, _block("lost", matchIds=["A"])], recommendations,
                      missing_rows=["A"], checkpoint_db=db, r2=r2, lease_lost=True, job_id="with-duplicate",
                      checkpoint_base_error=interruption, job_metadata={"aiCount": 2, "minCuts": 5}, reserved=95)
    if stage == "final":
        assert second["generation_attempts"] == []
    else:
        assert second["base_reused"] is True
    db.error_jobs.add("with-duplicate")
    with monkeypatch.context() as third_patch:
        third = _run(third_patch, [original], recommendations, checkpoint_db=db, r2=r2,
                     job_id="last", job_metadata={"aiCount": 1, "minCuts": 5}, reserved=95)
    assert third["success"]["cut_assets"][0]["metadata"]["fallback"] == [{
        "kind": "matching_substituted", "missingMatchId": "A", "substituteMatchId": "B"}]
    assert third["success"]["charge"] == 95
    if stage == "final":
        assert third["generation_attempts"] == []
    else:
        assert third["base_reused"] is True


def test_matching_checkpoint_uses_runtime_ids_fit_and_keeps_fallback_on_final_reuse(monkeypatch):
    db, r2 = _IntentDB(), _R2()
    blocks = [_block(matchIds=["missing"])]
    recommendations = [_item("missing"), _item("replacement")]
    first = _run(monkeypatch, blocks, recommendations, missing_rows=["missing"],
                 checkpoint_db=db, r2=r2, lease_lost=True, job_id="old")
    assert first["checkpoint_inputs"][0][0]["matchIds"] == ["replacement"]
    db.error_jobs.add("old")
    with monkeypatch.context() as second_patch:
        second = _run(second_patch, blocks, recommendations, missing_rows=["missing"],
                      checkpoint_db=db, r2=r2, job_id="new")
    assert second["generated"] == {}
    assert second["success"]["cut_assets"][0]["metadata"]["fallback"] == [
        {"kind": "matching_substituted", "missingMatchId": "missing", "substituteMatchId": "replacement"}]
    assert second["page_plans"][0]["matchingIds"] == ["replacement"]


@pytest.mark.parametrize("stage", ["final", "base"])
@pytest.mark.parametrize("saved_direction", ["front", "back"])
def test_checkpoint_restores_saved_pose_fallback_without_duplicate_current_records(monkeypatch, stage, saved_direction):
    monkeypatch.setattr(dpj.cut_generator, "load_example_asset_registry", lambda: (None, {
        "pose": {"cutType": "horizon", "direction": "back", "all": "pose.png"}}))
    async def example(*args, **kwargs): return dpj.InlineImage("image/png", b"POSE")
    monkeypatch.setattr(dpj.cut_generator, "load_example_image", example)
    db, r2 = _IntentDB(), _R2()
    first = _run(monkeypatch, [_block(exampleId="pose", refScope="pose")],
        checkpoint_db=db, r2=r2, lease_lost=True, job_id="old",
        checkpoint_base_error=dpj.face_identity.FacePassUnavailable("pod_not_ready") if stage == "base" else None)
    db.error_jobs.add("old")
    with monkeypatch.context() as second_patch:
        second = _run(second_patch, [_block(exampleId="pose", refScope="pose", direction=saved_direction)],
                      checkpoint_db=db, r2=r2, job_id="new")
    assert second["success"]["cut_assets"][0]["metadata"]["fallback"] == [
        {"kind": "direction_from_example", "exampleId": "pose", "fromDirection": "front",
         "fromSideStyle": None, "toDirection": "back", "toSideStyle": None}]
    if stage == "final":
        assert first["generated"]
        assert second["generated"] == {}
        assert second["cut_plans"] == []
    else:
        assert second["base_reused"] is True
    assert second["success"]["charge"] == 19


def test_example_back_direction_keeps_virtual_pair_without_face_exposure_prompt(monkeypatch):
    monkeypatch.setattr(dpj.cut_generator, "load_example_asset_registry", lambda: (None, {
        "pose": {"cutType": "horizon", "direction": "back", "all": "pose.png"}}))
    monkeypatch.setattr(dpj.cut_generator, "load_virtual_model_registry", lambda: {"m-test": {"views": {
        "face_front": {"key": "k/model-face", "mime": "image/png"},
        "body_front": {"key": "k/model-body", "mime": "image/png"}}}})
    async def example(*args, **kwargs): return dpj.InlineImage("image/png", b"POSE")
    monkeypatch.setattr(dpj.cut_generator, "load_example_image", example)
    wants = []
    original_face = dpj.cut_generator.wants_face
    def face(spec, clothing_type=None):
        value = original_face(spec, clothing_type)
        wants.append((spec["direction"], value))
        return value
    monkeypatch.setattr(dpj.cut_generator, "wants_face", face)
    r2 = _R2()
    r2.objects.update({"k/model-face": (b"FACE", "image/png", None),
                       "k/model-body": (b"BODY", "image/png", None)})
    result = _run(monkeypatch, [_block(exampleId="pose", refScope="pose")], model_id="m-test", r2=r2)
    generated = result["generated"]["b1"]
    assert wants[-1] == ("back", False)
    assert generated["images"] == [b"FACE", b"BODY", b"PRODUCT", b"POSE"]
    assert "the model's face may be visible" not in generated["prompt"]
    assert "keep their face in frame, visible and recognizable" not in generated["prompt"]


@pytest.mark.parametrize("direction,side_style,example_direction,example_side,changed", [
    ("front", None, "side", "threeQuarter", True),
    ("side", "profile", "back", None, True),
    ("side", "threeQuarter", "side", "threeQuarter", False),
])
def test_pose_example_direction_precedes_profile_face_manifest_and_qc(monkeypatch, direction,
        side_style, example_direction, example_side, changed):
    registry = {"pose": {"cutType": "horizon", "direction": example_direction,
                         "sideStyle": example_side, "all": "all.png"}}
    monkeypatch.setattr(dpj.cut_generator, "load_example_asset_registry", lambda: (None, registry))
    async def example(*args, **kwargs): return dpj.InlineImage("image/png", b"POSE")
    monkeypatch.setattr(dpj.cut_generator, "load_example_image", example)
    captured = {"profile": [], "face": [], "manifest": []}
    original_profile = dpj.confirmed_gpt_runtime.resolve_profile_request
    original_face = dpj.cut_generator.wants_face
    original_manifest = dpj.cut_generator.build_manifest
    def profile(spec, **kwargs):
        captured["profile"].append(spec["direction"])
        return original_profile(spec, **kwargs)
    def face(spec, clothing_type=None):
        value = original_face(spec, clothing_type)
        captured["face"].append((spec["direction"], value))
        return value
    def manifest(*args, **kwargs):
        captured["manifest"].append(kwargs["reference_direction_compatible"])
        return original_manifest(*args, **kwargs)
    monkeypatch.setattr(dpj.confirmed_gpt_runtime, "resolve_profile_request", profile)
    monkeypatch.setattr(dpj.cut_generator, "wants_face", face)
    monkeypatch.setattr(dpj.cut_generator, "build_manifest", manifest)
    result = _run(monkeypatch, [_block(exampleId="pose", refScope="pose", direction=direction, sideStyle=side_style)])
    runtime = result["generated"]["b1"]["spec"]
    assert runtime["direction"] == example_direction
    assert runtime.get("sideStyle") == example_side
    assert captured["profile"] == [example_direction]
    assert captured["face"][-1] == (example_direction, example_direction != "back")
    assert captured["manifest"] == [True]
    assert result["cut_plans"][0]["storyboard"]["direction"] == example_direction
    assert result["generated"]["b1"]["images"] == [b"PRODUCT", b"POSE"]
    asset_metadata = result["success"]["cut_assets"][0]["metadata"]
    assert bool(asset_metadata.get("fallback")) is changed
    if changed:
        assert asset_metadata["fallback"] == [{"kind": "direction_from_example", "exampleId": "pose",
            "fromDirection": direction, "fromSideStyle": side_style,
            "toDirection": example_direction, "toSideStyle": example_side}]


def test_retired_mirror_stops_job_before_generation_and_releases_credits(monkeypatch):
    monkeypatch.setattr(dpj.cut_generator, "load_example_asset_registry", lambda: (None, {
        "pose": {"cutType": "styling", "direction": "back", "all": "pose.png"}}))
    result = _run(monkeypatch, [_block(cutType="mirror", exampleId="pose", refScope="pose")])
    assert result["generated"] == {}
    assert result["failure"]["metadata"]["error"] == "unknown_cut_type"
    assert result["failure"]["reserved"] == 19


def test_two_invalid_generation_contracts_emit_one_bounded_critical_per_job(monkeypatch, caplog):
    with caplog.at_level(logging.CRITICAL, logger=dpj.log.name):
        result = _run(monkeypatch, [_block("b1"), _block("b2", direction="back")],
                      generate_errors={"b1": ValueError("x" * 240), "b2": ValueError("unknown_cut_type")})
    critical = [record.message for record in caplog.records if record.levelno == logging.CRITICAL]
    assert len(critical) == 1
    assert all(value in critical[0] for value in ["j1", "b1", "b2", "ValueError", "x" * 200, "unknown_cut_type"])
    assert "x" * 201 not in critical[0]
    assert all(payload["reason"] == "invalid_cut_spec" for _, payload in result["events"]
               if payload.get("status") == "cut_failed")


@pytest.mark.parametrize("error,reason,critical", [
    (DetailRecommendationError("detail_target_unknown"), "detail_target_unavailable", False),
    (ValueError("invalid_color"), "detail_target_unavailable", False),
    (ValueError("ordinary detail bug"), "invalid_cut_spec", True),
    (ValueError("invalid_color_authority"), "invalid_cut_spec", True),
    (dpj.DetailShotRejected("detail_source_unavailable"), "detail_source_unavailable", False),
])
def test_detail_errors_preserve_named_rejections_and_log_underlying_cause(monkeypatch, caplog, error, reason, critical):
    with caplog.at_level(logging.WARNING, logger=dpj.log.name):
        result = _run(monkeypatch, [_block(cutType="product", shot="detail")], detail_errors={"b1": error})
    assert any(payload.get("reason") == reason for _, payload in result["events"])
    assert bool([record for record in caplog.records if record.levelno == logging.CRITICAL]) is critical
    if reason == "detail_target_unavailable":
        assert any(str(error) in record.message and record.levelno == logging.WARNING for record in caplog.records)
