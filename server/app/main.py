"""Wearless Studio API (backend_integration_plan §4·§8).

Phase 0: healthz + JWT 검증 + 에러 봉투 { error: { code, message, details? } }.
Phase 1: /me/account · /projects(library) · projects CRUD (routes.py).
"""

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .agents.gemini_image import GeminiImageClient
from .auth import jwks_key_resolver, require_user
from .config import Settings, load_settings
from .db import create_pool
from .r2 import R2Client
from .routes import router as v1_router, COMMON_RESPONSES
from .workers.dispatcher import JobDispatcher

DEFAULT_ERROR_CODES = {
    401: "unauthorized",
    402: "insufficient_credits",
    403: "forbidden",
    404: "not_found",
}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()

    pool = create_pool(settings.database_url) if settings.database_url else None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        dispatcher = None
        if pool is not None:
            await pool.open()
            # job dispatcher (§5) — DB·R2 + 최소 1개 AI provider(마네킹=Gemini, 분석=Gemini/OpenAI)
            # 가 있고 활성화일 때만 기동. provider 없는 job 은 워커가 실패 봉투로 종결.
            if (
                settings.job_dispatcher_enabled
                and app.state.r2 is not None
                and (app.state.gemini is not None or settings.openai_api_key)
            ):
                dispatcher = JobDispatcher(app)
                await dispatcher.start()
                app.state.dispatcher = dispatcher
        yield
        if dispatcher is not None:
            await dispatcher.stop()
        if pool is not None:
            await pool.close()

    docs_url = "/docs" if settings.app_env == "dev" else None
    redoc_url = "/redoc" if settings.app_env == "dev" else None

    app = FastAPI(
        title="Wearless Studio API",
        docs_url=docs_url,
        redoc_url=redoc_url,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.pool = pool
    # R2는 필수 설정이 모두 있을 때만 — 일부만 설정된 채 워커가 도는 것을 막는다
    _r2_ready = all((
        settings.r2_bucket, settings.r2_access_key_id, settings.r2_secret_access_key,
        settings.r2_endpoint or settings.r2_account_id,
    ))
    app.state.r2 = R2Client(settings) if _r2_ready else None
    app.state.gemini = (
        GeminiImageClient(settings) if settings.gemini_api_key else None
    )
    app.state.dispatcher = None
    app.state.jwt_key_resolver = (
        jwks_key_resolver(settings.jwks_url) if settings.jwks_url else None
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
    )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        if isinstance(exc.detail, dict) and "code" in exc.detail:
            body = exc.detail
        else:
            body = {
                "code": DEFAULT_ERROR_CODES.get(exc.status_code, "error"),
                "message": str(exc.detail),
            }
        return JSONResponse(status_code=exc.status_code, content={"error": body})

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        # exc.errors()의 ctx에 raw 예외 객체(ValueError 등)가 섞여 json.dumps가 깨지므로
        # FastAPI 기본 핸들러처럼 jsonable_encoder로 직렬화 가능한 형태로 강제한다.
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "요청 형식이 올바르지 않습니다.",
                    "details": jsonable_encoder(exc.errors()),
                }
            },
        )

    @app.get("/healthz", tags=["System"], summary="서버 헬스 체크")
    async def healthz():
        """서버가 정상 기동 중인지 모니터링하기 위한 헬스체크 엔드포인트입니다."""
        return {"status": "ok"}

    @app.get(
        "/v1/me/ping",
        responses={**COMMON_RESPONSES},
        tags=["User & Account"],
        summary="인증 상태 디버그 핑",
    )
    async def me_ping(user_id: str = Depends(require_user)):
        """로그인 상태(JWT 서명 검증 성공 여부)를 검증하고 디버깅용으로 사용자 ID를 반환합니다.

        - **Bearer Token**: 필수
        """
        # Phase 0 완료 기준(JWT 검증 통과) 확인용 — Phase 1에서 /v1/me/account로 대체
        return {"userId": user_id}

    app.include_router(v1_router)

    return app


app = create_app()
