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
    # R2 (Cloudflare, S3 호환) — 자산 저장 (§3). secret 등급, 서버 전용.
    r2_account_id: str | None
    r2_access_key_id: str | None
    r2_secret_access_key: str | None
    r2_bucket: str | None
    r2_endpoint: str | None
    r2_public_base: str | None  # images.wearless.kr 등 공개 서빙 도메인 (없으면 signed GET)
    # ---- AI 에이전트 (Phase 4) ----
    # 마지막 블록 + 기본값 — 직접 생성(테스트)·미래 필드 추가에도 안 깨지게.
    # load_settings()는 아래 기본값을 env 값으로 항상 덮어쓴다.
    gemini_api_key: str | None = None  # AI Studio AIza… (서버 전용, secret)
    vertex_project: str | None = None  # 있으면 Vertex 엔드포인트, 없으면 AI Studio
    vertex_location: str = "global"
    # tier→모델 매핑 (ai_agent_modules §1 — 교체는 여기/env 한 곳)
    model_image_light: str = "gemini-3.1-flash-image"
    model_image_high: str = "gemini-3-pro-image"
    mannequin_tier: str = "image_high"  # AG-04 = Gemini 3 Pro (사용자 결정 — Flash 미사용)
    mannequin_image_size: str = "1K"  # 1K | 2K | 4K (2K 서버경로 저하 시 1K)
    mannequin_max_attempts: int = 2  # QC 게이팅 시 재시도 상한 (shadow면 실질 1회)
    mannequin_qc_enabled: bool = False  # False=shadow(판정 로그만) — 캘리브레이션 후 True
    mannequin_prompt_file: str | None = None  # 없으면 server/prompts/mannequin_generate_v1.txt
    mannequin_prompt_version: str = "v1"
    base_mannequin_women_asset_id: str | None = None  # R2 seed asset (startup 검증)
    base_mannequin_men_asset_id: str | None = None
    job_dispatcher_enabled: bool = True  # §5
    job_poll_interval_seconds: float = 3.0
    job_lease_timeout_seconds: int = 900
    job_worker_id: str = "web"
    credit_cost_version: str = "v1"  # §6 임시 단가
    credit_cost_mannequin_generate: int = 2


def _image_size() -> str:
    v = os.getenv("MANNEQUIN_IMAGE_SIZE", "1K").upper()
    return v if v in {"1K", "2K", "4K"} else "1K"


def _mannequin_tier() -> str:
    t = os.getenv("MANNEQUIN_TIER", "image_high")
    return t if t in {"image_light", "image_high"} else "image_high"


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
        r2_account_id=os.getenv("R2_ACCOUNT_ID") or None,
        r2_access_key_id=os.getenv("R2_ACCESS_KEY_ID") or None,
        r2_secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY") or None,
        r2_bucket=os.getenv("R2_BUCKET") or None,
        r2_endpoint=(os.getenv("R2_ENDPOINT") or "").rstrip("/") or None,
        r2_public_base=(os.getenv("R2_PUBLIC_BASE") or "").rstrip("/") or None,
        gemini_api_key=os.getenv("GEMINI_API_KEY") or None,
        vertex_project=os.getenv("VERTEX_PROJECT") or None,
        vertex_location=os.getenv("VERTEX_LOCATION", "global"),
        model_image_light=os.getenv("MODEL_ROUTING_IMAGE_LIGHT", "gemini-3.1-flash-image"),
        model_image_high=os.getenv("MODEL_ROUTING_IMAGE_HIGH", "gemini-3-pro-image"),
        mannequin_tier=_mannequin_tier(),
        mannequin_image_size=_image_size(),
        mannequin_max_attempts=int(os.getenv("MANNEQUIN_MAX_ATTEMPTS", "2")),
        mannequin_qc_enabled=(os.getenv("MANNEQUIN_QC_ENABLED", "false").lower() == "true"),
        mannequin_prompt_file=os.getenv("MANNEQUIN_PROMPT_FILE") or None,
        mannequin_prompt_version=os.getenv("MANNEQUIN_PROMPT_VERSION", "v1"),
        base_mannequin_women_asset_id=os.getenv("MANNEQUIN_BASE_WOMEN_ASSET_ID") or None,
        base_mannequin_men_asset_id=os.getenv("MANNEQUIN_BASE_MEN_ASSET_ID") or None,
        job_dispatcher_enabled=(os.getenv("JOB_DISPATCHER_ENABLED", "true").lower() != "false"),
        job_poll_interval_seconds=float(os.getenv("JOB_POLL_INTERVAL_SECONDS", "3")),
        job_lease_timeout_seconds=int(os.getenv("JOB_LEASE_TIMEOUT_SECONDS", "900")),
        job_worker_id=os.getenv("JOB_WORKER_ID", f"web-{os.getpid()}"),
        credit_cost_version=os.getenv("CREDIT_COST_VERSION", "v1"),
        credit_cost_mannequin_generate=int(os.getenv("CREDIT_COST_MANNEQUIN_GENERATE", "2")),
    )
