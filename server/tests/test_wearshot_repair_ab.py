"""Local paired repair receipts must prevent drift and duplicate paid submissions."""
import asyncio
from dataclasses import asdict, replace
import importlib
import io
import json
from types import SimpleNamespace

from PIL import Image
import pytest

from app.agents import vision_llm
from app.agents.gemini_image import GeminiImageClient, InlineImage
from app.agents.wearshot_contract import image_sha256
from test_wearshot_contract_v2 import packet, png
from test_wearshot_qc_v2 import observed, mark
from conftest import make_settings


def harness():
    assert importlib.util.find_spec("app.experiments.wearshot_repair_ab"), "paired repair harness missing"
    return importlib.import_module("app.experiments.wearshot_repair_ab")


@pytest.fixture
def evidence(tmp_path):
    contract, base = packet(), png("orange")
    paths = {}
    for ref in contract.references:
        path = tmp_path / f"{ref.key}.png"
        path.write_bytes(ref.image.data)
        paths[ref.key] = path.name
    (tmp_path / "base.png").write_bytes(base.data)
    manifest = dict(schemaVersion=1, models={"image2": "gpt-image-2", "sunburst": "gpt-image-2.5-sunburst-2026-09-08"},
                    qcModel="gpt-6-astra", qcTimeoutSeconds=180, quality="medium", imageSize="2K", cases=[dict(
                        id="sample-a", repairDeclared=True, contract=contract.to_dict(), referencePaths=paths,
                        base=dict(path="base.png", mime=base.mime, sha256=image_sha256(base)),
                        outputSize="1360x2048")])
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    return SimpleNamespace(path=path, out=tmp_path / "results", manifest=manifest, contract=contract, base=base)


@pytest.fixture
def external(monkeypatch, evidence):
    calls = []
    async def judge(settings, model, prompt, images, schema, timeout):
        calls.append(("qc", model, images))
        assert model == "gpt-6-astra"
        if "garments" in schema["properties"]:
            raw = observed(evidence.contract)
            if images[-1].data == evidence.base.data:
                raw["globalChecks"]["capture"] = mark("FAIL")
                assert images == [*(r.image for r in evidence.contract.references), evidence.base]
            else:
                assert images[0] == evidence.base
                raw["protectedChecks"] = {key: mark() for key in schema["properties"]["protectedChecks"]["properties"]}
            return raw
        return dict(viewAdequate=True, selectedFaceRelation="clear_target", strongestTargetEvidence="Visible target facial geometry",
                    strongestSourceEvidence="Source geometry absent", remainingAmbiguity="None observed")
    async def generate(self, model, prompt, images, image_size, **kwargs):
        calls.append(("repair", model, images, prompt, kwargs, image_size))
        assert images[0] == evidence.base
        assert kwargs == dict(openai_preserve_input_bytes=True, openai_output_size="1360x2048")
        result = io.BytesIO()
        Image.new("RGB", (1360, 2048), "green").save(result, format="PNG")
        return SimpleNamespace(image=result.getvalue(), mime="image/png", usage={}, latency_ms=1)
    monkeypatch.setattr(vision_llm, "_call_gpt", judge)
    monkeypatch.setattr(GeminiImageClient, "generate_content_image", generate)
    return calls


def run(e, **kwargs):
    settings = make_settings(openai_api_key="test", gemini_api_key=None, vertex_project=None, vertex_location="global",
                             analysis_timeout_seconds=1, mannequin_image_size="1K")
    return asyncio.run(harness().run_manifest(e.path, e.out, settings=settings, **kwargs))


def rewrite(e):
    e.path.write_text(json.dumps(e.manifest))


def test_default_dry_run_has_no_calls_or_output_writes(evidence, external):
    results = run(evidence)
    assert results[0]["status"] == "dry-run"
    assert not evidence.out.exists()
    assert external == []


@pytest.mark.parametrize("change", ["model", "undeclared", "fingerprint", "reference", "base", "size", "quality", "reference_order"])
def test_frozen_inputs_and_allowlist_fail_before_calls(evidence, external, change):
    row = evidence.manifest["cases"][0]
    if change == "model": evidence.manifest["models"]["sunburst"] = "gpt-image-other"
    if change == "undeclared": row["repairDeclared"] = False
    if change == "fingerprint": row["contract"]["fingerprint"] = "0" * 64
    if change == "reference": (evidence.path.parent / "face.png").write_bytes(png("pink").data)
    if change == "base": row["base"]["sha256"] = "0" * 64
    if change == "size": row["outputSize"] = "1024x1024"
    if change == "quality": evidence.manifest["quality"] = "high"
    if change == "reference_order": row["contract"]["references"].reverse()
    rewrite(evidence)
    with pytest.raises(ValueError): run(evidence)
    assert external == [] and not evidence.out.exists()


def test_pair_uses_same_production_repair_inputs_except_model(evidence, external):
    base_qc = run(evidence, mode="qc-base", case="sample-a")[0]
    assert base_qc["status"] == "completed" and not base_qc["releaseAllowed"]
    first = run(evidence, mode="repair", case="sample-a", arm="sunburst")[0]
    second = run(evidence, mode="repair", case="sample-a", arm="image2")[0]
    assert first["status"] == second["status"] == "completed"
    assert first["request"]["pairSha256"] == second["request"]["pairSha256"]
    assert first["request"]["repairPlan"]["failedAxes"] == ["capture"]
    assert "identity" in first["request"]["repairPlan"]["approvedAxes"]
    a, b = [call for call in external if call[0] == "repair"]
    assert (a[1], b[1]) == ("gpt-image-2.5-sunburst-2026-09-08", "gpt-image-2")
    assert a[2:] == b[2:]
    assert first["actualRequest"]["model"] == a[1]
    assert first["actualRequest"]["promptSha256"] == first["request"]["promptSha256"]
    assert first["output"]["width"] == 1360
    reviewed = run(evidence, mode="qc-repair", case="sample-a", arm="sunburst")[0]
    assert reviewed["status"] == "completed" and reviewed["releaseAllowed"]
    assert reviewed["request"]["sourceGeneration"]["sha256"]
    assert reviewed["verdict"]["repairBaseSha256"] == image_sha256(evidence.base)


@pytest.mark.parametrize("mode,arm", [("qc-base", None), ("repair", "image2"), ("qc-repair", "image2")])
def test_completed_receipt_blocks_repeated_submission(evidence, external, mode, arm):
    run(evidence, mode="qc-base", case="sample-a")
    if mode in {"repair", "qc-repair"}: run(evidence, mode="repair", case="sample-a", arm="image2")
    if mode == "qc-repair": run(evidence, mode=mode, case="sample-a", arm=arm)
    count = len(external)
    with pytest.raises(ValueError): run(evidence, mode=mode, case="sample-a", arm=arm)
    assert len(external) == count


@pytest.mark.parametrize("change", ["manifest", "base_qc", "code", "generation", "output", "legacy"])
def test_provenance_mutation_blocks_followup_before_calls(evidence, external, monkeypatch, change):
    run(evidence, mode="qc-base", case="sample-a")
    run(evidence, mode="repair", case="sample-a", arm="image2")
    mode, arm = "qc-repair", "image2"
    if change == "manifest":
        evidence.manifest["qcModel"] = "gpt-other"
        rewrite(evidence)
    elif change == "code": monkeypatch.setattr(harness(), "_code_versions", lambda: {"changed": "0" * 64})
    elif change == "output": (evidence.out / "sample-a.repair.image2.png").write_bytes(png("black").data)
    else:
        name = "sample-a.qc-base.receipt.json" if change == "base_qc" else "sample-a.repair.image2.receipt.json"
        path = evidence.out / name
        receipt = json.loads(path.read_text())
        if change == "base_qc":
            receipt["verdict"]["observations"]["globalChecks"]["capture"] = mark("PASS")
            mode, arm = "repair", "sunburst"
        elif change == "legacy": receipt["request"]["contract"]["contractVersion"] = "generic_v1"
        else: receipt["actualRequest"]["model"] = "gpt-image-other"
        path.write_text(json.dumps(receipt))
    count = len(external)
    with pytest.raises(ValueError): run(evidence, mode=mode, case="sample-a", arm=arm)
    assert len(external) == count


@pytest.mark.parametrize("failure", ["provider", "dimensions", "interrupt"])
def test_failed_or_uncertain_attempt_is_durable_and_never_resubmitted(evidence, external, monkeypatch, failure):
    run(evidence, mode="qc-base", case="sample-a")
    calls = []
    async def bad(self, *args, **kwargs):
        calls.append(1)
        if failure == "interrupt": raise KeyboardInterrupt()
        if failure == "provider": raise RuntimeError("OpenAI 403: SECRET")
        return SimpleNamespace(image=png().data, mime="image/png", usage={}, latency_ms=1)
    monkeypatch.setattr(GeminiImageClient, "generate_content_image", bad)
    if failure == "interrupt":
        with pytest.raises(KeyboardInterrupt): run(evidence, mode="repair", case="sample-a", arm="sunburst")
    else:
        result = run(evidence, mode="repair", case="sample-a", arm="sunburst")[0]
        assert result["status"] == "failed" and "SECRET" not in json.dumps(result)
    assert (evidence.out / "sample-a.repair.sunburst.started.json").exists()
    with pytest.raises(ValueError): run(evidence, mode="repair", case="sample-a", arm="sunburst")
    assert calls == [1]


def test_live_modes_require_one_case_and_one_explicit_arm(evidence, external):
    for kwargs in [dict(mode="qc-base"), dict(mode="repair", case="sample-a"),
                   dict(mode="qc-repair", case="sample-a", arm="both")]:
        with pytest.raises(ValueError): run(evidence, **kwargs)
    assert not evidence.out.exists() and external == []


def test_cli_default_never_loads_credentials_or_writes(evidence, external, monkeypatch, capsys):
    h = harness()
    def forbidden():
        pytest.fail("dry-run loaded live configuration")
    monkeypatch.setattr(h, "load_settings", forbidden)
    assert h.main(["--manifest", str(evidence.path), "--output-dir", str(evidence.out)]) == 0
    assert json.loads(capsys.readouterr().out) == [{"caseId": "sample-a", "status": "dry-run"}]
    assert not evidence.out.exists() and external == []


def test_unavailable_judge_writes_failed_receipt_without_fabricating_pass(evidence, external, monkeypatch):
    async def unavailable(*args, **kwargs):
        raise RuntimeError("SECRET")
    monkeypatch.setattr(vision_llm, "_call_gpt", unavailable)
    result = run(evidence, mode="qc-base", case="sample-a")[0]
    assert result["status"] == "failed"
    assert not result["releaseAllowed"]
    assert result["verdict"]["status"] == "UNJUDGEABLE"
    assert "SECRET" not in json.dumps(result)
    with pytest.raises(ValueError): run(evidence, mode="repair", case="sample-a", arm="sunburst")


@pytest.mark.parametrize("compatible_example", [True, False])
def test_shared_aspect_tolerance_accepts_native_rounding_and_checks_original_example(evidence, external, compatible_example):
    def sized(size):
        buffer = io.BytesIO()
        Image.new("RGB", size, "white").save(buffer, format="PNG")
        return InlineImage("image/png", buffer.getvalue())
    base, example = sized((1134, 1390)), sized((720, 883) if compatible_example else (800, 800))
    refs = tuple(replace(r, image=example) if r.key == "example" else r for r in evidence.contract.references)
    contract = packet(references=refs)
    (evidence.path.parent / "example.png").write_bytes(example.data)
    (evidence.path.parent / "base.png").write_bytes(base.data)
    row = evidence.manifest["cases"][0]
    row["contract"] = contract.to_dict()
    row["base"]["sha256"] = image_sha256(base)
    row["outputSize"] = "1664x2048"
    rewrite(evidence)
    if compatible_example:
        assert run(evidence)[0]["status"] == "dry-run"
    else:
        with pytest.raises(ValueError): run(evidence)
    assert external == [] and not evidence.out.exists()


@pytest.mark.parametrize("all_machine_pass", [True, False])
def test_human_failure_is_separate_from_machine_qc_and_identical_in_both_arms(evidence, external, monkeypatch, all_machine_pass):
    feedback = dict(failedAxes=["garment:top:detail:hem"], protectedAxes=["identity", "garment:top:color"],
                    evidence="Operator review of the frozen base identified a visible detail discrepancy.")
    evidence.manifest["cases"][0]["humanFeedback"] = feedback
    rewrite(evidence)
    judge = vision_llm._call_gpt
    async def review(*args, **kwargs):
        result = await judge(*args, **kwargs)
        if all_machine_pass and "garments" in result:
            result["globalChecks"]["capture"] = mark()
        return result
    monkeypatch.setattr(vision_llm, "_call_gpt", review)
    base = run(evidence, mode="qc-base", case="sample-a")[0]
    assert base["verdict"]["attributes"]["garment:top:detail:hem"]["status"] == "PASS"
    a = run(evidence, mode="repair", case="sample-a", arm="sunburst")[0]
    b = run(evidence, mode="repair", case="sample-a", arm="image2")[0]
    assert a["status"] == b["status"] == "completed"
    assert a["request"]["humanFeedback"] == feedback
    assert a["request"]["repairPlan"] == b["request"]["repairPlan"]
    assert a["request"]["pairSha256"] == b["request"]["pairSha256"]
    plan = a["request"]["repairPlan"]
    assert "garment:top:detail:hem" in plan["failedAxes"]
    assert "garment:top:detail:hem" not in plan["approvedAxes"]
    assert set(feedback["protectedAxes"]) <= set(plan["approvedAxes"])
    assert ("capture" not in plan["failedAxes"]) == all_machine_pass
    prompt = next(c[3] for c in external if c[0] == "repair")
    assert feedback["evidence"] not in prompt


@pytest.mark.parametrize("failure", ["unknown", "na", "conflict", "variation_conflict", "blank_evidence"])
def test_invalid_human_feedback_holds_before_any_submission(evidence, external, failure):
    feedback = dict(failedAxes=["capture"], protectedAxes=["identity"], evidence="Operator observed frozen base")
    if failure == "unknown": feedback["failedAxes"] = ["invented"]
    if failure == "na": feedback["failedAxes"] = ["body"]
    if failure == "conflict": feedback["protectedAxes"] = ["capture"]
    if failure == "variation_conflict": feedback.update(failedAxes=["variation"], protectedAxes=["pose"])
    if failure == "blank_evidence": feedback["evidence"] = " "
    evidence.manifest["cases"][0]["humanFeedback"] = feedback
    rewrite(evidence)
    with pytest.raises(ValueError): run(evidence, mode="qc-base", case="sample-a")
    assert external == [] and not evidence.out.exists()


@pytest.mark.parametrize("axis", ["capture", "identity"])
def test_human_protection_cannot_promote_machine_or_focused_failure(evidence, external, monkeypatch, axis):
    evidence.manifest["cases"][0]["humanFeedback"] = dict(
        failedAxes=["garment:top:detail:hem"], protectedAxes=[axis], evidence="Operator requested protection")
    rewrite(evidence)
    judge = vision_llm._call_gpt
    async def review(*args, **kwargs):
        result = await judge(*args, **kwargs)
        if axis == "identity" and "selectedFaceRelation" in result:
            result["selectedFaceRelation"] = "source_retained"
        return result
    monkeypatch.setattr(vision_llm, "_call_gpt", review)
    run(evidence, mode="qc-base", case="sample-a")
    count = len(external)
    with pytest.raises(ValueError): run(evidence, mode="repair", case="sample-a", arm="sunburst")
    assert len(external) == count


def test_human_variation_failure_expands_selected_axis_and_preserves_other_scene_axis(evidence, external):
    evidence.manifest["cases"][0]["humanFeedback"] = dict(
        failedAxes=["variation"], protectedAxes=["background"], evidence="Operator found no minimum variation")
    rewrite(evidence)
    run(evidence, mode="qc-base", case="sample-a")
    result = run(evidence, mode="repair", case="sample-a", arm="sunburst")[0]
    plan = result["request"]["repairPlan"]
    assert set(plan["failedAxes"]) == {"capture", "variation", "pose"}
    assert "pose" not in plan["approvedAxes"] and "background" in plan["approvedAxes"]


def test_real_frozen_settings_are_copied_and_forward_explicit_judge_and_image_size(evidence, external):
    settings = make_settings(openai_api_key="test", mannequin_image_size="1K", detail_cut_image_size="1K",
                             wearshot_qc_model="gpt-5.4-mini", cut_identity_review_model="gpt-5.4-mini")
    original = asdict(settings)
    h = harness()
    base = asyncio.run(h.run_manifest(evidence.path, evidence.out, mode="qc-base", case="sample-a", settings=settings))[0]
    assert base["status"] == "completed"
    repair = asyncio.run(h.run_manifest(evidence.path, evidence.out, mode="repair", case="sample-a", arm="sunburst", settings=settings))[0]
    assert repair["status"] == "completed"
    assert repair["actualRequest"]["imageSize"] == "2K"
    assert base["verdict"]["model"] == base["verdict"]["identityReview"]["model"] == "gpt-6-astra"
    assert asdict(settings) == original


@pytest.mark.parametrize("value", [None, 0, -1, 601, 10 ** 400, float("inf"), float("nan"), True, "180"])
def test_invalid_frozen_qc_deadline_rejected_without_calls_or_writes(evidence, external, value):
    evidence.manifest["qcTimeoutSeconds"] = value
    rewrite(evidence)
    with pytest.raises(ValueError): run(evidence)
    assert external == [] and not evidence.out.exists()


def test_frozen_qc_deadline_required(evidence, external):
    del evidence.manifest["qcTimeoutSeconds"]
    rewrite(evidence)
    with pytest.raises(ValueError): run(evidence)
    assert external == [] and not evidence.out.exists()


def test_manifest_deadline_overrides_real_settings_and_is_recorded_for_both_judges(evidence, external, monkeypatch):
    evidence.manifest["qcTimeoutSeconds"] = 95.5
    rewrite(evidence)
    settings = make_settings(openai_api_key="test")
    before, deadlines = asdict(settings), []
    transport = vision_llm._call_gpt
    async def judge(configured, model, prompt, images, schema, timeout):
        deadlines.append((configured.analysis_timeout_seconds, timeout))
        return await transport(configured, model, prompt, images, schema, timeout)
    monkeypatch.setattr(vision_llm, "_call_gpt", judge)
    dry = asyncio.run(harness().run_manifest(evidence.path, evidence.out))[0]
    assert dry["request"]["qcTimeoutSeconds"] == 95.5
    assert not evidence.out.exists() and deadlines == []
    receipt = asyncio.run(harness().run_manifest(evidence.path, evidence.out, mode="qc-base", case="sample-a", settings=settings))[0]
    assert receipt["status"] == "completed" and receipt["request"]["qcTimeoutSeconds"] == 95.5
    assert deadlines == [(95.5, 95.5), (95.5, 95.5)]
    assert receipt["verdict"]["timeoutSeconds"] == receipt["verdict"]["identityReview"]["timeoutSeconds"] == 95.5
    assert asdict(settings) == before


def test_changed_qc_deadline_invalidates_prior_receipt_before_repair(evidence, external):
    run(evidence, mode="qc-base", case="sample-a")
    original = (evidence.out / "sample-a.qc-base.receipt.json").read_bytes()
    count = len(external)
    evidence.manifest["qcTimeoutSeconds"] = 90
    rewrite(evidence)
    with pytest.raises(ValueError): run(evidence, mode="repair", case="sample-a", arm="sunburst")
    assert len(external) == count
    assert (evidence.out / "sample-a.qc-base.receipt.json").read_bytes() == original
