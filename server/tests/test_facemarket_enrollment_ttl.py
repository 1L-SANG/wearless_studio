"""2차 등록 진행 기한(ENROLLMENT_TTL) — 2026-09-27 오너 결정으로 24시간에서 72시간."""

from datetime import datetime, timedelta, timezone

from app import facemarket_enrollment
from test_facemarket_biometric_enrollment import (  # noqa: F401 (픽스처)
    auth, create_enrollment, enrollment_store, fake_pool, fake_r2, fake_rekognition, fake_sts,
    liveness_off_client,
)


def test_enrollment_ttl_is_72_hours():
    assert facemarket_enrollment.ENROLLMENT_TTL == timedelta(hours=72)


def test_new_enrollment_expires_72_hours_after_start(liveness_off_client, auth, enrollment_store):
    before = datetime.now(timezone.utc)
    create_enrollment(liveness_off_client, auth, verify_identity=False)
    after = datetime.now(timezone.utc)
    expires = enrollment_store.enrollments[0]["expires_at"]
    assert before + timedelta(hours=72) <= expires <= after + timedelta(hours=72)
