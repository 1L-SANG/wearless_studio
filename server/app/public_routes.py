"""인증 전 공개 체험 API. 프로젝트·DB·R2에 아무것도 영속하지 않는다."""

import asyncio
import json
import logging
import time
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import replace
from hashlib import sha256

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from . import rate_limit
from .agents import product_evidence_contract, detail_recommendations, points_merge
from .agents.vision_llm import VisionError
from .auth import optional_user
from .client_ip import client_ip as _client_ip
from .r2 import ext_for_mime
from .routes import MAX_UPLOAD_BYTES
from .workers.analyze_job import analyze_image_bytes, analyze_core_image_bytes, extract_image_features

logger = logging.getLogger("wearless.public_analysis")

MAX_PUBLIC_REQUEST_BYTES = 60 * 1024 * 1024
PUBLIC_ANALYSIS_CONCURRENCY = 4
_analysis_semaphore = asyncio.Semaphore(PUBLIC_ANALYSIS_CONCURRENCY)
PUBLIC_FEATURE_CONCURRENCY = 4
_feature_semaphore = asyncio.Semaphore(PUBLIC_FEATURE_CONCURRENCY)


class PublicAnalysisSizeLimitRoute(APIRoute):
    """FastAPI가 multipart body를 파싱하기 전에 명시된 전체 크기를 거절한다."""

    def get_route_handler(self):
        original = super().get_route_handler()

        async def limited_handler(request: Request):
            raw_length = request.headers.get("content-length")
            try:
                content_length = int(raw_length) if raw_length is not None else None
            except ValueError:
                content_length = None
            if content_length is not None and content_length > MAX_PUBLIC_REQUEST_BYTES:
                return JSONResponse(
                    status_code=413,
                    content={"error": {
                        "code": "request_too_large",
                        "message": "한 번에 올릴 수 있는 사진 용량을 초과했어요.",
                    }},
                )
            return await original(request)

        return limited_handler


router = APIRouter(prefix="/v1/public", route_class=PublicAnalysisSizeLimitRoute)

_HOUR_SECONDS = 60 * 60
_DAY_SECONDS = 24 * _HOUR_SECONDS


class PublicAnalysisRateLimiter:
    """IP별 단일 프로세스 슬라이딩 윈도 리미터.

    레코드는 하루 뒤 제거한다. 다중 인스턴스에서는 각 인스턴스별 한도이므로 완전한 전역
    제한이 아니다. 공개 체험의 조용한 안전밸브이며, 강한 제한이 필요해지면 공유 저장소로 교체한다.
    """

    def __init__(self, hourly_limit: int = 10, daily_limit: int = 30):
        self.hourly_limit = hourly_limit
        self.daily_limit = daily_limit
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str, *, now: float | None = None) -> bool:
        current = time.monotonic() if now is None else now
        cutoff_day = current - _DAY_SECONDS
        hits = self._hits.setdefault(key, deque())
        while hits and hits[0] <= cutoff_day:
            hits.popleft()
        if not hits:
            self._hits.pop(key, None)
            hits = self._hits.setdefault(key, deque())
        cutoff_hour = current - _HOUR_SECONDS
        hourly = sum(timestamp > cutoff_hour for timestamp in hits)
        if hourly >= self.hourly_limit or len(hits) >= self.daily_limit:
            return False
        hits.append(current)
        return True


def _bad_request(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=400, detail={"code": code, "message": message})


def _detected_image_mime(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


async def _read_analysis_inputs(images, slots, productContext):
    """핵심·특징 요청이 같은 사진·슬롯·문맥 검증 규칙을 쓴다."""
    if not 1 <= len(images) <= 4:
        raise _bad_request("invalid_image_count", "상품 사진은 1장부터 4장까지 올려주세요.")

    valid_slots = {"Front", "Back", "Detail", "BackDetail"}
    if slots is not None and (
        len(slots) != len(images) or any(slot not in valid_slots for slot in slots)
    ):
        raise _bad_request("invalid_slots", "사진 위치 정보가 올바르지 않아요.")

    source_images: list[tuple[bytes, str]] = []
    total_bytes = 0
    for upload in images:
        mime = (upload.content_type or "").lower()
        if ext_for_mime(mime) is None:
            raise _bad_request("unsupported_type", "지원하지 않는 이미지 형식입니다.")
        data = await upload.read(MAX_UPLOAD_BYTES + 1)
        if not data:
            raise _bad_request("empty_file", "비어 있는 사진은 분석할 수 없어요.")
        if len(data) > MAX_UPLOAD_BYTES:
            raise _bad_request("file_too_large", "사진 한 장은 25MB보다 작아야 해요.")
        total_bytes += len(data)
        if total_bytes > MAX_PUBLIC_REQUEST_BYTES:
            raise HTTPException(
                status_code=413,
                detail={
                    "code": "request_too_large",
                    "message": "한 번에 올릴 수 있는 사진 용량을 초과했어요.",
                },
            )
        if _detected_image_mime(data) != mime:
            raise _bad_request("invalid_image_content", "실제 이미지 파일만 올려주세요.")
        source_images.append((data, mime))

    # 공개 흐름은 DB Product가 없으므로 colorGroupId 반환에 필요한 최소 메타만
    # 받는다. AG-01 build_prompt가 모든 자유 텍스트를 sanitize한다.
    product = {}
    if productContext and len(productContext) <= 4096:
        try:
            parsed = json.loads(productContext)
            if isinstance(parsed, dict):
                product = parsed
        except (TypeError, ValueError):
            product = {}

    return source_images, product


def _validate_run_id(run_id):
    if not points_merge.valid_run_id(run_id):
        raise _bad_request("invalid_run_id", "분석 실행 정보가 올바르지 않아요.")


def _enforce_public_limit(request, authenticated_user, kind):
    # 비회원은 IP, 로그인은 사용자별 한도다. 기존 분석은 프로젝트 분석과 한도를 공유한다.
    if authenticated_user is None:
        limiter_name, key = f"public_{kind}_limiter", _client_ip(request)
    else:
        limiter_name, key = f"user_{kind}_limiter", rate_limit.user_key(authenticated_user)
    rate_limit.enforce(getattr(request.app.state, limiter_name, None), key)


@asynccontextmanager
async def _public_slot(request, authenticated_user, semaphore, kind):
    # 빈 슬롯이 없으면 한도를 차감하지 않고 즉시 거절한다. 획득 뒤에는 항상 반환한다.
    if semaphore.locked():
        raise HTTPException(status_code=429, detail={
            "code": "analysis_busy", "message": "잠시 후 다시 시도해주세요.",
        })
    await semaphore.acquire()
    try:
        _enforce_public_limit(request, authenticated_user, kind)
        yield
    finally:
        semaphore.release()


def _handoff_unavailable():
    return HTTPException(status_code=503, detail={
        "code": "analysis_handoff_unavailable",
        "message": "분석 결과를 안전하게 저장하지 못했어요. 잠시 후 다시 시도해 주세요.",
    })


@router.post("/analyze", tags=["Public Analysis"], summary="비로그인 상품 사진 AI 분석")
async def public_analyze(
    request: Request,
    images: list[UploadFile] = File(...),
    slots: list[str] | None = Form(default=None),
    productContext: str | None = Form(default=None),
    parts: str | None = Form(default=None),
    runId: str | None = Form(default=None),
    authenticated_user: str | None = Depends(optional_user),
):
    """parts가 없으면 기존 분석, core면 AG-01과 사진 일치만 기다린다."""
    if (parts is not None and parts != "core") or (parts is None and "parts" in await request.form()):
        raise _bad_request("invalid_parts", "분석 범위가 올바르지 않아요.")
    if parts == "core":
        _validate_run_id(runId)
    async with _public_slot(request, authenticated_user, _analysis_semaphore, "analysis"):
        source_images, product = await _read_analysis_inputs(images, slots, productContext)
        analyze = analyze_core_image_bytes if parts == "core" else analyze_image_bytes
        try:
            core = await analyze(
                request.app.state.settings, source_images, product=product, slots=slots,
                persist_confirmed_evidence=True,
            )
        except VisionError:
            raise HTTPException(status_code=502, detail={
                "code": "analysis_failed", "message": "상품 분석에 실패했어요. 다시 시도해 주세요.",
            }) from None
    data = dict(core["result_data"])
    evidence = (core.get("analysis_payload") or {}).get(
        product_evidence_contract.PERSISTED_KEY
    )
    if evidence is not None:
        try:
            closure = (core.get("analysis_payload") or {}).get("frontClosure")
            data[product_evidence_contract.HANDOFF_KEY] = (
                product_evidence_contract.issue_handoff(
                    evidence, request.app.state.settings.r2_secret_access_key,
                    **({"front_closure": closure} if closure is not None else {}),
                )
            )
        except product_evidence_contract.ProductEvidenceContractError:
            logger.exception("public analysis evidence handoff could not be sealed")
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "analysis_handoff_unavailable",
                    "message": "분석 결과를 안전하게 저장하지 못했어요. 잠시 후 다시 시도해 주세요.",
                },
            ) from None
    detail_contract = (core.get("analysis_payload") or {}).get(detail_recommendations.PERSISTED_KEY)
    if detail_contract is not None:
        try:
            data[detail_recommendations.PERSISTED_KEY] = detail_recommendations.public_summary(detail_contract)
            data[detail_recommendations.HANDOFF_KEY] = detail_recommendations.issue_handoff(
                detail_contract, request.app.state.settings.r2_secret_access_key
            )
        except detail_recommendations.DetailRecommendationError:
            logger.exception("public detail recommendations handoff could not be sealed")
            raise HTTPException(status_code=503, detail={
                "code": "analysis_handoff_unavailable",
                "message": "분석 결과를 안전하게 저장하지 못했어요. 잠시 후 다시 시도해 주세요.",
            }) from None
    if parts == "core":
        try:
            data["pointsTicket"] = points_merge.issue_ticket(
                points_merge.POINTS_PURPOSE, runId, data.get("aiSuggestedPoints") or [],
                request.app.state.settings.r2_secret_access_key,
            )
        except points_merge.PointsTicketError:
            raise _handoff_unavailable() from None
    return JSONResponse({"data": data})


@router.post("/analyze/features", tags=["Public Analysis"], summary="상품 강조 특징과 디테일 추천 분석")
async def public_analyze_features(
    request: Request,
    images: list[UploadFile] = File(...),
    slots: list[str] | None = Form(default=None),
    productContext: str | None = Form(default=None),
    runId: str | None = Form(default=None),
    attempt: str | None = Form(default=None),
    authenticated_user: str | None = Depends(optional_user),
):
    _validate_run_id(runId)
    if attempt is not None and attempt not in ("1", "2", "3"):
        raise _bad_request("invalid_attempt", "분석 시도 번호가 올바르지 않아요.")
    attempt_number = int(attempt or "1")
    started = time.perf_counter()
    provider, model, point_status, detail_status = None, None, "empty", "unavailable"
    point_count, candidate_count, outcome = 0, 0, "failed"
    try:
        async with _public_slot(request, authenticated_user, _feature_semaphore, "feature"):
            source_images, product = await _read_analysis_inputs(images, slots, productContext)
            settings = request.app.state.settings
            try:
                features = await extract_image_features(
                    replace(settings, analysis_model_order="gemini"), source_images, product=product, slots=slots)
            except VisionError:
                raise HTTPException(status_code=502, detail={
                    "code": "feature_extraction_failed", "message": "상품 특징 분석에 실패했어요. 다시 시도해 주세요.",
                }) from None
        points, contract, provider = features["points"], features["contract"], features["provider"]
        model = settings.model_text if provider == "gpt" else (settings.model_text_gemini_features or settings.model_text_gemini)
        point_status, point_count = "ready" if points else "empty", len(points)
        detail_status, candidate_count = contract["status"], len(contract["candidates"])
        try:
            data = {
                "points": {"status": point_status, "items": list(points)},
                detail_recommendations.PERSISTED_KEY: detail_recommendations.public_summary(contract),
                detail_recommendations.HANDOFF_KEY: detail_recommendations.issue_handoff(contract, settings.r2_secret_access_key),
                "featureTicket": points_merge.issue_ticket(points_merge.FEATURE_PURPOSE, runId, points, settings.r2_secret_access_key),
            }
        except (detail_recommendations.DetailRecommendationError, points_merge.PointsTicketError):
            raise _handoff_unavailable() from None
        outcome = "success"
        return JSONResponse({"data": data})
    except HTTPException as exc:
        outcome = exc.detail["code"]
        raise
    finally:
        logger.info(
            "public features duration_ms=%d points_status=%s points_count=%d detail_status=%s candidates=%d provider=%s model=%s attempt=%d run_hash=%s result=%s",
            round((time.perf_counter() - started) * 1000), point_status, point_count,
            detail_status, candidate_count, provider, model, attempt_number,
            sha256(runId.encode()).hexdigest()[:12], outcome,
        )


@router.post("/analyze/points-merge", tags=["Public Analysis"], summary="같은 실행의 강조 특징 합치기 판정")
async def public_points_merge(
    request: Request,
    body: object = Body(default=None),
    authenticated_user: str | None = Depends(optional_user),
):
    started = time.perf_counter()
    refine_count, new_count, outcome = 0, 0, "invalid_points_ticket"
    try:
        if not isinstance(body, dict):
            raise points_merge.PointsTicketError("points_ticket_invalid")
        run_id = body.get("runId")
        secret = request.app.state.settings.r2_secret_access_key
        early = points_merge.verify_ticket(body.get("pointsTicket"), secret, points_merge.POINTS_PURPOSE, run_id)
        late = points_merge.verify_ticket(body.get("featureTicket"), secret, points_merge.FEATURE_PURPOSE, run_id)
        _enforce_public_limit(request, authenticated_user, "merge")
        try:
            decisions = await points_merge.merge_points(request.app.state.settings, early, late)
        except points_merge.PointsMergeError as exc:
            outcome = str(exc)
            raise HTTPException(status_code=503, detail={
                "code": "points_merge_unavailable", "message": "상품 특징을 합치지 못했어요. 잠시 후 다시 시도해 주세요.",
            }) from None
        refine_count = sum(row["action"] == "refine" for row in decisions)
        new_count = sum(row["action"] == "new" for row in decisions)
        outcome = "success" if early and late else "model_skipped"
        return JSONResponse({"data": {"decisions": decisions}})
    except points_merge.PointsTicketError:
        raise _bad_request("invalid_points_ticket", "분석 특징의 서명 정보가 올바르지 않아요.") from None
    except HTTPException as exc:
        if exc.status_code != 503:
            outcome = exc.detail["code"]
        raise
    finally:
        logger.info(
            "public points merge duration_ms=%d refine=%d new=%d result=%s",
            round((time.perf_counter() - started) * 1000), refine_count, new_count, outcome,
        )
