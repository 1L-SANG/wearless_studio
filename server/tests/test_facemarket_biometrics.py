import base64
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from fastapi import FastAPI

from app import cx_identity, facemarket_enrollment, main
from app.config import load_settings
from app.cx_identity import (
    DEV_MOCK_OACX_BIOMETRIC_CONTRACT,
    OacxBiometricError,
    get_oacx_biometric_contract,
    parse_oacx_biometric_evidence,
    wipe_bytearray,
)
from app.facemarket_enrollment import router as biometric_enrollment_router
from app.main import create_app
from conftest import make_settings


DEV_TRANS = {
    "ci": "dev-ci-value",
    "birth": "19900102",
    "nm": "홍길동",
    "txId": "tx-dev-1",
    "idPortraitBase64": base64.b64encode(b"portrait-bytes").decode(),
    "idPortraitMime": "image/jpeg",
    "issuedAt": "2026-08-21T03:00:00Z",
}
NOW = datetime(2026, 8, 21, 3, 2, tzinfo=timezone.utc)


class RecordingRekognition:
    def __init__(
        self,
        session_id="00000000-0000-0000-0000-000000000001",
        result=None,
    ):
        self.session_id = session_id
        self.result = result or {}
        self.calls = []
        self.result_calls = []

    def create_face_liveness_session(self, **kwargs):
        self.calls.append(kwargs)
        return {"SessionId": self.session_id}

    def get_face_liveness_session_results(self, **kwargs):
        self.result_calls.append(kwargs)
        return self.result


class RecordingSts:
    def __init__(self):
        self.calls = []

    def assume_role(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "Credentials": {
                "AccessKeyId": "temporary-access-key",
                "SecretAccessKey": "temporary-secret-key",
                "SessionToken": "temporary-session-token",
                "Expiration": NOW + timedelta(minutes=15),
            },
            "AssumedRoleUser": {
                "AssumedRoleId": "AROATEST:fm-live-123456789abc",
                "Arn": "arn:aws:sts::123456789012:assumed-role/test/fm-live-123456789abc",
            },
        }


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


@pytest.mark.parametrize("pepper", [None, "", "   "])
def test_enabled_feature_requires_ci_pepper(pepper):
    with pytest.raises(RuntimeError, match="FM_CI_PEPPER"):
        create_app(biometric_settings(fm_ci_pepper=pepper))


def test_enabled_feature_requires_face_qc_weights_on_disk(tmp_path):
    with pytest.raises(RuntimeError, match="SFace|YuNet|face QC weight"):
        create_app(
            biometric_settings(fm_ci_pepper="pep", fm_face_qc_dir=str(tmp_path))
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("fm_liveness_confidence_threshold", ""),
        ("fm_liveness_confidence_threshold", float("nan")),
        ("fm_liveness_confidence_threshold", float("inf")),
        ("fm_liveness_confidence_threshold", float("-inf")),
        ("fm_liveness_confidence_threshold", True),
        ("fm_liveness_confidence_threshold", 0.0),
        ("fm_liveness_confidence_threshold", -0.01),
        ("fm_liveness_confidence_threshold", 100.01),
        ("fm_id_live_threshold", ""),
        ("fm_id_live_threshold", float("nan")),
        ("fm_id_live_threshold", float("inf")),
        ("fm_id_live_threshold", float("-inf")),
        ("fm_id_live_threshold", True),
        ("fm_id_live_threshold", 0.0),
        ("fm_id_live_threshold", -0.01),
        ("fm_id_live_threshold", 1.01),
        ("fm_retouched_live_threshold", ""),
        ("fm_retouched_live_threshold", float("nan")),
        ("fm_retouched_live_threshold", float("inf")),
        ("fm_retouched_live_threshold", float("-inf")),
        ("fm_retouched_live_threshold", True),
        ("fm_retouched_live_threshold", 0.0),
        ("fm_retouched_live_threshold", -0.01),
        ("fm_retouched_live_threshold", 1.01),
    ],
)
def test_enabled_feature_rejects_unsafe_threshold_before_clients_or_pool(
    monkeypatch, field, value
):
    monkeypatch.setattr(
        main,
        "create_pool",
        lambda *_args: pytest.fail("invalid threshold reached DB pool"),
    )
    monkeypatch.setattr(
        facemarket_enrollment.boto3,
        "client",
        lambda *_args, **_kwargs: pytest.fail("invalid threshold reached AWS client"),
    )

    with pytest.raises(RuntimeError, match="biometric thresholds"):
        create_app(
            biometric_settings(database_url="postgresql://unused", **{field: value})
        )


@pytest.mark.parametrize(
    "thresholds",
    [
        {
            "fm_liveness_confidence_threshold": 5e-324,
            "fm_id_live_threshold": 5e-324,
            "fm_retouched_live_threshold": 5e-324,
        },
        {
            "fm_liveness_confidence_threshold": 100.0,
            "fm_id_live_threshold": 1.0,
            "fm_retouched_live_threshold": 1.0,
        },
    ],
)
def test_enabled_feature_accepts_positive_threshold_boundaries(monkeypatch, thresholds):
    monkeypatch.setattr(
        facemarket_enrollment,
        "build_biometric_aws_clients",
        lambda _settings: (object(), object()),
    )

    app = create_app(biometric_settings(**thresholds))

    assert app.state.fm_rekognition is not None
    assert app.state.fm_sts is not None


@pytest.mark.parametrize(
    "env,field",
    [
        ("FM_LIVENESS_CONFIDENCE_THRESHOLD", "fm_liveness_confidence_threshold"),
        ("FM_ID_LIVE_THRESHOLD", "fm_id_live_threshold"),
        ("FM_RETOUCHED_LIVE_THRESHOLD", "fm_retouched_live_threshold"),
    ],
)
def test_disabled_feature_treats_whitespace_threshold_as_unset(
    monkeypatch, env, field
):
    monkeypatch.setenv(env, "   ")

    settings = load_settings()

    assert settings.fm_biometric_enrollment_enabled is False
    assert getattr(settings, field) is None


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

    assert [item.args for item in boto_client.call_args_list] == [
        ("rekognition",),
        ("sts",),
    ]
    for item in boto_client.call_args_list:
        assert item.kwargs["region_name"] == "us-east-1"
        assert item.kwargs["config"].retries == {
            "mode": "standard",
            "max_attempts": 3,
        }
        assert item.kwargs["config"].connect_timeout == 3
        assert item.kwargs["config"].read_timeout == 10
    assert app.state.fm_rekognition is rekognition
    assert app.state.fm_sts is sts
    assert biometric_enrollment_router in included_routers


def test_create_session_disables_audit_and_s3_output():
    rekognition = RecordingRekognition()

    session_id = facemarket_enrollment.create_liveness_session(
        rekognition, client_request_token="a" * 64
    )

    assert session_id == "00000000-0000-0000-0000-000000000001"
    assert rekognition.calls == [
        {
            "ClientRequestToken": "a" * 64,
            "Settings": {"AuditImagesLimit": 0},
        }
    ]


def test_create_session_rejects_malformed_provider_session_id():
    rekognition = RecordingRekognition(session_id="not-a-session-id")

    with pytest.raises(
        facemarket_enrollment.BiometricProviderError,
        match="^liveness_unavailable$",
    ):
        facemarket_enrollment.create_liveness_session(
            rekognition, client_request_token="a" * 64
        )


def test_get_result_requires_success_reference_and_threshold():
    rekognition = RecordingRekognition(result={
        "Status": "SUCCEEDED",
        "Confidence": 94.5,
        "ReferenceImage": {"Bytes": b"live-reference"},
    })

    result = facemarket_enrollment.get_liveness_result(
        rekognition,
        session_id="00000000-0000-0000-0000-000000000001",
        minimum_confidence=90.0,
    )

    assert result.reference_image == bytearray(b"live-reference")
    assert result.confidence == 94.5
    assert rekognition.result_calls == [{
        "SessionId": "00000000-0000-0000-0000-000000000001"
    }]


@pytest.mark.parametrize(
    "result,reason",
    [
        ({"Status": "IN_PROGRESS"}, "liveness_retry"),
        ({"Status": "FAILED"}, "liveness_retry"),
        (
            {
                "Status": "SUCCEEDED",
                "Confidence": 89.99,
                "ReferenceImage": {"Bytes": b"live"},
            },
            "liveness_failed",
        ),
        ({"Status": "SUCCEEDED", "Confidence": 99.0}, "liveness_retry"),
    ],
)
def test_get_result_fails_closed(result, reason):
    with pytest.raises(facemarket_enrollment.BiometricProviderError) as error:
        facemarket_enrollment.get_liveness_result(
            RecordingRekognition(result=result),
            session_id="00000000-0000-0000-0000-000000000001",
            minimum_confidence=90.0,
        )

    assert error.value.reason == reason


def test_sts_credentials_are_fifteen_minutes_start_only_and_region_locked():
    sts = RecordingSts()

    result = facemarket_enrollment.assume_liveness_browser_credentials(
        sts,
        role_arn="arn:aws:iam::123456789012:role/fm-liveness-browser",
        session_name="fm-live-123456789abc",
    )

    call = sts.calls[0]
    assert call["DurationSeconds"] == 900
    assert call["RoleArn"] == "arn:aws:iam::123456789012:role/fm-liveness-browser"
    assert call["RoleSessionName"] == "fm-live-123456789abc"
    assert json.loads(call["Policy"])["Statement"] == [
        {
            "Effect": "Allow",
            "Action": "rekognition:StartFaceLivenessSession",
            "Resource": "*",
            "Condition": {
                "StringEquals": {"aws:RequestedRegion": "us-east-1"}
            },
        }
    ]
    assert result == {
        "accessKeyId": "temporary-access-key",
        "secretAccessKey": "temporary-secret-key",
        "sessionToken": "temporary-session-token",
        "expiration": NOW + timedelta(minutes=15),
    }


def test_oacx_dev_contract_extracts_mutable_sensitive_buffers():
    evidence = parse_oacx_biometric_evidence(
        DEV_TRANS, contract=DEV_MOCK_OACX_BIOMETRIC_CONTRACT, now=NOW
    )

    assert evidence.ci == bytearray(b"dev-ci-value")
    assert evidence.portrait == bytearray(b"portrait-bytes")
    assert evidence.name_masked == "홍*동"
    assert evidence.contract_version == "dev-mock-v1"


@pytest.mark.parametrize(
    "patch,reason",
    [
        ({"idPortraitBase64": None}, "id_portrait_unavailable"),
        ({"idPortraitBase64": "not-base64"}, "id_portrait_unavailable"),
        ({"idPortraitMime": "application/pdf"}, "id_portrait_unavailable"),
        ({"issuedAt": "2026-08-21T02:54:59Z"}, "id_portrait_unavailable"),
        ({"birth": "20100102"}, "minor_blocked"),
    ],
)
def test_oacx_unusable_portrait_fails_with_sanitized_reason(patch, reason):
    trans = {**DEV_TRANS, **patch}

    with pytest.raises(OacxBiometricError) as error:
        parse_oacx_biometric_evidence(
            trans, contract=DEV_MOCK_OACX_BIOMETRIC_CONTRACT, now=NOW
        )

    assert error.value.reason == reason
    assert "dev-ci-value" not in str(error.value)
    assert "idPortraitBase64" not in str(error.value)


def test_oacx_validates_birth_after_contract_fields_and_ttl():
    trans = {
        **DEV_TRANS,
        "birth": "20100102",
        "issuedAt": "2026-08-21T02:54:59Z",
    }

    with pytest.raises(OacxBiometricError) as error:
        parse_oacx_biometric_evidence(
            trans, contract=DEV_MOCK_OACX_BIOMETRIC_CONTRACT, now=NOW
        )

    assert error.value.reason == "id_portrait_unavailable"


@pytest.mark.parametrize(
    "identity,issued_at,reason",
    [
        ({"birth": "20100102"}, DEV_TRANS["issuedAt"], "minor_blocked"),
        ({"birth": "20100102"}, "2026-08-21T02:54:59Z", "id_portrait_unavailable"),
        ({}, DEV_TRANS["issuedAt"], "id_portrait_unavailable"),
    ],
)
def test_oacx_birth_path_is_contract_owned_after_valid_ttl(
    identity, issued_at, reason
):
    contract = replace(
        DEV_MOCK_OACX_BIOMETRIC_CONTRACT,
        birth_path=("identity", "birth"),
    )
    trans = {
        key: value
        for key, value in DEV_TRANS.items()
        if key not in {"birth", "issuedAt"}
    }
    trans.update(identity=identity, issuedAt=issued_at)

    with pytest.raises(OacxBiometricError) as error:
        parse_oacx_biometric_evidence(trans, contract=contract, now=NOW)

    assert error.value.reason == reason


def test_oacx_oversized_portrait_fails_with_sanitized_reason():
    trans = {
        **DEV_TRANS,
        "idPortraitBase64": base64.b64encode(b"x" * (5 * 1024 * 1024 + 1)).decode(),
    }

    with pytest.raises(OacxBiometricError) as error:
        parse_oacx_biometric_evidence(
            trans, contract=DEV_MOCK_OACX_BIOMETRIC_CONTRACT, now=NOW
        )

    assert error.value.reason == "id_portrait_unavailable"


def test_oacx_oversized_encoding_is_rejected_before_decode(monkeypatch):
    contract = replace(DEV_MOCK_OACX_BIOMETRIC_CONTRACT, max_portrait_bytes=3)
    trans = {**DEV_TRANS, "idPortraitBase64": base64.b64encode(b"four").decode()}
    monkeypatch.setattr(
        cx_identity.base64,
        "b64decode",
        lambda *args, **kwargs: pytest.fail("oversized base64 reached decoder"),
    )

    with pytest.raises(OacxBiometricError) as error:
        parse_oacx_biometric_evidence(trans, contract=contract, now=NOW)

    assert error.value.reason == "id_portrait_unavailable"


def test_oacx_post_decode_failure_wipes_portrait(monkeypatch):
    wiped = []

    def track_wipe(value):
        wipe_bytearray(value)
        if value is not None:
            wiped.append(value)

    monkeypatch.setattr(cx_identity, "wipe_bytearray", track_wipe)
    trans = {**DEV_TRANS, "issuedAt": "2026-08-21T02:54:59Z"}

    with pytest.raises(OacxBiometricError):
        parse_oacx_biometric_evidence(
            trans, contract=DEV_MOCK_OACX_BIOMETRIC_CONTRACT, now=NOW
        )

    assert len(wiped) == 1
    assert wiped[0] == bytearray(len(b"portrait-bytes"))


def test_oacx_production_cannot_select_dev_contract():
    with pytest.raises(OacxBiometricError) as error:
        get_oacx_biometric_contract(
            make_settings(app_env="prod", fm_oacx_contract_mode="dev-mock-v1")
        )

    assert error.value.reason == "oacx_contract_unavailable"


def test_oacx_sensitive_bytearrays_can_be_wiped():
    value = bytearray(b"sensitive")

    wipe_bytearray(value)
    wipe_bytearray(None)

    assert value == bytearray(len(value))
