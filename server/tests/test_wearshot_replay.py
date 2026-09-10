"""Replay contracts: all paid boundaries are replaced; disk/image handling stays real."""

import asyncio
import hashlib
import importlib
import json
from io import BytesIO

import pytest
from PIL import Image

from app.agents.gemini_image import GeminiError, GeminiImageResult
from app.config import load_settings


def png(color, size=(24, 32)):
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, "PNG")
    return buffer.getvalue()


def fixture_manifest(tmp_path):
    (tmp_path / "prompt.txt").write_text("Exact prompt\n한글", encoding="utf-8")
    (tmp_path / "product.png").write_bytes(png("red"))
    (tmp_path / "face.png").write_bytes(png("blue"))
    (tmp_path / "candidate.png").write_bytes(png("green"))
    manifest = {"schemaVersion": 1, "generationModel": "gpt-image-2", "qcModel": "gpt-5.4-mini", "cases": [{
        "id": "case-a", "generation": {"promptPath": "prompt.txt", "inputs": [
            {"path": "product.png", "mime": "image/png"}, {"path": "face.png", "mime": "image/png"}],
            "outputSize": "1536x2048", "imageSize": "2K", "aspectRatio": "3:4"},
        "qc": {"plan": {}, "references": [{"role": "modelFace", "path": "face.png", "mime": "image/png"},
            {"role": "product", "path": "product.png", "mime": "image/png"}], "candidatePath": "candidate.png"}}]}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    return path, manifest


def replay():
    return importlib.import_module("app.experiments.wearshot_replay")


def invoke(path, tmp_path, mode="dry-run", case=None):
    return asyncio.run(replay().run_manifest(path, tmp_path / "out", mode=mode, case=case, settings=load_settings()))


def block_paid(monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("unexpected paid boundary")
    monkeypatch.setattr(replay().GeminiImageClient, "generate_content_image", forbidden)
    monkeypatch.setattr(replay().cut_output_qc, "verdict", forbidden)


def test_default_dry_run_decodes_and_hashes_without_paid_calls(tmp_path, monkeypatch):
    path, _ = fixture_manifest(tmp_path)
    block_paid(monkeypatch)
    result = invoke(path, tmp_path)
    assert result[0]["status"] == "dry-run"
    assert result[0]["generation"]["inputs"][0]["sha256"] == hashlib.sha256(png("red")).hexdigest()
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("bad", ["missing", "invalid", "mime", "duplicate", "../escape", "/absolute", "a/b", ".."])
def test_bad_evidence_or_output_id_stops_before_any_call(tmp_path, monkeypatch, bad):
    path, manifest = fixture_manifest(tmp_path)
    block_paid(monkeypatch)
    if bad == "missing":
        (tmp_path / "product.png").unlink()
    elif bad == "invalid":
        (tmp_path / "product.png").write_bytes(b"not an image")
    elif bad == "mime":
        manifest["cases"][0]["generation"]["inputs"][0]["mime"] = "image/jpeg"
    elif bad == "duplicate":
        manifest["cases"].append(manifest["cases"][0])
    else:
        manifest["cases"][0]["id"] = bad
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        invoke(path, tmp_path, "generate")


def test_generation_preserves_order_bytes_prompt_and_native_output_receipt(tmp_path, monkeypatch):
    path, _ = fixture_manifest(tmp_path)
    output = png("yellow", (48, 64))
    async def generate(self, **kwargs):
        started = json.loads((tmp_path / "out/case-a.generate.started.json").read_text())
        assert started["status"] == "started"
        assert kwargs["model"] == "gpt-image-2"
        assert kwargs["prompt"] == "Exact prompt\n한글"
        assert [im.data for im in kwargs["images"]] == [png("red"), png("blue")]
        assert kwargs["openai_preserve_input_bytes"] is True
        assert kwargs["openai_output_size"] == "1536x2048"
        assert kwargs["timeout"] == 300
        return GeminiImageResult(output, "image/png", 42, {"total_tokens": 9})
    monkeypatch.setattr(replay().GeminiImageClient, "generate_content_image", generate)
    result = invoke(path, tmp_path, "generate")[0]
    assert (tmp_path / "out/case-a.png").read_bytes() == output
    receipt = json.loads((tmp_path / "out/case-a.generate.receipt.json").read_text())
    assert receipt == result
    assert receipt["model"] == "gpt-image-2"
    assert receipt["usage"] == {"total_tokens": 9}
    assert receipt["output"]["width"] == 48 and receipt["output"]["height"] == 64
    assert receipt["output"]["sha256"] == hashlib.sha256(output).hexdigest()
    assert receipt["prompt"]["sha256"] == hashlib.sha256("Exact prompt\n한글".encode()).hexdigest()
    assert (tmp_path / "out/case-a.prompt.txt").read_bytes() == "Exact prompt\n한글".encode()


@pytest.mark.parametrize("existing", ["case-a.generate.started.json", "case-a.png", "case-a.prompt.txt", "case-a.generate.receipt.json"])
def test_existing_artifact_prevents_uncertain_or_overwriting_generation(tmp_path, monkeypatch, existing):
    path, _ = fixture_manifest(tmp_path)
    block_paid(monkeypatch)
    out = tmp_path / "out"
    out.mkdir()
    (out / existing).write_text("preserved")
    with pytest.raises(ValueError):
        invoke(path, tmp_path, "generate")
    assert (out / existing).read_text() == "preserved"


def test_error_receipt_is_sanitized_and_never_resubmitted(tmp_path, monkeypatch):
    path, _ = fixture_manifest(tmp_path)
    async def fail(self, **kwargs):
        raise GeminiError("OpenAI 502: secret-key base64-image", billable=True)
    monkeypatch.setattr(replay().GeminiImageClient, "generate_content_image", fail)
    receipt = invoke(path, tmp_path, "generate")[0]
    assert receipt["status"] == "error"
    assert receipt["error"]["type"] == "GeminiError"
    assert receipt["error"]["httpStatus"] == 502
    assert receipt["error"]["billable"] is True
    assert "secret-key" not in json.dumps(receipt) and "base64-image" not in json.dumps(receipt)
    block_paid(monkeypatch)
    with pytest.raises(ValueError):
        invoke(path, tmp_path, "generate")


def test_qc_only_preserves_roles_pins_gpt_and_binds_candidate(tmp_path, monkeypatch):
    path, manifest = fixture_manifest(tmp_path)
    manifest["cases"][0].pop("generation")
    path.write_text(json.dumps(manifest))
    block_paid(monkeypatch)
    async def judge(settings, plan, references, generated_image, **kwargs):
        assert settings.analysis_model_order == "gpt"
        assert settings.model_text == "gpt-5.4-mini"
        assert settings.analysis_timeout_seconds == 120
        assert plan == {}
        assert [ref.role for ref in references] == ["modelFace", "product"]
        assert [ref.image.data for ref in references] == [png("blue"), png("red")]
        assert generated_image.data == png("green")
        return {"provider": "gpt", "status": "PASS", "gates": {}}
    monkeypatch.setattr(replay().cut_output_qc, "verdict", judge)
    receipt = invoke(path, tmp_path, "qc")[0]
    assert receipt["caseId"] == "case-a" and receipt["provider"] == "gpt"
    assert receipt["candidate"]["sha256"] == hashlib.sha256(png("green")).hexdigest()
    assert "sourceGeneration" not in receipt
    assert receipt["verdict"]["status"] == "PASS"
    assert json.loads((tmp_path / "out/case-a.qc.receipt.json").read_text()) == receipt


def test_qc_records_old_generation_provenance_separately_from_changed_manifest(tmp_path, monkeypatch):
    path, manifest = fixture_manifest(tmp_path)
    generated = png("yellow", (48, 64))

    async def generate(self, **kwargs):
        return GeminiImageResult(generated, "image/png", 1, None)

    monkeypatch.setattr(replay().GeminiImageClient, "generate_content_image", generate)
    original_manifest_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    invoke(path, tmp_path, "generate")
    generation_receipt_path = tmp_path / "out/case-a.generate.receipt.json"
    generation_receipt_sha = hashlib.sha256(generation_receipt_path.read_bytes()).hexdigest()

    (tmp_path / "prompt-v2.txt").write_text("Changed generation prompt", encoding="utf-8")
    generation = manifest["cases"][0]["generation"]
    generation.update({"promptPath": "prompt-v2.txt", "outputSize": "1024x1024",
                       "imageSize": "1K", "aspectRatio": "1:1"})
    generation["inputs"].reverse()
    manifest["qcModel"] = "gpt-6-astra"
    manifest["cases"][0]["qc"]["plan"] = {"reviewRevision": 2}
    manifest["cases"][0]["qc"].pop("candidatePath")
    path.write_text(json.dumps(manifest))
    evaluation_manifest_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    assert evaluation_manifest_sha != original_manifest_sha

    expected_source = {
        "receipt": {"path": str(generation_receipt_path), "sha256": generation_receipt_sha},
        "manifestSha256": original_manifest_sha,
        "model": "gpt-image-2",
        "prompt": {"sha256": hashlib.sha256("Exact prompt\n한글".encode()).hexdigest()},
        "inputs": [
            {"mime": "image/png", "sha256": hashlib.sha256(png("red")).hexdigest()},
            {"mime": "image/png", "sha256": hashlib.sha256(png("blue")).hexdigest()},
        ],
        "output": {"sha256": hashlib.sha256(generated).hexdigest()},
        "outputSize": "1536x2048",
        "imageSize": "2K",
        "aspectRatio": "3:4",
    }

    async def judge(settings, plan, references, generated_image, **kwargs):
        started = json.loads((tmp_path / "out/case-a.qc.started.json").read_text())
        assert started["manifestSha256"] == evaluation_manifest_sha
        assert started["sourceGeneration"] == expected_source
        assert settings.model_text == "gpt-6-astra"
        assert plan == {"reviewRevision": 2}
        assert generated_image.data == generated
        return {"provider": "gpt", "status": "PASS", "gates": {}}

    monkeypatch.setattr(replay().cut_output_qc, "verdict", judge)
    receipt = invoke(path, tmp_path, "qc")[0]
    assert receipt["manifestSha256"] == evaluation_manifest_sha
    assert receipt["sourceGeneration"] == expected_source


def test_generated_candidate_requires_matching_success_receipt_and_hash(tmp_path, monkeypatch):
    path, manifest = fixture_manifest(tmp_path)
    manifest["cases"][0]["qc"].pop("candidatePath")
    path.write_text(json.dumps(manifest))
    async def generate(self, **kwargs):
        return GeminiImageResult(png("yellow"), "image/png", 1, None)
    monkeypatch.setattr(replay().GeminiImageClient, "generate_content_image", generate)
    invoke(path, tmp_path, "generate")
    block_paid(monkeypatch)
    (tmp_path / "out/case-a.png").write_bytes(png("green"))
    with pytest.raises(ValueError):
        invoke(path, tmp_path, "qc")


def test_case_filter_and_concurrency_cap(tmp_path, monkeypatch):
    path, manifest = fixture_manifest(tmp_path)
    row = manifest["cases"][0]
    manifest["cases"] = [dict(row, id=f"case-{index}") for index in range(5)]
    path.write_text(json.dumps(manifest))
    active = peak = 0
    async def generate(self, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(active, peak)
        await asyncio.sleep(0.01)
        active -= 1
        return GeminiImageResult(png("yellow"), "image/png", 1, None)
    monkeypatch.setattr(replay().GeminiImageClient, "generate_content_image", generate)
    assert len(invoke(path, tmp_path, "dry-run", "case-3")) == 1
    assert len(invoke(path, tmp_path, "generate")) == 5
    assert peak == 2


def test_absolute_and_parent_evidence_paths_are_allowed(tmp_path, monkeypatch):
    path, manifest = fixture_manifest(tmp_path)
    subdir = tmp_path / "nested"
    subdir.mkdir()
    for item in manifest["cases"][0]["generation"]["inputs"]:
        item["path"] = str(tmp_path / item["path"])
    manifest["cases"][0]["generation"]["promptPath"] = "../prompt.txt"
    for item in manifest["cases"][0]["qc"]["references"]:
        item["path"] = "../" + item["path"]
    manifest["cases"][0]["qc"]["candidatePath"] = "../candidate.png"
    path = subdir / "manifest.json"
    path.write_text(json.dumps(manifest))
    block_paid(monkeypatch)
    assert invoke(path, tmp_path)[0]["status"] == "dry-run"


def test_real_qc_failure_suppresses_provider_body_but_records_status(tmp_path, monkeypatch, caplog):
    from dataclasses import replace
    from app.agents import vision_llm
    path, _ = fixture_manifest(tmp_path)
    async def provider_failure(*args, **kwargs):
        raise vision_llm.VisionError("OpenAI 400: secret-key raw-image-body")
    monkeypatch.setitem(vision_llm._PROVIDERS, "gpt", (provider_failure, lambda s: s.model_text, lambda s: s.openai_api_key))
    setting = replace(load_settings(), openai_api_key="fake-key", gemini_api_key="fake-other-key")
    result = asyncio.run(replay().run_manifest(path, tmp_path / "out", mode="qc", settings=setting))[0]
    assert "secret-key" not in caplog.text and "raw-image-body" not in caplog.text
    assert result["status"] == "error"
    assert result["providerErrors"][0]["httpStatus"] == 400


def test_generated_candidate_success_and_missing_evidence_remain_visible(tmp_path, monkeypatch):
    path, manifest = fixture_manifest(tmp_path)
    manifest["cases"][0]["qc"].pop("candidatePath")
    manifest["cases"][0]["qc"]["references"] = []
    path.write_text(json.dumps(manifest))
    async def generate(self, **kwargs):
        return GeminiImageResult(png("yellow"), "image/png", 1, None)
    monkeypatch.setattr(replay().GeminiImageClient, "generate_content_image", generate)
    invoke(path, tmp_path, "generate")
    async def analyze(settings, prompt, images, schema):
        assert settings.analysis_model_order == "gpt"
        assert images[-1].data == png("yellow")
        return {}, "gpt"
    monkeypatch.setattr(replay().cut_output_qc, "analyze_with_fallback", analyze)
    result = invoke(path, tmp_path, "qc")[0]
    assert result["deterministicPreflight"]["garmentConstruction"]["status"] == "UNJUDGEABLE"
    assert result["verdict"]["gates"]["garmentConstruction"]["status"] == "UNJUDGEABLE"
    assert result["verdict"]["authorityProfile"] == "generic_v1"


def test_invalid_later_case_preflights_before_first_call(tmp_path, monkeypatch):
    path, manifest = fixture_manifest(tmp_path)
    manifest["cases"].append(json.loads(json.dumps(manifest["cases"][0])))
    manifest["cases"][1]["id"] = "case-b"
    manifest["cases"][1]["generation"]["inputs"][0]["path"] = "missing.png"
    path.write_text(json.dumps(manifest))
    block_paid(monkeypatch)
    with pytest.raises(ValueError):
        invoke(path, tmp_path, "generate")
    assert not (tmp_path / "out").exists()


def test_cli_defaults_to_dry_run(tmp_path, monkeypatch, capsys):
    path, _ = fixture_manifest(tmp_path)
    block_paid(monkeypatch)
    assert replay().main(["--manifest", str(path), "--output-dir", str(tmp_path / "out")]) == 0
    assert json.loads(capsys.readouterr().out) == [{"caseId": "case-a", "status": "dry-run"}]


def test_paid_generation_batch_cannot_exceed_ten(tmp_path, monkeypatch):
    path, manifest = fixture_manifest(tmp_path)
    row = manifest["cases"][0]
    manifest["cases"] = [dict(row, id=f"case-{index}") for index in range(11)]
    path.write_text(json.dumps(manifest))
    block_paid(monkeypatch)
    with pytest.raises(ValueError):
        invoke(path, tmp_path, "generate")
