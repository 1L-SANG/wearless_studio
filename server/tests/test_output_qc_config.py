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
    monkeypatch.setenv("MANNEQUIN_IMAGE_SIZE", "1K")
    monkeypatch.setenv("DETAIL_CUT_IMAGE_SIZE", "4K")
    monkeypatch.setenv("MODEL_ROUTING_DETAIL_CUT", "gpt-image-2-2026-04-21")
    settings = load_settings()
    assert settings.cut_output_qc_mode == "repair"
    assert settings.mannequin_image_size == "1K"
    assert settings.detail_cut_image_size == "4K"
    assert settings.model_detail_cut == "gpt-image-2-2026-04-21"

    monkeypatch.setenv("CUT_OUTPUT_QC_MODE", "enforce")
    monkeypatch.setenv("PAGE_OUTPUT_QC_MODE", "invalid")
    settings = load_settings()
    assert settings.cut_output_qc_mode == "off"
    assert settings.page_output_qc_mode == "off"


def test_production_manifest_bounds_4k_gpt_repair_concurrency_without_moving_shared_tier():
    manifest_path = Path(__file__).resolve().parents[2] / "copilot/api/manifest.yml"
    variables = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))["variables"]

    assert variables["MODEL_ROUTING_IMAGE_HIGH"] == "gemini-3-pro-image"
    assert variables["MODEL_ROUTING_DETAIL_CUT"] == "gpt-image-2-2026-04-21"
    assert variables["DETAIL_CUT_IMAGE_SIZE"] == "4K"
    assert variables["DETAIL_CUT_CONCURRENCY"] == "2"
    assert variables["DETAIL_CUT_STAGGER_MS"] == "3000"
    assert variables["DETAIL_CUT_MAX_ATTEMPTS"] == "1"
    assert variables["GARMENT_QC_MODE"] == "off"
    assert variables["CUT_OUTPUT_QC_MODE"] == "repair"

    secrets = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))["secrets"]
    assert secrets["OPENAI_API_KEY"] == "/copilot/wearless/prod/secrets/OPENAI_API_KEY"


def test_detail_cut_image_size_inherits_mannequin_size_when_unset(monkeypatch):
    monkeypatch.setenv("MANNEQUIN_IMAGE_SIZE", "2K")
    monkeypatch.delenv("DETAIL_CUT_IMAGE_SIZE", raising=False)

    settings = load_settings()

    assert settings.mannequin_image_size == "2K"
    assert settings.detail_cut_image_size == "2K"
