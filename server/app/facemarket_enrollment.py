"""Fail-closed FaceMarket biometric enrollment gate and AWS clients."""

import boto3
from fastapi import APIRouter

from .config import Settings

router = APIRouter(prefix="/v1/facemarket", tags=["FaceMarket biometric enrollment"])


def validate_biometric_settings(settings: Settings) -> None:
    if not settings.fm_biometric_enrollment_enabled:
        return
    if not settings.facemarket_enabled:
        raise RuntimeError("FACEMARKET_ENABLED is required for biometric enrollment")
    if settings.fm_liveness_region != "us-east-1":
        raise RuntimeError("Face Liveness region must be us-east-1")
    if not settings.fm_liveness_browser_role_arn:
        raise RuntimeError("FM_LIVENESS_BROWSER_ROLE_ARN is required")
    if not settings.fm_face_qc_enabled:
        raise RuntimeError("FM_FACE_QC_ENABLED is required")
    thresholds = (
        settings.fm_liveness_confidence_threshold,
        settings.fm_id_live_threshold,
        settings.fm_retouched_live_threshold,
        settings.fm_match_policy_version,
    )
    if any(value is None for value in thresholds):
        raise RuntimeError("calibrated biometric thresholds and policy version are required")
    if settings.fm_oacx_contract_mode == "dev-mock-v1" and settings.app_env != "dev":
        raise RuntimeError("verified OACX biometric contract is required outside dev")
    if settings.fm_oacx_contract_mode != "dev-mock-v1":
        raise RuntimeError("verified OACX biometric contract is required")


def build_biometric_aws_clients(settings: Settings):
    rekognition = boto3.client("rekognition", region_name="us-east-1")
    sts = boto3.client("sts", region_name="us-east-1")
    return rekognition, sts
