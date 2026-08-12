"""SAM2 service configuration. Environment only — nothing here is ever committed.

Deliberately separate from `app/config.py`: this service is its own deployment unit and must
not import the main backend to read a setting.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class SamSettings:
    #: Shared secret the main backend presents as `Authorization: Bearer <token>`.
    #: No default. A service that starts without it refuses every segment request rather than
    #: serving an open endpoint.
    internal_token: str | None
    r2_account_id: str | None
    r2_access_key_id: str | None
    r2_secret_access_key: str | None
    r2_bucket: str | None
    r2_endpoint: str | None
    #: Largest source object this service will pull out of R2 and decode.
    max_source_bytes: int = 40 * 1024 * 1024
    model_id: str = ""

    @property
    def auth_configured(self) -> bool:
        return bool(self.internal_token and self.internal_token.strip())


def load_settings() -> SamSettings:
    from sam_service.segmentation import MODEL_ID
    return SamSettings(
        internal_token=os.getenv("SAM_INTERNAL_TOKEN") or None,
        r2_account_id=os.getenv("R2_ACCOUNT_ID") or None,
        r2_access_key_id=os.getenv("R2_ACCESS_KEY_ID") or None,
        r2_secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY") or None,
        r2_bucket=os.getenv("R2_BUCKET") or None,
        r2_endpoint=(os.getenv("R2_ENDPOINT") or "").rstrip("/") or None,
        max_source_bytes=int(os.getenv("SAM_MAX_SOURCE_BYTES") or 40 * 1024 * 1024),
        model_id=os.getenv("SAM_MODEL_ID") or MODEL_ID,
    )
