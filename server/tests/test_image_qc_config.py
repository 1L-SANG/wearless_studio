import pytest

from app.config import load_settings
from conftest import make_settings


def test_image_qc_claude_defaults(monkeypatch):
    for name in ("MODEL_IMAGE_QC", "IMAGE_QC_CLAUDE_EFFORT", "IMAGE_QC_TIMEOUT_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    for settings in (make_settings(), load_settings()):
        assert settings.model_image_qc == ""
        assert settings.image_qc_claude_effort == "low"
        assert settings.image_qc_timeout_seconds == 90


@pytest.mark.parametrize("model", ["", " \t\n", " claude-opus-5-5 \t"])
def test_image_qc_environment_trims_model(monkeypatch, model):
    monkeypatch.setenv("MODEL_IMAGE_QC", model)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-claude-key")
    monkeypatch.setenv("IMAGE_QC_CLAUDE_EFFORT", "medium")
    monkeypatch.setenv("IMAGE_QC_TIMEOUT_SECONDS", "12.5")
    settings = load_settings()
    assert settings.model_image_qc == model.strip()
    assert settings.image_qc_claude_effort == "medium"
    assert settings.image_qc_timeout_seconds == 12.5


@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max"])
def test_image_qc_claude_accepts_effort(effort):
    assert make_settings(image_qc_claude_effort=effort).image_qc_claude_effort == effort


@pytest.mark.parametrize("effort", ["", "typo", "HIGH"])
def test_image_qc_claude_rejects_invalid_effort(monkeypatch, effort):
    with pytest.raises(ValueError, match="IMAGE_QC_CLAUDE_EFFORT"):
        make_settings(image_qc_claude_effort=effort)
    monkeypatch.setenv("IMAGE_QC_CLAUDE_EFFORT", effort)
    with pytest.raises(ValueError, match="IMAGE_QC_CLAUDE_EFFORT"):
        load_settings()


@pytest.mark.parametrize("model", ["gpt-5.4", "gemini-3-pro-image", "Claude-opus-5-5"])
def test_image_qc_rejects_nonclaude_model(monkeypatch, model):
    with pytest.raises(ValueError, match="MODEL_IMAGE_QC"):
        make_settings(model_image_qc=model)
    monkeypatch.setenv("MODEL_IMAGE_QC", model)
    with pytest.raises(ValueError, match="MODEL_IMAGE_QC"):
        load_settings()


@pytest.mark.parametrize("key", [None, "", " \t\n"])
def test_image_qc_claude_requires_key(monkeypatch, key):
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        make_settings(model_image_qc="claude-opus-5-5", anthropic_api_key=key)
    monkeypatch.setenv("MODEL_IMAGE_QC", "claude-opus-5-5")
    if key is None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    else:
        monkeypatch.setenv("ANTHROPIC_API_KEY", key)
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        load_settings()


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), -float("inf")])
def test_image_qc_rejects_nonpositive_or_nonfinite_timeout(monkeypatch, timeout):
    with pytest.raises(ValueError, match="IMAGE_QC_TIMEOUT_SECONDS"):
        make_settings(image_qc_timeout_seconds=timeout)
    monkeypatch.setenv("IMAGE_QC_TIMEOUT_SECONDS", str(timeout))
    with pytest.raises(ValueError, match="IMAGE_QC_TIMEOUT_SECONDS"):
        load_settings()
