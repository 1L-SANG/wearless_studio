from pathlib import Path

import pytest

from app import main
from app.config import load_settings
from conftest import make_settings


ENV_EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"


def test_production_facemarket_requires_mandatory_vc_before_pool_or_workers(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "create_pool", lambda _url: calls.append("pool"))
    monkeypatch.setattr(main, "JobDispatcher", lambda _app: calls.append("dispatcher"))

    with pytest.raises(RuntimeError, match="FACEMARKET_VC_REQUIRED"):
        main.create_app(
            make_settings(
                app_env="production",
                database_url="postgresql://unused",
                facemarket_enabled=True,
                fm_vc_required=False,
            )
        )

    assert calls == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("opendid_holder_url", None),
        ("opendid_holder_url", "   "),
        ("opendid_holder_hmac_secret", None),
        ("opendid_holder_hmac_secret", "\t"),
    ],
)
def test_required_vc_rejects_absent_or_blank_holder_config(field, value):
    values = {
        "fm_vc_required": True,
        "opendid_holder_url": "http://holder:8100",
        "opendid_holder_hmac_secret": "shared-secret",
    }
    values[field] = value

    with pytest.raises(RuntimeError, match="OpenDID Holder"):
        main.create_app(make_settings(**values))


def test_production_without_facemarket_can_keep_mandatory_vc_disabled():
    main.create_app(
        make_settings(
            app_env="production", facemarket_enabled=False, fm_vc_required=False
        )
    )


def test_dev_facemarket_can_keep_mandatory_vc_disabled():
    main.create_app(
        make_settings(app_env="dev", facemarket_enabled=True, fm_vc_required=False)
    )


def test_environment_loads_exact_mandatory_vc_names(monkeypatch):
    monkeypatch.setenv("FACEMARKET_VC_REQUIRED", "true")
    monkeypatch.setenv("OPENDID_HOLDER_URL", "http://holder:8100/")
    monkeypatch.setenv("OPENDID_HOLDER_HMAC_SECRET", "shared-secret")

    settings = load_settings()

    assert settings.fm_vc_required is True
    assert settings.opendid_holder_url == "http://holder:8100"
    assert settings.opendid_holder_hmac_secret == "shared-secret"


def test_environment_defaults_mandatory_vc_off_without_holder_secrets(monkeypatch):
    for name in (
        "FACEMARKET_VC_REQUIRED",
        "OPENDID_HOLDER_URL",
        "OPENDID_HOLDER_HMAC_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = load_settings()

    assert settings.fm_vc_required is False
    assert settings.opendid_holder_url is None
    assert settings.opendid_holder_hmac_secret is None


def test_env_example_has_no_holder_url_or_secret_default():
    values = {}
    for raw_line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        values[name] = value.split("#", 1)[0].strip()

    assert values["FACEMARKET_VC_REQUIRED"] == "false"
    assert values["OPENDID_HOLDER_URL"] == ""
    assert values["OPENDID_HOLDER_HMAC_SECRET"] == ""
