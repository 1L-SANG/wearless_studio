from app.config import Settings, load_settings


def test_output_qc_defaults_off_and_only_accepts_shadow(monkeypatch):
    assert Settings.__dataclass_fields__["cut_output_qc_mode"].default == "off"
    assert Settings.__dataclass_fields__["page_output_qc_mode"].default == "off"

    monkeypatch.setenv("CUT_OUTPUT_QC_MODE", "shadow")
    monkeypatch.setenv("PAGE_OUTPUT_QC_MODE", "shadow")
    settings = load_settings()
    assert settings.cut_output_qc_mode == "shadow"
    assert settings.page_output_qc_mode == "shadow"

    monkeypatch.setenv("CUT_OUTPUT_QC_MODE", "enforce")
    monkeypatch.setenv("PAGE_OUTPUT_QC_MODE", "invalid")
    settings = load_settings()
    assert settings.cut_output_qc_mode == "off"
    assert settings.page_output_qc_mode == "off"
