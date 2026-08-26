"""Wearless Studio API (backend_integration_plan §4·§8).

Phase 0: healthz + JWT 검증 + 에러 봉투 { error: { code, message, details? } }.
Phase 1: /me/account · /projects(library) · projects CRUD (routes.py).
"""

import logging
import os
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
from . import image_usage
from .r2 import R2Client
from .routes import router as v1_router, COMMON_RESPONSES
from .workers.dispatcher import JobDispatcher, configured_job_kinds
from .workers.draft_asset_reclaimer import DraftAssetReclaimer
from .workers.fm_vc_revocation_reconciler import FaceVcRevocationReconciler
from .workers.sam_retry_pusher import SamRetryPusher
from .services import sam_client
from .services.sam_autoscale import SamAutoscaleAdapter
from .workers.sam_autoscaler import SamAutoscaler

DEFAULT_ERROR_CODES = {
    401: "unauthorized",
    402: "insufficient_credits",
    403: "forbidden",
    404: "not_found",
}

# LogRecord 표준 속성 집합 — 이 밖의 키만 extra로 간주.
_RESERVED_LOG_ATTRS = frozenset(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class _ExtraFormatter(logging.Formatter):
    """기본 메시지 뒤에 extra={...} 필드를 key=value로 덧붙인다.

    analysis_spike·retrieval_call·seller_text_canonicalize 등 관측 로그는
    값이 전부 extra에 있어서, 이게 없으면 메시지만 찍히고 데이터가 사라진다.
    """

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = {
            k: v
            for k, v in record.__dict__.items()
            if k not in _RESERVED_LOG_ATTRS and not k.startswith("_")
        }
        if extras:
            kv = " ".join(f"{k}={v!r}" for k, v in extras.items())
            return f"{base} | {kv}"
        return base


def _configure_logging() -> None:
    """앱 로깅 정본 설정 — 중앙 설정이 없어 wearless.* / app.* INFO 로그가
    prod에서 묻히던 문제(관측 로그 유실)를 막는다. LOG_LEVEL env로 조절(기본 INFO).

    uvicorn은 자기 named 로거(propagate=False)만 설정하므로 root 핸들러 교체가
    access/error 로그를 이중 출력하거나 죽이지 않는다.
    """
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler()
    handler.setFormatter(_ExtraFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))

    root = logging.getLogger()
    root.handlers[:] = [handler]  # 재호출(테스트·import)에도 핸들러 중복 안 되게 교체
    root.setLevel(level)

    # INFO root에서 서드파티 소음 억제 — 우리 로그만 보이게.
    for noisy in ("httpx", "httpcore", "botocore", "boto3", "urllib3", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _validate_facemarket_vc_settings(settings: Settings) -> None:
    if (
        settings.app_env == "production"
        and settings.facemarket_enabled
        and not settings.fm_vc_required
    ):
        raise RuntimeError(
            "FACEMARKET_VC_REQUIRED=true is required for production FaceMarket"
        )
    if settings.fm_vc_required and (
        not settings.opendid_holder_url
        or not settings.opendid_holder_url.strip()
        or not settings.opendid_holder_hmac_secret
        or not settings.opendid_holder_hmac_secret.strip()
    ):
        raise RuntimeError(
            "OpenDID Holder URL and HMAC secret are required when FaceMarket VC is mandatory"
        )


def create_app(settings: Settings | None = None) -> FastAPI:
    _configure_logging()
    settings = settings or load_settings()
    job_kinds = configured_job_kinds()
    detail_worker_only = job_kinds == ("detail_page",)

    from .facemarket_enrollment import build_biometric_aws_clients, validate_biometric_settings

    validate_biometric_settings(settings)
    _validate_facemarket_vc_settings(settings)

    pool = create_pool(settings.database_url) if settings.database_url else None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        dispatcher = None
        draft_asset_reclaimer = None
        vc_revocation_reconciler = None
        sam_retry_pusher = None
        sam_autoscaler = None
        opendid_autoscaler = None
        detail_worker_autoscaler = None
        if pool is not None:
            await pool.open()
            # revoke_license/cutover 는 fm_vc_required 와 무관하게 vc_id 가 있으면
            # 내구성 폐기 잡을 적재한다 — Holder 가 설정돼 있으면(그 잡을 드레인할 대상이
            # 있으면) 리컨실러를 켠다. fm_vc_required 는 배포 게이트가 이미 Holder
            # URL+secret 존재를 강제하므로 holder-configured 를 함의한다.
            holder_configured = bool(
                settings.opendid_holder_url
                and settings.opendid_holder_url.strip()
                and settings.opendid_holder_hmac_secret
                and settings.opendid_holder_hmac_secret.strip()
            )
            if not detail_worker_only and (holder_configured or settings.fm_vc_required):
                vc_revocation_reconciler = FaceVcRevocationReconciler(app)
                await vc_revocation_reconciler.start()
            if not detail_worker_only and app.state.r2 is not None:
                draft_asset_reclaimer = DraftAssetReclaimer(app)
                await draft_asset_reclaimer.start()
            # sam2 온디맨드 기동/종료(2026-08-21). 디스패처 조건(R2·AI provider)과 **독립** —
            # DB 만 있으면 돈다. 디스패처 블록 안에 두면 provider 키가 빠진 환경에서 sam2 가
            # 영영 안 켜진다. off 면 어댑터가 클라이언트를 안 만들고 prewarm 은 즉시 return.
            # state 에는 off 여도 올려 둔다 — 라우트 훅이 getattr 분기 없이 부를 수 있게.
            if not detail_worker_only:
                autoscale_adapter = SamAutoscaleAdapter(settings)
                app.state.sam_autoscaler = SamAutoscaler(app, autoscale_adapter)
                sam_client.install_prewarm_hook(app.state.sam_autoscaler.prewarm)
                if autoscale_adapter.enabled:
                    sam_autoscaler = app.state.sam_autoscaler
                    await sam_autoscaler.start()
                # opendid(fm-holder) 온디맨드 — 같은 reconciler/어댑터를 service·demand·lock_key 만 바꿔
                # 재사용. 수요 = license_pending·vc_pending 등록. wake 는 issue_face_vc 지연 경로가
                # app.state.opendid_autoscaler.prewarm_soon() 으로 부른다(off 여도 즉시 return).
                opendid_adapter = SamAutoscaleAdapter(
                    settings, service="opendid",
                    enabled_attr="opendid_autoscale", topic_attr="sam_alert_topic_arn")
                app.state.opendid_autoscaler = SamAutoscaler(
                    app, opendid_adapter,
                    demand_fn=lambda repo, conn: repo.opendid_demand_snapshot(conn),
                    idle_attr="opendid_autoscale_idle_minutes",
                    name="opendid", lock_key="opendid_autoscaler")
                if opendid_adapter.enabled:
                    opendid_autoscaler = app.state.opendid_autoscaler
                    await opendid_autoscaler.start()
                detail_adapter = SamAutoscaleAdapter(
                    settings, service="detail-worker",
                    enabled_attr="detail_worker_autoscale",
                    topic_attr="sam_alert_topic_arn")
                app.state.detail_worker_autoscaler = SamAutoscaler(
                    app, detail_adapter,
                    demand_fn=lambda repo, conn: repo.detail_worker_demand_snapshot(conn),
                    idle_attr="detail_worker_autoscale_idle_minutes",
                    name="detail-worker", lock_key="detail_worker_autoscaler")
                if detail_adapter.enabled:
                    detail_worker_autoscaler = app.state.detail_worker_autoscaler
                    await detail_worker_autoscaler.start()
            # job dispatcher (§5) — DB·R2 + 최소 1개 AI provider(마네킹=Gemini, 분석=Gemini/OpenAI)
            # 가 있고 활성화일 때만 기동. provider 없는 job 은 워커가 실패 봉투로 종결.
            if (
                settings.job_dispatcher_enabled
                and app.state.r2 is not None
                and (app.state.gemini is not None or settings.openai_api_key)
            ):
                dispatcher = JobDispatcher(app, kinds=job_kinds)
                await dispatcher.start()
                app.state.dispatcher = dispatcher
                # 폴링하는 화면이 없는 SAM 잡(sam_preprocess·matching_cutout)의 재시도를 민다.
                # 디스패처와 **분리**한다 — 디스패처는 워커를 await 하므로 긴 잡이 도는 동안
                # 타이머가 멈춘다(2026-08-21).
                if not detail_worker_only:
                    sam_retry_pusher = SamRetryPusher(app)
                    await sam_retry_pusher.start()
        yield
        sam_client.install_prewarm_hook(None)
        if sam_autoscaler is not None:
            await sam_autoscaler.stop()
        if opendid_autoscaler is not None:
            await opendid_autoscaler.stop()
        if detail_worker_autoscaler is not None:
            await detail_worker_autoscaler.stop()
        if draft_asset_reclaimer is not None:
            await draft_asset_reclaimer.stop()
        if sam_retry_pusher is not None:
            await sam_retry_pusher.stop()      # 디스패처보다 먼저 — 새 잡을 더 걸지 않게
        if dispatcher is not None:
            await dispatcher.stop()
        if vc_revocation_reconciler is not None:
            await vc_revocation_reconciler.stop()
        if pool is not None:
            await image_usage.drain(timeout_seconds=5.0)
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
    if settings.fm_biometric_enrollment_enabled:
        app.state.fm_rekognition, app.state.fm_sts = build_biometric_aws_clients(settings)
    else:
        app.state.fm_rekognition = None
        app.state.fm_sts = None
    # R2는 필수 설정이 모두 있을 때만 — 일부만 설정된 채 워커가 도는 것을 막는다
    _r2_ready = all((
        settings.r2_bucket, settings.r2_access_key_id, settings.r2_secret_access_key,
        settings.r2_endpoint or settings.r2_account_id,
    ))
    app.state.r2 = R2Client(settings) if _r2_ready else None
    # FaceMarket 얼굴 = 생체 PII → 공개 도메인 미연결 비공개 버킷 필수.
    if _r2_ready and settings.r2_face_bucket:
        app.state.r2_face = R2Client(settings, bucket=settings.r2_face_bucket, public_base=None)
    elif _r2_ready and (settings.facemarket_enabled or settings.personalization_enabled):
        # 얼굴은 생체 PII라 환경과 무관하게 메인 버킷 폴백을 허용하지 않는다. dev에서도
        # 전용 버킷 없이 기능을 켜면 기존 공개 도메인 연결 버킷에 얼굴이 저장될 수 있다.
        raise RuntimeError(
            "R2_FACE_BUCKET is required when FACEMARKET_ENABLED or PERSONALIZATION_ENABLED "
            "(biometric face must use a private bucket, never the public-served main bucket)."
        )
    else:
        app.state.r2_face = None
    app.state.gemini = (
        GeminiImageClient(settings)
        if settings.gemini_api_key or settings.openai_api_key
        else None
    )
    # 이미지 실비 계측 — 풀이 없으면(테스트·DB 미설정) 자동으로 로그 전용이 된다.
    image_usage.configure(pool=pool, persist=settings.image_usage_persist)
    app.state.dispatcher = None
    app.state.detail_worker_autoscaler = None
    # 캐노니컬 컷아웃 조회기. 마네킹 워커가 이걸 통해 준비된 컷아웃을 읽는다 —
    # 없으면 None 을 돌려주고 베이스라인 경로가 그대로 돈다(보조 인프라).
    from .services.canonical_reference import load as _canonical_load

    app.state.canonical_reference_loader = _canonical_load
    # 공개 분석 리미터는 프로세스 로컬 안전밸브다(public_routes 주석의 다중 인스턴스 한계 참조).
    from .public_routes import PublicAnalysisRateLimiter

    app.state.public_analysis_limiter = PublicAnalysisRateLimiter()
    app.state.jwt_key_resolver = (
        jwks_key_resolver(settings.jwks_url) if settings.jwks_url else None
    )

    @app.middleware("http")
    async def unhandled_exception_envelope(request: Request, call_next):
        """500을 JSON 봉투로 고정해 브라우저에서 CORS 네트워크 실패로 위장되지 않게 한다."""
        try:
            return await call_next(request)
        except Exception:
            logging.getLogger("wearless.api").exception(
                "unhandled request error method=%s path=%s",
                request.method,
                request.url.path,
            )
            return JSONResponse(
                status_code=500,
                content={"error": {
                    "code": "internal_error",
                    "message": "서버 오류가 발생했어요. 잠시 후 다시 시도해 주세요.",
                }},
            )

    # CORS를 예외 봉투 밖쪽에 두어 정상 응답뿐 아니라 500에도 ACAO를 붙인다.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Draft-Token"],
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

    from .public_routes import router as public_router

    app.include_router(public_router)

    # 토스 크레딧 추가구매(WS3) — 라우터는 항상 등록하고, 키 미설정이면 checkout 이 503 으로
    # 거절한다(플래그로 라우트를 숨기면 프론트가 404 를 '미배포'와 구분 못 해 디버깅이 어렵다).
    from .payments import router as payments_router

    app.include_router(payments_router)

    # FaceMarket(해커톤) — 플래그 on일 때만 등록. off(프로드 기본)면 라우트 미존재 →
    # 기존 셀러 플로우/배포 무영향. verify·settle 훅이 OpenDID env 없는 프로드를 파손하지 않게.
    if settings.facemarket_enabled:
        from .facemarket import router as facemarket_router
        from .facemarket_chain import FaceMarketChain

        app.include_router(facemarket_router)
        # 온체인 정산 recorder(선택과제2). 체인 env 미설정이면 None → 정산 훅 no-op.
        app.state.fm_chain = FaceMarketChain.from_settings(settings)
        if settings.fm_biometric_enrollment_enabled:
            from .facemarket_enrollment import router as biometric_enrollment_router

            app.include_router(biometric_enrollment_router)
    else:
        app.state.fm_chain = None

    # 개인화(사용자 본인 얼굴·신체) — 플래그 on일 때만 등록. off(프로드 기본)면 라우트 미존재
    # → 생체정보 처리 코드가 프로드에 배포되지 않는다(api-spec §1.1).
    if settings.personalization_enabled:
        from .personalization import router as personalization_router

        app.include_router(personalization_router)

    return app


app = create_app()
