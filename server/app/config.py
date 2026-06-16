"""환경 변수 → Settings. backend_integration_plan §9 (인증·CORS) 기준.

dev 익명 플래그(AUTH_DEV_USER_ID)는 APP_ENV=dev에서만 동작 — Phase 1까지만 유지 (§9).
"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    app_env: str
    supabase_url: str
    jwks_url: str
    jwt_audience: str
    cors_origins: list[str]
    dev_user_id: str | None
    database_url: str | None


def load_settings() -> Settings:
    app_env = os.getenv("APP_ENV", "dev")
    supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    jwks_url = os.getenv("SUPABASE_JWKS_URL", "")
    if not jwks_url and supabase_url:
        jwks_url = f"{supabase_url}/auth/v1/.well-known/jwks.json"

    cors_origins = [
        o.strip()
        for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
        if o.strip()
    ]

    dev_user_id = os.getenv("AUTH_DEV_USER_ID") or None
    if app_env != "dev":
        dev_user_id = None

    return Settings(
        app_env=app_env,
        supabase_url=supabase_url,
        jwks_url=jwks_url,
        jwt_audience=os.getenv("SUPABASE_JWT_AUDIENCE", "authenticated"),
        cors_origins=cors_origins,
        dev_user_id=dev_user_id,
        database_url=os.getenv("DATABASE_URL") or None,
    )
