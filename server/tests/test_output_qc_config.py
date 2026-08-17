from pathlib import Path

import yaml

from app.config import Settings, load_settings


def test_output_qc_defaults_off_and_accepts_repair(monkeypatch):
    assert Settings.__dataclass_fields__["cut_output_qc_mode"].default == "off"
    assert Settings.__dataclass_fields__["page_output_qc_mode"].default == "off"

    monkeypatch.setenv("CUT_OUTPUT_QC_MODE", "shadow")
    monkeypatch.setenv("PAGE_OUTPUT_QC_MODE", "shadow")
    settings = load_settings()
    assert settings.cut_output_qc_mode == "shadow"
    assert settings.page_output_qc_mode == "shadow"

    monkeypatch.setenv("CUT_OUTPUT_QC_MODE", "repair")
    settings = load_settings()
    assert settings.cut_output_qc_mode == "repair"

    monkeypatch.setenv("CUT_OUTPUT_QC_MODE", "enforce")
    monkeypatch.setenv("PAGE_OUTPUT_QC_MODE", "invalid")
    settings = load_settings()
    assert settings.cut_output_qc_mode == "off"
    assert settings.page_output_qc_mode == "off"


def test_production_manifest_enables_parallel_gemini_repair_pipeline():
    manifest_path = Path(__file__).resolve().parents[2] / "copilot/api/manifest.yml"
    variables = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))["variables"]

    assert variables["MODEL_ROUTING_IMAGE_HIGH"] == "gemini-3-pro-image"
    assert variables["DETAIL_CUT_CONCURRENCY"] == "0"
    assert variables["DETAIL_CUT_STAGGER_MS"] == "3000"
    assert variables["GARMENT_QC_MODE"] == "off"
    assert variables["CUT_OUTPUT_QC_MODE"] == "repair"
