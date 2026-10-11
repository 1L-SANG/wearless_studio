"""Editor runtime fallbacks keep stored selections and credit settlement intact."""
import asyncio
import contextlib
import json
import logging
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from app.agents import detail_recommendations
from app.workers import editor_image_job as eij
from conftest import FakeConn, fake_worker_app, make_settings, worker_job


def recommendation(item_id, category="팬츠", clothing_type="bottom", **kwargs):
    return {"id": item_id, "category": category, "clothingType": clothing_type,
            "isCompatible": True, "isCustom": False, **kwargs}


@pytest.fixture
def runtime(monkeypatch):
    class Pool:
        active = 0

        @contextlib.asynccontextmanager
        async def connection(self):
            self.active += 1
            try:
                yield FakeConn()
            finally:
                self.active -= 1

    class R2:
        unavailable = set()

        def get_bytes(self, key):
            state["reads"].append(key)
            if key in self.unavailable:
                raise OSError("missing object")
            return key.encode()

        def put_bytes(self, key, data, mime, cache=None):
            state["writes"].append(data)

    state = {
        "analysis": {"matchClothing": [], "fitProfile": {
            "version": 2, "category": "top", "gender": "women", "axes": {"fit": "regular"},
            "matchCut": "wide", "matchingFit": {
                "clothingId": "lost", "fitCategory": "pants", "axes": {"cut": "wide"}}}},
        "catalog_missing": set(), "asset_missing": set(), "metadata": {},
        "lookups": [], "metadata_lookups": [], "reads": [], "writes": [],
        "generations": [], "qc": [],
        "storyboard": [],
        "model_resolver": eij.cut_generator.resolve_virtual_model_assets,
        "qc_fit": [],
    }
    app = fake_worker_app(make_settings(
        garment_qc_mode="off", cut_output_qc_mode="shadow", r2_bucket="b"), r2=R2())
    app.state.pool = Pool()
    state["app"] = app

    async def matching_asset(conn, item_id, uid, pid):
        assert (uid, pid) == ("u1", "p1")
        state["lookups"].append(item_id)
        return None if item_id in state["catalog_missing"] else f"asset-{item_id}"

    async def asset(conn, uid, asset_id):
        if asset_id in state["asset_missing"]:
            return None
        return {"id": asset_id, "r2_key": asset_id, "mime_type": "image/png"}

    async def metadata(conn, item_id, uid, pid):
        state["metadata_lookups"].append(item_id)
        return state["metadata"].get(item_id)

    async def generate(settings, gemini, spec, product, images, **kwargs):
        assert app.state.pool.active == 0, "AI must run outside database connections"
        state["generations"].append({
            "spec": deepcopy(spec), "normalized": eij.cut_generator.normalize_spec(spec, clothing_type="top"),
            "images": [image.data for image in images], "analysis": deepcopy(kwargs["analysis"]),
            "manifest": kwargs["manifest"], "wants_face": eij.cut_generator.wants_face(spec, "top"),
            "prompt": eij.cut_generator.build_prompt(
                spec, product, analysis=kwargs["analysis"], manifest=kwargs["manifest"]),
        })
        if state.get("generate_error"):
            raise state["generate_error"]
        return b"GENERATED", "image/png"

    async def compile_qc(settings, plan, references, output):
        assert app.state.pool.active == 0
        state["qc"].append(plan.to_dict())
        return {"overall": "PASS"}

    compile_plan = eij.cut_plan.compile_cut_plan

    def plan_with_fit(*args, **kwargs):
        state["qc_fit"].append(deepcopy(kwargs.get("fit_profile")))
        return compile_plan(*args, **kwargs)

    async def success(conn, **kwargs):
        state["success"] = kwargs
        return {"id": "output"}

    async def failure(conn, **kwargs):
        state["failure"] = kwargs
        return True

    monkeypatch.setattr(eij.repo, "get_project", AsyncMock(return_value={}))
    monkeypatch.setattr(eij.repo, "get_product", AsyncMock(return_value={
        "clothingType": "top", "colors": [{"id": "base", "isBase": True,
                                               "images": [{"slot": "Front", "id": "product"}]}]}))
    monkeypatch.setattr(eij.repo, "get_analysis", AsyncMock(side_effect=lambda *_: state["analysis"]))
    monkeypatch.setattr(eij.repo, "get_storyboard", AsyncMock(side_effect=lambda *_: state["storyboard"]))
    monkeypatch.setattr(eij.repo, "get_matching_item_asset", matching_asset)
    monkeypatch.setattr(eij.repo, "get_matching_item_metadata", metadata)
    monkeypatch.setattr(eij.repo, "get_asset_for_user", asset)
    monkeypatch.setattr(eij.repo, "finalize_editor_image_success", success)
    monkeypatch.setattr(eij.repo, "finalize_editor_image_failure", failure)
    monkeypatch.setattr(eij.cut_generator, "resolve_virtual_model_assets", lambda *_args, **_kw: None)
    monkeypatch.setattr(eij.cut_generator, "generate", generate)
    monkeypatch.setattr(eij.cut_generator, "load_example_image", AsyncMock(return_value=eij.InlineImage("image/png", b"EXAMPLE")))
    monkeypatch.setattr(eij.cut_output_qc, "verdict", compile_qc)
    monkeypatch.setattr(eij.cut_plan, "compile_cut_plan", plan_with_fit)
    monkeypatch.setattr(eij, "_emit", AsyncMock())
    return state


def run(state, payload=None):
    request = {"mode": "new", "cutType": "styling", "shot": "full",
               "direction": "front", "colorId": "base", **(payload or {})}
    asyncio.run(eij.run_editor_image_job(state["app"], worker_job(request, credits_reserved=19)))
    return request


@pytest.mark.parametrize("missing", ["catalog", "asset", "bytes"])
@pytest.mark.parametrize("retry", [False, True])
def test_missing_matching_substitutes_for_new_and_restored_retry(runtime, missing, retry, caplog):
    runtime["analysis"]["matchClothing"] = [
        recommendation("lost"), recommendation("different", "스커트"), recommendation("next")]
    if missing == "catalog":
        runtime["catalog_missing"].add("lost")
    elif missing == "asset":
        runtime["asset_missing"].add("asset-lost")
    else:
        runtime["app"].state.r2.unavailable = {"asset-lost"}
    storyboard = [{"id": "failed", "source": "ai", "cutType": "styling", "direction": "front",
                   "shot": "full", "colorId": "base", "matchIds": ["lost"]}]
    original_storyboard, original_analysis = deepcopy(storyboard), deepcopy(runtime["analysis"])
    runtime["storyboard"] = storyboard
    if retry:
        request = eij.horizon_background.restore_failed_retry(
            {"mode": "new", "retryBlockId": "failed"}, storyboard,
            [{"elements": [{"type": "image", "sourceBlockId": "failed", "genFailed": True}]}])
    else:
        request = {"matchIds": ["lost"]}
    original_request = deepcopy(request)
    with caplog.at_level(logging.INFO, logger=eij.log.name):
        run(runtime, request)
    assert "failure" not in runtime
    generated = runtime["generations"][0]
    assert generated["spec"]["matchIds"] == generated["normalized"]["matchIds"] == ["next"]
    assert generated["images"] == [b"product", b"asset-next"]
    assert generated["manifest"].count("MATCHING") == 1
    assert "matchCut" not in generated["analysis"]["fitProfile"]
    assert "matchingFit" not in generated["analysis"]["fitProfile"]
    assert generated["analysis"]["fitProfile"]["axes"] == {"fit": "regular"}
    assert runtime["qc"][0]["storyboard"]["matching"] == ["next"]
    assert "matchCut" not in runtime["qc_fit"][0]
    assert "matchingFit" not in runtime["qc_fit"][0]
    assert runtime["qc_fit"][0]["axes"] == {"fit": "regular"}
    assert runtime["success"]["image"]["metadata"]["fallback"] == [{
        "kind": "matching_substituted", "missingMatchId": "lost", "substituteMatchId": "next"}]
    assert runtime["success"]["charge"] == runtime["success"]["reserved"] == 19
    if retry:
        assert runtime["success"]["image"]["metadata"]["retryBlockId"] == "failed"
    assert runtime["metadata_lookups"] == []
    assert request == original_request and storyboard == original_storyboard
    assert runtime["analysis"] == original_analysis
    assert "matching_substituted" in caplog.text


def test_substitution_uses_repo_metadata_when_saved_original_absent(runtime):
    runtime["analysis"]["matchClothing"] = [recommendation("other", "스커트"), recommendation("next")]
    runtime["catalog_missing"].add("lost")
    runtime["metadata"]["lost"] = {"clothing_type": "bottom", "category": "팬츠"}
    run(runtime, {"matchIds": ["lost"]})
    assert runtime["generations"][0]["spec"]["matchIds"] == ["next"]
    assert runtime["metadata_lookups"] == ["lost"]


def test_candidates_exclude_selected_incompatible_custom_and_other_type(runtime):
    runtime["analysis"]["matchClothing"] = [
        recommendation("lost"), recommendation("selected"),
        recommendation("bad", isCompatible=False), recommendation("custom", isCustom=True),
        recommendation("top", clothing_type="top"), recommendation("next", "스커트")]
    runtime["catalog_missing"].add("lost")
    run(runtime, {"matchIds": ["lost", "selected"]})
    assert runtime["generations"][0]["spec"]["matchIds"] == ["next", "selected"]
    assert runtime["generations"][0]["images"] == [b"product", b"asset-next", b"asset-selected"]
    assert runtime["lookups"] == ["lost", "selected", "next"]


def test_two_missing_matches_get_distinct_loadable_replacements(runtime):
    runtime["analysis"]["matchClothing"] = [
        recommendation("lost1"), recommendation("lost2"), recommendation("missing-row"),
        recommendation("missing-asset"), recommendation("missing-bytes"),
        recommendation("next1"), recommendation("next2")]
    runtime["catalog_missing"].update({"lost1", "lost2", "missing-row"})
    runtime["asset_missing"].add("asset-missing-asset")
    runtime["app"].state.r2.unavailable = {"asset-missing-bytes"}
    run(runtime, {"matchIds": ["lost1", "lost2"]})
    assert runtime["generations"][0]["spec"]["matchIds"] == ["next1", "next2"]
    assert runtime["generations"][0]["images"] == [b"product", b"asset-next1", b"asset-next2"]
    assert runtime["success"]["image"]["metadata"]["fallback"] == [
        {"kind": "matching_substituted", "missingMatchId": "lost1", "substituteMatchId": "next1"},
        {"kind": "matching_substituted", "missingMatchId": "lost2", "substituteMatchId": "next2"}]


@pytest.mark.parametrize("case", ["no_metadata", "empty_candidates", "all_unavailable"])
def test_unavailable_matching_fails_after_candidates_without_charging(runtime, case):
    runtime["catalog_missing"].add("lost")
    if case != "no_metadata":
        runtime["analysis"]["matchClothing"] = [recommendation("lost")]
    if case == "all_unavailable":
        runtime["analysis"]["matchClothing"].append(recommendation("next"))
        runtime["app"].state.r2.unavailable = {"asset-next"}
    run(runtime, {"matchIds": ["lost"]})
    assert runtime["generations"] == runtime["writes"] == []
    assert "success" not in runtime
    assert runtime["failure"]["metadata"]["error"] == "matching_asset_unavailable"
    assert runtime["failure"]["reserved"] == 19


def test_existing_matching_keeps_fit_without_fallback(runtime):
    run(runtime, {"matchIds": ["available"]})
    assert runtime["generations"][0]["analysis"]["fitProfile"] == runtime["analysis"]["fitProfile"]
    assert "fallback" not in runtime["success"]["image"]["metadata"]


@pytest.mark.parametrize("retry", [False, True])
@pytest.mark.parametrize("from_direction,from_side,to_direction,to_side", [
    ("front", None, "back", None), ("front", None, "side", "profile"),
    ("side", "threeQuarter", "side", "profile"), ("back", None, "front", None)])
def test_pose_direction_is_prepared_before_generation_manifest_and_qc(
    runtime, monkeypatch, retry, from_direction, from_side, to_direction, to_side,
):
    entry = {"cutType": "styling", "direction": to_direction, "sideStyle": to_side,
             "pose": "published.png", "applicableClothingTypes": ["top"]}
    monkeypatch.setattr(eij.cut_generator, "load_example_asset_registry", lambda: ("", {"example": entry}))
    resolved_model_specs = []

    def model_refs(spec, **kwargs):
        resolved_model_specs.append(deepcopy(spec))
        return runtime["model_resolver"](spec, **kwargs)

    monkeypatch.setattr(eij.cut_generator, "resolve_virtual_model_assets", model_refs)
    monkeypatch.setattr(eij.cut_generator, "load_virtual_model_registry", lambda: {"m-test": {
        "views": {"face_front": {"key": "model-face", "mime": "image/png"},
                  "body_front": {"key": "model-body", "mime": "image/png"}}}})
    request = {"direction": from_direction, "sideStyle": from_side, "exampleId": "example",
               "refScope": "pose", "modelId": "m-test"}
    if retry:
        storyboard = [{"id": "failed", "source": "ai", "cutType": "styling", "shot": "full", **request}]
        runtime["storyboard"] = storyboard
        request = eij.horizon_background.restore_failed_retry(
            {"retryBlockId": "failed"}, storyboard,
            [{"elements": [{"type": "image", "sourceBlockId": "failed"}]}])
    original = deepcopy(request)
    run(runtime, request)
    assert "failure" not in runtime
    generated = runtime["generations"][0]
    assert generated["spec"]["direction"] == generated["normalized"]["direction"] == to_direction
    assert generated["spec"]["sideStyle"] == generated["normalized"]["sideStyle"] == to_side
    assert resolved_model_specs[0]["direction"] == to_direction
    assert resolved_model_specs[0]["sideStyle"] == to_side
    assert generated["images"] == [b"model-face", b"model-body", b"product", b"EXAMPLE"]
    assert "MODEL FACE" in generated["manifest"] and "MODEL FULL BODY" in generated["manifest"]
    assert runtime["qc"][0]["storyboard"]["direction"] == to_direction
    if to_direction == "back":
        assert generated["wants_face"] is False
        assert "NO face visible at all" in generated["prompt"]
        assert "keep their face in frame" not in generated["prompt"]
        assert "the model's face may be visible" not in generated["prompt"]
    assert runtime["success"]["image"]["metadata"]["fallback"] == [{
        "kind": "direction_from_example", "exampleId": "example",
        "fromDirection": from_direction, "fromSideStyle": from_side,
        "toDirection": to_direction, "toSideStyle": to_side}]
    assert request == original


def test_identical_pose_direction_does_not_record_fallback(runtime, monkeypatch):
    monkeypatch.setattr(eij.cut_generator, "load_example_asset_registry", lambda: ("", {"example": {
        "cutType": "styling", "direction": "front", "pose": "published.png"}}))
    run(runtime, {"exampleId": "example", "refScope": "pose"})
    assert "fallback" not in runtime["success"]["image"]["metadata"]


def test_mirror_incompatible_pose_still_fails_with_retry_copy(runtime, monkeypatch):
    monkeypatch.setattr(eij.cut_generator, "load_example_asset_registry", lambda: ("", {"example": {
        "cutType": "styling", "direction": "front", "pose": "published.png"}}))
    run(runtime, {"cutType": "mirror", "exampleId": "example", "refScope": "pose"})
    assert runtime["generations"] == []
    assert runtime["failure"]["metadata"]["error"] == "pose_direction_incompatible"
    assert runtime["failure"]["message"] == "이 예시로 만들지 못했어요. 잠시 후 다시 시도해 주세요."


@pytest.mark.parametrize("stage", ["normalize", "generate"])
def test_ordinary_contract_failure_logs_one_critical(runtime, monkeypatch, caplog, stage):
    message = "broken-contract:" + "x" * 300
    if stage == "normalize":
        monkeypatch.setattr(eij.cut_generator, "normalize_spec", lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError(message)))
    else:
        runtime["generate_error"] = ValueError(message)
    with caplog.at_level(logging.WARNING, logger=eij.log.name):
        run(runtime)
    assert runtime["failure"]["metadata"]["error"] == "invalid_spec"
    records = [record for record in caplog.records if record.levelno == logging.CRITICAL]
    assert len(records) == 1
    assert "j1" in records[0].message and "ValueError" in records[0].message
    assert message[:200] in records[0].message and message[:201] not in records[0].message


@pytest.mark.parametrize("mode", ["new", "vary"])
@pytest.mark.parametrize("error,expected,critical", [
    (ValueError("unexpected contract"), "invalid_cut_spec", True),
    (ValueError("invalid_color"), "detail_target_unavailable", False),
    (detail_recommendations.DetailRecommendationError("detail_target_unknown"), "detail_target_unavailable", False),
    (eij.DetailShotRejected("detail_reference_required", {"passed": False}), "detail_reference_required", False),
])
def test_detail_failure_distinguishes_expected_rejection_and_contract_error(
    runtime, monkeypatch, caplog, mode, error, expected, critical,
):
    monkeypatch.setattr(eij.detail_shot_runtime, "generate", AsyncMock(side_effect=error))
    request = {"cutType": "product", "shot": "detail"}
    if mode == "vary":
        monkeypatch.setattr(eij.repo, "get_project_asset_for_user", AsyncMock(return_value={
            "id": "source", "r2_key": "source", "mime_type": "image/png",
            "metadata": {"detail_recipe": {"version": 1, "colorId": "base", "shot": "detail", "direction": "front"}}}))
        request = {"mode": "vary", "source": {"src": "/v1/assets/source/file", "cutType": "product"}}
    with caplog.at_level(logging.WARNING, logger=eij.log.name):
        run(runtime, request)
    assert runtime["writes"] == [] and "success" not in runtime
    assert runtime["failure"]["metadata"]["error"] == expected
    records = [record for record in caplog.records if record.levelno == logging.CRITICAL]
    assert len(records) == int(critical)
    if not critical and expected == "detail_target_unavailable":
        assert any(record.levelno == logging.WARNING and str(error) in record.message for record in caplog.records)


@pytest.mark.parametrize("error", [
    json.JSONDecodeError("provider payload unreadable", "{", 0),
    UnicodeDecodeError("utf-8", b"\xff", 0, 1, "provider bytes unreadable"),
])
def test_provider_decode_errors_use_generic_failure_without_critical(runtime, monkeypatch, caplog, error):
    def provider_failure(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(runtime["app"].state.r2, "put_bytes", provider_failure)
    with caplog.at_level(logging.WARNING, logger=eij.log.name):
        run(runtime)
    assert "success" not in runtime
    assert runtime["failure"]["code"] == "generation_failed"
    assert runtime["failure"]["metadata"]["error"] == str(error)[:300]
    assert runtime["failure"]["reserved"] == 19
    assert not any(record.levelno == logging.CRITICAL for record in caplog.records)
