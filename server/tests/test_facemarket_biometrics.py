from unittest.mock import Mock, call

import pytest
from fastapi import FastAPI

from app.facemarket_enrollment import router as biometric_enrollment_router
from app.main import create_app
from conftest import make_settings


def biometric_settings(**overrides):
    values = dict(
        app_env="dev",
        facemarket_enabled=True,
        fm_biometric_enrollment_enabled=True,
        fm_oacx_contract_mode="dev-mock-v1",
        fm_liveness_region="us-east-1",
        fm_liveness_browser_role_arn="arn:aws:iam::123456789012:role/fm-liveness-browser",
        fm_liveness_confidence_threshold=90.0,
        fm_id_live_threshold=0.45,
        fm_retouched_live_threshold=0.40,
        fm_match_policy_version="dev-gold-v1",
        fm_face_qc_enabled=True,
        opendid_holder_url="http://holder.test",
    )
    values.update(overrides)
    return make_settings(**values)


def test_biometric_settings_defaults_off():
    settings = make_settings()

    assert settings.fm_biometric_enrollment_enabled is False
    assert settings.fm_liveness_region == "us-east-1"


def test_production_rejects_dev_mock_contract():
    with pytest.raises(RuntimeError, match="verified OACX biometric contract"):
        create_app(biometric_settings(app_env="production"))


def test_enabled_feature_requires_three_calibrated_settings():
    with pytest.raises(RuntimeError, match="biometric thresholds"):
        create_app(biometric_settings(fm_id_live_threshold=None))


def test_disabled_feature_has_no_biometric_aws_clients():
    app = create_app(make_settings())

    assert app.state.fm_rekognition is None
    assert app.state.fm_sts is None


def test_enabled_dev_feature_builds_isolated_clients_and_includes_router(monkeypatch):
    rekognition = object()
    sts = object()
    boto_client = Mock(side_effect=[rekognition, sts])
    included_routers = []
    include_router = FastAPI.include_router

    def track_router(app, router, **kwargs):
        included_routers.append(router)
        return include_router(app, router, **kwargs)

    monkeypatch.setattr("app.facemarket_enrollment.boto3.client", boto_client)
    monkeypatch.setattr(FastAPI, "include_router", track_router)

    app = create_app(biometric_settings())

    assert boto_client.call_args_list == [
        call("rekognition", region_name="us-east-1"),
        call("sts", region_name="us-east-1"),
    ]
    assert app.state.fm_rekognition is rekognition
    assert app.state.fm_sts is sts
    assert biometric_enrollment_router in included_routers
