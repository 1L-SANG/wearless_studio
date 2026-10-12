from app.config import load_settings


def test_editor_prices_load_defaults_and_independent_environment_overrides(monkeypatch):
    for key in ("CREDIT_COST_EDITOR_IMAGE", "CREDIT_COST_EDITOR_EDIT", "CREDIT_COST_EDITOR_SIMILAR"):
        monkeypatch.delenv(key, raising=False)
    settings = load_settings()
    assert (settings.credit_cost_editor_image, settings.credit_cost_editor_edit,
            settings.credit_cost_editor_similar) == (19, 5, 8)
    monkeypatch.setenv("CREDIT_COST_EDITOR_IMAGE", "23")
    monkeypatch.setenv("CREDIT_COST_EDITOR_EDIT", "7")
    monkeypatch.setenv("CREDIT_COST_EDITOR_SIMILAR", "11")
    settings = load_settings()
    assert (settings.credit_cost_editor_image, settings.credit_cost_editor_edit,
            settings.credit_cost_editor_similar) == (23, 7, 11)
