import asyncio
from types import SimpleNamespace

import pytest

from app.agents.cut_plan import CutPlanError
from app.agents.gemini_image import InlineImage
from app.agents.vision_llm import VisionError
from app.workers import detail_page_job as dpj
from app.workers import editor_image_job as eij
from conftest import fake_worker_app, make_settings, worker_job


class _RecordingR2:
    def __init__(self, events):
        self.events = events
        self.saved = []

    def get_bytes(self, key):
        return b"PRODUCT"

    def put_bytes(self, key, data, mime, cache=None):
        self.events.append("save")
        self.saved.append(data)

    def public_url(self, key):
        # 실제 R2.public_url 미러 — cut_done previewUrl(editor_wait_dev_spec §2-1)
        return f"https://r2.test/{key}"

    def preview_url(self, key, expires=3600):
        return f"https://r2.test/{key}"

    def delete(self, key):
        return None


def _detail_spec():
    return {
        "id": "b1",
        "cutType": "product",
        "direction": "front",
        "shot": "ghost",
        "faceExposure": None,
        "pose": "auto",
        "refScope": "all",
    }


def _run_detail_cut(
    monkeypatch, *, qc_mode="shadow", manifest="1. PRODUCT — front", events=None,
    generated_outputs=None, confirmed_packet=None, spec=None, settings_overrides=None,
    release_policy=None, reference_images=None,
):
    events = [] if events is None else events
    captured = {}

    generated_outputs = list(generated_outputs or [b"INITIAL"])

    async def fake_generate(settings, *_args, **kwargs):
        events.append("generate")
        captured.setdefault("generateModels", []).append(settings.model_image_high)
        captured.setdefault("generateKwargs", []).append(kwargs)
        output = generated_outputs.pop(0) if generated_outputs else b"INITIAL"
        if isinstance(output, Exception):
            raise output
        return output, "image/png"

    async def fake_best_of(settings, product_images, initial, generate_candidate):
        events.append("garment")
        assert initial.data == b"INITIAL"
        return InlineImage("image/png", b"CHOSEN"), {"chosenIndex": 0}, []

    async def fake_emit(_pool, _job_id, event, data):
        captured.setdefault("emitted", []).append((event, data))

    monkeypatch.setattr(dpj.cut_generator, "generate", fake_generate)
    monkeypatch.setattr(dpj.image_qc, "best_of", fake_best_of)
    monkeypatch.setattr(dpj, "_emit", fake_emit)

    r2 = _RecordingR2(events)
    setting_values = {
        "gemini_api_key": "x",
        "r2_bucket": "b",
        "model_image_high": "gemini-3-pro-image",
        "model_detail_cut": "gpt-image-2-2026-04-21",
        "garment_qc_mode": "off",
        "cut_output_qc_mode": qc_mode,
    }
    setting_values.update(settings_overrides or {})
    app = fake_worker_app(make_settings(**setting_values), r2=r2)
    if release_policy is not None:
        object.__setattr__(app.state.settings, "cut_output_release_policy", release_policy)
    product_image = InlineImage("image/png", b"PRODUCT")
    prepared = (spec or _detail_spec(), reference_images or [product_image], manifest, False, [product_image])
    if confirmed_packet is not None:
        prepared = (*prepared, None, False, None, confirmed_packet)
    result = asyncio.run(dpj._gen_cuts(
        app,
        worker_job(),
        [prepared],
        {"clothingType": "top"},
        {"fitProfile": {"axes": {"fit": "regular"}}},
    ))
    captured.update(events=events, r2=r2, result=result)
    return captured


def test_confirmed_profile_is_first_result_only_and_uses_confirmed_qc_authority(
    monkeypatch,
):
    captured = {}

    async def recording_cut_qc(
        settings, plan, references, generated, *, authority_profile,
        confirmed_prompt_input,
    ):
        captured.update(
            authority_profile=authority_profile,
            confirmed_prompt_input=confirmed_prompt_input,
            generated=generated,
            references=references,
        )
        return _qc_result()

    monkeypatch.setattr(dpj.cut_output_qc, "verdict", recording_cut_qc)
    prompt_input = object()
    run = _run_detail_cut(
        monkeypatch,
        confirmed_packet=SimpleNamespace(prompt_input=prompt_input),
    )

    assert run["events"] == ["generate", "save"]
    assert run["generateKwargs"] == [
        {"confirmed_prompt_input": prompt_input},
    ]
    assert run["result"][3] == []  # no garment best-of insertion
    assert captured["authority_profile"] == "confirmed_gpt_v1"
    assert captured["confirmed_prompt_input"] is prompt_input
    assert captured["generated"].data == b"INITIAL"


def test_confirmed_framing_crop_edits_stage1_with_exact_example_contract(monkeypatch):
    captured = {}
    prompt_input = object()

    async def fake_cut_qc(
        settings, plan, references, generated, *, authority_profile,
        confirmed_prompt_input,
    ):
        captured.setdefault("qcInputs", []).append(confirmed_prompt_input)
        return _qc_result("framingCrop") if generated.data == b"INITIAL" else _qc_result()

    async def fake_repair(
        settings, gemini, cut_spec, product, source, *, qc_corrections,
        confirmed_prompt_input,
    ):
        captured.update(
            source=source,
            corrections=qc_corrections,
            repairPromptInput=confirmed_prompt_input,
        )
        return b"CROPPED", "image/png"

    monkeypatch.setattr(dpj.cut_output_qc, "verdict", fake_cut_qc)
    monkeypatch.setattr(dpj.cut_generator, "repair", fake_repair)
    run = _run_detail_cut(
        monkeypatch,
        qc_mode="repair",
        confirmed_packet=SimpleNamespace(prompt_input=prompt_input),
    )

    assert captured["source"].data == b"INITIAL"
    assert captured["corrections"] == (dpj.cut_output_qc._CORRECTIONS["framingCrop"],)
    assert captured["repairPromptInput"] is prompt_input
    assert captured["qcInputs"] == [prompt_input, prompt_input]
    assert run["r2"].saved == [b"CROPPED"]
    repair = run["result"][4][0]["repair"]
    assert repair["route"] == "EDIT_STAGE1"
    assert repair["accepted"] is True


def test_signature_cut_keeps_main_profile_settings_in_detail_worker(monkeypatch):
    signature = {
        "id": "sig-block",
        "cutType": "styling",
        "direction": "front",
        "shot": "medium",
        "pose": "auto",
        "refScope": "all",
        "exampleId": "sig_men_01",
    }
    run = _run_detail_cut(
        monkeypatch,
        qc_mode="off",
        spec=signature,
        settings_overrides={
            "openai_api_key": None,
            "model_image_signature": "gpt-image-2",
        },
    )

    # Missing OpenAI key must still fall back to shared Gemini as main's signature
    # profile specifies; AG-06's confirmed-detail GPT override must not intercept it.
    assert run["generateModels"] == ["gemini-3-pro-image"]


def _qc_result(*failed_gates):
    raw = {
        "gates": [
            {"gate": gate, "status": "PASS", "evidence": f"evidence for {gate}"}
            for gate in dpj.cut_output_qc.GATES
        ]
    }
    for gate in failed_gates:
        raw["gates"][dpj.cut_output_qc.GATES.index(gate)]["status"] = "FAIL"
    return dpj.cut_output_qc.validate(raw)


@pytest.mark.parametrize("failure", ["modelIdentity", "garmentColor", "matchingGarmentIdentity", "outage", "disabled"])
def test_critical_release_holds_before_any_delivery_side_effect(monkeypatch, failure):
    cleanup_calls = []

    async def cleanup(*args, **kwargs):
        cleanup_calls.append(kwargs)
        return "cleanup-id"

    async def qc(*args, **kwargs):
        if failure == "outage":
            raise VisionError("provider secret must never reach event")
        return _qc_result(failure)

    monkeypatch.setattr(dpj.cut_output_qc, "verdict", qc)
    monkeypatch.setattr(dpj.repo, "create_ai_output_cleanup_intent", cleanup)
    run = _run_detail_cut(monkeypatch, release_policy="critical", qc_mode="off" if failure == "disabled" else "shadow")
    assert run["r2"].saved == []
    assert cleanup_calls == []
    assert run["result"][0] == run["result"][1] == []
    steps = [data for event, data in run["emitted"] if event == "step"]
    assert [step["status"] for step in steps] == ["cut_start", "cut_failed"]
    assert steps[-1]["reason"] == "quality_review_required"
    assert steps[-1]["qualityReview"]["allowed"] is False
    assert "provider secret" not in str(steps)
    assert "CHOSEN" not in str(steps)


@pytest.mark.parametrize("repair_outcome,want_saved", [
    ("accepted", [b"EDITED"]), ("rejected", [b"CHOSEN"]),
    ("critical_remains", []), ("error", []),
])
def test_critical_release_checks_the_actual_selected_repair(monkeypatch, repair_outcome, want_saved):
    from app.agents import vision_llm
    from test_cut_identity_review import face_raw

    reviewed = []

    async def face_transport(settings, model, prompt, images, schema, timeout, **kwargs):
        reviewed.append(images[-1].data)
        return face_raw()

    monkeypatch.setattr(vision_llm, "_call_gpt", face_transport)
    async def qc(settings, plan, references, generated):
        if generated.data == b"CHOSEN":
            return (_qc_result("framingDirectionFacePose") if repair_outcome == "rejected"
                    else _qc_result("framingDirectionFacePose", "modelIdentity", "garmentColor"))
        if repair_outcome == "rejected":
            return _qc_result("modelIdentity")
        if repair_outcome == "critical_remains":
            return _qc_result("modelIdentity")
        return _qc_result()

    async def repaired(*args, **kwargs):
        if repair_outcome == "error":
            raise VisionError("repair unavailable")
        return b"EDITED", "image/png"

    monkeypatch.setattr(dpj.cut_output_qc, "verdict", qc)
    monkeypatch.setattr(dpj.cut_generator, "repair", repaired)
    stage2 = VisionError("repair unavailable") if repair_outcome == "error" else b"EDITED"
    run = _run_detail_cut(
        monkeypatch, release_policy="critical", qc_mode="repair",
        generated_outputs=[b"INITIAL", stage2],
        manifest="1. PRODUCT — front\n2. MODEL FACE — selected target",
        reference_images=[InlineImage("image/png", b"PRODUCT"), InlineImage("image/png", b"FACE")],
        settings_overrides={"openai_api_key": "test"},
    )
    assert run["r2"].saved == want_saved
    statuses = [data["status"] for event, data in run["emitted"] if event == "step"]
    assert statuses[-1] == ("cut_done" if want_saved else "cut_failed")
    assert reviewed == want_saved
    if want_saved:
        selected = run["result"][4][0]
        if repair_outcome == "accepted":
            assert "identityReview" not in selected
            selected = selected["repair"]["stage2Qc"]
        else:
            assert "identityReview" not in selected["repair"]["stage2Qc"]
        assert selected["identityReview"]["status"] == "PASS"


@pytest.mark.parametrize("policy,primary,secondary,want_saved,want_calls", [
    ("critical", "PASS", "mixed", [], 1),
    ("critical", "PASS", "clear_target", [b"CHOSEN"], 1),
    ("critical", "PASS", "error", [], 1),
    ("critical", "FAIL", "clear_target", [], 0),
    ("critical", "UNJUDGEABLE", "clear_target", [], 0),
    ("critical", "NA", "clear_target", [b"CHOSEN"], 0),
    ("off", "PASS", "mixed", [b"CHOSEN"], 0),
])
def test_real_qc_and_release_require_selected_visible_face_confirmation(
    monkeypatch, policy, primary, secondary, want_saved, want_calls,
):
    from app.agents import vision_llm
    from test_cut_identity_review import face_raw

    face_inputs = []

    async def primary_transport(settings, prompt, images, schema):
        return {"gates": [
            {"gate": gate, "status": primary if gate == "modelIdentity" and primary != "NA" else "PASS", "evidence": "Visible comparison of the required features."}
            for gate in dpj.cut_output_qc.GATES
        ]}, "gpt"

    async def face_transport(settings, model, prompt, images, schema, timeout, **kwargs):
        face_inputs.append([image.data for image in images])
        if secondary == "error":
            raise VisionError("secret-provider-error")
        return face_raw(secondary)

    monkeypatch.setattr(dpj.cut_output_qc, "analyze_with_fallback", primary_transport)
    monkeypatch.setattr(vision_llm, "_call_gpt", face_transport)
    run = _run_detail_cut(
        monkeypatch, release_policy=policy,
        spec={**_detail_spec(), "cutType": "styling", "shot": "full", "modelId": "selected-model", "faceExposure": "hide" if primary == "NA" else "show"},
        manifest="1. PRODUCT — front\n2. MODEL FACE — target\n3. EXAMPLE (scope: all) — source",
        reference_images=[InlineImage("image/png", data) for data in (b"PRODUCT", b"FACE", b"SOURCE")],
        settings_overrides={"openai_api_key": "test"},
    )
    assert run["r2"].saved == want_saved
    assert face_inputs == ([[b"FACE", b"SOURCE", b"CHOSEN"]] if want_calls else [])
    steps = [data for event, data in run["emitted"] if event == "step"]
    assert steps[-1]["status"] == ("cut_done" if want_saved else "cut_failed")
    assert "secret-provider-error" not in str(steps)
    if want_saved and want_calls:
        assert run["result"][4][0]["identityReview"]["raw"]["selectedFaceRelation"] == "clear_target"


@pytest.mark.parametrize("bad_review", ["stale", "raises", "malformed"])
def test_worker_rejects_unbound_or_broken_face_review(monkeypatch, bad_review):
    from app.agents import cut_identity_review
    reviewed = []

    async def primary(*args, **kwargs):
        return _qc_result()

    async def bad(*args, **kwargs):
        reviewed.append(args[-1].data)
        if bad_review == "raises":
            raise RuntimeError("secret-review-failure")
        if bad_review == "malformed":
            return None
        return {"status": "PASS", "evidence": "Visible target eyelids match.", "candidateSha256": "stale-other-image"}

    monkeypatch.setattr(dpj.cut_output_qc, "verdict", primary)
    monkeypatch.setattr(cut_identity_review, "verdict", bad)
    run = _run_detail_cut(monkeypatch, release_policy="critical")
    assert run["r2"].saved == []
    assert reviewed == [b"CHOSEN"]
    steps = [data for event, data in run["emitted"] if event == "step"]
    assert steps[-1]["status"] == "cut_failed"
    assert steps[-1]["reason"] == "quality_review_required"
    assert "secret-review-failure" not in str(steps)


@pytest.mark.parametrize("scenario,want_saved,want_color", [
    ("preserved", [b"EDITED"], True),
    ("target_shifted", [], True),
    ("matching_shifted", [], True),
    ("uncertain", [], True),
    ("baseline_wrong", [b"EDITED"], False),
    ("global_regeneration", [b"EDITED"], False),
    ("lighting_repair", [b"EDITED"], False),
    ("rejected", [b"CHOSEN"], False),
    ("off", [b"EDITED"], False),
])
def test_local_color_witness_controls_delivery_without_freezing_global_repairs(monkeypatch, scenario, want_saved, want_color):
    from app.agents import vision_llm
    from test_cut_color_review import color_raw
    from test_cut_identity_review import face_raw

    color_inputs, face_inputs, repair_inputs, cleanup_calls = [], [], [], []

    async def primary(settings, plan, references, generated):
        if generated.data == b"CHOSEN":
            failed = {"baseline_wrong": "garmentColor", "global_regeneration": "modelIdentity",
                      "lighting_repair": "lightingShadowReflectionDrape"}.get(scenario, "framingCrop")
            return _qc_result(failed)
        return _qc_result("modelIdentity") if scenario == "rejected" else _qc_result()

    async def repair(settings, gemini, block, product, source, **kwargs):
        repair_inputs.append(source.data)
        return b"EDITED", "image/png"

    async def transport(settings, model, prompt, images, schema, timeout):
        if "target" in schema["properties"]:
            color_inputs.append([image.data for image in images])
            return color_raw(target="shifted" if scenario == "target_shifted" else "uncertain" if scenario == "uncertain" else "preserved",
                             matching="shifted" if scenario == "matching_shifted" else "preserved")
        face_inputs.append(images[-1].data)
        return face_raw()

    async def cleanup(*args, **kwargs):
        cleanup_calls.append(kwargs)
        return "cleanup-id"

    monkeypatch.setattr(dpj.cut_output_qc, "verdict", primary)
    monkeypatch.setattr(dpj.cut_generator, "repair", repair)
    monkeypatch.setattr(vision_llm, "_call_gpt", transport)
    monkeypatch.setattr(dpj.repo, "create_ai_output_cleanup_intent", cleanup)
    run = _run_detail_cut(monkeypatch, qc_mode="repair", release_policy="off" if scenario == "off" else "critical",
        generated_outputs=[b"INITIAL", b"EDITED"], settings_overrides={"openai_api_key": "test"},
        manifest="1. PRODUCT — target\n2. MATCHING — support\n3. MODEL FACE — face",
        reference_images=[InlineImage("image/png", data) for data in (b"PRODUCT", b"SUPPORT", b"FACE")])
    assert run["r2"].saved == want_saved
    assert bool(cleanup_calls) is bool(want_saved)
    assert color_inputs == ([[b"CHOSEN", b"EDITED", b"PRODUCT", b"SUPPORT"]] if want_color else [])
    assert face_inputs == ([] if scenario == "off" else [b"CHOSEN" if scenario == "rejected" else b"EDITED"])
    assert repair_inputs == ([] if scenario in {"baseline_wrong", "global_regeneration"} else [b"CHOSEN"])
    steps = [data for event, data in run["emitted"] if event == "step"]
    assert steps[-1]["status"] == ("cut_done" if want_saved else "cut_failed")
    if not want_saved:
        assert steps[-1]["qualityReview"]["blockingGates"] == ["colorReview"]
    if scenario == "preserved":
        qc = run["result"][4][0]
        assert "colorReview" not in qc
        assert qc["repair"]["stage2Qc"]["colorReview"]["status"] == "PASS"
        assert qc["repair"]["stage2Qc"]["identityReview"]["status"] == "PASS"


@pytest.mark.parametrize("problem", ["baseline_hash", "candidate_hash", "raises", "malformed"])
def test_worker_holds_stale_or_broken_color_witness_before_storage(monkeypatch, problem):
    import hashlib
    from app.agents import cut_color_review, vision_llm
    from test_cut_color_review import color_raw
    from test_cut_identity_review import face_raw

    reviewed = []

    async def primary(settings, plan, references, generated):
        return _qc_result("framingCrop") if generated.data == b"CHOSEN" else _qc_result()

    async def repair(*args, **kwargs):
        return b"EDITED", "image/png"

    async def face(*args, **kwargs):
        return face_raw()

    async def bad(settings, refs, before, after, *, protected_axes):
        reviewed.append((before.data, after.data))
        if problem == "raises":
            raise RuntimeError("private-color-error")
        if problem == "malformed":
            return None
        result = cut_color_review.validate(color_raw(), protected_axes=protected_axes)
        result.update(baselineSha256=hashlib.sha256(b"STALE" if problem == "baseline_hash" else before.data).hexdigest(),
                      candidateSha256=hashlib.sha256(b"STALE" if problem == "candidate_hash" else after.data).hexdigest())
        return result

    monkeypatch.setattr(dpj.cut_output_qc, "verdict", primary)
    monkeypatch.setattr(dpj.cut_generator, "repair", repair)
    monkeypatch.setattr(vision_llm, "_call_gpt", face)
    monkeypatch.setattr(cut_color_review, "verdict", bad)
    run = _run_detail_cut(monkeypatch, qc_mode="repair", release_policy="critical", settings_overrides={"openai_api_key": "test"},
        manifest="1. PRODUCT — target\n2. MATCHING — support\n3. MODEL FACE — face",
        reference_images=[InlineImage("image/png", data) for data in (b"PRODUCT", b"SUPPORT", b"FACE")])
    assert reviewed == [(b"CHOSEN", b"EDITED")]
    assert run["r2"].saved == []
    steps = [data for event, data in run["emitted"] if event == "step"]
    assert steps[-1]["reason"] == "quality_review_required"
    assert steps[-1]["qualityReview"]["blockingGates"] == ["colorReview"]
    assert "private-color-error" not in str(steps)


@pytest.mark.parametrize("relation", ["preserved", "shifted"])
def test_real_primary_qc_and_local_repair_require_target_color_witness(monkeypatch, relation):
    from app.agents import vision_llm
    from test_cut_color_review import color_raw

    primary_inputs, color_inputs = [], []

    async def primary_transport(settings, prompt, images, schema):
        primary_inputs.append(images[-1].data)
        return {"gates": [
            {"gate": gate, "status": "FAIL" if gate == "framingCrop" and images[-1].data == b"CHOSEN" else "PASS",
             "evidence": "Corresponding required visible features compared."}
            for gate in dpj.cut_output_qc.GATES
        ]}, "gpt"

    async def color_transport(settings, model, prompt, images, schema, timeout):
        color_inputs.append([image.data for image in images])
        return color_raw(target=relation)

    async def repair(*args, **kwargs):
        return b"EDITED", "image/png"

    monkeypatch.setattr(dpj.cut_output_qc, "analyze_with_fallback", primary_transport)
    monkeypatch.setattr(dpj.cut_generator, "repair", repair)
    monkeypatch.setattr(vision_llm, "_call_gpt", color_transport)
    run = _run_detail_cut(monkeypatch, qc_mode="repair", release_policy="critical",
        spec={**_detail_spec(), "refScope": "none"}, settings_overrides={"openai_api_key": "test"})
    assert primary_inputs == [b"CHOSEN", b"EDITED"]
    assert color_inputs == [[b"CHOSEN", b"EDITED", b"PRODUCT"]]
    assert run["r2"].saved == ([b"EDITED"] if relation == "preserved" else [])
    if relation == "preserved":
        final_qc = run["result"][4][0]["repair"]["stage2Qc"]
        assert final_qc["colorReview"]["axes"]["matching"]["status"] == "NA"


def test_confirmed_local_edit_preserves_first_result_baseline(monkeypatch):
    from app.agents import vision_llm
    from test_cut_color_review import color_raw
    from test_cut_identity_review import face_raw

    color_inputs = []

    async def primary(settings, plan, references, generated, **kwargs):
        return _qc_result("framingCrop") if generated.data == b"INITIAL" else _qc_result()

    async def repair(*args, **kwargs):
        return b"EDITED", "image/png"

    async def transport(settings, model, prompt, images, schema, timeout):
        if "target" in schema["properties"]:
            color_inputs.append([image.data for image in images])
            return color_raw()
        return face_raw()

    monkeypatch.setattr(dpj.cut_output_qc, "verdict", primary)
    monkeypatch.setattr(dpj.cut_generator, "repair", repair)
    monkeypatch.setattr(vision_llm, "_call_gpt", transport)
    run = _run_detail_cut(monkeypatch, qc_mode="repair", release_policy="critical",
        confirmed_packet=SimpleNamespace(prompt_input=object()), settings_overrides={"openai_api_key": "test"},
        manifest="1. PRODUCT — target\n2. MATCHING — support\n3. MODEL FACE — face",
        reference_images=[InlineImage("image/png", data) for data in (b"PRODUCT", b"SUPPORT", b"FACE")])
    assert color_inputs == [[b"INITIAL", b"EDITED", b"PRODUCT", b"SUPPORT"]]
    assert run["r2"].saved == [b"EDITED"]


def test_detail_shadow_qc_observes_chosen_output_before_save(monkeypatch):
    captured = {}
    events = []

    async def recording_cut_qc(settings, plan, references, generated):
        events.append("cut-qc")
        captured.update(
            plan=plan.to_dict(), references=references, generated=generated,
        )
        return {"verdict": "FAIL", "passed": False, "gates": {}}

    monkeypatch.setattr(dpj.cut_output_qc, "verdict", recording_cut_qc)
    run = _run_detail_cut(monkeypatch, events=events)
    cut_results, assets, faces, garment_qcs, cut_qcs, page_qc, warnings = run["result"]
    assert len(cut_results) == len(assets) == 1 and faces == 0
    assert garment_qcs == [{"blockId": "b1", "chosenIndex": 0}]
    assert cut_qcs == [{
        "blockId": "b1", "verdict": "FAIL", "passed": False, "gates": {},
    }]
    assert page_qc is None
    assert warnings == []
    assert captured["generated"].data == b"CHOSEN"
    assert [reference.role for reference in captured["references"]] == ["product"]
    assert captured["plan"]["declaredFitAxes"] == ["fit"]
    assert run["r2"].saved == [b"CHOSEN"]
    assert run["events"] == ["generate", "garment", "cut-qc", "save"]


def test_detail_repair_mode_regenerates_global_failure_and_accepts_pass(monkeypatch):
    seen = []

    async def fake_cut_qc(settings, plan, references, generated):
        seen.append(generated.data)
        return (
            _qc_result("garmentConstruction")
            if generated.data == b"CHOSEN"
            else _qc_result()
        )

    monkeypatch.setattr(dpj.cut_output_qc, "verdict", fake_cut_qc)
    run = _run_detail_cut(
        monkeypatch,
        qc_mode="repair",
        generated_outputs=[b"INITIAL", b"REPAIRED"],
    )

    assert seen == [b"CHOSEN", b"REPAIRED"]
    assert run["r2"].saved == [b"REPAIRED"]
    assert run["generateModels"] == [
        "gpt-image-2-2026-04-21",
        "gpt-image-2-2026-04-21",
    ]
    assert len(run["generateKwargs"]) == 2
    assert run["generateKwargs"][1]["qc_corrections"] == (
        dpj.cut_output_qc._CORRECTIONS["garmentConstruction"],
    )
    repair = run["result"][4][0]["repair"]
    assert repair["route"] == "REGENERATE_FROM_SCRATCH"
    assert repair["attempted"] is repair["accepted"] is True
    assert repair["finalSource"] == "stage2"


def test_detail_repair_mode_edits_stage1_for_local_failure(monkeypatch):
    captured = {}

    async def fake_cut_qc(settings, plan, references, generated):
        return (
            _qc_result("framingDirectionFacePose")
            if generated.data == b"CHOSEN"
            else _qc_result()
        )

    async def fake_repair(
        settings, gemini, cut_spec, product, source, *, qc_corrections,
    ):
        captured.update(
            model=settings.model_image_high,
            source=source,
            cut_spec=cut_spec,
            product=product,
            qc_corrections=qc_corrections,
        )
        return b"EDITED", "image/png"

    monkeypatch.setattr(dpj.cut_output_qc, "verdict", fake_cut_qc)
    monkeypatch.setattr(dpj.cut_generator, "repair", fake_repair)
    run = _run_detail_cut(monkeypatch, qc_mode="repair")

    assert captured["source"].data == b"CHOSEN"
    assert captured["model"] == "gpt-image-2-2026-04-21"
    assert captured["cut_spec"]["cutType"] == "product"
    assert captured["product"]["clothingType"] == "top"
    assert captured["qc_corrections"] == (
        dpj.cut_output_qc._CORRECTIONS["framingDirectionFacePose"],
    )
    assert run["r2"].saved == [b"EDITED"]
    assert len(run["generateKwargs"]) == 1
    repair = run["result"][4][0]["repair"]
    assert repair["route"] == "EDIT_STAGE1"
    assert repair["accepted"] is True


def test_detail_repair_mode_keeps_stage1_when_stage2_regresses(monkeypatch):
    async def fake_cut_qc(settings, plan, references, generated):
        return (
            _qc_result("framingDirectionFacePose")
            if generated.data == b"CHOSEN"
            else _qc_result("framingDirectionFacePose", "modelIdentity")
        )

    async def fake_repair(*_args, **_kwargs):
        return b"REGRESSED", "image/png"

    monkeypatch.setattr(dpj.cut_output_qc, "verdict", fake_cut_qc)
    monkeypatch.setattr(dpj.cut_generator, "repair", fake_repair)
    run = _run_detail_cut(monkeypatch, qc_mode="repair")

    assert run["r2"].saved == [b"CHOSEN"]
    repair = run["result"][4][0]["repair"]
    assert repair["attempted"] is True
    assert repair["accepted"] is False
    assert repair["finalSource"] == "stage1"
    assert repair["regressions"] == ["modelIdentity"]


def test_detail_repair_keeps_five_provider_calls_in_flight(monkeypatch):
    state = {"active": 0, "peak": 0}
    all_started = asyncio.Event()

    async def fake_generate(settings, gemini, block, product, images, **kwargs):
        state["active"] += 1
        state["peak"] = max(state["peak"], state["active"])
        if state["active"] == 5:
            all_started.set()
        await asyncio.wait_for(all_started.wait(), timeout=1)
        state["active"] -= 1
        return f"IMAGE-{block['id']}".encode(), "image/png"

    async def fake_best_of(settings, product_images, initial, generate_candidate):
        return initial, None, []

    async def fake_cut_qc(settings, plan, references, generated):
        return _qc_result()

    async def fake_emit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(dpj.cut_generator, "generate", fake_generate)
    monkeypatch.setattr(dpj.image_qc, "best_of", fake_best_of)
    monkeypatch.setattr(dpj.cut_output_qc, "verdict", fake_cut_qc)
    monkeypatch.setattr(dpj, "_emit", fake_emit)

    r2 = _RecordingR2([])
    app = fake_worker_app(make_settings(
        gemini_api_key="x",
        r2_bucket="b",
        garment_qc_mode="off",
        cut_output_qc_mode="repair",
        detail_cut_concurrency=5,
        detail_cut_stagger_ms=0,
    ), r2=r2)
    product_image = InlineImage("image/png", b"PRODUCT")
    blocks = [
        {**_detail_spec(), "id": f"b{i}", "colorId": f"c{i}"}
        for i in range(5)
    ]
    result = asyncio.run(dpj._gen_cuts(
        app,
        worker_job(),
        [
            (block, [product_image], f"1. PRODUCT — {block['id']}", False, [product_image])
            for block in blocks
        ],
        {"clothingType": "top"},
        {},
    ))

    assert state["peak"] == 5
    assert len(result[0]) == len(result[1]) == 5
    assert len(r2.saved) == 5


@pytest.mark.parametrize("failure", ["provider", "manifest", "plan"])
def test_detail_shadow_qc_failure_warns_but_still_saves(monkeypatch, failure):
    calls = {"verdict": 0}

    async def unavailable(*_args, **_kwargs):
        calls["verdict"] += 1
        raise VisionError("judge unavailable")

    monkeypatch.setattr(dpj.cut_output_qc, "verdict", unavailable)
    manifest = "1. PRODUCT — front"
    if failure == "manifest":
        manifest = "1. UNKNOWN — not an authority role"
    elif failure == "plan":
        monkeypatch.setattr(
            dpj.cut_plan,
            "compile_cut_plan",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(CutPlanError("bad plan")),
        )

    run = _run_detail_cut(monkeypatch, manifest=manifest)
    _cuts, assets, _faces, _garment_qcs, cut_qcs, page_qc, warnings = run["result"]
    assert len(assets) == 1
    assert run["r2"].saved == [b"CHOSEN"]
    assert cut_qcs == []
    assert page_qc is None
    assert warnings == [{"blockId": "b1", "code": "cut_output_qc_unavailable"}]
    assert calls["verdict"] == (1 if failure == "provider" else 0)


def test_detail_off_and_passthrough_never_call_cut_qc(monkeypatch):
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("cut QC must not run")

    monkeypatch.setattr(dpj.cut_output_qc, "verdict", forbidden)
    off = _run_detail_cut(monkeypatch, qc_mode="off")
    assert off["result"][4] == []

    async def fake_emit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(dpj, "_emit", fake_emit)
    app = fake_worker_app(make_settings(
        gemini_api_key="x", r2_bucket="b", cut_output_qc_mode="shadow",
    ))
    original = {"id": "seller-detail", "width": 100, "height": 200}
    passthrough = asyncio.run(dpj._gen_cuts(
        app,
        worker_job(),
        [(_detail_spec(), [InlineImage("image/png", b"PRODUCT")], "manifest",
          False, [], None, False, original)],
        {"clothingType": "top"},
        {},
    ))
    assert passthrough[1] == []
    assert passthrough[4] == []
    assert passthrough[5] is None
    assert passthrough[6] == []


def test_detail_job_persists_cut_qc_per_block(monkeypatch):
    captured = {}

    async def fake_project(conn, uid, pid):
        return {"copywriting": False}

    async def fake_storyboard(conn, pid):
        return [{"id": "b1", "source": "ai", "cutType": "product", "shot": "ghost"}]

    async def fake_product(conn, pid):
        return {"colors": [{"isBase": True, "images": [{"slot": "Front", "id": "a1"}]}]}

    async def fake_analysis(conn, pid):
        return {}

    async def fake_asset(conn, uid, asset_id):
        return {"id": asset_id, "r2_key": "product", "mime_type": "image/png"}

    async def fake_gen_cuts(*_args, **_kwargs):
        return (
            [{"blockId": "b1", "imageUrl": "/out"}],
            [{"key": "out"}],
            0,
            [],
            [{"blockId": "b1", "verdict": "PASS", "passed": True}],
            None,
            [],
        )

    def fake_assemble(*_args, **_kwargs):
        return []

    async def fake_finalize(conn, **kwargs):
        captured.update(kwargs)
        return {"editor_blocks": [], "available": 1}

    async def fake_emit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(dpj.repo, "get_project", fake_project)
    monkeypatch.setattr(dpj.repo, "get_storyboard", fake_storyboard)
    monkeypatch.setattr(dpj.repo, "get_product", fake_product)
    monkeypatch.setattr(dpj.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(dpj.repo, "get_asset_for_user", fake_asset)
    monkeypatch.setattr(dpj, "_gen_cuts", fake_gen_cuts)
    monkeypatch.setattr(dpj.page_assembler, "assemble", fake_assemble)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_success", fake_finalize)
    monkeypatch.setattr(dpj, "_emit", fake_emit)

    app = fake_worker_app(make_settings(gemini_api_key="x", r2_bucket="b"))
    asyncio.run(dpj.run_detail_page_job(app, worker_job(credits_reserved=1)))
    assert captured["metadata"]["cutQc"] == [
        {"blockId": "b1", "verdict": "PASS", "passed": True},
    ]
    assert "garmentQc" not in captured["metadata"]


def _run_editor(monkeypatch, *, qc_mode, qc_error=False):
    captured = {"events": [], "qcCalls": 0}

    async def fake_product(conn, pid):
        return {"clothingType": "top", "colors": [{
            "id": "color", "isBase": True,
            "images": [{"slot": "Front", "id": "product"}],
        }]}

    async def fake_analysis(conn, pid):
        return {"fitProfile": {"axes": {"fit": "regular"}}}

    async def fake_asset(conn, uid, asset_id):
        return {"id": asset_id, "r2_key": "product", "mime_type": "image/png"}

    async def fake_generate(*_args, **_kwargs):
        captured["events"].append("generate")
        return b"INITIAL", "image/png"

    async def fake_best_of(settings, product_images, initial, generate_candidate):
        captured["events"].append("garment")
        return InlineImage("image/png", b"CHOSEN"), {"chosenIndex": 0}, []

    async def fake_cut_qc(settings, plan, references, generated):
        captured["qcCalls"] += 1
        captured["events"].append("cut-qc")
        captured.update(
            plan=plan.to_dict(), references=references, generated=generated,
        )
        if qc_error:
            raise VisionError("judge unavailable")
        return {"verdict": "PASS", "passed": True, "gates": {}}

    async def fake_finalize(conn, **kwargs):
        captured["finalize"] = kwargs
        return {"id": "wardrobe"}

    async def fake_emit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(eij.repo, "get_product", fake_product)
    monkeypatch.setattr(eij.repo, "get_analysis", fake_analysis)
    monkeypatch.setattr(eij.repo, "get_asset_for_user", fake_asset)
    monkeypatch.setattr(eij.cut_generator, "generate", fake_generate)
    monkeypatch.setattr(eij.image_qc, "best_of", fake_best_of)
    monkeypatch.setattr(eij.cut_output_qc, "verdict", fake_cut_qc)
    monkeypatch.setattr(eij.repo, "finalize_editor_image_success", fake_finalize)
    monkeypatch.setattr(eij, "_emit", fake_emit)

    r2 = _RecordingR2(captured["events"])
    app = fake_worker_app(
        make_settings(
            gemini_api_key="x",
            r2_bucket="b",
            garment_qc_mode="off",
            cut_output_qc_mode=qc_mode,
        ),
        r2=r2,
    )
    asyncio.run(eij.run_editor_image_job(app, worker_job({
        "mode": "new",
        "colorId": "color",
        "cutType": "product",
        "direction": "front",
        "shot": "ghost",
    })))
    return captured, r2


@pytest.mark.parametrize(
    ("qc_mode", "qc_error", "expected_calls", "expected_events"),
    [
        ("shadow", False, 1, ["generate", "garment", "cut-qc", "save"]),
        ("shadow", True, 1, ["generate", "garment", "cut-qc", "save"]),
        ("repair", False, 1, ["generate", "garment", "cut-qc", "save"]),
        ("off", False, 0, ["generate", "garment", "save"]),
    ],
)
def test_editor_cut_qc_stays_observation_only_and_persisted_separately(
    monkeypatch, qc_mode, qc_error, expected_calls, expected_events,
):
    captured, r2 = _run_editor(monkeypatch, qc_mode=qc_mode, qc_error=qc_error)
    metadata = captured["finalize"]["metadata"]
    assert captured["qcCalls"] == expected_calls
    assert captured["events"] == expected_events
    assert r2.saved == [b"CHOSEN"]
    assert metadata["garmentQc"] == {"chosenIndex": 0}
    if qc_mode in {"shadow", "repair"} and not qc_error:
        assert metadata["cutQc"] == {"verdict": "PASS", "passed": True, "gates": {}}
        assert captured["generated"].data == b"CHOSEN"
        assert [reference.role for reference in captured["references"]] == ["product"]
        assert captured["plan"]["declaredFitAxes"] == ["fit"]
        assert "warnings" not in metadata
    elif qc_mode in {"shadow", "repair"}:
        assert "cutQc" not in metadata
        assert metadata["warnings"] == [{"code": "cut_output_qc_unavailable"}]
    else:
        assert "cutQc" not in metadata
        assert "warnings" not in metadata
