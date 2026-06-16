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

from .auth import jwks_key_resolver, require_user
from .config import Settings, load_settings
from .db import create_pool
from .routes import router as v1_router

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
        if pool is not None:
            await pool.open()
        yield
        if pool is not None:
            await pool.close()

    app = FastAPI(
        title="Wearless Studio API", docs_url=None, redoc_url=None, lifespan=lifespan
    )
    app.state.settings = settings
    app.state.pool = pool
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

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    @app.get("/v1/me/ping")
    async def me_ping(user_id: str = Depends(require_user)):
        # Phase 0 완료 기준(JWT 검증 통과) 확인용 — Phase 1에서 /v1/me/account로 대체
        return {"userId": user_id}

    app.include_router(v1_router)

    return app


app = create_app()
