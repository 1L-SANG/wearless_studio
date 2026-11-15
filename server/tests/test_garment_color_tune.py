"""합성 사진과 가짜 fal 응답만으로 색 보정, 실패 보존, 다섯 경로, 재개를 확인한다."""
import asyncio
import hashlib
import io
import threading
from dataclasses import replace
from unittest.mock import AsyncMock, Mock

import numpy as np
import pytest
from PIL import Image

from app.agents.gemini_image import InlineImage
from app.services import garment_color_consistency as G, garment_color_tune as T
from conftest import make_settings
from test_garment_color_flow import detail_flow, editor_flow, product


def scene(color=(0.62, 0.03, 0.05)):
    mask = np.zeros((256, 192), bool)
    mask[40:216, 36:156] = True
    rgb = np.full((256, 192, 3), (200, 205, 210), np.uint8)
    y, x = np.mgrid[:256, :192]
    folds = 1 + 0.04 * np.sin(2 * np.pi * (x - 36) / 24)
    lab = np.array(color) * folds[mask, None]
    rgb[mask] = np.rint(G.oklab_to_srgb(lab)).astype(np.uint8)
    return rgb, mask


def png(rgb):
    stream = io.BytesIO()
    Image.fromarray(rgb).save(stream, "PNG")
    return stream.getvalue()


def sha(data):
    return hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize("distance,reason", [(0.03, None), (0.05, "over_cap"), (0.005, "within_tolerance")])
def test_target_shift_moves_only_garment_chroma_and_preserves_noops(distance, reason):
    rgb, mask = scene()
    before = np.array(G.measure_garment_color(rgb, mask)["center"])
    target = before[1:] + (distance, 0)
    original = rgb.copy()
    result, record = G.match_to_target_color(rgb, mask, target)
    repeated, same_record = G.match_to_target_color(rgb, mask, target)
    assert np.array_equal(repeated, result) and same_record == record
    assert np.array_equal(rgb, original)
    assert record["reason"] == reason
    assert record["needed_shift"]["distance_ab"] == pytest.approx(distance)
    assert record["algorithm_version"] == G.TARGET_ALGORITHM_VERSION
    if reason:
        assert result is rgb
        assert record["actual_shift"] == {"d_l": 0.0, "d_ab": [0.0, 0.0]}
    else:
        after = np.array(G.measure_garment_color(result, mask)["center"])
        assert record["status"] == "corrected" and record["plan"]["d_l"] == 0
        assert np.linalg.norm(after[1:] - target) <= 0.003
        assert abs(after[0] - before[0]) <= 0.005
        assert np.array_equal(result[~mask], rgb[~mask])


def test_target_achromatic_and_bad_masks_preserve_array():
    rgb, mask = scene()
    result, record = G.match_to_target_color(rgb, mask, (0.01, 0))
    assert result is rgb and record["reason"] == "target_achromatic"
    for bad, reason in ((np.zeros_like(mask), "mask_tiny"), (np.ones_like(mask), "mask_frame_fill")):
        result, record = G.match_to_target_color(rgb, bad, (0.06, 0.05))
        assert result is rgb and record["reason"] == reason


def test_target_self_check_failure_and_calculation_failure_preserve_array(monkeypatch):
    rgb, mask = scene()
    monkeypatch.setattr(G, "self_check", lambda *a: {"reason": "reverted_shortfall"})
    result, record = G.match_to_target_color(rgb, mask, (0.06, 0.05))
    assert result is rgb and record["status"] == "reverted"
    monkeypatch.setattr(G, "_shift", lambda *a: (_ for _ in ()).throw(RuntimeError("cpu failure")))
    result, record = G.match_to_target_color(rgb, mask, (0.06, 0.05))
    assert result is rgb and record["reason"] == "calculation_failed"


@pytest.mark.parametrize("bad", [(0,), (0, 0, 0), (float("nan"), 0), (0, float("inf"))])
def test_target_rejects_invalid_input(bad):
    rgb, mask = scene()
    with pytest.raises(ValueError):
        G.match_to_target_color(rgb, mask, bad)
    with pytest.raises(ValueError):
        G.match_to_target_color(rgb.astype(float), mask, (0.06, 0.05))
    with pytest.raises(ValueError):
        G.match_to_target_color(rgb, mask.astype(np.uint8), (0.06, 0.05))


@pytest.mark.parametrize("failure", ["target_ambiguous", "target_wrong_only", "output_combined", "output_partial_overlap", "separate"])
def test_recolor_protects_the_coordination_and_rejects_ambiguous_source_masks(monkeypatch, failure):
    shirt = np.zeros((280, 200), bool)
    shirt[45:153, 60:140] = True
    pants = np.zeros_like(shirt)
    pants[153:266, 52:148] = True
    backdrop = np.full((280, 200, 3), (213, 212, 210), np.uint8)

    def fill(image, mask, lab):
        image[mask] = np.rint(G.oklab_to_srgb(np.array(lab))).astype(np.uint8)

    output = backdrop.copy()
    fill(output, shirt | pants, (0.62, 0.04, 0.05))
    master, target = output.copy(), output.copy()
    fill(master, shirt, (0.62, 0.03, 0.048))
    fill(target, pants, (0.62, 0.064, 0.05))
    if not failure.startswith("target_"):
        fill(target, shirt, (0.62, 0.064, 0.05))
    overlap = np.zeros_like(shirt)
    overlap[153:176, 60:140] = True
    product_mask = {"output_combined": shirt | pants,
                    "output_partial_overlap": shirt | overlap}.get(failure, shirt)
    calls = []

    async def run(settings, model, body):
        prompt = body["prompt"]
        calls.append(prompt)
        if len(calls) == 1:
            masks = {"target_ambiguous": [shirt, pants], "target_wrong_only": [pants]}.get(failure, [shirt])
        elif prompt in {"pants", "lower body garment"}:
            masks = [pants]
        else:
            masks = [product_mask]
        return {
            "masks": [{"url": T.fal_api.image_input_url(png(np.repeat(
                (mask * 255).astype(np.uint8)[..., None], 3, axis=2)), "image/png")} for mask in masks],
            "scores": [0.99] * len(masks),
        }

    monkeypatch.setattr(T.fal_api, "run_model", run)
    sources = {name: InlineImage("image/png", png(rgb))
               for name, rgb in (("master", master), ("target", target), ("output", output))}
    from conftest import fake_worker_app, worker_job
    settings = make_settings(garment_color_tune_enabled=True, fal_key="fake")
    job = worker_job()
    metadata = AsyncMock(return_value={"clothing_type": "bottom"})
    monkeypatch.setattr(T.repo, "get_matching_item_metadata", metadata)
    result, _, record = asyncio.run(T.tune(settings, **sources, clothing_type="top",
        pool=fake_worker_app(settings).state.pool, job=job, recipe={"matchIds": ["actual-pants"]}))
    assert metadata.await_args.args[1:] == ("actual-pants", job["user_id"], job["project_id"])
    assert calls[0] == "top"
    if failure == "separate":
        assert record["status"] == "corrected"
        with Image.open(io.BytesIO(result)) as im:
            pixels = np.array(im)
        assert np.any(pixels[shirt] != output[shirt])
        assert np.array_equal(pixels[~shirt], output[~shirt])
    else:
        assert result is sources["output"].data
        assert record["reason"] == ("target_ambiguous" if failure.startswith("target_") else "no_candidate")


@pytest.fixture
def fal(monkeypatch):
    _, mask = scene()
    uri = T.fal_api.image_input_url(png(np.repeat((mask * 255).astype(np.uint8)[..., None], 3, axis=2)), "image/png")
    async def respond(settings, model, payload):
        if payload["prompt"] == "lower body garment":
            return {"masks": [], "scores": []}
        return {"masks": [{"url": uri}], "scores": [0.99]}
    run = AsyncMock(side_effect=respond)
    monkeypatch.setattr(T.fal_api, "run_model", run)
    return run


def inputs(target_color=(0.62, 0.06, 0.05)):
    return dict(master=InlineImage("image/png", png(scene((0.62, -0.02, -0.05))[0])),
                target=InlineImage("image/png", png(scene(target_color)[0])),
                output=InlineImage("image/png", png(scene()[0])), clothing_type="top")


@pytest.mark.parametrize("target,reason,calls", [
    ((0.62, 0.06, 0.05), None, 4), ((0.62, 0.08, 0.05), "over_cap", 4),
    ((0.62, 0.035, 0.05), "within_tolerance", 4), ((0.62, 0.0, 0.0), "target_achromatic", 1)])
def test_service_real_pixel_pipeline_with_fake_fal(fal, target, reason, calls):
    kwargs = inputs(target)
    execution, warnings = {"colorCheck": {"status": "passed", "checkedOutputSha256": sha(kwargs["output"].data)}}, []
    data, mime = asyncio.run(T.after_color_check(make_settings(garment_color_tune_enabled=True, fal_key="fake"),
        **kwargs, color_execution=execution, warnings=warnings))
    record = execution["colorTune"]
    assert record.get("reason") == reason and fal.await_count == calls
    assert execution["colorCheck"]["checkedOutputSha256"] == sha(kwargs["output"].data)
    assert record["inputOutputSha256"] == sha(kwargs["output"].data)
    assert record["outputSha256"] == sha(data)
    assert warnings == ([{"code": "color_tune_over_cap"}] if reason == "over_cap" else [])
    if reason:
        assert data is kwargs["output"].data
    else:
        rgb, mask = scene()
        with Image.open(io.BytesIO(data)) as im:
            result = np.array(im.convert("RGB"))
        assert mime == "image/png" and np.array_equal(result[~mask], rgb[~mask])
        before = G.measure_garment_color(rgb, mask)["center"]
        after = G.measure_garment_color(result, mask)["center"]
        assert np.linalg.norm(np.array(after[1:]) - record["target_ab"]) <= 0.003
        assert abs(after[0] - before[0]) <= 0.005


@pytest.mark.parametrize("enabled,key,expected", [(False, "fake", {"status": "disabled"}),
    (True, None, {"status": "skipped", "reason": "fal_unconfigured"})])
def test_disabled_and_unconfigured_do_not_call_fal(fal, enabled, key, expected):
    kwargs = inputs()
    data, mime, record = asyncio.run(T.tune(make_settings(garment_color_tune_enabled=enabled, fal_key=key), **kwargs))
    assert data is kwargs["output"].data and record == expected
    fal.assert_not_awaited()


def test_target_color_is_measured_at_fal_input_size(monkeypatch, fal):
    kwargs = inputs()
    target = np.array(Image.fromarray(scene((0.62, 0.06, 0.05))[0]).resize((1200, 2400)))
    kwargs["target"] = InlineImage("image/png", png(target))
    original = T._target_color
    def measure(rgb, masks):
        assert max(rgb.shape[:2]) == T.fal_api.MAX_PX
        assert all(mask.shape == rgb.shape[:2] for mask in masks)
        return original(rgb, masks)
    measured = Mock()
    def check(rgb, masks):
        measured(rgb.shape)
        return measure(rgb, masks)
    monkeypatch.setattr(T, "_target_color", check)
    _, _, result = asyncio.run(T.tune(make_settings(garment_color_tune_enabled=True, fal_key="fake"), **kwargs))
    assert result.get("reason") != "calculation_failed"
    measured.assert_called_once()


def test_noop_preserves_original_jpeg_encoding(fal):
    kwargs = inputs((0.62, 0.08, 0.05))
    stream = io.BytesIO()
    Image.fromarray(scene()[0]).save(stream, "JPEG", quality=93)
    kwargs["output"] = InlineImage("image/jpeg", stream.getvalue())
    data, mime, record = asyncio.run(T.tune(make_settings(garment_color_tune_enabled=True, fal_key="fake"), **kwargs))
    assert record["reason"] == "over_cap"
    assert data is kwargs["output"].data and mime == "image/jpeg"


@pytest.mark.parametrize("failure,reason", [("fal", "fal_failed"), ("no_candidate", "no_candidate"),
    ("selection", "no_candidate"), ("timeout", "timeout"), ("cpu_timeout", "timeout"),
    ("decode", "invalid_image"), ("target_decode", "invalid_image"), ("master_decode", "invalid_image")])
def test_service_failure_never_changes_bytes_or_raises(monkeypatch, fal, failure, reason):
    kwargs = inputs()
    if failure == "fal":
        fal.side_effect = RuntimeError("provider down")
    elif failure == "no_candidate":
        calls = 0
        response = fal.side_effect
        async def no_output(settings, model, payload):
            nonlocal calls
            calls += 1
            return {"masks": [], "scores": []} if calls >= 3 else await response(settings, model, payload)
        fal.side_effect = no_output
    elif failure == "selection":
        def reject(*a, **kw):
            raise T.tone_mask.NoGarmentCandidate("no plausible candidate")
        monkeypatch.setattr(T.tone_mask, "produce", reject)
    elif failure == "timeout":
        async def hang(*a, **kw):
            await asyncio.Event().wait()
        monkeypatch.setattr(T.tone_mask, "candidates_for_prepared_image", hang)
        monkeypatch.setattr(T, "TIMEOUT_S", 0.02)
    elif failure == "cpu_timeout":
        # 대기 중인 CPU 작업도 전체 상한에 포함된다.
        async def queued(*a, **kw):
            await asyncio.Event().wait()
        monkeypatch.setattr(T, "run_cpu_bound", queued)
        monkeypatch.setattr(T, "TIMEOUT_S", 0.02)
    else:
        field = {"decode": "output", "target_decode": "target", "master_decode": "master"}[failure]
        kwargs[field] = InlineImage("image/png", b"broken photo")
    data, mime, record = asyncio.run(T.tune(make_settings(garment_color_tune_enabled=True, fal_key="fake"), **kwargs))
    assert data is kwargs["output"].data and mime == kwargs["output"].mime
    assert record["status"] == "skipped" and record["reason"] == reason
    if failure.endswith("decode"):
        fal.assert_not_awaited()


def test_job_cancellation_is_not_swallowed(fal):
    # 잡 취소와 워커 종료 신호는 이 단계가 삼키면 안 된다.
    fal.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(T.tune(make_settings(garment_color_tune_enabled=True, fal_key="fake"), **inputs()))


def test_cpu_work_runs_off_the_event_loop(monkeypatch, fal):
    main = threading.get_ident()
    seen = []
    for module, name in ((T, "_decode"), (T, "_target_color"), (T, "_finish"),
                         (T.fal_api, "prepare_image")):
        original = getattr(module, name)
        def check(*a, _func=original, **kw):
            seen.append(threading.get_ident())
            return _func(*a, **kw)
        monkeypatch.setattr(module, name, check)
    asyncio.run(T.tune(make_settings(garment_color_tune_enabled=True, fal_key="fake"), **inputs()))
    assert len(seen) >= 5 and all(tid != main for tid in seen)


def test_switch_environment_defaults_off(monkeypatch):
    from app.config import load_settings
    monkeypatch.delenv("GARMENT_COLOR_TUNE_ENABLED", raising=False)
    assert load_settings().garment_color_tune_enabled is False
    monkeypatch.setenv("GARMENT_COLOR_TUNE_ENABLED", "true")
    assert load_settings().garment_color_tune_enabled is True


def test_failed_qc_defense_never_calls_tune(monkeypatch):
    tuned = AsyncMock()
    monkeypatch.setattr(T, "tune", tuned)
    kwargs = inputs()
    data, mime = asyncio.run(T.after_color_check(make_settings(garment_color_tune_enabled=True),
        **kwargs, color_execution={"colorCheck": {"status": "failed"}}, warnings=[]))
    assert data is kwargs["output"].data
    tuned.assert_not_awaited()


def install_tune(monkeypatch, status="passed", *, reason=None):
    async def tune(settings, **kwargs):
        assert settings.garment_color_tune_enabled
        # 호출 시점에 QC가 이미 끝나 있어야 한다.
        assert qc.await_count == tuned.await_count
        assert status != "failed"
        return b"TUNED-" + kwargs["output"].data, "image/png", {"status": "corrected", "reason": reason}
    qc = AsyncMock(return_value={"status": status})
    tuned = AsyncMock(side_effect=tune)
    from app.agents import garment_color_qc
    monkeypatch.setattr(garment_color_qc, "check", qc)
    monkeypatch.setattr(T, "tune", tuned)
    return qc, tuned


@pytest.mark.parametrize("status", ["passed", "unavailable", "indeterminate", "failed"])
@pytest.mark.parametrize("flow_fixture", ["detail_flow", "editor_flow"])
def test_new_cut_paths_adopt_tune_only_after_accepted_qc(request, monkeypatch, flow_fixture, status):
    flow = request.getfixturevalue(flow_fixture)
    flow.app.state.settings = replace(flow.app.state.settings, garment_color_tune_enabled=True)
    from app.workers import detail_page_job, editor_image_job
    worker = detail_page_job if flow_fixture == "detail_flow" else editor_image_job
    monkeypatch.setattr(worker.repo, "get_matching_item_asset", AsyncMock(return_value="matching-asset"))
    monkeypatch.setattr(worker, "catalog_image", lambda mime, data: InlineImage(mime, data))
    if flow_fixture == "detail_flow":
        flow.board[0]["matchIds"] = ["selected-pants"]
    else:
        flow.job["payload"]["matchIds"] = ["selected-pants"]
    qc, tuned = install_tune(monkeypatch, status)
    flow.run()
    assert qc.await_count == 1
    finals = [c[2] for c in flow.calls if c[0] == "finalize"]
    if status == "failed":
        tuned.assert_not_awaited()
        assert not finals
        return
    assert tuned.await_count == 1
    assert tuned.await_args.kwargs["recipe"]["matchIds"] == ["selected-pants"]
    image = finals[0]["cut_assets"][0] if flow_fixture == "detail_flow" else finals[0]["image"]
    assert any(c[0] == "upload" and c[2] == b"TUNED-FINAL" for c in flow.calls)
    assert image["metadata"]["colorExecution"]["colorTune"]["outputSha256"] == sha(b"TUNED-FINAL")
    assert image["metadata"]["colorExecution"]["colorCheck"]["checkedOutputSha256"] == sha(b"FINAL")


@pytest.mark.parametrize("flow_fixture", ["detail_flow", "editor_flow"])
def test_over_cap_warning_is_persisted_with_cut(request, monkeypatch, flow_fixture):
    flow = request.getfixturevalue(flow_fixture)
    flow.app.state.settings = replace(flow.app.state.settings, garment_color_tune_enabled=True)
    from app.agents import garment_color_qc
    monkeypatch.setattr(garment_color_qc, "check", AsyncMock(return_value={"status": "passed"}))
    async def over_cap(settings, **kw):
        return kw["output"].data, kw["output"].mime, {"status": "skipped", "reason": "over_cap"}
    monkeypatch.setattr(T, "tune", over_cap)
    flow.run()
    final = next(c[2] for c in flow.calls if c[0] == "finalize")
    assert any(w["code"] == "color_tune_over_cap" for w in final["metadata"]["warnings"])
    image = final["cut_assets"][0] if flow_fixture == "detail_flow" else final["image"]
    assert image["sha256"] == sha(b"FINAL")


def test_tuned_hash_keeps_face_outcome_and_feeds_neck_repair(monkeypatch):
    from app.agents import garment_color
    from app.workers import detail_page_job as worker
    from conftest import worker_job
    from test_storyboard_similar_cuts import setup_generation, source, item
    from test_real_horizon_neck_repair import IDENTITY
    app, _ = setup_generation(monkeypatch)
    app.state.settings = replace(app.state.settings, garment_color_tune_enabled=True)
    block = source(colorId="blue", cutType="horizon")
    async def generate(*a, **kw):
        kw["face_pass_outcome"].update(face_pass="applied", face_recipe="master-face")
        return b"MASTER", "image/png"
    async def apply(*a, **kw):
        kw["face_pass_outcome"].update(face_pass="applied", face_recipe="color-face")
        return b"FINAL", "image/png"
    monkeypatch.setattr(worker.cut_generator, "generate", generate)
    monkeypatch.setattr(garment_color, "apply", apply)
    install_tune(monkeypatch)
    def eligible(*a, **kw):
        assert kw["outcome"] == {"face_pass": "applied", "face_recipe": "color-face"}
        return True
    repair = AsyncMock(return_value=(InlineImage("image/png", b"NECK-FINAL"), {"applied": True}))
    monkeypatch.setattr(worker.real_horizon_neck_repair, "eligible", eligible)
    monkeypatch.setattr(worker.real_horizon_neck_repair, "repair", repair)
    prepared = (*item(block), None, False, None, None, True)
    color = {"plan": garment_color.plan_for(product(), block), "target_asset": {"id": "blue-front"},
             "target_image": InlineImage("image/png", b"BLUE")}
    result = asyncio.run(worker._gen_cuts(app, worker_job(), [prepared], product(), {},
        face_identity_spec=IDENTITY, color_context={"original": color}))
    assert repair.call_args.args[2].data == b"TUNED-FINAL"
    asset = result[1][0]
    assert asset["sha256"] == sha(b"NECK-FINAL")
    assert asset["metadata"]["face_recipe"] == "color-face"
    assert asset["metadata"]["colorExecution"]["colorTune"]["outputSha256"] == sha(b"TUNED-FINAL")


@pytest.mark.parametrize("child_status", ["passed", "unavailable", "indeterminate", "failed"])
def test_detail_similar_path_and_checkpoint_reuse_do_not_tune_twice(monkeypatch, child_status):
    from app.agents import garment_color
    from app.workers import detail_page_job as worker, cut_checkpoints
    from test_storyboard_similar_cuts import setup_generation, source, child, item
    from test_detail_cut_checkpoints import _IntentDB
    from conftest import worker_job
    app, seen = setup_generation(monkeypatch)
    app.state.settings = replace(app.state.settings, garment_color_tune_enabled=True)
    db = _IntentDB()
    db.install(monkeypatch)
    original, followup = source(colorId="blue", matchIds=["resolved-pants"]), child(colorId="blue")
    async def apply(s, p, master, *a, **kw):
        return b"BLUE-" + master.data, "image/png"
    monkeypatch.setattr(garment_color, "apply", apply)
    qc, tuned = install_tune(monkeypatch)
    async def check(s, master, *a, **kw):
        return {"status": child_status if master.data.startswith(b"CHILD") else "passed"}
    qc.side_effect = check
    async def run(job_id):
        job = {**worker_job(), "id": job_id}
        store = await cut_checkpoints.CutCheckpointStore.open(app, job, identity="NONE")
        color = {"plan": garment_color.plan_for(product(), original), "target_asset": {"id": "blue-front"},
                 "target_image": worker.InlineImage("image/png", b"BLUE")}
        result = await worker._gen_cuts(app, job, [item(original), item(followup)], product(), {},
            color_context={"original": color, "next": color}, cut_checkpoints=store, failed_cuts={})
        db.error_jobs.add(job_id)
        return result
    first = asyncio.run(run("first"))
    assert qc.await_count == 2
    assert tuned.await_count == (1 if child_status == "failed" else 2)
    assert all(call.kwargs["recipe"]["matchIds"] == ["resolved-pants"] for call in tuned.await_args_list)
    for asset in first[1]:
        assert app.state.r2.get_bytes(asset["key"]).startswith(b"TUNED-BLUE-")
    if child_status == "failed":
        assert len(first[1]) == 1
        return
    second = asyncio.run(run("second"))
    assert qc.await_count == tuned.await_count == 2
    assert len(seen["vary"]) == 1
    assert len(second[1]) == 2
    assert second[1][1]["metadata"]["colorExecution"]["colorTune"]["outputSha256"] == sha(
        app.state.r2.get_bytes(second[1][1]["key"]))


@pytest.mark.parametrize("path", ["color", "similar"])
def test_editor_edit_and_similar_paths_adopt_tuned_photo(monkeypatch, path):
    from app.workers import editor_image_job as worker
    from test_garment_color_flow import (
        test_editor_color_edit_uses_stored_original_master_and_keeps_target_record as color_case,
        test_editor_similar_uses_stored_base_master_then_persists_its_varied_master as similar_case)
    # 기존 경로의 입력, 소유권, 저장 대역을 그대로 써서 실제 워커를 끝까지 실행한다.
    original_settings = make_settings
    import conftest
    monkeypatch.setattr(conftest, "make_settings", lambda **kw: original_settings(garment_color_tune_enabled=True, **kw))
    async def tune(settings, **kwargs):
        assert settings.garment_color_tune_enabled
        # 해당 기존 시나리오는 check의 대역을 각각 설치한다. 실제 호출이 끝난 뒤여야 한다.
        assert worker.garment_color_qc.check.await_count == 1
        return b"TUNED", "image/png", {"status": "corrected"}
    tuned = AsyncMock(side_effect=tune)
    monkeypatch.setattr(T, "tune", tuned)
    if path == "color":
        monkeypatch.setattr(worker.garment_color_qc, "check", AsyncMock(return_value={"status": "passed"}))
        color_case(monkeypatch)
    else:
        similar_case(monkeypatch, "passed", "base", "blue")
    assert tuned.await_count == 1
    final = worker.repo.finalize_editor_image_success.call_args.kwargs["image"]
    assert final["sha256"] == sha(b"TUNED")
    assert final["metadata"]["colorExecution"]["colorTune"]["outputSha256"] == sha(b"TUNED")
